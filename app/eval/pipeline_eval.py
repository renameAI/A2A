"""전 구간 결정 회귀 — LLM 없이, 프로덕션 결정 로직 그대로.

왜 필요한가: 기존 벤치(signal·contact·poc)는 컴포넌트 하나씩을 재는데,
이 저장소에서 실제로 난 사고는 **컴포넌트 사이**에서 났다.
- 출처 승수를 p에 곱했는데 랭킹이 p를 안 쓰고 있었다
- 문턱 판정을 심층 판독이 갱신했는데 점수를 다시 안 접었다
- humanTick 규칙 순서가 뒤집혀 "웹 수집"이 "자료 읽기"로 새었다
- HARD_RULES의 한국어 규칙이 숫자까지 한글로 풀었다
전부 컴포넌트 테스트는 초록인데 파이프라인이 틀린 경우다.

왜 LLM을 안 부르는가: 커밋 게이트에 들어가려면 초 단위로 끝나고 결정적이어야
한다. 모델 판정(p·reachability·언어)은 시나리오에 **고정값**으로 박아 두고,
그 위에서 도는 **코드의 결정**(가중·정렬·언어 선택·상호 표기·인용 계약)만
잰다. 판정의 품질은 별도 벤치(run_signal_bench 등)가 실모델로 잰다 —
판정=모델, 결정=코드라는 이 저장소의 분업을 평가에도 그대로 적용한다.
"""
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenarios() -> "list[dict]":
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(_DIR.glob("*.json"))]


def _reach_weight(ont: "dict | None", fact: bool = False) -> float:
    """랭킹의 문턱 가중 — router._rank_pool과 같은 식이어야 한다."""
    if fact:
        return 1.0
    reach = (ont or {}).get("reachability")
    return 1.0 if reach is None else 0.35 + 0.65 * float(reach)


def rank(scn: dict, reach_facts: "set[str] | None" = None) -> "list[dict]":
    """시나리오 후보를 프로덕션과 같은 식으로 정렬한다.

    score = 보완성 × p × 문턱가중 (+ 피드백은 시나리오에 없으므로 0)
    """
    facts = reach_facts or set()
    out = []
    for c in scn["candidates"]:
        w = _reach_weight(c.get("ontology"), c.get("site") in facts)
        out.append({**c, "reach_w": round(w, 3),
                    "score": round(c["complementarity"] * c["p"] * w, 4)})
    out.sort(key=lambda x: (-x["score"], x["company_id"]))
    return out


def check(scn: dict) -> "list[str]":
    """시나리오 기대와 어긋난 것들을 문장으로 돌려준다. 빈 목록이면 통과."""
    fails = []
    ranked = rank(scn)
    exp = scn["expect"]
    top = ranked[0]

    for name in exp.get("top_is_not", []):
        if top["name"] == name:
            fails.append(
                f"[{scn['name']}] 1위가 {name} — 닿을 수 없는 후보가 위에 있으면 "
                f"목록 전체가 안 믿긴다 (score={top['score']}, w={top['reach_w']})")

    if exp.get("top_has_contact"):
        if not ((top.get("ontology") or {}).get("contacts")):
            fails.append(f"[{scn['name']}] 1위 {top['name']}에 접점이 없다 — "
                         f"'닿기'가 제품의 약속인데 첫 후보부터 못 닿는다")

    for cid, want in (exp.get("reach_weight") or {}).items():
        got = next(c["reach_w"] for c in ranked if c["company_id"] == cid)
        if abs(got - want) > 1e-6:
            fails.append(f"[{scn['name']}] {cid} 문턱가중 {got} ≠ 기대 {want} — "
                         f"랭킹 식이 바뀌었다")

    need = exp.get("segments_low_or_mid_min")
    if need is not None:
        got = sum(1 for s in scn["segments"] if s.get("reach") in ("low", "mid"))
        if got < need:
            fails.append(f"[{scn['name']}] 체급 맞는 경로 {got}개 < {need}개 — "
                         f"풀이 대기업으로만 차면 랭킹이 할 수 있는 일이 없다")

    # 메일 언어·상호 — 크로스보더에서 깨졌던 결정
    for cid, want in (exp.get("mail_language") or {}).items():
        c = next(x for x in ranked if x["company_id"] == cid)
        got = (c.get("ontology") or {}).get("business_language") or "ko"
        if got != want:
            fails.append(f"[{scn['name']}] {c['name']} 메일 언어 {got} ≠ {want}")
    for cid, want in (exp.get("sender_name") or {}).items():
        c = next(x for x in ranked if x["company_id"] == cid)
        lang = (c.get("ontology") or {}).get("business_language") or "ko"
        rq = scn["requester"]
        got = (rq.get("name_latin") or rq["name"]) if lang != "ko" else rq["name"]
        if got != want:
            fails.append(f"[{scn['name']}] {c['name']}에게 보낼 상호 {got} ≠ {want} — "
                         f"읽을 수 없는 상호가 나간다")

    # 인용 계약 — 신호에 출처가 있어야 메일이 링크를 걸 수 있다
    for cid in exp.get("cited_url_required", []):
        c = next(x for x in ranked if x["company_id"] == cid)
        sigs = (c.get("ontology") or {}).get("signals") or []
        if not any(s.get("source_url") for s in sigs):
            fails.append(f"[{scn['name']}] {c['name']} 신호에 출처 URL이 없다 — "
                         f"메일이 '무엇을 보고 연락하는지' 밝힐 수 없다")
    return fails


def run_all() -> "tuple[int, list[str]]":
    fails = []
    scns = load_scenarios()
    for scn in scns:
        fails.extend(check(scn))
    return len(scns), fails

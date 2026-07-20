"""데이터 증강 — 풀 파인튜닝을 태울 만큼 데이터가 없을 때, 정직하게 늘린다.

원칙 (증강의 제1계율): 라벨-입력 일관성이 깨지는 증강은 안 하느니만 못하다.
여기의 모든 전략은 그 일관성을 '구조적으로' 보장하거나(결정적 재유도·동시 치환),
보장 못 하는 경우 게이트로 검증 후 탈락시킨다(LLM 패러프레이즈 → 그라운딩 게이트).

전략 4종:
  synthesize()          합성 — 결정적 프로필 생성기 × _mock_extract 라벨.
                        조합 다양성(산업×문제×지역×필드 유무)으로 양을 만든다.
  entity_substitution() 엔티티 일관 치환 — 회사명·지명을 입력과 라벨 양쪽에
                        동시 치환. 시드 기반 결정적.
  shuffle_lines()       '키: 값' 라인 순서 셔플 — 추출은 순서 불변이므로 라벨 유지.
  field_dropout()       선택 필드 제거 후 라벨을 엔진으로 '재유도' — 희소 프로필
                        시뮬레이션. label_source=deterministic 예제에만 허용.
  llm_paraphrase()      패러프레이즈 어댑터 — 버스트 주간의 자체 vLLM(OpenAI 호환)
                        엔드포인트용. 무료 Friendli API를 쓰지 않는다(한도).
                        통과 조건: 라벨의 stated 필드가 패러프레이즈된 원문에
                        여전히 그라운딩되는가 (3-gram, R1과 동일 임계).

규율: 증강은 train에만. held-out은 원본 그대로 봉인 유지 — check_no_leak()가
seal.json과 대조한다. 모든 랜덤은 seed 기반 결정적(random.Random(seed)).
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

# ── 합성 재료 — 조합 다양성이 양을 만든다 (반복 복제가 아니라) ──────────

_DOMAINS = [
    # (산업, 문제, 솔루션, 타겟, 판매가치)
    ("hospitality_renovation", "노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담",
     "저자본·무철거 경험형 객실 전환, 매출 쉐어", "노후 객실을 보유한 중소 호텔 오너", "매출"),
    ("logistics_ai", "중소 화주의 공차·반송 낭비로 인한 물류비 증가",
     "AI 예측 배차로 공차율 절감", "월 500건 이상 출고하는 중소 화주", "비용"),
    ("fintech_fraud", "온라인 가맹점의 결제 사기로 인한 차지백 손실",
     "실시간 이상거래 탐지 API", "월 거래 1만 건 이상 온라인 가맹점", "비용"),
    ("edutech_matching", "지방 중소 학원의 강사 수급난과 폐강 위험",
     "검증된 원격 강사 매칭 플랫폼", "수도권 외 중소 학원 원장", "문제해결"),
    ("agri_data", "시설원예 농가의 감에 의존한 환경 제어로 수확량 편차",
     "센서 기반 생육 데이터 처방", "스마트팜 전환을 원하는 시설원예 농가", "매출"),
    ("saas_hr", "제조 중소기업의 교대 근무 스케줄 수작업 관리 부담",
     "교대 스케줄 자동 편성 SaaS", "3교대 운영 중소 제조사 인사팀", "비용"),
    ("healthcare_screen", "중소 병원의 검진 결과 안내 지연과 재방문 이탈",
     "검진 결과 자동 해설·후속 안내 시스템", "건강검진센터 운영 중소 병원", "문제해결"),
    ("food_material", "식품 제조사의 수입 원료 품질 편차와 규제 대응 부담",
     "국산 대체 소재 개발·규격 문서 일괄 제공", "기능성 식품 제조사 구매팀", "문제해결"),
    ("green_retrofit", "노후 상업건물의 에너지 비용 급증과 규제 압박",
     "무공사 단열 필름·운영 데이터 리포트", "연면적 1천평 이상 상업건물 운영사", "비용"),
    ("content_local", "K-콘텐츠 수출사의 현지화 품질 편차와 납기 지연",
     "전문 감수 결합 현지화 파이프라인", "동남아 진출 콘텐츠 제작사", "임팩트"),
    ("mobility_fleet", "법인 차량 운행 기록 수기 관리로 인한 보험·정산 누수",
     "OBD 기반 운행 자동 기록·정산", "차량 30대 이상 운용 법인", "비용"),
    ("beauty_devices", "피부관리샵의 고가 장비 투자 부담과 가동률 저하",
     "구독형 장비 공급과 시술 데이터 관리", "1인샵·소형 피부관리샵 원장", "매출"),
]
_REGIONS = ["한국", "베트남", "태국", "인도네시아", "일본", "싱가포르"]
_CITIES = {"한국": ["서울", "부산", "성수동", "판교"], "베트남": ["하노이", "호치민"],
           "태국": ["방콕"], "인도네시아": ["자카르타"], "일본": ["도쿄", "오사카"],
           "싱가포르": [None]}
_NAME_A = ["한올", "성진", "다온", "미리내", "온새미", "누리", "가온", "새빛",
           "이든", "라온", "해솔", "도담"]
_NAME_B = ["테크", "랩스", "웍스", "그룹", "솔루션", "파트너스", "시스템즈",
           "컴퍼니", "네트웍스", "AI"]
_REFS = ["첫 파일럿 완료", "PoC 2건 진행", "대기업 1사 납품", "공공기관 시범사업",
         None, None]   # None 비중 — 레퍼런스 없는 초기 기업도 흔하다
_TRACTIONS = ["유료 전환 3건", "MRR 800만원", "재계약 1건", None, None]
_WILLING = ["매우 적극적", "적극적", "중간", None]

_OPTIONAL_KEYS = ["도시", "레퍼런스", "트랙션", "판매의향", "설립"]


def _company_name(rng: random.Random) -> str:
    return rng.choice(_NAME_A) + rng.choice(_NAME_B)


def _synth_source(rng: random.Random) -> str:
    """결정적 '키: 값' 프로필 원문 1건 합성."""
    industry, problem, solution, target, vp = rng.choice(_DOMAINS)
    region = rng.choice(_REGIONS)
    city = rng.choice(_CITIES[region])
    lines = [f"이름: {_company_name(rng)}", f"국가: {region}"]
    if city and rng.random() < 0.7:
        lines.append(f"도시: {city}")
    lines += [f"산업: {industry}",
              f"설명: {target}을 위한 {solution.split(',')[0]} 사업",
              f"문제: {problem}", f"솔루션: {solution}", f"타겟: {target}",
              f"판매가치: {vp}"]
    if rng.random() < 0.5:
        lines.append(f"구매가치: {rng.choice(['매출', '비용', '문제해결'])}")
    ref = rng.choice(_REFS)
    if ref:
        lines.append(f"레퍼런스: {ref}")
    tr = rng.choice(_TRACTIONS)
    if tr:
        lines.append(f"트랙션: {tr}")
    w = rng.choice(_WILLING)
    if w:
        lines.append(f"판매의향: {w}")
    if rng.random() < 0.3:
        lines.append(f"설립: {rng.randint(2015, 2025)}")
    return "\n".join(lines)


def _label_from_source(source: str) -> str:
    """결정적 라벨 재유도 — 엔진의 실제 파서·상 합성을 그대로 사용 (환각 구조 불가)."""
    from .engine.represent import _mock_extract
    profile, open_questions = _mock_extract(source)
    return json.dumps({"profile": profile.model_dump(mode="json"),
                       "open_questions": open_questions}, ensure_ascii=False)


def _example(source: str, *, subject: str, strategy: str,
             parent: "str | None" = None) -> dict:
    from .engine.prompts import EXTRACT_SYSTEM
    return {
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": source},
            {"role": "assistant", "content": _label_from_source(source)},
        ],
        "meta": {"kind": "represent", "subject": subject,
                 "structured_input": True, "label_source": "deterministic",
                 "augmented": strategy != "synthesize", "strategy": strategy,
                 **({"parent": parent} if parent else {})},
    }


def synthesize(n: int, seed: int = 42) -> list[dict]:
    """합성 코퍼스 — 조합 공간에서 n건. 같은 (seed, n)이면 항상 같은 결과."""
    rng = random.Random(seed)
    out, seen = [], set()
    attempts = 0
    while len(out) < n and attempts < n * 20:
        attempts += 1
        source = _synth_source(rng)
        fp = hashlib.sha256(source.encode()).hexdigest()
        if fp in seen:
            continue
        seen.add(fp)
        subject = source.splitlines()[0].split(":", 1)[1].strip()
        out.append(_example(source, subject=subject, strategy="synthesize"))
    return out


# ── 증강 전략 — 기존 예제 1건 → 변형 예제 ─────────────────────────────

def _all_contents(ex: dict) -> list[str]:
    return [m["content"] for m in ex["messages"]]


def _sub_all(ex: dict, mapping: dict[str, str]) -> dict:
    """모든 메시지에 동시 치환 — 긴 키부터 (부분 문자열 오치환 방지)."""
    new = json.loads(json.dumps(ex, ensure_ascii=False))
    for old in sorted(mapping, key=len, reverse=True):
        for m in new["messages"]:
            m["content"] = m["content"].replace(old, mapping[old])
    return new


def entity_substitution(ex: dict, rng: random.Random) -> "dict | None":
    """회사명 일관 치환 — 입력·라벨 양쪽 동시. 치환 후 원 명칭이 남으면 실패(None).

    지명·산업은 치환하지 않는다: judge류 라벨은 지역·산업이 결정에 들어가므로
    (stage_compatibility·인접성 보정) 바꾸면 라벨 정합이 깨질 수 있다.
    회사명은 판단·추출 어디에도 의미 기여가 없어 안전하다.
    """
    subject = ex.get("meta", {}).get("subject")
    if not subject or len(subject) < 2:
        return None
    new_name = _company_name(rng)
    while new_name == subject:
        new_name = _company_name(rng)
    new = _sub_all(ex, {subject: new_name})
    if any(subject in c for c in _all_contents(new)):   # 잔존 = 불완전 치환
        return None
    new["meta"] = {**new["meta"], "subject": new_name, "augmented": True,
                   "strategy": "entity_substitution",
                   "parent": fingerprint(ex)}
    return new


def shuffle_lines(ex: dict, rng: random.Random) -> "dict | None":
    """'키: 값' 라인 셔플 — 구조화 입력 전용. 추출은 순서 불변이므로 라벨 유지.
    이름 라인은 맨 앞 고정(주체 고정 규칙과의 혼선 방지)."""
    if not ex.get("meta", {}).get("structured_input"):
        return None
    user = next(m for m in ex["messages"] if m["role"] == "user")
    lines = user["content"].splitlines()
    if len(lines) < 4 or not all(":" in ln for ln in lines if ln.strip()):
        return None
    head = [ln for ln in lines if ln.startswith("이름:")]
    rest = [ln for ln in lines if not ln.startswith("이름:")]
    rng.shuffle(rest)
    new = json.loads(json.dumps(ex, ensure_ascii=False))
    next(m for m in new["messages"] if m["role"] == "user")["content"] = \
        "\n".join(head + rest)
    new["meta"] = {**new["meta"], "augmented": True, "strategy": "shuffle_lines",
                   "parent": fingerprint(ex)}
    return new


def field_dropout(ex: dict, rng: random.Random) -> "dict | None":
    """선택 필드 제거 + 라벨 결정적 재유도 — 희소 프로필 학습 신호.

    라벨을 손으로 고치지 않는다: 줄어든 입력을 엔진 파서에 다시 태워 라벨을
    재유도한다(보강 질문까지 정확해진다). 전문가 라벨(trajectory) 예제에는
    적용 불가 — 재유도가 전문가 품질을 재현할 수 없으므로 거른다.
    """
    meta = ex.get("meta", {})
    if meta.get("label_source") != "deterministic" \
            or not meta.get("structured_input"):
        return None
    user = next(m for m in ex["messages"] if m["role"] == "user")
    lines = user["content"].splitlines()
    droppable = [i for i, ln in enumerate(lines)
                 if ln.split(":", 1)[0].strip() in _OPTIONAL_KEYS]
    if not droppable:
        return None
    k = rng.randint(1, len(droppable))
    drop = set(rng.sample(droppable, k))
    source = "\n".join(ln for i, ln in enumerate(lines) if i not in drop)
    return {**_example(source, subject=meta.get("subject", "미상"),
                       strategy="field_dropout", parent=fingerprint(ex)),
            }


def llm_paraphrase(ex: dict, client, rng: random.Random) -> "dict | None":
    """LLM 패러프레이즈 + 그라운딩 게이트 — client는 버스트 주간 자체 vLLM
    (OpenAI 호환) 어댑터. client(text)->text. 없으면(평상시·테스트) 건너뛴다.

    게이트: 라벨의 stated 3필드(문제·솔루션·타겟) 값이 패러프레이즈된 원문에
    여전히 그라운딩되는가 — R1 강등과 동일한 3-gram 포함도·동일 임계(0.15).
    떨어지면 라벨-입력 정합이 깨진 것 → 폐기. 폐기는 집계에 드러난다.
    """
    if client is None:
        return None
    user = next(m for m in ex["messages"] if m["role"] == "user")
    try:
        para = client(user["content"])
    except Exception:
        return None
    if not para or para.strip() == user["content"].strip():
        return None
    from .engine.represent import _GROUND_DEMOTE_THRESHOLD
    from .engine.vision import grounding_score
    label = json.loads(next(m for m in ex["messages"]
                            if m["role"] == "assistant")["content"])
    prof = label.get("profile", label)
    for name in ("problem_solved", "solution", "target_customer"):
        f = prof.get(name) or {}
        if f.get("provenance") == "stated" and f.get("value"):
            g = grounding_score(f["value"], para)
            if g is not None and g < _GROUND_DEMOTE_THRESHOLD:
                return None   # stated 근거 소실 — 오염 증강 폐기
    new = json.loads(json.dumps(ex, ensure_ascii=False))
    next(m for m in new["messages"] if m["role"] == "user")["content"] = para
    new["meta"] = {**new["meta"], "augmented": True, "strategy": "llm_paraphrase",
                   "structured_input": False, "parent": fingerprint(ex)}
    return new


# ── 파이프라인 ────────────────────────────────────────────────────────

def fingerprint(ex: dict) -> str:
    return hashlib.sha256(json.dumps(
        [m["content"] for m in ex["messages"]],
        ensure_ascii=False).encode()).hexdigest()


def augment(examples: list[dict], factor: int = 4, seed: int = 42,
            llm_client=None) -> tuple[list[dict], dict]:
    """원본 1건당 최대 (factor-1)건 변형 생성. 반환: (전체, 정직 집계).

    전략을 순환 적용하고, 내용 지문으로 중복 제거한다. 같은 (입력, seed)면
    항상 같은 출력 — 재현·감사 가능.
    """
    rng = random.Random(seed)
    strategies = [entity_substitution, shuffle_lines, field_dropout]
    if llm_client is not None:
        strategies.append(
            lambda e, r: llm_paraphrase(e, llm_client, r))
    out = list(examples)
    seen = {fingerprint(e) for e in examples}
    tally = {"originals": len(examples), "generated": 0, "dedup_dropped": 0,
             "gate_rejected": 0, "by_strategy": {}}
    for ex in examples:
        made = 0
        for i in range(factor * 2):            # 실패 여유 — 목표는 factor-1
            if made >= factor - 1:
                break
            strat = strategies[i % len(strategies)]
            variant = strat(ex, rng)
            if variant is None:
                if strat not in (entity_substitution, shuffle_lines,
                                 field_dropout):
                    tally["gate_rejected"] += 1   # 패러프레이즈 게이트 탈락
                continue
            fp = fingerprint(variant)
            if fp in seen:
                tally["dedup_dropped"] += 1
                continue
            seen.add(fp)
            out.append(variant)
            made += 1
            tally["generated"] += 1
            s = variant["meta"]["strategy"]
            tally["by_strategy"][s] = tally["by_strategy"].get(s, 0) + 1
    return out, tally


def check_no_leak(examples: list[dict], seal: dict) -> list[str]:
    """봉인 규율 — 증강 결과에 held-out 주체가 섞였는지. 위반 목록 반환."""
    held_subjects = set(seal.get("subjects", []))
    return [f"주체 누수: {s}" for s in
            {e.get("meta", {}).get("subject") for e in examples}
            if s in held_subjects]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="SFT 증강 — 합성 + 결정적 변형 (train 전용, held-out 불가침)")
    ap.add_argument("--sft-in", help="dataset.py가 만든 train_sft.jsonl (없으면 합성만)")
    ap.add_argument("--out", default="dataset/train_aug.jsonl")
    ap.add_argument("--synth", type=int, default=500, help="합성 코퍼스 건수")
    ap.add_argument("--factor", type=int, default=4, help="원본당 최대 배수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--seal", help="dataset/seal.json — 봉인 누수 검사")
    args = ap.parse_args()

    base = _read_jsonl(Path(args.sft_in)) if args.sft_in else []
    synth = synthesize(args.synth, seed=args.seed)
    all_examples, tally = augment(base + synth, factor=args.factor,
                                  seed=args.seed)
    tally["synthesized"] = len(synth)
    tally["from_trajectories"] = len(base)

    if args.seal:
        seal = json.loads(Path(args.seal).read_text(encoding="utf-8"))
        leaks = check_no_leak(all_examples, seal)
        if leaks:
            raise SystemExit("❌ 봉인 위반:\n" + "\n".join(leaks))
        tally["seal_checked"] = True

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    tally["total"] = len(all_examples)
    print(json.dumps(tally, ensure_ascii=False, indent=2))
    print(f"\n✅ {out} — 총 {len(all_examples)}건 "
          f"(궤적 {len(base)} + 합성 {len(synth)} + 변형 {tally['generated']})")


if __name__ == "__main__":
    main()

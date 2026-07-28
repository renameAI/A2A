"""판단 축 정의 로더 — judge_cases/buyer_ontology.yaml이 정규 소스.

축 이름·핵심질문·판정선(verdict_rule)을 프롬프트에 복붙해두면 yaml이 갱신될 때마다
조용히 어긋난다. 여기서 한 번 읽어 프롬프트와 스키마 enum이 같은 출처를 보게 한다.

seller_ontology.yaml은 **판단 루브릭이 아니다** — 축마다 readout(신호 판독)→moves
(대응 행동)이고 verdict_rule이 없다. "내가 잘 팔고 있나"의 실행 플레이북이라
judge가 아니라 협상·작문 단계의 재료다. 그래서 여기선 buy 축만 싣는다.
"""
from functools import lru_cache
from pathlib import Path

_YAML = Path(__file__).resolve().parent.parent.parent / "judge_cases" / "buyer_ontology.yaml"


@lru_cache(maxsize=1)
def judge_axes() -> list[dict]:
    """[{id, name, core_value, verdict_rule, purpose_by_buyer_type}] — 없으면 빈 목록."""
    try:
        import yaml
        data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))["ontology"]["bases"]
    except Exception:                       # noqa: BLE001 — 재료 문제로 판단을 막지 않는다
        return []
    return [{"id": b["id"], "name": b.get("name", ""),
             "core_value": b.get("core_value", ""),
             "verdict_rule": b.get("verdict_rule", ""),
             # 상대(구매자)가 이 축에서 실제로 던지는 질문 — 박사님 케이스에서
             # 추출된 실문장이다. UI가 A2A 왕복을 보여줄 때 상대 에이전트의
             # 대사로 그대로 쓴다(새로 지어내지 않는다).
             "questions": b.get("questions") or [],
             # BB1에만 있는 '구매자 유형별 목적' 매핑 — 상대가 CSR 조직인지 지자체인지
             # 유통사인지에 따라 '무엇을 목적으로 사는가'가 통째로 다르다.
             "purpose_by_buyer_type": b.get("purpose_by_buyer_type") or {}}
            for b in data]


@lru_cache(maxsize=1)
def axis_block() -> str:
    """프롬프트에 넣을 축 정의 블록. 축마다 '무엇을 보는가 + 판정선'을 함께 준다.

    BB1의 purpose_by_buyer_type도 함께 싣는다 — 예전엔 이 필드를 로더가 버려서
    judge가 **모든 상대를 같은 잣대로** 목적 정합을 쟀다. 대기업 CSR팀(ESG KPI)과
    지자체(정책 실적)와 유통사(내 고객이 사는가)는 사는 이유가 완전히 다른데,
    구분 없이 재면 셋 다 애매하게 나온다. 스카우트처럼 상대 유형이 다양한 경로에서
    특히 치명적이었다.
    """
    axes = judge_axes()
    if not axes:
        return ""
    lines = []
    for a in axes:
        lines.append(f"- {a['id']} ({a['name']}): {a['core_value']}")
        if a["verdict_rule"]:
            lines.append(f"    판정선 — {a['verdict_rule']}")
        if a["purpose_by_buyer_type"]:
            lines.append("    ⚠ 상대 유형을 먼저 정하고 그 유형의 목적으로 판정한다 "
                         "(유형이 안 잡히면 status=unknown):")
            for t, purpose in a["purpose_by_buyer_type"].items():
                lines.append(f"      · {t} → {purpose}")
    return "\n".join(lines)

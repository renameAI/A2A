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
    """[{id, name, core_value, verdict_rule}] — 파일이 없으면 빈 목록(프롬프트만 축약)."""
    try:
        import yaml
        data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))["ontology"]["bases"]
    except Exception:                       # noqa: BLE001 — 재료 문제로 판단을 막지 않는다
        return []
    return [{"id": b["id"], "name": b.get("name", ""),
             "core_value": b.get("core_value", ""),
             "verdict_rule": b.get("verdict_rule", "")} for b in data]


@lru_cache(maxsize=1)
def axis_block() -> str:
    """프롬프트에 넣을 축 정의 블록. 축마다 '무엇을 보는가 + 판정선'을 함께 준다."""
    axes = judge_axes()
    if not axes:
        return ""
    lines = []
    for a in axes:
        lines.append(f"- {a['id']} ({a['name']}): {a['core_value']}")
        if a["verdict_rule"]:
            lines.append(f"    판정선 — {a['verdict_rule']}")
    return "\n".join(lines)

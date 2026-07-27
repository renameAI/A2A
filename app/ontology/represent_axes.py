"""represent 추출 온톨로지 로더 — represent_ontology.yaml이 정규 소스.

axes.py(judge)와 같은 역할을 represent에서 한다. 규율을 프롬프트 산문에만 두면
두 가지가 반복해서 깨졌다:
  · 하나를 조이면 다른 하나가 무너진다 — 자기참조를 막으면 구체성이 죽고, 느슨하게
    하면 자기참조가 돌아왔다(실측: 하루 5회 재작성, 매번 한쪽이 무너짐).
  · 코드와 프롬프트가 각자 규칙을 들고 있어 계약이 어긋난다 — ground_profile의
    금칙 동사 목록과 프롬프트의 지시가 따로 관리되면 조용히 벌어진다.
이제 프롬프트 블록도, 코드 게이트(R5)도 이 한 파일에서 나온다.
"""
from functools import lru_cache
from pathlib import Path

_YAML = Path(__file__).resolve().parent / "represent_ontology.yaml"


@lru_cache(maxsize=1)
def _onto() -> dict:
    try:
        import yaml
        return yaml.safe_load(_YAML.read_text(encoding="utf-8"))["ontology"]
    except Exception:                      # noqa: BLE001 — 재료 문제로 추출을 막지 않는다
        return {}


def customer_purpose() -> dict:
    """고객 유형 → 그 유형이 돈을 쓰는 목적. buyer_ontology.BB1과 같은 축."""
    return _onto().get("customer_purpose") or {}


def field_rule(field_id: str) -> dict:
    """필드별 계약 — subject·금칙어·회사명 금칙·최소 구체 사실 수."""
    for b in _onto().get("bases") or []:
        if b.get("id") == field_id:
            return b
    return {}


@lru_cache(maxsize=1)
def extract_block() -> str:
    """EXTRACT 프롬프트에 넣을 규율 블록 — 온톨로지에서 렌더링한다."""
    o = _onto()
    if not o:
        return ""
    lines: list[str] = []
    if o.get("principles"):
        lines.append("■ 원칙 (represent 온톨로지 v%s):" % o.get("version", "?"))
        lines += [f"- {p}" for p in o["principles"]]
    cp = o.get("customer_purpose") or {}
    if cp:
        lines.append("")
        lines.append("■ 고객 유형을 먼저 정한다 — 유형마다 '무엇을 위해 돈을 쓰는가'가 다르다.")
        lines.append("  (Judge의 BB1 판정도 같은 축을 쓴다 — 여기서 정한 유형이 그대로 이어진다)")
        lines += [f"  · {k} → {v}" for k, v in cp.items()]
    lines.append("")
    lines.append("■ 필드별 계약:")
    for b in o.get("bases") or []:
        subj = {"customer": "고객", "self": "우리"}.get(b.get("subject", ""), "?")
        lines.append(f"- {b['id']} ({b.get('name','')}) — 주어는 **{subj}**. "
                     f"{b.get('core_value','')}")
        for s in b.get("signals") or []:
            lines.append(f"    · {s}")
        if b.get("forbidden_self_name"):
            lines.append("    · 🚫 우리 회사 이름을 쓰지 않는다 (코드가 검사한다)")
        if b.get("forbidden_terms"):
            lines.append(f"    · 🚫 우리 행위 동사 금지 — {'·'.join(b['forbidden_terms'][:4])} 등 "
                         f"(코드가 검사한다). 떠올랐다면 그건 solution 칸 내용이다")
        if b.get("min_concrete_facts"):
            lines.append(f"    · 자료의 구체 사실(업무 단계·수치·고유명사) "
                         f"{b['min_concrete_facts']}개 이상 포함")
        if b.get("verdict_rule"):
            lines.append(f"    판정선 — {' '.join(str(b['verdict_rule']).split())}")
    return "\n".join(lines)

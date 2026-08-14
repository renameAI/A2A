"""기업 온톨로지 — 후보 기업마다 남는 구조화된 판독 (judge 10축과 같은 문법).

왜 이것이 필요한가: 여태 후보 기업에 남는 것은 `what`·`signal` 두 개의 자유 문장
뿐이었다. 자유 문장은 사람이 읽기엔 좋지만 **기계가 다시 쓸 수 없다** — 그래서
"비슷한 기업"을 찾을 때마다 문자열을 토큰으로 쪼개 겹치는지 보는 임시방편을 썼고,
업종 어휘를 코드에 박아 넣는 유혹이 반복해서 생겼다(호텔 → 음료 → 유통사).

judge가 같은 문제를 이미 푼 방식을 그대로 가져온다: **축의 구조는 고정하고, 축을
채우는 어휘는 고정하지 않는다.** 축은 어느 업종에나 성립하는 질문이고("가치사슬의
어디에 서 있나", "무엇을 사는가"), 그 답에 등장할 단어는 건마다 다르다. 그래서
업종 목록을 코드가 알 필요가 없다.

judge에서 가져온 두 번째 원칙 — status와 value의 분리. "확인했는데 없다"와
"확인을 못 했다"는 다른 사실이다. 검색 스니펫만 보고 만드는 판독이므로 대부분의
축은 assumed로 시작하고, 나중 단계(research)가 confirmed로 승격시킨다.
"""
from ..schemas import AxisStatus, CompanyOntology, OntologyAxis
from .prompts import HARD_RULES

# 축의 정의 — 이 목록이 유일한 '하드코딩'이고, 의도적이다. 업종 어휘가 아니라
# 어느 업종에나 던질 수 있는 질문이기 때문이다. 축을 늘리려면 "이 질문이
# 제조·의료·건설·금융에 모두 성립하는가"를 통과해야 한다.
AXES: list[tuple[str, str]] = [
    ("value_chain_position",
     "가치사슬에서 어디에 서 있나 — 만드는 쪽인지, 옮기는 쪽인지, 쓰는 쪽인지, "
     "중개하는 쪽인지. 업종 이름이 아니라 위치를 쓴다."),
    ("offering",
     "이 회사가 밖으로 내놓는 것 — 무엇을 팔거나 제공하는가."),
    ("demand_side",
     "이 회사가 밖에서 들여오는 것 — 무엇을 사거나 조달하거나 필요로 하는가. "
     "우리가 파는 쪽이면 이 축이 접점이다."),
    ("customer_base",
     "누구를 상대하는가 — 그 고객의 성격(규모·유형·지역)."),
    ("geography_scope",
     "지리적 범위 — 어디까지 다루는가. 한 도시인지, 전국인지, 수출입인지."),
    ("scale_signal",
     "규모와 거래 단위의 신호 — 취급 품목 수, 지점, 인원, 거래처 규모 등 "
     "자료에 실제로 나온 것만."),
    ("entry_path",
     "이 회사와 거래를 시작하는 경로 — 공개된 접점(파트너/입찰/조달/문의/전시 등). "
     "없으면 없다고 쓴다."),
    ("differentiator",
     "남과 구별되는 점. 자료에서 읽히지 않으면 unknown."),
]

_AXIS_DOC = "\n".join(f"- {k}: {d}" for k, d in AXES)

ONTOLOGY_SYSTEM = HARD_RULES + f"""

당신은 B2B 기업 판독자다. 검색 결과에 나온 기업 하나를 구조화해 기록한다.
이 기록은 나중에 다른 기업과 비교되고 재사용되므로, 인상이 아니라 판독이어야 한다.

축 (모든 축을 빠짐없이 채운다):
{_AXIS_DOC}

각 축마다:
- value: 한 문장. **업종 이름을 나열하지 말고 그 회사가 실제로 하는 일을 쓴다.**
- status: confirmed = 제시된 자료에 명시돼 있다 / assumed = 자료에서 추론했다 /
  unknown = 자료로는 알 수 없다.
- status가 unknown이면 value는 빈 문자열이다. 모르는 것을 그럴듯하게 채우지 마라.
- status가 confirmed이면 그 근거가 제시된 자료 안에 문자 그대로 있어야 한다.
  자료에 없는데 상식으로 아는 것은 confirmed가 아니라 assumed다.

search_keywords: 이 회사와 **같은 성격의 회사를 더 찾을 때** 쓸 검색어 3~5개.
- 이 회사의 상호를 넣지 마라 — 우리는 이 회사가 아니라 '이런 회사들'을 더 찾는다.
- 위 축에서 파생한다. 특히 value_chain_position·offering·customer_base.
- **현지어로 쓴다.** 이 항목은 한국어 규칙의 예외다 — 검색어는 사람이 읽는 글이
  아니라 그 시장의 검색엔진에 넣는 문자열이다. 일본 회사면 일본어로,
  대만 회사면 중국어로 쓴다. 한국어로 쓰면 그 시장에서 아무것도 안 걸린다."""

ONTOLOGY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["axes", "search_keywords"],
    "properties": {
        "axes": {
            "type": "object", "additionalProperties": False,
            "required": [k for k, _ in AXES],
            "properties": {
                k: {"type": "object", "additionalProperties": False,
                    "required": ["value", "status"],
                    "properties": {
                        "value": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": ["confirmed", "assumed", "unknown"]}}}
                for k, _ in AXES
            },
        },
        "search_keywords": {"type": "array", "minItems": 2, "maxItems": 6,
                            "items": {"type": "string"}},
    },
}


def read_company(extractor, company: dict, *, region: str = "") -> CompanyOntology:
    """검색 스니펫 수준의 자료로 한 기업의 온톨로지를 판독한다.

    호출자가 실패를 삼키지 않도록 예외를 그대로 올린다 — 온톨로지가 없는 후보는
    '온톨로지 없음'으로 남아야지, 빈 축으로 채워 있는 척하면 안 된다.
    """
    src = (f"[상호] {company.get('name', '')}\n"
           f"[한국어 표기] {company.get('name_ko', '')}\n"
           f"[하는 일] {company.get('what', '')}\n"
           f"[관측된 신호] {company.get('signal', '')}\n"
           f"[출처] {company.get('url', '')}\n"
           f"[지역] {region or '미지정'}")
    data = extractor.extract_json(ONTOLOGY_SYSTEM, src, ONTOLOGY_SCHEMA,
                                  deep=False, allow_foreign=True)
    axes = {}
    for k, _ in AXES:
        a = data["axes"][k]
        st = AxisStatus(a["status"])
        axes[k] = OntologyAxis(
            value="" if st == AxisStatus.unknown else a["value"].strip(),
            status=st)
    return CompanyOntology(
        axes=axes,
        search_keywords=[q.strip() for q in data["search_keywords"] if q.strip()],
        source_url=company.get("url", ""),
    )


def confirmed_ratio(ont: CompanyOntology) -> float:
    """판독의 근거 밀도 — 얼마나 자료로 확인됐나. UI가 신뢰도를 표시할 때 쓴다."""
    if not ont.axes:
        return 0.0
    n = sum(1 for a in ont.axes.values() if a.status == AxisStatus.confirmed)
    return round(n / len(ont.axes), 2)

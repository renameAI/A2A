"""하드 팩트 마이너 — 크롤링·파싱된 자유 원문에서 검증 가능한 사실을 결정적으로 캐낸다.

배경: mock 경로는 '키: 값' 라인만 읽어서 실제 IR·홈페이지 원문(가장 중요한 하드
팩터)을 통째로 버렸다. LLM 없이도 정직하게 건질 수 있는 것은 건진다 — 모든 추출은
원문의 '부분문자열 그대로'다(요약·의역·생성 없음). 그래서 환각이 구조적으로
불가능하고, provenance=stated가 정당하다.

캐내는 것: 설립연도 · 수치 문장(매출/고객 수/계약 규모...) · 고객·파트너 신호 문장 ·
인증·수상·특허 문장 · 대표 설명 문단 · 지역 힌트. 문제/솔루션/타겟 같은 '해석'이
필요한 필드는 여기서 만들지 않는다 — 그건 LLM 또는 사람(사전 입력·보강 질문)의 몫.
"""
import re
from dataclasses import asdict, dataclass, field

_YEAR = re.compile(r"((?:19|20)\d{2})\s*년(?:에)?\s*(?:법인\s*)?(?:설립|창업|창립)")
_METRIC = re.compile(
    r"\d[\d,.]*\s*(?:억|천만|백만|만\s*원|만원|만\s*명|명|개사|개\s*지점|지점|건|%|"
    r"호점|배|회|톤|위|개국|개\s*매장|매장)")
_CLIENT_KW = ("고객사", "파트너", "도입", "납품", "입점", "공급", "협업 사례",
              "레퍼런스", "클라이언트", "거래처")
_CERT_KW = ("특허", "인증", "수상", "선정", "벤처기업", "ISO", "HACCP", "FDA",
            "CE ", "GS인증")
_REGIONS = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
            "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주")

_MAX_SENT_LEN = 160
_MAX_PER_BUCKET = 5


@dataclass
class HardFacts:
    founded_year: "int | None" = None
    description: str = ""                       # 첫 의미 문단 (원문 그대로)
    metric_sentences: list = field(default_factory=list)
    client_sentences: list = field(default_factory=list)
    cert_sentences: list = field(default_factory=list)
    regions: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return (len(self.metric_sentences) + len(self.client_sentences)
                + len(self.cert_sentences) + (1 if self.founded_year else 0))

    def as_dict(self) -> dict:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    """단순 결정적 문장 분리 — 마침표·줄바꿈 기준. NLP 없음."""
    parts = re.split(r"(?<=[.!?다요])\s+|\n+", text)
    out = []
    for p in parts:
        p = " ".join(p.split())                 # 공백 정규화
        if 8 <= len(p) <= _MAX_SENT_LEN:
            out.append(p)
    return out


def _looks_structured(line: str) -> bool:
    """'키: 값' 정형 라인 — mock 파서 몫이므로 마이너는 건드리지 않는다."""
    return bool(re.match(r"^[\w가-힣 ]{1,12}:\s", line))


def mine_hard_facts(full_text: str) -> HardFacts:
    facts = HardFacts()
    if not full_text or not full_text.strip():
        return facts

    m = _YEAR.search(full_text)
    if m:
        facts.founded_year = int(m.group(1))

    # 버킷 우선순위: 구체적 키워드(인증·고객)가 일반 신호(숫자 포함)보다 먼저 —
    # "특허 2건 보유" 같은 문장은 인증 신호이지 매출 지표가 아니다.
    seen: set[str] = set()
    for sent in _sentences(full_text):
        if _looks_structured(sent) or sent in seen:
            continue
        seen.add(sent)
        if len(facts.cert_sentences) < _MAX_PER_BUCKET \
                and any(kw in sent for kw in _CERT_KW):
            facts.cert_sentences.append(sent)
            continue                            # 한 문장은 한 버킷에만
        if len(facts.client_sentences) < _MAX_PER_BUCKET \
                and any(kw in sent for kw in _CLIENT_KW):
            facts.client_sentences.append(sent)
            continue
        if len(facts.metric_sentences) < _MAX_PER_BUCKET and _METRIC.search(sent):
            facts.metric_sentences.append(sent)

    # 대표 설명 문단 — 정형 라인이 아닌 첫 '충분히 긴' 문장 (브랜드 소개는 짧을 수 있다)
    for sent in _sentences(full_text):
        if not _looks_structured(sent) and 25 <= len(sent):
            facts.description = sent
            break

    facts.regions = [r for r in _REGIONS if r in full_text][:3]
    return facts

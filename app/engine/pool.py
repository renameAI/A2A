"""이중 풀 시드 데이터 (RET-04, 기획서 6.4).

CoT 샘플 #01~#04의 케이스를 v0 시드로 옮긴 것.
- members: 가입 회원 풀 (양면 정보 보유)
- external: 외부 기업 풀 — buy-side·Willingness 공란이 정상 (외부 풀 계약)
pain_points 는 엔진 내부 검색 인덱스용 텍스트 (API로 노출되지 않음).
Phase 2에서 실제 수집 파이프라인 + 벡터DB(OpenSearch)로 교체.
"""
from dataclasses import dataclass, field

from ..schemas import (PoolKind, Profile, BasicInfo, ProvField, Provenance,
                       ValueProp, Willingness)


@dataclass
class CandidateRecord:
    company_id: str
    pool: PoolKind
    profile: Profile
    pain_points: str          # 이 회사가 '겪는 문제' (buy-side 검색 면)
    tags: list[str] = field(default_factory=list)


def _stated(v: str) -> ProvField:
    return ProvField(value=v, provenance=Provenance.stated)


def _inferred(v: str, conf: float = 0.7) -> ProvField:
    return ProvField(value=v, provenance=Provenance.inferred, confidence=conf)


SEED_POOL: list[CandidateRecord] = [
    # ── CoT #01·#02 — 리비 하노이 함롱 ──
    CandidateRecord(
        company_id="ext-livi-hanoi",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="리비 하노이 함롱", country="베트남", city="하노이",
                            industry="hospitality"),
            description="하노이 호안끼엠 중심부 아파트 호텔. 입지·교통 우수. "
                        "노후 객실 보유, 객실 매출 정체, 리뉴얼 자본 부담. 객실 수 적음.",
            problem_solved=_inferred("노후 객실의 매출 정체 해소"),
            solution=_inferred("아파트 호텔 숙박 운영", 0.6),
            target_customer=_inferred("하노이 방문 관광객"),
            references=[],
        ),
        pain_points="노후 객실 매출 정체, 리뉴얼 자본 부담, 객실 단가·점유율 정체",
        tags=["노후 객실", "매출 정체", "리뉴얼 자본 부담"],
    ),
    # ── CoT #03 — 방콕 중가 호텔 ──
    CandidateRecord(
        company_id="ext-bangkok-mid",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="방콕 시내 중가 호텔", country="태국", city="방콕",
                            industry="hospitality"),
            description="방콕 시내 한복판 중간 가격대 호텔. 투숙객 대부분 2030 외국인 관광객. "
                        "노후 객실 일부 보유, 경쟁 호텔 대비 차별화 필요.",
            problem_solved=_inferred("객실 차별화 부재로 인한 매출 정체"),
            solution=_inferred("중가 호텔 숙박 운영", 0.6),
            target_customer=_inferred("2030 외국인 관광객"),
            references=[],
        ),
        pain_points="노후 객실, 차별화 부재, 객실 매출 정체, 2030 외국인 관광객 트렌드",
        tags=["노후 객실", "차별화 필요", "2030 관광객"],
    ),
    # ── CoT #04 — 카사블랑카 오디세이 (인바운드·Willingness 높음) ──
    CandidateRecord(
        company_id="ext-casablanca-odyssee",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="카사블랑카 오디세이", country="모로코", city="카사블랑카",
                            industry="hospitality"),
            description="카사블랑카 호텔. 노후 객실 다수, 오너가 한국 문화에 호감. "
                        "객실 리뉴얼로 매출 상승 희망.",
            problem_solved=_inferred("노후 객실 리뉴얼과 매출 상승"),
            solution=_inferred("호텔 숙박 운영", 0.6),
            target_customer=_inferred("카사블랑카 방문객"),
            references=[],
            willingness_purchase=Willingness.high,   # 인바운드 선요청 이력
        ),
        pain_points="노후 객실 다수, 객실 매출 정체, 리뉴얼 희망, 한국 문화 호감",
        tags=["노후 객실", "매출 정체", "한국 문화 호감"],
    ),
    # ── distractor: 신축 럭셔리 호텔 — 노후 문제 없음 (온톨로지 배제 예시, 6.2-b) ──
    CandidateRecord(
        company_id="ext-hanoi-luxury-new",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="하노이 신축 럭셔리 호텔", country="베트남", city="하노이",
                            industry="hospitality"),
            description="신축 럭셔리 호텔. 최신 시설과 프리미엄 브랜딩 완비.",
            problem_solved=_inferred("프리미엄 브랜드 인지도 확대", 0.5),
            solution=_inferred("럭셔리 숙박 운영", 0.6),
            target_customer=_inferred("하이엔드 여행객"),
            references=[],
        ),
        pain_points="프리미엄 브랜드 인지도, 하이엔드 마케팅",
        tags=["신축", "럭셔리"],
    ),
    # ── distractor: 동종 경쟁사 — 유사도 검색이면 이런 회사가 나온다 (RET-02) ──
    CandidateRecord(
        company_id="ext-competitor-interior",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="현지 인테리어 시공사", country="베트남", city="호치민",
                            industry="hospitality_renovation"),
            description="호텔 객실 인테리어 시공 전문 업체. 통일된 인테리어 시공.",
            problem_solved=_inferred("호텔 인테리어 노후화 시공", 0.6),
            solution=_inferred("객실 인테리어 일괄 시공"),
            target_customer=_inferred("현지 호텔"),
            references=[],
        ),
        pain_points="신규 시공 수주 부족, 해외 발주처 확보",
        tags=["인테리어 시공"],
    ),
    # ── deal-breaker 테스트용: 글로벌 대기업 (사업단계 근본 부적합, JDG-04) ──
    CandidateRecord(
        company_id="ext-global-aero",
        pool=PoolKind.external,
        profile=Profile(
            basic=BasicInfo(name="글로벌 에어로 컴퍼니", country="미국",
                            industry="manufacturing"),
            description="글로벌 대기업 항공기 제조사. 대규모 부품 조달 체계 보유.",
            problem_solved=_inferred("항공기 부품 공급망 관리", 0.6),
            solution=_inferred("항공기 제조"),
            target_customer=_inferred("항공사"),
            references=["다수 국제 항공사"],
        ),
        pain_points="부품 공급망 다변화",
        tags=["항공 제조", "대기업"],
    ),
    # ── 가입 회원 풀: 다이브인그룹 (판매자, CoT #01) ──
    CandidateRecord(
        company_id="mem-divein",
        pool=PoolKind.members,
        profile=Profile(
            basic=BasicInfo(name="다이브인그룹", country="한국", city="서울",
                            industry="hospitality_renovation"),
            description="노후 호텔 객실을 예술 경험형 상품으로 전환하는 스타트업. "
                        "선투자 부담 없는 매출 쉐어 구조.",
            problem_solved=_stated("노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담"),
            solution=_stated("저자본·무철거 예술 경험형 객실 전환 (매출 쉐어)"),
            target_customer=_stated("노후 객실을 보유한 중소 호텔 오너"),
            references=["성수 Poco Hotel 전환", "한국 부티크 호텔 업셀링 데이터"],
            sell_value_props=[ValueProp.revenue_growth, ValueProp.cost_reduction],
            willingness_sell=Willingness.very_high,
        ),
        pain_points="해외 유통 파트너 부재, 동남아 레퍼런스 확보",
        tags=["예술 객실 전환", "매출 쉐어"],
    ),
]


def get_pool() -> list[CandidateRecord]:
    return SEED_POOL


def find(company_id: str) -> CandidateRecord | None:
    return next((r for r in SEED_POOL if r.company_id == company_id), None)

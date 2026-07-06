"""Consultant 모드 — 글로벌 진출 진단 인터뷰 (CON-01~02, 기획서 9장).

실제 인터뷰 시뮬레이션 3건(식품소재·소재 딥테크·하드웨어 부품)에서 검증된
방법론을 엔진화한 것. 검증된 패턴:
- 한 번에 하나의 슬롯만, 앞 답이 다음 질문을 결정 (좁히기)
- 회사의 상(像)에서 도출한 4~6지선다 + 힌트 (대표가 바로 고를 수 있게)
- 10개 슬롯이 확정되면 종료 판단 + 최종 아웃리치 가설 산출

엔진은 stateless — 인터뷰 히스토리는 매 호출에 전달받는다 (SYS-01).
"""
from .. import audit, progress
from ..config import get_settings
from ..errors import EngineError
from ..schemas import Profile
from .llm import get_extractor, sanitize
from .prompts import _CONSULT_SLOTS, CONSULT_SCHEMA, CONSULT_SYSTEM, consult_user

# ── Mock 경로 — 오프라인·CI용 스크립트 인터뷰 (LLM 계약과 동일 형태) ──

_MOCK_SCRIPT = [
    ("solution", "이번 글로벌 B2B 진출에서 가장 먼저 전면에 세울 제품/적용 분야는 무엇인가요?",
     "여러 분야를 다 들고 나가면 메시지가 약해집니다 — 첫 아웃리치는 하나로 좁힙니다.",
     [("핵심 제품/서비스 그대로", "프로필의 주력 솔루션을 전면에"),
      ("특정 적용 산업으로 좁힘", "가장 반응이 빠를 산업 하나"),
      ("부품/모듈/원료 공급", "완제품이 아니라 상대 제품에 들어가는 형태"),
      ("기술 라이선싱/공동개발", "직접 판매보다 파트너십 우선")], False),
    ("pain_point", "그 고객은 '왜' 새로운 대안을 찾고 있을까요? 탐색의 동기가 무엇인가요?",
     "표면 스펙이 아니라 고객이 새 대안을 찾는 이유를 먼저 잡아야 메시지가 섭니다.",
     [("기존 대안의 품질/성능 한계", "현상 유지로는 안 되는 이유"),
      ("비용/자원 부담", "지금 방식이 너무 비싸다"),
      ("규제/리스크 압박", "규제·안전·지속가능성 요구"),
      ("차별화 필요", "경쟁 대비 새로운 것이 필요하다")], True),
    ("segments", "첫 번째로 접근할 타겟 세그먼트는 어디인가요? (둘이면 A/B 테스트)",
     "성격이 다른 두 트랙이면 반응 속도를 비교하는 A/B가 유효합니다.",
     [("대기업 OI/R&D팀", "검증 체계·신뢰 중시"),
      ("동종 브랜드/제조사 R&D팀", "산업 적용성·차별화 중시"),
      ("공공 프로젝트/컨소시엄", "검증 트랙 — 느리지만 레퍼런스"),
      ("유통/파트너사", "판로 우선")], True),
    ("market", "1차 시장은 어디로 보시나요?",
     "시장마다 요구하는 검증 자료와 메시지가 다릅니다.",
     [("유럽", "규제·지속가능성 검증 논리가 강함"),
      ("북미", "상업성·속도 중시"),
      ("일본", "품질·신뢰 관계 중시"),
      ("동남아", "가격·성장성 중시")], False),
    ("recipient", "첫 콜드메일은 누구에게 보내는 게 맞을까요?",
     "첫 접점은 '검토를 여는 사람'이 중요합니다 — 기술 검토자는 그 다음입니다.",
     [("BD/Open Innovation 담당자", "외부 협업의 문을 여는 사람"),
      ("R&D/소재개발 담당자", "기술 검토 직행"),
      ("대표/경영진", "소규모 회사라면 직통"),
      ("구매/소싱 담당자", "이미 카테고리가 잡힌 경우")], True),
    ("cta", "첫 메일에서 요청할 1차 액션(CTA)은 무엇인가요?",
     "첫 CTA는 부담이 낮아야 합니다 — 계약·공동개발은 후속 단계입니다.",
     [("15~30분 온라인 미팅", "기본값 — 부담 최소"),
      ("샘플/데모 검토 요청", "제공물이 준비된 경우"),
      ("자료 공유 후 피드백", "가장 가벼운 진입")], False),
    ("proof_points", "첫 메시지에 앞세울 근거(proof point) 1~2개는 무엇인가요?",
     "'누가 관심을 보였다'보다 '지금 무엇을 제공·검증할 수 있다'가 강합니다.",
     [("샘플/데모 즉시 제공 가능", "말이 아니라 테스트 가능한 상태"),
      ("검증 데이터/성적서 보유", "안전성·성능의 객관 근거"),
      ("기존 계약/수출 실적", "상업적 검증"),
      ("수상/특허/투자 실적", "보조 신뢰 요소")], True),
    ("assets", "지금 바로 제공 가능한 자료/샘플/데모는 실제로 무엇인가요?",
     "제공물의 실체가 후속 전환 속도를 결정합니다. 없으면 없다고 정리합니다.",
     [("물리 샘플", "상대가 직접 테스트 가능"),
      ("데모 영상/Before·After", "시각 proof"),
      ("검사성적서/데이터시트", "문서 proof"),
      ("아직 준비 중", "제공물 준비가 선행 과제")], True),
    ("risk", "상대가 가장 먼저 걱정할 리스크는 무엇이고, 첫 메일에서 무엇을 선제적으로 낮출까요?",
     "상대의 첫 번째 의심을 첫 메일이 미리 풀어줘야 회신률이 오릅니다.",
     [("적용/장착 가능성", "우리 제품·공정에 실제로 들어가나"),
      ("규제/인증 대응", "우리 시장 규제를 통과하나"),
      ("가격/원가 부담", "감당 가능한 수준인가"),
      ("양산/공급 안정성", "스케일이 되나")], True),
    ("follow_up", "1차 CTA 이후의 전환 흐름은 어떻게 설계할까요?",
     "미팅 다음 단계가 정의되어야 인터뷰를 실행안으로 바꿀 수 있습니다.",
     [("미팅 → 샘플 테스트 → 파일럿 → 계약", "소재·부품형 표준 흐름"),
      ("미팅 → R&D팀 연결 → 공동 검증", "BD 경유형"),
      ("미팅 → PoC → OEM/라이선싱 논의", "하드웨어형"),
      ("미팅 → 파일럿 고객 온보딩", "SaaS형")], False),
]


def _mock_consult(profile: Profile, history: list[dict]) -> dict:
    filled = {k: None for k in _CONSULT_SLOTS}
    for i, turn in enumerate(history[:len(_MOCK_SCRIPT)]):
        filled[_MOCK_SCRIPT[i][0]] = turn["answer"]
    step = len(history)
    if step >= len(_MOCK_SCRIPT):
        name = profile.basic.name
        parts = [f"{name}의 1차 글로벌 진출 가설 (Mock 인터뷰 결과):"]
        parts += [f"- {slot}: {filled[slot]}" for slot in _CONSULT_SLOTS]
        return {"filled": filled, "done": True, "question": None,
                "why": "모든 슬롯이 확정되어 인터뷰를 종료합니다.",
                "options": [], "allow_multi": False,
                "hypothesis": "\n".join(parts)}
    slot, question, why, options, multi = _MOCK_SCRIPT[step]
    return {"filled": filled, "done": False, "question": question, "why": why,
            "options": [{"label": l, "hint": h} for l, h in options],
            "allow_multi": multi, "hypothesis": None}


# ── 메인 ────────────────────────────────────────────────────────────

def consult(profile: Profile, history: list[dict]) -> dict:
    """인터뷰 한 턴 진행. history = [{question, answer}, ...] (제품 레이어가 보유).

    반환: CONSULT_SCHEMA 형태 dict — done=false면 다음 질문+선택지,
    done=true면 최종 아웃리치 가설.
    """
    with progress.node("consult", "컨설턴트 인터뷰 턴"):
        extractor = get_extractor(get_settings())
        if extractor is not None:
            progress.log("컨설턴트", f"턴 {len(history) + 1} — 슬롯 공백 분석·질문 설계")
            data = sanitize(extractor.extract_json(
                CONSULT_SYSTEM, consult_user(profile, history), CONSULT_SCHEMA))
        else:
            progress.log("컨설턴트", f"턴 {len(history) + 1} — Mock 스크립트 인터뷰")
            data = _mock_consult(profile, history)

        # 계약 방어 — 약한 모델 대비 (질문 턴엔 선택지 필수)
        if not data.get("done"):
            if not data.get("question"):
                raise EngineError(502, "llm_error", "컨설턴트 질문 누락 — 재시도 필요")
            if not data.get("options"):
                data["options"] = [{"label": "직접 입력", "hint": "자유 서술로 답변"}]
        filled_count = sum(1 for v in (data.get("filled") or {}).values() if v)
        progress.log("컨설턴트",
                     f"슬롯 {filled_count}/{len(_CONSULT_SLOTS)} 확정"
                     + (" — 인터뷰 종료, 가설 산출" if data.get("done")
                        else f" — 다음 질문: {data['question'][:40]}..."))

    audit.record("consult", {                     # SYS-04 — 인터뷰도 데이터 자산
        "company": profile.basic.name,
        "turn": len(history) + 1,
        "history": history,
        "output": data,
    })
    return data

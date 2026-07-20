"""하드 팩트 마이너 — 크롤·파싱 원문이 실제로 프로필에 담기는지.

귤메달 실측 결함의 회귀 테스트: 실제 IR·홈페이지처럼 '키: 값' 정형 라인이 하나도
없는 자유 원문을 넣었을 때, 이전에는 전부 '미상'이 됐다.
"""
from app.engine.represent import _mock_extract
from app.ingest.mine import mine_hard_facts

# 실제 IR·홈페이지 스타일 자유 원문 — 정형 라인 없음 (귤메달 시나리오 재현)
_IR_STYLE = """귤메달은 제주 감귤을 새로운 방식으로 즐기는 디저트 브랜드입니다.
2021년 설립 이후 제주 본점을 시작으로 현재 전국 12개 매장을 운영하고 있습니다.
2024년 연 매출 18억 원을 달성했으며 재구매율은 43%에 이릅니다.
신세계백화점, 현대백화점에 입점해 유통 채널을 넓혔고 CJ올리브영과 협업 사례가 있습니다.
농림축산식품부 우수식품 인증을 받았으며 감귤 가공 특허 2건을 보유하고 있습니다.
서울과 부산에 플래그십 매장을 준비 중입니다."""


class TestMiner:
    def test_founded_year(self):
        assert mine_hard_facts(_IR_STYLE).founded_year == 2021

    def test_metric_sentences_are_verbatim(self):
        facts = mine_hard_facts(_IR_STYLE)
        assert facts.metric_sentences
        normalized = " ".join(_IR_STYLE.split())
        for s in facts.metric_sentences:      # 전부 원문 부분문자열 — 환각 불가 계약
            assert s in normalized

    def test_client_and_cert_signals(self):
        facts = mine_hard_facts(_IR_STYLE)
        assert any("입점" in s or "협업" in s for s in facts.client_sentences)
        assert any("인증" in s or "특허" in s for s in facts.cert_sentences)

    def test_description_from_first_paragraph(self):
        facts = mine_hard_facts(_IR_STYLE)
        assert "귤메달" in facts.description

    def test_structured_lines_left_to_parser(self):
        """'키: 값' 라인은 mock 파서 몫 — 마이너가 중복 수확하지 않는다."""
        facts = mine_hard_facts("트랙션: 매출 10억\n문제: 어떤 문제")
        assert not facts.metric_sentences
        assert facts.description == ""

    def test_empty_input(self):
        facts = mine_hard_facts("")
        assert facts.total == 0
        assert facts.as_dict()["metric_sentences"] == []

    def test_deterministic(self):
        assert mine_hard_facts(_IR_STYLE) == mine_hard_facts(_IR_STYLE)


class TestMockExtractWithMiner:
    def test_free_text_no_longer_all_unknown(self):
        """귤메달 회귀 — 자유 원문만 넣어도 하드 팩트가 프로필에 담긴다."""
        profile, _ = _mock_extract(_IR_STYLE, mine_hard_facts(_IR_STYLE))
        assert profile.basic.founded_year == 2021
        assert profile.basic.country == "한국"        # 지역 힌트(서울·부산)
        assert "귤메달" in profile.description
        assert profile.traction and "18억" in profile.traction
        assert profile.references                     # 입점·협업 문장
        assert "인증·수상 신호" in profile.portrait.assets

    def test_structured_lines_still_win(self):
        """정형 라인이 있으면 사용자가 명시한 값이 마이너보다 우선."""
        text = "이름: 귤메달\n설명: 직접 쓴 설명\n트랙션: 직접 쓴 트랙션\n" + _IR_STYLE
        profile, _ = _mock_extract(text, mine_hard_facts(text))
        assert profile.basic.name == "귤메달"
        assert profile.description == "직접 쓴 설명"
        assert profile.traction == "직접 쓴 트랙션"

    def test_core_fields_still_ask(self):
        """문제·솔루션·타겟은 해석이 필요한 필드 — 마이너가 지어내지 않는다."""
        profile, open_questions = _mock_extract(
            _IR_STYLE, mine_hard_facts(_IR_STYLE))
        assert profile.problem_solved.provenance.value == "ask"
        assert profile.solution.provenance.value == "ask"
        assert len(open_questions) >= 3

    def test_name_via_dialogue_line(self):
        """프론트가 기업명을 dialogue 정규 키('이름: X')로 보내면 파서가 읽는다."""
        profile, _ = _mock_extract("이름: 귤메달\n" + _IR_STYLE,
                                   mine_hard_facts(_IR_STYLE))
        assert profile.basic.name == "귤메달"

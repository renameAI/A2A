"""정화기 문자군 분리 회귀 테스트 (이슈 #6-D 품질 수정).

배경(실측): 한자·가나 제거는 EXAONE 환각 방어용인데, 해외 리드 발굴을 붙이자
일본 기업명·현지어 검색어·일본어 메일 본문을 통째로 지웠다
(株式会社ヤマト飲料 → ''). 제어문자와 CJK를 분리해 맥락에 따라 고른다.
"""
from app.engine.llm import _clean_text, sanitize


class TestControlChars:
    """제어문자·대체문자는 어떤 언어에서도 쓰레기 — 항상 제거."""

    def test_always_stripped(self):
        for keep in (True, False):
            out = _clean_text("정상\x07텍스트�", keep_cjk=keep)
            assert out == "정상텍스트", f"keep_cjk={keep}"

    def test_stripped_in_foreign_text(self):
        assert _clean_text("株式会社\x00テスト", keep_cjk=True) == "株式会社テスト"


class TestCjkScoping:
    def test_korean_path_strips_cjk(self):
        """기본 경로는 순수 한국어 보장 — EXAONE 환각 노이즈 방어(이슈 #3)."""
        assert _clean_text("보고서漢字ひらがな") == "보고서"

    def test_foreign_path_keeps_cjk(self):
        """해외 데이터 경로는 현지어를 보존한다."""
        assert _clean_text("株式会社ヤマト飲料", keep_cjk=True) == "株式会社ヤマト飲料"
        assert _clean_text("日本 健康飲料 卸売業者", keep_cjk=True) == \
            "日本 健康飲料 卸売業者"

    def test_japanese_email_survives(self):
        """메일 초안이 문장부호만 남던 사고 고정."""
        body = "はじめまして。ご連絡いたします。"
        assert _clean_text(body, keep_cjk=True) == body
        assert _clean_text(body) == "。。"   # 기존(한국어) 경로 동작은 불변


class TestRecursive:
    def test_nested_structures(self):
        obj = {"companies": [{"name": "株式会社天福", "note": "정상"}]}
        kept = sanitize(obj, keep_cjk=True)
        assert kept["companies"][0]["name"] == "株式会社天福"
        stripped = sanitize(obj)
        assert stripped["companies"][0]["name"] == ""
        assert stripped["companies"][0]["note"] == "정상"

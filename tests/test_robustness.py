"""약한/로컬 모델 대비 방어 테스트 — 오프라인 (실제 LLM 호출 없음).

프롬프트로 강제 + 코드로 사후 정화하는 이중 방어의 코드 절반을 검증한다.
"""
import pytest

from app.config import Settings
from app.engine import prompts
from app.engine.llm import (AnthropicExtractor, _clean_text, get_extractor, sanitize)


# ── 이슈 #3: 순수 한국어 출력 정화 ──────────────────────────────────

class TestSanitize:
    def test_strips_cjk_and_kana(self):
        assert "貴" not in _clean_text("現地 규제에 대한 貴오버스가 부재")
        assert "ガ" not in _clean_text("유통망 부재 ガ 리스크")
        assert "�" not in _clean_text("깨진�글자")

    def test_preserves_hangul_english_numbers(self):
        assert _clean_text("성수동 Poco Hotel 전환 (2024)") == "성수동 Poco Hotel 전환 (2024)"

    def test_recursive_over_nested_output(self):
        out = sanitize({"a": "正常 항목", "b": ["李 리스트", {"c": "淸 값"}]})
        assert out == {"a": "항목", "b": ["리스트", {"c": "값"}]}

    def test_non_string_untouched(self):
        assert sanitize({"n": 4, "f": 0.8, "b": True, "z": None}) == \
            {"n": 4, "f": 0.8, "b": True, "z": None}


# ── 프롬프트 강제 규칙 (이슈 #1·#2·#3) ──────────────────────────────

class TestHardRules:
    def test_all_system_prompts_carry_hard_rules(self):
        # 절대 규칙이 전 프롬프트 최상단에 삽입되어 약한 모델이 먼저 본다
        for sys in (prompts.EXTRACT_SYSTEM, prompts.JUDGE_SYSTEM,
                    prompts.COMPOSE_SYSTEM, prompts.SYNTH_SYSTEM):
            assert sys.startswith("[절대 규칙")
            assert "환각 금지" in sys        # #1
            assert "순수 한국어" in sys       # #3

    def test_extract_prompt_has_entity_rule(self):
        # #2: 회사명 오추출 방지 규칙 (주체 vs 레퍼런스)
        assert "주체 고정" in prompts.EXTRACT_SYSTEM
        assert "Poco Hotel" in prompts.EXTRACT_SYSTEM   # 구체 예시


# ── 공급자 선택 — Claude 하나(2026-10 통일) ─────────────────────────

class TestProviderSelection:
    def _settings(self, provider="anthropic", key="") -> Settings:
        s = Settings()
        s.llm_provider = provider
        s.anthropic_api_key = key
        return s

    def test_claude_selected_with_key(self):
        assert isinstance(get_extractor(self._settings(key="sk-ant-x")), AnthropicExtractor)

    def test_missing_key_fails_loud(self):
        from app.errors import EngineError
        with pytest.raises(EngineError) as e:
            get_extractor(self._settings())
        assert "ANTHROPIC_API_KEY" in e.value.message

    @pytest.mark.parametrize("old", ["openai", "friendli", "local", "mock"])
    def test_retired_providers_refuse_with_reason(self, old):
        """예전 .env 값이 남아 있으면 조용히 Claude로 바꾸지 않고 이유를 말한다."""
        from app.errors import EngineError
        with pytest.raises(EngineError) as e:
            get_extractor(self._settings(provider=old, key="sk-ant-x"))
        assert "anthropic" in e.value.message

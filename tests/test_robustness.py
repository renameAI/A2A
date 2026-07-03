"""약한/로컬 모델 대비 방어 테스트 — 오프라인 (실제 LLM 호출 없음).

프롬프트로 강제 + 코드로 사후 정화하는 이중 방어의 코드 절반을 검증한다.
"""
import pytest

from app.config import Settings
from app.engine import prompts
from app.engine.llm import (LocalExtractor, _clean_text, get_extractor, sanitize)


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


# ── 로컬/오프라인 프로바이더 선택 ───────────────────────────────────

class TestProviderSelection:
    def _settings(self, **env) -> Settings:
        s = Settings()
        s.llm_provider = env.get("provider", "mock")
        s.friendli_token = env.get("friendli_token", "")
        s.friendli_endpoint_id = env.get("friendli_endpoint_id", "")
        s.local_base_url = env.get("local_base_url", "http://localhost:11434/v1/chat/completions")
        s.local_model = env.get("local_model", "exaone3.5:7.8b")
        s.anthropic_api_key = env.get("anthropic_api_key", "")
        return s

    def test_local_provider_selected(self):
        ext = get_extractor(self._settings(provider="local"))
        assert isinstance(ext, LocalExtractor)
        assert ext._thinking_kwargs is False   # 범용 모델엔 reasoning 토글 없음

    def test_friendli_isolated_from_local(self):
        # provider=friendli면 local 설정이 있어도 friendli만 (모델 개입 차단)
        s = self._settings(provider="friendli")   # friendli 키 없음
        assert get_extractor(s) is None

    def test_mock_when_provider_mock(self):
        assert get_extractor(self._settings(provider="mock")) is None

    def test_unknown_provider_errors(self):
        from app.errors import EngineError
        with pytest.raises(EngineError):
            get_extractor(self._settings(provider="gpt5"))


# ── 로컬 어댑터 연결 실패 메시지 (오프라인 서버 없을 때) ────────────

def test_local_unreachable_message():
    from app.errors import EngineError
    s = Settings()
    s.llm_provider = "local"
    s.local_base_url = "http://127.0.0.1:1/v1/chat/completions"  # 닫힌 포트
    s.local_model = "test"
    s.llm_timeout = 2
    ext = get_extractor(s)
    with pytest.raises(EngineError) as exc:
        ext.complete_text("sys", "user")
    assert exc.value.code == "llm_unreachable"
    assert "Ollama" in exc.value.message   # 오프라인 안내

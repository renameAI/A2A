"""환경 설정 — API 키는 .env로만 주입 (ING-05, SEC-01).

키가 없으면 엔진은 Mock 모드로 degrade한다. 코드에 키를 넣지 않는다.
"""
import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


class Settings:
    def __init__(self):
        _load_dotenv()
        # LLM 프로바이더 고정 — 기본은 friendli(K-EXAONE 소버린 트랙, 기획서 13.2).
        # 다른 어댑터는 LLM_PROVIDER를 명시적으로 바꿀 때만 사용된다.
        self.llm_provider = os.environ.get("LLM_PROVIDER", "friendli").lower()
        # K-EXAONE (Friendli dedicated)
        self.friendli_token = os.environ.get("FRIENDLI_TOKEN", "")
        self.friendli_endpoint_id = os.environ.get("FRIENDLI_ENDPOINT_ID", "")
        # 로컬/저사양 OpenAI 호환 모델 (Ollama 등) — LLM_PROVIDER=local, 오프라인 실행
        self.local_base_url = os.environ.get(
            "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1/chat/completions")
        self.local_model = os.environ.get("LOCAL_LLM_MODEL", "exaone3.5:7.8b")
        self.local_token = os.environ.get("LOCAL_LLM_TOKEN", "")  # Ollama는 불필요
        # Anthropic — LLM_PROVIDER=anthropic일 때만 사용
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
        self.apify_token = os.environ.get("APIFY_TOKEN", "")
        self.fetch_timeout = float(os.environ.get("INGEST_FETCH_TIMEOUT", "15"))
        self.crawl_max_pages = int(os.environ.get("CRAWL_MAX_PAGES", "5"))
        self.llm_timeout = float(os.environ.get("LLM_TIMEOUT", "300"))  # reasoning 모델 대비
        # 학습 스코어러 서빙 (training/scorer/serve.py, SSH 터널 경유) — 비면 off.
        # retrieve의 '랭킹 순서'만 담당하고 τ 게이트는 휴리스틱에 남는다 (RET-06 보존).
        self.scorer_url = os.environ.get("A2A_SCORER_URL", "")
        self.scorer_timeout = float(os.environ.get("A2A_SCORER_TIMEOUT", "60"))
        # 근거 시각화 (bbox) — IR덱 PDF 페이지에서 근거 위치를 찾는 비전 모델.
        # 텍스트 추출(LLM_PROVIDER)과는 독립 — 키만 넣으면 켜진다.
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        # judge 결정 자기일관성 투표 (FORMALIZATION.md L2) — k-표본 다수결.
        # 기본 1 = off(비용·지연 3배 방지). k>1이면 범주형 결정에 다수결 + 일치율 계측.
        self.judge_samples = max(1, int(os.environ.get("JUDGE_SAMPLES", "1")))
        # 일치율 임계 (L3) — 미만이면 needs_human + decision을 hold로 캡(저합의 자동추천 차단).
        self.judge_agreement_threshold = float(
            os.environ.get("JUDGE_AGREEMENT_THRESHOLD", "0.6"))
        # VLM 전송 계층 예산 — 배치 크기·페이로드 바이트·토큰을 전부 상한으로 관리
        self.vision_pages_per_call = int(os.environ.get("VISION_PAGES_PER_CALL", "4"))
        self.vision_batch_bytes = int(os.environ.get(
            "VISION_BATCH_BYTES", str(3 * 1024 * 1024)))    # 배치당 이미지 총량 3MB
        self.vision_token_budget = int(os.environ.get("VISION_TOKEN_BUDGET", "300000"))
        self.vision_jpeg_quality = int(os.environ.get("VISION_JPEG_QUALITY", "80"))

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "friendli":
            return bool(self.friendli_token and self.friendli_endpoint_id)
        if self.llm_provider == "local":
            return bool(self.local_base_url and self.local_model)
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return False

    @property
    def vision_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def get_settings() -> Settings:
    return Settings()

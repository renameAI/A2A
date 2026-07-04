"""테스트는 항상 오프라인·Mock 모드로 — 실 API 호출·비용 발생 방지.

셸에 ANTHROPIC_API_KEY가 export돼 있어도 테스트가 실제 LLM을 호출하지 않도록
세션 시작 시 키를 제거한다. 실 LLM 검증은 별도 스크립트로 수동 수행.
"""
import os

# 빈 문자열로 고정 — .env가 있어도 setdefault로 덮이지 않아 Mock 모드 보장
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["FRIENDLI_TOKEN"] = ""
os.environ["FRIENDLI_ENDPOINT_ID"] = ""
os.environ["APIFY_TOKEN"] = ""
os.environ["INGEST_FETCH_TIMEOUT"] = "5"

# 테스트 산출물 격리 — 프로젝트 폴더에 cache/·audit/ 흔적을 남기지 않는다
import tempfile

_tmp = tempfile.mkdtemp(prefix="a2a-test-")
os.environ.setdefault("A2A_CACHE_DIR", os.path.join(_tmp, "cache"))
os.environ.setdefault("A2A_AUDIT_DIR", os.path.join(_tmp, "audit"))

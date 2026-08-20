"""테스트는 실 Friendli(K-EXAONE) API로 돈다 — mock 제거(2026-07) 이후 방침.

예전엔 여기서 키를 비워 오프라인 Mock으로 돌렸다. 그 구조가 '가짜 결과가 진짜처럼
보이는' 통로였고(mock 모드인데 .env 자동 로드로 스코어러가 실 API를 호출한 실측 포함),
엔진에서 조용한 규칙 대체 경로를 제거하면서 테스트도 실 경로를 검증하도록 바꿨다.

전제: FRIENDLI_TOKEN·FRIENDLI_ENDPOINT_ID가 .env에 있어야 한다(app/config.py가
자동 로드). 없으면 LLM을 타는 테스트는 config_error로 실패한다 — 조용히 통과하지
않는 게 의도다.

대가: 스위트가 느려지고 LLM 비결정성으로 간헐 실패가 날 수 있다. 순수 로직 테스트
(파서·게이트·스키마)는 여전히 빠르고 결정적이다.
"""
import os
import pytest
import tempfile

# 수집 타임아웃만 짧게 — 외부 웹 크롤이 테스트를 붙잡지 않게
os.environ.setdefault("INGEST_FETCH_TIMEOUT", "5")

# 테스트 산출물 격리 — 프로젝트 폴더에 cache/·audit/ 흔적을 남기지 않는다
_tmp = tempfile.mkdtemp(prefix="a2a-test-")
os.environ.setdefault("A2A_CACHE_DIR", os.path.join(_tmp, "cache"))
os.environ.setdefault("A2A_AUDIT_DIR", os.path.join(_tmp, "audit"))
os.environ.setdefault("A2A_PAGES_DIR", os.path.join(_tmp, "pages"))
os.environ.setdefault("A2A_DB_PATH", os.path.join(_tmp, "a2a.db"))


@pytest.fixture(autouse=True)
def _clear_ontology_cache():
    """판독 캐시를 테스트마다 비운다.

    캐시는 프로세스 전역이라 테스트 사이에 살아남는다 — 호출 횟수를 세거나
    서로 다른 판정을 기대하는 테스트가 앞 테스트의 결과를 받아 조용히
    통과하거나 깨진다(실측: 게이트 10건 실패).
    """
    from app.engine.company_ontology import _ont_cache
    _ont_cache.clear()
    yield
    _ont_cache.clear()

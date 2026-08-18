"""SaaS 계층 오프라인 테스트 (이슈 #6-B~E) — LLM·웹·Firebase 전부 무호출.

가입→온보딩→승인→Request→brief→search→insight→compose의 전체 여정을
로컬 store + 대역(canned) 추출기로 관통한다. 비용 하드캡의 원자 검사와
prospect 모드의 부분 프로필 계약도 여기서 고정한다.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.errors import EngineError
from app.saas import store as store_module
from app.saas.store import LocalSaasStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 실제 .env에서 격리 — _load_dotenv가 setdefault라 delenv한 키를 되살린다.
    # 개발 머신에 SAAS_ALLOWED_USERS가 생기면 '전원 거부' 테스트가 깨진다(실측).
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
    monkeypatch.setenv("SAAS_AUTH", "dev")
    monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "saas.db"))
    monkeypatch.setenv("SAAS_ALLOWED_USERS", "boram")   # 기본: boram만 허용
    # 승인은 폼 초안을 만들려고 LLM을 부른다. 스텁하지 않으면 이 스위트가
    # 실제 API로 나가고(실측: 8초 → 114초), 네트워크·키 상태에 따라 붉어진다.
    # 초안의 내용 자체는 tests/test_brief_draft.py가 본다 — 여기서는 승인이
    # 초안을 실어 돌려주는 배관만 확인하면 된다.
    import app.saas.router as router_mod
    monkeypatch.setattr(router_mod, "propose_brief", lambda _p: {
        "region": "", "target_type": "스텁", "notes": "스텁",
        "purpose": "revenue", "why": "테스트 스텁"})
    store_module._store = None            # 싱글턴 초기화 (테스트 격리)
    from app.main import app
    return TestClient(app)


H = {"X-Dev-User": "boram"}


class TestAuth:
    def test_dev_mode_requires_header(self, client):
        assert client.get("/saas/me").status_code == 401
        r = client.get("/saas/me", headers=H)
        assert r.status_code == 200
        assert r.json()["workspace"]["workspace_id"] == "ws-boram"

    def test_firebase_mode_requires_bearer(self, client, monkeypatch):
        monkeypatch.setenv("SAAS_AUTH", "firebase")
        assert client.get("/saas/me", headers=H).status_code == 401


class TestCostCap:
    def test_request_cap_blocks_atomically(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "c.db"))
        s.reserve_cost("ws", "r1", 4.0, req_cap=5.0, month_cap=100.0)
        with pytest.raises(EngineError) as e:
            s.reserve_cost("ws", "r1", 1.5, req_cap=5.0, month_cap=100.0)
        assert e.value.code == "cost_cap"
        # 실패한 예약은 가산되지 않는다 — 재시도 여지 보존
        assert s.get("cost_request", "ws", "r1")["usd"] == 4.0

    def test_month_cap_spans_requests(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "c.db"))
        s.reserve_cost("ws", "r1", 60.0, req_cap=999, month_cap=100.0)
        with pytest.raises(EngineError):
            s.reserve_cost("ws", "r2", 50.0, req_cap=999, month_cap=100.0)


class TestProspectMode:
    def test_prospect_returns_partial_profile(self, monkeypatch):
        """미달 프로필이어도 raise 없이 minimum_met=False (§5.4)."""
        from app.engine import represent as rep_mod
        from app.schemas import (Asset, AssetType, BasicInfo, Profile,
                                 ProvField, Provenance, RepresentRequest)
        thin = Profile(
            basic=BasicInfo(name="웹후보", country="일본", industry="unknown"),
            description="스니펫만 있는 후보",
            problem_solved=ProvField(value="", provenance=Provenance.ask),
            solution=ProvField(value="", provenance=Provenance.ask),
            target_customer=ProvField(value="", provenance=Provenance.ask))
        monkeypatch.setattr(rep_mod, "get_extractor", lambda s: None)
        monkeypatch.setattr(rep_mod, "_mock_extract",
                            lambda text, mined: (thin, []))
        res = rep_mod.represent(RepresentRequest(
            assets=[Asset(type=AssetType.text, content="후보 스니펫")],
            profile_purpose="prospect"))
        assert res.minimum_met is False
        assert res.profile.basic.name == "웹후보"

    def test_requester_still_raises(self, monkeypatch):
        from app.engine import represent as rep_mod
        from app.errors import ProfileBelowMinimum
        from app.schemas import (Asset, AssetType, BasicInfo, Profile,
                                 ProvField, Provenance, RepresentRequest)
        thin = Profile(
            basic=BasicInfo(name="미달", country="한국", industry="unknown"),
            description="",
            problem_solved=ProvField(value="", provenance=Provenance.ask),
            solution=ProvField(value="", provenance=Provenance.ask),
            target_customer=ProvField(value="", provenance=Provenance.ask))
        monkeypatch.setattr(rep_mod, "get_extractor", lambda s: None)
        monkeypatch.setattr(rep_mod, "_mock_extract",
                            lambda text, mined: (thin, []))
        with pytest.raises(ProfileBelowMinimum):
            rep_mod.represent(RepresentRequest(
                assets=[Asset(type=AssetType.text, content="빈약")]))


class _CannedExtractor:
    """insight·compose 대역 — 스키마 필수 키만 채운 고정 JSON."""

    # 실 시그니처와 정렬 — allow_foreign 누락으로 test_full_journey가
    # 10커밋째 빨간불이었다(대역이 실제 계약을 못 따라간 전형).
    def extract_json(self, system, user, schema, deep=False,
                     allow_foreign=False):
        req = set(schema.get("required", []))
        if "axes" in req:                      # 기업 온톨로지 판독
            axes = {k: {"value": "판독 값", "status": "assumed"}
                    for k in schema["properties"]["axes"]["properties"]}
            return {"axes": axes, "search_keywords": ["유사 기업 검색어"],
                    "signals": [], "contacts": []}
        if "questions" in req:                 # 명확화 질문
            return {"questions": []}
        if "segments" in req:
            return {"segments": [{"label": "테스트 업종", "why": "대역"}]}
        if "queries" in req:
            return {"queries": ["테스트 검색어 회사소개"]}
        if "companies" in req:                 # 기업 추출 — 스텁 히트와 정렬
            return {"companies": [{
                "name": "Hotel Sakura Annex", "name_ko": "호텔 사쿠라 애넥스",
                "what": "객실 노후로 리뉴얼을 검토 중인 독립 호텔",
                "signal": "시설관리 채용공고 관측",
                "url": "https://ex.jp/sakura"}]}
        if "drafts" in req:
            return {"drafts": [{
                "variant_label": "A안",
                "subject": "객실 리뉴얼 제안",
                "body": "채용공고를 보고 연락드립니다. 30분 소개 기회를 주세요.",
                "call_to_action": "30분 온라인 소개",
                "claims": [{"claim": "채용공고를 보고",
                            "evidence": "시설관리 채용공고 관측"}]}]}
        return {"observed_needs": ["시설 노후"],
                "need_evidence": ["시설관리 채용공고"],
                "value_bridge": ["노후 객실 ↔ 저자본 리노베이션"],
                "personalization_hooks": ["최근 시설관리 채용공고"],
                "uncertainties": ["예산 규모"]}


class TestJourney:
    """가입→온보딩→승인→Request→brief→search→insight→compose 오프라인 관통."""

    def _mock_engine(self, monkeypatch):
        from app.engine import retrieve as ret_mod
        from app.saas import router as saas_mod
        from app.schemas import (BasicInfo, CompanyPortrait, Profile, ProvField,
                                 Provenance, RepresentResponse, SearchBrief)
        ok_profile = Profile(
            basic=BasicInfo(name="다이브인그룹", country="한국", industry="공간개발"),
            description="호텔 예술 전환",
            problem_solved=ProvField(value="노후 객실 매출 정체",
                                     provenance=Provenance.stated),
            solution=ProvField(value="저자본 예술 리노베이션",
                               provenance=Provenance.stated),
            target_customer=ProvField(value="독립 호텔 오너",
                                      provenance=Provenance.stated),
            sell_value_props=["revenue_growth"])

        def fake_represent(req, settings=None):
            return RepresentResponse(
                profile=ok_profile, embedding=[0.0], ontology_anchors=[],
                minimum_met=True, open_questions=[], engine_mode="llm",
                sources=[])
        monkeypatch.setattr(saas_mod, "represent", fake_represent)
        monkeypatch.setattr(saas_mod, "build_search_brief",
                            lambda req, segment=None: SearchBrief(
            deterministic_anchor="앵커", synthesized_counterpart="노후 객실 호텔",
            query_hypotheses=["일본 독립 호텔 리뉴얼"], must_have=[], exclusions=[]))
        monkeypatch.setattr(saas_mod, "get_extractor",
                            lambda s: _CannedExtractor())
        # 오프라인 계약 — 이 테스트는 네트워크를 한 번도 타면 안 된다.
        # 실측: 스텁 없이 돌리면 retrieve가 (1) 학습 스코어러(SCORER_URL)와
        # (2) synthesize_counterpart의 로컬 LLM(Ollama)을 실제로 호출해
        # 관통 테스트 하나가 90~120초를 쓴다. 게이트는 빨라야 게이트다.
        # 더 나쁜 것: get_settings()가 .env를 os.environ에 주입하므로,
        # LLM_PROVIDER=openai인 개발자 머신에서는 테스트가 실제 API를 태운다.
        import app.engine.scorer_client as sc
        monkeypatch.setattr(sc, "score_batch_timed", lambda pairs: (None, None))
        monkeypatch.setattr(sc, "api_score_batch", lambda pairs: (None, None))
        # 실 계약은 (order, ms) 2-튜플이다 — bare None을 돌려주면 호출부가
        # 언팩에서 죽는다(스텁이 계약을 어긴 것이지 코드 결함이 아니다).
        monkeypatch.setattr(sc, "api_rank_listwise",
                            lambda *a, **k: (None, None))
        monkeypatch.setattr(ret_mod, "synthesize_counterpart",
                            lambda req: "노후 객실 리뉴얼이 필요한 일본 독립 호텔")

        import app.connectors.tavily as tv
        monkeypatch.setattr(tv, "search", lambda q, s, max_results=8: [
            {"title": "Hotel Sakura Annex", "url": "https://ex.jp/sakura",
             "snippet": "객실 노후로 리뉴얼 검토, 시설관리 채용"}])
        return ok_profile

    def _poll(self, client, job_id):
        # /saas/jobs — 인증·소유권이 걸린 경로다. /product/jobs는 인증이 없어
        # 기본 차단됐다(공개 프록시로 남의 검색 결과가 읽히던 경로).
        for _ in range(50):
            j = client.get(f"/saas/jobs/{job_id}", headers=H).json()
            if j["status"] != "running":
                assert j["status"] == "done", f"job 실패: {j.get('error')}"
                return j
        raise AssertionError("job 미완료")

    def test_full_journey(self, client, monkeypatch):
        self._mock_engine(monkeypatch)
        # 온보딩
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "회사 소개"}]}).json()["session_id"]
        j = self._poll(client, client.post(
            f"/saas/onboarding-sessions/{sid}/run", headers=H).json()["job_id"])
        assert j["status"] == "done" and j["result"]["needs_answers"] is False
        appr = client.post(f"/saas/onboarding-sessions/{sid}/approve",
                           headers=H).json()
        vid = appr["version_id"]
        # 승인 응답에 폼 초안이 실려 온다 — 화면이 이것으로 Lead Request를
        # 미리 채운다. 빠지면 폼이 다시 빈칸으로 열린다(호텔 하드코딩을
        # 걷어낸 뒤로 빈칸이 곧 아무 제안 없음이다).
        assert appr["brief"]["target_type"] == "스텁"
        assert appr["brief"]["purpose"] in ("revenue", "poc")
        # Request → brief → search
        rid = client.post("/saas/lead-requests", headers=H, json={
            "title": "일본 독립호텔", "profile_version_id": vid,
            "intent": {"value_props": ["revenue_growth"], "target_region": "일본",
                       "target_type": "독립 호텔", "lead_count": 5}}).json()["request_id"]
        self._poll(client, client.post(f"/saas/lead-requests/{rid}/search-brief",
                                       headers=H).json()["job_id"])
        j = self._poll(client, client.post(f"/saas/lead-requests/{rid}/search",
                                           headers=H).json()["job_id"])
        cands = j["result"]["candidates"]
        assert cands and cands[0]["source_url"] == "https://ex.jp/sakura"
        cid = cands[0]["company_id"]
        # insight → compose
        j = self._poll(client, client.post(
            f"/saas/lead-requests/{rid}/candidates/{cid}/insight",
            headers=H).json()["job_id"])
        assert j["result"]["insight"]["uncertainties"] == ["예산 규모"]
        j = self._poll(client, client.post(
            f"/saas/lead-requests/{rid}/candidates/{cid}/compose",
            headers=H).json()["job_id"])
        res = j["result"]
        assert res["send_blocked"] is True
        assert res["drafts"][0]["warnings"] == ["미확인이라 본문에서 제외: 예산 규모"]
        assert res["drafts"][0]["claim_trace"], "주장→근거 연결이 비어 있으면 안 된다"


class TestLlmToggle:
    def test_state_and_guarded_switch(self, client, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "local")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        s = client.get("/saas/settings/llm", headers=H).json()
        assert s["provider"] == "local" and s["ready"]["openai"] is False
        # 키 없이 GPT 전환 → 409 (조용한 대체 없음)
        r = client.post("/saas/settings/llm", headers=H,
                        json={"provider": "openai"})
        assert r.status_code == 409
        # 키가 있으면 전환되고 상태가 반영된다
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        r = client.post("/saas/settings/llm", headers=H,
                        json={"provider": "openai"}).json()
        assert r["provider"] == "openai" and r["label"] == "GPT Luna"
        # 로컬 복귀
        r = client.post("/saas/settings/llm", headers=H,
                        json={"provider": "local"}).json()
        assert r["provider"] == "local"


class TestAccessControl:
    """허용 목록 — 미설정이면 전원 거부(fail closed). API 예산 방어의 1차 관문."""

    def test_empty_allowlist_denies_everyone(self, client, monkeypatch):
        monkeypatch.delenv("SAAS_ALLOWED_USERS", raising=False)
        r = client.get("/saas/me", headers=H)
        assert r.status_code == 403
        assert "닫혀" in r.json()["detail"]

    def test_only_listed_user_passes(self, client, monkeypatch):
        monkeypatch.setenv("SAAS_ALLOWED_USERS", "boram, tools@renamecorp.com")
        assert client.get("/saas/me", headers=H).status_code == 200
        assert client.get("/saas/me",
                          headers={"X-Dev-User": "stranger"}).status_code == 403

    def test_email_match_is_case_insensitive(self, client, monkeypatch):
        monkeypatch.setenv("SAAS_ALLOWED_USERS", "BORAM@DEV.LOCAL")
        assert client.get("/saas/me", headers=H).status_code == 200


class TestGlobalCostCap:
    """워크스페이스별 캡만으로는 사용자 수만큼 곱해진다 — 전역 캡이 계정을 지킨다."""

    def test_global_cap_blocks_across_workspaces(self, tmp_path, monkeypatch):
        from app.saas import cost
        s = LocalSaasStore(str(tmp_path / "g.db"))
        monkeypatch.setenv("COST_CAP_GLOBAL_MONTH_USD", "0.06")
        monkeypatch.setenv("COST_CAP_REQUEST_USD", "99")
        monkeypatch.setenv("COST_CAP_MONTH_USD", "99")
        cost.reserve(s, "ws-a", "r1", "represent")   # 0.05
        with pytest.raises(EngineError) as e:        # 다른 사용자여도 전역이 막는다
            cost.reserve(s, "ws-b", "r1", "represent")
        assert e.value.code == "cost_cap"

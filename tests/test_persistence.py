"""상태 영속화 (Phase 6) — ProductStore가 SQLite로 재시작을 생존하는지.

conftest가 A2A_DB_PATH를 임시 파일로 고정한다. 새 ProductStore 인스턴스 =
서버 재시작 시뮬레이션 — 같은 DB 파일을 다시 열어 상태가 복원돼야 한다.
"""
from app.product.store import ProductStore
from app.schemas import (BBox, BasicInfo, CommentThread, PrivateState,
                         ProvField, Provenance, Profile, QuestionPin,
                         ThreadComment)


def _profile(name="다이브인"):
    return Profile(
        basic=BasicInfo(name=name, country="한국", industry="hospitality"),
        description="d",
        problem_solved=ProvField(value="p", provenance=Provenance.stated),
        solution=ProvField(value="s", provenance=Provenance.stated),
        target_customer=ProvField(value="t", provenance=Provenance.stated),
        sell_value_props=["revenue_growth"])


def _seed(store, name="다이브인"):
    rec = store.save_company(_profile(name), PrivateState(items=[]),
                             ["협력 의향은?"], None, "mock")
    pin = QuestionPin(evidence_id="ev1", question="협력 의향은?", asset_index=0,
                      page=1, box=BBox(ymin=1, xmin=1, ymax=9, xmax=9),
                      quote="q", relevance=0.9, grounding=1.0)
    th = CommentThread(thread_id="th1", evidence_id="ev1",
                       comments=[ThreadComment(author="ai", text="협력 의향은?",
                                               ts="now")])
    store.set_question_pins(rec.company_id, [pin], [th])
    return rec.company_id


class TestPersistenceSurvivesRestart:
    def test_company_and_nested_state_survive_new_instance(self):
        cid = _seed(ProductStore())
        # 완전히 새 인스턴스 = 재시작
        fresh = ProductStore()
        rec = fresh.get(cid)
        assert rec is not None
        assert rec.profile.basic.name == "다이브인"
        assert len(rec.question_pins) == 1
        assert rec.question_pins[0].relevance == 0.9
        assert rec.question_pins[0].grounding == 1.0
        assert rec.threads["th1"].status == "open"

    def test_reply_and_answered_loop_persist(self):
        cid = _seed(ProductStore())
        ProductStore().reply_thread(cid, "th1", "매우 적극적", "now")
        # 재시작 후에도 답변·resolved·소통루프 축적이 살아있다
        rec = ProductStore().get(cid)
        assert rec.threads["th1"].status == "resolved"
        assert rec.threads["th1"].comments[-1].author == "human"
        assert [(d.q, d.a) for d in rec.answered_questions] == [("협력 의향은?", "매우 적극적")]
        assert ProductStore().open_thread_count(cid) == 0
        assert [(d.q, d.a) for d in ProductStore().answered_dialogue(cid)] \
            == [("협력 의향은?", "매우 적극적")]

    def test_update_preserves_pins_and_answered(self):
        cid = _seed(ProductStore())
        ProductStore().reply_thread(cid, "th1", "매우 적극적", "now")
        # 재분석(update)은 프로필만 갈아끼우고 핀·answered는 보존해야 한다
        ProductStore().update_company(
            cid, _profile("다이브인그룹"), PrivateState(items=[]),
            [], {"x": ["c1"]}, "llm")
        rec = ProductStore().get(cid)
        assert rec.profile.basic.name == "다이브인그룹"
        assert rec.engine_mode == "llm"
        assert rec.evidence == {"x": ["c1"]}
        assert len(rec.question_pins) == 1           # 보존
        assert len(rec.answered_questions) == 1      # 보존

    def test_get_missing_returns_none(self):
        assert ProductStore().get("co-없음") is None

    def test_list_reflects_persisted_rows(self):
        store = ProductStore()
        before = len(store.list())
        _seed(store, "새회사")
        assert len(ProductStore().list()) == before + 1

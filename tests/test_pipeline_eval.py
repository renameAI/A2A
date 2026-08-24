"""전 구간 결정 회귀 — 커밋 게이트에서 도는 평가 하네스.

컴포넌트 테스트가 전부 초록인데 파이프라인이 틀린 사고가 이 저장소에서
반복됐다(출처 승수를 랭킹이 안 쓰던 것, 심층 판독이 문턱을 갱신하고도
점수를 안 접던 것). 시나리오는 실측 케이스에서 왔고, 모델 판정은 고정값으로
박아 LLM 없이 결정 로직만 잰다 — 그래야 초 단위로 끝나고 결정적이다.
"""
import pytest

from app.eval.pipeline_eval import check, load_scenarios, rank, run_all


def test_scenarios_exist_and_are_wellformed():
    scns = load_scenarios()
    assert len(scns) >= 2
    for s in scns:
        assert s["candidates"] and s["expect"] and s["requester"]["name"]


@pytest.mark.parametrize("scn", load_scenarios(), ids=lambda s: s["name"])
def test_scenario_expectations_hold(scn):
    fails = check(scn)
    assert not fails, "\n" + "\n".join(fails)


def test_harness_actually_catches_a_regression():
    """하네스가 통과만 하면 그물이 아니다 — 망가뜨리면 잡는지 본다."""
    scn = next(s for s in load_scenarios() if s["name"] == "gyulmedal")
    broken = {**scn, "candidates": [
        {**c, "ontology": {**(c["ontology"] or {}), "reachability": 0.9}}
        if c["name"] == "롯데백화점" else c for c in scn["candidates"]]}
    assert check(broken), "문턱을 뒤집었는데 하네스가 통과시켰다"


def test_rank_matches_router_formula():
    """하네스와 프로덕션이 같은 식을 써야 회귀를 잡는다."""
    import inspect

    from app.saas import router
    src = inspect.getsource(router.recombine_score)
    assert "0.35 + 0.65 * float(reach)" in src, \
        "router의 문턱 가중식이 바뀌었다 — pipeline_eval._reach_weight도 맞춰라"
    scn = next(s for s in load_scenarios() if s["name"] == "gyulmedal")
    top = rank(scn)[0]
    assert top["name"] == "프레시스" and top["reach_w"] == 0.727


def test_run_all_reports_scenario_count():
    n, fails = run_all()
    assert n >= 2 and fails == []

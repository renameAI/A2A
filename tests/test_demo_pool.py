"""강화학습 데모용 호텔 후보 풀의 고정 소스 계약."""

from app.engine import pool as pool_module
from app.schemas import PoolKind, ValueProp, Willingness


def _reload_pool(monkeypatch):
    monkeypatch.setattr(pool_module, "_EXTRA_POOL", None)
    return pool_module.get_pool()


def test_demo_pool_ignores_legacy_pool_environment(monkeypatch):
    monkeypatch.setenv("A2A_POOL_DIR", r"Z:\legacy-pool-must-not-be-read")
    monkeypatch.setenv("A2A_SEED_POOL", "1")

    candidates = _reload_pool(monkeypatch)

    assert len(candidates) == 25
    assert all(candidate.pool == PoolKind.external for candidate in candidates)
    assert all(candidate.company_id.startswith("kq-") for candidate in candidates)
    assert {candidate.profile.basic.name for candidate in candidates} >= {
        "Hanoi Old Quarter Boutique",
        "Da Nang Beachfront Stay",
    }


def test_demo_pool_preserves_buy_side_fields(monkeypatch):
    candidates = _reload_pool(monkeypatch)
    hanoi = next(
        candidate
        for candidate in candidates
        if candidate.profile.basic.name == "Hanoi Old Quarter Boutique"
    )

    assert hanoi.profile.basic.country == "Vietnam"
    assert "노후 객실 매출 정체" in hanoi.profile.problem_solved.value
    assert hanoi.profile.purchase_value_props == [ValueProp.revenue_growth]
    assert hanoi.profile.willingness_purchase == Willingness.medium
    assert "노후 객실 매출 정체" in hanoi.pain_points

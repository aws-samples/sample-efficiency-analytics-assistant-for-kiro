"""derivations metric unit tests."""
from backend.shared import derivations as dv


def test_usage_rate_basic():
    assert dv.usage_rate(50, 100) == 0.5
    assert dv.usage_rate(0, 100) == 0.0


def test_usage_rate_zero_cap():
    # Returns 0 when the limit is 0 or negative, avoiding division by zero
    assert dv.usage_rate(50, 0) == 0.0
    assert dv.usage_rate(50, -1) == 0.0


def test_balance_floor_zero():
    assert dv.balance(30, 100) == 70.0
    # Not less than 0 when over the limit
    assert dv.balance(150, 100) == 0.0


def test_overage_cost():
    # overage cost = overage credits * unit price (input 0 when overage is off -> 0)
    assert dv.overage_cost(100, 0.04) == 4.0
    assert dv.overage_cost(0) == 0.0
    assert dv.overage_cost(0, 0.04) == 0.0


def test_overage_cost_default_price_is_kiro_official():
    # Default unit price = Kiro's official overage price $0.04/credit
    assert dv.DEFAULT_CREDIT_UNIT_PRICE == 0.04
    assert dv.overage_cost(100) == 4.0


def test_tier_monthly_cost():
    # Monthly fee by tier (single source of truth, aligned with the frontend TIER_PRICE_USD)
    assert dv.tier_monthly_cost("Pro") == 20.0
    assert dv.tier_monthly_cost("ProPlus") == 40.0
    assert dv.tier_monthly_cost("ProMax") == 100.0
    assert dv.tier_monthly_cost("Power") == 200.0
    # Unknown/empty tier returns 0 (do not invent)
    assert dv.tier_monthly_cost("") == 0.0
    assert dv.tier_monthly_cost("Unknown") == 0.0


def test_is_overage():
    # Overage = credits > usage_limit
    assert dv.is_overage(1200, 1000) is True
    assert dv.is_overage(1000, 1000) is False
    assert dv.is_overage(500, 1000) is False
    # A limit of 0/invalid is not overage (avoid false positives)
    assert dv.is_overage(500, 0) is False

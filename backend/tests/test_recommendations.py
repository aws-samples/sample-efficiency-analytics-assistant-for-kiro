"""Unit tests for the shared up/down tier suggestion module (shared by the email report and the report-page API).

"Plan match" metric: find the cheapest tier (required) that covers this month's actual usage,
and compare it with the current tier to suggest up/down/keep.
"""
from backend.shared.recommendations import (
    recommendations, next_tier, prev_tier, required_tier,
    annotate_tier_steps, TIER_LIMITS, TIER_PRICE_USD, tier_label,
)


def test_tier_ladder_edges():
    assert next_tier("Pro") == "ProPlus"
    assert next_tier("ProMax") == "Power"
    assert next_tier("Power") is None          # highest tier does not upgrade
    assert prev_tier("Power") == "ProMax"
    assert prev_tier("Pro") is None            # lowest tier does not downgrade
    assert next_tier("Unknown") is None


def test_tier_limits_and_prices():
    assert TIER_LIMITS == {"Pro": 1000, "ProPlus": 2000, "ProMax": 5000, "Power": 10000}
    assert TIER_PRICE_USD == {"Pro": 20, "ProPlus": 40, "ProMax": 100, "Power": 200}


def test_tier_label_official_names_and_price():
    # official naming (no price)
    assert tier_label("Pro") == "Pro"
    assert tier_label("ProPlus") == "Pro+"
    assert tier_label("ProMax") == "Pro Max"
    assert tier_label("Power") == "Power"
    # with price
    assert tier_label("ProPlus", True) == "Pro+ ($40)"
    assert tier_label("Power", True) == "Power ($200)"
    # unknown key returned as-is, no price appended
    assert tier_label("Unknown", True) == "Unknown"


def test_required_tier_boundaries():
    # boundary: exactly equal to the limit counts as "covered"
    assert required_tier(1) == "Pro"
    assert required_tier(1000) == "Pro"
    assert required_tier(1000.01) == "ProPlus"
    assert required_tier(2000) == "ProPlus"
    assert required_tier(2000.5) == "ProMax"
    assert required_tier(5000) == "ProMax"
    assert required_tier(5000.1) == "Power"
    assert required_tier(10000) == "Power"
    assert required_tier(999999) == "Power"    # over the highest tier: still takes Power


def test_proplus_example_from_user():
    """The Pro+/$40 example: <=1000 downgrade to Pro; 1000~2000 keep; >2000 upgrade to Pro Max."""
    rows = [
        {"subscription_tier": "ProPlus", "credits_used": 900},   # <=1000 -> downgrade to Pro
        {"subscription_tier": "ProPlus", "credits_used": 1500},  # 1000~2000 -> keep
        {"subscription_tier": "ProPlus", "credits_used": 3000},  # >2000 -> upgrade to Pro Max
    ]
    up, down = recommendations(rows)
    assert [(u["credits_used"], u["suggested_tier"]) for u in up] == [(3000, "ProMax")]
    assert [(d["credits_used"], d["suggested_tier"]) for d in down] == [(900, "Pro")]


def test_multi_tier_jump_and_over_power():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 6000},      # only Power covers -> multi-tier upgrade to Power
        {"subscription_tier": "Power", "credits_used": 800},     # only needs Pro -> multi-tier downgrade to Pro
        {"subscription_tier": "Pro", "credits_used": 50000},     # over the Power limit -> still Power (upgrade)
        {"subscription_tier": "Power", "credits_used": 50000},   # over Power and already on Power -> keep
    ]
    up, down = recommendations(rows)
    ups = {u["credits_used"]: u["suggested_tier"] for u in up}
    downs = {d["credits_used"]: d["suggested_tier"] for d in down}
    assert ups == {6000: "Power", 50000: "Power"}
    assert downs == {800: "Pro"}


def test_keep_and_zero_usage_not_recommended():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 500},       # usage falls in Pro -> keep
        {"subscription_tier": "ProMax", "credits_used": 0},      # zero usage -> no suggestion
        {"subscription_tier": "Unknown", "credits_used": 3000},  # unknown current tier -> no suggestion
    ]
    up, down = recommendations(rows)
    assert up == [] and down == []


def test_annotate_tier_steps():
    """Usage-rate threshold model: attach one-tier up/down suggestion keys to each row (tier ladder next/prev)."""
    rows = [
        {"subscription_tier": "Pro", "credits_used": 1500},
        {"subscription_tier": "Power", "credits_used": 500},
        {"subscription_tier": "ProPlus", "credits_used": 1200},
        {"subscription_tier": "Unknown", "credits_used": 300},
    ]
    out = annotate_tier_steps(rows)
    # Pro: can upgrade to Pro+, cannot downgrade further
    assert out[0]["suggested_up"] == "ProPlus" and out[0]["suggested_down"] is None
    # Power: cannot upgrade further, can downgrade to Pro Max
    assert out[1]["suggested_up"] is None and out[1]["suggested_down"] == "ProMax"
    # ProPlus: upgrade Pro Max / downgrade Pro
    assert out[2]["suggested_up"] == "ProMax" and out[2]["suggested_down"] == "Pro"
    # unknown tier: both None
    assert out[3]["suggested_up"] is None and out[3]["suggested_down"] is None
    # other keys pass through as-is
    assert out[0]["credits_used"] == 1500


def test_sorting():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 1500},   # upgrade Pro+
        {"subscription_tier": "Pro", "credits_used": 6000},   # upgrade Power (highest usage, sorts first)
        {"subscription_tier": "Power", "credits_used": 800},  # downgrade Pro
        {"subscription_tier": "Power", "credits_used": 300},  # downgrade Pro (lowest usage, sorts first)
    ]
    up, down = recommendations(rows)
    assert [u["credits_used"] for u in up] == [6000, 1500]     # descending
    assert [d["credits_used"] for d in down] == [300, 800]     # ascending

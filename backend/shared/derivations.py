"""Derived-field metrics (single source of truth).

Metrics (calibrated against real Kiro data):
- usage_rate = Credits_Used / Usage_Limit (plan limit, PRO=1000/PRO_MAX=5000/POWER=10000)
- balance    = Usage_Limit - Credits_Used (floored at 0)
- Cost (approach A, matching the real billing magnitude):
    cost = subscription monthly fee (by tier, whole month, fixed per seat) + overage cost (only the over-limit part)
    * overage cost = credit_unit_price * Overage_Credits_Used (the actually-billed overage credits)
      — note: this is **not** credits_used * unit price. $0.04/credit applies only to the part that exceeds the plan limit
      and has overage enabled; credits within the plan limit are already included in the monthly fee.
    * for customers without overage enabled, Overage_Credits_Used=0, so the overage cost is naturally 0.
  The monthly fee is not summed row-by-row in the daily table (otherwise a month would add it ~30 times); instead it is
  added once per seat (the tier of each email) in the aggregation query; the daily table's est_cost holds **only that day's overage cost**.

Note: an earlier version used est_cost = credits_used * $0.04, billing in-plan credits as overage too,
which badly overestimated total cost. It has been changed to approach A.

Everywhere that needs derived fields (ETL / API / Agent) reads from here, to avoid metric drift.
"""
from __future__ import annotations

DEFAULT_CREDIT_UNIT_PRICE = 0.04  # Kiro's official overage/add-on credit unit price

# Tier monthly fee (USD/seat/month, Kiro's official price). Single source of truth, aligned with the frontend TIER_PRICE_USD.
# The key is the normalized tier name (Pro/ProPlus/ProMax/Power).
TIER_MONTHLY_USD: dict[str, float] = {
    "Pro": 20.0,
    "ProPlus": 40.0,
    "ProMax": 100.0,
    "Power": 200.0,
}


def tier_monthly_cost(subscription_tier: str) -> float:
    """Return the whole-month subscription fee (USD) by tier. An unknown tier returns 0 (cannot price it, do not invent)."""
    return TIER_MONTHLY_USD.get((subscription_tier or "").strip(), 0.0)


def overage_cost(overage_credits_used: float,
                 credit_unit_price: float = DEFAULT_CREDIT_UNIT_PRICE) -> float:
    """Overage cost = actually-billed overage credits * unit price. When overage is off the input is 0 -> 0."""
    return round(max(overage_credits_used or 0.0, 0.0) * credit_unit_price, 2)


def usage_rate(credits_used: float, usage_limit: float) -> float:
    """Usage rate = used / plan limit. Returns 0 when the limit is 0."""
    if not usage_limit or usage_limit <= 0:
        return 0.0
    return round(credits_used / usage_limit, 4)


def balance(credits_used: float, usage_limit: float) -> float:
    """Balance = limit - used, displayed as no less than 0."""
    return round(max(usage_limit - credits_used, 0.0), 1)


def is_overage(credits_used: float, usage_limit: float) -> bool:
    """Is overage = used credits exceed the plan limit (matching the Kiro console metric)."""
    return bool(usage_limit and usage_limit > 0 and credits_used > usage_limit)

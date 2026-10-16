"""Single source of truth for tier suggestions (shared by the email report and the report-page API, so metrics stay consistent).

Metric (a "plan match" model):
- Kiro has four plan tiers, each with a fixed credit limit and monthly fee:
  Pro=1000credit/$20 · Pro+=2000/$40 · Pro Max=5000/$100 · Power=10000/$200
- Based on the user's **actual usage** this month (credits_used), find the **cheapest tier** that covers that usage
  (required tier = the lowest tier satisfying usage <= limit; if usage exceeds the Power limit, take the highest tier Power).
- Compare the required tier with the user's **current tier**:
    required lower  -> suggest a downgrade (next month down to required, may span multiple tiers)
    required higher -> suggest an upgrade (next month up to required, may span multiple tiers)
    required equal  -> keep, no suggestion
- Users with no usage (credits_used<=0) get no suggestion (treated as keep).

Example (currently Pro+/$40, limit 2000):
  usage <= 1000 -> downgrade to Pro; 1000 < usage <= 2000 -> keep; usage > 2000 -> upgrade to Pro Max.
"""
from __future__ import annotations

from typing import Optional

# Tier ladder (low -> high)
TIER_LADDER = ["Pro", "ProPlus", "ProMax", "Power"]
# Tier -> display name
TIER_ZH = {"Pro": "Pro", "ProPlus": "Pro+", "ProMax": "Pro Max", "Power": "Power"}

# Per-tier credit limit (low -> high, corresponding to TIER_LADDER)
TIER_LIMITS: dict[str, int] = {
    "Pro": 1000,
    "ProPlus": 2000,
    "ProMax": 5000,
    "Power": 10000,
}
# Per-tier monthly fee (USD), for display/explanation
TIER_PRICE_USD: dict[str, int] = {
    "Pro": 20,
    "ProPlus": 40,
    "ProMax": 100,
    "Power": 200,
}


def tier_label(key: str, with_price: bool = False) -> str:
    """Tier display name (Kiro's official naming); with_price appends the monthly fee, e.g. "Power ($200)"."""
    name = TIER_ZH.get(key, key)
    price = TIER_PRICE_USD.get(key)
    return f"{name} (${price})" if with_price and price is not None else name


def next_tier(tier: str) -> Optional[str]:
    """One tier up; returns None if already at the highest tier."""
    if tier not in TIER_LADDER:
        return None
    i = TIER_LADDER.index(tier)
    return TIER_LADDER[i + 1] if i < len(TIER_LADDER) - 1 else None


def prev_tier(tier: str) -> Optional[str]:
    """One tier down; returns None if already at the lowest tier."""
    if tier not in TIER_LADDER:
        return None
    i = TIER_LADDER.index(tier)
    return TIER_LADDER[i - 1] if i > 0 else None


def required_tier(credits_used: float) -> str:
    """The cheapest tier that covers this usage; returns the highest tier Power when usage exceeds the top-tier limit."""
    for tier in TIER_LADDER:  # low -> high
        if credits_used <= TIER_LIMITS[tier]:
            return tier
    return TIER_LADDER[-1]  # over the Power limit: no higher tier to upgrade to, so take Power


def annotate_tier_steps(rows: list[dict]) -> list[dict]:
    """Attach one-tier-up/one-tier-down suggestion keys to each row (for the usage-rate threshold model):
       suggested_up   = next_tier(subscription_tier)  # already at the highest tier Power -> None
       suggested_down = prev_tier(subscription_tier)  # already at the lowest tier Pro  -> None
    Both are None when the current tier is unknown (not in TIER_LADDER). All other fields of the row pass through unchanged.
    """
    out = []
    for r in rows:
        tier = r.get("subscription_tier") or ""
        out.append({
            **r,
            "suggested_up": next_tier(tier),
            "suggested_down": prev_tier(tier),
        })
    return out


def recommendations(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """From per-user rows (with subscription_tier / credits_used) compute
    (upgrade list, downgrade list). Each item adds suggested_tier to the original row.

    Logic (plan match): required = the cheapest tier covering this month's usage;
      required above current -> upgrade; below -> downgrade; equal -> keep.
      Users with no usage (credits_used<=0) or an unknown current tier get no suggestion.
    """
    upgrades, downgrades = [], []
    for r in rows:
        tier = r.get("subscription_tier") or ""
        used = float(r.get("credits_used") or 0)
        if tier not in TIER_LADDER or used <= 0:
            continue
        req = required_tier(used)
        cur_i = TIER_LADDER.index(tier)
        req_i = TIER_LADDER.index(req)
        if req_i > cur_i:
            upgrades.append({**r, "suggested_tier": req})
        elif req_i < cur_i:
            downgrades.append({**r, "suggested_tier": req})
        # req_i == cur_i: keep, no suggestion
    # Upgrades: higher usage first; downgrades: lower usage first
    upgrades.sort(key=lambda x: float(x.get("credits_used") or 0), reverse=True)
    downgrades.sort(key=lambda x: float(x.get("credits_used") or 0))
    return upgrades, downgrades

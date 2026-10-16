"""ETL core transform: Kiro per-user CSV -> Parquet long tables.

Produces two fact tables (partitioned by year/month/day):
  fact_user_daily        one row / user / day (cross-client aggregation + derived fields + department)
  fact_user_model_daily  one row / user / day / model (model message-count long table)

This module does not depend on AWS and can run locally against mock data;
the Lambda handler (handler.py) handles the S3 I/O and calls this module.
"""
from __future__ import annotations

import io
import glob
import os
from typing import Iterable

import pandas as pd

# Keep consistent with shared.derivations; the ETL packaging includes shared
try:
    from backend.shared import derivations as dv
except ImportError:  # inside Lambda, shared is a sibling of this module
    import derivations as dv  # type: ignore

STATIC_NUMERIC = [
    "Chat_Conversations", "Credits_Used", "Overage_Cap",
    "Overage_Credits_Used", "Total_Messages", "Usage_Limit",
]


def _read_csv(buf) -> pd.DataFrame:
    df = pd.read_csv(buf, dtype=str).fillna("")
    numeric_cols = STATIC_NUMERIC + [c for c in df.columns if c.endswith("_messages")]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Boolean columns: the real CSV may lack a column or parse it as a non-Series; handle robustly in one place.
    def _bool_col(name: str) -> pd.Series:
        if name in df.columns:
            return df[name].astype(str).str.strip().str.lower() == "true"
        return pd.Series([False] * len(df), index=df.index)

    df["Overage_Enabled"] = _bool_col("Overage_Enabled")
    df["New_User"] = _bool_col("New_User")

    # Email normalization
    if "User_Email" in df.columns:
        df["User_Email"] = df["User_Email"].astype(str).str.strip().str.lower()

    # Tier case normalization: real data is all-caps like POWER/PRO; unify to Pro/ProPlus/ProMax/Power.
    if "Subscription_Tier" in df.columns:
        df["Subscription_Tier"] = df["Subscription_Tier"].astype(str).map(_normalize_tier)
    return df


_TIER_MAP = {
    "pro": "Pro", "proplus": "ProPlus", "pro_plus": "ProPlus", "pro plus": "ProPlus",
    "promax": "ProMax", "pro_max": "ProMax", "pro max": "ProMax", "power": "Power",
}


def _normalize_tier(v: str) -> str:
    key = (v or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return _TIER_MAP.get(key, v.strip().title() if v else "")


def transform(
    frames: Iterable[pd.DataFrame],
    department_map: dict[str, dict],
    credit_unit_price: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize multiple CSV DataFrames into (fact_user_daily, fact_user_model_daily)."""
    df = pd.concat(list(frames), ignore_index=True)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    model_cols = [c for c in df.columns if c.endswith("_messages")]

    # --- Aggregate across clients by (Date, User_Email) ---
    agg = {
        "Chat_Conversations": "sum",
        "Credits_Used": "sum",
        "Overage_Cap": "max",            # the cap is the same for a user; max is robust
        "Overage_Credits_Used": "sum",
        "Total_Messages": "sum",
        "Usage_Limit": "max",            # plan limit (fixed per user, take max)
        "Overage_Enabled": "max",        # enabled if any client has it enabled
        "New_User": "max",
        "Subscription_Tier": "first",
        "UserId": "first",
        "ProfileId": "first",
    }
    for mc in model_cols:
        agg[mc] = "sum"

    daily = df.groupby(["Date", "User_Email"], as_index=False).agg(agg)

    # --- Department join (from the Identity Center sync) ---
    def _dept(email: str) -> str:
        info = department_map.get(email)
        return info.get("department", "Ungrouped") if info else "Ungrouped"

    def _name(email: str) -> str:
        info = department_map.get(email)
        return (info or {}).get("display_name", "") or email.split("@")[0]

    daily["department"] = daily["User_Email"].map(_dept)
    daily["display_name"] = daily["User_Email"].map(_name)

    # --- Derived fields (unified metric: based on the Usage_Limit plan limit) ---
    # Fallback: if Usage_Limit is missing/0 (early CSVs lacked this column), fall back to Overage_Cap.
    def _limit(r):
        ul = r.get("Usage_Limit", 0) or 0
        return ul if ul > 0 else r["Overage_Cap"]
    daily["usage_rate"] = daily.apply(
        lambda r: dv.usage_rate(r["Credits_Used"], _limit(r)), axis=1)
    daily["balance"] = daily.apply(
        lambda r: dv.balance(r["Credits_Used"], _limit(r)), axis=1)
    daily["is_overage"] = daily.apply(
        lambda r: dv.is_overage(r["Credits_Used"], _limit(r)), axis=1)
    # In the daily table est_cost holds **only that day's overage cost** (summable per day);
    # the subscription monthly fee is a whole-month, per-seat fixed amount, cannot be summed row-by-row, and is added per tier by the aggregation query.
    # overage cost = Overage_Credits_Used * unit price (always 0 for customers without overage enabled).
    daily["est_cost"] = daily["Overage_Credits_Used"].map(
        lambda oc: dv.overage_cost(oc, credit_unit_price))

    # Normalize column names to snake_case, convenient for Athena/frontend
    fact_user_daily = daily.rename(columns={
        "Date": "date", "User_Email": "email", "UserId": "user_id",
        "Chat_Conversations": "chat_conversations", "Credits_Used": "credits_used",
        "Overage_Cap": "overage_cap", "Overage_Credits_Used": "overage_credits_used",
        "Overage_Enabled": "overage_enabled", "ProfileId": "profile_id",
        "Subscription_Tier": "subscription_tier", "Total_Messages": "total_messages",
        "New_User": "new_user", "Usage_Limit": "usage_limit",
    })[[
        "date", "user_id", "email", "display_name", "department",
        "subscription_tier", "chat_conversations", "credits_used", "overage_cap",
        "overage_credits_used", "overage_enabled", "total_messages", "new_user",
        "usage_rate", "balance", "est_cost", "profile_id", "usage_limit", "is_overage",
    ]]

    # [Metric: only show users with usage] The dashboard only counts users who appear in the Kiro CSV
    # (= actually activated and active users). Pending/not-yet-activated subscription users do not appear in the CSV,
    # so they are naturally excluded, which keeps the activation rate and similar metrics accurate. Therefore we do **not** add 0 rows for all subscription users here.

    # Force the Parquet column types to match the Glue table schema (otherwise Athena reports type incompatible).
    # bigint columns must be int64; double columns float64; boolean columns bool; everything else string.
    fact_user_daily = _cast_types(fact_user_daily, {
        "chat_conversations": "int64", "total_messages": "int64",
        "credits_used": "float64", "overage_cap": "float64",
        "overage_credits_used": "float64", "usage_rate": "float64",
        "balance": "float64", "est_cost": "float64", "usage_limit": "float64",
        "overage_enabled": "bool", "new_user": "bool", "is_overage": "bool",
    })

    # --- Model long table ---
    long = daily.melt(
        id_vars=["Date", "User_Email"],
        value_vars=model_cols,
        var_name="model",
        value_name="model_messages",
    )
    long["model"] = long["model"].str.replace("_messages$", "", regex=True)
    long = long[long["model_messages"] > 0]
    fact_user_model_daily = long.rename(
        columns={"Date": "date", "User_Email": "email"})[
        ["date", "email", "model", "model_messages"]]
    fact_user_model_daily = _cast_types(fact_user_model_daily, {"model_messages": "int64"})

    return fact_user_daily.reset_index(drop=True), fact_user_model_daily.reset_index(drop=True)


def _cast_types(df: pd.DataFrame, types: dict[str, str]) -> pd.DataFrame:
    """Force-cast columns to the target types, so the written Parquet types match the Glue table schema."""
    if df.empty:
        return df
    for col, t in types.items():
        if col not in df.columns:
            continue
        if t == "int64":
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round().astype("int64")
        elif t == "float64":
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype("float64")
        elif t == "bool":
            df[col] = df[col].astype("bool")
    return df


def run_local(csv_glob: str, identity_json: str, credit_unit_price: float = 0.15):
    """Local convenience entry point: run the transform over mock data and return the two tables."""
    import json
    with open(identity_json, encoding="utf-8") as f:
        dept_map = json.load(f)
    files = glob.glob(csv_glob, recursive=True)
    frames = [_read_csv(p) for p in files]
    return transform(frames, dept_map, credit_unit_price)

"""Controlled query tools for the NL Q&A Agent (reusing the shared query layer).

Design (generic approach):
- The main tool is **query_usage**: a single parameterized generic query that covers the vast majority of
  usage questions via allowlisted dimensions (a metric × a group-by × a filter × a time range), rather than one tool per question, and never accepts arbitrary SQL.
- Helper tools: find_user (resolve a person's name to an email), get_summary (KPI overview), get_filters (available values).
- Tools query organization-wide data; access is gated by the AgentCore Runtime inbound Cognito JWT authorizer.

Packaging: inside the container backend/shared/{athena_client,derivations}.py are flattened as siblings,
so this uses the flat `from athena_client import ...` (local development falls back via sys.path).
"""
from __future__ import annotations

import os
from typing import Optional

from strands import tool

try:
    from athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # local development (repository root)
    from backend.shared.athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

GLUE_DB = os.environ.get("GLUE_DB", "kiro_usage")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
REGION = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))


_queries = Queries(db=GLUE_DB)
_layer: Optional[QueryLayer] = None


def _get_layer() -> QueryLayer:
    global _layer
    if _layer is None:
        runner = make_athena_runner(
            database=GLUE_DB, workgroup=ATHENA_WORKGROUP, region=REGION)
        _layer = QueryLayer(runner, cache_ttl=60)
    return _layer


def _resolve(period, df, dt):
    """period=YYYY-MM expands to the first/last day of the month; otherwise use date_from/date_to."""
    if period and not (df or dt):
        return f"{period}-01", f"{period}-31"
    return df, dt


@tool
def query_usage(metric: str = "credits", group_by: str = "none",
                department: Optional[str] = None,
                subscription_tier: Optional[str] = None,
                model: Optional[str] = None,
                user: Optional[str] = None,
                overage_only: bool = False,
                period: Optional[str] = None,
                date_from: Optional[str] = None,
                date_to: Optional[str] = None,
                sort: str = "desc", limit: int = 50) -> dict:
    """Generic usage query: one tool answers the vast majority of usage questions, composed as [metric × group-by × filter × time range].

    Usage examples (mapping natural language to parameters):
    - "which department used the most credits" -> metric=credits, group_by=department
    - "how many users are in each tier" -> metric=users, group_by=subscription_tier
    - "the 10 users with the most credits" -> metric=credits, group_by=user, limit=10
    - "which models did John Lee use / usage per model" -> metric=messages, group_by=model, user=<John Lee's email>
      (if you only know the name, first use find_user to get the email, then fill in user)
    - "overall model distribution" -> metric=messages, group_by=model
    - "who went over their limit" -> metric=credits, group_by=user, overage_only=true
    - "Sunnyvale team's credits this month" -> metric=credits, group_by=none/user, department=Sunnyvale
    - "daily credits trend" -> metric=credits, group_by=date

    Args:
        metric: the metric. One of credits | messages (model message count = number of prompts) | est_cost | active_days | users (head count).
        group_by: the grouping dimension. One of none | department | subscription_tier | model | date | user.
        department: filter by department (the department name, e.g. Sunnyvale).
        subscription_tier: filter by tier (Pro/Pro+/Pro Max/Power).
        model: filter by model (e.g. gpt_5.6_sol).
        user: filter by user (email or name keyword; model-related queries match by email only, so resolve names with find_user first).
        overage_only: return only overage users (cumulative credits in range > plan limit).
        period: data period YYYY-MM (choose this or date_from/date_to).
        date_from: start date YYYY-MM-DD.
        date_to: end date YYYY-MM-DD.
        sort: sort direction asc | desc (default desc).
        limit: number of rows returned (default 50, max 500).
    """
    df, dt = _resolve(period, date_from, date_to)
    filters = {"department": department, "subscription_tier": subscription_tier,
               "model": model, "user": user, "overage_only": overage_only}
    filters = {k: v for k, v in filters.items() if v}
    spec = _queries.query_usage(
        metric=metric, group_by=group_by, filters=filters,
        date_from=df, date_to=dt, sort=sort, limit=limit)
    rows = _get_layer().run(spec)
    return {"range": {"from": df, "to": dt}, "metric": metric, "group_by": group_by,
            "rows": rows, "note": spec.note}


@tool
def find_user(keyword: str) -> dict:
    """Find users by name or email keyword (searches across all time, not limited by month), returning their email/department/tier.

    Used to resolve a name like "John Lee" to a concrete email—when querying someone's models/usage, first use this tool to get the
    email, then call query_usage(..., user=<email>). Finding people is time-independent, so no dates are passed.

    Args:
        keyword: a name or email keyword (e.g. John Lee or jlee).
    """
    spec = _queries.users(None, None, page=1, page_size=50, search=keyword)
    rows = _get_layer().run(spec)
    users = [{"email": r.get("email"), "display_name": r.get("display_name"),
              "department": r.get("department"), "subscription_tier": r.get("subscription_tier")} for r in rows]
    return {"matched": len(users), "users": users,
            "note": "All-time fuzzy match by name/email; when there are multiple results, confirm with the user which person"}


@tool
def get_summary(period: Optional[str] = None, date_from: Optional[str] = None,
                date_to: Optional[str] = None) -> dict:
    """Get the overall usage KPI summary (registered/active users, activation rate, total credits, overage users, estimated cost, tier distribution, daily-active avg/peak).

    Args:
        period: data period YYYY-MM (choose this or date_from/date_to).
        date_from: start date YYYY-MM-DD.
        date_to: end date YYYY-MM-DD.
    """
    df, dt = _resolve(period, date_from, date_to)
    spec = _queries.summary(df, dt)
    row = _get_layer().one(spec)
    return {"range": {"from": df, "to": dt}, "kpis": row, "note": spec.note}


@tool
def get_filters() -> dict:
    """Get the available data periods, departments, and model list (for clarifying questions or validating filter values)."""
    periods = [r["period"] for r in _get_layer().run(_queries.periods())]
    depts = [r["department"] for r in _get_layer().run(_queries.department_list())]
    models = [r["model"] for r in _get_layer().run(_queries.model_list())]
    return {"periods": periods, "departments": depts, "models": models}


ALL_TOOLS = [query_usage, find_user, get_summary, get_filters]

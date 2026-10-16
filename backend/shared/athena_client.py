"""Shared Athena query layer.

Both the dashboard API (backend/api) and the NL Q&A Agent (backend/agent) reuse this module, which guarantees:
- a single source of truth for query metrics (derived fields in derivations.py, already materialized by the ETL into fact_user_daily);
- only **parameterized** query builders are exposed, never concatenating arbitrary SQL / user input never enters the SQL structure;
- an optional `identity_filter` data filter (None means all data; {"email": ...} restricts to one user, e.g. single-user drill-down).

Execution model: start_query_execution -> poll -> get_query_results, with a simple in-memory TTL cache.
This module can be unit-tested without AWS (QueryLayer takes an injectable, replaceable runner).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from backend.shared import derivations as _dv
except ImportError:  # Lambda packaging: shared is a top-level package (API) or a sibling of this module (ETL)
    try:
        from shared import derivations as _dv  # type: ignore
    except ImportError:
        import derivations as _dv  # type: ignore


# SQL fragment for the cost metric (approach A): per-user cost = monthly fee (by tier) + overage cost.
# The monthly fee comes from a tier mapping (derivations.TIER_MONTHLY_USD, single source of truth); overage = sum(est_cost)
# (est_cost in the daily table has been changed to that day's overage cost, so it can be summed per day).
def _tier_monthly_sql(tier_expr: str) -> str:
    """Build a CASE expression mapping tier -> monthly fee. tier_expr is the tier column/expression (already in the SQL)."""
    whens = " ".join(
        f"WHEN {_q(t)} THEN {p:g}"
        for t, p in _dv.TIER_MONTHLY_USD.items()
    )
    return f"(CASE {tier_expr} {whens} ELSE 0 END)"


# ---------------------------------------------------------------------------
# Security: identifier and literal validation (injection prevention)
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SORTABLE_COLUMNS = {
    "credits_used", "balance", "usage_rate", "usage_limit", "total_messages",
    "chat_conversations", "active_days", "est_cost", "email",
}
_SORT_DIRS = {"asc", "desc"}

# Allowlists for query_usage's metric / group_by: the metric becomes an alias in SELECT/ORDER BY,
# so it must be validated before building the SQL to prevent injection.
_ALLOWED_METRICS = frozenset({"credits", "messages", "est_cost", "active_days", "users"})
_ALLOWED_GROUP_BY = frozenset({"none", "department", "subscription_tier", "model", "date", "user"})


def _q(value: str) -> str:
    """Safely wrap a string as a single-quoted SQL literal (escaping single quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def _valid_date(d: str) -> str:
    if not _DATE_RE.match(d or ""):
        raise ValueError(f"invalid date (expected YYYY-MM-DD): {d!r}")
    return d


def _valid_period(p: str) -> str:
    if not _PERIOD_RE.match(p or ""):
        raise ValueError(f"invalid period (expected YYYY-MM): {p!r}")
    return p


# ---------------------------------------------------------------------------
# Query builders (parameterized; return a directly executable SQL string)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QuerySpec:
    """A single query: SQL + a note (for the Agent to cite the metric)."""
    sql: str
    note: str = ""


class Queries:
    """The single construction point for all dashboard/Agent queries.

    Conventions:
    - The time range uses the `date` string column ('YYYY-MM-DD') with BETWEEN; partition pruning is handled by Athena.
    - `identity_filter`: None = all data; otherwise shaped like {"email": "user@example.com"}, added to the WHERE.
    """

    def __init__(self, db: str = "kiro_usage",
                 fact_daily: str = "fact_user_daily",
                 fact_model: str = "fact_user_model_daily"):
        self.db = db
        self.t_daily = f"{db}.{fact_daily}"
        self.t_model = f"{db}.{fact_model}"

    # -- Internal: build the WHERE fragment ------------------------------------
    def _where(self, date_from: Optional[str], date_to: Optional[str],
               identity_filter: Optional[dict], extra: Optional[list[str]] = None,
               alias: str = "") -> str:
        col = (alias + "." if alias else "")
        conds: list[str] = []
        if date_from:
            conds.append(f"{col}date >= {_q(_valid_date(date_from))}")
        if date_to:
            conds.append(f"{col}date <= {_q(_valid_date(date_to))}")
        if identity_filter:
            email = identity_filter.get("email")
            if email:
                conds.append(f"lower({col}email) = {_q(str(email).strip().lower())}")
        for e in (extra or []):
            conds.append(e)
        return ("WHERE " + " AND ".join(conds)) if conds else ""

    # -- KPI summary + tier distribution ---------------------------------------
    def summary(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        # The KPI computation takes two paths:
        #  per_user: user-level aggregation (sum credits across days, take each user's max tier/limit, active = has credits in range);
        #            overage users = cumulative credits in range exceed the plan limit (sum(credits)>usage_limit, matching the Kiro monthly-limit metric).
        #  per_day : distinct active users per day, then the range's daily-active avg/peak (matching the reference dashboard algorithm).
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 max(display_name) AS display_name,
                 max_by(subscription_tier, date) AS subscription_tier,
                 max(department) AS department,
                 sum(credits_used) AS credits_used,
                 sum(est_cost) AS est_cost,
                 max_by(usage_limit, date) AS usage_limit,
                 count(distinct date) AS active_days,
                 max(new_user) AS ever_new
          FROM {self.t_daily}
          {w}
          GROUP BY email
        ),
        per_day AS (
          SELECT date, count(distinct email) AS dau
          FROM {self.t_daily}
          {w}{' AND' if w else 'WHERE'} credits_used > 0
          GROUP BY date
        )
        SELECT
          (SELECT count(*) FROM per_user) AS total_users,
          (SELECT count_if(credits_used > 0) FROM per_user) AS active_users,
          (SELECT round(coalesce(sum(credits_used),0),1) FROM per_user) AS total_credits,
          (SELECT round(coalesce(sum(est_cost + {_tier_monthly_sql('subscription_tier')}),0),2) FROM per_user) AS est_cost,
          (SELECT count_if(usage_limit > 0 AND credits_used > usage_limit) FROM per_user) AS overage_users,
          (SELECT round(coalesce(sum(credits_used),0) / nullif(count_if(credits_used>0),0),1) FROM per_user) AS avg_credits_per_active,
          (SELECT count_if(subscription_tier='Pro') FROM per_user) AS tier_pro,
          (SELECT count_if(subscription_tier='ProPlus') FROM per_user) AS tier_proplus,
          (SELECT count_if(subscription_tier='ProMax') FROM per_user) AS tier_promax,
          (SELECT count_if(subscription_tier='Power') FROM per_user) AS tier_power,
          (SELECT round(coalesce(avg(dau),0),1) FROM per_day) AS daily_active_avg,
          (SELECT coalesce(max(dau),0) FROM per_day) AS daily_active_peak
        """
        return QuerySpec(sql=_norm(sql),
                         note="KPIs and tier distribution: credits aggregated per user over the range; active = credits_used>0 in range; overage = cumulative credits>usage_limit in range; daily-active avg/peak = range avg/max of distinct active users per day")

    # -- Daily trend (credits + active users) ----------------------------------
    def trend(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT date,
               round(sum(credits_used),1) AS credits,
               count(distinct email) AS active_users
        FROM {self.t_daily}
        {w}
        GROUP BY date
        ORDER BY date
        """
        return QuerySpec(sql=_norm(sql), note="Daily credits and active-user count")

    # -- Model usage distribution ----------------------------------------------
    def models(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        # The model long table joins identity_filter by email
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT model, sum(model_messages) AS messages
        FROM {self.t_model}
        {w}
        GROUP BY model
        ORDER BY messages DESC
        """
        return QuerySpec(sql=_norm(sql), note="Model message-count distribution (including auto)")

    # -- Model concentration detail (per-employee share of target-model messages in their total model messages) --
    def model_concentration_detail(self, models: list[str], date_from=None,
                                   date_to=None, identity_filter=None) -> QuerySpec:
        """Per-employee ratio = total target-model messages / the employee's total model messages (metric per the read-only research report).

        Two stages: per_user (aggregate total/target by email, HAVING total>0 to exclude zero-usage users)
        → ratios (compute the ratio). Returns email, ratio (rounded to 4 places), ordered by ratio descending.
        Model names are escaped via _q into IN (...); allowlist validation is done by the API layer using model_list() results.
        Empty models raises ValueError.
        """
        sel = [str(m).strip() for m in (models or []) if str(m).strip()]
        if not sel:
            raise ValueError("models must be a non-empty list")
        in_list = ", ".join(_q(m) for m in sel)
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 sum(model_messages) AS total_msgs,
                 sum(CASE WHEN model IN ({in_list}) THEN model_messages ELSE 0 END) AS target_msgs
          FROM {self.t_model}
          {w}
          GROUP BY email
          HAVING sum(model_messages) > 0
        ),
        ratios AS (
          SELECT email, total_msgs, target_msgs,
                 CAST(target_msgs AS double) / total_msgs AS ratio
          FROM per_user
        )
        SELECT email, total_msgs, target_msgs, round(ratio, 4) AS ratio
        FROM ratios
        ORDER BY ratio DESC
        """
        return QuerySpec(sql=_norm(sql),
                         note="Model concentration detail: per-employee total target-model messages / total model messages; denominator = employees with messages in range")

    # -- All users (for the monthly report: unpaginated, with limit/tier/usage rate) --
    def report_users(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        """Per-user cumulative credits in range + most-recent-day tier/limit + usage rate (monthly report / tier suggestions)."""
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max_by(display_name, date) AS display_name,
               max_by(department, date) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY usage_rate DESC
        """
        return QuerySpec(sql=_norm(sql), note="Monthly report: all users' cumulative credits/limit/usage rate")

    # -- All users' usage (unpaginated, for merging with the registration roster / CSV export) --
    def all_users_usage(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        """Full per-user aggregated usage rows in range (same fields as the detail table, but unpaginated).

        Used by the backend to LEFT JOIN the "registration roster (DynamoDB)" with "usage (Athena)":
        keyed by email, returns all users with usage records; zero-usage users are filled in from the roster.
        Fields align with the `users()` detail table (credits_used/usage_limit/balance/usage_rate/
        is_overage/est_cost/total_messages/active_days/tier).
        """
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max_by(display_name, date) AS display_name,
               max_by(department, date) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(max_by(usage_limit, date) - sum(credits_used),2) AS balance,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate,
               (max_by(usage_limit, date) > 0 AND sum(credits_used) > max_by(usage_limit, date)) AS is_overage,
               round(sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')},2) AS est_cost,
               sum(total_messages) AS total_messages,
               count(distinct date) AS active_days
        FROM {self.t_daily}
        {w}
        GROUP BY email
        """
        return QuerySpec(sql=_norm(sql), note="All users' usage (unpaginated, for merging the roster / CSV export)")

    # -- Generic parameterized query (for the NL Agent: one tool covers most usage questions) --
    def query_usage(self, metric: str = "credits", group_by: str = "none",
                    filters: Optional[dict] = None,
                    date_from=None, date_to=None,
                    sort: str = "desc", limit: int = 50,
                    identity_filter=None) -> QuerySpec:
        """Generic structured query builder (fully allowlisted, does not accept arbitrary SQL).

        metric   : credits | messages | est_cost | active_days | users
        group_by : none | department | subscription_tier | model | date | user
        filters  : {department, subscription_tier, model, user(name/email keyword), overage_only(bool)}
        time/sort/limit are all parameterized and validated.

        Path: metric=messages or group_by=model uses the model long table (fact_user_model_daily);
        everything else uses the daily table aggregated per user (fact_user_daily).
        """
        if metric not in _ALLOWED_METRICS:
            raise ValueError(f"invalid metric: {metric!r}")
        if group_by not in _ALLOWED_GROUP_BY:
            raise ValueError(f"invalid group_by: {group_by!r}")
        filters = filters or {}
        direction = "asc" if str(sort).lower() == "asc" else "desc"
        limit = _valid_limit(limit)

        use_model = (metric == "messages" or group_by == "model")

        # Common filters (department/tier/overage/user keyword) — handled per table by available columns.
        # The model long table has only email (no display_name/department), so its user filter can only be by email.
        def _common_extra(has_dept_tier: bool, email_only_search: bool = False) -> list[str]:
            ex = []
            dep = filters.get("department")
            if dep and has_dept_tier:
                ex.append(f"department = {_q(str(dep))}")
            tier = filters.get("subscription_tier")
            if tier and has_dept_tier:
                ex.append(f"subscription_tier = {_q(_norm_tier(str(tier)))}")
            model = filters.get("model")
            if model and use_model:
                ex.append(f"model = {_q(str(model))}")
            sc = (_email_search_clause(filters.get("user")) if email_only_search
                  else _search_clause(filters.get("user")))
            if sc:
                ex.append(sc)
            return ex

        if use_model:
            # Model long table: only email, so the user filter is by email (a display name must first be resolved to an email in the daily table)
            w = self._where(date_from, date_to, identity_filter,
                            extra=_common_extra(False, email_only_search=True))
            gb = {"model": "model", "user": "email", "date": "date",
                  "none": None}.get(group_by, "model")
            if gb is None:
                sql = f"SELECT sum(model_messages) AS messages FROM {self.t_model} {w}"
            else:
                sql = (f"SELECT {gb} AS {gb}, sum(model_messages) AS messages "
                       f"FROM {self.t_model} {w} GROUP BY {gb} "
                       f"ORDER BY messages {direction} LIMIT {limit}")
            return QuerySpec(sql=_norm(sql),
                             note=f"query_usage: metric=messages group_by={group_by} (model long table, message count = number of prompts)")

        # Daily table: first aggregate per-user metrics, then roll up by group_by
        extra = _common_extra(True)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        having = ("HAVING max_by(usage_limit, date) > 0 AND "
                  "sum(credits_used) > max_by(usage_limit, date)") if filters.get("overage_only") else ""

        per_user = (
            f"SELECT email, max_by(display_name, date) AS display_name, "
            f"max_by(department, date) AS department, "
            f"max_by(subscription_tier, date) AS subscription_tier, "
            f"sum(credits_used) AS credits, "
            # est_cost = monthly fee (by tier) + overage (sum of the daily table's est_cost), same metric as summary.
            f"sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')} AS est_cost, "
            f"count(distinct date) AS active_days "
            f"FROM {self.t_daily} {w} GROUP BY email {having}"
        )

        gb_col = {"department": "department", "subscription_tier": "subscription_tier",
                  "date": "date", "user": "email"}.get(group_by)

        if group_by == "user" or group_by == "none":
            # Detail/summary: list users directly (also list per user when none, so the Agent can read a single user)
            metric_sel = {"credits": "round(credits,1)", "est_cost": "round(est_cost,2)",
                          "active_days": "active_days"}.get(metric, "round(credits,1)")
            sql = (f"SELECT email, display_name, department, subscription_tier, "
                   f"{metric_sel} AS {metric} FROM ({per_user}) "
                   f"ORDER BY {metric} {direction} LIMIT {limit}")
        elif group_by == "date":
            # By day: must go back to raw rows (cannot use per_user's user-level aggregation)
            magg = {"credits": "round(sum(credits_used),1)", "est_cost": "round(sum(est_cost),2)",
                    "users": "count(distinct email)"}.get(metric, "round(sum(credits_used),1)")
            sql = (f"SELECT date, {magg} AS {metric if metric!='users' else 'users'} "
                   f"FROM {self.t_daily} {w} GROUP BY date ORDER BY date")
        else:
            # By department/tier: aggregate per_user again
            if metric == "users":
                # Head count: emit only one users column to avoid a duplicate column name
                sql = (f"SELECT {gb_col} AS {gb_col}, count(*) AS users "
                       f"FROM ({per_user}) GROUP BY {gb_col} "
                       f"ORDER BY users {direction} LIMIT {limit}")
            else:
                if metric == "active_days":
                    agg = "round(avg(active_days),1)"
                elif metric == "est_cost":
                    agg = "round(sum(est_cost),2)"
                else:
                    agg = "round(sum(credits),1)"
                sql = (f"SELECT {gb_col} AS {gb_col}, {agg} AS {metric}, count(*) AS users "
                       f"FROM ({per_user}) GROUP BY {gb_col} "
                       f"ORDER BY {metric} {direction} LIMIT {limit}")
        return QuerySpec(sql=_norm(sql),
                         note=f"query_usage: metric={metric} group_by={group_by} filters={list(filters)}")

    # -- Department comparison (total credits + per capita) --------------------
    def departments(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        WITH per_user AS (
          SELECT email, max(department) AS department, sum(credits_used) AS credits_used
          FROM {self.t_daily}
          {w}
          GROUP BY email
        )
        SELECT department,
               count(*) AS users,
               round(sum(credits_used),1) AS total_credits,
               round(sum(credits_used)/nullif(count(*),0),1) AS avg_credits
        FROM per_user
        GROUP BY department
        ORDER BY total_credits DESC
        """
        return QuerySpec(sql=_norm(sql), note="Per-department total credits and per-capita credits")

    # -- Top N users -----------------------------------------------------------
    def top_users(self, date_from=None, date_to=None, limit: int = 15,
                  identity_filter=None) -> QuerySpec:
        limit = _valid_limit(limit)
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max(display_name) AS display_name,
               max(department) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY credits_used DESC
        LIMIT {limit}
        """
        return QuerySpec(sql=_norm(sql), note=f"Top {limit} users (by total credits in range)")

    # -- Detail table (pagination/sort/department filter/name search) ----------
    def users(self, date_from=None, date_to=None, department: Optional[str] = None,
              sort: str = "credits_used", direction: str = "desc",
              page: int = 1, page_size: int = 20, identity_filter=None,
              search: Optional[str] = None) -> QuerySpec:
        sort_col = sort if sort in _SORTABLE_COLUMNS else "credits_used"
        direction = direction.lower() if direction.lower() in _SORT_DIRS else "desc"
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        offset = (page - 1) * page_size

        extra = []
        if department:
            extra.append(f"department = {_q(department)}")
        sc = _search_clause(search)
        if sc:
            extra.append(sc)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        sql = f"""
        SELECT email,
               max(display_name) AS display_name,
               max(department) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(greatest(max_by(usage_limit, date) - sum(credits_used), 0),2) AS balance,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate,
               (max_by(usage_limit, date) > 0 AND sum(credits_used) > max_by(usage_limit, date)) AS is_overage,
               round(sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')},2) AS est_cost,
               sum(total_messages) AS total_messages,
               count(distinct date) AS active_days
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY {sort_col} {direction}
        OFFSET {offset}
        LIMIT {page_size}
        """
        return QuerySpec(sql=_norm(sql),
                         note=f"Detail table page={page} size={page_size} sort={sort_col} {direction}")

    def users_count(self, date_from=None, date_to=None, department: Optional[str] = None,
                    identity_filter=None, search: Optional[str] = None) -> QuerySpec:
        extra = []
        if department:
            extra.append(f"department = {_q(department)}")
        sc = _search_clause(search)
        if sc:
            extra.append(sc)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        sql = f"""
        SELECT count(*) AS total FROM (
          SELECT email FROM {self.t_daily} {w} GROUP BY email
        )
        """
        return QuerySpec(sql=_norm(sql), note="Detail table total row count (for pagination)")

    # -- Filter options: available periods, department list, model list --------
    def periods(self) -> QuerySpec:
        sql = f"SELECT distinct substr(date,1,7) AS period FROM {self.t_daily} ORDER BY period DESC"
        return QuerySpec(sql=_norm(sql), note="Available data periods (year-month)")

    def department_list(self) -> QuerySpec:
        sql = f"SELECT distinct department FROM {self.t_daily} ORDER BY department"
        return QuerySpec(sql=_norm(sql), note="Department list")

    def model_list(self) -> QuerySpec:
        sql = f"SELECT distinct model FROM {self.t_model} ORDER BY model"
        return QuerySpec(sql=_norm(sql), note="Model list")

    # -- Overage users (common for the Agent) ----------------------------------
    def overage_users(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        # Overage definition: cumulative credits in range > usage_limit (matching the reference dashboard / Kiro monthly limit).
        # First aggregate per user, then filter with sum(credits)>max(usage_limit); overage_credits = the portion over the limit.
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 max(display_name) AS display_name,
                 max(department) AS department,
                 sum(credits_used) AS credits_used,
                 max_by(usage_limit, date) AS usage_limit
          FROM {self.t_daily}
          {w}
          GROUP BY email
        )
        SELECT email, display_name, department,
               round(greatest(credits_used - usage_limit, 0),1) AS overage_credits,
               round(credits_used,1) AS credits_used,
               round(usage_limit,1) AS usage_limit
        FROM per_user
        WHERE usage_limit > 0 AND credits_used > usage_limit
        ORDER BY overage_credits DESC
        """
        return QuerySpec(sql=_norm(sql), note="Users who triggered overage (cumulative credits>usage_limit in range)")


def _norm(sql: str) -> str:
    """Collapse redundant whitespace, convenient for logs and the cache key."""
    return re.sub(r"\s+", " ", sql).strip()


def _search_clause(search: Optional[str]) -> str:
    """Build a parameterized WHERE fragment for a fuzzy name/email search (injection-safe).

    Matches lower(display_name) or lower(email) containing the keyword. Escapes the LIKE wildcards % _ and single quotes.
    Empty/all-whitespace returns an empty string (no condition added).
    """
    s = (search or "").strip().lower()
    if not s:
        return ""
    # Escape LIKE special characters, then wrap with %...%; _q then handles single quotes
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = _q(f"%{esc}%")
    return (f"(lower(display_name) LIKE {pat} ESCAPE '\\' "
            f"OR lower(email) LIKE {pat} ESCAPE '\\')")


def _email_search_clause(search: Optional[str]) -> str:
    """Fuzzy match by email only (for the model long table, which has no display_name column)."""
    s = (search or "").strip().lower()
    if not s:
        return ""
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = _q(f"%{esc}%")
    return f"lower(email) LIKE {pat} ESCAPE '\\'"


def _norm_tier(v: str) -> str:
    """Normalize various tier spellings to the table's canonical values (Pro/ProPlus/ProMax/Power)."""
    key = (v or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "").replace("+", "plus")
    return {"pro": "Pro", "proplus": "ProPlus", "promax": "ProMax",
            "power": "Power"}.get(key, v.strip())


def _valid_limit(n: int) -> int:
    n = int(n)
    if n < 1:
        return 1
    return min(n, 500)


# ---------------------------------------------------------------------------
# Executor: real Athena runner + TTL cache
# ---------------------------------------------------------------------------
Row = dict[str, Any]
Runner = Callable[[str], list[Row]]


@dataclass
class _CacheEntry:
    ts: float
    rows: list[Row]


class QueryLayer:
    """Execute a QuerySpec into a list of rows. The runner is injectable for unit tests."""

    def __init__(self, runner: Runner, cache_ttl: float = 60.0):
        self._runner = runner
        self._ttl = cache_ttl
        self._cache: dict[str, _CacheEntry] = {}

    def run(self, spec: QuerySpec) -> list[Row]:
        now = time.time()
        hit = self._cache.get(spec.sql)
        if hit and (now - hit.ts) < self._ttl:
            return hit.rows
        rows = self._runner(spec.sql)
        self._cache[spec.sql] = _CacheEntry(ts=now, rows=rows)
        return rows

    def one(self, spec: QuerySpec) -> Row:
        rows = self.run(spec)
        return rows[0] if rows else {}


def make_athena_runner(*, database: str, workgroup: str, region: str,
                       poll_interval: float = 0.5, timeout: float = 45.0) -> Runner:
    """Build a real Athena runner (boto3). Works under Lambda/local credentials."""
    import logging
    import boto3  # lazy import so unit tests do not depend on boto3

    log = logging.getLogger("athena")
    client = boto3.client("athena", region_name=region)

    def _run(sql: str) -> list[Row]:
        try:
            qid = client.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": database},
                WorkGroup=workgroup,
            )["QueryExecutionId"]
        except Exception:
            # Do not log the SQL text: the query embeds personal info such as user emails as literals, so log only its length.
            log.exception("start_query_execution failed; wg=%s db=%s sql_len=%d",
                          workgroup, database, len(sql))
            raise

        deadline = time.time() + timeout
        while True:
            st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = st["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                reason = st.get("StateChangeReason", "")
                log.error("Athena %s qid=%s reason=%s", state, qid, reason)
                raise RuntimeError(f"Athena query {state}: {reason}")
            if time.time() > deadline:
                raise TimeoutError(f"Athena query timed out after {timeout}s (qid={qid})")
            _POLL_WAIT.wait(poll_interval)

        return _collect_results(client, qid)

    return _run


_POLL_WAIT = threading.Event()  # never set; .wait(t) is the poll delay


def _collect_results(client, qid: str) -> list[Row]:
    """Convert the paginated get_query_results into [{col: typed_value}]."""
    rows: list[Row] = []
    header: list[str] = []
    token = None
    first = True
    while True:
        kw = {"QueryExecutionId": qid}
        if token:
            kw["NextToken"] = token
        resp = client.get_query_results(**kw)
        rs = resp["ResultSet"]
        col_info = rs["ResultSetMetadata"]["ColumnInfo"]
        types = [c["Type"] for c in col_info]
        for i, r in enumerate(rs["Rows"]):
            values = [d.get("VarCharValue") for d in r["Data"]]
            if first and i == 0:
                header = [c["Name"] for c in col_info]
                continue
            rows.append({header[j]: _coerce(values[j], types[j]) for j in range(len(header))})
        first = False
        token = resp.get("NextToken")
        if not token:
            break
    return rows


def _coerce(v: Optional[str], t: str) -> Any:
    if v is None:
        return None
    if t in ("bigint", "integer", "int", "smallint", "tinyint"):
        try:
            return int(v)
        except ValueError:
            return None
    if t in ("double", "float", "decimal", "real"):
        try:
            return float(v)
        except ValueError:
            return None
    if t == "boolean":
        return v.lower() == "true"
    return v

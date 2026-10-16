"""Shared query-layer unit tests: no real AWS.

Strategy:
- Validate QuerySpec.sql structure (correct table, GROUP BY, injected WHERE, parameterized LIMIT/OFFSET).
- Validate injection guards: invalid date/period raises; single quotes are escaped; invalid sort column falls back.
- Validate QueryLayer execution + TTL cache (the runner is called only once).
"""
import pytest

from backend.shared.athena_client import (
    Queries, QueryLayer, QuerySpec, _coerce, _q,
)


@pytest.fixture
def q():
    return Queries()


# --- SQL construction ------------------------------------------------------
def test_summary_sql_shape(q):
    spec = q.summary(date_from="2026-09-01", date_to="2026-09-07")
    s = spec.sql
    assert "kiro_usage.fact_user_daily" in s
    assert "date >= '2026-09-01'" in s
    assert "date <= '2026-09-07'" in s
    assert "tier_power" in s and "overage_users" in s


def test_trend_groups_by_date(q):
    s = q.trend(date_from="2026-09-01", date_to="2026-09-07").sql
    assert "GROUP BY date" in s and "ORDER BY date" in s


def test_models_uses_model_table(q):
    s = q.models().sql
    assert "kiro_usage.fact_user_model_daily" in s
    assert "GROUP BY model" in s


def test_top_users_limit_param(q):
    s = q.top_users(limit=15).sql
    assert "LIMIT 15" in s
    # upper-bound protection
    assert "LIMIT 500" in q.top_users(limit=99999).sql
    assert "LIMIT 1" in q.top_users(limit=0).sql


def test_users_pagination_and_sort(q):
    s = q.users(page=3, page_size=20, sort="balance", direction="asc").sql
    assert "OFFSET 40" in s      # (3-1)*20
    assert "LIMIT 20" in s
    assert "ORDER BY balance asc" in s


def test_users_invalid_sort_falls_back(q):
    s = q.users(sort="DROP TABLE", direction="weird").sql
    assert "ORDER BY credits_used desc" in s
    assert "DROP TABLE" not in s


def test_users_department_filter_escaped(q):
    s = q.users(department="R&D's team").sql
    assert "department = 'R&D''s team'" in s


def test_model_concentration_detail_shape(q):
    s = q.model_concentration_detail(
        ["auto", "claude_opus_5"], date_from="2026-09-01", date_to="2026-09-07").sql
    assert "kiro_usage.fact_user_model_daily" in s
    assert "GROUP BY email" in s
    assert "HAVING sum(model_messages) > 0" in s
    assert "IN ('auto', 'claude_opus_5')" in s
    assert "ORDER BY ratio DESC" in s
    assert "date >= '2026-09-01'" in s
    # new columns: total_msgs/target_msgs pass through to the ratios CTE and the final SELECT
    assert "total_msgs" in s and "target_msgs" in s
    assert "round(ratio, 4) AS ratio" in s


def test_model_concentration_detail_escapes_quotes(q):
    s = q.model_concentration_detail(["a'b"]).sql
    assert "IN ('a''b')" in s


def test_model_concentration_detail_empty_models_raises(q):
    with pytest.raises(ValueError):
        q.model_concentration_detail([])
    with pytest.raises(ValueError):
        q.model_concentration_detail(["   "])


def test_model_concentration_detail_invalid_date_raises(q):
    with pytest.raises(ValueError):
        q.model_concentration_detail(["auto"], date_from="2026/09/01")


def test_department_and_filter_lists(q):
    assert "distinct department" in q.department_list().sql
    assert "distinct model" in q.model_list().sql
    assert "substr(date,1,7)" in q.periods().sql


# --- injection guard -------------------------------------------------------
def test_invalid_date_raises(q):
    with pytest.raises(ValueError):
        q.summary(date_from="2026/09/01")
    with pytest.raises(ValueError):
        q.trend(date_to="not-a-date")


def test_query_usage_rejects_invalid_metric_and_group_by(q):
    with pytest.raises(ValueError):
        q.query_usage(metric="credits); DROP")
    with pytest.raises(ValueError):
        q.query_usage(metric="credits", group_by="email; DROP")


def test_query_usage_accepts_all_allowed_combinations(q):
    for m in ("credits", "messages", "est_cost", "active_days", "users"):
        for g in ("none", "department", "subscription_tier", "model", "date", "user"):
            assert q.query_usage(metric=m, group_by=g).sql.startswith("SELECT")


def test_identity_filter_injected(q):
    s = q.summary(identity_filter={"email": "Admin@Example.com"}).sql
    assert "lower(email) = 'admin@example.com'" in s


def test_identity_filter_none_no_where(q):
    s = q.trend().sql  # no filters
    assert "WHERE" not in s


def test_quote_escaping():
    assert _q("a'b") == "'a''b'"


# --- execution + cache -----------------------------------------------------
def test_query_layer_runs_and_caches():
    calls = {"n": 0}

    def runner(sql):
        calls["n"] += 1
        return [{"total_users": 40}]

    layer = QueryLayer(runner, cache_ttl=60)
    spec = QuerySpec(sql="SELECT 1")
    r1 = layer.run(spec)
    r2 = layer.run(spec)
    assert r1 == r2 == [{"total_users": 40}]
    assert calls["n"] == 1  # second call hits the cache


def test_query_layer_cache_expiry():
    calls = {"n": 0}

    def runner(sql):
        calls["n"] += 1
        return [{"x": calls["n"]}]

    layer = QueryLayer(runner, cache_ttl=0)  # expires immediately
    spec = QuerySpec(sql="SELECT 1")
    layer.run(spec)
    layer.run(spec)
    assert calls["n"] == 2


def test_one_returns_first_or_empty():
    layer = QueryLayer(lambda sql: [], cache_ttl=0)
    assert layer.one(QuerySpec(sql="x")) == {}
    layer2 = QueryLayer(lambda sql: [{"a": 1}, {"a": 2}], cache_ttl=0)
    assert layer2.one(QuerySpec(sql="y")) == {"a": 1}


# --- type coercion ---------------------------------------------------------
def test_coerce_types():
    assert _coerce("42", "bigint") == 42
    assert _coerce("3.5", "double") == 3.5
    assert _coerce("true", "boolean") is True
    assert _coerce("false", "boolean") is False
    assert _coerce(None, "bigint") is None
    assert _coerce("abc", "varchar") == "abc"

"""FastAPI endpoint unit tests: inject a mock query layer via dependency_overrides, no AWS.

MockLayer returns canned rows based on the tables/keywords in the SQL, covering the response shapes of the 7 endpoints.
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.deps import get_query_layer
from backend.shared.athena_client import QuerySpec


class MockLayer:
    """Return canned results based on QuerySpec.note/sql keywords."""

    def run(self, spec: QuerySpec):
        s = spec.sql
        if "GROUP BY date" in s:  # trend
            return [
                {"date": "2026-09-01", "credits": 100.0, "active_users": 10},
                {"date": "2026-09-02", "credits": 120.5, "active_users": 12},
            ]
        if "fact_user_model_daily" in s and "GROUP BY model" in s:
            return [
                {"model": "claude_haiku_4_5", "messages": 13036},
                {"model": "auto", "messages": 12237},
            ]
        if "ORDER BY ratio DESC" in s:  # model_concentration_detail
            return [
                {"email": "a@example.com", "ratio": 0.9, "target_msgs": 90, "total_msgs": 100},
                {"email": "b@example.com", "ratio": 0.6, "target_msgs": 60, "total_msgs": 100},
                {"email": "c@example.com", "ratio": 0.2, "target_msgs": 20, "total_msgs": 100},
                {"email": "d@example.com", "ratio": 0.0, "target_msgs": 0, "total_msgs": 100},
            ]
        if "GROUP BY department" in s:
            return [
                {"department": "Engineering", "users": 10, "total_credits": 4403.4, "avg_credits": 440.3},
                {"department": "Ungrouped", "users": 5, "total_credits": 1998.7, "avg_credits": 399.7},
            ]
        if "ORDER BY credits_used DESC" in s and "LIMIT" in s:  # top-users
            return [
                {"email": "u1@example.com", "display_name": "Liang Wei", "department": "Engineering",
                 "subscription_tier": "Power", "credits_used": 250.8},
            ]
        if "ORDER BY usage_rate" in s:  # report_users
            return [
                {"email": "hi@example.com", "display_name": "HighUser", "department": "SunCloud",
                 "subscription_tier": "Pro", "credits_used": 1500.0, "usage_limit": 1000.0, "usage_rate": 1.5},
                {"email": "lo@example.com", "display_name": "LowUser", "department": "SunCloud",
                 "subscription_tier": "ProMax", "credits_used": 500.0, "usage_limit": 5000.0, "usage_rate": 0.10},
                {"email": "mid@example.com", "display_name": "MidUser", "department": "SunCloud",
                 "subscription_tier": "ProPlus", "credits_used": 1200.0, "usage_limit": 2000.0, "usage_rate": 0.60},
            ]
        if "GROUP BY email" in s:  # all_users_usage (for merging the roster / CSV export)
            return [
                {"email": "u1@example.com", "display_name": "Liang Wei", "department": "Engineering",
                 "subscription_tier": "Power", "credits_used": 250.8, "usage_limit": 400.0,
                 "balance": 149.2, "usage_rate": 0.627, "is_overage": False, "est_cost": 10.03,
                 "total_messages": 916, "active_days": 5},
                {"email": "u2@example.com", "display_name": "Zhang San", "department": "Engineering",
                 "subscription_tier": "Pro", "credits_used": 120.0, "usage_limit": 400.0,
                 "balance": 280.0, "usage_rate": 0.30, "is_overage": False, "est_cost": 4.8,
                 "total_messages": 300, "active_days": 3},
            ]
        if "OFFSET" in s:  # users detail page
            return [
                {"email": "u1@example.com", "display_name": "Liang Wei", "department": "Engineering",
                 "subscription_tier": "Power", "credits_used": 250.8, "usage_limit": 400.0,
                 "balance": 149.2, "usage_rate": 0.627, "is_overage": False, "est_cost": 10.03,
                 "total_messages": 916, "active_days": 5},
            ]
        if "substr(date,1,7)" in s:  # periods
            return [{"period": "2026-09"}]
        if "distinct department" in s:
            return [{"department": "Engineering"}, {"department": "Ungrouped"}]
        if "distinct model" in s:
            return [{"model": "auto"}, {"model": "gpt_5"}]
        return []

    def one(self, spec: QuerySpec):
        s = spec.sql
        if "tier_power" in s:  # summary
            return {
                "total_users": 40, "active_users": 36, "total_credits": 21374.2,
                "est_cost": 3206.13, "overage_users": 2, "avg_credits_per_active": 593.7,
                "tier_pro": 4, "tier_proplus": 4, "tier_promax": 9, "tier_power": 23,
                "daily_active_avg": 18.5, "daily_active_peak": 24,
            }
        if "count(*) AS total FROM" in s:  # users_count
            return {"total": 40}
        rows = self.run(spec)
        return rows[0] if rows else {}


@pytest.fixture
def client():
    app.dependency_overrides[get_query_layer] = lambda: MockLayer()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_summary(client):
    r = client.get("/api/summary?period=2026-09")
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["total_users"] == 40
    assert body["kpis"]["active_users"] == 36
    assert body["kpis"]["activation_rate"] == round(36 / 40, 4)
    assert body["tiers"]["Power"] == 23
    assert body["range"]["from"] == "2026-09-01"
    # cost note (approach A): monthly fee + overage, not a bill
    assert "monthly fee" in body["est_cost_note"]
    assert "$0.04/credit" in body["est_cost_note"]


def test_trend(client):
    r = client.get("/api/trend?from=2026-09-01&to=2026-09-07")
    assert r.status_code == 200
    s = r.json()["series"]
    assert len(s) == 2 and s[0]["date"] == "2026-09-01" and s[1]["active_users"] == 12


def test_models(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    m = r.json()["models"]
    assert m[0]["model"] == "claude_haiku_4_5" and m[0]["messages"] == 13036


def test_model_concentration_ok(client):
    # valid model (auto is in the distinct-model allowlist), threshold 0.5
    r = client.get("/api/model-concentration?models=auto&threshold=0.5&bins=10")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == ["auto"]
    assert body["total_users"] == 4            # 4 detail rows
    assert body["concentrated_users"] == 2     # ratio>=0.5: 0.9, 0.6
    assert body["concentration_rate"] == round(2 / 4, 4)
    assert body["ratios"] == [0.9, 0.6, 0.2, 0.0]
    assert len(body["histogram"]) == 10
    # 0.9->bucket9, 0.6->bucket6, 0.2->bucket2, 0.0->bucket0
    counts = {b["from"]: b["count"] for b in body["histogram"]}
    assert counts[0.9] == 1 and counts[0.6] == 1 and counts[0.2] == 1 and counts[0.0] == 1


def test_model_concentration_threshold_counts(client):
    # threshold 0.6 (inclusive): 0.9, 0.6 hit -> 2
    r = client.get("/api/model-concentration?models=auto&threshold=0.6")
    assert r.status_code == 200
    assert r.json()["concentrated_users"] == 2


def test_model_concentration_invalid_model_400(client):
    r = client.get("/api/model-concentration?models=not_a_model")
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "no valid model selected"
    assert "auto" in body["valid_models"]


def test_model_concentration_members_shape(client):
    # members returns all employees with messages (not only above-threshold), by ratio descending, all keys present
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert isinstance(members, list) and len(members) == 4   # 4 rows total
    keys = {"email", "display_name", "department",
            "target_messages", "total_messages", "ratio"}
    for m in members:
        assert keys <= set(m.keys())
    assert [m["ratio"] for m in members] == [0.9, 0.6, 0.2, 0.0]
    # key mapping: target_messages/total_messages come from the query layer target_msgs/total_msgs
    assert members[0]["target_messages"] == 90
    assert members[0]["total_messages"] == 100


def test_model_concentration_member_roster_fallback(client):
    # DEPT_TABLE is unconfigured in the test env → _roster returns [], everything falls back:
    # display_name=email prefix, department="Ungrouped"
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0]["display_name"] == "a"
    assert members[0]["department"] == "Ungrouped"


def test_model_concentration_member_roster_hit(client, monkeypatch):
    # monkeypatch _roster: matched emails take the roster name/department, the rest fall back
    import backend.api.main as main
    monkeypatch.setattr(main, "_roster", lambda settings: [
        {"email": "a@example.com", "display_name": "Zhang Wei", "department": "Engineering"},
    ])
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0]["display_name"] == "Zhang Wei"
    assert members[0]["department"] == "Engineering"
    # unmatched still falls back
    assert members[1]["display_name"] == "b"
    assert members[1]["department"] == "Ungrouped"


def test_departments(client):
    r = client.get("/api/departments")
    assert r.status_code == 200
    d = r.json()["departments"]
    assert d[0]["department"] == "Engineering" and d[0]["users"] == 10


def test_top_users_default_limit(client):
    r = client.get("/api/top-users")
    assert r.status_code == 200
    assert r.json()["users"][0]["email"] == "u1@example.com"


def test_users_detail(client):
    # include_all=false uses the old behavior (Athena pagination, usage users only)
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used&include_all=false")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 40 and body["page"] == 1
    item = body["items"][0]
    assert item["email"] == "u1@example.com"
    assert item["status"] == "normal"  # usage_rate 0.627 -> normal


def test_users_detail_include_all_default(client):
    # default include_all=true: merge usage based on the roster (the test env has no roster table
    # → degrades to usage users only). This path sorts/paginates in memory.
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # all_users_usage returns 2 rows
    emails = [it["email"] for it in body["items"]]
    assert emails == ["u1@example.com", "u2@example.com"]  # by credits_used desc


def test_users_include_out_of_roster_usage_users(client, monkeypatch):
    """Union metric: users off the roster but with usage should also appear in the detail (department=Ungrouped).

    The roster (_roster) has only u1; Athena usage (all_users_usage) has u1+u2. u2 is not on the roster,
    but really has usage, so it must appear in the detail table, with department "Ungrouped" and tier/limit from Athena.
    """
    import backend.api.main as main
    monkeypatch.setattr(main, "_roster", lambda settings: [
        {"email": "u1@example.com", "display_name": "Liang Wei", "department": "Engineering"},
    ])
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # union of u1 (on roster) + u2 (off roster, with usage)
    by_email = {it["email"]: it for it in body["items"]}
    assert "u2@example.com" in by_email  # off-roster usage user is included
    u2 = by_email["u2@example.com"]
    assert u2["department"] == "Ungrouped"          # off-roster uniformly goes to Ungrouped
    assert u2["subscription_tier"] == "Pro"      # tier uses the real Athena value
    assert u2["usage_limit"] == 400.0            # limit uses the real Athena value


def test_status_tiny_usage_is_normal_not_unused():
    """Tiny usage (usage_rate rounded to 0 but actually used) should be marked normal, not unused.

    E.g. a Pro user consuming 0.04 credit → usage_rate≈0.00004 → round(,4)=0. Looking at usage_rate alone
    misclassifies it as "unused"; when has_usage=True (credits>0 or messages>0) it should be "normal".
    """
    import backend.api.main as main
    # really used but usage_rate zeroed → normal
    assert main._status(0.0, is_overage=False, has_usage=True) == "normal"
    # completely unused → unused
    assert main._status(0.0, is_overage=False, has_usage=False) == "unused"
    # overage / near-limit are unaffected
    assert main._status(1.05, has_usage=True) == "overage"
    assert main._status(0.85, has_usage=True) == "near_limit"


def test_users_export_csv(client):
    r = client.get("/api/users/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")  # strip BOM
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("Email,Name,Department,Tier")
    assert len(lines) == 3  # header + 2 data rows
    assert "u1@example.com" in text and "u2@example.com" in text


def test_users_export_csv_neutralizes_formula_injection():
    """When name/department starts with = + - @, prepend a single quote (CSV formula-injection guard); numeric columns unchanged."""
    import csv
    import io

    class EvilLayer(MockLayer):
        def run(self, spec):
            rows = super().run(spec)
            if "GROUP BY email" in spec.sql:
                rows = [dict(r) for r in rows]
                rows[0]["display_name"] = '=HYPERLINK("http://evil.example","x")'
                rows[0]["department"] = "@SUM(1+1)"
            return rows

    app.dependency_overrides[get_query_layer] = lambda: EvilLayer()
    try:
        r = TestClient(app).get("/api/users/export")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    evil = next(row for row in rows[1:] if row[0] == "u1@example.com")
    assert evil[1] == '\'=HYPERLINK("http://evil.example","x")'
    assert evil[2] == "'@SUM(1+1)"
    assert evil[4] == "250.8"  # numeric column as-is
    other = next(row for row in rows[1:] if row[0] == "u2@example.com")
    assert other[1] == "Zhang San"  # ordinary value unchanged


def test_users_export_filename_has_dept_and_range(client):
    import urllib.parse

    r = client.get("/api/users/export?department=Engineering&from=2026-09-01&to=2026-09-30")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # the real filename (which may contain a non-ASCII department) is in filename*=UTF-8'', decode before comparing
    import re
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    assert m, cd
    decoded = urllib.parse.unquote(m.group(1))
    assert decoded == "kiro-users-Engineering-20260901-20260930.csv", decoded


def test_users_export_filename_no_range_falls_back(client):
    import re
    import urllib.parse

    r = client.get("/api/users/export")  # no department, no range
    cd = r.headers["content-disposition"]
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    assert m, cd
    decoded = urllib.parse.unquote(m.group(1))
    # department falls back to "all", range falls back to "asofYYYYMMDD"
    assert decoded.startswith("kiro-users-all-asof") and decoded.endswith(".csv"), decoded


def test_filters(client):
    r = client.get("/api/filters")
    assert r.status_code == 200
    body = r.json()
    assert body["periods"] == ["2026-09"]
    assert "Ungrouped" in body["departments"]
    assert "auto" in body["models"]


def test_users_invalid_sort_is_accepted_and_sanitized(client):
    # a non-allowlisted sort should not 500 (the query layer falls back to credits_used)
    r = client.get("/api/users?sort=DROP+TABLE")
    assert r.status_code == 200


def test_summary_bad_period_rejected(client):
    r = client.get("/api/summary?period=2026-9")  # does not match YYYY-MM
    assert r.status_code == 422


def test_report(client):
    r = client.get("/api/report?period=2026-09")
    assert r.status_code == 200
    body = r.json()
    # summary KPIs
    assert body["kpis"]["active_users"] == 36
    assert body["kpis"]["tiers"]["Power"] == 23
    # plan match: HighUser(Pro,1500>1000)->ProPlus; LowUser(ProMax,500 only needs Pro)->Pro (multi-tier down); MidUser(ProPlus,1200 falls in Pro+) keeps
    ups = {u["email"]: u["suggested_tier"] for u in body["upgrades"]}
    downs = {d["email"]: d["suggested_tier"] for d in body["downgrades"]}
    assert ups == {"hi@example.com": "ProPlus"}
    assert downs == {"lo@example.com": "Pro"}
    # usage-rate threshold model: all users[] carry one-tier up/down suggestions (suggested_up/suggested_down)
    users = body["users"]
    assert len(users) == 3
    by_email = {u["email"]: u for u in users}
    assert by_email["hi@example.com"]["suggested_up"] == "ProPlus"
    assert by_email["hi@example.com"]["suggested_down"] is None   # Pro cannot downgrade further
    assert by_email["lo@example.com"]["suggested_up"] == "Power"
    assert by_email["lo@example.com"]["suggested_down"] == "ProPlus"
    assert by_email["mid@example.com"]["suggested_up"] == "ProMax"
    assert by_email["mid@example.com"]["suggested_down"] == "Pro"
    # each row has usage_rate (for frontend local threshold filtering)
    for u in users:
        assert "usage_rate" in u


def test_report_dedup(client, monkeypatch):
    """/api/report dedup integration: monkeypatch _roster to attach employee number/created time, assert the four new keys on users[].

    report_users returns three rows hi@/lo@/mid@example.com. The roster gives hi@/mid@ the same employee number E1
    (mid@ created earlier = primary, hi@ is the duplicate), lo@ has an empty employee number (independent), and there is no off-roster
    usage user (all three rows are on the roster, so "off-roster" is additionally verified via the roster not containing a row).
    """
    import backend.api.main as main
    monkeypatch.setattr(main, "_roster", lambda settings: [
        # hi@ / mid@ same employee number E1: mid@ created earlier -> mid@ primary, hi@ duplicate.
        {"email": "mid@example.com", "display_name": "MidUser", "department": "SunCloud",
         "employee_number": "E1", "created_date": "2024-01-01T00:00:00+00:00"},
        {"email": "hi@example.com", "display_name": "HighUser", "department": "SunCloud",
         "employee_number": "E1", "created_date": "2024-05-01T00:00:00+00:00"},
        # lo@ empty employee number -> independent subscription. Note: lo@ is not on the roster (off-roster usage user), hitting the "no match" branch.
    ])
    r = client.get("/api/report?period=2026-09")
    assert r.status_code == 200
    body = r.json()
    users = body["users"]
    by_email = {u["email"]: u for u in users}

    # each row has the four new keys
    for u in users:
        for k in ("employee_number", "created_date",
                  "is_duplicate_subscription", "primary_email"):
            assert k in u

    # hi@ / mid@ same employee number E1: mid@ primary, hi@ duplicate, primary_email points to mid@
    assert by_email["mid@example.com"]["employee_number"] == "E1"
    assert by_email["mid@example.com"]["is_duplicate_subscription"] is False
    assert by_email["mid@example.com"]["primary_email"] == ""
    assert by_email["hi@example.com"]["is_duplicate_subscription"] is True
    assert by_email["hi@example.com"]["primary_email"] == "mid@example.com"

    # lo@ off-roster (_roster does not contain it) -> both columns "", independent subscription
    assert by_email["lo@example.com"]["employee_number"] == ""
    assert by_email["lo@example.com"]["created_date"] == ""
    assert by_email["lo@example.com"]["is_duplicate_subscription"] is False
    assert by_email["lo@example.com"]["primary_email"] == ""

    # regression: upgrades/downgrades take the raw users, no dedup keys added, assertions still hold
    ups = {u["email"]: u["suggested_tier"] for u in body["upgrades"]}
    downs = {d["email"]: d["suggested_tier"] for d in body["downgrades"]}
    assert ups == {"hi@example.com": "ProPlus"}
    assert downs == {"lo@example.com": "Pro"}
    for u in body["upgrades"] + body["downgrades"]:
        assert "is_duplicate_subscription" not in u
    # the one-tier up/down suggestions are still present
    assert by_email["lo@example.com"]["suggested_up"] == "Power"
    assert by_email["lo@example.com"]["suggested_down"] == "ProPlus"


# --- single authorization boundary (API Gateway JWT authorizer only) -------
def test_no_app_layer_authorization():
    """Any authenticated user of the pool is authorized: a request carrying an API Gateway JWT authorizer
    context (sub/email only, no role claims) reaches the route and returns 200."""
    import asyncio

    app.dependency_overrides[get_query_layer] = lambda: MockLayer()

    async def call():
        scope = {
            "type": "http", "method": "GET", "path": "/api/summary",
            "raw_path": b"/api/summary", "query_string": b"period=2026-09",
            "headers": [], "aws.event": {"requestContext": {"authorizer": {
                "jwt": {"claims": {"sub": "u-1", "email": "user@example.com"}}}}},
            "scheme": "http", "server": ("test", 80), "client": ("t", 1),
            "http_version": "1.1",
        }
        messages = []
        received = {"done": False}

        async def receive():
            if not received["done"]:
                received["done"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(msg):
            messages.append(msg)

        await app(scope, receive, send)
        return next(m["status"] for m in messages if m["type"] == "http.response.start")

    try:
        assert asyncio.run(call()) == 200
    finally:
        app.dependency_overrides.clear()


def test_removed_routes_are_gone(client):
    """The remote MCP endpoint, the tier-request endpoints and the admin user-management API no longer exist."""
    assert client.post("/mcp", json={}).status_code == 404
    assert client.post("/api/tier-requests", json={}).status_code == 404
    assert client.get("/api/tier-requests").status_code == 404
    assert client.get("/api/admin/users").status_code == 404
    assert client.post("/api/admin/users", json={}).status_code == 404

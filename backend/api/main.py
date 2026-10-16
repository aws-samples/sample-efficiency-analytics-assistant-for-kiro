"""Dashboard REST API (FastAPI on Lambda).

Prefix `/api`; every endpoint except `/api/health` is only reachable through the API Gateway Cognito JWT authorizer.
There is no application-level authorization: any user of the Cognito user pool is an authorized dashboard user.
Routes only do parameter validation + call the shared query layer + assemble frontend-friendly responses; derived metrics are unified in the query layer/derivations.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Body, Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    from backend.shared.athena_client import Queries, QueryLayer
    from backend.shared.recommendations import recommendations as _recommendations
    from backend.shared.recommendations import annotate_tier_steps as _annotate_tier_steps
    from backend.api.settings import Settings, get_settings
    from backend.api.deps import get_queries, get_query_layer
except ImportError:  # flat packaging inside Lambda
    from shared.athena_client import Queries, QueryLayer  # type: ignore
    from shared.recommendations import recommendations as _recommendations  # type: ignore
    from shared.recommendations import annotate_tier_steps as _annotate_tier_steps  # type: ignore
    from settings import Settings, get_settings  # type: ignore
    from deps import get_queries, get_query_layer  # type: ignore

import os

from fastapi import Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Kiro Usage Dashboard API", version="1.0.0")

# Model multiplier maintenance routes (/api/model-multipliers); authentication is enforced by the API Gateway JWT authorizer
try:
    from backend.api.model_multipliers import router as model_multipliers_router
except ImportError:  # flat packaging inside Lambda
    from model_multipliers import router as model_multipliers_router  # type: ignore
app.include_router(model_multipliers_router)

# CORS: same-origin configuration as API Gateway CORS—infra injects the cdk context `corsOrigins` as a comma-separated
# CORS_ORIGINS; when unset it falls back to "*" (matching the gateway-side default). A real deployment should set corsOrigins
# to the CloudFront site URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # Let the browser read the CSV export's filename (non-safelisted headers are not exposed by default under CORS).
    expose_headers=["Content-Disposition"],
)

# Cost note (approach A): subscription monthly fee (whole month by tier) + overage cost, not a bill.
_EST_COST_NOTE = (
    "Estimate: subscription monthly fee (by tier, Pro $20 / Pro+ $40 / Pro Max $100 / Power $200, whole month)"
    " + overage cost (the over-limit part at $0.04/credit); the monthly fee is not prorated by days subscribed,"
    " the magnitude matches the subscription bill, and for exact reconciliation refer to the AWS bill / Cost Explorer."
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/summary")
def summary(
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    date_from, date_to = _resolve_range(period, date_from, date_to)
    row = layer.one(q.summary(date_from, date_to))
    total = int(row.get("total_users") or 0)      # users with usage records in range
    active = int(row.get("active_users") or 0)    # credits_used>0 in range
    daily_active_avg = row.get("daily_active_avg") or 0
    daily_active_peak = int(row.get("daily_active_peak") or 0)
    # Registered count metric: roster ∪ users with usage (including usage users not on the roster).
    registered = _registered_total(settings, _usage_emails(q, layer, date_from, date_to)) or total
    return {
        "range": {"from": date_from, "to": date_to},
        "kpis": {
            "registered_users": registered,
            "total_users": total,
            "active_users": active,
            "activation_rate": round(active / registered, 4) if registered else 0.0,
            "daily_active": daily_active_avg,
            "daily_active_avg": daily_active_avg,
            "daily_active_peak": daily_active_peak,
            "total_credits": row.get("total_credits") or 0,
            "overage_users": int(row.get("overage_users") or 0),
            "est_cost": row.get("est_cost") or 0,
            "avg_credits_per_active": row.get("avg_credits_per_active") or 0,
        },
        "tiers": {
            "Pro": int(row.get("tier_pro") or 0),
            "ProPlus": int(row.get("tier_proplus") or 0),
            "ProMax": int(row.get("tier_promax") or 0),
            "Power": int(row.get("tier_power") or 0),
        },
        "est_cost_note": _EST_COST_NOTE,
    }


def _dedup_sort_key(row: dict):
    """Dedup sort key: empty created_date goes last; ISO8601 string ascending == chronological; email as a tiebreaker.

    ISO8601 lexical order == chronological, so created_date is compared as a string directly. cd=="" is True(1) and sorts
    after values (False/0), guaranteeing that a subscription with a missing creation time is never treated as the "earliest" and wrongly chosen as the primary.
    """
    cd = str(row.get("created_date") or "").strip()
    return (cd == "", cd, str(row.get("email") or ""))


def annotate_duplicate_subscriptions(users: list[dict]) -> list[dict]:
    """Mark duplicate subscriptions grouped by employee_number (pure function, no in-place mutation, returns a new list).

    Metric:
      1) Group key = str(employee_number).strip(). An empty string = an independent subscription, not grouped,
         directly is_duplicate_subscription=False / primary_email="".
      2) Within a non-empty group, sort by _dedup_sort_key (empty created_date last, others by ISO ascending,
         email as a tiebreaker): the first row is the primary (False, primary_email=""); the rest are duplicates
         (True, primary_email=the primary's email).
      3) A single-member group is the primary; when a group has no created_date at all, the primary is chosen by email ascending (sort-key fallback).
      4) The return order matches the input (backfilled via an email->mark mapping, not disturbing ORDER BY usage_rate DESC).

    Zero-trust on input: str(...).strip() when employee_number/created_date are not strings, never raises.
    """
    # 1) Group: bucket by employee number (skip empty, marked independent later).
    groups: dict[str, list[dict]] = {}
    for row in users:
        empno = str(row.get("employee_number") or "").strip()
        if not empno:
            continue
        groups.setdefault(empno, []).append(row)

    # 2) Sort each group to choose the primary, producing an email -> (is_duplicate, primary_email) mark mapping.
    marks: dict[str, tuple[bool, str]] = {}
    for members in groups.values():
        ordered = sorted(members, key=_dedup_sort_key)
        primary = ordered[0]
        primary_email = str(primary.get("email") or "")
        for i, row in enumerate(ordered):
            email = str(row.get("email") or "")
            if i == 0:
                marks[email] = (False, "")
            else:
                marks[email] = (True, primary_email)

    # 3) Backfill (preserving input order); empty employee number / no match -> independent subscription.
    out: list[dict] = []
    for row in users:
        email = str(row.get("email") or "")
        is_dup, primary_email = marks.get(email, (False, ""))
        out.append({
            **row,
            "is_duplicate_subscription": is_dup,
            "primary_email": primary_email,
        })
    return out


@app.get("/api/report")
def report(
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    """Monthly report: summary KPIs + up/down tier suggestions. Shares the common decision algorithm with the email report."""
    date_from, date_to = _resolve_range(period, date_from, date_to)
    srow = layer.one(q.summary(date_from, date_to))
    total = int(srow.get("total_users") or 0)
    active = int(srow.get("active_users") or 0)
    # Registered count metric: roster ∪ users with usage.
    registered = _registered_total(settings, _usage_emails(q, layer, date_from, date_to)) or total
    kpis = {
        "registered_users": registered,
        "active_users": active,
        "activation_rate": round(active / registered, 4) if registered else 0.0,
        "total_credits": srow.get("total_credits") or 0,
        "overage_users": int(srow.get("overage_users") or 0),
        "est_cost": srow.get("est_cost") or 0,
        "daily_active_avg": srow.get("daily_active_avg") or 0,
        "daily_active_peak": int(srow.get("daily_active_peak") or 0),
        "tiers": {
            "Pro": int(srow.get("tier_pro") or 0),
            "ProPlus": int(srow.get("tier_proplus") or 0),
            "ProMax": int(srow.get("tier_promax") or 0),
            "Power": int(srow.get("tier_power") or 0),
        },
    }
    users = layer.run(q.report_users(date_from, date_to))
    upgrades, downgrades = _recommendations(users)       # kept (compatibility + does not break test_report; takes the raw users)
    # Join two roster columns (by lowercased email); usage users not on the roster -> both columns "" (independent subscription).
    roster_by_email = {u["email"]: u for u in _roster(settings)}
    enriched = []
    for u in users:
        email = (u.get("email") or "").strip().lower()
        person = roster_by_email.get(email) or {}
        enriched.append({
            **u,
            "employee_number": person.get("employee_number", "") or "",
            "created_date": person.get("created_date", "") or "",
        })
    # First add the one-tier up/down suggestions, then mark duplicates (both are row-level pass-throughs, non-interfering).
    users_annotated = annotate_duplicate_subscriptions(_annotate_tier_steps(enriched))
    return {
        "range": {"from": date_from, "to": date_to},
        "kpis": kpis,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "users": users_annotated,
        "est_cost_note": _EST_COST_NOTE,
    }


@app.get("/api/trend")
def trend(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    rows = layer.run(q.trend(date_from, date_to))
    return {"series": [
        {"date": r["date"], "credits": r.get("credits") or 0,
         "active_users": int(r.get("active_users") or 0)}
        for r in rows
    ]}


@app.get("/api/models")
def models(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    email: Optional[str] = Query(None),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    # Optional single-user drill-down: the email parameter is a data filter, not an authorization control.
    idf = {"email": email} if email else None
    rows = layer.run(q.models(date_from, date_to, identity_filter=idf))
    return {"models": [
        {"model": r["model"], "messages": int(r.get("messages") or 0)} for r in rows
    ]}


@app.get("/api/model-concentration")
def model_concentration(
    models: str = Query(..., description="comma-separated model names"),
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    bins: int = Query(10, ge=2, le=50),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    """Model concentration analysis (module A, metric per the read-only research report).

    Metric = message-count share (not cost); denominator = employees with messages in range (zero-usage users excluded).
    A single detail query gets each employee's ratio, then Python computes the summary + equal-width histogram buckets and returns the ratios
    so the frontend can recompute locally when the threshold slider changes (no re-request needed).
    """
    date_from, date_to = _resolve_range(period, date_from, date_to)
    # 1) Model allowlist validation (using model_list's distinct model results)
    valid = {r["model"] for r in layer.run(q.model_list())}
    requested = [m.strip() for m in models.split(",") if m.strip()]
    chosen = [m for m in requested if m in valid]
    if not chosen:
        return JSONResponse(status_code=400, content={
            "detail": "no valid model selected", "valid_models": sorted(valid)})
    # 2) Single detail query: each employee's ratio (+ target/total message counts)
    rows = layer.run(q.model_concentration_detail(
        chosen, date_from, date_to))
    # Name/department join: reuses the 5-minute-cached roster (DEPT_TABLE unset/scan failed → empty table → everything falls back).
    roster_by_email = {u["email"]: u for u in _roster(settings)}
    members = []
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        person = roster_by_email.get(email) or {}
        name = (person.get("display_name") or "").strip() or email.split("@")[0]
        dept = (person.get("department") or "").strip() or "Ungrouped"
        members.append({
            "email": email,
            "display_name": name,
            "department": dept,
            # Query-layer keys target_msgs/total_msgs → external keys target_messages/total_messages (do not pass through)
            "target_messages": int(r.get("target_msgs") or 0),
            "total_messages": int(r.get("total_msgs") or 0),
            "ratio": float(r.get("ratio") or 0.0),
        })
    ratios = [m["ratio"] for m in members]
    total = len(ratios)
    concentrated = sum(1 for x in ratios if x >= threshold)
    # 3) Equal-width histogram buckets [0,1]; x==1.0 falls into the last bucket
    hist = [0] * bins
    for x in ratios:
        idx = min(bins - 1, int(x * bins))
        hist[idx] += 1
    buckets = [{"from": round(i / bins, 4), "to": round((i + 1) / bins, 4),
                "count": hist[i]} for i in range(bins)]
    return {
        "range": {"from": date_from, "to": date_to},
        "models": chosen,
        "threshold": threshold,
        "total_users": total,
        "concentrated_users": concentrated,
        "concentration_rate": round(concentrated / total, 4) if total else 0.0,
        "histogram": buckets,
        "ratios": [round(x, 4) for x in ratios],
        "members": members,
    }


@app.get("/api/departments")
def departments(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    rows = layer.run(q.departments(date_from, date_to))
    return {"departments": [
        {"department": r["department"], "users": int(r.get("users") or 0),
         "total_credits": r.get("total_credits") or 0,
         "avg_credits": r.get("avg_credits") or 0}
        for r in rows
    ]}


@app.get("/api/top-users")
def top_users(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    n = limit or settings.top_n
    rows = layer.run(q.top_users(date_from, date_to, limit=n))
    return {"users": [
        {"email": r["email"], "display_name": r.get("display_name") or "",
         "department": r.get("department") or "Ungrouped",
         "subscription_tier": r.get("subscription_tier") or "",
         "credits_used": r.get("credits_used") or 0}
        for r in rows
    ]}


@app.get("/api/users")
def users(
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("credits_used"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    include_all: bool = Query(True),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    # include_all=True (default): based on the full roster, zero-usage users filled with 0, sorted and paginated in memory.
    if include_all:
        merged = _users_merged(settings, q, layer, date_from, date_to,
                               department, search)
        total = len(merged)
        ordered = _sort_users(merged, sort, direction)
        start = (page - 1) * page_size
        items = ordered[start:start + page_size]
        return {
            "total": total, "page": page, "page_size": page_size,
            "sort": sort, "direction": direction, "items": items,
        }

    # include_all=False: old behavior, only users with usage in range, paginated by Athena.
    total = int(layer.one(q.users_count(
        date_from, date_to, department=department,
        search=search)).get("total") or 0)
    rows = layer.run(q.users(
        date_from, date_to, department=department, sort=sort, direction=direction,
        page=page, page_size=page_size, search=search))
    items = [{
        "email": r["email"],
        "display_name": r.get("display_name") or "",
        "department": r.get("department") or "Ungrouped",
        "subscription_tier": r.get("subscription_tier") or "",
        "credits_used": r.get("credits_used") or 0,
        "usage_limit": r.get("usage_limit") or 0,
        "balance": r.get("balance") or 0,
        "usage_rate": r.get("usage_rate") or 0,
        "is_overage": bool(r.get("is_overage")),
        "est_cost": r.get("est_cost") or 0,
        "total_messages": int(r.get("total_messages") or 0),
        "active_days": int(r.get("active_days") or 0),
        "status": _status(
            r.get("usage_rate") or 0, bool(r.get("is_overage")),
            has_usage=(float(r.get("credits_used") or 0) > 0
                       or int(r.get("total_messages") or 0) > 0)),
    } for r in rows]
    return {
        "total": total, "page": page, "page_size": page_size,
        "sort": sort, "direction": direction, "items": items,
    }


def _csv_safe(v):
    """CSV formula-injection guard: prepend a single quote to strings starting with = + - @ \\t \\r, so Excel no longer evaluates them as formulas."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/api/users/export")
def users_export(
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("credits_used"),
    direction: str = Query("desc"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
):
    """Export all users in the current department tab + search scope as CSV (including zero usage, unpaginated)."""
    import csv
    import io
    import urllib.parse
    from datetime import datetime

    from fastapi.responses import Response

    merged = _users_merged(settings, q, layer, date_from, date_to,
                           department, search)
    ordered = _sort_users(merged, sort, direction)

    status_cn = {"overage": "Overage", "near_limit": "Near limit",
                 "normal": "Normal", "unused": "Unused"}
    headers = ["Email", "Name", "Department", "Tier", "Credits", "Limit", "Balance",
               "Usage rate", "Overage", "Estimated cost (USD)", "Messages", "Active days", "Status"]
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM, ensures Excel detects the encoding correctly
    w = csv.writer(buf)
    w.writerow(headers)
    for r in ordered:
        # String cells go through _csv_safe (name/department come from Identity Center, guarding against CSV formula injection); numeric columns as-is.
        w.writerow([
            _csv_safe(r.get("email")),
            _csv_safe(r.get("display_name")),
            _csv_safe(r.get("department")),
            _csv_safe(r.get("subscription_tier")) or "-",
            r.get("credits_used") or 0,
            r.get("usage_limit") or 0,
            r.get("balance") or 0,
            r.get("usage_rate") or 0,
            "Yes" if r.get("is_overage") else "No",
            r.get("est_cost") or 0,
            int(r.get("total_messages") or 0),
            int(r.get("active_days") or 0),
            _csv_safe(status_cn.get(r.get("status") or "", r.get("status") or "")),
        ])

    dept_part = (department or "all").replace("/", "_").replace("\\", "_")
    # Time range: use {from}_{to} (hyphens removed) when both dates are present, otherwise fall back to the export date.
    def _compact(d: str) -> str:
        return (d or "").replace("-", "")
    if date_from and date_to:
        range_part = f"{_compact(date_from)}-{_compact(date_to)}"
    elif date_from:
        range_part = f"{_compact(date_from)}-todate"
    else:
        range_part = f"asof{datetime.now().strftime('%Y%m%d')}"
    fname = f"kiro-users-{dept_part}-{range_part}.csv"
    quoted = urllib.parse.quote(fname)
    # HTTP headers must be latin-1: filename= uses an ASCII fallback name (no non-ASCII department, but keeps the time range),
    # while the real name (which may contain a non-ASCII department) goes through filename*.
    ascii_range = range_part if range_part.isascii() else datetime.now().strftime('%Y%m%d')
    ascii_fallback = f"kiro-users-{ascii_range}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
            ),
        },
    )


@app.get("/api/filters")
def filters(
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    # The three SELECT DISTINCTs are independent; run them in parallel to avoid summing toward the API Gateway 29s timeout.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_periods = ex.submit(layer.run, q.periods())
        f_depts = ex.submit(layer.run, q.department_list())
        f_models = ex.submit(layer.run, q.model_list())
        periods = [r["period"] for r in f_periods.result()]
        depts = [r["department"] for r in f_depts.result()]
        mdls = [r["model"] for r in f_models.result()]
    return {"periods": periods, "departments": depts, "models": mdls}


@app.post("/api/chat")
def chat(
    request: Request,
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """NL Q&A: forward the user's question to AgentCore Runtime (using the caller's Cognito bearer token
    for inbound auth). The frontend only talks to this API (behind the API Gateway JWT authorizer), avoiding
    the browser CORS problem of connecting directly to the AgentCore endpoint, and the runtime ARN is not exposed to the browser.
    """
    import json as _json
    import urllib.parse
    import urllib.request
    import uuid

    arn = settings.agent_runtime_arn
    if not arn:
        return JSONResponse(status_code=503, content={"detail": "NL Q&A is not configured (AGENT_RUNTIME_ARN is empty)"})

    prompt = (body or {}).get("prompt") or ""
    if not prompt.strip():
        return JSONResponse(status_code=400, content={"detail": "prompt must not be empty"})

    # Get the bearer token from the inbound request (passed through to AgentCore for inbound JWT auth).
    # Note: the API Gateway JWT authorizer validates the id token (aud), while AgentCore's inbound
    # customJWT validates the access token (client_id). The frontend uses the id token in the header (to pass API GW),
    # and passes the access token in body.access_token (for AgentCore); if missing, fall back to the header token.
    header_auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    body_access = (body or {}).get("access_token") or ""
    agent_token = body_access or (header_auth[7:] if header_auth.lower().startswith("bearer ") else "")
    if not agent_token:
        return JSONResponse(status_code=401, content={"detail": "Missing a token usable for Agent authentication"})

    enc = urllib.parse.quote(arn, safe="")
    url = (f"https://bedrock-agentcore.{settings.region}.amazonaws.com"
           f"/runtimes/{enc}/invocations?qualifier=DEFAULT")
    session_id = (body.get("session_id") or uuid.uuid4().hex)
    # AgentCore requires a session id of length >= 33
    if len(session_id) < 33:
        session_id = (session_id + uuid.uuid4().hex)[:40]

    req = urllib.request.Request(
        url, data=_json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {agent_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = _json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"detail": f"Agent call failed: {e}"})

    return {"reply": data.get("reply") or data.get("output") or "", "session_id": session_id}


# --- helpers ---------------------------------------------------------------
def _resolve_range(period: Optional[str], date_from: Optional[str],
                   date_to: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """period=YYYY-MM expands to the month's first/last day (simple metric: 01 ~ 31, safe for Athena string comparison)."""
    if period and not (date_from or date_to):
        return f"{period}-01", f"{period}-31"
    return date_from, date_to


def _status(usage_rate: float, is_overage: bool = False, has_usage: bool = False) -> str:
    """Return a language-independent status key, localized by the frontend.

    `has_usage`: whether the user has actually used anything (credits_used>0 or total_messages>0). Used to distinguish
    "tiny usage" from "completely unused": after round(,4), tiny usage (e.g. a Pro user consuming 0.04 credit → rate≈0.00004)
    gets zeroed, so looking at usage_rate alone would misclassify it as "unused".
    As long as there is real usage (even 1 message), it should be "normal", not "unused".
    """
    if is_overage or usage_rate >= 1.0:
        return "overage"
    if usage_rate >= 0.8:
        return "near_limit"
    if usage_rate > 0 or has_usage:
        return "normal"
    return "unused"


_REGISTERED_CACHE: dict[str, tuple[float, int]] = {}
_ROSTER_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _roster(settings: Settings) -> list[dict]:
    """Full registration roster (full scan of the user_department table), with a 5-minute in-memory cache.

    Returns [{email, display_name, department}]. Returns an empty list if the table is unconfigured/scan fails.
    Note: the roster comes from identity-sync (subscription seats), with email/department/name, but **no** tier/limit
    (tier is only in the usage CSV).
    """
    import time as _time
    table = settings.dept_table
    if not table:
        return []
    hit = _ROSTER_CACHE.get(table)
    now = _time.time()
    if hit and (now - hit[0]) < 300:
        return hit[1]
    out: list[dict] = []
    try:
        import boto3
        ddb = boto3.client("dynamodb", region_name=settings.region)
        kw = {"TableName": table,
              "ProjectionExpression": "user_key, display_name, department, employee_number, created_date"}
        while True:
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                out.append({
                    "email": (it.get("user_key", {}).get("S") or "").strip().lower(),
                    "display_name": it.get("display_name", {}).get("S") or "",
                    "department": it.get("department", {}).get("S") or "Ungrouped",
                    # Two columns written by identity-sync; older entries without the columns read "" -> naturally treated as independent subscriptions, no migration needed.
                    "employee_number": it.get("employee_number", {}).get("S") or "",
                    "created_date": it.get("created_date", {}).get("S") or "",
                })
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kw["ExclusiveStartKey"] = lek
    except Exception:  # noqa: BLE001
        return []
    out = [u for u in out if u["email"]]
    _ROSTER_CACHE[table] = (now, out)
    return out


def _users_merged(settings, q, layer, date_from, date_to, department, search) -> list[dict]:
    """Based on the roster, LEFT JOIN the range's usage, filling zero usage with 0.

    - Roster: _roster() (full DynamoDB);
    - Usage: Athena all_users_usage() (aggregated by email);
    - The department filter uses the **roster department** (tab semantics); search is a fuzzy name/email match.
    - Zero-usage users: credits=0 etc., tier/limit unknown (data-source limitation).
    Returns a list of frontend rows (unsorted/unpaginated).
    """
    roster = _roster(settings)
    usage_rows = layer.run(q.all_users_usage(date_from, date_to))
    usage_by_email = {(r.get("email") or "").strip().lower(): r for r in usage_rows}

    # When the roster is empty (DEPT_TABLE unset), degrade to usage-only users (old behavior).
    if not roster:
        base = [{"email": e, "display_name": u.get("display_name") or "",
                 "department": u.get("department") or "Ungrouped"}
                for e, u in usage_by_email.items()]
    else:
        # Roster ∪ users with usage: some users have records in the Kiro usage data (Athena)
        # but are not in the Identity Center subscription-app assignment roster (unassigned / seat opened directly in Kiro / no such IdC user).
        # These users really consumed credits and must be included in the detail/CSV, otherwise the tier counts would not match summary (Athena metric).
        # Off-roster usage users get department "Ungrouped" (identity-sync has no department for them); tier/limit use Athena's real values.
        base = list(roster)
        roster_emails = {p["email"] for p in roster}
        for e, u in usage_by_email.items():
            if e not in roster_emails:
                base.append({
                    "email": e,
                    "display_name": u.get("display_name") or e.split("@")[0],
                    "department": "Ungrouped",
                })

    s = (search or "").strip().lower()
    out: list[dict] = []
    for person in base:
        email = person["email"]
        dept = person.get("department") or "Ungrouped"
        name = person.get("display_name") or ""
        # Department tab filter (using the roster department)
        if department and dept != department:
            continue
        if s and (s not in email.lower()) and (s not in name.lower()):
            continue
        u = usage_by_email.get(email)
        if u:
            rate = float(u.get("usage_rate") or 0)
            is_over = bool(u.get("is_overage"))
            out.append({
                "email": email,
                "display_name": name or (u.get("display_name") or ""),
                "department": dept,
                "subscription_tier": u.get("subscription_tier") or "",
                "credits_used": u.get("credits_used") or 0,
                "usage_limit": u.get("usage_limit") or 0,
                "balance": u.get("balance") or 0,
                "usage_rate": rate,
                "is_overage": is_over,
                "est_cost": u.get("est_cost") or 0,
                "total_messages": int(u.get("total_messages") or 0),
                "active_days": int(u.get("active_days") or 0),
                "status": _status(
                    rate, is_over,
                    has_usage=(float(u.get("credits_used") or 0) > 0
                               or int(u.get("total_messages") or 0) > 0)),
            })
        else:
            # Zero-usage registered user: all usage 0, tier/limit unknown
            out.append({
                "email": email, "display_name": name, "department": dept,
                "subscription_tier": "", "credits_used": 0, "usage_limit": 0,
                "balance": 0, "usage_rate": 0, "is_overage": False, "est_cost": 0,
                "total_messages": 0, "active_days": 0, "status": "unused",
            })
    return out


def _sort_users(rows: list[dict], sort: str, direction: str) -> list[dict]:
    """Sort in memory by the given column (allowlisted columns; an invalid column falls back to credits_used desc)."""
    allowed = {"credits_used", "usage_limit", "balance", "usage_rate",
               "total_messages", "active_days", "est_cost", "email"}
    col = sort if sort in allowed else "credits_used"
    rev = (direction or "desc").lower() != "asc"
    return sorted(rows, key=lambda r: (r.get(col) if r.get(col) is not None else ("" if col == "email" else 0)), reverse=rev)


def _usage_emails(q, layer, date_from, date_to) -> set:
    """The set (lowercased) of emails with Athena usage records in this range. Used for the registered-count union metric.

    Reuses all_users_usage (QueryLayer has a TTL cache, so it hits the same result as _users_merged).
    """
    try:
        rows = layer.run(q.all_users_usage(date_from, date_to))
        return {(r.get("email") or "").strip().lower() for r in rows if r.get("email")}
    except Exception:  # noqa: BLE001
        return set()


def _registered_total(settings: Settings, usage_emails: Optional[set] = None) -> int:
    """Total registered users = deduplicated count of the roster (DynamoDB) ∪ users with usage (Athena).

    Used as the activation-rate denominator. Previously this only counted department-table entries; now it unions in users
    with Athena usage but not on the roster (consistent with the _users_merged union metric, so registered count / tier distribution / detail table / CSV are unified across the app).
    `usage_emails`: the set (lowercased) of emails with Athena usage this range; None degrades to roster-only counting.
    The roster scan result has a 5-minute cache (it caches the roster email set, and the union is computed each time from the passed usage_emails).
    Returns 0 when DEPT_TABLE is unconfigured or the scan fails (the caller falls back to the range's total user count).
    """
    import time as _time

    table = settings.dept_table
    if not table:
        return 0  # roster table unconfigured: fall back (caller uses `or total`)
    hit = _REGISTERED_CACHE.get(table)
    now = _time.time()
    roster_emails: Optional[set] = None
    if hit and (now - hit[0]) < 300:
        roster_emails = hit[1]
    if roster_emails is None:
        try:
            import boto3
            ddb = boto3.client("dynamodb", region_name=settings.region)
            roster_emails = set()
            kw = {"TableName": table, "ProjectionExpression": "user_key"}
            while True:
                resp = ddb.scan(**kw)
                for it in resp.get("Items", []):
                    e = (it.get("user_key", {}).get("S") or "").strip().lower()
                    if e:
                        roster_emails.add(e)
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                kw["ExclusiveStartKey"] = lek
        except Exception:  # noqa: BLE001
            return 0  # roster scan failed: fall back (caller uses `or total`)
        _REGISTERED_CACHE[table] = (now, roster_emails)
    return len(roster_emails | (usage_emails or set()))

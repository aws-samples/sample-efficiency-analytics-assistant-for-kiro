"""Monthly usage report Lambda: sends the current month's usage report + tier suggestions 7 days before month-end.

Triggers:
- EventBridge daily; the handler checks "is today the last day of the month - 6" and only sends on a hit
  (that day to month-end is exactly a 7-day window). Use {"force": true} to bypass the date check for manual test triggering.

Report content (month's 1st → today, in progress):
- Summary: registered/active, activation rate, total credits, estimated cost, overage count, tier distribution;
- Tier suggestions (plan match): match this month's actual usage to the cheapest covering tier,
  higher than current → upgrade, lower → downgrade (may span multiple tiers), equal → keep.

Delivery: Amazon SES (HTML email + CSV attachment). Recipients = REPORT_RECIPIENTS (falls back to SES_SENDER).

Environment variables:
  GLUE_DB, ATHENA_WORKGROUP, DEPLOY_REGION/AWS_REGION, DEPT_TABLE, CREDIT_UNIT_PRICE
  SES_SENDER            an SES-verified sender email (required to send)
  REPORT_RECIPIENTS     comma-separated recipients; empty falls back to SES_SENDER
"""
from __future__ import annotations

import csv
import html
import io
import os
from datetime import date, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3

try:
    from shared.athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # flat packaging
    from athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

GLUE_DB = os.environ.get("GLUE_DB", "kiro_usage")
WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
REGION = os.environ.get("AWS_REGION") or os.environ.get("DEPLOY_REGION", "us-east-1")
CREDIT_UNIT_PRICE = float(os.environ.get("CREDIT_UNIT_PRICE", "0.04"))
SES_SENDER = os.environ.get("SES_SENDER", "")
REPORT_RECIPIENTS = os.environ.get("REPORT_RECIPIENTS", "")

# Tier ladder/suggestions — shared module (uses the same algorithm as the report-page API)
try:
    from shared.recommendations import TIER_ZH, tier_label, recommendations as _recommendations
except ImportError:  # flat packaging (inside the container shared is a sibling of this module)
    from recommendations import TIER_ZH, tier_label, recommendations as _recommendations  # type: ignore


def _last_day_of_month(d: date) -> date:
    first_next = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def _is_send_day(today: date) -> bool:
    """Last day of the month - 6: from this day to month-end is exactly 7 days."""
    return today == _last_day_of_month(today) - timedelta(days=6)


def _fmt(n) -> str:
    try:
        return f"{float(n):,.1f}"
    except (TypeError, ValueError):
        return str(n)


def _pct(rate) -> str:
    return f"{float(rate) * 100:.1f}%"


def _e(v) -> str:
    """HTML-escape a value for the report email body. Display names, emails and departments come from
    IAM Identity Center and are untrusted, so every interpolated value goes through this helper."""
    return html.escape("" if v is None else str(v), quote=True)


def _build_html(period_label: str, date_range: tuple, kpis: dict, tiers: dict,
                upgrades: list[dict], downgrades: list[dict]) -> str:
    df, dt = date_range

    def _rows(items: list[dict]) -> str:
        if not items:
            return '<tr><td colspan="6" style="padding:8px;color:#888;">None</td></tr>'
        out = []
        for u in items:
            out.append(
                "<tr>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{_e(u.get('display_name') or u.get('email',''))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{_e(u.get('department') or '-')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{_e(tier_label(u.get('subscription_tier',''), True))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{_e(_fmt(u.get('credits_used')))} / {_e(_fmt(u.get('usage_limit')))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{_e(_pct(u.get('usage_rate')))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;font-weight:600;'>{_e(tier_label(u.get('suggested_tier',''), True))}</td>"
                "</tr>"
            )
        return "".join(out)

    th = "style='padding:6px 10px;text-align:left;background:#f5f7fa;border-bottom:2px solid #ddd;'"
    tier_line = " | ".join(f"{_e(tier_label(k, True))}: <b>{_e(v)}</b>" for k, v in tiers.items())
    return f"""
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;">
  <h2 style="margin:0 0 4px;">Kiro usage report · {_e(period_label)}</h2>
  <p style="color:#6b7280;margin:0 0 16px;">Period: {_e(df)} ~ {_e(dt)} (month to date)</p>

  <h3 style="margin:16px 0 8px;">Summary</h3>
  <ul style="line-height:1.8;margin:0;">
    <li>Registered / Active: <b>{_e(kpis.get('registered_users'))}</b> / <b>{_e(kpis.get('active_users'))}</b> (activation rate {_e(_pct(kpis.get('activation_rate') or 0))})</li>
    <li>Total credits: <b>{_e(_fmt(kpis.get('total_credits')))}</b> | Estimated cost: <b>${_e(_fmt(kpis.get('est_cost')))}</b> ($0.04/credit)</li>
    <li>Overage users: <b>{_e(kpis.get('overage_users'))}</b> | Daily active (avg/peak): {_e(_fmt(kpis.get('daily_active_avg')))} / {_e(kpis.get('daily_active_peak'))}</li>
    <li>Tier distribution: {tier_line}</li>
  </ul>

  <h3 style="margin:20px 0 8px;color:#b45309;">Suggested upgrades (usage exceeds the current tier's limit, {len(upgrades)} users)</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <tr><th {th}>Name</th><th {th}>Department</th><th {th}>Current tier</th><th {th}>Usage/Limit</th><th {th}>Usage rate</th><th {th}>Suggested tier</th></tr>
    {_rows(upgrades)}
  </table>

  <h3 style="margin:20px 0 8px;color:#2563eb;">Suggested downgrades (a lower tier covers the usage, {len(downgrades)} users)</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <tr><th {th}>Name</th><th {th}>Department</th><th {th}>Current tier</th><th {th}>Usage/Limit</th><th {th}>Usage rate</th><th {th}>Suggested tier</th></tr>
    {_rows(downgrades)}
  </table>

  <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
    Note: based on this month's actual usage, matched to the cheapest tier that covers it (Pro 1000/$20 · Pro+ 2000/$40 ·
    Pro Max 5000/$100 · Power 10000/$200): higher than the current tier suggests an upgrade, lower suggests a downgrade (may span multiple tiers),
    and equal means keep. Usage over the Power limit still takes Power. This report is generated automatically.
  </p>
</body></html>
"""


def _csv_safe(v):
    """CSV formula-injection guard: prepend a single quote to strings starting with = + - @ \\t \\r, so Excel no longer evaluates them as formulas."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _build_csv(upgrades: list[dict], downgrades: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Suggestion type", "Name", "Email", "Department", "Current tier", "Cumulative credits", "Plan limit", "Usage rate", "Suggested tier"])
    for kind, items in [("Upgrade", upgrades), ("Downgrade", downgrades)]:
        for u in items:
            # String cells go through _csv_safe (name/department come from Identity Center); numeric columns as-is.
            w.writerow([
                kind, _csv_safe(u.get("display_name")), _csv_safe(u.get("email")),
                _csv_safe(u.get("department")), _csv_safe(tier_label(u.get("subscription_tier") or "")),
                u.get("credits_used"), u.get("usage_limit"),
                _pct(u.get("usage_rate")), _csv_safe(tier_label(u.get("suggested_tier") or "")),
            ])
    return buf.getvalue()


def _ses_verified(ses, emails: list[str]) -> set[str]:
    """Return the set (lowercased) of emails already verified (Success) in SES.

    In sandbox mode, sending to an unverified address rejects the whole email, so unverified recipients are filtered before sending.
    If the sandbox is lifted (production access), any address can be sent to, but filtering is harmless (verified addresses are kept).
    """
    if not emails:
        return set()
    try:
        attrs = ses.get_identity_verification_attributes(
            Identities=list({e for e in emails}))["VerificationAttributes"]
        return {k.lower() for k, v in attrs.items()
                if (v or {}).get("VerificationStatus") == "Success"}
    except Exception:  # noqa: BLE001
        return set()


def _send_email(subject: str, html: str, csv_text: str, csv_name: str) -> dict:
    # Recipients: the configured REPORT_RECIPIENTS (comma-separated, deduplicated); fall back to SES_SENDER.
    explicit = [r.strip() for r in (REPORT_RECIPIENTS or "").split(",") if r.strip()]
    seen, recipients = set(), []
    for e in explicit:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(e)
    if not recipients and SES_SENDER:
        recipients = [SES_SENDER]
    if not SES_SENDER or not recipients:
        return {"sent": False, "reason": "SES_SENDER or recipients empty", "recipients": recipients}
    ses = boto3.client("ses", region_name=REGION)
    # Filter: in sandbox, send only to verified recipients, so one unverified address does not drop the whole email.
    verified = _ses_verified(ses, recipients + [SES_SENDER])
    eligible = [r for r in recipients if r.lower() in verified]
    skipped = [r for r in recipients if r.lower() not in verified]
    if not eligible:
        return {"sent": False, "reason": "no verified recipients (SES sandbox)",
                "recipients": recipients, "skipped_unverified": skipped}
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = SES_SENDER
    msg["To"] = ", ".join(eligible)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    part = MIMEApplication(csv_text.encode("utf-8-sig"))
    part.add_header("Content-Disposition", "attachment", filename=csv_name)
    part.add_header("Content-Type", "text/csv; charset=utf-8")
    msg.attach(part)
    resp = ses.send_raw_email(Source=SES_SENDER, Destinations=eligible,
                              RawMessage={"Data": msg.as_string()})
    return {"sent": True, "message_id": resp.get("MessageId"),
            "recipients": eligible, "skipped_unverified": skipped}


def handler(event, context):
    event = event or {}
    today = date.today()
    if not event.get("force") and not _is_send_day(today):
        return {"status": "skip", "reason": "not send day", "today": str(today),
                "send_day": str(_last_day_of_month(today) - timedelta(days=6))}

    date_from = today.replace(day=1).isoformat()
    date_to = today.isoformat()
    period_label = today.strftime("%Y-%m")

    runner = make_athena_runner(database=GLUE_DB, workgroup=WORKGROUP, region=REGION)
    layer = QueryLayer(runner, cache_ttl=0)
    q = Queries(db=GLUE_DB)

    summary = layer.one(q.summary(date_from, date_to))
    users = layer.run(q.report_users(date_from, date_to))

    # Total registered (activation-rate denominator): scan the department table
    registered = _registered_total()
    total_users = int(summary.get("total_users") or 0)
    active = int(summary.get("active_users") or 0)
    denom = registered or total_users
    kpis = {
        "registered_users": denom,
        "active_users": active,
        "activation_rate": round(active / denom, 4) if denom else 0.0,
        "total_credits": summary.get("total_credits") or 0,
        "est_cost": summary.get("est_cost") or 0,
        "overage_users": int(summary.get("overage_users") or 0),
        "daily_active_avg": summary.get("daily_active_avg") or 0,
        "daily_active_peak": int(summary.get("daily_active_peak") or 0),
    }
    tiers = {
        "Pro": int(summary.get("tier_pro") or 0),
        "ProPlus": int(summary.get("tier_proplus") or 0),
        "ProMax": int(summary.get("tier_promax") or 0),
        "Power": int(summary.get("tier_power") or 0),
    }

    upgrades, downgrades = _recommendations(users)
    html = _build_html(period_label, (date_from, date_to), kpis, tiers, upgrades, downgrades)
    csv_text = _build_csv(upgrades, downgrades)
    subject = f"[Kiro usage report] {period_label} · {len(upgrades)} upgrades / {len(downgrades)} downgrades"
    try:
        send_res = _send_email(subject, html, csv_text, f"kiro-adjust-{period_label}.csv")
    except Exception as e:  # noqa: BLE001 — a send failure should not make the whole task error out
        send_res = {"sent": False, "error": str(e)}

    return {
        "status": "ok", "period": period_label, "range": [date_from, date_to],
        "upgrade_count": len(upgrades), "downgrade_count": len(downgrades),
        "email": send_res,
    }


def _registered_total() -> int:
    table = os.environ.get("DEPT_TABLE", "")
    if not table:
        return 0
    try:
        ddb = boto3.client("dynamodb", region_name=REGION)
        count, kw = 0, {"TableName": table, "Select": "COUNT"}
        while True:
            resp = ddb.scan(**kw)
            count += int(resp.get("Count") or 0)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kw["ExclusiveStartKey"] = lek
        return count
    except Exception:  # noqa: BLE001
        return 0

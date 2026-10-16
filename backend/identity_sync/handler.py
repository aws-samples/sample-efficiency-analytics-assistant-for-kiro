"""identity-sync Lambda handler: Identity Center -> DynamoDB user_department.

Modes (via the MOCK_MODE environment variable):
  MOCK_MODE=true  load the mock mapping from s3://RAW_BUCKET/IDENTITY_JSON_KEY
  otherwise       resolve the Kiro subscription-user -> department mapping from Identity Center

[Source of subscription users] Kiro/Q Developer subscription users = the principals assigned to the Q Developer profile
application (the SSO application). An assignment can be:
  - USER: a user assigned directly to the profile — such users do not go through any group;
  - GROUP: a group assigned to the profile (e.g. the admin group) — all members of the group are subscription users.
Walking only groups would miss the "directly assigned users". So we prefer SUBSCRIPTION_APP_ARN to read the application
assignments, including USERs directly and expanding GROUP members. When the ARN is not configured, fall back to "walk all groups" (the old behavior).

Department metric: the DisplayName of the user's group is the department; direct assignments (no group) go to "Ungrouped".

DynamoDB item:
  user_key(PK)=email(lowercase), department, groups(list), display_name, synced_at
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ParamValidationError

log = logging.getLogger("identity_sync")

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")

DEPT_TABLE = os.environ["DEPT_TABLE"]
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
IDENTITY_JSON_KEY = os.environ.get("IDENTITY_JSON_KEY", "identity_groups.json")
IDENTITY_STORE_ID = os.environ.get("IDENTITY_STORE_ID", "")
# Identity Store / SSO-admin are regional services and must be called in the instance's region (which may differ from the Lambda's).
IDENTITY_STORE_REGION = os.environ.get("IDENTITY_STORE_REGION", "") or os.environ.get("AWS_REGION", "us-west-2")
# SSO instance ARN + Q Developer profile application ARN (the authoritative source of subscription users).
SSO_INSTANCE_ARN = os.environ.get("SSO_INSTANCE_ARN", "")
SUBSCRIPTION_APP_ARN = os.environ.get("SUBSCRIPTION_APP_ARN", "")
# The Kiro-exported user->group mapping CSV (lookup/user_groups.csv), columns: user_email,user_id,group_name,group_id.
# When configured, it is preferred as the department source (most authoritative, simplest, no SSO API needed).
GROUP_CSV_BUCKET = os.environ.get("GROUP_CSV_BUCKET", "")
GROUP_CSV_KEY = os.environ.get("GROUP_CSV_KEY", "")
UNGROUPED = "Ungrouped"

# Identity Center enterprise extension schema (includes employeeNumber). describe_user returns the extension only when this parameter is passed.
_ENTERPRISE_EXT = "aws:identitystore:enterprise"
# Process-level short-circuit: once we confirm the runtime boto3 does not accept the Extensions parameter, stop retrying with the extension,
# reducing the fallback cost from O(number of users) to O(1). Production boto3 supports it, so this switch is normally False.
_ext_unsupported = False

# Add-on groups (not departments): stacked onto a department group they only affect the billing tier, not department membership.
ADDON_GROUP_PREFIXES = ("kiro-power", "kiro-promax", "kiro-proplus", "kiro-pro")


def _is_addon_group(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in ADDON_GROUP_PREFIXES)


def _extract_zh_dept(description: str) -> str:
    """Extract a CJK department-name prefix from an Identity Center group's Description.

    A real Description looks like '<CJK department name>Kiro Pro subscription users - teamalias',
    with the CJK department name at the start. Rule: take the leading run of CJK characters
    (including common CJK punctuation and ·-) up to the first ASCII letter/digit. If no CJK name
    can be extracted, return an empty string (the caller falls back to the English group name).
    """
    import re
    s = (description or "").strip()
    if not s:
        return ""
    m = re.match(r"^[\u4e00-\u9fff（）()·\-—、／/&＆\s]+", s)
    if not m:
        return ""
    zh = m.group(0).strip(" -—/·、")
    # Must contain at least one CJK character to be valid
    return zh if re.search(r"[\u4e00-\u9fff]", zh) else ""


def _load_mock() -> dict[str, dict]:
    body = s3.get_object(Bucket=RAW_BUCKET, Key=IDENTITY_JSON_KEY)["Body"].read()
    return json.loads(body)


def _iso_created_at(created_at) -> str:
    """describe_user top-level CreatedAt(datetime) -> ISO8601 string.

    None -> ""; datetime -> .isoformat() (boto3 returns a timezone-aware datetime);
    other/error -> str(created_at) or "" (narrow try, does not swallow unrelated exceptions).
    """
    if created_at is None:
        return ""
    try:
        return created_at.isoformat()
    except AttributeError:
        return str(created_at) if created_at else ""


def _employee_number_from(extensions) -> str:
    """Get employeeNumber from describe_user's Extensions, tolerating None/list/dict shapes.

    - None/falsy -> "" (confirmed behavior: when a user has no employeeNumber, Extensions returns None).
    - dict -> recursively find a key (case-insensitive) ending in 'employeenumber' (covering namespace prefixes),
      returning str(v).strip() on the first non-empty hit.
    - list -> recurse element by element.
    Never raises.
    """
    def _find(node) -> str:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower().endswith("employeenumber") and v:
                    s = str(v).strip()
                    if s:
                        return s
            for v in node.values():
                found = _find(v)
                if found:
                    return found
        elif isinstance(node, (list, tuple)):
            for item in node:
                found = _find(item)
                if found:
                    return found
        return ""

    if not extensions:
        return ""
    return _find(extensions)


def _is_param_unsupported(e: Exception) -> bool:
    """Decide whether the exception means "the runtime does not recognize the Extensions parameter" (old boto3).

    Conservative rule: only ParamValidationError / TypeError, or an error message containing both 'Extensions'
    and ('Unknown parameter' or 'unexpected keyword'). Permission/resource/throttling errors do not match.
    """
    if isinstance(e, (ParamValidationError, TypeError)):
        return True
    msg = str(e)
    return "Extensions" in msg and ("Unknown parameter" in msg or "unexpected keyword" in msg)


def _describe_user_with_ext(ids, uid: str) -> dict:
    """describe_user with the enterprise-extension parameter; when old boto3 rejects it, short-circuit at process level and retry without the extension.

    Fall back only for "parameter not supported" errors (set _ext_unsupported=True); other exceptions
    (AccessDenied/ResourceNotFound/Throttling, etc.) are re-raised as-is, never swallowed.
    """
    global _ext_unsupported
    if not _ext_unsupported:
        try:
            return ids.describe_user(
                IdentityStoreId=IDENTITY_STORE_ID, UserId=uid,
                Extensions=[_ENTERPRISE_EXT])
        except Exception as e:  # noqa: BLE001 — triage immediately: fall back only for unsupported-parameter, re-raise the rest
            if _is_param_unsupported(e):
                _ext_unsupported = True
            else:
                raise
    return ids.describe_user(IdentityStoreId=IDENTITY_STORE_ID, UserId=uid)


def _user_email_display(ids, uid: str) -> tuple[str, str, str, str]:
    """Return (email_lower, display_name, created_date_iso, employee_number).

    created_date: describe_user top-level CreatedAt(datetime) as an ISO string, "" if unavailable.
    employee_number: the employeeNumber from the enterprise extension, "" if unavailable/empty.
    """
    user = _describe_user_with_ext(ids, uid)
    emails = user.get("Emails", [])
    email = next((e["Value"] for e in emails if e.get("Primary")), None)
    if not email and emails:
        email = emails[0]["Value"]
    display = user.get("DisplayName") or user.get("UserName", "")
    created_date = _iso_created_at(user.get("CreatedAt"))
    employee_number = _employee_number_from(user.get("Extensions"))
    return (email.strip().lower() if email else ""), display, created_date, employee_number


def _group_members(ids, gid: str) -> list[str]:
    uids = []
    mp = ids.get_paginator("list_group_memberships")
    for page in mp.paginate(IdentityStoreId=IDENTITY_STORE_ID, GroupId=gid):
        for m in page.get("GroupMemberships", []):
            uids.append(m["MemberId"]["UserId"])
    return uids


def _add(result: dict, email: str, display: str, group: str | None,
         created_date: str = "", employee_number: str = "") -> None:
    if not email:
        return
    entry = result.setdefault(email, {
        "department": UNGROUPED, "groups": [], "display_name": display,
        "created_date": "", "employee_number": "",
    })
    if display and not entry.get("display_name"):
        entry["display_name"] = display
    # created_date / employee_number are subscription (= IdC user = email) attributes; first non-empty wins, not overwritten.
    if created_date and not entry.get("created_date"):
        entry["created_date"] = created_date
    if employee_number and not entry.get("employee_number"):
        entry["employee_number"] = employee_number
    if group:
        if group not in entry["groups"]:
            entry["groups"].append(group)
        # The primary department is the first group
        entry["department"] = entry["groups"][0]


def _load_from_subscription_app(ids, sso) -> dict[str, dict]:
    """Resolve registered users from the Kiro/Q Developer subscription profile application's assignments (the authoritative roster).

    Subscription users = the principals assigned to that application: USERs are included directly; GROUPs are expanded to members.
    Department: the department-group name (extracted from the group Description), skipping add-on groups (kiro-power/promax/proplus);
    display name: the Identity Center user's DisplayName. This is the single authoritative source for the total registered count (aligned with subscription seats).
    """
    result: dict[str, dict] = {}
    assignments = []
    paginator = sso.get_paginator("list_application_assignments")
    for page in paginator.paginate(ApplicationArn=SUBSCRIPTION_APP_ARN):
        assignments.extend(page.get("ApplicationAssignments", []))

    # First collect each user's group membership (group name + department name), then decide the department.
    for a in assignments:
        pid, ptype = a["PrincipalId"], a["PrincipalType"]
        if ptype == "USER":
            email, display, created, empno = _user_email_display(ids, pid)
            _add(result, email, display, None, created, empno)  # direct assignment, no group
        elif ptype == "GROUP":
            grp = ids.describe_group(IdentityStoreId=IDENTITY_STORE_ID, GroupId=pid)
            gname = grp.get("DisplayName", pid)
            zh = _extract_zh_dept(grp.get("Description") or "")
            for uid in _group_members(ids, pid):
                email, display, created, empno = _user_email_display(ids, uid)
                # Add-on group: members still count as subscriptions (recorded in result) but not as a department;
                # department group: use the department name (fall back to the English group name if none can be extracted).
                if _is_addon_group(gname):
                    _add(result, email, display, None, created, empno)
                else:
                    _add(result, email, display, zh or gname, created, empno)
    return result


def _load_from_all_groups(ids) -> dict[str, dict]:
    """Fallback: walk all groups and their members (used when the subscription application ARN is not configured)."""
    result: dict[str, dict] = {}
    gp = ids.get_paginator("list_groups")
    groups = []
    for page in gp.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        groups.extend(page.get("Groups", []))
    for g in groups:
        gname = g.get("DisplayName", g["GroupId"])
        for uid in _group_members(ids, g["GroupId"]):
            email, display, created, empno = _user_email_display(ids, uid)
            _add(result, email, display, gname, created, empno)
    return result


def _load_from_group_csv() -> dict[str, dict]:
    """Read the user->group mapping from the Kiro-exported lookup/user_groups.csv.

    Columns: user_email,user_id,group_name,group_id. A user may belong to multiple groups (multiple rows).

    Both the department name and the user's display name are enriched from Identity Center (more authoritative, general):
      - Department name: the department-group name of the user's group (extracted from the group Description),
        skipping add-on groups like kiro-power/promax/proplus; fall back to the English group name if none can be extracted.
      - Display name: the Identity Center user's DisplayName (fall back to the name fields, then to the email prefix).
    When Identity Center is unavailable, everything falls back (department = English group name, display name = email prefix) without affecting the main flow.
    """
    import csv
    import io

    body = s3.get_object(Bucket=GROUP_CSV_BUCKET, Key=GROUP_CSV_KEY)["Body"].read()
    text = body.decode("utf-8-sig", errors="replace")
    result: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        email = (row.get("user_email") or "").strip().lower()
        group = (row.get("group_name") or "").strip()
        if not email:
            continue
        _add(result, email, email.split("@")[0], group or None)

    # --- Enrich the department name + display name from Identity Center (best-effort, fall back on failure) ---
    try:
        ids = boto3.client("identitystore", region_name=IDENTITY_STORE_REGION)
        dept_map = _load_group_dept_map(ids)      # group_name(lower) -> department name
        name_map = _load_idc_display_names(ids)   # email(lower) -> display name
        for email, info in result.items():
            # Department: take the first non-add-on group's department name
            dept = UNGROUPED
            for g in info.get("groups", []):
                if _is_addon_group(g):
                    continue
                dept = dept_map.get(g.strip().lower()) or g
                break
            info["department"] = dept
            # Display name: prefer the IdC DisplayName, fall back to the original email prefix
            zh = name_map.get(email)
            if zh:
                info["display_name"] = zh
    except Exception:  # noqa: BLE001 — keep the CSV fallback values when IdC is unavailable
        log.warning("Identity Center lookup failed; keeping CSV fallback values", exc_info=True)
    return result


def _load_group_dept_map(ids) -> dict[str, str]:
    """Mapping of all Identity Center groups -> department name (key = group DisplayName lowercased).

    The department name is extracted from the group Description; add-on groups are skipped; groups with no extractable name are omitted (caller falls back to English).
    """
    out: dict[str, str] = {}
    gp = ids.get_paginator("list_groups")
    for page in gp.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        for g in page.get("Groups", []):
            name = (g.get("DisplayName") or "").strip()
            if not name or _is_addon_group(name):
                continue
            zh = _extract_zh_dept(g.get("Description") or "")
            if zh:
                out[name.lower()] = zh
    return out


def _load_idc_display_names(ids) -> dict[str, str]:
    """Mapping of all Identity Center users -> DisplayName (key = primary email lowercased).

    Only users whose DisplayName contains CJK characters are included; those without (romanized/phone-number names) are omitted, and the caller falls back to the email prefix.
    """
    import re
    out: dict[str, str] = {}
    up = ids.get_paginator("list_users")
    for page in up.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        for u in page.get("Users", []):
            emails = u.get("Emails", []) or []
            email = next((e["Value"] for e in emails if e.get("Primary")), None)
            if not email and emails:
                email = emails[0].get("Value")
            if not email:
                continue
            dn = (u.get("DisplayName") or "").strip()
            if not re.search(r"[\u4e00-\u9fff]", dn):
                # Fallback: concatenate the name fields
                fam = (u.get("Name", {}) or {}).get("FamilyName", "") or ""
                giv = (u.get("Name", {}) or {}).get("GivenName", "") or ""
                cand = (fam + giv).strip()
                dn = cand if re.search(r"[\u4e00-\u9fff]", cand) else ""
            if dn:
                out[email.strip().lower()] = dn
    return out


def _load_from_identity_center() -> dict[str, dict]:
    # Priority: subscription-app assignments (authoritative roster) > group CSV > walk all groups.
    # The subscription app is the single authoritative source for "registered users" (= actually provisioned seats, matching the reference dashboard's total registered).
    ids = boto3.client("identitystore", region_name=IDENTITY_STORE_REGION)
    if SUBSCRIPTION_APP_ARN:
        sso = boto3.client("sso-admin", region_name=IDENTITY_STORE_REGION)
        return _load_from_subscription_app(ids, sso)
    if GROUP_CSV_BUCKET and GROUP_CSV_KEY:
        return _load_from_group_csv()
    return _load_from_all_groups(ids)


def handler(event, context):
    mapping = _load_mock() if MOCK_MODE else _load_from_identity_center()

    table = ddb.Table(DEPT_TABLE)
    now = datetime.now(timezone.utc).isoformat()
    # First record this sync's keys, then clean up old users that no longer exist (avoid leaving unsubscribed users behind).
    current_keys = set()
    written = 0
    with table.batch_writer() as bw:
        for email, info in mapping.items():
            key = email.strip().lower()
            current_keys.add(key)
            bw.put_item(Item={
                "user_key": key,
                "department": info.get("department", UNGROUPED) or UNGROUPED,
                "groups": info.get("groups", []),
                "display_name": info.get("display_name", ""),
                "employee_number": info.get("employee_number", "") or "",
                "created_date": info.get("created_date", "") or "",
                "synced_at": now,
            })
            written += 1

    # Clean up old entries not in this sync's result (in real mode; mock mode does not clean up to avoid accidental deletion)
    removed = 0
    if not MOCK_MODE and current_keys:
        resp = table.scan(ProjectionExpression="user_key")
        stale = [it["user_key"] for it in resp.get("Items", []) if it["user_key"] not in current_keys]
        with table.batch_writer() as bw:
            for k in stale:
                bw.delete_item(Key={"user_key": k})
                removed += 1

    return {"status": "ok", "mock_mode": MOCK_MODE, "users_synced": written, "stale_removed": removed}

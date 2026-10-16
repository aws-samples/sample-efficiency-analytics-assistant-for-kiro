#!/usr/bin/env python3
"""Generate mock data matching the real per-user CSV schema of Kiro Enterprise.

Reference: https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/

Output:
  mock/AWSLogs/<accountId>/KiroLogs/user_report/<region>/<yyyy>/<mm>/<dd>/00/
        <clientType>_<accountId>_user_report_<ts>.csv
  mock/identity_groups.json   (email -> {department, groups, display_name})

Usage:
  python tools/generate_mock_data.py --days 7 --out mock \
      --account-id 123456789012 --region us-west-2
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from datetime import date, datetime, timedelta, timezone

# --- Fixed enumerations ----------------------------------------------------
CLIENT_TYPES = ["KIRO_IDE", "KIRO_CLI", "PLUGIN"]
TIERS = ["Pro", "ProPlus", "ProMax", "Power"]
# Per-tier plan credit cap (Overage_Cap takes this value when Overage is off)
TIER_CAP = {"Pro": 100.0, "ProPlus": 150.0, "ProMax": 200.0, "Power": 400.0}
# Model columns (lowercase, Auto first, the rest alphabetical) — real files are dynamic; a fixed set here eases review
MODELS = ["auto", "claude_haiku_4_5", "claude_opus_4_1", "claude_sonnet_4_5", "gpt_5", "gpt_5_5"]

# Departments (correspond to Identity Center group displayName) — replace with your real org
DEPARTMENTS = [
    "Sun Power - R&D Center",
    "Sun Power - Power Electronics Institute",
    "Sun Digital Center - Software Dept",
    "Sun Digital Center - Data Dept",
    "Sun Storage - Systems Dept",
    "Ungrouped",
]
# Per-department weights (headcount share); Ungrouped is a small share
DEPT_WEIGHTS = [28, 18, 22, 12, 15, 5]

# Static column order (strictly per the docs)
STATIC_COLS = [
    "Date", "UserId", "Client_Type", "Chat_Conversations", "Credits_Used",
    "Overage_Cap", "Overage_Credits_Used", "Overage_Enabled", "ProfileId",
    "Subscription_Tier", "Total_Messages", "New_User", "User_Email",
]
MODEL_COLS = [f"{m}_messages" for m in MODELS]
ALL_COLS = STATIC_COLS + MODEL_COLS


def make_users(n: int) -> list[dict]:
    """Build n stable user profiles."""
    first = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
             "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
             "Thomas", "Sarah", "Charles", "Karen", "Chris", "Nancy", "Daniel", "Lisa",
             "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
             "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Josh", "Michelle"]
    last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
            "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas",
            "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
            "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson"]
    users = []
    for i in range(n):
        name = random.choice(first) + " " + random.choice(last)
        email = f"user{i:03d}@example.com"
        tier = random.choices(TIERS, weights=[10, 5, 30, 55])[0]  # close to the reference UI distribution
        # Force the first 3 users to "Ungrouped" so review covers the ungrouped branch
        dept = "Ungrouped" if i < 3 else random.choices(DEPARTMENTS, weights=DEPT_WEIGHTS)[0]
        users.append({
            "user_id": f"idc-user-{i:05d}",
            "email": email,
            "name": name,
            "tier": tier,
            "department": dept,
            "profile_id": "profile-KIRO-PRO",
            "activity": random.random(),  # 0..1, drives the activity level
        })
    return users


def gen_day_rows(users: list[dict], day: date, is_first_day: bool) -> dict[str, list[dict]]:
    """Generate each client's rows for a given day. Returns {client_type: [row,...]}."""
    out = {c: [] for c in CLIENT_TYPES}
    for u in users:
        # whether this user is active that day
        if random.random() > (0.35 + 0.6 * u["activity"]):
            continue
        # a user may be active on 1~2 clients that day
        clients = random.sample(CLIENT_TYPES, k=random.choice([1, 1, 2]))
        for ct in clients:
            cap = TIER_CAP[u["tier"]]
            credits_used = round(random.uniform(0.5, cap * 1.1) * u["activity"], 1)
            overage_enabled = u["tier"] in ("ProMax", "Power") and random.random() < 0.2
            overage_used = 0.0
            if credits_used > cap:
                if overage_enabled:
                    overage_used = round(credits_used - cap, 1)
                else:
                    credits_used = round(min(credits_used, cap), 1)
            convs = int(credits_used / random.uniform(2, 6)) + random.randint(0, 3)
            total_msgs = convs * random.randint(4, 20)
            new_user = is_first_day and random.random() < 0.05

            row = {
                "Date": day.isoformat(),
                "UserId": u["user_id"],
                "Client_Type": ct,
                "Chat_Conversations": convs,
                "Credits_Used": credits_used,
                "Overage_Cap": cap,
                "Overage_Credits_Used": overage_used,
                "Overage_Enabled": str(overage_enabled).lower(),
                "ProfileId": u["profile_id"],
                "Subscription_Tier": u["tier"],
                "Total_Messages": total_msgs,
                "New_User": str(new_user).lower(),
                "User_Email": u["email"],
            }
            # Dynamic model columns: spread total_msgs across a few models
            remaining = total_msgs
            model_msgs = {mc: 0 for mc in MODEL_COLS}
            active_models = random.sample(MODEL_COLS, k=random.randint(1, len(MODEL_COLS)))
            for j, mc in enumerate(active_models):
                if j == len(active_models) - 1:
                    model_msgs[mc] = remaining
                else:
                    take = random.randint(0, remaining)
                    model_msgs[mc] = take
                    remaining -= take
            row.update(model_msgs)
            out[ct].append(row)
    return out


def write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--out", default="mock")
    ap.add_argument("--account-id", default="123456789012")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--prefix", default="user-activity-reports")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    users = make_users(args.users)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=args.days - 1)

    total_rows = 0
    for d in range(args.days):
        day = start + timedelta(days=d)
        by_client = gen_day_rows(users, day, is_first_day=(d == 0))
        for ct, rows in by_client.items():
            if not rows:
                continue
            ts = datetime.combine(day, datetime.min.time()).strftime("%Y%m%dT020000Z")
            # Path close to the real one: prefix/AWSLogs/acct/KiroLogs/user_report/region/yyyy/mm/dd/00/
            rel = os.path.join(
                args.prefix, "AWSLogs", args.account_id, "KiroLogs", "user_report",
                args.region, f"{day:%Y}", f"{day:%m}", f"{day:%d}", "00",
                f"{ct}_{args.account_id}_user_report_{ts}.csv",
            )
            write_csv(rows, os.path.join(args.out, rel))
            total_rows += len(rows)

    # identity_groups.json — some users intentionally have no group (=> Ungrouped)
    identity = {}
    for u in users:
        if u["department"] == "Ungrouped":
            groups = []
            dept = "Ungrouped"
        else:
            groups = [u["department"]]
            dept = u["department"]
        identity[u["email"]] = {
            "department": dept,
            "groups": groups,
            "display_name": u["name"],
            "user_id": u["user_id"],
        }
    with open(os.path.join(args.out, "identity_groups.json"), "w", encoding="utf-8") as f:
        json.dump(identity, f, ensure_ascii=False, indent=2)

    print(f"Done: {total_rows} CSV rows, {len(users)} users, {args.days} days")
    print(f"Output directory: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

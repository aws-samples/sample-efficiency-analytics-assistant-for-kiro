"""ETL Lambda handler: read raw CSV -> transform -> write curated Parquet.

Triggers:
- EventBridge schedule (daily): process all client CSVs for the event-specified date or "yesterday".
- Manual invoke: the event may contain {"date": "YYYY-MM-DD"} or {"backfill": true} to process everything.

Environment variables:
  RAW_BUCKET, RAW_PREFIX       where the Kiro CSVs live (in mock mode this is the uploaded mock data)
  CURATED_BUCKET               Parquet output bucket
  DEPT_TABLE                   DynamoDB user_department table name
  CREDIT_UNIT_PRICE            estimated-cost unit price
  MOCK_MODE                    behavior is unchanged when "true" (the data source is still RAW_BUCKET)
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta

import boto3
import pandas as pd

import transform  # packaged in the same layer

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")

RAW_BUCKET = os.environ["RAW_BUCKET"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "").strip("/")
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
DEPT_TABLE = os.environ["DEPT_TABLE"]
CREDIT_UNIT_PRICE = float(os.environ.get("CREDIT_UNIT_PRICE", "0.15"))


def _load_department_map() -> dict[str, dict]:
    table = ddb.Table(DEPT_TABLE)
    items, resp = [], table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return {
        it["user_key"]: {
            "department": it.get("department", "Ungrouped"),
            "display_name": it.get("display_name", ""),
            "groups": it.get("groups", []),
        }
        for it in items
    }


def _list_csv_keys(target_day: date | None) -> list[str]:
    """List CSV keys in the raw bucket matching the date. target_day=None means all."""
    keys, token = [], None
    prefix = RAW_PREFIX + "/" if RAW_PREFIX else ""
    while True:
        kw = {"Bucket": RAW_BUCKET, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            k = obj["Key"]
            if not k.endswith(".csv"):
                continue
            if target_day is None:
                keys.append(k)
            else:
                seg = f"/{target_day:%Y}/{target_day:%m}/{target_day:%d}/"
                if seg in k:
                    keys.append(k)
        if resp.get("IsTruncated"):
            token = resp["NextContinuationToken"]
        else:
            break
    return keys


def _read_key(key: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=RAW_BUCKET, Key=key)["Body"].read()
    return transform._read_csv(io.BytesIO(body))


def _write_parquet(df: pd.DataFrame, table_name: str) -> int:
    """Write Parquet to the curated bucket, partitioned by year/month/day."""
    if df.empty:
        return 0
    written = 0
    df = df.copy()
    df["_d"] = pd.to_datetime(df["date"])
    for (y, m, d), part in df.groupby([df["_d"].dt.year, df["_d"].dt.month, df["_d"].dt.day]):
        part = part.drop(columns=["_d"])
        buf = io.BytesIO()
        part.to_parquet(buf, index=False, engine="pyarrow")
        buf.seek(0)
        key = f"{table_name}/year={y}/month={m:02d}/day={d:02d}/{table_name}_{y}{m:02d}{d:02d}.parquet"
        s3.put_object(Bucket=CURATED_BUCKET, Key=key, Body=buf.getvalue())
        written += len(part)
    return written


def handler(event, context):
    event = event or {}
    if event.get("backfill"):
        target = None
    elif event.get("date"):
        target = date.fromisoformat(event["date"])
    else:
        target = date.today() - timedelta(days=1)  # default to processing yesterday

    keys = _list_csv_keys(target)
    if not keys:
        return {"status": "no_data", "target": str(target), "keys": 0}

    dept_map = _load_department_map()
    frames = [_read_key(k) for k in keys]
    fud, fmd = transform.transform(frames, dept_map, CREDIT_UNIT_PRICE)

    n1 = _write_parquet(fud, "fact_user_daily")
    n2 = _write_parquet(fmd, "fact_user_model_daily")
    return {
        "status": "ok",
        "target": str(target),
        "csv_keys": len(keys),
        "fact_user_daily_rows": n1,
        "fact_user_model_daily_rows": n2,
    }

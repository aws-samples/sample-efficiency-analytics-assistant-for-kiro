"""Dashboard API runtime configuration (injected from environment variables)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    glue_db: str = os.environ.get("GLUE_DB", "kiro_usage")
    athena_workgroup: str = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
    region: str = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))
    credit_unit_price: float = float(os.environ.get("CREDIT_UNIT_PRICE", "0.04"))
    top_n: int = int(os.environ.get("TOP_N", "15"))
    cache_ttl: float = float(os.environ.get("QUERY_CACHE_TTL", "60"))
    # Source of the total registered-user count: the department table written by identity-sync (contains all synced users).
    # Activation-rate denominator = the number of entries in that table (matching the reference dashboard: total registered).
    dept_table: str = os.environ.get("DEPT_TABLE", "")
    # NL Q&A Agent (AgentCore Runtime) ARN; empty means /api/chat returns "not configured".
    agent_runtime_arn: str = os.environ.get("AGENT_RUNTIME_ARN", "")
    # Model multiplier (credit multiplier) maintenance: DynamoDB table name (injected by ApiStack).
    model_multiplier_table: str = os.environ.get("MODEL_MULTIPLIER_TABLE", "")
    # region reuses the existing Settings.region


def get_settings() -> Settings:
    return Settings()

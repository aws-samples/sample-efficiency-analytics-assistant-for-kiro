"""Kiro usage NL Q&A Agent (Strands + Bedrock).

- The system prompt constrains it: use only the controlled tools, cite specific numbers and time ranges, and say so when data is insufficient.
- The model runs on Bedrock (default Claude Sonnet).
- No arbitrary SQL is accepted; all data access goes through the parameterized tools in tools.py.
"""
from __future__ import annotations

import os

from strands import Agent
from strands.models import BedrockModel

try:
    from tools import ALL_TOOLS
except ImportError:
    from backend.agent.tools import ALL_TOOLS  # type: ignore

# Use a cross-region inference profile ID (on-demand direct invocation of sonnet-4.5 is not supported; an inference profile is required).
MODEL_ID = os.environ.get(
    "AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
REGION = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))

SYSTEM_PROMPT = """You are the usage-analytics assistant of the "Efficiency Analytics Assistant for Kiro", serving enterprise admins.

Responsibilities and rules:
- Prefer the generic tool query_usage for the vast majority of questions, mapping natural language to parameters:
  metric(credits/messages/est_cost/active_days/users) × group_by(none/department/
  subscription_tier/model/date/user) × filters(department/subscription_tier/model/user/overage_only)
  × time(period or date_from/date_to).
- Which models a person used: if you only know their name, first call find_user to get the email, then call
  query_usage(metric=messages, group_by=model, user=<email>). Use get_summary for overall KPIs.
- Query data only through the provided controlled tools; never fabricate numbers and never run arbitrary SQL.
- Always cite specific numbers in your answer and state the time range and metric used (e.g. "2026-09-01 to 2026-09-07, aggregated per user").
- If the question lacks a time range, use get_filters to learn the available periods, or ask the user to clarify; do not guess.
- [Year] When the user says only something like "September" with no year, do not guess a year (e.g. 2024); first call get_filters to see the periods that actually exist
  (e.g. 2026-09) and use that year-month. Find people with find_user (which is not time-bound), then query usage by month once you have the email.
- Metrics: messages = number of prompts (not credits); usage rate = used / plan limit; overage = cumulative credits in range > limit; estimated cost = unit price × used credits (an estimate).
- When data is insufficient or a tool does not cover it, say so explicitly; do not give unsupported conclusions.
- Answer concisely in English, using lists or small tables to present key numbers when helpful.
"""


def build_agent(system_prompt: str = SYSTEM_PROMPT) -> Agent:
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(model=model, tools=ALL_TOOLS, system_prompt=system_prompt)

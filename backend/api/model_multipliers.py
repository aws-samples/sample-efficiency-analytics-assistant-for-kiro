"""Model multiplier (credit multiplier) maintenance endpoints and storage access.

Each model's credit consumption is scaled by its "multiplier"; this module provides reading multipliers (GET, with seed
backfill and unknown-model backfill) and admin editing (PUT). Storage is DynamoDB (boto3 **resource** mode),
the table name comes from `settings.model_multiplier_table` (injected by env `MODEL_MULTIPLIER_TABLE`),
with primary key `model` (aligned with the Athena fact_user_model_daily.model string).

Multiplier `source` semantics:
- official: from the kiro.dev official multiplier table (2026-10-08);
- estimated: an estimated value when there is no official entry (e.g. the DB's claude_sonnet_5 maps to the official Sonnet 5.5);
- edited: manually edited by an admin on the maintenance page;
- unknown: a model seen in the data but not covered by the seed (multiplier=null, not pre-selected, still counted in the denominator).

The module uses lazy boto3 resource init + _table(settings) + an import fallback for flat Lambda packaging.
Authentication is enforced by the API Gateway Cognito JWT authorizer; there is no application-level authorization.
"""
from __future__ import annotations

import logging
from numbers import Real
from typing import Optional

import boto3
from fastapi import APIRouter, Body, Depends, Path
from fastapi.responses import JSONResponse

try:
    from backend.api.settings import Settings, get_settings
    from backend.api.deps import get_queries, get_query_layer
    from backend.shared.athena_client import Queries, QueryLayer
except ImportError:  # flat packaging inside Lambda
    from settings import Settings, get_settings  # type: ignore
    from deps import get_queries, get_query_layer  # type: ignore
    from shared.athena_client import Queries, QueryLayer  # type: ignore

log = logging.getLogger("model_multipliers")

router = APIRouter(tags=["model-multipliers"])

# Seed multiplier table: key = the **actual string** of Athena fact_user_model_daily.model (not a display name),
# value = {multiplier, source}. Official values come from the kiro.dev multiplier table (2026-10-08);
# claude_sonnet_5 has no official same-name entry (official is Sonnet 5.5=1.3), so it is marked estimated.
# A model seen in the data but not in this table is backfilled at GET time as multiplier=null, source='unknown'.
# key = the actual Athena fact_user_model_daily.model string. The ETL does not rename the model column
# (it only strips the `_messages` suffix), so key = the <model> from the source CSV `<model>_messages` column as-is:
# all lowercase, spaces→underscores, version dots preserved (per the production-confirmed gpt_5.6_sol / claude_opus_5).
# All multipliers below come from Kiro IDE model-selector screenshots (2026-10, user-provided), marked official.
# For models not yet seen in production data, the actual column-name spelling is inferred from the rule above; if one later appears and the spelling differs,
# the maintenance page shows an extra unknown entry for an admin to merge manually on the multiplier maintenance page.
SEED: dict[str, dict] = {
    "auto": {"multiplier": 1.0, "source": "official"},
    "claude_opus_5.5": {"multiplier": 2.0, "source": "official"},
    "claude_sonnet_5.5": {"multiplier": 1.3, "source": "official"},
    "claude_opus_5": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_5": {"multiplier": 1.3, "source": "official"},
    "claude_opus_4.8": {"multiplier": 2.2, "source": "official"},
    "gpt_5.6_sol": {"multiplier": 4.4, "source": "official"},
    "gpt_5.6_terra": {"multiplier": 2.2, "source": "official"},
    "gpt_5.6_luna": {"multiplier": 0.6, "source": "official"},
    "claude_opus_4.7": {"multiplier": 2.2, "source": "official"},
    "claude_opus_4.6": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_4.6": {"multiplier": 1.3, "source": "official"},
    "claude_opus_4.5": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_4.5": {"multiplier": 1.3, "source": "official"},
    "claude_sonnet_4": {"multiplier": 1.3, "source": "official"},
    "claude_haiku_4.5": {"multiplier": 0.4, "source": "official"},
    "deepseek_3.2": {"multiplier": 0.25, "source": "official"},
    "minimax_m2.5": {"multiplier": 0.25, "source": "official"},
    "minimax_m2.1": {"multiplier": 0.15, "source": "official"},
    "glm_5": {"multiplier": 0.5, "source": "official"},
    "qwen3_coder_next": {"multiplier": 0.05, "source": "official"},
}

# Lazily initialized DynamoDB resource (module-level cache), created with settings.region on first table access.
_dynamodb = None


def _resource(region: str):
    """Return the cached DynamoDB service resource (created on first call)."""
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=region)
    return _dynamodb


def _table(settings: Settings):
    """Get the ModelMultipliers Table handle, reused for scan/put_item/update.

    The table name comes from `settings.model_multiplier_table` (env MODEL_MULTIPLIER_TABLE); the region
    reuses `settings.region`. Raises when the table name is empty, to avoid silently accessing a wrong default name.
    """
    table_name = settings.model_multiplier_table
    if not table_name:
        raise RuntimeError("MODEL_MULTIPLIER_TABLE is not configured")
    return _resource(settings.region).Table(table_name)


def _coerce_multiplier(raw) -> Optional[float]:
    """Validate the multiplier input to a non-negative float; invalid (non-numeric/negative/boolean) returns None."""
    # bool is a subclass of int; exclude it explicitly so True/False are not treated as 1/0.
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return None
    val = float(raw)
    if val < 0:
        return None
    return val


def _athena_models(q: Queries, layer: QueryLayer) -> list[str]:
    """Athena distinct model list (the authoritative source of the multiplier table keys)."""
    rows = layer.run(q.model_list())
    return [r["model"] for r in rows if r.get("model")]


def _scan_table(table) -> dict[str, dict]:
    """Full-table scan (paginated), returning a model -> item mapping."""
    out: dict[str, dict] = {}
    scan_kwargs: dict = {}
    while True:
        resp = table.scan(**scan_kwargs)
        for it in resp.get("Items", []):
            out[it["model"]] = it
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return out


@router.get("/api/model-multipliers")
def list_multipliers(
    settings: Settings = Depends(get_settings),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    """List all model multipliers (with first-time seed write + unknown-model backfill).

    (1) scan the table for existing multipliers;
    (2) when the table is empty, write the seed on first run (writing only SEED keys that hit the current Athena distinct model);
    (3) backfill models present in Athena but not yet in the table as multiplier=null, source='unknown';
    returns `{"items": [{"model", "multiplier", "source"}]}`, ordered by model ascending.
    """
    table = _table(settings)
    existing = _scan_table(table)
    athena_models = _athena_models(q, layer)

    # Table empty: write **all** SEED on first run (preloads the full multiplier table, not filtered by Athena hits—
    # so the maintenance page shows the official multipliers for all known models from the start, and future models already have a multiplier).
    if not existing:
        for model, seed in SEED.items():
            table.put_item(Item={"model": model, "multiplier": _to_decimal(seed["multiplier"]),
                                 "source": seed["source"]})
            existing[model] = {"model": model, "multiplier": seed["multiplier"],
                               "source": seed["source"]}

    # Assemble the response: table entries + models in Athena but not in the table backfilled as unknown.
    items: dict[str, dict] = {}
    for model, it in existing.items():
        items[model] = {
            "model": model,
            "multiplier": _mult_out(it.get("multiplier")),
            "source": it.get("source") or "unknown",
        }
    for model in athena_models:
        if model not in items:
            items[model] = {"model": model, "multiplier": None, "source": "unknown"}

    return {"items": sorted(items.values(), key=lambda x: x["model"])}


@router.put("/api/model-multipliers")
def update_multipliers(
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """Batch update multipliers (body `{"items": [{"model", "multiplier"}]}`).

    Each item's multiplier is validated as a non-negative number (any invalid one fails the whole request with 400, nothing persisted); on success put_item
    writes source='edited'. Returns `{"ok": True, "updated": n}`.
    """
    raw_items = (body or {}).get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return JSONResponse(status_code=400, content={"detail": "items must not be empty"})

    # Validate everything first; any invalid one fails the whole request with 400 (avoid partial writes).
    validated: list[tuple[str, float]] = []
    for it in raw_items:
        model = (it or {}).get("model")
        if not isinstance(model, str) or not model.strip():
            return JSONResponse(status_code=400, content={"detail": "invalid model"})
        mult = _coerce_multiplier((it or {}).get("multiplier"))
        if mult is None:
            return JSONResponse(status_code=400,
                                content={"detail": f"multiplier must be a non-negative number: {model}"})
        validated.append((model, mult))

    table = _table(settings)
    for model, mult in validated:
        table.put_item(Item={"model": model, "multiplier": _to_decimal(mult),
                             "source": "edited"})
    return {"ok": True, "updated": len(validated)}


@router.put("/api/model-multipliers/{model}")
def update_multiplier(
    model: str = Path(...),
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """Update a single model multiplier (body `{"multiplier"}`); validate a non-negative number, source set to 'edited'."""
    mult = _coerce_multiplier((body or {}).get("multiplier"))
    if mult is None:
        return JSONResponse(status_code=400,
                            content={"detail": "multiplier must be a non-negative number"})
    table = _table(settings)
    table.put_item(Item={"model": model, "multiplier": _to_decimal(mult),
                         "source": "edited"})
    return {"ok": True, "model": model, "multiplier": mult, "source": "edited"}


# --- helpers ---------------------------------------------------------------
def _to_decimal(val: float):
    """Convert a float to a DynamoDB-acceptable Decimal (the resource layer does not accept native float).

    Routing through str() avoids binary floating-point noise (e.g. 1.3 -> Decimal('1.3')).
    """
    from decimal import Decimal
    return Decimal(str(val))


def _mult_out(val) -> Optional[float]:
    """Convert the stored multiplier (Decimal/None) to a JSON-friendly float/None."""
    if val is None:
        return None
    return float(val)

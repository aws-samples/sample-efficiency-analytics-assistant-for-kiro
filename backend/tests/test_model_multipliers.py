"""Model-multipliers backend unit tests: no AWS.

Mirrors the fake-layer style of test_api.py:
- FakeTable replaces the DynamoDB Table (scan/put_item), primary key `model`;
- FakeLayer replaces QueryLayer, `run(model_list())` returns the preset Athena distinct models;
- get_settings is injected via app.dependency_overrides with a non-empty table name; get_query_layer injects FakeLayer;
- TestClient(app) calls the app directly; authentication is enforced at the API Gateway JWT authorizer, not in the app.

Covers: GET on an empty table writes the seed with keys = the real model strings; GET backfills models present in Athena
but not in the table as source=unknown; PUT updates a multiplier with source=edited; an invalid
(negative/non-numeric) multiplier returns 400.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api import model_multipliers as mm
from backend.api.deps import get_query_layer
from backend.api.settings import Settings, get_settings
from backend.shared.athena_client import QuerySpec


# The confirmed real Athena distinct model set (the authoritative source of multiplier-table keys).
ATHENA_MODELS = ["auto", "claude_opus_5", "claude_sonnet_5", "gpt_5.6_sol", "gpt_5.6_terra"]


def _settings() -> Settings:
    """A set of test config: non-empty multiplier table name."""
    return Settings(model_multiplier_table="ModelMultipliers-test")


# --- fake DynamoDB Table -----------------------------------------------------
class FakeTable:
    """In-memory DynamoDB Table, primary key `model`. Supports scan/put_item."""

    def __init__(self, items=None):
        self.items = {it["model"]: dict(it) for it in (items or [])}
        self.put_calls = []

    def scan(self, **kwargs):
        return {"Items": [dict(v) for v in self.items.values()]}

    def put_item(self, Item):
        self.put_calls.append(dict(Item))
        self.items[Item["model"]] = dict(Item)
        return {}


# --- fake QueryLayer ---------------------------------------------------------
class FakeLayer:
    """Only needs to support run(model_list()), returning the preset Athena distinct model rows."""

    def __init__(self, models):
        self._models = models

    def run(self, spec: QuerySpec):
        return [{"model": m} for m in self._models]


@pytest.fixture
def make_client(monkeypatch):
    """Factory: given preset items and athena models, assemble FakeTable + overrides + TestClient."""
    def _factory(items=None, athena_models=None):
        table = FakeTable(items)
        monkeypatch.setattr(mm, "_table", lambda settings: table)
        app.dependency_overrides[get_settings] = _settings
        app.dependency_overrides[get_query_layer] = lambda: FakeLayer(
            ATHENA_MODELS if athena_models is None else athena_models)
        client = TestClient(app)
        return client, table
    yield _factory
    app.dependency_overrides.clear()


# =============================================================================
# GET — empty table writes the seed, keys are the real model strings
# =============================================================================
def test_get_empty_table_writes_full_seed(make_client):
    """On an empty table, write **all** SEED (21 official multipliers), not filtered by Athena hits."""
    client, table = make_client()
    r = client.get("/api/model-multipliers")
    assert r.status_code == 200
    items = {it["model"]: it for it in r.json()["items"]}

    # all SEED entries have been written to the table (preloaded full multiplier table).
    assert set(table.items) == set(mm.SEED)
    assert len(table.items) == 21
    # all are official (multipliers from Kiro IDE model-selector screenshots).
    assert all(v["source"] == "official" for v in mm.SEED.values())
    # spot-check a few key values + real model string keys.
    assert items["auto"] == {"model": "auto", "multiplier": 1.0, "source": "official"}
    assert items["gpt_5.6_sol"]["multiplier"] == 4.4
    assert items["gpt_5.6_terra"]["multiplier"] == 2.2
    assert items["claude_opus_5"]["multiplier"] == 2.2
    assert items["claude_opus_5.5"]["multiplier"] == 2.0   # dot preserved
    assert items["claude_sonnet_5"] == {"model": "claude_sonnet_5",
                                        "multiplier": 1.3, "source": "official"}
    assert items["qwen3_coder_next"]["multiplier"] == 0.05


# =============================================================================
# GET — models in Athena but not in the table are backfilled as source=unknown (not written to the table)
# =============================================================================
def test_get_fills_unknown_for_athena_only_models(make_client):
    # there is already an edited auto; Athena has an extra model not in the seed.
    seed = [{"model": "auto", "multiplier": Decimal("1.5"), "source": "edited"}]
    extra_models = ATHENA_MODELS + ["mystery_model_x"]
    client, table = make_client(items=seed, athena_models=extra_models)

    r = client.get("/api/model-multipliers")
    assert r.status_code == 200
    items = {it["model"]: it for it in r.json()["items"]}

    # table non-empty -> no seed write; the existing auto keeps its edited value.
    assert items["auto"] == {"model": "auto", "multiplier": 1.5, "source": "edited"}
    # models in Athena but not in the table are backfilled as unknown/null.
    assert items["mystery_model_x"] == {"model": "mystery_model_x",
                                        "multiplier": None, "source": "unknown"}
    # other Athena models (seed not persisted because a non-empty table skips the seed write) are also backfilled as unknown.
    assert items["gpt_5.6_sol"] == {"model": "gpt_5.6_sol",
                                    "multiplier": None, "source": "unknown"}
    # not written to the table (backfill is only in the response layer).
    assert "mystery_model_x" not in table.items


# =============================================================================
# PUT batch — update multiplier with source=edited
# =============================================================================
def test_put_batch_updates_multiplier_source_edited(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": 1.25},
                                   {"model": "gpt_5.6_sol", "multiplier": 5}]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "updated": 2}
    assert table.items["auto"]["source"] == "edited"
    assert float(table.items["auto"]["multiplier"]) == 1.25
    assert table.items["gpt_5.6_sol"]["source"] == "edited"
    assert float(table.items["gpt_5.6_sol"]["multiplier"]) == 5.0


# =============================================================================
# PUT single — update multiplier with source=edited
# =============================================================================
def test_put_single_updates_multiplier_source_edited(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers/claude_opus_5", json={"multiplier": 3.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["source"] == "edited"
    assert table.items["claude_opus_5"]["source"] == "edited"
    assert float(table.items["claude_opus_5"]["multiplier"]) == 3.0


# =============================================================================
# PUT — invalid multiplier returns 400 (negative / non-numeric), nothing persisted
# =============================================================================
def test_put_batch_negative_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": -1}]})
    assert r.status_code == 400
    assert "auto" not in table.put_calls and not table.put_calls


def test_put_batch_non_numeric_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": "abc"}]})
    assert r.status_code == 400
    assert not table.put_calls


def test_put_single_negative_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers/auto", json={"multiplier": -0.5})
    assert r.status_code == 400
    assert not table.put_calls


def test_put_batch_empty_items_400(make_client):
    client, _ = make_client()
    r = client.put("/api/model-multipliers", json={"items": []})
    assert r.status_code == 400

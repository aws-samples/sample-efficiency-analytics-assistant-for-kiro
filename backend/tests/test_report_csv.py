"""Monthly report CSV attachment (report/handler.py::_build_csv) unit test: CSV formula-injection guard.

report/handler.py uses the Lambda-flat `from shared.athena_client import ...`,
so backend/ is temporarily added to sys.path, then the module is loaded as a standalone object by absolute path via importlib.
"""
import csv
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_HANDLER_PATH = _BACKEND / "report" / "handler.py"


@pytest.fixture(scope="module")
def rh():
    added = str(_BACKEND) not in sys.path
    if added:
        sys.path.insert(0, str(_BACKEND))
    try:
        spec = importlib.util.spec_from_file_location("report_handler_under_test", _HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(str(_BACKEND))
    return module


def test_build_csv_neutralizes_formula_injection(rh):
    up = [{"display_name": '=HYPERLINK("http://evil.example","x")', "email": "a@example.com",
           "department": "+cmd", "subscription_tier": "Pro", "credits_used": 1500.0,
           "usage_limit": 1000.0, "usage_rate": 1.5, "suggested_tier": "ProPlus"}]
    down = [{"display_name": "Zhang San", "email": "b@example.com", "department": "Engineering",
             "subscription_tier": "ProMax", "credits_used": 500.0, "usage_limit": 5000.0,
             "usage_rate": 0.1, "suggested_tier": "Pro"}]
    rows = list(csv.reader(io.StringIO(rh._build_csv(up, down))))
    assert rows[0][0] == "Suggestion type"  # header unchanged
    assert rows[1][1] == '\'=HYPERLINK("http://evil.example","x")'
    assert rows[1][3] == "'+cmd"
    assert rows[1][5] == "1500.0"  # numeric column as-is
    assert rows[2][1] == "Zhang San" and rows[2][3] == "Engineering"  # ordinary values unchanged


def test_csv_safe(rh):
    assert rh._csv_safe(None) == ""
    assert rh._csv_safe("-1") == "'-1"
    assert rh._csv_safe("@x") == "'@x"
    assert rh._csv_safe("\tx") == "'\tx"
    assert rh._csv_safe("ok") == "ok"


def test_build_html_escapes_untrusted_fields(rh):
    """Identity Center display names / emails / departments must be HTML-escaped in the report email."""
    evil = {"display_name": "<script>alert(1)</script>", "email": "u@example.com",
            "department": "<img src=x onerror=alert(2)>", "subscription_tier": "Pro",
            "credits_used": 1500, "usage_limit": 1000, "usage_rate": 1.5, "suggested_tier": "ProPlus"}
    out = rh._build_html("2026-10", ("2026-10-01", "2026-10-22"), {"registered_users": 1},
                              {"Pro": 1}, [evil], [])
    assert "<script>" not in out
    assert "<img src=x" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&lt;img src=x onerror=alert(2)&gt;" in out


def test_build_html_keeps_normal_names(rh):
    u = {"display_name": "Jane Doe", "email": "jane@example.com", "department": "R&D",
         "subscription_tier": "Pro", "credits_used": 10, "usage_limit": 1000, "usage_rate": 0.01,
         "suggested_tier": "Pro"}
    out = rh._build_html("2026-10", ("2026-10-01", "2026-10-22"), {}, {}, [], [u])
    assert "Jane Doe" in out
    assert "R&amp;D" in out

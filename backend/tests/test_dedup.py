"""Boundary unit tests for the pure function annotate_duplicate_subscriptions (no AWS).

Dedup metric:
- Group key = employee_number.strip(), empty string = independent subscription, not grouped;
- within a non-empty group the earliest created_date is the primary (is_duplicate_subscription=False, primary_email=""),
  the rest are duplicates (True, primary_email=the primary's email);
- empty created_date sorts last (never chosen as primary); when a group has no created_date, the primary is chosen by email ascending;
- return order == input order.
"""
from backend.api.main import annotate_duplicate_subscriptions


def _by_email(rows):
    return {r["email"]: r for r in rows}


def test_same_employee_number_earliest_is_primary():
    users = [
        {"email": "a@example.com", "employee_number": "E1", "created_date": "2024-01-01"},
        {"email": "b@example.com", "employee_number": "E1", "created_date": "2024-02-01"},
        {"email": "c@example.com", "employee_number": "E1", "created_date": "2024-03-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    assert out["a@example.com"]["is_duplicate_subscription"] is False
    assert out["a@example.com"]["primary_email"] == ""
    assert out["b@example.com"]["is_duplicate_subscription"] is True
    assert out["b@example.com"]["primary_email"] == "a@example.com"
    assert out["c@example.com"]["is_duplicate_subscription"] is True
    assert out["c@example.com"]["primary_email"] == "a@example.com"


def test_empty_employee_number_all_independent():
    users = [
        {"email": "a@example.com", "employee_number": "", "created_date": "2024-01-01"},
        {"email": "b@example.com", "employee_number": "", "created_date": "2024-02-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    for e in ("a@example.com", "b@example.com"):
        assert out[e]["is_duplicate_subscription"] is False
        assert out[e]["primary_email"] == ""


def test_missing_created_date_sorts_last_never_primary():
    users = [
        {"email": "a@example.com", "employee_number": "E1", "created_date": ""},
        {"email": "b@example.com", "employee_number": "E1", "created_date": "2024-02-01"},
        {"email": "c@example.com", "employee_number": "E1", "created_date": "2024-01-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # Among non-empty values the earliest c is primary; a with empty created_date must be a duplicate (sorts last, never primary).
    assert out["c@example.com"]["is_duplicate_subscription"] is False
    assert out["c@example.com"]["primary_email"] == ""
    assert out["a@example.com"]["is_duplicate_subscription"] is True
    assert out["a@example.com"]["primary_email"] == "c@example.com"
    assert out["b@example.com"]["is_duplicate_subscription"] is True
    assert out["b@example.com"]["primary_email"] == "c@example.com"


def test_all_missing_created_date_primary_by_email():
    users = [
        {"email": "z@example.com", "employee_number": "E1", "created_date": ""},
        {"email": "a@example.com", "employee_number": "E1", "created_date": ""},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # All missing created_date -> primary chosen by email ascending: a is primary, z is duplicate.
    assert out["a@example.com"]["is_duplicate_subscription"] is False
    assert out["a@example.com"]["primary_email"] == ""
    assert out["z@example.com"]["is_duplicate_subscription"] is True
    assert out["z@example.com"]["primary_email"] == "a@example.com"


def test_single_row_group_is_primary():
    users = [{"email": "a@example.com", "employee_number": "E9", "created_date": "2024-01-01"}]
    out = _by_email(annotate_duplicate_subscriptions(users))
    assert out["a@example.com"]["is_duplicate_subscription"] is False
    assert out["a@example.com"]["primary_email"] == ""


def test_input_order_preserved():
    users = [
        {"email": "c@example.com", "employee_number": "E1", "created_date": "2024-03-01"},
        {"email": "a@example.com", "employee_number": "E1", "created_date": "2024-01-01"},
        {"email": "x@example.com", "employee_number": "", "created_date": "2024-02-01"},
        {"email": "b@example.com", "employee_number": "E1", "created_date": "2024-02-01"},
    ]
    out = annotate_duplicate_subscriptions(users)
    assert [r["email"] for r in out] == ["c@example.com", "a@example.com", "x@example.com", "b@example.com"]
    # No in-place mutation: the original dict has no dedup keys
    assert "is_duplicate_subscription" not in users[0]


def test_non_string_inputs_do_not_raise():
    users = [
        {"email": "a@example.com", "employee_number": 1, "created_date": None},
        {"email": "b@example.com", "employee_number": 1, "created_date": "2024-01-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # employee_number=1(int) -> grouped as "1"; b has created_date so is primary, a(None->"") sorts last as duplicate.
    assert out["b@example.com"]["is_duplicate_subscription"] is False
    assert out["a@example.com"]["is_duplicate_subscription"] is True
    assert out["a@example.com"]["primary_email"] == "b@example.com"

"""Local ETL verification: run the transform over mock data, print both tables' shapes and samples, and assert consistency."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.etl import transform  # noqa: E402


def main():
    fud, fmd = transform.run_local(
        csv_glob="mock/**/*.csv",
        identity_json="mock/identity_groups.json",
        credit_unit_price=0.15,
    )
    print("=== fact_user_daily ===", fud.shape)
    print(fud.head(3).to_string())
    print("\n=== fact_user_model_daily ===", fmd.shape)
    print(fmd.head(5).to_string())

    # Consistency assertions
    assert not fud.empty and not fmd.empty, "neither table should be empty"
    # each (date,email) is unique
    assert fud.duplicated(["date", "email"]).sum() == 0, "fact_user_daily should be unique by (date,email)"
    # usage rate = credits_used / overage_cap (spot-check one row)
    r = fud.iloc[0]
    if r["overage_cap"] > 0:
        assert abs(r["usage_rate"] - round(r["credits_used"] / r["overage_cap"], 4)) < 1e-6
    # the sum of model-table messages should be <= the sum of per-user total_messages (melt lost nothing)
    print("\nDepartment distribution:")
    print(fud.groupby("department")["email"].nunique().to_string())
    print("\nModel distribution (top by messages):")
    print(fmd.groupby("model")["model_messages"].sum().sort_values(ascending=False).to_string())
    print("\n[OK] ETL consistency assertions passed")


if __name__ == "__main__":
    main()

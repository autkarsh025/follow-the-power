"""SEC quarter derivation: direct quarters, YTD differencing, Q4 from the 10-K, restatements, point-in-time."""
import pandas as pd
import pytest

from ftp import sec


def fact(start, end, val, filed, form="10-Q", accn="a"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form, "accn": accn}


def facts(concept, items):
    return {"facts": {"us-gaap": {concept: {"units": {"USD": items}}}}}


# Fiscal year 2024 = calendar 2024. Quarters: 100, 120, 140, 160 (FY = 520).
INCOME_STYLE = [  # revenue: 10-Qs report the quarter AND year-to-date; Q4 only inside the 10-K total
    fact("2024-01-01", "2024-03-31", 100, "2024-04-25"),
    fact("2024-04-01", "2024-06-30", 120, "2024-07-25"), fact("2024-01-01", "2024-06-30", 220, "2024-07-25"),
    fact("2024-07-01", "2024-09-30", 140, "2024-10-25"), fact("2024-01-01", "2024-09-30", 360, "2024-10-25"),
    fact("2024-01-01", "2024-12-31", 520, "2025-02-20", form="10-K"),
]
CASHFLOW_STYLE = [  # capex: only year-to-date (3, 6, 9, 12 months)
    fact("2024-01-01", "2024-03-31", 10, "2024-04-25"),
    fact("2024-01-01", "2024-06-30", 25, "2024-07-25"),
    fact("2024-01-01", "2024-09-30", 45, "2024-10-25"),
    fact("2024-01-01", "2024-12-31", 70, "2025-02-20", form="10-K"),
]


def values(df):
    latest = sec.as_of(df, "2030-01-01").sort_values("fiscal_q")
    return dict(zip(latest["fiscal_q"], latest["value_usd"]))


def test_income_statement_style_with_q4_from_10k():
    df = sec.quarterly(facts("Revenues", INCOME_STYLE), "NVDA", "revenue")
    assert values(df) == {1: 100, 2: 120, 3: 140, 4: 160}
    q4 = df[df["fiscal_q"] == 4].iloc[0]
    assert q4["derived"] == 1 and q4["filed_date"] == "2025-02-20"


def test_cash_flow_ytd_style_is_differenced():
    df = sec.quarterly(facts("PaymentsToAcquirePropertyPlantAndEquipment", CASHFLOW_STYLE), "MSFT", "capex")
    assert values(df) == {1: 10, 2: 15, 3: 20, 4: 25}


def test_falls_back_to_second_concept():
    df = sec.quarterly(facts("PaymentsToAcquireProductiveAssets", CASHFLOW_STYLE), "AMZN", "capex")
    assert df["concept"].iloc[0] == "PaymentsToAcquireProductiveAssets"


def test_restatement_is_a_new_version_and_point_in_time_respects_it():
    items = INCOME_STYLE + [fact("2024-01-01", "2024-03-31", 105, "2025-04-25", accn="b")]  # Q1 restated a year later
    df = sec.quarterly(facts("Revenues", items), "NVDA", "revenue")
    q1 = df[df["fiscal_q"] == 1].sort_values("filed_date")
    assert list(q1["value_usd"]) == [100, 105]
    assert values(df)[1] == 105                                                     # latest view
    early = sec.as_of(df, "2024-12-31")
    assert early[early["fiscal_q"] == 1]["value_usd"].iloc[0] == 100                # what was known then
    assert 4 not in set(early["fiscal_q"])                                          # Q4 wasn't public yet


def test_same_value_refiled_keeps_first_date():
    items = INCOME_STYLE + [fact("2024-01-01", "2024-03-31", 100, "2025-04-25", accn="b")]  # comparative re-report
    df = sec.quarterly(facts("Revenues", items), "NVDA", "revenue")
    q1 = df[df["fiscal_q"] == 1]
    assert len(q1) == 1 and q1["filed_date"].iloc[0] == "2024-04-25"


def test_q4_without_9m_ytd_uses_sum_of_quarters():
    items = [i for i in INCOME_STYLE if not (i["start"] == "2024-01-01" and i["end"] == "2024-09-30")]
    df = sec.quarterly(facts("Revenues", items), "NVDA", "revenue")
    assert values(df)[4] == 160


def test_current_fiscal_year_without_10k_is_included():
    # FY2025 is still in progress: only Q1 and the 6-month YTD have been filed.
    items = INCOME_STYLE + [
        fact("2025-01-01", "2025-03-31", 200, "2025-04-25"),
        fact("2025-04-01", "2025-06-30", 210, "2025-07-25"), fact("2025-01-01", "2025-06-30", 410, "2025-07-25"),
    ]
    df = sec.quarterly(facts("Revenues", items), "NVDA", "revenue")
    fy25 = sec.as_of(df, "2030-01-01").query("fiscal_year == 2025").sort_values("fiscal_q")
    assert list(fy25["value_usd"]) == [200, 210]

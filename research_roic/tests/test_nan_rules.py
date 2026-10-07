"""Missing stays missing: nothing is filled with zero and nothing becomes inf."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import levels
from src.data_io import safe_div
from tests.conftest import build, make_source


def test_zero_denominator_is_nan_not_inf(cfg, seed):
    """Zero pretax income, zero average IC and zero equity all give NaN."""
    src = make_source(seed)
    src.loc[src.index[5], "piq"] = 0.0
    src.loc[src.index[6], ["actq", "lctq", "ppentq", "gdwlq"]] = [100.0, 100.0, 0.0, 0.0]
    src.loc[src.index[7], ["actq", "lctq", "ppentq", "gdwlq"]] = [100.0, 100.0, 0.0, 0.0]
    src.loc[src.index[8], ["atq", "ltq"]] = [1000.0, 1000.0]
    panel = build(cfg, src)

    assert pd.isna(panel.loc[5, "calc_raw_tax_rate_quarterly"])
    assert panel.loc[7, "calc_average_ic_raw_quarterly"] == 0.0
    assert pd.isna(panel.loc[7, "calc_roic_posttax_quarterly_ic_raw"])
    assert panel.loc[8, "equity"] == 0.0
    assert pd.isna(panel.loc[8, "calc_debt_to_equity_quarterly"])

    floats = [c for c in panel.columns if panel[c].dtype == "float64"]
    assert not any(np.isinf(panel[c].to_numpy()).any() for c in floats)


def test_safe_div_never_returns_inf():
    out = safe_div(np.array([1.0, 1.0, np.nan, 0.0]), np.array([0.0, np.nan, 2.0, 0.0]))
    assert np.isnan(out).all()


def test_working_capital_is_mandatory(cfg, seed):
    """No current assets or liabilities means no invested capital, not PPE+goodwill."""
    src = make_source(seed)
    src.loc[src.index[3], "actq"] = np.nan
    src.loc[src.index[4], "lctq"] = np.nan
    panel = build(cfg, src)
    assert pd.isna(panel.loc[3, "calc_ic_raw"])
    assert pd.isna(panel.loc[4, "calc_ic_raw"])


def test_missing_ppe_and_goodwill_count_as_zero(cfg, seed):
    """A single missing line should not delete a whole quarter."""
    src = make_source(seed)
    row = src.index[2]
    src.loc[row, ["ppentq", "gdwlq"]] = [np.nan, np.nan]
    panel = build(cfg, src)
    expected = src.loc[row, "actq"] - src.loc[row, "lctq"]
    assert panel.loc[2, "calc_ic_raw"] == expected


def test_debt_missing_leg_is_zero_both_missing_is_nan():
    assert levels.debt_value(np.array([np.nan]), np.array([5.0]))[0] == 5.0
    assert levels.debt_value(np.array([3.0]), np.array([np.nan]))[0] == 3.0
    assert np.isnan(levels.debt_value(np.array([np.nan]), np.array([np.nan]))[0])


def test_any_missing_factor_blanks_all_three_contributions(cfg, seed):
    """One missing input blanks the whole ROIC bridge for that row."""
    src = make_source(seed)
    src.loc[src.index[4], "oiadpq"] = np.nan
    panel = build(cfg, src)
    for col in ("calc_roic_ebit_contribution", "calc_roic_tax_contribution",
                "calc_roic_ic_contribution", "calc_roic_posttax_change_quarterly"):
        assert pd.isna(panel.loc[4, col]), col
        assert pd.isna(panel.loc[5, col]), f"{col} at t+1"


def test_total_absolute_contribution_has_min_count_three(cfg, seed):
    """One NaN contribution gives NaN, not a two-term sum."""
    src = make_source(seed)
    src.loc[src.index[4], "piq"] = np.nan
    panel = build(cfg, src)
    assert pd.isna(panel.loc[4, "calc_roic_total_absolute_contribution"])


def test_real_no_derived_column_has_inf(real_panel):
    panel, _ = real_panel
    floats = [c for c in panel.columns if panel[c].dtype == "float64"]
    offenders = [c for c in floats if np.isinf(panel[c].to_numpy()).any()]
    assert offenders == []


def test_real_disabled_columns_are_entirely_empty(real_panel):
    """The three FCF columns exist in the schema and hold nothing at all."""
    panel, _ = real_panel
    for col in ("free_cash_flow", "calc_free_cash_flow_ttm", "calc_ev_to_fcf_quarterly"):
        assert col in panel.columns
        assert int(panel[col].notna().sum()) == 0, col

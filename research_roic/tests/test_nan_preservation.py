"""Missing values stay NaN: nothing is filled with zero, tickers and dates are not guessed."""
import re

import numpy as np
import pandas as pd
import pytest

from src import data_io
from tests.conftest import build_features


def test_nan_inputs_propagate(cfg, synthetic):
    df = synthetic.copy()
    m = (df["symbol"] == "CCC") & (df["period_key"] == "2019Q2")
    df.loc[m, ["calc_nopat_quarterly", "calc_roic_ebit_contribution"]] = np.nan
    df.loc[m, "calc_roic_posttax_quarterly_ic_raw"] = np.nan
    out = build_features(cfg, df).set_index(["symbol", "period_key"])
    assert np.isnan(out.loc[("CCC", "2019Q2"), "roic_posttax_q"])
    assert np.isnan(out.loc[("CCC", "2019Q2"), "ebit_contribution_sum_4q"])
    assert np.isnan(out.loc[("CCC", "2019Q3"), "ebit_contribution_trend_4q"])


def test_zero_denominator_is_nan_not_inf(cfg, synthetic):
    df = synthetic.copy()
    m = (df["symbol"] == "AAA") & (df["period_key"] == "2018Q2")
    df.loc[m, "calc_average_ic_raw_quarterly"] = 0.0
    out = build_features(cfg, df).set_index(["symbol", "period_key"])
    assert np.isnan(out.loc[("AAA", "2018Q2"), "roic_posttax_q"])
    assert not np.isinf(out[["roic_posttax_q", "roic_pretax_q", "roic_trend_4q"]].to_numpy()).any()


def test_loader_keeps_nan_tickers_and_reports_bad_dates(cfg, tmp_path):
    csv = tmp_path / "mini.csv"
    csv.write_text(
        "KEY,symbol,period_key,fiscal_period_end_date,filing_date,run_date,ebit,market_cap.1,market_cap\n"
        "2020Q1_NA,NA,2020Q1,2020-03-31,,2026-09-05,,5,5\n"
        "2020Q2_NA,NA,2020Q2,31/06/2020,2020-08-01,2026-09-05,1.5,,\n"
        "2020Q3_NULL,NULL,2020Q3,2020-09-30,bad-date,2026-09-05,0,7,7\n", encoding="utf-8")
    local = dict(cfg)
    local["_paths"] = {**cfg["_paths"], "panel_csv": csv}
    local["io"] = {**cfg["io"], "boolean_columns": []}
    df, rep = data_io.load_panel(local)
    assert df["symbol"].tolist() == ["NA", "NA", "NULL"]
    assert np.isnan(df.loc[0, "ebit"]) and df.loc[2, "ebit"] == 0
    assert "market_cap.1" not in df.columns and np.isnan(df.loc[1, "market_cap"])
    assert rep["dates"]["fiscal_period_end_date"]["n_unparsable"] == 1          # 31/06 does not exist -> reported
    assert pd.isna(df.loc[1, "fiscal_period_end_date"])
    assert rep["dates"]["filing_date"]["n_unparsable"] == 1 and rep["dates"]["filing_date"]["n_missing_or_empty"] == 1


def test_real_no_new_zeros_or_fills(real_panel, raw_csv):
    """Every original numeric column keeps its NaN count; no zero appears where the CSV had NaN."""
    raw = raw_csv.set_index("KEY")
    prep = real_panel.set_index("KEY").loc[raw.index]
    floats = [c for c in raw.columns if pd.api.types.is_float_dtype(raw[c])]
    numeric = [c for c in floats if c in prep.columns]
    dropped = set(floats) - set(numeric)
    assert all(re.fullmatch(r".+\.\d+", c) for c in dropped), f"only identical duplicate columns may be absent: {dropped}"
    for col in numeric:
        assert int(prep[col].isna().sum()) == int(raw[col].isna().sum()), col
        assert int(((prep[col] == 0) & raw[col].isna()).sum()) == 0, col


def test_real_derived_levels_keep_file_nan_pattern(real_panel):
    assert (real_panel["roic_posttax_q"].isna() == real_panel["calc_roic_posttax_quarterly_ic_raw"].isna()).all()
    assert (real_panel["roic_pretax_q"].isna() == real_panel["calc_roic_pretax_quarterly_ic_raw"].isna()).all()


def test_real_unrealized_returns_are_nan_not_zero(real_panel):
    un = real_panel["fwd_return_window_unrealized"]
    assert un.any()
    assert real_panel.loc[un, "fwd_return"].isna().all()
    assert (real_panel.loc[un, "Future_Quarterly_Return"].fillna(0) == 0).all()   # documents the source zero-coding
    assert not (real_panel["momentum_3q"] == 0).any()


def test_real_zero_coded_market_fields(real_panel):
    assert (real_panel["market_cap"] == 0).any()                      # documents the source zero-coding
    assert not (real_panel["market_cap_clean"] == 0).any()
    assert not (real_panel["price_clean"] == 0).any()
    same = real_panel["market_cap"] != 0
    assert ((real_panel.loc[same, "market_cap_clean"] == real_panel.loc[same, "market_cap"])
            | real_panel.loc[same, "market_cap"].isna()).all()


def test_real_features_not_zero_filled(real_panel):
    d = real_panel[real_panel["in_population"]]
    assert d["roic_trend_4q"].isna().any()
    assert (d["roic_trend_4q"] == 0).sum() == 0

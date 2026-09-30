"""Trend windows: OLS slope on x = 0..3 only over four present, consecutive quarters."""
import numpy as np
import pandas as pd
import pytest

from src import features
from tests.conftest import build_features, make_synthetic_panel


def test_slope_matches_polyfit(cfg, synthetic):
    out = build_features(cfg, synthetic)
    for sym, grp in out.groupby("symbol"):
        y = grp["roic_posttax_q"].to_numpy()
        for i in range(3, len(grp)):
            expected = np.polyfit(np.arange(4), y[i - 3:i + 1], 1)[0]
            assert grp["roic_trend_4q"].iloc[i] == pytest.approx(expected, rel=1e-10, abs=1e-14)
        assert grp["roic_trend_4q"].iloc[:3].isna().all(), "first three quarters of a firm cannot have a 4q slope"


def test_slope_of_exact_line(cfg, seed):
    df = make_synthetic_panel(seed, symbols=("LIN",), n_quarters=6)
    df["calc_nopat_quarterly"] = df["calc_average_ic_raw_quarterly"] * (0.02 + 0.005 * np.arange(6))
    out = build_features(cfg, df)
    assert np.allclose(out["roic_trend_4q"].iloc[3:], 0.005)


def test_missing_value_in_window_gives_nan(cfg, synthetic):
    df = synthetic.copy()
    idx = df.index[(df["symbol"] == "AAA") & (df["period_key"] == "2018Q3")]
    df.loc[idx, "calc_nopat_quarterly"] = np.nan
    out = build_features(cfg, df).set_index(["symbol", "period_key"])
    for pk in ("2018Q3", "2018Q4", "2019Q1", "2019Q2"):          # every window t-3..t that contains 2018Q3
        assert np.isnan(out.loc[("AAA", pk), "roic_trend_4q"])
    assert not np.isnan(out.loc[("AAA", "2019Q3"), "roic_trend_4q"])
    assert not np.isnan(out.loc[("BBB", "2019Q1"), "roic_trend_4q"])


def test_period_gap_breaks_window(cfg, synthetic):
    df = synthetic[~((synthetic["symbol"] == "AAA") & (synthetic["period_key"] == "2018Q3"))].copy()
    out = build_features(cfg, df).set_index(["symbol", "period_key"])
    assert not out.loc[("AAA", "2018Q4"), "is_consecutive_prev"]
    for pk in ("2018Q4", "2019Q1", "2019Q2"):
        assert np.isnan(out.loc[("AAA", pk), "roic_trend_4q"])
    assert not np.isnan(out.loc[("AAA", "2019Q3"), "roic_trend_4q"])   # 2018Q4..2019Q3 is a clean run


def test_day_gap_outside_range_breaks_window(cfg, synthetic):
    df = synthetic.copy()
    m = (df["symbol"] == "BBB") & (df["period_key"] == "2018Q2")
    df.loc[m, "fiscal_period_end_date"] = df.loc[m, "fiscal_period_end_date"] - pd.Timedelta(days=45)   # 46-day gap
    out = build_features(cfg, df).set_index(["symbol", "period_key"])
    assert not out.loc[("BBB", "2018Q2"), "is_consecutive_prev"]
    assert np.isnan(out.loc[("BBB", "2019Q1"), "roic_trend_4q"])


def test_windows_do_not_cross_firms(cfg, synthetic):
    out = build_features(cfg, synthetic)
    first_rows = out.groupby("symbol").head(3)
    assert first_rows["roic_trend_4q"].isna().all()
    assert first_rows["ebit_contribution_sum_4q"].isna().all()


def test_sum_and_mean(cfg, synthetic):
    out = build_features(cfg, synthetic)
    g = out.groupby("symbol")["calc_roic_ic_contribution"]
    expected = sum(g.shift(k) for k in range(4))
    ok = out["ic_contribution_sum_4q"].notna()
    assert ok.sum() == len(out) - 3 * out["symbol"].nunique()
    assert np.allclose(out.loc[ok, "ic_contribution_sum_4q"], expected[ok])
    assert np.allclose(out.loc[ok, "ic_contribution_mean_4q"], expected[ok] / 4)


def test_slope_weights():
    assert np.allclose(features.ols_slope_weights(4), [-0.3, -0.1, 0.1, 0.3])


def test_real_windows_are_consecutive(real_panel):
    d = real_panel
    g = d.groupby("symbol", sort=False)
    has = d["roic_trend_4q"].notna()
    assert has.any()
    for k in (1, 2, 3):
        assert ((d["q_ord"] - g["q_ord"].shift(k))[has] == k).all()
    assert d.loc[has, "window_ok_4q"].all()
    assert d.loc[d["ebit_contribution_trend_4q"].notna(), "window_ok_4q"].all()


def test_real_contribution_sums_add_up_to_4q_roic_change(real_panel):
    """Shapley efficiency carried over the window: sum of all contributions over t-3..t = ROIC[t] - ROIC[t-4]."""
    d = real_panel
    total = d["ebit_contribution_sum_4q"] + d["tax_contribution_sum_4q"] + d["ic_contribution_sum_4q"]
    ok = total.notna() & d["roic_change_4q"].notna()
    dev = (total - d["roic_change_4q"])[ok].abs()
    assert ok.sum() > 5000
    assert dev.median() < 1e-6          # CSV rounds contributions to 1e-6
    assert (dev < 1e-4).mean() > 0.99   # tail = rounding of NOPAT / avg IC for tiny capital bases

"""No future information enters signals, eligibility or the population."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src import pipeline
from tests.conftest import build_features

FEATURE_COLS = ["roic_pretax_q", "roic_posttax_q", "roic_trend_4q", "ebit_contribution_trend_4q", "tax_contribution_trend_4q",
                "ic_contribution_trend_4q", "ebit_contribution_sum_4q", "ic_contribution_mean_4q", "roic_change_4q",
                "avg_ic_change_4q", "avg_ic_pct_change_4q", "avg_ic_trend_4q_rel", "n_econ_valid_4q"]


def _equal(a: pd.Series, b: pd.Series) -> bool:
    return bool(((a == b) | (a.isna() & b.isna())).all())


def test_future_rows_do_not_change_past_features(cfg, synthetic, seed):
    base = build_features(cfg, synthetic).set_index("KEY")
    cutoff = pd.Period("2019Q1", "Q")
    rng = np.random.default_rng(seed + 1)
    shocked = synthetic.copy()
    future = shocked["period"] > cutoff
    for col in ("ebit", "calc_nopat_quarterly", "calc_average_ic_raw_quarterly", "calc_roic_ebit_contribution",
                "calc_roic_tax_contribution", "calc_roic_ic_contribution"):
        shocked.loc[future, col] = rng.normal(0, 1e6, future.sum())
    out = build_features(cfg, shocked).set_index("KEY")
    past = base.index[base["period"] <= cutoff]
    for col in FEATURE_COLS:
        assert _equal(base.loc[past, col], out.loc[past, col]), col


def test_returns_do_not_affect_signals_or_population(cfg, real_inputs, real_panel, seed):
    panel_raw, rel, _ = real_inputs
    rng = np.random.default_rng(seed)
    shuffled = panel_raw.copy()
    shuffled["Future_Quarterly_Return"] = rng.normal(0, 1, len(shuffled))
    shuffled.loc[rng.random(len(shuffled)) < 0.3, "Future_Quarterly_Return"] = np.nan
    shuffled["Momentum_3Q"] = rng.normal(0, 1, len(shuffled))
    alt, _ = pipeline.build_research_panel(cfg, panel_raw=shuffled, relevance=rel)
    a, b = real_panel.set_index("KEY"), alt.set_index("KEY").loc[real_panel["KEY"]]
    for col in FEATURE_COLS + ["in_index", "in_population", "has_level_and_trend_signals", "signal_lag_below_min"]:
        assert _equal(a[col], b[col]), col
    assert not _equal(a["in_evaluation_sample"], b["in_evaluation_sample"])   # only the evaluation layer may move


def test_signal_modules_never_reference_returns():
    src = Path(__file__).resolve().parents[1] / "src"
    for name in ("features.py", "eligibility.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert not re.search(r"Future_Quarterly_Return|fwd_return|forward_return|Momentum_3Q|momentum", text), name


def test_timing_of_returns(real_panel):
    d = real_panel[~real_panel["is_benchmark"]]
    assert (d["formation_date"] >= d["fiscal_period_end_date"]).all()
    assert (d["formation_date"] > d["period"].dt.end_time.dt.normalize()).all()
    months = (d["return_window_end"].dt.year * 12 + d["return_window_end"].dt.month) - (d["formation_date"].dt.year * 12 + d["formation_date"].dt.month)
    assert (months == 3).all()
    assert d.loc[d["fwd_return"].notna(), "return_window_end"].max() <= pd.Timestamp("2026-09-05")


def test_momentum_uses_only_past_returns(real_panel):
    d = real_panel[~real_panel["is_benchmark"]]
    g = d.groupby("symbol", sort=False)
    prev3 = (d["prev_key_gap"] == 1) & (g["prev_key_gap"].shift(1) == 1) & (g["prev_key_gap"].shift(2) == 1)
    r = g["Future_Quarterly_Return"]
    built = (1 + r.shift(1)) * (1 + r.shift(2)) * (1 + r.shift(3)) - 1
    ok = prev3.fillna(False) & built.notna() & d["momentum_3q"].notna()
    assert ok.sum() > 15000
    assert np.allclose(d.loc[ok, "momentum_3q"], built[ok], atol=1e-6)

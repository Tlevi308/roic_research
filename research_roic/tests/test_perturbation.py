"""Invariances: no leakage across firms or from the future, and scale-free labels."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import schema
from tests.conftest import build, make_source

MONEY_FIELDS = ("actq", "lctq", "ppentq", "gdwlq", "atq", "ltq",
                "oiadpq", "piq", "txtq", "dlcq", "dlttq", "mkvaltq")


def test_one_firm_at_a_time_equals_the_full_run(cfg, seed):
    """No window leaks across firms."""
    src = make_source(seed, tickers=("AAA", "BBB", "CCC"))
    whole = build(cfg, src)
    parts = [build(cfg, src[src.tic == t].reset_index(drop=True)) for t in ("AAA", "BBB", "CCC")]
    joined = pd.concat(parts, ignore_index=True)
    whole = whole.sort_values("KEY").reset_index(drop=True)
    joined = joined.sort_values("KEY").reset_index(drop=True)
    for col in schema.PANEL_COLUMNS:
        if col == "run_date":
            continue
        a, b = whole[col].astype("string"), joined[col].astype("string")
        assert a.equals(b), col


def test_future_rows_do_not_change_past_columns(cfg, seed):
    """Randomising everything after a cutoff leaves earlier rows untouched."""
    rng = np.random.default_rng(seed + 1)
    src = make_source(seed, tickers=("AAA", "BBB"), n_quarters=12)
    base = build(cfg, src)
    cutoff = pd.Timestamp("2019-06-30")

    shocked = src.copy()
    future = shocked["datadate"] > cutoff
    for col in MONEY_FIELDS:
        shocked.loc[future, col] = rng.normal(0, 1e6, int(future.sum()))
    after = build(cfg, shocked)

    keep = pd.to_datetime(base["fiscal_period_end_date"]) <= cutoff
    for col in schema.PANEL_COLUMNS:
        if col == "run_date":
            continue
        a = base.loc[keep, col].astype("string")
        b = after.loc[keep.to_numpy(), col].astype("string")
        assert a.reset_index(drop=True).equals(b.reset_index(drop=True)), col


def test_scaling_every_money_field_leaves_ratios_and_labels_unchanged(cfg, seed):
    """The money bands scale with the data, so materiality is unit-free."""
    src = make_source(seed, tickers=("AAA", "BBB"), n_quarters=10)
    base = build(cfg, src)
    scaled_src = src.copy()
    for col in MONEY_FIELDS:
        scaled_src[col] = scaled_src[col] * 1000.0
    scaled = build(cfg, scaled_src)

    for col in schema.LABEL_LEVELS:
        assert base[col].astype("string").equals(scaled[col].astype("string")), col
    for col in ("calc_raw_tax_rate_quarterly", "calc_roic_posttax_quarterly_ic_raw",
                "calc_roic_ebit_absolute_share", "calc_roic_tax_absolute_share",
                "calc_roic_ic_absolute_share", "calc_roic_offset_ratio",
                "calc_debt_to_equity_quarterly"):
        a = base[col].to_numpy()
        b = scaled[col].to_numpy()
        assert np.allclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True), col
    assert base["calc_roic_explanation"].astype("string").equals(
        scaled["calc_roic_explanation"].astype("string"))


def test_input_row_order_does_not_change_the_output(cfg, seed):
    """Firms may arrive in any order; the panel sorts them itself."""
    src = make_source(seed, tickers=("AAA", "BBB", "CCC"))
    forward = build(cfg, src).sort_values("KEY").reset_index(drop=True)
    reversed_ = build(cfg, src.iloc[::-1].reset_index(drop=True)) \
        .sort_values("KEY").reset_index(drop=True)
    for col in schema.PANEL_COLUMNS:
        if col == "run_date":
            continue
        assert forward[col].astype("string").equals(reversed_[col].astype("string")), col


def test_a_sign_flip_on_the_tax_rate_flips_the_effect_not_the_movement(cfg, seed):
    """The effect reads the contribution's sign; the movement reads the rate."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    src.loc[src.index[3], "txtq"] = src.loc[src.index[2], "txtq"] * 3
    panel = build(cfg, src)
    assert panel.loc[3, "calc_tax_rate_movement"] == "UP"
    assert panel.loc[3, "calc_nopat_tax_effect"] == "NEGATIVE"

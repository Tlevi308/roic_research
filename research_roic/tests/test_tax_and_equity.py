"""The tax sign, the unclipped rate, and equity from the balance sheet."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import levels, validation
from tests.conftest import build, make_source

# measured on data/data.parquet as of 2026-10-05
TAX_PRESENT = 1_540_896
TAX_NEGATIVE = 208_851
RATE_DEFINED = 1_532_010
RATE_NEGATIVE = 149_251
RATE_ABOVE_ONE = 21_892
BENEFIT_ON_PROFIT = 45_861
TAX_ON_LOSS = 103_390
EQUITY_ZERO = 854
EQUITY_NEGATIVE = 121_389


def test_tax_expense_is_txtq_with_no_sign_flip(cfg, seed):
    """GuruFocus reports the provision negative; Compustat does not, so nothing flips."""
    src = make_source(seed)
    src.loc[src.index[2], "txtq"] = -40.0          # a tax benefit
    panel = build(cfg, src)
    assert panel.loc[2, "calc_tax_expense_quarterly"] == -40.0
    assert np.allclose(panel["calc_tax_expense_quarterly"].to_numpy(),
                       src["txtq"].to_numpy())


def test_rate_is_never_clipped(cfg, seed):
    """A negative rate and a rate above 100% are kept and flagged, not trimmed."""
    src = make_source(seed)
    src.loc[src.index[2], ["txtq", "piq"]] = [-20.0, 100.0]     # benefit on a profit
    src.loc[src.index[3], ["txtq", "piq"]] = [20.0, -100.0]     # tax on a loss
    src.loc[src.index[4], ["txtq", "piq"]] = [150.0, 100.0]     # above 100%
    panel = build(cfg, src)
    assert panel.loc[2, "calc_raw_tax_rate_quarterly"] == -0.2
    assert panel.loc[3, "calc_raw_tax_rate_quarterly"] == -0.2
    assert panel.loc[4, "calc_raw_tax_rate_quarterly"] == 1.5
    assert panel.loc[2, "calc_tax_rate_quality_flag"] == "NEGATIVE_TAX_RATE"
    assert panel.loc[3, "calc_tax_rate_quality_flag"] == "NEGATIVE_TAX_RATE"
    assert panel.loc[4, "calc_tax_rate_quality_flag"] == "ABOVE_100_PERCENT"


def test_missing_rate_is_flagged_missing(cfg, seed):
    src = make_source(seed)
    src.loc[src.index[2], "piq"] = 0.0
    panel = build(cfg, src)
    assert panel.loc[2, "calc_tax_rate_quality_flag"] == "MISSING"


def test_roic_quality_flag_ladder(cfg, seed):
    """Negative capital is announced before an out-of-range tax rate."""
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    for row in (4, 5):
        src.loc[src.index[row], ["actq", "lctq", "ppentq", "gdwlq"]] = [10.0, 900.0, 0.0, 0.0]
    src.loc[src.index[5], ["txtq", "piq"]] = [-20.0, 100.0]
    panel = build(cfg, src)
    assert panel.loc[5, "calc_roic_quality_flag"] == "NEGATIVE_IC_MECHANICAL_ONLY"

    src2 = make_source(seed, tickers=("AAA",), n_quarters=8)
    src2.loc[src2.index[5], ["txtq", "piq"]] = [-20.0, 100.0]
    panel2 = build(cfg, src2)
    assert panel2.loc[5, "calc_roic_quality_flag"] == "TAX_RATE_OUT_OF_RANGE"


def test_equity_is_assets_minus_liabilities(cfg, seed):
    src = make_source(seed)
    panel = build(cfg, src)
    assert np.allclose(panel["equity"].to_numpy(),
                       (src["atq"] - src["ltq"]).to_numpy())


def test_equity_helper_is_a_plain_difference():
    out = levels.equity_from_balance_sheet(np.array([100.0, np.nan, 50.0]),
                                           np.array([40.0, 10.0, np.nan]))
    assert out[0] == 60.0
    assert np.isnan(out[1]) and np.isnan(out[2])


def test_zero_equity_gives_nan_debt_to_equity(cfg, seed):
    src = make_source(seed)
    src.loc[src.index[3], ["atq", "ltq"]] = [500.0, 500.0]
    panel = build(cfg, src)
    assert panel.loc[3, "equity"] == 0.0
    assert pd.isna(panel.loc[3, "calc_debt_to_equity_quarterly"])


def test_negative_equity_gives_a_negative_ratio_not_a_filter(cfg, seed):
    src = make_source(seed)
    src.loc[src.index[3], ["atq", "ltq"]] = [500.0, 900.0]
    panel = build(cfg, src)
    assert panel.loc[3, "equity"] == -400.0
    assert panel.loc[3, "calc_debt_to_equity_quarterly"] < 0


def test_real_tax_census_matches_the_measurement(real_panel):
    panel, _ = real_panel
    c = validation.tax_sign_census(panel).set_index("measure")["rows"]
    assert c["tax expense present"] == TAX_PRESENT
    assert c["tax expense < 0 (benefit)"] == TAX_NEGATIVE
    assert c["tax rate defined"] == RATE_DEFINED
    assert c["tax rate < 0"] == RATE_NEGATIVE
    assert c["tax rate > 1 (above 100%)"] == RATE_ABOVE_ONE
    benefit = c["negative rate: benefit on a profit (tax<0, pretax>0)"]
    on_loss = c["negative rate: tax on a pretax loss (tax>0, pretax<0)"]
    assert (benefit, on_loss) == (BENEFIT_ON_PROFIT, TAX_ON_LOSS)
    assert benefit + on_loss == RATE_NEGATIVE        # the split is exhaustive


def test_real_nothing_was_clipped(real_panel):
    """The out-of-range rates are still there, by the thousand."""
    panel, _ = real_panel
    rate = panel["calc_raw_tax_rate_quarterly"].to_numpy()
    assert int((rate > 1).sum()) == RATE_ABOVE_ONE
    assert int((rate < 0).sum()) == RATE_NEGATIVE


def test_real_equity_census_matches_the_measurement(real_panel):
    panel, _ = real_panel
    c = validation.equity_sign_census(panel).set_index("measure")["rows"]
    assert c["equity == 0 (D/E is NaN by the denominator rule)"] == EQUITY_ZERO
    assert c["equity < 0 (D/E negative and mechanical)"] == EQUITY_NEGATIVE


def test_real_negative_tax_rate_flag_is_exact(real_panel):
    panel, _ = real_panel
    rate = panel["calc_raw_tax_rate_quarterly"].to_numpy()
    flag = panel["calc_tax_rate_quality_flag"].astype("string")
    assert int((flag == "NEGATIVE_TAX_RATE").sum()) == int((rate < 0).sum())
    assert ((rate < 0) == (flag == "NEGATIVE_TAX_RATE").to_numpy()).all()

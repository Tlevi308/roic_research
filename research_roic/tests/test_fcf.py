"""FCFE from quarterly items: the formula, the term-wise fill, and the TTM window."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import levels
from src.data_io import safe_div
from tests.conftest import build, build_with_report, make_source


def _hand_fcfe(src: pd.DataFrame, row: int) -> float:
    """The four steps of the specification, written out by hand for one row."""
    def nwc(i):
        r = src.iloc[i]
        return (r.actq - r.cheq) - (r.lctq - r.dlcq)
    d_nwc = nwc(row) - nwc(row - 1)
    d_ppe = src.iloc[row].ppentq - src.iloc[row - 1].ppentq
    return src.iloc[row].ibq - d_nwc - d_ppe


# ---------------------------------------------------------------- the formula
def test_fcfe_matches_the_formula_by_hand(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    panel = build(cfg, src)
    for row in range(1, 8):
        assert np.isclose(panel.loc[row, "free_cash_flow_quarterly"],
                          _hand_fcfe(src, row), rtol=1e-12), row


def test_the_six_terms_carry_the_right_signs(cfg, seed):
    """FCFE = ibq - dNWC - dPPENT expands to +ibq -dACT +dCHE +dLCT -dDLC -dPPE."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    base = build(cfg, src).loc[2, "free_cash_flow_quarterly"]
    for field, sign in (("actq", -1), ("cheq", +1), ("lctq", +1),
                        ("dlcq", -1), ("ppentq", -1), ("ibq", +1)):
        bumped = src.copy()
        bumped.loc[bumped.index[2], field] += 100.0
        got = build(cfg, bumped).loc[2, "free_cash_flow_quarterly"]
        assert np.isclose(got - base, sign * 100.0, rtol=1e-9), field


def test_first_quarter_of_a_run_keeps_only_the_income_term(cfg, seed):
    """No previous quarter means every difference is omitted, so FCFE = ibq."""
    src = make_source(seed, tickers=("AAA", "BBB"), n_quarters=6)
    panel = build(cfg, src)
    firsts = panel.groupby("symbol", sort=False).head(1)
    for key, got in zip(firsts["KEY"], firsts["free_cash_flow_quarterly"]):
        ticker = key.split("_")[1]
        assert np.isclose(got, src.loc[src.tic == ticker, "ibq"].iloc[0], rtol=1e-12), key


# ------------------------------------------------- missing inputs do not drop
def test_a_missing_balance_leg_omits_only_that_term(cfg, seed):
    """Dropping cheq at t removes the cash term at t and t+1, and nothing else."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    base = build(cfg, src)
    holed = src.copy()
    holed.loc[holed.index[3], "cheq"] = np.nan
    panel = build(cfg, holed)

    assert pd.notna(panel.loc[3, "free_cash_flow_quarterly"])
    assert pd.notna(panel.loc[4, "free_cash_flow_quarterly"])
    # the cash term at t=3 was +(cheq_3 - cheq_2); removing it shifts the flow
    # by exactly that amount
    cash_term = src.iloc[3].cheq - src.iloc[2].cheq
    assert np.isclose(panel.loc[3, "free_cash_flow_quarterly"],
                      base.loc[3, "free_cash_flow_quarterly"] - cash_term, rtol=1e-9)
    # untouched quarters are bit-identical
    for row in (1, 2, 5):
        assert np.isclose(panel.loc[row, "free_cash_flow_quarterly"],
                          base.loc[row, "free_cash_flow_quarterly"], rtol=1e-12), row


def test_a_missing_difference_is_omitted_not_replaced_by_the_level(cfg, seed):
    """ppentq_t - 0 would read as buying the whole asset base in one quarter."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    level = src.iloc[3].ppentq
    src.loc[src.index[2], "ppentq"] = np.nan
    panel = build(cfg, src)
    got = panel.loc[3, "free_cash_flow_quarterly"]
    assert pd.notna(got)
    # the omitted term contributes nothing, so the flow stays the order of
    # magnitude of the income instead of swinging by the whole PPE balance
    assert abs(got) < level / 2


def test_missing_income_still_leaves_the_balance_terms(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    src.loc[src.index[3], "ibq"] = np.nan
    panel = build(cfg, src)
    got = panel.loc[3, "free_cash_flow_quarterly"]
    assert pd.notna(got)
    assert np.isclose(got, _hand_fcfe(src.fillna({"ibq": 0.0}), 3), rtol=1e-9)


def test_a_row_with_no_usable_term_at_all_is_nan(cfg, seed):
    """Zero out of nothing is not a cash flow of zero."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    for field in ("ibq", "actq", "cheq", "lctq", "dlcq", "ppentq"):
        src.loc[src.index[1], field] = np.nan
        src.loc[src.index[2], field] = np.nan
    panel = build(cfg, src)
    assert pd.isna(panel.loc[2, "free_cash_flow_quarterly"])
    assert pd.isna(panel.loc[2, "calc_p_to_fcf_quarterly"])


def test_a_genuine_zero_is_kept(cfg, seed):
    """A flat balance sheet with no income is a real flow of zero, not missing."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    for field in ("actq", "cheq", "lctq", "dlcq", "ppentq"):
        src[field] = 100.0
    src["ibq"] = 0.0
    panel = build(cfg, src)
    assert (panel["free_cash_flow_quarterly"] == 0).all()
    assert panel["free_cash_flow_quarterly"].notna().all()


def test_a_gap_omits_the_differences_but_keeps_the_income(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    src = src.drop(index=src.index[3]).reset_index(drop=True)
    panel = build(cfg, src)
    assert np.isclose(panel.loc[3, "free_cash_flow_quarterly"],
                      src.iloc[3].ibq, rtol=1e-12)


def test_term_count_is_reported(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=5)
    src.loc[src.index[3], "cheq"] = np.nan
    _, report = build_with_report(cfg, src)
    used = report["fcf_terms"]["terms_used"]
    assert used[0] == 1            # first row: income only
    assert used[1] == 6            # a complete row
    assert used[3] == 5            # cash term omitted
    assert used[4] == 5            # and again at t+1


def test_available_change_omits_rather_than_fills(cfg, seed):
    _, report = build_with_report(cfg, make_source(seed, tickers=("AAA",), n_quarters=4))
    grid = report["grid"]
    values = np.array([10.0, np.nan, 30.0, 40.0])
    change, usable = levels.available_change(values, grid)
    assert list(usable) == [False, False, False, True]
    assert list(change) == [0.0, 0.0, 0.0, 10.0]


# ------------------------------------------------------------------- the TTM
def test_ttm_is_the_sum_of_four_and_never_partial(cfg, seed):
    panel = build(cfg, make_source(seed, tickers=("AAA",), n_quarters=10))
    fcf = panel["free_cash_flow_quarterly"].to_numpy()
    ttm = panel["calc_free_cash_flow_ttm"].to_numpy()
    assert np.isnan(ttm[:3]).all()
    for row in range(3, 10):
        assert np.isclose(ttm[row], fcf[row - 3:row + 1].sum(), rtol=1e-12), row


def test_ttm_needs_four_consecutive_rows(cfg, seed):
    panel, report = build_with_report(cfg, make_source(seed, tickers=("AAA",), n_quarters=8))
    have = panel["calc_free_cash_flow_ttm"].notna().to_numpy()
    assert (report["grid"].run_len[have] >= 4).all()


def test_one_unusable_quarter_blanks_four_ttm_windows(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=12)
    for field in ("ibq", "actq", "cheq", "lctq", "dlcq", "ppentq"):
        src.loc[src.index[4], field] = np.nan
        src.loc[src.index[5], field] = np.nan
    panel = build(cfg, src)
    assert pd.isna(panel.loc[5, "free_cash_flow_quarterly"])
    assert panel.loc[5:8, "calc_free_cash_flow_ttm"].isna().all()
    assert pd.notna(panel.loc[9, "calc_free_cash_flow_ttm"])


def test_lag_k_requires_an_unbroken_run(cfg, seed):
    _, report = build_with_report(cfg, make_source(seed, tickers=("AAA",), n_quarters=6))
    grid = report["grid"]
    values = np.arange(grid.n, dtype="float64")
    assert np.isnan(grid.lag_k(values, 3)[:3]).all()
    assert list(grid.lag_k(values, 3)[3:]) == [0.0, 1.0, 2.0]
    assert list(grid.lag_k(values, 0)) == list(values)


# ---------------------------------------------------------------- the ratios
def test_quarterly_ratio_uses_the_quarterly_flow(cfg, seed):
    panel = build(cfg, make_source(seed, tickers=("AAA",), n_quarters=10))
    expected = safe_div(panel["market_cap"].to_numpy(),
                        panel["free_cash_flow_quarterly"].to_numpy())
    assert np.allclose(panel["calc_p_to_fcf_quarterly"].to_numpy(), expected,
                       rtol=1e-12, equal_nan=True)


def test_ttm_ratio_uses_the_trailing_flow(cfg, seed):
    panel = build(cfg, make_source(seed, tickers=("AAA",), n_quarters=10))
    expected = safe_div(panel["market_cap"].to_numpy(),
                        panel["calc_free_cash_flow_ttm"].to_numpy())
    assert np.allclose(panel["calc_p_to_fcf_ttm"].to_numpy(), expected,
                       rtol=1e-12, equal_nan=True)


def test_the_two_ratios_are_different_measures(cfg, seed):
    panel = build(cfg, make_source(seed, tickers=("AAA",), n_quarters=10))
    both = (panel["calc_p_to_fcf_quarterly"].notna()
            & panel["calc_p_to_fcf_ttm"].notna())
    assert both.sum() > 0
    assert not np.allclose(panel.loc[both, "calc_p_to_fcf_quarterly"],
                           panel.loc[both, "calc_p_to_fcf_ttm"])


def test_both_ratios_use_market_cap_not_enterprise_value(cfg, seed):
    """FCFE is a levered flow, so a pure debt change must not move the ratios."""
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    base = build(cfg, src)
    bumped = src.copy()
    bumped["dlttq"] = bumped["dlttq"] + 1000.0
    after = build(cfg, bumped)
    for col in ("calc_p_to_fcf_quarterly", "calc_p_to_fcf_ttm"):
        assert np.allclose(base[col].to_numpy(), after[col].to_numpy(),
                           rtol=1e-12, equal_nan=True), col


def test_zero_flow_gives_nan_not_inf(cfg, seed):
    assert np.isnan(safe_div(np.array([5.0]), np.array([0.0]))[0])
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    for field in ("actq", "cheq", "lctq", "dlcq", "ppentq"):
        src[field] = 100.0
    src["ibq"] = 0.0
    panel = build(cfg, src)
    assert (panel["free_cash_flow_quarterly"] == 0).all()
    assert panel["calc_p_to_fcf_quarterly"].isna().all()


def test_negative_flow_gives_a_negative_ratio_not_a_filter(cfg, seed):
    src = make_source(seed, tickers=("AAA",), n_quarters=10)
    src["ibq"] = -200.0
    panel = build(cfg, src)
    rows = panel[panel["calc_free_cash_flow_ttm"] < 0]
    assert len(rows) > 0
    assert (rows["calc_p_to_fcf_ttm"] < 0).all()


# --------------------------------------------------------------- on real data
def test_real_fcf_identities(real_panel):
    """The emitted columns reproduce the formula on the whole panel."""
    panel, report = real_panel
    grid, aux = report["grid"], report["aux"]
    fcf = panel["free_cash_flow_quarterly"].to_numpy()

    signed = [(panel["total_current_assets"].to_numpy(), -1.0),
              (aux["cash_and_short_term_investments"], +1.0),
              (panel["total_current_liabilities"].to_numpy(), +1.0),
              (panel["short_term_debt"].to_numpy(), -1.0),
              (panel["net_ppe"].to_numpy(), -1.0)]
    rebuilt, used = levels.free_cash_flow_quarterly(
        aux["income_before_extraordinary_items"], signed, grid)
    assert np.allclose(fcf, rebuilt, rtol=1e-9, atol=1e-9, equal_nan=True)
    assert np.array_equal(used, report["fcf_terms"]["terms_used"])
    # the fill is what makes this large: every row with any usable term has a flow
    assert int(np.isfinite(fcf).sum()) == int((used > 0).sum())

    ttm = panel["calc_free_cash_flow_ttm"].to_numpy()
    have = np.isfinite(ttm)
    assert (grid.run_len[have] >= 4).all()
    assert np.allclose(ttm[have], sum(grid.lag_k(fcf, k) for k in range(4))[have],
                       rtol=1e-9, atol=1e-9)

    mc = panel["market_cap"].to_numpy()
    assert np.allclose(panel["calc_p_to_fcf_quarterly"].to_numpy(),
                       safe_div(mc, fcf), rtol=1e-12, equal_nan=True)
    assert np.allclose(panel["calc_p_to_fcf_ttm"].to_numpy(),
                       safe_div(mc, ttm), rtol=1e-12, equal_nan=True)


def test_real_term_completeness_distribution(real_panel):
    """How many rows were built from how many terms - measured 2026-10-11.

    This is the disclosure the term-wise fill makes necessary: 54.6% of rows
    carry the full six-term formula, 27.8% are partial, and the worst case is a
    flow that is nothing but ibq. A shift here means the input coverage moved.
    """
    panel, report = real_panel
    used = report["fcf_terms"]["terms_used"]
    n = len(panel)
    expected = {0: 0.1755, 1: 0.1086, 2: 0.0343, 3: 0.0332,
                4: 0.0749, 5: 0.0274, 6: 0.5460}
    for k, want in expected.items():
        got = float((used == k).sum() / n)
        assert abs(got - want) <= 0.003, f"{k} terms: {got:.4f} vs {want}"
    # a flow exists exactly where at least one term was computable
    assert int(np.isfinite(panel["free_cash_flow_quarterly"].to_numpy()).sum())         == int((used > 0).sum())
    # and the partial rows are a material share, so they must stay visible
    assert 0.25 <= float(((used > 0) & (used < 6)).sum() / n) <= 0.31


def test_real_zero_flows_exist_and_are_kept(real_panel):
    """A reported flow of exactly zero is data, not a missing value."""
    panel, _ = real_panel
    fcf = panel["free_cash_flow_quarterly"].to_numpy()
    assert int((fcf == 0).sum()) > 0
    zero = np.isfinite(fcf) & (fcf == 0)
    assert panel.loc[zero, "calc_p_to_fcf_quarterly"].isna().all()


def test_real_no_inf_in_either_ratio(real_panel):
    panel, _ = real_panel
    for col in ("calc_p_to_fcf_quarterly", "calc_p_to_fcf_ttm"):
        assert not np.isinf(panel[col].to_numpy()).any(), col

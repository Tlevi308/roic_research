"""A window never crosses a gap in the quarter grid."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import keys
from tests.conftest import build, build_with_report, make_source


def test_quarter_index_steps_by_one_across_a_calendar_quarter(cfg, seed):
    _, report = build_with_report(cfg, make_source(seed, tickers=("AAA",), n_quarters=8))
    qidx = report["panel_keys"]["quarter_index"].to_numpy()
    assert (np.diff(qidx) == 1).all()


def test_gap_blanks_the_average_and_both_bridges(cfg, seed):
    """Deleting one quarter blanks the next row's comparison, not the whole firm."""
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    src = src.drop(index=src.index[3]).reset_index(drop=True)
    panel = build(cfg, src)
    # the row after the hole
    assert pd.isna(panel.loc[3, "calc_average_ic_raw_quarterly"])
    assert panel.loc[3, "calc_roic_decomposition_status"] == "UNCLASSIFIED_NONCONSECUTIVE"
    assert panel.loc[3, "calc_nopat_decomposition_status"] == "UNCLASSIFIED_NONCONSECUTIVE"
    # and the one after that, which inherits a missing average at t-1
    assert panel.loc[4, "calc_roic_decomposition_status"] == "UNCLASSIFIED_MISSING_DATA"
    # a clean run further on is unaffected
    assert panel.loc[6, "calc_roic_decomposition_status"] == "VALID"


def test_day_gap_outside_the_range_breaks_the_link(cfg, seed):
    """A quarter step of 1 is not enough; the day gap must also be plausible."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    src.loc[src.index[2], "datadate"] = src.loc[src.index[1], "datadate"] + pd.Timedelta(days=20)
    panel = build(cfg, src)
    assert panel.loc[2, "calc_nopat_decomposition_status"] == "UNCLASSIFIED_NONCONSECUTIVE"


def test_windows_do_not_cross_firms(cfg, seed):
    """Each firm's first row has no previous quarter."""
    _, report = build_with_report(cfg, make_source(seed, tickers=("AAA", "BBB", "CCC")))
    grid = report["grid"]
    firsts = np.flatnonzero(np.diff(grid.firm_codes, prepend=-1) != 0)
    assert not grid.prev_ok[firsts].any()
    assert (grid.run_len[firsts] == 1).all()


def test_run_len_counts_the_consecutive_tail(cfg, seed):
    _, report = build_with_report(cfg, make_source(seed, tickers=("AAA",), n_quarters=6))
    assert list(report["grid"].run_len) == [1, 2, 3, 4, 5, 6]


def test_non_december_fiscal_year_still_forms_a_clean_run(cfg, seed):
    """The period rule handles an offset fiscal calendar with no extra input."""
    src = make_source(seed, tickers=("AAA",), n_quarters=8, month_offset=2)
    _, report = build_with_report(cfg, src)
    assert (np.diff(report["panel_keys"]["quarter_index"].to_numpy()) == 1).all()


def test_assert_sorted_rejects_a_duplicate_quarter():
    df = pd.DataFrame({"gvkey": ["A", "A"], "quarter_index": [10, 10],
                       "fiscal_period_end_date": pd.to_datetime(["2020-03-31", "2020-03-31"])})
    try:
        keys.assert_sorted(df)
    except ValueError as exc:
        assert "sorted by (gvkey, quarter_index)" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_real_classified_rows_are_truly_consecutive(real_panel):
    """Every VALID ROIC row has a real t-1 and t-2 behind it."""
    panel, report = real_panel
    grid = report["grid"]
    valid = (panel["calc_roic_decomposition_status"] == "VALID").to_numpy()
    assert grid.prev_ok[valid].all()
    assert (grid.run_len[valid] >= 3).all()

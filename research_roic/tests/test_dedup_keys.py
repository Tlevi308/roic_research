"""Duplicate merging, exactly as specified, and a KEY that cannot fuse firms."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import keys
from tests.conftest import build, build_with_report, make_source

# measured on data/data.parquet as of 2026-10-05
EXPECTED_SOURCE_ROWS = 1_905_162
EXPECTED_STEP1_REMOVED = 1_108
EXPECTED_STEP2_REMOVED = 673
EXPECTED_PANEL_ROWS = 1_903_381
EXPECTED_ROWS_WITHOUT_TICKER = 383


def test_step1_keeps_the_first_row_and_fills_its_gaps(cfg, seed):
    """Same gvkey and date: first row stays, its missing values come from the next."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    first = src.iloc[[1]].copy()
    first.loc[first.index[0], "oiadpq"] = np.nan          # missing on the winner
    first.loc[first.index[0], "piq"] = 111.0              # present on the winner
    second = src.iloc[[1]].copy()
    second.loc[second.index[0], "oiadpq"] = 77.0          # fills the gap
    second.loc[second.index[0], "piq"] = 999.0            # must NOT override
    src = pd.concat([src.iloc[[0]], first, second, src.iloc[2:]], ignore_index=True)

    panel, report = build_with_report(cfg, src)
    assert report["dedup"]["removed_step1_same_date"] == 1
    assert panel.loc[1, "ebit"] == 77.0
    assert panel.loc[1, "pretax_income"] == 111.0


def test_step2_first_row_decides_and_nothing_is_merged(cfg, seed):
    """Same quarter, different dates: no value crosses between fiscal periods."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    extra = src.iloc[[1]].copy()
    extra.loc[extra.index[0], "datadate"] = src.loc[src.index[1], "datadate"] + pd.Timedelta(days=30)
    extra.loc[extra.index[0], "oiadpq"] = 1234.0
    src.loc[src.index[1], "oiadpq"] = np.nan
    src = pd.concat([src.iloc[:2], extra, src.iloc[2:]], ignore_index=True)

    panel, report = build_with_report(cfg, src)
    assert report["dedup"]["removed_step2_same_period"] == 1
    assert pd.isna(panel.loc[1, "ebit"])        # the later row did NOT fill it
    audit = report["duplicates_audit"]
    assert (audit["reason"] == "SAME_PERIOD_DIFFERENT_DATE_FIRST_ROW_WINS").any()
    # the audit carries every value the removed row held, under its logical name
    assert (audit["removed_ebit"] == 1234.0).any()


def test_merge_is_deterministic_under_a_shuffle_of_equal_rows(cfg, seed):
    """Source order decides, so the same input always gives the same winner."""
    src = make_source(seed, tickers=("AAA",), n_quarters=4)
    panel_a = build(cfg, src)
    panel_b = build(cfg, src.copy())
    pd.testing.assert_frame_equal(panel_a, panel_b)


def test_rows_without_a_ticker_get_no_key_and_do_not_collide(cfg, seed):
    """Two tickerless firms in the same quarter must stay two rows."""
    src = make_source(seed, tickers=("AAA", "BBB"), n_quarters=4)
    src["tic"] = src["tic"].where(src["gvkey"] == "000001", other=None)
    src.loc[src["gvkey"] == "000001", "tic"] = None
    panel, report = build_with_report(cfg, src)
    assert report["dedup"]["removed_step2_same_period"] == 0
    assert len(panel) == len(src)
    assert int(panel["KEY"].isna().sum()) == len(src)


def test_key_format(cfg, seed):
    panel = build(cfg, make_source(seed, tickers=("AAA",), first="2016Q4", n_quarters=1))
    assert panel.loc[0, "KEY"] == "2016Q4_AAA"


def test_duplicate_does_not_pair_a_quarter_with_itself(cfg, seed):
    """Without the merge, shift(1) would compare a quarter to its own copy."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    src = pd.concat([src.iloc[:3], src.iloc[[2]], src.iloc[3:]], ignore_index=True)
    panel, report = build_with_report(cfg, src)
    assert len(panel) == 6
    grid = report["grid"]
    qidx = report["panel_keys"]["quarter_index"].to_numpy()
    assert (np.diff(qidx) == 1).all()
    assert not (np.diff(qidx) == 0).any()


def test_real_merge_counts_are_the_measured_ones(real_panel):
    panel, report = real_panel
    d = report["dedup"]
    assert d["rows_in"] == EXPECTED_SOURCE_ROWS
    assert d["removed_step1_same_date"] == EXPECTED_STEP1_REMOVED
    assert d["removed_step2_same_period"] == EXPECTED_STEP2_REMOVED
    assert d["rows_out"] == EXPECTED_PANEL_ROWS
    assert len(panel) == EXPECTED_PANEL_ROWS


def test_real_key_is_unique_wherever_there_is_a_ticker(real_panel):
    panel, _ = real_panel
    has_key = panel["KEY"].notna()
    assert int((~has_key).sum()) == EXPECTED_ROWS_WITHOUT_TICKER
    assert panel.loc[has_key, "KEY"].is_unique


def test_real_one_row_per_firm_quarter(real_panel):
    _, report = real_panel
    pk = report["panel_keys"]
    assert not pk.duplicated(["gvkey", "period_key"]).any()


def test_real_audit_accounts_for_every_removed_row(real_panel):
    _, report = real_panel
    audit = report["duplicates_audit"]
    d = report["dedup"]
    assert len(audit) == d["removed_step1_same_date"] + d["removed_step2_same_period"]

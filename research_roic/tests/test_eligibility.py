"""Relevance ranges: start inclusive, end exclusive, open end = member through the latest quarter."""
import copy

import numpy as np
import pandas as pd
import pytest

from src import data_io, eligibility


def _rel(rows):
    rel = pd.DataFrame(rows, columns=["ticker", "start_quarter", "end_quarter"]).astype("string")
    start, _ = data_io.parse_period_key(rel["start_quarter"], r"^\d{4}Q[1-4]$")
    end, _ = data_io.parse_period_key(rel["end_quarter"].where(rel["end_quarter"] != ""), r"^\d{4}Q[1-4]$")
    rel["start_ord"], rel["end_ord"] = data_io.quarter_ordinal(start), data_io.quarter_ordinal(end)
    rel["open_end"] = rel["end_quarter"] == ""
    rel["range_id"] = np.arange(len(rel))
    return rel


def _panel(pairs):
    df = pd.DataFrame(pairs, columns=["symbol", "period_key"])
    df["symbol"] = df["symbol"].astype("string")
    df["q_ord"] = data_io.quarter_ordinal(pd.Series(pd.PeriodIndex(df["period_key"], freq="Q")))
    return df


REL = [("AAA", "2018Q1", "2019Q1"), ("BBB", "2020Q2", ""), ("CCC", "2017Q1", "2018Q1"), ("CCC", "2019Q3", ""),
       ("DDD", "2019Q2", "2019Q2")]
PAIRS = [("AAA", "2017Q4"), ("AAA", "2018Q1"), ("AAA", "2018Q4"), ("AAA", "2019Q1"),
         ("BBB", "2020Q1"), ("BBB", "2020Q2"), ("BBB", "2030Q4"),
         ("CCC", "2017Q4"), ("CCC", "2018Q1"), ("CCC", "2019Q2"), ("CCC", "2019Q3"),
         ("DDD", "2019Q2"), ("ZZZ", "2019Q1")]
EXPECTED_EXCLUSIVE = [False, True, True, False, False, True, True, True, False, False, True, False, False]


def test_boundaries_start_inclusive_end_exclusive(cfg):
    out = eligibility.membership(_panel(PAIRS), _rel(REL), cfg)
    assert out["in_index"].tolist() == EXPECTED_EXCLUSIVE
    assert not out["ticker_in_relevance_file"].iloc[-1] and out["ticker_in_relevance_file"].iloc[:-1].all()


def test_end_inclusive_toggle(cfg):
    alt = copy.deepcopy(cfg)
    alt["relevance"]["end_inclusive"] = True
    out = eligibility.membership(_panel(PAIRS), _rel([r for r in REL if r[0] != "DDD"]), alt)
    got = dict(zip(PAIRS, out["in_index"]))
    assert got[("AAA", "2019Q1")] and got[("CCC", "2018Q1")]


def test_range_id_points_to_matching_range(cfg):
    out = eligibility.membership(_panel(PAIRS), _rel(REL), cfg)
    got = dict(zip(PAIRS, out["relevance_range_id"]))
    assert got[("CCC", "2017Q4")] == 2 and got[("CCC", "2019Q3")] == 3
    assert pd.isna(got[("CCC", "2019Q2")])


def test_overlapping_ranges_raise(cfg):
    with pytest.raises(ValueError, match="Overlapping"):
        eligibility.validate_ranges(_rel([("AAA", "2018Q1", "2019Q2"), ("AAA", "2019Q1", "")]), cfg)
    with pytest.raises(ValueError, match="Overlapping"):
        eligibility.validate_ranges(_rel([("AAA", "2018Q1", ""), ("AAA", "2019Q1", "")]), cfg)


def test_adjacent_ranges_do_not_overlap_under_exclusive_end(cfg):
    rep = eligibility.validate_ranges(_rel([("AAA", "2018Q1", "2019Q1"), ("AAA", "2019Q1", "")]), cfg)
    assert rep["overlapping_tickers"] == []


def test_same_quarter_range_is_empty(cfg):
    rep = eligibility.validate_ranges(_rel(REL), cfg)
    assert rep["ranges_empty_under_rule"]["ticker"].tolist() == ["DDD"]


def test_real_membership_matches_independent_expansion(cfg, real_panel, real_inputs):
    _, rel, _ = real_inputs
    stocks = real_panel[~real_panel["is_benchmark"]]
    members = eligibility.expand_members(rel, cfg, int(stocks["q_ord"].min()), int(stocks["q_ord"].max()))
    member_set = set(zip(members["ticker"], members["q_ord"]))
    expected = [(s, int(q)) in member_set for s, q in zip(stocks["symbol"], stocks["q_ord"])]
    assert stocks["in_index"].tolist() == expected


def test_real_known_example_aal(real_panel):
    """AAL: range 2015Q1-2024Q3 -> member through 2024Q2, not in 2024Q3 (end exclusive)."""
    aal = real_panel[real_panel["symbol"] == "AAL"].set_index("period_key")["in_index"]
    assert aal["2024Q2"] and not aal["2024Q3"] and not aal["2025Q1"]


def test_real_index_size_close_to_503(cfg, real_inputs):
    _, rel, _ = real_inputs
    members = eligibility.expand_members(rel, cfg, pd.Period("2023Q1", "Q").ordinal, pd.Period("2026Q2", "Q").ordinal)
    per_q = members.groupby("period_key").size()
    assert per_q.between(500, 506).all()


def test_benchmarks_never_in_population(real_panel):
    assert not real_panel.loc[real_panel["is_benchmark"], "in_population"].any()

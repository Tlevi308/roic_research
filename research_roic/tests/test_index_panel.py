"""The index panel: the two dates of a quarter, leaving and rejoining the index,
records that are one continuous stay, the boundaries, the (gvkey, KEY) join,
the no-lookahead property, and the measured facts of the Nasdaq 100 file."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import index_panel as I, schema
from tests.conftest import build as build_base, make_source


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def spells(*rows: tuple[str, str, str | None]) -> pd.DataFrame:
    """Standardized index records: (gvkey, from, thru or None for still in)."""
    return pd.DataFrame({
        "gvkey": pd.array([g for g, _, _ in rows], dtype="string"),
        "from": pd.to_datetime([f for _, f, _ in rows]).astype("datetime64[ns]"),
        "thru": pd.to_datetime([t for _, _, t in rows]).astype("datetime64[ns]"),
        "company": pd.array([f"FIRM {g}" for g, _, _ in rows], dtype="string"),
        "source_row": np.arange(len(rows)),
    })


def gap(cfg) -> int:
    return int(cfg["index_panel"]["contiguous_gap_days"])


def flags(cfg, records: pd.DataFrame, periods: list[str], gvkey: str = "000001",
          merge: bool = True) -> dict[str, int]:
    stays = I.merge_contiguous_spells(records, gap(cfg))[0] if merge else records
    q, f = I.quarter_dates(pd.Series(periods, dtype="string"), cfg)
    out = I.in_index_flag(pd.Series([gvkey] * len(periods)), q, f, stays)
    return dict(zip(periods, out.tolist()))


def reference(cfg, records: pd.DataFrame, period: str) -> int:
    """The researcher's rule, one record at a time, written out by hand."""
    q_end = pd.Period(period, freq="Q").end_time.normalize()
    formation = q_end + pd.offsets.MonthEnd(int(cfg["timing"]["quarter_shift_months"]))
    for start, end in zip(records["from"], records["thru"]):
        if start <= q_end and (pd.isna(end) or formation <= end):
            return 1
    return 0


def quarters(first: str, last: str) -> list[str]:
    return [str(p) for p in pd.period_range(first, last, freq="Q")]


# ---------------------------------------------------------------------------
# the two dates
# ---------------------------------------------------------------------------
def test_quarter_end_and_investment_date_come_from_the_key(cfg):
    period = I.period_from_key(pd.Series(["2025Q1_AIR", "2025Q4_AAPL", "2023Q4_X", None],
                                         dtype="string"), cfg)
    assert period.tolist()[:3] == ["2025Q1", "2025Q4", "2023Q4"] and pd.isna(period[3])
    q, f = I.quarter_dates(period, cfg)
    assert q.dt.strftime("%Y-%m-%d").tolist()[:3] == ["2025-03-31", "2025-12-31", "2023-12-31"]
    assert f.dt.strftime("%Y-%m-%d").tolist()[:3] == ["2025-05-31", "2026-02-28", "2024-02-29"]
    for bad in ("Q1-2025_AIR", "2025Q5_AIR", "2025Q1AIR"):
        with pytest.raises(ValueError):
            I.period_from_key(pd.Series([bad], dtype="string"), cfg)


# ---------------------------------------------------------------------------
# leaving and rejoining
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("records", [
    (("000001", "2000-01-01", "2003-06-30"), ("000001", "2019-01-01", None)),
    (("000001", "1996-03-15", "1998-01-10"), ("000001", "2001-05-01", "2004-12-31"),
     ("000001", "2010-07-01", None)),
    (("000001", "1995-01-01", "1997-02-27"), ("000001", "1999-12-20", "2001-12-21"),
     ("000001", "2008-06-01", "2009-03-31"), ("000001", "2015-12-21", None)),
])
def test_leave_and_rejoin_matches_the_rule_quarter_by_quarter(cfg, records):
    rec = spells(*records)
    periods = quarters("1994Q4", "2026Q2")
    got = flags(cfg, rec, periods)
    assert got == {p: reference(cfg, rec, p) for p in periods}
    # 1 inside each stay, 0 in every gap between them
    assert got["2000Q2"] == reference(cfg, rec, "2000Q2")
    assert 0 in got.values() and 1 in got.values()


def test_two_years_out_of_the_index_are_zero(cfg):
    got = flags(cfg, spells(("000001", "2000-01-01", "2003-06-30"),
                            ("000001", "2019-01-01", None)),
                ["2002Q4", "2003Q1", "2003Q2", "2010Q1", "2018Q4", "2019Q1", "2026Q1"])
    # 2003Q1: invested 2003-05-31, inside the first stay; 2003Q2: invested
    # 2003-08-31, after it ended; 2018Q4: quarter end 2018-12-31, before the return
    assert got == {"2002Q4": 1, "2003Q1": 1, "2003Q2": 0, "2010Q1": 0,
                   "2018Q4": 0, "2019Q1": 1, "2026Q1": 1}


# ---------------------------------------------------------------------------
# records that are one stay
# ---------------------------------------------------------------------------
def test_a_next_day_record_continues_the_stay(cfg):
    """Comcast: one record to 2002-11-18, the next from 2002-11-19."""
    rec = spells(("000001", "1995-01-01", "2002-11-18"), ("000001", "2002-11-19", None))
    stays, audit = I.merge_contiguous_spells(rec, gap(cfg))
    assert len(stays) == 1 and pd.isna(stays["thru"].iloc[0])
    assert audit["relation"].tolist() == ["FIRST_RECORD_OF_STAY", "STARTS_RIGHT_AFTER_PREVIOUS"]
    assert audit["days_after_previous_thru"].tolist()[1] == 1
    # 2002Q3: quarter end in the first record, investment date in the second
    assert flags(cfg, rec, ["2002Q3"]) == {"2002Q3": 1}
    assert flags(cfg, rec, ["2002Q3"], merge=False) == {"2002Q3": 0}


def test_overlapping_and_nested_records_are_one_stay(cfg):
    """Two share classes of one firm, and a record nested inside another."""
    rec = spells(("000001", "2005-12-21", None), ("000001", "2014-04-03", None),
                 ("000002", "2005-01-01", "2010-12-31"), ("000002", "2006-01-01", "2007-01-01"),
                 ("000002", "2011-01-01", "2012-06-30"))
    stays, audit = I.merge_contiguous_spells(rec, gap(cfg))
    assert stays["gvkey"].tolist() == ["000001", "000002"]
    assert stays.loc[1, "thru"] == pd.Timestamp("2012-06-30")
    assert (audit["relation"] == "OVERLAPS_PREVIOUS").sum() == 2
    assert audit["days_after_previous_thru"].isna().sum() == 4


def test_a_real_gap_keeps_two_stays(cfg):
    """Two days apart is an exit: 2000-06-30 sits in the gap."""
    rec = spells(("000001", "2000-01-01", "2000-06-29"), ("000001", "2000-07-01", None))
    stays, audit = I.merge_contiguous_spells(rec, gap(cfg))
    assert len(stays) == 2 and audit.empty
    assert flags(cfg, rec, ["2000Q1", "2000Q2", "2000Q3"]) == \
        {"2000Q1": 1, "2000Q2": 0, "2000Q3": 1}     # 2000Q1: both dates in the first stay


# ---------------------------------------------------------------------------
# both dates in the same stay, and the boundaries
# ---------------------------------------------------------------------------
def test_both_dates_must_sit_in_the_same_stay(cfg):
    rec = spells(("000001", "2000-01-01", "2000-04-15"), ("000001", "2000-06-01", None))
    # 2000Q1: 2000-03-31 in the first stay, 2000-05-31 in neither
    assert flags(cfg, rec, ["2000Q1", "2000Q2"]) == {"2000Q1": 0, "2000Q2": 1}


def test_joined_between_the_two_dates_is_zero(cfg):
    """Shopify 2025Q1: in the index on 2025-05-31 but not on 2025-03-31."""
    rec = spells(("000001", "2025-04-15", None))
    assert flags(cfg, rec, ["2025Q1", "2025Q2"]) == {"2025Q1": 0, "2025Q2": 1}


@pytest.mark.parametrize("start, end, expected", [
    ("2025-03-31", None, 1),            # from on the quarter end: in
    ("2025-04-01", None, 0),            # from a day later: out
    ("2020-01-01", "2025-05-31", 1),    # thru on the investment date: in
    ("2020-01-01", "2025-05-30", 0),    # thru a day before it: out
    ("2020-01-01", None, 1),            # empty thru: still in
])
def test_boundaries(cfg, start, end, expected):
    assert flags(cfg, spells(("000001", start, end)), ["2025Q1"]) == {"2025Q1": expected}


def test_another_firms_stay_does_not_count(cfg):
    rec = spells(("000002", "2000-01-01", None))
    assert flags(cfg, rec, ["2010Q1"], gvkey="000001") == {"2010Q1": 0}


def test_future_records_do_not_change_the_past(cfg):
    """Records that start after an investment date cannot move its flag."""
    rng = np.random.default_rng(int(cfg["project"]["seed"]))
    base = spells(("000001", "1999-03-01", "2004-08-15"), ("000001", "2012-02-01", None),
                  ("000002", "2001-10-10", "2013-05-05"), ("000003", "2009-09-09", None))
    cutoff = pd.Timestamp("2011-06-30")
    later = base["from"] > cutoff
    shocked = base.copy()
    shocked.loc[later, "from"] = cutoff + pd.to_timedelta(rng.integers(1, 2000, int(later.sum())), "D")
    shocked = pd.concat([shocked, spells(("000002", "2015-01-01", None),
                                         ("000004", "2012-01-01", None))], ignore_index=True)
    shocked["source_row"] = np.arange(len(shocked))
    periods = [p for p in quarters("1998Q1", "2011Q1")]
    for g in ("000001", "000002", "000003", "000004"):
        before = flags(cfg, base, periods, gvkey=g)
        after = flags(cfg, shocked, periods, gvkey=g)
        assert before == after, g


# ---------------------------------------------------------------------------
# reading the index file
# ---------------------------------------------------------------------------
GOOD = ("tic,from,gvkeyx,conm,indextype,gvkey,conm_cst,thru\n"
        "I0028,1995-01-01,000208,Nasdaq 100,LGCAP,001690,APPLE INC,\n"
        "I0028,2000-01-01,000208,Nasdaq 100,LGCAP,001161,ADVANCED MICRO DEVICES,2003-06-30\n")


def with_file(cfg, tmp_path, text: str):
    path = tmp_path / "idx.csv"
    path.write_text(text, encoding="utf-8")
    c = copy.deepcopy(cfg)
    c["indices"]["test"] = {**cfg["indices"]["qqq100"], "file": str(path)}
    return c


def test_read_keeps_gvkey_as_text_and_empty_thru_as_open(cfg, tmp_path):
    out = I.read_constituents(with_file(cfg, tmp_path, GOOD), "test")
    assert out["gvkey"].tolist() == ["001690", "001161"]
    assert pd.isna(out.loc[0, "thru"]) and out.loc[1, "thru"] == pd.Timestamp("2003-06-30")


@pytest.mark.parametrize("change, error", [
    (lambda t: t.replace(",000208,", ",000003,", 1), ValueError),          # another index
    (lambda t: t.replace("2003-06-30", "1999-06-30"), ValueError),         # ends before it starts
    (lambda t: t.replace(",001161,", ",1161,"), ValueError),               # gvkey lost its zeros
    (lambda t: t.replace("2000-01-01", "01/01/2000"), ValueError),         # another date format
    (lambda t: t.replace("gvkeyx", "index"), KeyError),                    # a column is missing
])
def test_read_refuses_what_it_cannot_trust(cfg, tmp_path, change, error):
    with pytest.raises(error):
        I.read_constituents(with_file(cfg, tmp_path, change(GOOD)), "test")


def test_an_unknown_index_name_raises(cfg):
    with pytest.raises(KeyError):
        I.index_settings(cfg, "no_such_index")


# ---------------------------------------------------------------------------
# the build: rows, the (gvkey, KEY) join, copied values
# ---------------------------------------------------------------------------
def synthetic_prices(panel: pd.DataFrame, cfg) -> pd.DataFrame:
    """One price row per base row, dated on the investment date, in the price contract."""
    period = I.period_from_key(panel["KEY"], cfg)
    _, formation = I.quarter_dates(period, cfg)
    rng = np.random.default_rng(int(cfg["project"]["seed"]))
    n = len(panel)
    return pd.DataFrame({
        "KEY": panel["KEY"].astype("string").to_numpy(),
        "gvkey": panel["gvkey"].astype("string").to_numpy(),
        "Date": formation.to_numpy("datetime64[ns]"),
        "Ticker": panel["symbol"].astype("string").to_numpy(),
        "Close": rng.uniform(10, 500, n),
        "Quaterly_return": rng.normal(0.02, 0.1, n),
        "MomentomQ3": rng.normal(0.05, 0.2, n),
        "ipodate": pd.NaT, "dldte": pd.NaT,
    }).astype({"KEY": "string", "gvkey": "string", "Ticker": "string",
               "ipodate": "datetime64[ns]", "dldte": "datetime64[ns]"})


@pytest.fixture()
def synthetic_build(cfg, seed):
    base = build_base(cfg, make_source(seed, tickers=("AAA", "BBB", "CCC"), first="2018Q1"))
    prices = synthetic_prices(base, cfg)
    # AAA has no price in 2018Q2; a BBB row carries AAA's KEY and must not fill it
    hole = prices["KEY"] == "2018Q2_AAA"
    decoy = prices.loc[hole].assign(gvkey=pd.array(["000002"], dtype="string"), Close=-1.0)
    prices = pd.concat([prices[~hole], decoy], ignore_index=True)
    rec = spells(("000001", "2018-01-01", None), ("000002", "2018-05-01", "2019-03-31"))
    table, report = I.build_index_panel(cfg, "qqq100", spells=rec, panel=base, prices=prices)
    return base, prices, table, report


def test_rows_are_every_base_row_of_the_index_firms(synthetic_build):
    base, _, table, _ = synthetic_build
    assert list(table.columns) == list(schema.INDEX_PANEL_COLUMNS)
    expected = base[base["gvkey"].isin(["000001", "000002"])].reset_index(drop=True)
    assert len(table) == len(expected) == 24
    pd.testing.assert_frame_equal(table[list(schema.PANEL_COLUMNS)], expected)
    assert not table.duplicated(["gvkey", "KEY"]).any()


def test_prices_join_on_gvkey_and_key(synthetic_build):
    _, prices, table, _ = synthetic_build
    t = table.set_index("KEY")
    hole = t.loc["2018Q2_AAA"]
    assert pd.isna(hole["Date"]) and pd.isna(hole["Close"])     # the decoy did not match
    assert (table["Close"].dropna() > 0).all()
    p = prices.set_index(["gvkey", "KEY"])
    matched = table.dropna(subset=["Date"]).set_index(["gvkey", "KEY"])
    for col in ("Date", "Ticker", "Close", "Quaterly_return", "MomentomQ3"):
        np.testing.assert_array_equal(matched[col].to_numpy(),
                                      p.loc[matched.index, col].to_numpy(), err_msg=col)
    with_price = table["Date"].notna()
    assert (table.loc[with_price, "formation_date"] == table.loc[with_price, "Date"]).all()


def test_flags_in_the_build(synthetic_build, cfg):
    _, _, table, report = synthetic_build
    t = table.set_index("KEY")["in_index"]
    assert t["2018Q1_AAA"] == 1 and t["2020Q4_AAA"] == 1
    # BBB: in from 2018-05-01 to 2019-03-31
    assert t["2018Q1_BBB"] == 0          # 2018-03-31 is before it joined
    assert t["2018Q2_BBB"] == 1          # 2018-06-30 and 2018-08-31
    assert t["2018Q4_BBB"] == 1          # 2018-12-31 and 2019-02-28, before it left
    assert t["2019Q1_BBB"] == 0          # invested 2019-05-31, after 2019-03-31
    assert table["in_index"].dtype == np.int8 and set(table["in_index"]) <= {0, 1}
    assert report["rows_in_index"] == int(table["in_index"].sum())


def test_months_from_fiscal_end_to_investment(cfg, seed):
    base = build_base(cfg, make_source(seed, tickers=("AAA",), first="2018Q1", month_offset=2))
    rec = spells(("000001", "2010-01-01", None))
    table, _ = I.build_index_panel(cfg, "qqq100", spells=rec, panel=base,
                                   prices=synthetic_prices(base, cfg).iloc[0:0])
    # fiscal quarters end in Feb/May/Aug/Nov: the investment date is the same month
    assert set(table["months_fiscal_end_to_formation"].dropna().tolist()) == {0}


# ---------------------------------------------------------------------------
# the real Nasdaq 100 file - measured on 2026-11-07
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def real_index(cfg):
    out = cfg["_paths"]["output_dir"]
    needed = [out / cfg["output"]["panel_file"], out / cfg["prices"]["output_file"],
              I.constituents_path(cfg, "qqq100")]
    if not all(p.exists() for p in needed):
        pytest.skip("panel, prices or index file not available")
    return I.build_index_panel(cfg, "qqq100")


def test_real_counts(real_index):
    table, rep = real_index
    assert rep["records_in_file"] == 524 and len(rep["stays"]) == 514
    assert rep["firms_in_file"] == 436 and table["gvkey"].nunique() == 436
    assert len(table) == 46_152
    assert rep["rows_with_price"] == 40_485
    assert rep["rows_in_index"] == 12_489
    assert rep["flag_cross_check"] is True
    assert len(rep["investable_without_row"]) == 10
    assert sorted(rep["firms_without_prices"]["gvkey"]) == \
        ["002230", "004828", "012144", "013506", "030095"]


def test_real_examples(real_index):
    table, _ = real_index
    t = table.set_index("KEY")["in_index"]
    assert t["2002Q3_CMCSA"] == 1          # crosses the 2002-11-18/19 seam
    assert t["2025Q1_SHOP"] == 0           # joined between 2025-03-31 and 2025-05-31
    assert t["2016Q4_AAPL"] == 1 and t["2025Q1_AAPL"] == 1
    assert (table.loc[table["KEY"].str.startswith("1994Q4"), "in_index"] == 0).all()


def test_real_values_are_copied_untouched(real_index, cfg):
    table, _ = real_index
    out = cfg["_paths"]["output_dir"]
    gv = sorted(table["gvkey"].unique())
    base = (pq.read_table(out / cfg["output"]["panel_file"], filters=[("gvkey", "in", gv)])
            .to_pandas().sort_values(["gvkey", "fiscal_period_end_date"], kind="mergesort")
            .reset_index(drop=True))
    pd.testing.assert_frame_equal(table[list(schema.PANEL_COLUMNS)], base)
    prices = pq.read_table(out / cfg["prices"]["output_file"],
                           filters=[("gvkey", "in", gv)]).to_pandas().set_index(["gvkey", "KEY"])
    matched = table.dropna(subset=["Date"]).set_index(["gvkey", "KEY"])
    for col in ("Date", "Ticker", "Close", "Quaterly_return", "MomentomQ3", "ipodate", "dldte"):
        left, right = matched[col], prices.loc[matched.index, col]
        assert ((left == right) | (left.isna() & right.isna())).all(), col
    with_price = table["Date"].notna()
    assert (table.loc[with_price, "formation_date"] == table.loc[with_price, "Date"]).all()

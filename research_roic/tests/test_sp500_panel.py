"""The S&P 500 panel: the quarter rule, leaving and rejoining, the ticker ->
firm assignment (identical ticker, Compustat's .<n> suffix, the exchange check,
the most report quarters, ties), one record to one firm, the (gvkey, KEY) join,
the no-lookahead property, the separation from the index panels, and the
measured facts of sp500hist.csv."""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import schema, sp500_panel as S
from tests.conftest import build as build_base, make_source

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def recs(cfg, *rows: tuple[str, str, str | None]) -> pd.DataFrame:
    """Standardized records from (ticker, start_quarter, end_quarter or None)."""
    raw = pd.DataFrame({"ticker": [r[0] for r in rows], "start_quarter": [r[1] for r in rows],
                        "end_quarter": [r[2] for r in rows]}, dtype=object)
    return S.standardize_records(raw, cfg["sp500_panel"])


def ident(*rows: tuple[str, str, str, int]) -> pd.DataFrame:
    """Base-panel identity rows from (gvkey, symbol, first quarter, number of quarters)."""
    parts = []
    for gvkey, symbol, first, n in rows:
        span = pd.period_range(first, periods=n, freq="Q").astype(str)
        parts.append(pd.DataFrame({"KEY": [f"{p}_{symbol}" for p in span], "gvkey": gvkey,
                                   "symbol": symbol, "company": f"{symbol} CORP"}))
    return pd.concat(parts, ignore_index=True).astype("string")


def assign(cfg, records: pd.DataFrame, identity: pd.DataFrame, exchanges: dict):
    firms = S.firm_quarters(identity, records["ticker"].unique(), cfg)
    return S.assign_records(records, firms, exchanges, cfg)


def flags_of(identity: pd.DataFrame, assigned: pd.DataFrame, cfg) -> dict[str, int]:
    period = S.period_from_key(identity["KEY"], cfg)
    out = S.in_index_flag(identity["gvkey"], period, assigned)
    return dict(zip(identity["KEY"].astype(str), out.tolist()))


def status_of(status: pd.DataFrame) -> dict[str, str]:
    return dict(zip(status["ticker"].astype(str) + "@" + status["start_quarter"].astype(str),
                    status["status"]))


# ---------------------------------------------------------------------------
# the quarter rule
# ---------------------------------------------------------------------------
def test_quarter_rule_start_in_end_out_open_end(cfg):
    assigned = pd.DataFrame({"gvkey": ["000001", "000002", "000003"],
                             "start_quarter": ["2015Q1", "2023Q3", "2019Q2"],
                             "end_quarter": ["2024Q3", None, "2019Q2"]})
    rows = pd.DataFrame({
        "gvkey": ["000001"] * 4 + ["000002"] * 3 + ["000003"] * 3,
        "period": ["2014Q4", "2015Q1", "2024Q2", "2024Q3",
                   "2023Q2", "2023Q3", "2030Q1",
                   "2019Q1", "2019Q2", "2019Q3"]})
    got = S.in_index_flag(rows["gvkey"], rows["period"], assigned).tolist()
    # AAL-like: 2024Q2 (quarter end 2024-06-30, invested 2024-08-31) in, 2024Q3 out;
    # ABNB-like: open end; a zero-length record marks nothing
    assert got == [0, 1, 1, 0, 0, 1, 1, 0, 0, 0]


def test_the_two_dates_come_from_the_quarter(cfg):
    period = S.period_from_key(pd.Series(["2024Q2_AAL", "2025Q4_AA.3", None], dtype="string"), cfg)
    assert period.tolist()[:2] == ["2024Q2", "2025Q4"] and pd.isna(period[2])
    q, f = S.quarter_dates(period, cfg)
    assert q.dt.strftime("%Y-%m-%d").tolist()[:2] == ["2024-06-30", "2025-12-31"]
    assert f.dt.strftime("%Y-%m-%d").tolist()[:2] == ["2024-08-31", "2026-02-28"]
    for bad in ("Q2-2024_AAL", "2024Q5_AAL", "2024Q2AAL"):
        with pytest.raises(ValueError):
            S.period_from_key(pd.Series([bad], dtype="string"), cfg)


def test_leave_and_rejoin(cfg):
    """AMD: out after 2013Q3, back from 2017Q1 - one firm, two records."""
    r = recs(cfg, ("AMD", "2000Q1", "2013Q3"), ("AMD", "2017Q1", None))
    i = ident(("000001", "AMD", "1995Q1", 126))
    assigned, status, _ = assign(cfg, r, i, {"000001": 14})
    assert len(assigned) == 2 and set(assigned["gvkey"]) == {"000001"}
    assert set(assigned["decided_by"]) == {S.ONLY_CANDIDATE}
    got = flags_of(i, assigned, cfg)
    assert [got[f"{q}_AMD"] for q in ("1999Q4", "2000Q1", "2013Q2", "2013Q3", "2016Q4",
                                      "2017Q1", "2026Q2")] == [0, 1, 1, 0, 0, 1, 1]


# ---------------------------------------------------------------------------
# from a ticker record to a firm
# ---------------------------------------------------------------------------
def test_only_candidate(cfg):
    r = recs(cfg, ("AAPL", "2000Q1", None))
    assigned, status, cand = assign(cfg, r, ident(("001690", "AAPL", "1990Q1", 146)),
                                    {"001690": 14})
    assert assigned[["gvkey", "decided_by"]].values.tolist() == [["001690", S.ONLY_CANDIDATE]]
    assert status["status"].tolist() == [S.ASSIGNED]
    assert cand["outcome"].tolist() == ["CHOSEN"]


def test_older_holder_with_more_quarters_wins(cfg):
    """AA 2000Q1-2016Q4: Alcoa Inc (AA.3) 67 quarters, the 2016 spin-off (AA) 11."""
    r = recs(cfg, ("AA", "2000Q1", "2016Q4"))
    i = ident(("000001", "AA.3", "1990Q1", 107), ("000002", "AA", "2014Q1", 50))
    assigned, status, cand = assign(cfg, r, i, {"000001": 11, "000002": 11})
    assert assigned[["gvkey", "symbol", "decided_by"]].values.tolist() == \
        [["000001", "AA.3", S.MOST_QUARTERS]]
    q = dict(zip(cand["symbol"], cand["quarters_in_range"]))
    assert q == {"AA.3": 67, "AA": 11}
    assert dict(zip(cand["symbol"], cand["outcome"])) == {"AA.3": "CHOSEN", "AA": "FEWER_QUARTERS"}
    got = flags_of(i, assigned, cfg)
    assert got["2015Q1_AA.3"] == 1 and got["2016Q3_AA.3"] == 1
    assert got["2015Q1_AA"] == 0 and got["2016Q3_AA"] == 0     # never the spin-off


def test_exchange_check_drops_a_candidate_even_when_alone(cfg):
    """CBOE on Cboe (21): out. EP: Empire Petroleum on NYSE American (12) is out,
    so El Paso (EP.1, NYSE) wins although the exact ticker had rows too."""
    r = recs(cfg, ("CBOE", "2017Q1", None), ("EP", "2000Q1", "2012Q2"),
             ("NOEX", "2000Q1", None))
    i = ident(("000001", "CBOE", "2009Q1", 70), ("000002", "EP", "2010Q1", 66),
              ("000003", "EP.1", "1977Q1", 141), ("000004", "NOEX", "1999Q1", 20))
    assigned, status, cand = assign(cfg, r, i, {"000001": 21, "000002": 12, "000003": 11,
                                                "000004": np.nan})
    st = status_of(status)
    assert st["CBOE@2017Q1"] == S.NOT_NYSE_NASDAQ
    assert st["NOEX@2000Q1"] == S.NOT_NYSE_NASDAQ             # no exchange at all
    assert assigned[["ticker", "symbol", "decided_by"]].values.tolist() == \
        [["EP", "EP.1", S.EXCHANGE_CHECK]]
    ep = cand[cand["ticker"] == "EP"].set_index("symbol")["outcome"].to_dict()
    assert ep == {"EP": "EXCHANGE_NOT_ALLOWED", "EP.1": "CHOSEN"}


def test_a_tie_stays_unassigned(cfg):
    """LSI 2000Q1-2014Q2: LSI Corp and Life Storage, 57 quarters each, both allowed."""
    r = recs(cfg, ("LSI", "2000Q1", "2014Q2"))
    i = ident(("000001", "LSI.1", "1982Q1", 129), ("000002", "LSI", "1994Q1", 117))
    assigned, status, cand = assign(cfg, r, i, {"000001": 14, "000002": 11})
    assert assigned.empty
    assert status["status"].tolist() == [S.TIE]
    assert set(cand["quarters_in_range"]) == {57} and set(cand["outcome"]) == {"TIED"}
    assert "LSI.1" in status["candidates"].iloc[0] and "LSI " in status["candidates"].iloc[0]


def test_only_dot_digits_is_the_same_ticker(cfg):
    """For AA: AA and AA.<n> - never AAX, never the share class AA.B."""
    base = S.base_ticker(pd.Series(["AA", "AA.3", "AA.10", "AAX", "AA.B", "BRK.B", "BF.B.1"]))
    assert base.tolist() == ["AA", "AA", "AA", "AAX", "AA.B", "BRK.B", "BF.B"]
    r = recs(cfg, ("AA", "2000Q1", None))
    i = ident(("000001", "AAX", "2000Q1", 10), ("000002", "AA.B", "2000Q1", 10),
              ("000003", "AA.3", "2000Q1", 10))
    _, status, cand = assign(cfg, r, i, {"000001": 11, "000002": 11, "000003": 11})
    assert cand["symbol"].tolist() == ["AA.3"]
    assert status["gvkey"].tolist() == ["000003"]


def test_why_a_record_goes_nowhere(cfg):
    r = recs(cfg, ("ZZZ", "2000Q1", None), ("OLD", "2010Q1", "2012Q1"),
             ("BMS", "2019Q2", "2019Q2"))
    i = ident(("000001", "OLD", "1990Q1", 40), ("000002", "BMS", "2010Q1", 40))
    assigned, status, _ = assign(cfg, r, i, {"000001": 11, "000002": 11})
    assert assigned.empty
    assert status_of(status) == {"ZZZ@2000Q1": S.TICKER_NOT_IN_PANEL,
                                 "OLD@2010Q1": S.NO_REPORT_ROW_IN_RANGE,
                                 "BMS@2019Q2": S.ZERO_LENGTH}
    holders = status.set_index("ticker")["ticker_holders_in_panel"]
    assert holders["OLD"] == "OLD OLD CORP (1990Q1-1999Q4)"


def test_one_record_never_goes_to_two_firms(cfg, seed):
    """Random holders, spans and exchanges: every record ends with one firm or none,
    and the firm it gets is the allowed candidate with strictly the most quarters."""
    rng = np.random.default_rng(seed)
    rows, exch, records = [], {}, []
    g = 0
    for t in range(40):
        tic = f"T{t}"
        for k in range(int(rng.integers(1, 4))):
            g += 1
            gv = f"{g:06d}"
            symbol = tic if k == 0 else f"{tic}.{k}"
            first = str(pd.Period("1995Q1", freq="Q") + int(rng.integers(0, 80)))
            rows.append((gv, symbol, first, int(rng.integers(1, 60))))
            exch[gv] = float(rng.choice([11, 14, 12, 19]))
        start = pd.Period("1998Q1", freq="Q") + int(rng.integers(0, 80))
        end = None if rng.random() < 0.3 else str(start + int(rng.integers(0, 40)))
        records.append((tic, str(start), end))
    r = recs(cfg, *records)
    assigned, status, cand = assign(cfg, r, ident(*rows), exch)
    assert not assigned["source_row"].duplicated().any()
    assert (status["status"] == S.ASSIGNED).sum() == len(assigned)
    assert set(status["status"]) <= set(S.RECORD_STATUSES)
    ok = cand[cand["exchange_ok"]]
    for a in assigned.itertuples(index=False):
        mine = ok[ok["source_row"] == a.source_row]
        assert a.quarters_in_range == mine["quarters_in_range"].max()
        assert (mine["quarters_in_range"] == a.quarters_in_range).sum() == 1
        assert exch[a.gvkey] in (11.0, 14.0)


def test_future_records_do_not_change_the_past(cfg):
    """Records that start after a quarter cannot move any flag up to it."""
    i = ident(("000001", "AAA", "1996Q1", 120), ("000002", "BBB", "1996Q1", 120),
              ("000003", "CCC.1", "1996Q1", 120), ("000004", "DDD", "1996Q1", 120))
    exch = {"000001": 11, "000002": 14, "000003": 11, "000004": 11}
    base = recs(cfg, ("AAA", "2000Q1", "2008Q2"), ("BBB", "2003Q4", None),
                ("CCC", "2001Q1", "2010Q1"))
    later = recs(cfg, ("AAA", "2000Q1", "2008Q2"), ("BBB", "2003Q4", None),
                 ("CCC", "2001Q1", "2010Q1"), ("AAA", "2012Q1", None),
                 ("DDD", "2011Q3", "2015Q1"), ("CCC", "2014Q2", None))
    cutoff = "2011Q2"
    before = flags_of(i, assign(cfg, base, i, exch)[0], cfg)
    after = flags_of(i, assign(cfg, later, i, exch)[0], cfg)
    past = [k for k in before if k[:6] <= cutoff]
    assert past and all(before[k] == after[k] for k in past)
    assert any(before[k] != after[k] for k in before if k[:6] > cutoff)


# ---------------------------------------------------------------------------
# reading the file
# ---------------------------------------------------------------------------
GOOD = "ticker,start_quarter,end_quarter\nAAPL,2000Q1,\nAA,2000Q1,2016Q4\nBMS,2019Q2,2019Q2\n"


def with_file(cfg, tmp_path, text: str):
    path = tmp_path / "sp500.csv"
    path.write_text(text, encoding="utf-8")
    c = copy.deepcopy(cfg)
    c["sp500_panel"]["file"] = str(path)
    return c


def test_read_keeps_an_empty_end_open_and_a_zero_length_record(cfg, tmp_path):
    out = S.read_records(with_file(cfg, tmp_path, GOOD))
    assert out["ticker"].tolist() == ["AAPL", "AA", "BMS"]
    assert pd.isna(out.loc[0, "end_quarter"]) and out.loc[1, "end_quarter"] == "2016Q4"
    assert out.loc[2, "start_quarter"] == out.loc[2, "end_quarter"]


@pytest.mark.parametrize("change, error", [
    (lambda t: t.replace("2016Q4", "2016Q5"), ValueError),            # not a quarter
    (lambda t: t.replace("2016Q4", "2016-12"), ValueError),           # another format
    (lambda t: t.replace("AA,2000Q1,2016Q4", "AA,2017Q1,2016Q4"), ValueError),  # ends before
    (lambda t: t.replace("AAPL,", ",", 1), ValueError),               # no ticker
    (lambda t: t.replace("start_quarter", "start"), KeyError),        # a column is missing
])
def test_read_refuses_what_it_cannot_trust(cfg, tmp_path, change, error):
    with pytest.raises(error):
        S.read_records(with_file(cfg, tmp_path, change(GOOD)))


# ---------------------------------------------------------------------------
# the build: rows, the (gvkey, KEY) join, copied values
# ---------------------------------------------------------------------------
def synthetic_prices(panel: pd.DataFrame, cfg) -> pd.DataFrame:
    """One price row per base row, dated on the investment date, in the price contract."""
    period = S.period_from_key(panel["KEY"], cfg)
    _, formation = S.quarter_dates(period, cfg)
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
    old = make_source(seed, tickers=("AA.3", "BBB", "CCC"), first="2014Q1", n_quarters=12)
    new = make_source(seed, tickers=("AA",), first="2016Q1", n_quarters=8).assign(gvkey="000004")
    base = build_base(cfg, pd.concat([old, new], ignore_index=True))
    prices = synthetic_prices(base, cfg)
    # AA.3 has no price in 2015Q2; a BBB row carries AA.3's KEY and must not fill it
    hole = prices["KEY"] == "2015Q2_AA.3"
    decoy = prices.loc[hole].assign(gvkey=pd.array(["000002"], dtype="string"), Close=-1.0)
    prices = pd.concat([prices[~hole], decoy], ignore_index=True)
    records = recs(cfg, ("AA", "2014Q1", "2016Q4"), ("BBB", "2015Q2", None))
    exchanges = {"000001": 11, "000002": 14, "000003": 11, "000004": 11}
    table, report = S.build_sp500_panel(cfg, records=records, panel=base, prices=prices,
                                        exchanges=exchanges)
    return base, prices, table, report


def test_rows_are_every_base_row_of_the_assigned_firms(synthetic_build):
    base, _, table, report = synthetic_build
    assert list(table.columns) == list(S.SP500_PANEL_COLUMNS) == list(schema.INDEX_PANEL_COLUMNS)
    # AA.3 and BBB; not CCC (no record), not the AA spin-off (fewer quarters)
    expected = base[base["gvkey"].isin(["000001", "000002"])] \
        .sort_values(["gvkey", "fiscal_period_end_date"], kind="mergesort").reset_index(drop=True)
    assert len(table) == len(expected) == 24
    pd.testing.assert_frame_equal(table[list(schema.PANEL_COLUMNS)], expected)
    assert not table.duplicated(["gvkey", "KEY"]).any()
    assert report["flag_cross_check"] is True and report["firms"] == 2


def test_prices_join_on_gvkey_and_key(synthetic_build):
    _, prices, table, _ = synthetic_build
    t = table.set_index("KEY")
    hole = t.loc["2015Q2_AA.3"]
    assert pd.isna(hole["Date"]) and pd.isna(hole["Close"])     # the decoy did not match
    assert (table["Close"].dropna() > 0).all()
    p = prices.set_index(["gvkey", "KEY"])
    matched = table.dropna(subset=["Date"]).set_index(["gvkey", "KEY"])
    for col in ("Date", "Ticker", "Close", "Quaterly_return", "MomentomQ3"):
        np.testing.assert_array_equal(matched[col].to_numpy(),
                                      p.loc[matched.index, col].to_numpy(), err_msg=col)
    with_price = table["Date"].notna()
    assert (table.loc[with_price, "formation_date"] == table.loc[with_price, "Date"]).all()


def test_flags_in_the_build(synthetic_build):
    _, _, table, report = synthetic_build
    t = table.set_index("KEY")["in_index"]
    assert t["2014Q1_AA.3"] == 1 and t["2016Q3_AA.3"] == 1 and t["2016Q4_AA.3"] == 0
    assert t["2015Q1_BBB"] == 0 and t["2015Q2_BBB"] == 1 and t["2016Q4_BBB"] == 1
    assert table["in_index"].dtype == np.int8 and set(table["in_index"]) <= {0, 1}
    assert report["rows_in_index"] == int(table["in_index"].sum())
    assert report["moved_over_current_holder"]["symbol"].tolist() == ["AA.3"]


# ---------------------------------------------------------------------------
# kept apart from the index panels (researcher 2026-11-17)
# ---------------------------------------------------------------------------
def test_output_is_apart_from_the_index_panels(cfg):
    out = cfg["_paths"]["output_dir"]
    root = S.output_root(cfg)
    index_root = out / cfg["index_panel"]["output_dir"]
    assert root == out / "sp500_panel"
    assert root != index_root and index_root not in root.parents and root not in index_root.parents
    qqq_file = cfg["index_panel"]["file_pattern"].format(name="qqq100")
    assert cfg["sp500_panel"]["panel_file"] != qqq_file
    assert "sp500" not in cfg["indices"]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[-1] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1])
            names |= {a.name for a in node.names}
    return names


def test_code_does_not_import_the_index_panel_code():
    for path in (ROOT / "src" / "sp500_panel.py", ROOT / "scripts" / "run_build_sp500_panel.py"):
        assert not _imports(path) & {"index_panel", "run_build_index_panel", "validation"}, path


# ---------------------------------------------------------------------------
# the real file - measured on 2026-11-17
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def real_sp500(cfg):
    out = cfg["_paths"]["output_dir"]
    needed = [out / cfg["output"]["panel_file"], out / cfg["prices"]["output_file"],
              S.records_path(cfg), cfg["_paths"]["input_parquet"]]
    if not all(p.exists() for p in needed):
        pytest.skip("panel, prices, the S&P 500 file or the source file not available")
    return S.build_sp500_panel(cfg)


def test_real_counts(real_sp500):
    table, rep = real_sp500
    assert rep["records_in_file"] == 872 and rep["tickers_in_file"] == 852
    assert rep["records_by_status"].to_dict() == {
        S.ASSIGNED: 799, S.TICKER_NOT_IN_PANEL: 44, S.NO_REPORT_ROW_IN_RANGE: 20,
        S.NOT_NYSE_NASDAQ: 3, S.TIE: 3, S.ZERO_LENGTH: 3}
    assert rep["assigned_by_decision"].to_dict() == {
        S.ONLY_CANDIDATE: 761, S.EXCHANGE_CHECK: 4, S.MOST_QUARTERS: 34}
    assert rep["firms"] == 785 and table["gvkey"].nunique() == 785
    assert len(table) == 121_263
    assert rep["rows_with_price"] == 113_688
    assert rep["rows_in_index"] == 48_014
    assert rep["flag_cross_check"] is True
    assert sorted(rep["moved_over_current_holder"]["ticker"]) == \
        ["AA", "ADT", "ALTR", "EP", "GENZ", "GLK", "KG", "NSM", "SE", "SUN"]
    assert rep["moved_suffix_only"] == 48
    s = rep["status"]
    assert set(s.loc[s["status"] == S.NOT_NYSE_NASDAQ, "ticker"]) == {"CBOE", "SBNY", "XEC"}
    assert set(s.loc[s["status"] == S.TIE, "ticker"]) == {"DD", "LSI", "MMI"}


def test_real_examples(real_sp500):
    table, _ = real_sp500
    t = table.set_index("KEY")["in_index"]
    assert t["2024Q2_AAL"] == 1 and t["2024Q3_AAL"] == 0
    assert t["2023Q2_ABNB"] == 0 and t["2023Q3_ABNB"] == 1
    assert t["2015Q1_AA.3"] == 1 and t["2010Q1_EP.1"] == 1
    assert t["1999Q4_AAPL"] == 0 and t["2016Q4_AAPL"] == 1
    assert t["2013Q2_AMD"] == 1 and t["2013Q3_AMD"] == 0 and t["2017Q1_AMD"] == 1
    assert "2015Q1_AA" not in t.index and "2010Q1_EP" not in t.index
    assert (table.loc[table["KEY"].str[:6] < "2000Q1", "in_index"] == 0).all()


def test_real_values_are_copied_untouched(real_sp500, cfg):
    table, _ = real_sp500
    assert list(table.columns) == list(schema.INDEX_PANEL_COLUMNS)
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

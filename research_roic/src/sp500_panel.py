"""The S&P 500 panel: every base-panel row of the firms that the S&P 500 file's
records go to, the price columns joined on ``(gvkey, KEY)``, and ``in_index`` -
1 when the firm was in the index in that quarter, 0 when it was not.

Kept apart from the index panels on purpose (researcher 2026-11-17)
-------------------------------------------------------------------
Its own code (this module), its own config section ``sp500_panel`` and its own
output folder ``output/sp500_panel/``. Nothing here imports
``src/index_panel.py`` or reads ``indices``/``index_panel``, so a change to one
panel can never move the other. Shared is only the project base, read-only:
``data_io``, the base and price column contracts in ``schema`` and the quarter
rule in ``keys``.

Rules enforced here
-------------------
* The file holds ``ticker, start_quarter, end_quarter``. A record goes to at
  most ONE firm (researcher 2026-11-17):
    1. candidates - firms whose ticker is the record's ticker exactly, or that
       ticker with Compustat's ``.<n>`` suffix, the mark of an older firm whose
       ticker a newer one took (``AA.3`` = Alcoa Inc). A firm is a candidate
       only with a report row inside the record's quarters;
    2. the exchange check - only candidates whose ``exchg`` is in
       ``sp500_panel.exchanges`` stay (11 NYSE, 14 NASDAQ). ``exchg`` is
       Compustat's header value: today's exchange, or the last one;
    3. the record goes to the candidate with the most report quarters inside
       it. A tie leaves the record unassigned and listed - never a guess.
  ``AAX`` or ``AA.B`` is not ``AA``: only ``AA`` and ``AA.<digits>`` are.
* ``in_index = 1`` when an assigned record of the same gvkey has
  ``start_quarter <= quarter < end_quarter`` - the quarter read from the KEY,
  an empty end still in (researcher 2026-09-13). The file holds quarters, not
  days, so the last quarter before a removal is 1 whatever day it left on.
* Prices join on ``(gvkey, KEY)``, left from the fundamentals: a quarter without
  a price keeps its fundamentals and gets empty price columns. Values are
  copied, never recomputed.
* Choosing between firms that share a ticker reads the report rows of the
  record's whole range - it names the firm the record means. It reads no price
  and no return.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from . import keys, schema

LOGGER = logging.getLogger("roic.sp500_panel")

QUARTER_PATTERN = r"\d{4}Q[1-4]"
SUFFIX_PATTERN = r"\.\d+$"          # Compustat's mark of an older holder: AA.3
OPEN_END = "9999Q4"                 # an empty end_quarter, inside comparisons only

JOIN_KEYS: tuple[str, ...] = ("gvkey", "KEY")
IDENTITY_COLUMNS: tuple[str, ...] = ("KEY", "gvkey", "symbol", "company")
FLAG_COLUMNS: tuple[str, ...] = (
    "quarter_end", "formation_date", "in_index", "months_fiscal_end_to_formation",
)
PRICE_VALUE_COLUMNS: tuple[str, ...] = tuple(c for c in schema.PRICE_COLUMNS
                                             if c not in JOIN_KEYS)
# every base column, every price column but the join key, then the four flags -
# derived from the base and price contracts, so no column is listed twice
SP500_PANEL_COLUMNS: tuple[str, ...] = (
    schema.PANEL_COLUMNS + PRICE_VALUE_COLUMNS + FLAG_COLUMNS
)
ROIC_COLUMN = "calc_roic_posttax_quarterly_ic_raw"

# what became of a record, in the order the reasons are tested
ASSIGNED = "ASSIGNED"
ZERO_LENGTH = "ZERO_LENGTH"
TICKER_NOT_IN_PANEL = "TICKER_NOT_IN_PANEL"
NO_REPORT_ROW_IN_RANGE = "NO_REPORT_ROW_IN_RANGE"
NOT_NYSE_NASDAQ = "NOT_NYSE_NASDAQ"
TIE = "TIE"
RECORD_STATUSES: tuple[str, ...] = (ASSIGNED, TICKER_NOT_IN_PANEL, NO_REPORT_ROW_IN_RANGE,
                                    NOT_NYSE_NASDAQ, TIE, ZERO_LENGTH)
# how an assigned record was decided
ONLY_CANDIDATE = "ONLY_CANDIDATE"   # one firm, and it passed the exchange check
EXCHANGE_CHECK = "EXCHANGE_CHECK"   # several firms, one passed the exchange check
MOST_QUARTERS = "MOST_QUARTERS"     # several passed it; the most report quarters won
DECISIONS: tuple[str, ...] = (ONLY_CANDIDATE, EXCHANGE_CHECK, MOST_QUARTERS)


# ---------------------------------------------------------------------------
# config and paths
# ---------------------------------------------------------------------------
def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    if "sp500_panel" not in cfg:
        raise KeyError("config.yaml has no `sp500_panel` section")
    return cfg["sp500_panel"]


def records_path(cfg: dict[str, Any]) -> Path:
    """The S&P 500 file, relative to config.yaml like every other path."""
    return (Path(cfg["_meta"]["base_dir"]) / settings(cfg)["file"]).resolve()


def output_root(cfg: dict[str, Any]) -> Path:
    """``output/sp500_panel/`` - the only folder this build writes to."""
    return cfg["_paths"]["output_dir"] / settings(cfg)["output_dir"]


# ---------------------------------------------------------------------------
# the S&P 500 file
# ---------------------------------------------------------------------------
def read_records(cfg: dict[str, Any]) -> pd.DataFrame:
    """One row per record of the file. Every value is read as text, and only an
    empty cell is missing."""
    raw = pd.read_csv(records_path(cfg), dtype=str, keep_default_na=False, na_values=[""])
    return standardize_records(raw, settings(cfg))


def standardize_records(raw: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    """``source_row, ticker, start_quarter, end_quarter`` - or an error, never a guess.

    A record with ``start_quarter == end_quarter`` is kept: it covers no quarter,
    and the assignment lists it as ZERO_LENGTH.
    """
    cols = spec["columns"]
    missing = [f"{logical} ({cols.get(logical)!r})" for logical in ("ticker", "start", "end")
               if cols.get(logical) not in raw.columns]
    if missing:
        raise KeyError(f"the S&P 500 file lacks the columns {missing}")

    ticker = raw[cols["ticker"]].astype("string")
    start = raw[cols["start"]].astype("string")
    end = raw[cols["end"]].astype("string")
    if ticker.isna().any():
        raise ValueError(f"{int(ticker.isna().sum())} records have no ticker")
    bad = (~start.fillna("").str.fullmatch(QUARTER_PATTERN)
           | (end.notna() & ~end.fillna("").str.fullmatch(QUARTER_PATTERN)))
    bad = bad.fillna(True).to_numpy(dtype=bool)
    if bad.any():
        sample = raw.loc[bad, [cols["ticker"], cols["start"], cols["end"]]].head(5).to_dict("records")
        raise ValueError(f"{int(bad.sum())} records carry a quarter that is not YYYYQn, e.g. {sample}")
    # YYYYQn strings of one width sort in time order
    before = (end.notna() & (end < start)).fillna(False).to_numpy(dtype=bool)
    if before.any():
        sample = raw.loc[before, [cols["ticker"], cols["start"], cols["end"]]].head(5).to_dict("records")
        raise ValueError(f"{int(before.sum())} records end before they start, e.g. {sample}")
    return pd.DataFrame({
        "source_row": np.arange(len(raw)),
        "ticker": pd.array(ticker.to_numpy(), dtype="string"),
        "start_quarter": pd.array(start.to_numpy(), dtype="string"),
        "end_quarter": pd.array(end.to_numpy(), dtype="string"),
    })


# ---------------------------------------------------------------------------
# reading the panels and the exchange
# ---------------------------------------------------------------------------
def read_rows(path: Path, gvkeys: Iterable[str] | None = None,
              columns: list[str] | None = None) -> pd.DataFrame:
    """Rows of a panel - only these firms' when ``gvkeys`` is given, filtered
    while the parquet is read."""
    flt = ds.field("gvkey").isin(list(gvkeys)) if gvkeys is not None else None
    return ds.dataset(str(path), format="parquet").to_table(columns=columns, filter=flt).to_pandas()


def read_exchanges(cfg: dict[str, Any], gvkeys: Iterable[str] | None = None) -> pd.Series:
    """``exchg`` per gvkey from the source file - one value per firm, or an error.

    Compustat's ``exchg`` is a header value: the exchange today, or the last one
    of a firm that is gone. A firm without a value is NaN and fails the check.
    """
    field = settings(cfg)["exchange_field"]
    flt = ds.field("gvkey").isin(list(gvkeys)) if gvkeys is not None else None
    d = (ds.dataset(str(cfg["_paths"]["input_parquet"]), format="parquet")
         .to_table(columns=["gvkey", field], filter=flt).to_pandas())
    n = d.groupby("gvkey")[field].nunique(dropna=False)
    if (n > 1).any():
        raise ValueError(f"{int((n > 1).sum())} firms carry more than one {field!r}, "
                         f"e.g. {n[n > 1].index[:5].tolist()}")
    out = d.drop_duplicates("gvkey").set_index("gvkey")[field].astype("float64")
    out.index = out.index.astype(str)
    return out.rename("exchg")


# ---------------------------------------------------------------------------
# the quarter of a row and its two dates
# ---------------------------------------------------------------------------
def period_from_key(key: pd.Series, cfg: dict[str, Any]) -> pd.Series:
    """``2024Q2`` from ``2024Q2_AAL``; a KEY that is not ``YYYYQn<sep>...`` raises."""
    sep = re.escape(cfg["timing"]["key_separator"])
    k = key.astype("string")
    period = k.str.extract(rf"^({QUARTER_PATTERN}){sep}.+$", expand=False)
    bad = (k.notna() & period.isna()).to_numpy(dtype=bool)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} KEYs are not 'YYYYQn{cfg['timing']['key_separator']}"
                         f"TICKER', e.g. {k[bad].head(5).tolist()}")
    return period.astype("string")


def quarter_dates(period: pd.Series, cfg: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    """``quarter_end`` and ``formation_date`` of a quarter such as ``2024Q2``.

    2024Q2 -> 2024-06-30 and 2024-08-31 · 2025Q4 -> 2025-12-31 and 2026-02-28.
    The offset is ``timing.quarter_shift_months``, the rule that maps a price
    month back to its quarter, so ``formation_date`` is the price ``Date``.
    """
    shift = int(cfg["timing"]["quarter_shift_months"])
    uniq = pd.Series(period.dropna().unique(), dtype="object")
    q_end = pd.PeriodIndex(uniq.astype(str), freq="Q").to_timestamp(how="end").normalize()
    lookup_end = dict(zip(uniq, q_end))
    lookup_form = dict(zip(uniq, q_end + pd.offsets.MonthEnd(shift)))
    quarter_end = pd.to_datetime(period.map(lookup_end)).astype("datetime64[ns]")
    formation = pd.to_datetime(period.map(lookup_form)).astype("datetime64[ns]")
    return quarter_end, formation


# ---------------------------------------------------------------------------
# from a ticker record to a firm
# ---------------------------------------------------------------------------
def base_ticker(symbol: pd.Series) -> pd.Series:
    """The ticker without Compustat's ``.<n>`` suffix: ``AA.3`` -> ``AA``.

    Only a dot followed by digits is removed, so ``BRK.B`` stays ``BRK.B`` and
    ``AAX`` stays ``AAX``. Computed once per distinct symbol.
    """
    s = symbol.astype("string")
    uniq = pd.Series(s.dropna().unique(), dtype="string")
    lookup = dict(zip(uniq, uniq.str.replace(SUFFIX_PATTERN, "", regex=True)))
    return pd.Series(pd.array(s.map(lookup), dtype="string"), index=symbol.index)


def firm_quarters(ident: pd.DataFrame, tickers: Iterable[str], cfg: dict[str, Any]) -> pd.DataFrame:
    """The report quarters of every firm whose ticker is one of ``tickers``,
    exactly or with the ``.<n>`` suffix: ``gvkey, symbol, company, base, period_key``."""
    keyed = ident[ident["KEY"].notna() & ident["symbol"].notna()]
    base = base_ticker(keyed["symbol"])
    mine = base.isin(set(map(str, tickers))).to_numpy(dtype=bool)
    rows = keyed[mine]
    return pd.DataFrame({
        "gvkey": pd.array(rows["gvkey"].astype(str).to_numpy(), dtype="string"),
        "symbol": pd.array(rows["symbol"].to_numpy(), dtype="string"),
        "company": pd.array(rows["company"].to_numpy(), dtype="string"),
        "base": pd.array(base[mine].to_numpy(), dtype="string"),
        "period_key": pd.array(period_from_key(rows["KEY"], cfg).to_numpy(), dtype="string"),
    })


def _in_range(period: pd.Series, start: pd.Series, end: pd.Series) -> np.ndarray:
    """``start <= period < end``, an empty end open; quarters as YYYYQn text."""
    p = period.astype("string")
    hit = (p >= start.astype("string")) & (p < end.astype("string").fillna(OPEN_END))
    return hit.fillna(False).to_numpy(dtype=bool)


def assign_records(records: pd.DataFrame, firms: pd.DataFrame, exchanges: pd.Series | dict,
                   cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Each record to at most one firm: ``(assigned, status, candidates)``.

    ``assigned`` - one row per assigned record and its firm. ``status`` - one
    row per record with what became of it. ``candidates`` - one row per
    (record, firm with a report row inside it): the audit of every choice.
    """
    allowed = {float(x) for x in settings(cfg)["exchanges"]}
    exch = pd.Series(exchanges, dtype="float64")
    exch.index = exch.index.astype(str)
    rec = records[["source_row", "ticker", "start_quarter", "end_quarter"]].copy()

    # --- candidates: report quarters inside each record's range
    m = rec.merge(firms, left_on="ticker", right_on="base", how="inner")
    m = m[_in_range(m["period_key"], m["start_quarter"], m["end_quarter"])]
    cand = (m.groupby(["source_row", "gvkey"], sort=True)
            .agg(symbol=("symbol", "first"), company=("company", "first"),
                 quarters_in_range=("period_key", "nunique"))
            .reset_index())
    cand = cand.merge(rec, on="source_row", how="left")
    cand["exchg"] = cand["gvkey"].astype(str).map(exch).astype("float64")
    cand["exact_ticker"] = (cand["symbol"] == cand["ticker"]).fillna(False).astype(bool)
    cand["exchange_ok"] = cand["exchg"].isin(allowed)

    # --- the exchange check, then the most report quarters; a tie decides nothing
    ok = cand[cand["exchange_ok"]]
    best = ok.groupby("source_row")["quarters_in_range"].transform("max")
    top = ok[ok["quarters_in_range"] == best]
    n_top = top.groupby("source_row")["gvkey"].transform("size")
    winners = top[n_top == 1]
    tied_rows = set(top.loc[n_top > 1, "source_row"])

    n_cand = cand.groupby("source_row").size()
    n_ok = ok.groupby("source_row").size()
    decided = np.where(winners["source_row"].map(n_cand) == 1, ONLY_CANDIDATE,
                       np.where(winners["source_row"].map(n_ok) == 1, EXCHANGE_CHECK, MOST_QUARTERS))
    assigned = winners.assign(decided_by=decided)[
        ["source_row", "ticker", "start_quarter", "end_quarter", "gvkey", "symbol", "company",
         "exchg", "quarters_in_range", "decided_by"]].reset_index(drop=True)

    # --- what became of every record
    zero = (rec["end_quarter"].notna() & (rec["end_quarter"] == rec["start_quarter"])) \
        .fillna(False).to_numpy(dtype=bool)
    known = rec["ticker"].isin(set(firms["base"].dropna())).to_numpy(dtype=bool)
    with_rows = rec["source_row"].isin(set(cand["source_row"])).to_numpy(dtype=bool)
    with_ok = rec["source_row"].isin(set(ok["source_row"])).to_numpy(dtype=bool)
    tied = rec["source_row"].isin(tied_rows).to_numpy(dtype=bool)
    reason = np.select(
        [zero, ~known, ~with_rows, ~with_ok, tied],
        [ZERO_LENGTH, TICKER_NOT_IN_PANEL, NO_REPORT_ROW_IN_RANGE, NOT_NYSE_NASDAQ, TIE],
        default=ASSIGNED)
    chosen = assigned.set_index("source_row")
    status = rec.assign(
        status=reason,
        decided_by=rec["source_row"].map(chosen["decided_by"]),
        gvkey=rec["source_row"].map(chosen["gvkey"]),
        symbol=rec["source_row"].map(chosen["symbol"]),
        company=rec["source_row"].map(chosen["company"]),
        quarters_in_range=rec["source_row"].map(chosen["quarters_in_range"]).astype("Int64"),
        n_candidates=rec["source_row"].map(n_cand).fillna(0).astype(int),
        n_candidates_exchange_ok=rec["source_row"].map(n_ok).fillna(0).astype(int),
        candidates=rec["source_row"].map(_candidate_text(cand)).fillna(""),
        ticker_holders_in_panel=rec["ticker"].map(_holder_text(firms)).fillna(""),
    )
    if (status["status"] == ASSIGNED).sum() != len(assigned):
        raise AssertionError("assigned records and record statuses disagree")

    # --- the audit: why each candidate was chosen or not
    won = set(zip(assigned["source_row"], assigned["gvkey"]))
    at_top = set(zip(top["source_row"], top["gvkey"]))
    pair = list(zip(cand["source_row"], cand["gvkey"]))
    cand["outcome"] = np.select(
        [np.array([p in won for p in pair], dtype=bool),
         ~cand["exchange_ok"].to_numpy(dtype=bool),
         np.array([p in at_top for p in pair], dtype=bool)],
        ["CHOSEN", "EXCHANGE_NOT_ALLOWED", "TIED"], default="FEWER_QUARTERS")
    cand = cand[["source_row", "ticker", "start_quarter", "end_quarter", "gvkey", "symbol",
                 "company", "exchg", "exact_ticker", "exchange_ok", "quarters_in_range",
                 "outcome"]].reset_index(drop=True)

    if assigned["source_row"].duplicated().any():
        raise AssertionError("a record went to more than one firm")
    LOGGER.info("S&P 500 records: %d, assigned %d (%s); not assigned %s", len(rec), len(assigned),
                assigned["decided_by"].value_counts().to_dict(),
                status.loc[status["status"] != ASSIGNED, "status"].value_counts().to_dict())
    return assigned, status.reset_index(drop=True), cand


def _candidate_text(cand: pd.DataFrame) -> pd.Series:
    """``AA.3 ALCOA INC (exchg 11, 67q); AA ALCOA CORP (exchg 11, 11q)`` per record."""
    if cand.empty:
        return pd.Series(dtype="object")
    c = cand.sort_values(["source_row", "quarters_in_range", "symbol"], ascending=[True, False, True])
    exch = c["exchg"].map(lambda v: "NA" if pd.isna(v) else str(int(v)))
    text = (c["symbol"].astype(str) + " " + c["company"].astype(str) + " (exchg " + exch + ", "
            + c["quarters_in_range"].astype(str) + "q)")
    return text.groupby(c["source_row"]).agg("; ".join)


def _holder_text(firms: pd.DataFrame) -> pd.Series:
    """Every firm in the panel that holds a ticker, exactly or with ``.<n>``, and
    the quarters of its reports: ``AA ALCOA CORP (2014Q1-2026Q2)`` per ticker.
    It shows why a record found no report row inside its own range."""
    if firms.empty:
        return pd.Series(dtype="object")
    f = (firms.groupby(["base", "gvkey"], sort=True)
         .agg(symbol=("symbol", "first"), company=("company", "first"),
              first=("period_key", "min"), last=("period_key", "max"))
         .reset_index().sort_values(["base", "first", "symbol"]))
    text = (f["symbol"].astype(str) + " " + f["company"].astype(str) + " ("
            + f["first"].astype(str) + "-" + f["last"].astype(str) + ")")
    return text.groupby(f["base"].astype(str)).agg("; ".join)


# ---------------------------------------------------------------------------
# the flag
# ---------------------------------------------------------------------------
def in_index_flag(gvkey: pd.Series, period: pd.Series, assigned: pd.DataFrame) -> np.ndarray:
    """1 where an assigned record of the same gvkey covers the quarter, else 0 (``int8``).

    Covers = ``start_quarter <= period < end_quarter``, an empty end open. Each
    row is compared with its own firm's records only.
    """
    n = len(gvkey)
    rows = pd.DataFrame({"_row": np.arange(n),
                         "gvkey": pd.Series(gvkey).astype(str).to_numpy(),
                         "period": pd.array(pd.Series(period).astype("string").to_numpy(),
                                            dtype="string")})
    recs = pd.DataFrame({"gvkey": assigned["gvkey"].astype(str).to_numpy(),
                         "start": pd.array(assigned["start_quarter"].to_numpy(), dtype="string"),
                         "end": pd.array(assigned["end_quarter"].to_numpy(), dtype="string")})
    j = rows.merge(recs, on="gvkey", how="inner")
    hit = _in_range(j["period"], j["start"], j["end"])
    out = np.zeros(n, dtype=np.int8)
    out[j.loc[hit, "_row"].to_numpy()] = 1
    return out


def investable_quarters(assigned: pd.DataFrame, last_period: str) -> pd.DataFrame:
    """Every ``(gvkey, quarter)`` the records mark as in the index, up to ``last_period``.

    Enumerated from the assigned records alone, independently of the panel, so
    it can name the quarters without a report row - and cross-check the flag.
    """
    last = pd.Period(last_period, freq="Q")
    parts = []
    for r in assigned.itertuples(index=False):
        first = pd.Period(str(r.start_quarter), freq="Q")
        stop = last if pd.isna(r.end_quarter) else min(last, pd.Period(str(r.end_quarter), freq="Q") - 1)
        if first > stop:
            continue
        span = pd.period_range(first, stop, freq="Q")
        parts.append(pd.DataFrame({"gvkey": str(r.gvkey), "company": r.company,
                                   "period_key": span.astype(str)}))
    if not parts:
        return pd.DataFrame(columns=["gvkey", "company", "period_key"])
    out = pd.concat(parts, ignore_index=True)
    return out.drop_duplicates(["gvkey", "period_key"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# the join and the contract
# ---------------------------------------------------------------------------
def assert_unique(df: pd.DataFrame, what: str) -> None:
    keyed = df[df["KEY"].notna()]
    dup = keyed.duplicated(list(JOIN_KEYS), keep=False)
    if dup.any():
        sample = keyed.loc[dup, list(JOIN_KEYS)].head(5).to_dict("records")
        raise ValueError(f"{int(dup.sum())} {what} rows share a (gvkey, KEY); e.g. {sample}")


def join_prices(panel: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Left join from the fundamentals on ``(gvkey, KEY)``, one price row at most."""
    if prices["KEY"].isna().any():
        raise ValueError("every price row must carry a KEY")
    assert_unique(panel, "fundamentals")
    assert_unique(prices, "price")
    # the base panel's dtypes decide, so every base value is copied as it is
    prices = prices.astype({c: panel[c].dtype for c in JOIN_KEYS})
    joined = panel.merge(prices[list(JOIN_KEYS) + list(PRICE_VALUE_COLUMNS)],
                         on=list(JOIN_KEYS), how="left", validate="many_to_one")
    if len(joined) != len(panel):
        raise ValueError("the price join changed the row count")
    return joined


def finalize(joined: pd.DataFrame, flags: dict[str, Any]) -> pd.DataFrame:
    """The joined frame in contract order, with the flag columns in their dtypes.

    The base and price columns keep the dtypes they were read with, so a value
    in this panel is the very value in ``panel.parquet``/``prices.parquet``.
    """
    missing = [c for c in SP500_PANEL_COLUMNS if c not in joined.columns and c not in flags]
    if missing:
        raise KeyError(f"S&P 500 panel columns not produced: {missing}")
    columns: dict[str, Any] = {c: joined[c] for c in SP500_PANEL_COLUMNS if c not in FLAG_COLUMNS}
    columns["quarter_end"] = pd.to_datetime(
        pd.Series(flags["quarter_end"], index=joined.index)).astype("datetime64[ns]")
    columns["formation_date"] = pd.to_datetime(
        pd.Series(flags["formation_date"], index=joined.index)).astype("datetime64[ns]")
    columns["in_index"] = pd.Series(np.asarray(flags["in_index"], dtype="int8"), index=joined.index)
    columns["months_fiscal_end_to_formation"] = pd.Series(
        flags["months_fiscal_end_to_formation"], index=joined.index).astype("Int8")
    out = pd.DataFrame(columns, index=joined.index)[list(SP500_PANEL_COLUMNS)]
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------
def build_sp500_panel(cfg: dict[str, Any], records: pd.DataFrame | None = None,
                      panel: pd.DataFrame | None = None, prices: pd.DataFrame | None = None,
                      exchanges: pd.Series | dict | None = None
                      ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the S&P 500 panel. ``records``, ``panel``, ``prices`` and ``exchanges``
    are injectable; whatever is not given is read from its file."""
    report: dict[str, Any] = {"index": "sp500"}
    if records is None:
        records = read_records(cfg)
    out = cfg["_paths"]["output_dir"]
    panel_path = out / cfg["output"]["panel_file"]
    prices_path = out / cfg["prices"]["output_file"]

    # --- 1. which firm each record means
    ident = (read_rows(panel_path, columns=list(IDENTITY_COLUMNS)) if panel is None
             else panel[list(IDENTITY_COLUMNS)])
    firms = firm_quarters(ident, records["ticker"].dropna().unique(), cfg)
    if exchanges is None:
        exchanges = read_exchanges(cfg, sorted(firms["gvkey"].unique()))
    assigned, status, candidates = assign_records(records, firms, exchanges, cfg)
    gvkeys = sorted(assigned["gvkey"].astype(str).unique())

    # --- 2. every row of those firms, and their prices
    if panel is None:
        panel = read_rows(panel_path, gvkeys)
    else:
        panel = panel[panel["gvkey"].astype(str).isin(gvkeys)]
    if prices is None:
        prices = read_rows(prices_path, gvkeys)
    else:
        prices = prices[prices["gvkey"].astype(str).isin(gvkeys)]
    panel = panel.sort_values(["gvkey", "fiscal_period_end_date"], kind="mergesort") \
                 .reset_index(drop=True)
    keyed = panel["KEY"].notna()
    joined = join_prices(panel, prices)

    # --- 3. the quarter of every row: from the KEY, checked against the fiscal date
    from_date = keys.add_period_columns(panel[["fiscal_period_end_date"]], cfg)["period_key"]
    period = period_from_key(panel["KEY"], cfg)
    disagree = (period.notna() & (period != from_date.astype("string"))).fillna(False) \
        .to_numpy(dtype=bool)
    if disagree.any():
        raise ValueError(f"{int(disagree.sum())} KEYs name another quarter than their "
                         f"fiscal_period_end_date, e.g. {panel.loc[disagree, 'KEY'].head(5).tolist()}")
    period = period.fillna(from_date.astype("string"))
    quarter_end, formation = quarter_dates(period, cfg)

    flag = in_index_flag(panel["gvkey"], period, assigned)
    fiscal = pd.to_datetime(panel["fiscal_period_end_date"])
    months = ((formation.dt.year - fiscal.dt.year) * 12
              + (formation.dt.month - fiscal.dt.month))
    table = finalize(joined, {
        "quarter_end": quarter_end, "formation_date": formation,
        "in_index": flag, "months_fiscal_end_to_formation": months,
    })

    # --- 4. the cross-check. The quarters the records mark as in the index,
    # enumerated from the records alone, through the last quarter the file names
    first_period = str(records["start_quarter"].min())
    last_period = str(max(records["start_quarter"].max(), records["end_quarter"].dropna().max()))
    investable = investable_quarters(assigned, last_period)
    per = period.astype(str).to_numpy()
    gv = panel["gvkey"].astype(str).to_numpy()
    have = pd.MultiIndex.from_arrays([gv, per])
    want = pd.MultiIndex.from_arrays([investable["gvkey"].astype(str).to_numpy(),
                                      investable["period_key"].astype(str).to_numpy()])
    covered = (flag == 1) & (per <= last_period)
    flagged = pd.MultiIndex.from_arrays([gv[covered], per[covered]])
    no_row = investable[~want.isin(have)].reset_index(drop=True)
    cross_check = bool(set(want[want.isin(have)]) == set(flagged))

    has_price = table["Date"].notna()
    firm_price = has_price.groupby(table["gvkey"].astype(str)).any()
    firm_info = (table.groupby(table["gvkey"].astype(str))
                 .agg(symbol=("symbol", "first"), company=("company", "first"),
                      rows=("KEY", "size"), rows_in_index=("in_index", "sum")))
    inside = flag == 1
    # records that went to an older firm (AA.3) rather than the exact ticker:
    # over a current holder of the ticker that was a candidate too, or alone
    had_exact = set(candidates.loc[candidates["exact_ticker"], "source_row"])
    moved = assigned[assigned["symbol"] != assigned["ticker"]]
    over_holder = moved["source_row"].isin(had_exact).to_numpy(dtype=bool)
    report.update({
        "records_in_file": int(len(records)),
        "tickers_in_file": int(records["ticker"].nunique()),
        "first_period_in_file": first_period,
        "last_period_in_file": last_period,
        "records_starting_at_file_start": int((records["start_quarter"] == first_period).sum()),
        "assigned": assigned,
        "status": status,
        "candidates": candidates,
        "records_by_status": status["status"].value_counts().reindex(RECORD_STATUSES, fill_value=0),
        "assigned_by_decision": assigned["decided_by"].value_counts().reindex(DECISIONS, fill_value=0),
        "moved_over_current_holder": moved[over_holder].reset_index(drop=True),
        "moved_suffix_only": int((~over_holder).sum()),
        "firms": int(len(gvkeys)),
        "firms_without_rows": sorted(set(gvkeys) - set(panel["gvkey"].astype(str))),
        "firms_without_prices": firm_info[~firm_price].reset_index(),
        "rows_without_key": int((~keyed).sum()),
        "rows_in_panel": int(len(table)),
        "rows_with_price": int(has_price.sum()),
        "rows_in_index": int(inside.sum()),
        "rows_in_index_without_price": int((inside & ~has_price.to_numpy()).sum()),
        "rows_before_file_start": int((per < first_period).sum()),
        "rows_in_index_after_file": int((inside & (per > last_period)).sum()),
        "period_key": per,
        "investable_without_row": no_row,
        "flag_cross_check": cross_check,
    })
    LOGGER.info("S&P 500 panel: %d rows, %d firms, %d with price, %d in the index",
                len(table), table["gvkey"].nunique(), report["rows_with_price"],
                report["rows_in_index"])
    if not cross_check:
        raise AssertionError("in_index disagrees with the quarters enumerated from the records")
    return table, report


# ---------------------------------------------------------------------------
# the tables of the build - written here, so the S&P 500 run depends on no
# index-panel code
# ---------------------------------------------------------------------------
def by_quarter(table: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    """Per quarter: the firms' rows, how many are in the index, and how many of
    those carry a price, a return and a ROIC - the stage-2 starting count."""
    inside = table["in_index"].to_numpy() == 1
    d = pd.DataFrame({
        "period_key": report["period_key"],
        "rows": 1,
        "in_index": inside,
        "in_index_with_price_row": inside & table["Date"].notna().to_numpy(),
        "in_index_with_close": inside & table["Close"].notna().to_numpy(),
        "in_index_with_return": inside & table["Quaterly_return"].notna().to_numpy(),
        "in_index_with_roic": inside & table[ROIC_COLUMN].notna().to_numpy(),
    })
    out = d.groupby("period_key", sort=True).sum().reset_index()
    missing = report["investable_without_row"].groupby("period_key").size()
    out["investable_without_report_row"] = out["period_key"].map(missing).fillna(0).astype(int)
    return out


def timing_gap_census(table: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    """Months from the fiscal quarter end to the investment date, in-index rows only.

    2 is the calendar case (December -> end of February). 1 and 0 are fiscal
    quarters ending one month, or the same month, before the investment date -
    the report is probably not out yet on that day. Counted, never changed.
    """
    inside = table["in_index"].to_numpy() == 1
    d = pd.DataFrame({"period_year": pd.Series(report["period_key"]).str[:4].to_numpy()[inside],
                      "months": table["months_fiscal_end_to_formation"].to_numpy()[inside],
                      "symbol": table["symbol"].to_numpy()[inside]})
    by_year = (d.groupby(["period_year", "months"], dropna=False).size()
               .rename("rows").reset_index())
    total = (d.groupby("months", dropna=False).size().rename("rows").reset_index()
             .assign(period_year="all"))
    out = pd.concat([total, by_year], ignore_index=True)
    out["share_of_year"] = out["rows"] / out.groupby("period_year")["rows"].transform("sum")
    last = d[d["period_year"] == d["period_year"].max()]
    examples = (last.groupby("months")["symbol"]
                .agg(lambda s: ", ".join(sorted(map(str, s.unique()))[:12])))
    out["examples_latest_year"] = np.where(out["period_year"] == "all",
                                           out["months"].map(examples), "")
    return out[["period_year", "months", "rows", "share_of_year", "examples_latest_year"]]


def not_included(report: dict[str, Any]) -> pd.DataFrame:
    """What the panel cannot show for the assigned firms, one reason per line.

    INVESTABLE_QUARTER_WITHOUT_REPORT_ROW - a record says in the index, but the
    firm has no report row for that quarter. FIRM_WITHOUT_ANY_PRICE - the firm's
    rows are all in, with empty price columns. Records that went to no firm are
    in ``records_not_matched``.
    """
    cols = ["reason", "gvkey", "company", "period_key", "symbol", "rows_in_panel", "rows_in_index"]
    rows: list[dict[str, Any]] = []
    for r in report["investable_without_row"].itertuples(index=False):
        rows.append({"reason": "INVESTABLE_QUARTER_WITHOUT_REPORT_ROW", "gvkey": r.gvkey,
                     "company": r.company, "period_key": r.period_key})
    for r in report["firms_without_prices"].itertuples(index=False):
        rows.append({"reason": "FIRM_WITHOUT_ANY_PRICE", "gvkey": r.gvkey, "company": r.company,
                     "symbol": r.symbol, "rows_in_panel": int(r.rows),
                     "rows_in_index": int(r.rows_in_index)})
    for g in report["firms_without_rows"]:
        rows.append({"reason": "FIRM_WITHOUT_ANY_REPORT_ROW", "gvkey": g,
                     "rows_in_panel": 0, "rows_in_index": 0})
    out = pd.DataFrame(rows, columns=cols)
    for c in ("rows_in_panel", "rows_in_index"):
        out[c] = out[c].astype("Int64")
    return out


def records_not_matched(report: dict[str, Any]) -> pd.DataFrame:
    """Every record that went to no firm, with its one reason and its candidates."""
    s = report["status"]
    out = s[s["status"] != ASSIGNED][["source_row", "ticker", "start_quarter", "end_quarter",
                                      "status", "n_candidates", "n_candidates_exchange_ok",
                                      "candidates", "ticker_holders_in_panel"]]
    order = {r: i for i, r in enumerate(RECORD_STATUSES)}
    out = out.assign(_o=out["status"].map(order)).sort_values(["_o", "ticker", "start_quarter"],
                                                             kind="mergesort")
    return out.drop(columns="_o").rename(columns={"status": "reason"}).reset_index(drop=True)


def ticker_matching(report: dict[str, Any]) -> pd.DataFrame:
    """Every record and every candidate firm: the full audit of the assignment.

    A record without any candidate still has one line, with empty firm columns.
    """
    s = report["status"][["source_row", "ticker", "start_quarter", "end_quarter", "status",
                          "decided_by", "gvkey"]].rename(columns={"gvkey": "chosen_gvkey"})
    c = report["candidates"].drop(columns=["ticker", "start_quarter", "end_quarter"]) \
        .rename(columns={"gvkey": "candidate_gvkey", "symbol": "candidate_symbol",
                         "company": "candidate_company", "exchg": "candidate_exchg"})
    out = s.merge(c, on="source_row", how="left")
    return out.sort_values(["source_row", "quarters_in_range"], ascending=[True, False],
                           kind="mergesort").reset_index(drop=True)

"""The price panel: one adjusted close per firm and quarter, the return over the
next quarter and the momentum over the last three, from Compustat Security
Monthly (``data/prices.gz``).

Rules enforced here
-------------------
* The quarter of a price month comes from ``keys.add_period_columns`` - the
  base panel's own rule (Feb -> Q4 of the previous year, May -> Q1, Aug -> Q2,
  Nov -> Q3) - and ``KEY`` from ``keys.build_key``, so a price KEY and a
  fundamentals KEY are built by the same code.
* The security is the one whose ``(gvkey, tic)`` equals the base panel's. The
  file carries no ``iid``; a firm whose ticker is not among its securities gets
  no rows and is listed, never matched by guesswork.
* ``Close = prccm / ajexm``. A zero or missing price, or a missing factor, is
  "no price": Close is NaN. Nothing is filled.
* Returns and momentum are looked up by calendar quarter, ``(gvkey,
  quarter_index +- k)``, never by row position: a missing quarter gives NaN,
  never a two-quarter change labelled as one. Only the two endpoints count.
* ``Quaterly_return`` reads the NEXT quarter. It is the dependent variable, and
  ``forward_quarterly_return`` is the one sanctioned future read in ``src``.
* ``ipodate`` and ``dldte`` pass through untouched; nothing here filters on them.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from . import data_io, keys, schema

LOGGER = logging.getLogger("roic.prices")

REQUIRED_FIELDS = ("gvkey", "symbol", "month_end", "price_close", "adjustment_factor")
STRING_FIELDS = ("gvkey", "symbol", "company")
DATE_FIELDS = ("month_end", "ipo_date", "deletion_date")

# why a Close is missing; 0 means it is not
CLOSE_OK, ZERO_PRICE, MISSING_PRICE, MISSING_FACTOR, NEGATIVE_PRICE = 0, 1, 2, 3, 4
CLOSE_REASONS = {CLOSE_OK: "OK", ZERO_PRICE: "ZERO_PRICE", MISSING_PRICE: "MISSING_PRICE",
                 MISSING_FACTOR: "MISSING_FACTOR", NEGATIVE_PRICE: "NEGATIVE_PRICE"}

# the worked example the researcher gave on 2026-10-16: Yahoo adjusted closes,
# which include dividends, on the last trading day of the month. It pins the
# formulas in the tests and sits beside the real numbers in the report; it is
# never an input.
RESEARCHER_EXAMPLE_AAPL: tuple[tuple[str, str, float, float, float], ...] = (
    ("2013Q4", "2014-02-28", 16.37577248, 0.209591479, np.nan),
    ("2014Q1", "2014-05-30", 19.80799484, 0.139129314, np.nan),
    ("2014Q2", "2014-08-29", 22.56386757, 0.165324158, np.nan),
    ("2014Q3", "2014-11-28", 26.29421997, 0.084393922, 0.605678145),
    ("2014Q4", "2015-02-27", 28.51329231, 0.01840409, 0.439484034),
    ("2015Q1", "2015-05-29", 29.03805351, -0.130561971, 0.286927138),
    ("2015Q2", "2015-08-31", 25.24678802, 0.053621976, -0.039835064),
    ("2015Q3", "2015-11-30", 26.60057068, -0.178236257, -0.067081753),
    ("2015Q4", "2016-02-29", 21.85938454, 0.039073564, -0.247215915),
    ("2016Q1", "2016-05-31", 22.71350861, 0.06824289, -0.100340662),
    ("2016Q2", "2016-08-31", 24.26354408, 0.047006918, -0.087856258),
    ("2016Q3", "2016-11-30", 25.40409851, 0.244878394, 0.162159825),
    ("2016Q4", "2017-02-28", 31.62501335, 0.119720589, 0.392343821),
    ("2017Q1", "2017-05-31", 35.41117859, 0.077795366, 0.459439663),
)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def read_prices_file(cfg: dict[str, Any]) -> pd.DataFrame:
    """Read the gzip CSV with explicit types, so gvkey keeps its leading zeros.

    Arrow streams the decompression itself (the ``.gz`` suffix is enough) and
    nothing is written next to the source. Empty strings are read as missing:
    406 rows carry no ticker, and an empty ticker must not match anything.
    """
    path = cfg["_paths"]["prices_input"]
    types: dict[str, pa.DataType] = {}
    for logical, source in cfg["prices"]["field_map"].items():
        if logical in STRING_FIELDS:
            types[source] = pa.string()
        elif logical in DATE_FIELDS:
            types[source] = pa.date32()
        else:
            types[source] = pa.float64()
    table = pacsv.read_csv(str(path), convert_options=pacsv.ConvertOptions(
        column_types=types, strings_can_be_null=True))
    df = table.to_pandas(types_mapper={pa.string(): pd.StringDtype("pyarrow")}.get,
                         date_as_object=False)
    LOGGER.info("Prices loaded: %d rows x %d columns from %s", len(df), df.shape[1], path.name)
    return df


def base_tickers(cfg: dict[str, Any]) -> pd.DataFrame:
    """``(gvkey, symbol, company)`` of every firm in the base panel's source.

    The base KEY uses ``tic`` exactly as it stands in ``data.parquet``, so that
    is where the symbol is read. One ticker per gvkey is required: a second
    one would make the KEY ambiguous, so it raises instead of choosing.
    """
    src = data_io.mapped_source_columns(cfg)
    cols = {name: src[name][0] for name in ("gvkey", "symbol", "company")}
    table = pq.read_table(cfg["_paths"]["input_parquet"], columns=list(cols.values()))
    df = table.to_pandas().rename(columns={v: k for k, v in cols.items()})
    for col in ("gvkey", "symbol", "company"):
        df[col] = df[col].astype("string")
    sym = df.dropna(subset=["symbol"]).drop_duplicates(["gvkey", "symbol"])
    multi = sym.loc[sym["gvkey"].duplicated(), "gvkey"]
    if len(multi):
        raise ValueError(f"{multi.nunique()} gvkeys carry more than one ticker in the "
                         f"base source, e.g. {multi.unique()[:5].tolist()}")
    out = (df[["gvkey"]].drop_duplicates()
           .merge(sym[["gvkey", "symbol"]], on="gvkey", how="left")
           .merge(df.dropna(subset=["company"]).drop_duplicates("gvkey")[["gvkey", "company"]],
                  on="gvkey", how="left"))
    return out.sort_values("gvkey").reset_index(drop=True)


def base_company_status(cfg: dict[str, Any]) -> pd.Series | None:
    """``costat`` per gvkey (A active / I inactive), for the listing-dates census.

    Read by name because no calculation uses it; ``None`` when the column is
    absent, and the census then simply omits the active/inactive rows.
    """
    path = cfg["_paths"]["input_parquet"]
    if "costat" not in pq.read_schema(path).names:
        return None
    df = pq.read_table(path, columns=["gvkey", "costat"]).to_pandas()
    df = df.dropna(subset=["costat"]).drop_duplicates("gvkey", keep="last")
    return df.set_index(df["gvkey"].astype("string"))["costat"].astype("string")


def to_logical_fields(raw: pd.DataFrame, cfg: dict[str, Any]
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rename the source columns to logical names and report every column.

    A required field that is absent raises - there is no price panel without
    it. An optional one (``trt1m``, the dates) becomes empty and is reported,
    exactly like a missing field in the base panel.
    """
    n = len(raw)
    out: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for logical, source in cfg["prices"]["field_map"].items():
        present = source in raw.columns
        if not present and logical in REQUIRED_FIELDS:
            raise KeyError(f"prices file lacks the required column {source!r} ({logical})")
        rows.append({"logical_field": logical, "source_column": source,
                     "status": "MAPPED" if present else "MISSING_INPUT",
                     "n_non_null": int(raw[source].notna().sum()) if present else 0})
        if present:
            used.add(source)
        if logical in STRING_FIELDS:
            s = raw[source] if present else pd.Series(pd.NA, index=raw.index)
            out[logical] = s if isinstance(s.dtype, pd.StringDtype) else s.astype("string")
        elif logical in DATE_FIELDS:
            out[logical] = (pd.to_datetime(raw[source]).astype("datetime64[ns]") if present
                            else pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]"))
        else:
            out[logical] = (pd.to_numeric(raw[source]).astype("float64") if present
                            else pd.Series(np.nan, index=raw.index, dtype="float64"))
    for col in raw.columns:
        if col not in used:
            rows.append({"logical_field": "", "source_column": col,
                         "status": "UNMAPPED_SOURCE_COLUMN",
                         "n_non_null": int(raw[col].notna().sum())})
    df = pd.DataFrame(out, index=raw.index)
    if len(df) != n:
        raise ValueError("field mapping changed the row count")
    return df, pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# choosing the security
# ---------------------------------------------------------------------------
def select_securities(monthly: pd.DataFrame, tickers: pd.DataFrame
                      ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Keep the security whose ``(gvkey, tic)`` equals the base panel's.

    Every base firm lands in exactly one category - MATCHED, NO_TIC_MATCH (the
    gvkey is in the file only under other tickers), NO_PRICE_ROWS (not in the
    file at all) or NO_SYMBOL_IN_BASE (no ticker, so no KEY is possible) - and
    the tickers the file does hold are listed, so the gap can be reviewed.
    """
    t = tickers[["gvkey", "symbol", "company"]].copy()
    t["gvkey"] = t["gvkey"].astype(monthly["gvkey"].dtype)
    t["symbol"] = t["symbol"].astype(monthly["symbol"].dtype)
    pairs = monthly[["gvkey", "symbol"]].drop_duplicates()
    matched_pairs = (t.dropna(subset=["symbol"])[["gvkey", "symbol"]]
                     .merge(pairs.dropna(subset=["symbol"]), on=["gvkey", "symbol"], how="inner"))

    in_file = set(pairs["gvkey"].tolist())
    matched_ids = set(matched_pairs["gvkey"].tolist())
    gv = t["gvkey"].tolist()
    category = np.where(t["symbol"].isna().to_numpy(), "NO_SYMBOL_IN_BASE",
                        np.where([g in matched_ids for g in gv], "MATCHED",
                                 np.where([g in in_file for g in gv], "NO_TIC_MATCH",
                                          "NO_PRICE_ROWS")))
    # the tickers the file holds, for the firms that did not match
    unmatched = set(t.loc[category != "MATCHED", "gvkey"].tolist()) & in_file
    held_pairs = pairs[pairs["gvkey"].isin(unmatched)].dropna(subset=["symbol"])
    held = (held_pairs.assign(symbol=held_pairs["symbol"].astype(str))
            .groupby("gvkey")["symbol"].agg(lambda s: "|".join(sorted(s))))
    selection = t.assign(category=category,
                         tickers_in_file=t["gvkey"].map(held).astype("string"))
    counts = {c: int((category == c).sum())
              for c in ("MATCHED", "NO_TIC_MATCH", "NO_PRICE_ROWS", "NO_SYMBOL_IN_BASE")}
    counts["FILE_ONLY_NOT_IN_BASE"] = len(in_file - set(gv))
    matched = monthly.merge(matched_pairs, on=["gvkey", "symbol"], how="inner")
    LOGGER.info("Securities: %s", counts)
    return matched, selection, counts


def _assert_unique(df: pd.DataFrame, cols: list[str], what: str) -> None:
    """A duplicate would pair a period with itself; raise rather than choose."""
    dup = df.duplicated(cols, keep=False)
    if dup.any():
        sample = df.loc[dup, cols].head(5).to_dict("records")
        raise ValueError(f"{int(dup.sum())} rows share a (firm, {what}); e.g. {sample}")


# ---------------------------------------------------------------------------
# calendar lookups
# ---------------------------------------------------------------------------
_STRIDE = np.int64(1) << np.int64(32)


def _value_at_offset(firm: np.ndarray, index: np.ndarray, values: np.ndarray,
                     offset: int) -> np.ndarray:
    """``values`` of the same firm at ``index + offset``; NaN where no row sits there.

    Rows must be sorted by (firm, index) with each index unique per firm. The
    lookup is by calendar position, not by row position, which is what stops a
    missing quarter from silently pairing two non-adjacent quarters.
    """
    v = np.asarray(values, dtype="float64")
    out = np.full(len(v), np.nan, dtype="float64")
    if len(v) == 0:
        return out
    key = np.asarray(firm, dtype=np.int64) * _STRIDE + np.asarray(index, dtype=np.int64)
    if (np.diff(key) <= 0).any():
        raise ValueError("lookup needs rows sorted by (firm, index), one row per index")
    target = key + np.int64(offset)
    pos = np.minimum(np.searchsorted(key, target), len(key) - 1)
    hit = key[pos] == target
    out[hit] = v[pos[hit]]
    return out


def value_periods_back(firm: np.ndarray, index: np.ndarray, values: np.ndarray,
                       k: int) -> np.ndarray:
    """``values`` of the same firm ``k`` periods earlier (``k >= 1``)."""
    if int(k) < 1:
        raise ValueError("value_periods_back only looks backwards (k >= 1)")
    return _value_at_offset(firm, index, values, -int(k))


def _first_index_of_firm(firm: np.ndarray, index: np.ndarray) -> np.ndarray:
    """Per row, the firm's first index (rows sorted by firm)."""
    n = len(firm)
    if n == 0:
        return np.asarray(index, dtype=np.int64)
    starts = np.r_[0, np.flatnonzero(np.diff(firm)) + 1]
    return np.repeat(np.asarray(index, dtype=np.int64)[starts], np.diff(np.r_[starts, n]))


# ---------------------------------------------------------------------------
# the three numbers
# ---------------------------------------------------------------------------
def adjusted_close(price: np.ndarray, factor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``prccm / ajexm``, NaN without a price, plus the reason code per row.

    A price of exactly zero is how the file says "no price" (36,498 monthly
    rows, nearly all OTC shells), so it is treated like a missing price rather
    than as a stock worth nothing.
    """
    p = np.asarray(price, dtype="float64")
    f = np.asarray(factor, dtype="float64")
    code = np.full(len(p), CLOSE_OK, dtype=np.int8)
    code[np.isnan(p)] = MISSING_PRICE
    code[p == 0] = ZERO_PRICE
    code[p < 0] = NEGATIVE_PRICE
    code[(code == CLOSE_OK) & ~(f > 0)] = MISSING_FACTOR
    close = data_io.safe_div(p, f)
    close[code != CLOSE_OK] = np.nan
    return close, code


def momentum(firm: np.ndarray, qidx: np.ndarray, close: np.ndarray, code: np.ndarray,
             quarters: int) -> tuple[np.ndarray, np.ndarray]:
    """``MomentomQ3 = Close(q) / Close(q-3) - 1``, and why it is NaN where it is.

    Only the two endpoints are read (researcher 2026-10-17); the quarters in
    between neither enter nor block the value.
    """
    back = value_periods_back(firm, qidx, close, quarters)
    back_code = value_periods_back(firm, qidx, code.astype("float64"), quarters)
    value = data_io.safe_div(close, back) - 1.0
    first = _first_index_of_firm(firm, qidx)
    reason = np.full(len(close), "OK", dtype=object)
    no_row = np.isnan(back_code)
    reason[no_row & (qidx - quarters < first)] = "BEFORE_FIRST_PRICE"
    reason[no_row & (qidx - quarters >= first)] = "BACK_QUARTER_MISSING"
    for c, name in CLOSE_REASONS.items():
        if c != CLOSE_OK:
            reason[back_code == c] = f"BACK_{name}"
    for c, name in CLOSE_REASONS.items():
        if c != CLOSE_OK:
            reason[code == c] = f"CLOSE_{name}"
    return value, reason


def forward_quarterly_return(firm: np.ndarray, qidx: np.ndarray, close: np.ndarray,
                             code: np.ndarray, date: pd.Series, deletion_date: pd.Series,
                             last_month_in_file: pd.Timestamp
                             ) -> tuple[np.ndarray, np.ndarray]:
    """``Quaterly_return = Close(q+1) / Close(q) - 1``, and why it is NaN where it is.

    THE ONE SANCTIONED FUTURE READ IN ``src``. The value is the dependent
    variable - the return over the quarter after the price date - and must
    never feed a signal, a filter or a sort. A test confines every forward
    lookup in the package to this function.

    A missing next quarter is labelled: NOT_YET_AVAILABLE when its date lies
    beyond the file, DELETED_BY_NEXT_DATE when ``dldte`` falls on or before it,
    NEXT_QUARTER_MISSING otherwise. The labels only describe the gap.
    """
    nxt = _value_at_offset(firm, qidx, close, +1)
    nxt_code = _value_at_offset(firm, qidx, code.astype("float64"), +1)
    value = data_io.safe_div(nxt, close) - 1.0

    next_date = (pd.to_datetime(date) + pd.offsets.MonthEnd(3)).to_numpy("datetime64[ns]")
    deleted = pd.to_datetime(deletion_date).to_numpy("datetime64[ns]")
    reason = np.full(len(close), "OK", dtype=object)
    no_row = np.isnan(nxt_code)
    beyond = next_date > pd.Timestamp(last_month_in_file).to_datetime64()
    gone = ~np.isnat(deleted) & (deleted <= next_date)
    reason[no_row] = "NEXT_QUARTER_MISSING"
    reason[no_row & gone] = "DELETED_BY_NEXT_DATE"
    reason[no_row & beyond] = "NOT_YET_AVAILABLE"
    for c, name in CLOSE_REASONS.items():
        if c != CLOSE_OK:
            reason[nxt_code == c] = f"NEXT_{name}"
    for c, name in CLOSE_REASONS.items():
        if c != CLOSE_OK:
            reason[code == c] = f"CLOSE_{name}"
    return value, reason


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------
def build_prices(cfg: dict[str, Any], raw: pd.DataFrame | None = None,
                 tickers: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the price panel. ``raw`` and ``tickers`` are injectable for tests."""
    report: dict[str, Any] = {}
    if raw is None:
        raw = read_prices_file(cfg)
    if tickers is None:
        tickers = base_tickers(cfg)
    monthly, input_fields = to_logical_fields(raw, cfg)
    del raw
    month_end = monthly["month_end"]
    report.update({
        "rows_in_source": int(len(monthly)),
        "input_fields": input_fields,
        "first_month_in_file": month_end.min(),
        "last_month_in_file": month_end.max(),
        "dates_not_month_end": int((month_end != month_end + pd.offsets.MonthEnd(0)).sum()),
    })

    matched, selection, counts = select_securities(monthly, tickers)
    del monthly
    matched = matched.sort_values(["gvkey", "month_end"], kind="mergesort").reset_index(drop=True)
    _assert_unique(matched, ["gvkey", "month_end"], "month")
    report.update({"selection": selection, "selection_counts": counts,
                   "rows_matched_monthly": int(len(matched))})

    in_quarter_month = matched["month_end"].dt.month.isin(cfg["prices"]["months"]).to_numpy()
    q = matched.loc[in_quarter_month].copy()
    q["_monthly_row"] = np.flatnonzero(in_quarter_month)
    q = keys.add_period_columns(q, cfg, date_col="month_end")
    q = q.sort_values(["gvkey", "quarter_index"], kind="mergesort").reset_index(drop=True)
    _assert_unique(q, ["gvkey", "quarter_index"], "quarter")
    counts["MATCHED_WITHOUT_QUARTER_MONTH"] = counts["MATCHED"] - int(q["gvkey"].nunique())

    firm = pd.factorize(q["gvkey"], sort=False)[0].astype(np.int64)
    qidx = q["quarter_index"].to_numpy(dtype=np.int64)
    close, code = adjusted_close(q["price_close"].to_numpy(), q["adjustment_factor"].to_numpy())
    mom, mom_reason = momentum(firm, qidx, close, code, int(cfg["prices"]["momentum_quarters"]))
    ret, ret_reason = forward_quarterly_return(firm, qidx, close, code, q["month_end"],
                                               q["deletion_date"], report["last_month_in_file"])

    data = {
        "KEY": keys.build_key(q["period_key"], q["symbol"], cfg),
        "Date": q["month_end"],
        "Ticker": q["symbol"],
        "Close": close,
        "Quaterly_return": ret,
        "MomentomQ3": mom,
        "ipodate": q["ipo_date"],
        "dldte": q["deletion_date"],
    }
    prices = schema.finalize_prices(data, n_rows=len(q))
    if prices["KEY"].isna().any() or prices["KEY"].duplicated().any():
        raise ValueError("price KEY must be present and unique on every row")

    report.update({
        "rows_in_panel": int(len(prices)),
        "monthly": matched,
        "quarter_keys": q[["gvkey", "period_key", "period_year", "quarter_index",
                           "_monthly_row"]].assign(firm=firm),
        "close_reason": np.array([CLOSE_REASONS[int(c)] for c in range(5)],
                                 dtype=object)[code],
        "momentum_reason": mom_reason,
        "return_reason": ret_reason,
    })
    LOGGER.info("Price panel built: %d rows, %d firms", len(prices), int(firm.max() + 1)
                if len(firm) else 0)
    return prices, report

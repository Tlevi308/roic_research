"""The index panel: every base-panel row of the firms that were ever in an
index, the price columns joined on ``(gvkey, KEY)``, and ``in_index`` - 1 when
the firm was investable in that quarter, 0 when it was not.

Rules enforced here
-------------------
* The firms are the gvkeys of the index file (researcher 2026-11-07). Nothing
  is matched by ticker, and nothing here knows a particular index: the file,
  its id and its column names come from ``indices.<name>`` in config.yaml.
* Prices join on ``(gvkey, KEY)``, left from the fundamentals: a quarter
  without a price keeps its fundamentals and gets empty price columns. Values
  are copied, never recomputed - every lag, average and momentum was computed
  on the full history first, so a firm that enters the index keeps the
  quarters before it.
* A firm can leave the index and rejoin it. Records of one gvkey whose next
  ``from`` falls at most ``contiguous_gap_days`` after the previous ``thru``
  are one stay - two share classes, a ticker change, a next-day renewal. A
  longer gap is a real exit, and the stays stay separate.
* ``in_index = 1`` when ONE stay of the same gvkey holds both dates,
  ``from <= quarter_end`` and ``formation_date <= thru``, an empty ``thru``
  being open. Both dates come from the KEY: ``2025Q1_X`` -> 2025-03-31, and
  ``timing.quarter_shift_months`` later 2025-05-31 - the month-end the price
  panel uses as ``Date``.
* ``thru`` - the day the firm will leave - never enters the panel.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from . import keys, schema

LOGGER = logging.getLogger("roic.index_panel")

REQUIRED_COLUMNS = ("gvkey", "from", "thru", "index_id")
# an empty thru, inside comparisons only: later than any date in the data, and
# still far enough from pandas' upper bound to add the contiguity gap to it
OPEN = pd.Timestamp("2262-01-01")


# ---------------------------------------------------------------------------
# the index file
# ---------------------------------------------------------------------------
def index_settings(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    indices = cfg.get("indices") or {}
    if name not in indices:
        raise KeyError(f"index {name!r} is not under `indices` in config.yaml "
                       f"(defined: {sorted(indices)})")
    return indices[name]


def constituents_path(cfg: dict[str, Any], name: str) -> Path:
    """The index file, relative to config.yaml like every other path."""
    return (Path(cfg["_meta"]["base_dir"]) / index_settings(cfg, name)["file"]).resolve()


def read_constituents(cfg: dict[str, Any], name: str) -> pd.DataFrame:
    """One row per record of the index file. Every value is read as text first,
    so gvkey keeps its leading zeros, and only an empty cell is missing."""
    raw = pd.read_csv(constituents_path(cfg, name), dtype=str,
                      keep_default_na=False, na_values=[""])
    return standardize_constituents(raw, index_settings(cfg, name), name)


def standardize_constituents(raw: pd.DataFrame, spec: dict[str, Any],
                             name: str) -> pd.DataFrame:
    """``gvkey, from, thru, company, source_row`` - or an error, never a guess.

    A file in another format changes ``columns`` and ``date_format`` in the
    config; anything that does not fit them raises here.
    """
    cols = spec["columns"]
    missing = [f"{logical} ({cols.get(logical)!r})" for logical in REQUIRED_COLUMNS
               if cols.get(logical) not in raw.columns]
    if missing:
        raise KeyError(f"index file for {name!r} lacks the columns {missing}")

    ids = raw[cols["index_id"]]
    if ids.isna().any() or set(ids.unique()) != {str(spec["gvkeyx"])}:
        raise ValueError(f"index file for {name!r} must hold only gvkeyx "
                         f"{spec['gvkeyx']!r}; found {sorted(ids.dropna().unique())[:5]}")

    gvkey = raw[cols["gvkey"]].astype("string")
    bad = ~gvkey.fillna("").str.fullmatch(r"\d{6}")
    if bad.any():
        raise ValueError(f"{int(bad.sum())} records carry a gvkey that is not six digits, "
                         f"e.g. {gvkey[bad].head(5).tolist()}")

    fmt = spec.get("date_format")
    start = pd.to_datetime(raw[cols["from"]], format=fmt).astype("datetime64[ns]")
    end = pd.to_datetime(raw[cols["thru"]], format=fmt).astype("datetime64[ns]")
    if start.isna().any():
        raise ValueError(f"{int(start.isna().sum())} records have no `from` date")
    if (end < start).any():
        raise ValueError(f"{int((end < start).sum())} records end before they start")

    company = (raw[cols["company"]].astype("string") if cols.get("company") in raw.columns
               else pd.Series(pd.NA, index=raw.index, dtype="string"))
    return pd.DataFrame({"gvkey": gvkey, "from": start, "thru": end,
                         "company": company, "source_row": np.arange(len(raw))})


# ---------------------------------------------------------------------------
# stays
# ---------------------------------------------------------------------------
def merge_contiguous_spells(spells: pd.DataFrame, gap_days: int
                            ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per continuous stay in the index, and an audit of every merge.

    Records are walked in ``from`` order within the firm. A record joins the
    current stay when it starts no later than ``gap_days`` after the furthest
    ``thru`` seen so far - so a record nested inside another, or one that
    overlaps it, never splits a stay. An empty ``thru`` keeps the stay open.
    """
    s = spells.sort_values(["gvkey", "from", "source_row"], kind="mergesort").reset_index(drop=True)
    end = s["thru"].fillna(OPEN)
    reach = end.groupby(s["gvkey"]).cummax()
    prev_reach = reach.groupby(s["gvkey"]).shift()
    gap = pd.Timedelta(days=int(gap_days))
    new_stay = (prev_reach.isna() | (s["from"] > prev_reach + gap)).to_numpy()
    s["stay"] = np.cumsum(new_stay) - 1
    s["_end"] = end

    stays = s.groupby("stay", sort=True).agg(
        gvkey=("gvkey", "first"), company=("company", "first"),
        start=("from", "min"), _end=("_end", "max"), n_records=("source_row", "size"))
    stays = pd.DataFrame({
        "gvkey": stays["gvkey"].astype("string").to_numpy(),
        "company": stays["company"].astype("string").to_numpy(),
        "from": stays["start"].to_numpy("datetime64[ns]"),
        "thru": stays["_end"].where(stays["_end"] < OPEN).to_numpy("datetime64[ns]"),
        "n_records": stays["n_records"].to_numpy(),
    })

    size = s.groupby("stay")["stay"].transform("size")
    merged = s[size > 1].copy()
    pr = prev_reach[size > 1]
    relation = np.where(new_stay[size.to_numpy() > 1], "FIRST_RECORD_OF_STAY",
                        np.where(merged["from"] <= pr, "OVERLAPS_PREVIOUS",
                                 "STARTS_RIGHT_AFTER_PREVIOUS"))
    # the gap is reported for a next-day record only; for an overlap it says nothing
    days_after = (merged["from"] - pr).dt.days.where(relation == "STARTS_RIGHT_AFTER_PREVIOUS")
    audit = pd.DataFrame({
        "gvkey": merged["gvkey"].to_numpy(),
        "company": merged["company"].to_numpy(),
        "source_row": merged["source_row"].to_numpy(),
        "from": merged["from"].to_numpy(),
        "thru": merged["thru"].to_numpy(),
        "relation": relation,
        "days_after_previous_thru": days_after.astype("Int64").to_numpy(),
        "stay_from": stays["from"].to_numpy()[merged["stay"].to_numpy()],
        "stay_thru": stays["thru"].to_numpy()[merged["stay"].to_numpy()],
        "records_in_stay": stays["n_records"].to_numpy()[merged["stay"].to_numpy()],
    })
    LOGGER.info("Index records -> stays: %d -> %d (%d records merged into a stay)",
                len(spells), len(stays), len(spells) - len(stays))
    return stays, audit


# ---------------------------------------------------------------------------
# the two dates of a quarter
# ---------------------------------------------------------------------------
def period_from_key(key: pd.Series, cfg: dict[str, Any]) -> pd.Series:
    """``2025Q1`` from ``2025Q1_AIR``; a KEY that is not ``YYYYQn<sep>...`` raises."""
    sep = re.escape(cfg["timing"]["key_separator"])
    k = key.astype("string")
    period = k.str.extract(rf"^(\d{{4}}Q[1-4]){sep}.+$", expand=False)
    bad = k.notna() & period.isna()
    if bad.any():
        raise ValueError(f"{int(bad.sum())} KEYs are not 'YYYYQn{cfg['timing']['key_separator']}"
                         f"TICKER', e.g. {k[bad].head(5).tolist()}")
    return period.astype("string")


def quarter_dates(period: pd.Series, cfg: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    """``quarter_end`` and ``formation_date`` of a quarter such as ``2025Q1``.

    2025Q1 -> 2025-03-31 and 2025-05-31 · 2025Q4 -> 2025-12-31 and 2026-02-28.
    The offset is ``timing.quarter_shift_months``, the rule that maps a price
    month back to its quarter, so ``formation_date`` is the price ``Date``.
    """
    shift = int(cfg["timing"]["quarter_shift_months"])
    uniq = pd.Series(period.dropna().unique())
    q_end = pd.PeriodIndex(uniq, freq="Q").to_timestamp(how="end").normalize()
    lookup_end = dict(zip(uniq, q_end))
    lookup_form = dict(zip(uniq, q_end + pd.offsets.MonthEnd(shift)))
    quarter_end = pd.to_datetime(period.map(lookup_end)).astype("datetime64[ns]")
    formation = pd.to_datetime(period.map(lookup_form)).astype("datetime64[ns]")
    return quarter_end, formation


# ---------------------------------------------------------------------------
# the flag
# ---------------------------------------------------------------------------
def in_index_flag(gvkey: pd.Series, quarter_end: pd.Series, formation_date: pd.Series,
                  stays: pd.DataFrame) -> np.ndarray:
    """1 where one stay of the same gvkey holds both dates, else 0 (``int8``).

    Each row is compared with its own firm's stays only, and a single matching
    stay is enough - the vectorised "stop at the first match". Both dates must
    sit in the SAME stay: ``from <= quarter_end`` and ``formation_date <= thru``.
    """
    n = len(gvkey)
    rows = pd.DataFrame({"_row": np.arange(n), "gvkey": pd.Series(gvkey).astype("string").to_numpy(),
                         "q": pd.to_datetime(pd.Series(quarter_end)).to_numpy("datetime64[ns]"),
                         "f": pd.to_datetime(pd.Series(formation_date)).to_numpy("datetime64[ns]")})
    st = pd.DataFrame({"gvkey": stays["gvkey"].astype("string").to_numpy(),
                       "from": stays["from"].to_numpy("datetime64[ns]"),
                       "thru": stays["thru"].fillna(OPEN).to_numpy("datetime64[ns]")})
    j = rows.merge(st, on="gvkey", how="inner")
    hit = (j["from"] <= j["q"]) & (j["f"] <= j["thru"])
    out = np.zeros(n, dtype=np.int8)
    out[j.loc[hit, "_row"].to_numpy()] = 1
    return out


def last_period_in_file(last_date: pd.Timestamp, cfg: dict[str, Any]) -> str:
    """The latest quarter whose investment date the index file already covers.

    With a file last updated on 2026-10-09 that is 2026Q2 (invested 2026-08-31);
    2026Q3 would be invested on 2026-11-30, after the file.
    """
    p = pd.Period(last_date, freq="Q")
    while quarter_dates(pd.Series([str(p)], dtype="string"), cfg)[1].iloc[0] > last_date:
        p -= 1
    return str(p)


def quarters_in_stays(stays: pd.DataFrame, last_period: str, cfg: dict[str, Any]) -> pd.DataFrame:
    """Every ``(gvkey, quarter)`` the rule marks as investable, up to ``last_period``.

    Built from the stays alone, independently of the panel, so it can name the
    investable quarters that have no report row - and cross-check the flag.
    """
    last = pd.Period(last_period, freq="Q")
    parts = []
    for gvkey, company, start, end in zip(stays["gvkey"], stays["company"],
                                          stays["from"], stays["thru"]):
        first = pd.Period(start, freq="Q")                      # the quarter holding `from`
        stop = last if pd.isna(end) else min(last, pd.Period(end, freq="Q"))
        if first > stop:
            continue
        span = pd.period_range(first, stop, freq="Q")
        parts.append(pd.DataFrame({"gvkey": gvkey, "company": company,
                                   "period_key": span.astype(str)}))
    cand = (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=["gvkey", "company", "period_key"]))
    q_end, form = quarter_dates(cand["period_key"].astype("string"), cfg)
    flag = in_index_flag(cand["gvkey"], q_end, form, stays)
    out = cand.assign(quarter_end=q_end, formation_date=form)[flag == 1]
    return out.drop_duplicates(["gvkey", "period_key"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# reading the two panels
# ---------------------------------------------------------------------------
def read_firms(path: Path, gvkeys: list[str]) -> pd.DataFrame:
    """Only the rows of these firms, filtered while the parquet is read."""
    table = ds.dataset(str(path), format="parquet").to_table(
        filter=ds.field("gvkey").isin(list(gvkeys)))
    return table.to_pandas()


def _assert_unique(df: pd.DataFrame, what: str) -> None:
    keyed = df[df["KEY"].notna()]
    dup = keyed.duplicated(list(schema.PRICE_JOIN_KEYS), keep=False)
    if dup.any():
        sample = keyed.loc[dup, list(schema.PRICE_JOIN_KEYS)].head(5).to_dict("records")
        raise ValueError(f"{int(dup.sum())} {what} rows share a (gvkey, KEY); e.g. {sample}")


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------
def build_index_panel(cfg: dict[str, Any], name: str, spells: pd.DataFrame | None = None,
                      panel: pd.DataFrame | None = None, prices: pd.DataFrame | None = None
                      ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build one index panel. ``spells``, ``panel`` and ``prices`` are injectable."""
    report: dict[str, Any] = {"index": name}
    if spells is None:
        spells = read_constituents(cfg, name)
    stays, audit = merge_contiguous_spells(
        spells, int(cfg["index_panel"]["contiguous_gap_days"]))
    gvkeys = sorted(spells["gvkey"].astype(str).unique())

    out_dir = cfg["_paths"]["output_dir"]
    if panel is None:
        panel = read_firms(out_dir / cfg["output"]["panel_file"], gvkeys)
    else:
        panel = panel[panel["gvkey"].astype(str).isin(gvkeys)]
    if prices is None:
        prices = read_firms(out_dir / cfg["prices"]["output_file"], gvkeys)
    else:
        prices = prices[prices["gvkey"].astype(str).isin(gvkeys)]
    panel = panel.sort_values(["gvkey", "fiscal_period_end_date"], kind="mergesort") \
                 .reset_index(drop=True)

    # --- join, (gvkey, KEY), left from the fundamentals
    if prices["KEY"].isna().any():
        raise ValueError("every price row must carry a KEY")
    _assert_unique(panel, "fundamentals")
    _assert_unique(prices, "price")
    price_cols = [c for c in schema.PRICE_COLUMNS if c not in schema.PRICE_JOIN_KEYS]
    keyed = panel["KEY"].notna()
    # the base panel's dtypes decide, so every base value is copied as it is
    prices = prices.astype({c: panel[c].dtype for c in schema.PRICE_JOIN_KEYS})
    joined = panel.merge(prices[list(schema.PRICE_JOIN_KEYS) + price_cols],
                         on=list(schema.PRICE_JOIN_KEYS), how="left", validate="many_to_one")
    # every price row carries a KEY, so a fundamentals row without one finds no price
    if len(joined) != len(panel):
        raise ValueError("the price join changed the row count")

    # --- the quarter of every row: from the KEY, checked against the fiscal date
    from_date = keys.add_period_columns(panel[["fiscal_period_end_date"]], cfg)["period_key"]
    period = period_from_key(panel["KEY"], cfg)
    disagree = period.notna() & (period != from_date.astype("string"))
    if disagree.any():
        raise ValueError(f"{int(disagree.sum())} KEYs name another quarter than their "
                         f"fiscal_period_end_date, e.g. {panel.loc[disagree, 'KEY'].head(5).tolist()}")
    period = period.fillna(from_date.astype("string"))
    quarter_end, formation = quarter_dates(period, cfg)

    flag = in_index_flag(panel["gvkey"], quarter_end, formation, stays)
    fiscal = pd.to_datetime(panel["fiscal_period_end_date"])
    months = ((formation.dt.year - fiscal.dt.year) * 12
              + (formation.dt.month - fiscal.dt.month))
    table = schema.finalize_index_panel(joined, {
        "quarter_end": quarter_end, "formation_date": formation,
        "in_index": flag, "months_fiscal_end_to_formation": months,
    })

    # --- what the rule marks investable but the panel cannot show. Enumerated
    # from the stays alone, through the last quarter the index file covers, so
    # it also cross-checks the flag row by row.
    last_date = max(spells["from"].max(), spells["thru"].max())
    last_period = last_period_in_file(last_date, cfg)
    investable = quarters_in_stays(stays, last_period, cfg)
    have = pd.MultiIndex.from_arrays([panel["gvkey"].astype(str), period.astype(str)])
    want = pd.MultiIndex.from_arrays([investable["gvkey"].astype(str),
                                      investable["period_key"].astype(str)])
    covered = (flag == 1) & (period <= last_period).to_numpy()
    flagged = pd.MultiIndex.from_arrays([panel["gvkey"].astype(str)[covered],
                                         period.astype(str)[covered]])
    no_row = investable[~want.isin(have)].reset_index(drop=True)
    cross_check = bool(set(want[want.isin(have)]) == set(flagged))

    has_price = table["Date"].notna()
    firm_price = has_price.groupby(table["gvkey"].astype(str)).any()
    firm_info = (table.groupby(table["gvkey"].astype(str))
                 .agg(symbol=("symbol", "first"), company=("company", "first"),
                      rows=("KEY", "size"), rows_in_index=("in_index", "sum")))
    report.update({
        "records_in_file": int(len(spells)),
        "firms_in_file": int(len(gvkeys)),
        "records_per_firm": spells.groupby("gvkey").size().value_counts().sort_index(),
        "stays": stays,
        "spell_audit": audit,
        "firms_without_rows": sorted(set(gvkeys) - set(panel["gvkey"].astype(str))),
        "firms_without_prices": firm_info[~firm_price].reset_index(),
        "rows_without_key": int((~keyed).sum()),
        "rows_in_panel": int(len(table)),
        "rows_with_price": int(has_price.sum()),
        "rows_in_index": int(flag.sum()),
        "period_key": period.to_numpy(),
        "investable_without_row": no_row,
        "flag_cross_check": cross_check,
        "first_date_in_file": spells["from"].min(),
        "last_date_in_file": last_date,
        "last_period_in_file": last_period,
        "rows_in_index_after_file": int(((flag == 1) & (period > last_period).to_numpy()).sum()),
    })
    LOGGER.info("Index panel %s: %d rows, %d firms, %d with price, %d in the index",
                name, len(table), table["gvkey"].nunique(), report["rows_with_price"],
                report["rows_in_index"])
    if not cross_check:
        raise AssertionError("in_index disagrees with the quarters enumerated from the stays")
    return table, report

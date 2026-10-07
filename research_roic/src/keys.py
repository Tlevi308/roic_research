"""Keys, duplicate merging, and the quarter grid every window depends on.

Rules enforced here
-------------------
* ``period_key`` is the calendar quarter of (fiscal period end - 2 months).
  The rule is used for timing and for ``KEY``, and for nothing else.
* ``KEY = period_key + "_" + symbol``. Duplicates are decided on
  ``(gvkey, period_key)`` because ``gvkey`` is always present and ``tic`` is not:
  383 rows (trusts, ETNs) carry no ticker, and concatenating a missing ticker
  would fuse unrelated firms into one key.
* One row per (firm, quarter) leaves the merge. Without that, ``shift(1)`` pairs
  a quarter with itself and every t-1 comparison is silently wrong.
* A row links to the previous quarter only when the quarter index steps by
  exactly 1 **and** the day gap is inside the configured range.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("roic.keys")


# ---------------------------------------------------------------------------
# period derivation
# ---------------------------------------------------------------------------
def add_period_columns(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Add ``period_year``, ``period_quarter``, ``period_key`` and ``quarter_index``.

    2026-03-31 -> 2026-01-31 -> 2026Q1 · 2025-09-30 -> 2025-07-31 -> 2025Q3
    2025-08-31 -> 2025-06-30 -> 2025Q2 · 2026-01-31 -> 2025-11-30 -> 2025Q4
    """
    out = df.copy()
    end = pd.to_datetime(out["fiscal_period_end_date"])
    shifted = end - pd.DateOffset(months=int(cfg["timing"]["quarter_shift_months"]))
    out["period_year"] = shifted.dt.year.astype("int32")
    out["period_quarter"] = shifted.dt.quarter.astype("int8")
    out["period_key"] = (out["period_year"].astype(str) + "Q"
                         + out["period_quarter"].astype(str))
    out["quarter_index"] = (out["period_year"].astype("int64") * 4
                            + (out["period_quarter"].astype("int64") - 1))
    return out


def build_key(period_key: pd.Series, symbol: pd.Series, cfg: dict[str, Any]) -> pd.Series:
    """``period_key + sep + symbol``, and NA where the symbol is missing.

    A row with no ticker gets no KEY rather than a key shared with every other
    tickerless firm in that quarter.
    """
    sep = cfg["timing"]["key_separator"]
    sym = symbol.astype("string")
    key = period_key.astype("string") + sep + sym
    return key.where(sym.notna() & (sym.str.len() > 0))


# ---------------------------------------------------------------------------
# duplicate merging
# ---------------------------------------------------------------------------
def merge_duplicates(df: pd.DataFrame, cfg: dict[str, Any],
                     value_columns: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Two-step merge, exactly as the researcher specified (2026-10-04).

    Step 1 - rows sharing ``(gvkey, fiscal_period_end_date)``: the first row in
    source order stays, and each of its missing values is filled from the
    following rows of the same group, column by column.

    Step 2 - rows sharing ``(gvkey, period_key)`` with *different* dates (a
    fiscal year-end change): the first row decides and its values decide.
    Nothing is merged across different dates, because those are different
    fiscal periods.

    Returns the merged frame, a full audit of every removed row, and counts.
    """
    d = df.copy()
    d["_src"] = np.arange(len(d), dtype="int64")
    audit: list[pd.DataFrame] = []
    n_in = len(d)

    if cfg["panel"]["dedup"]["collapse_same_datadate"]:
        dup = d.duplicated(["gvkey", "fiscal_period_end_date"], keep=False)
        if dup.any():
            grouped = d[dup].sort_values("_src")
            collapsed = grouped.groupby(["gvkey", "fiscal_period_end_date"],
                                        sort=False, as_index=False).first()
            removed = _audit_rows(grouped, collapsed, value_columns,
                                  group_keys=["gvkey", "fiscal_period_end_date"],
                                  reason="SAME_GVKEY_AND_DATE_VALUES_MERGED")
            audit.append(removed)
            d = pd.concat([d[~dup], collapsed], ignore_index=True)
    n_after_step1 = len(d)

    if cfg["panel"]["dedup"]["keep_first_per_period"]:
        d = d.sort_values("_src", kind="mergesort")
        dup = d.duplicated(["gvkey", "period_key"], keep=False)
        if dup.any():
            grouped = d[dup]
            kept = grouped.drop_duplicates(["gvkey", "period_key"], keep="first")
            removed = _audit_rows(grouped, kept, value_columns,
                                  group_keys=["gvkey", "period_key"],
                                  reason="SAME_PERIOD_DIFFERENT_DATE_FIRST_ROW_WINS")
            audit.append(removed)
            d = d.drop_duplicates(["gvkey", "period_key"], keep="first")
    n_out = len(d)

    d = d.sort_values(["gvkey", "quarter_index"], kind="mergesort").reset_index(drop=True)
    audit_df = (pd.concat(audit, ignore_index=True) if audit
                else pd.DataFrame(columns=["gvkey", "symbol", "reason"]))
    report = {
        "rows_in": n_in,
        "removed_step1_same_date": n_in - n_after_step1,
        "rows_after_step1": n_after_step1,
        "removed_step2_same_period": n_after_step1 - n_out,
        "rows_out": n_out,
    }
    LOGGER.info("Duplicates merged: %d -> %d -> %d (step1 -%d, step2 -%d)",
                n_in, n_after_step1, n_out,
                report["removed_step1_same_date"], report["removed_step2_same_period"])
    return d.drop(columns=["_src"]), audit_df, report


def _audit_rows(group: pd.DataFrame, kept: pd.DataFrame, value_columns: list[str],
                group_keys: list[str], reason: str) -> pd.DataFrame:
    """One audit row per removed source row, naming the row that superseded it.

    Every value carried by a removed row is written out, so the merge can be
    reviewed - and reversed - without re-reading the source.
    """
    survivors = set(kept["_src"].tolist())
    dropped = group[~group["_src"].isin(survivors)]
    if dropped.empty:
        return pd.DataFrame(columns=["gvkey", "symbol", "reason"])
    first_src = group.groupby(group_keys, sort=False)["_src"].transform("min")
    cols = ["gvkey", "symbol", "company", "fiscal_period_end_date", "period_key"]
    out = dropped[[c for c in cols if c in dropped.columns]].copy()
    out["reason"] = reason
    out["removed_source_row"] = dropped["_src"].to_numpy()
    out["superseded_by_source_row"] = first_src.loc[dropped.index].to_numpy()
    present = [c for c in value_columns if c in dropped.columns]
    out["n_values_on_removed_row"] = dropped[present].notna().sum(axis=1).to_numpy()
    for c in present:
        out[f"removed_{c}"] = dropped[c].to_numpy()
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# the quarter grid
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    """Per-row position in the firm's quarterly run, built once in numpy.

    ``prev_ok[i]`` is True when row ``i-1`` is the same firm's immediately
    preceding quarter. ``run_len[i]`` counts how many consecutive quarters end
    at ``i`` (1 when the previous link is broken), so a window of ``n`` rows is
    available exactly when ``run_len >= n`` - no repeated groupby-shift.
    """
    n: int
    firm_codes: np.ndarray
    quarter_index: np.ndarray
    prev_ok: np.ndarray
    run_len: np.ndarray

    def lag(self, values: np.ndarray) -> np.ndarray:
        """Previous-quarter value, NaN wherever the previous link is broken."""
        v = np.asarray(values, dtype="float64")
        out = np.full(self.n, np.nan, dtype="float64")
        out[1:] = v[:-1]
        out[~self.prev_ok] = np.nan
        return out

    def window_ok(self, n_rows: int) -> np.ndarray:
        """True when rows t-n_rows+1 .. t of the same firm form a consecutive run."""
        return self.run_len >= n_rows


def build_grid(df: pd.DataFrame, cfg: dict[str, Any]) -> Grid:
    """Build the grid from a frame already sorted by (gvkey, quarter_index)."""
    assert_sorted(df)
    n = len(df)
    firm_codes = pd.factorize(df["gvkey"], sort=False)[0].astype("int64")
    qidx = df["quarter_index"].to_numpy(dtype="int64")
    days = (pd.to_datetime(df["fiscal_period_end_date"]).diff().dt.days
            .to_numpy(dtype="float64"))

    cons = cfg["panel"]["consecutive"]
    prev_ok = np.zeros(n, dtype=bool)
    if n > 1:
        same_firm = firm_codes[1:] == firm_codes[:-1]
        step_ok = (qidx[1:] - qidx[:-1]) == int(cons["key_gap_quarters"])
        gap = days[1:]
        day_ok = (gap >= float(cons["min_days"])) & (gap <= float(cons["max_days"]))
        prev_ok[1:] = same_firm & step_ok & day_ok

    idx = np.arange(n)
    starts = np.where(~prev_ok, idx, 0)
    run_len = idx - np.maximum.accumulate(starts) + 1
    return Grid(n=n, firm_codes=firm_codes, quarter_index=qidx,
                prev_ok=prev_ok, run_len=run_len)


def assert_sorted(df: pd.DataFrame) -> None:
    """Panel must be sorted by (gvkey, quarter_index) with unique quarters per firm."""
    gv = df["gvkey"].astype(str).to_numpy()
    q = df["quarter_index"].to_numpy(dtype="int64")
    if len(df) < 2:
        return
    same = gv[1:] == gv[:-1]
    if (gv[1:] < gv[:-1]).any() or (same & (q[1:] <= q[:-1])).any():
        raise ValueError("Panel must be sorted by (gvkey, quarter_index) "
                         "with unique quarters per firm")

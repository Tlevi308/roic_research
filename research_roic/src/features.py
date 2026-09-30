"""Signal construction.

All features at row t use rows t-k (k >= 0) of the same firm only; returns are
never an input. The panel must be sorted by (symbol, q_ord) before calling the
window functions - ``sort_panel`` does that and ``assert_sorted`` enforces it.

Window rule: a 4-quarter statistic at t is computed only when the four values
t-3..t are all present AND the rows are consecutive fiscal quarters
(period_key step of 1 and 60-130 days between fiscal period ends). Otherwise NaN.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("roic.features")


# ---------------------------------------------------------------------------
# ordering and consecutiveness
# ---------------------------------------------------------------------------
def sort_panel(df: pd.DataFrame, symbol_col: str = "symbol") -> pd.DataFrame:
    return df.sort_values([symbol_col, "q_ord"], kind="mergesort").reset_index(drop=True)


def assert_sorted(df: pd.DataFrame, symbol_col: str = "symbol") -> None:
    sym = df[symbol_col].astype(str).to_numpy()
    q = df["q_ord"].astype("float64").to_numpy()
    same = sym[1:] == sym[:-1]
    if (sym[1:] < sym[:-1]).any() or (same & (q[1:] <= q[:-1])).any():
        raise ValueError("Panel must be sorted by (symbol, q_ord) with unique quarters per symbol")


def add_sequence_info(df: pd.DataFrame, cfg: dict[str, Any], symbol_col: str = "symbol",
                      fiscal_col: str = "fiscal_period_end_date") -> pd.DataFrame:
    """prev_key_gap, prev_day_gap and is_consecutive_prev (link from the previous row to this row)."""
    assert_sorted(df, symbol_col)
    cons = cfg["panel"]["consecutive"]
    g = df.groupby(symbol_col, sort=False)
    out = df.copy()
    out["prev_key_gap"] = (df["q_ord"] - g["q_ord"].shift(1)).astype("Int64")
    out["prev_day_gap"] = (df[fiscal_col] - g[fiscal_col].shift(1)).dt.days.astype("Int64")
    ok = (out["prev_key_gap"] == cons["key_gap_quarters"]) & out["prev_day_gap"].between(cons["min_days"], cons["max_days"])
    out["is_consecutive_prev"] = ok.fillna(False).astype(bool)
    return out


def window_ok(df: pd.DataFrame, n_rows: int, symbol_col: str = "symbol") -> pd.Series:
    """True when rows t-n_rows+1 .. t of the same firm form a consecutive quarterly run."""
    assert_sorted(df, symbol_col)
    link = df["is_consecutive_prev"].astype(bool)
    ok = link.copy() if n_rows > 1 else pd.Series(True, index=df.index)
    g = link.groupby(df[symbol_col], sort=False)
    for k in range(1, n_rows - 1):
        ok &= g.shift(k).astype("boolean").fillna(False).astype(bool)
    return ok


def _lag_matrix(df: pd.DataFrame, col: str, n_rows: int, symbol_col: str) -> np.ndarray:
    """Columns ordered oldest -> newest: value at t-(n-1), ..., t."""
    g = df.groupby(symbol_col, sort=False)[col]
    return np.column_stack([g.shift(n_rows - 1 - k).to_numpy(dtype="float64") for k in range(n_rows)])


def ols_slope_weights(window: int) -> np.ndarray:
    x = np.arange(window, dtype="float64")
    xc = x - x.mean()
    return xc / np.sum(xc ** 2)


def rolling_slope(df: pd.DataFrame, col: str, window: int, symbol_col: str = "symbol") -> pd.Series:
    """OLS slope of ``col`` on x = 0..window-1 over the last ``window`` consecutive quarters."""
    y = _lag_matrix(df, col, window, symbol_col)
    slope = y @ ols_slope_weights(window)          # NaN propagates when any value is missing
    slope[~window_ok(df, window, symbol_col).to_numpy()] = np.nan
    return pd.Series(slope, index=df.index, name=f"{col}_slope")


def rolling_sum_mean(df: pd.DataFrame, col: str, window: int, symbol_col: str = "symbol") -> tuple[pd.Series, pd.Series]:
    y = _lag_matrix(df, col, window, symbol_col)
    ok = window_ok(df, window, symbol_col).to_numpy()
    s = y.sum(axis=1)                               # NaN when any value is missing
    s[~ok] = np.nan
    return pd.Series(s, index=df.index), pd.Series(s / window, index=df.index)


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------
def add_levels(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Full-precision quarterly ROIC (dictionary formulas) and debt-to-equity.

    The CSV stores calc_roic_*_quarterly_ic_raw rounded to 0.01, which creates
    massive ties in cross-sectional sorts. The dictionary defines
        pre-tax  ROIC = ebit / calc_average_ic_raw_quarterly
        post-tax ROIC = calc_nopat_quarterly / calc_average_ic_raw_quarterly
    and both inputs are available in the same file (rounded to 0.01 in $M only).
    """
    c = cfg["columns"]
    out = df.copy()
    avg_ic = out[c["avg_ic"]]
    denom = avg_ic.where(avg_ic != 0)
    source = cfg["features"]["roic_level_source"]
    report: dict[str, Any] = {"roic_level_source": source}
    for name, num_col, file_col in (("roic_pretax_q", c["ebit"], c["roic_pretax_file"]),
                                    ("roic_posttax_q", c["nopat"], c["roic_posttax_file"])):
        if source == "recompute":
            val = out[num_col] / denom
            extra = val.notna() & out[file_col].isna()
            lost = val.isna() & out[file_col].notna()
            val = val.where(~extra)                 # the file's validity (NaN pattern) is authoritative
            report[name] = {"set_nan_where_file_nan": int(extra.sum()), "nan_where_file_has_value": int(lost.sum()),
                            "max_abs_dev_vs_rounded_file": float((val - out[file_col]).abs().max())}
        elif source == "file":
            val = out[file_col].astype("float64")
        else:
            raise ValueError(f"Unknown features.roic_level_source: {source}")
        out[name] = val
    equity = out[c["stockholders_equity"]]
    out["debt_to_equity_q"] = out[c["debt"]] / equity.where(equity != 0)
    return out, report


# ---------------------------------------------------------------------------
# trends and window aggregates
# ---------------------------------------------------------------------------
def add_trend_features(df: pd.DataFrame, cfg: dict[str, Any], symbol_col: str = "symbol") -> pd.DataFrame:
    assert_sorted(df, symbol_col)
    f, c = cfg["features"], cfg["columns"]
    w = int(f["trend_window"])
    out = df.copy()
    ok_w = window_ok(out, w, symbol_col)
    ok_w1 = window_ok(out, w + 1, symbol_col)
    out[f"window_ok_{w}q"] = ok_w
    out[f"window_ok_{w + 1}q"] = ok_w1

    for feat, col in f["trend_inputs"].items():
        out[feat] = rolling_slope(out, col, w, symbol_col)
    for stem, col in f["window_aggregates"].items():
        s, m = rolling_sum_mean(out, col, w, symbol_col)
        out[f"{stem}_sum_{w}q"], out[f"{stem}_mean_{w}q"] = s, m

    # ROIC change across the window: sum of the w quarterly changes = ROIC_t - ROIC_{t-w}
    roic = _lag_matrix(out, "roic_posttax_q", w + 1, symbol_col)
    chg = roic[:, -1] - roic[:, 0]
    chg[~ok_w1.to_numpy()] = np.nan
    out[f"roic_change_{w}q"] = chg

    if f.get("raw_ic_change", True):
        ic = _lag_matrix(out, c["avg_ic"], w + 1, symbol_col)
        change = ic[:, -1] - ic[:, 0]
        pct = np.where(ic[:, 0] > 0, ic[:, -1] / np.where(ic[:, 0] > 0, ic[:, 0], np.nan) - 1.0, np.nan)
        change[~ok_w1.to_numpy()] = np.nan
        pct[~ok_w1.to_numpy()] = np.nan
        out[f"avg_ic_change_{w}q"] = change
        out[f"avg_ic_pct_change_{w}q"] = pct
        slope = rolling_slope(out, c["avg_ic"], w, symbol_col).to_numpy()
        win = _lag_matrix(out, c["avg_ic"], w, symbol_col)
        mean_ic = win.mean(axis=1)
        out[f"avg_ic_trend_{w}q_rel"] = np.where(mean_ic > 0, slope / np.where(mean_ic > 0, mean_ic, np.nan), np.nan)

    # window quality descriptors (flags only - no filtering here)
    status = (out[c["decomposition_status"]] == "VALID").astype("float64")
    econ = out[c["econ_valid"]].astype("boolean").fillna(False).astype("float64")
    ic_pos = (out[c["avg_ic"]] > 0).astype("float64").where(out[c["avg_ic"]].notna())
    for name, series in (("n_decomp_valid", status), ("n_econ_valid", econ), ("n_positive_avg_ic", ic_pos)):
        tmp = out[[symbol_col]].assign(_v=series)
        mat = _lag_matrix(tmp, "_v", w, symbol_col)
        cnt = np.nansum(mat, axis=1)
        cnt[~ok_w.to_numpy()] = np.nan
        out[f"{name}_{w}q"] = cnt
    out[f"all_econ_valid_{w}q"] = (out[f"n_econ_valid_{w}q"] == w)
    return out


def feature_columns(cfg: dict[str, Any]) -> list[str]:
    f = cfg["features"]
    w = int(f["trend_window"])
    cols = ["roic_pretax_q", "roic_posttax_q", "debt_to_equity_q", *f["trend_inputs"].keys()]
    for stem in f["window_aggregates"]:
        cols += [f"{stem}_sum_{w}q", f"{stem}_mean_{w}q"]
    cols += [f"roic_change_{w}q"]
    if f.get("raw_ic_change", True):
        cols += [f"avg_ic_change_{w}q", f"avg_ic_pct_change_{w}q", f"avg_ic_trend_{w}q_rel"]
    cols += [f"n_decomp_valid_{w}q", f"n_econ_valid_{w}q", f"n_positive_avg_ic_{w}q", f"all_econ_valid_{w}q",
             f"window_ok_{w}q", f"window_ok_{w + 1}q"]
    return cols


def feature_summary(df: pd.DataFrame, cols: list[str], mask: pd.Series | None = None) -> pd.DataFrame:
    d = df.loc[mask] if mask is not None else df
    rows = []
    for col in cols:
        s = pd.to_numeric(d[col], errors="coerce").astype("float64")
        v = s.dropna()
        rows.append({"feature": col, "n": int(v.size), "share_available": float(s.notna().mean()),
                     "mean": v.mean(), "std": v.std(), "min": v.min(), "p01": v.quantile(.01), "p25": v.quantile(.25),
                     "median": v.median(), "p75": v.quantile(.75), "p99": v.quantile(.99), "max": v.max(),
                     "n_exact_zero": int((v == 0).sum())})
    return pd.DataFrame(rows)


def ties_by_quarter(df: pd.DataFrame, cols: list[str], mask: pd.Series) -> pd.DataFrame:
    """Distinct values vs observations per quarter - shows the effect of the CSV rounding on sorts."""
    d = df.loc[mask]
    rows = []
    for col in cols:
        g = d.groupby("period_key")[col]
        stats = pd.DataFrame({"n_obs": g.count(), "n_distinct": g.nunique()})
        stats = stats[stats["n_obs"] > 0]
        rows.append({"column": col, "quarters": int(len(stats)),
                     "median_obs_per_quarter": float(stats["n_obs"].median()),
                     "median_distinct_per_quarter": float(stats["n_distinct"].median()),
                     "median_share_tied": float((1 - stats["n_distinct"] / stats["n_obs"]).median())})
    return pd.DataFrame(rows)

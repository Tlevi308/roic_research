"""Timing conventions and the three sample layers.

Layers (never mixed):
1. raw sample            - every row of the panel CSV (stocks + benchmark ETFs).
2. signal-construction   - the investable population at portfolio formation:
   population              company rows whose ticker is an index member in period_key
                           (relevance rule). Defined WITHOUT any reference to returns.
3. evaluation sample     - population rows whose forward return has been realised
                           (return window ended by the data date) and is not NaN.

Timing (researcher's convention):
    formation_date(t)    = month-end of (quarter end of period_key t + 2 months)
    return_window_end(t) = month-end of (formation_date + 3 months)
Example: period_key 2025Q4 -> formation 2026-02-28 -> window end 2026-05-31.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("roic.sample")


def add_timing(df: pd.DataFrame, cfg: dict[str, Any], fiscal_col: str = "fiscal_period_end_date") -> pd.DataFrame:
    t = cfg["timing"]
    out = df.copy()
    month_end = pd.PeriodIndex(out["period"], freq="Q").asfreq("M", how="end")
    shift, horizon = int(t["quarter_shift_months"]), int(t["return_horizon_months"])
    out["formation_date"] = (month_end + shift).to_timestamp(how="end").normalize()
    out["return_window_end"] = (month_end + shift + horizon).to_timestamp(how="end").normalize()
    out["signal_lag_days"] = (out["formation_date"] - out[fiscal_col]).dt.days.astype("Int64")
    fe = out[fiscal_col]
    months = (out["formation_date"].dt.year * 12 + out["formation_date"].dt.month) - (fe.dt.year * 12 + fe.dt.month)
    out["signal_lag_months"] = months.astype("Int64")
    out["signal_lag_below_min"] = (out["signal_lag_months"] < int(t["min_signal_lag_months"])).fillna(False).astype(bool)
    return out


def resolve_data_as_of(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.Timestamp:
    explicit = cfg["timing"].get("data_as_of")
    if explicit:
        return pd.Timestamp(explicit)
    as_of = df[cfg["columns"]["run_date"]].max()
    if pd.isna(as_of):
        raise ValueError("timing.data_as_of is null and run_date has no values")
    return pd.Timestamp(as_of)


def clean_returns(df: pd.DataFrame, cfg: dict[str, Any], as_of: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    """fwd_return / momentum_3q: keep NaN as NaN and undo zero-coding of missing values (flagged)."""
    c, r = cfg["columns"], cfg["returns"]
    out = df.copy()
    raw = out[c["forward_return"]].astype("float64")
    unrealized = (out["return_window_end"] > as_of).fillna(False).astype(bool)
    out["fwd_return_window_unrealized"] = unrealized
    out["fwd_return"] = raw.where(~unrealized) if r["unrealized_to_nan"] else raw
    out["fwd_return_exact_zero_realized"] = (~unrealized) & (raw == 0)

    mom = out[c["momentum"]].astype("float64")
    zero_mom = mom == 0
    out["momentum_3q_exact_zero"] = zero_mom
    out["momentum_3q"] = mom.where(~zero_mom) if r["momentum_exact_zero_to_nan"] else mom

    zero_coded = {}
    for src, dest in (r.get("zero_means_missing") or {}).items():
        is_zero = out[src] == 0
        out[dest] = out[src].where(~is_zero)
        zero_coded[src] = {"derived_column": dest, "zeros_set_nan": int(is_zero.sum()),
                           "zeros_in_company_rows": int((is_zero & ~out["symbol"].isin(cfg["panel"]["benchmark_symbols"])).sum())}

    report = {
        "zero_means_missing": zero_coded,
        "data_as_of": as_of.date().isoformat(),
        "rows_unrealized_window": int(unrealized.sum()),
        "unrealized_by_period": out.loc[unrealized, "period_key"].value_counts().sort_index().to_dict(),
        "unrealized_raw_value_counts": {"exact_zero": int((unrealized & (raw == 0)).sum()),
                                        "nan": int((unrealized & raw.isna()).sum()),
                                        "other": int((unrealized & raw.notna() & (raw != 0)).sum())},
        "realized_exact_zero_returns_kept": int(out["fwd_return_exact_zero_realized"].sum()),
        "momentum_exact_zero_set_nan": int(zero_mom.sum()) if r["momentum_exact_zero_to_nan"] else 0,
    }
    return out, report


def add_sample_layers(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Boolean layer flags. ``in_population`` must not depend on returns (tested)."""
    out = df.copy()
    w = int(cfg["features"]["trend_window"])
    out["is_benchmark"] = out["symbol"].isin(cfg["panel"]["benchmark_symbols"])
    out["in_population"] = (~out["is_benchmark"]) & out["in_index"].astype(bool)
    out["has_roic_pretax_level"] = out["roic_pretax_q"].notna()
    out["has_roic_posttax_level"] = out["roic_posttax_q"].notna()
    out["has_roic_trend"] = out[f"roic_trend_{w}q"].notna()
    contrib = [k for k in cfg["features"]["trend_inputs"] if k != f"roic_trend_{w}q"]
    out["has_all_contribution_trends"] = out[contrib].notna().all(axis=1)
    out["has_level_and_trend_signals"] = out[["has_roic_pretax_level", "has_roic_posttax_level", "has_roic_trend"]].all(axis=1)
    out["in_evaluation_sample"] = out["in_population"] & out["fwd_return"].notna()
    return out


def sample_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Row counts from the raw file to the evaluation sample (overall)."""
    pop = df["in_population"]
    steps = [
        ("1 raw rows (all symbols)", pd.Series(True, index=df.index)),
        ("1a benchmark ETF rows (SPY/QQQ)", df["is_benchmark"]),
        ("1b company rows", ~df["is_benchmark"]),
        ("2 population: company rows that are index members in period_key", pop),
        ("2a population with pre-tax ROIC level", pop & df["has_roic_pretax_level"]),
        ("2b population with post-tax ROIC level", pop & df["has_roic_posttax_level"]),
        ("2c population with ROIC 4q trend", pop & df["has_roic_trend"]),
        ("2d population with all three signals (common sort sample)", pop & df["has_level_and_trend_signals"]),
        ("2e population with all three contribution trends", pop & df["has_all_contribution_trends"]),
        ("2f population rows flagged fiscal end < 2 months before formation (kept)", pop & df["signal_lag_below_min"]),
        ("3 evaluation: population with realised forward return", df["in_evaluation_sample"]),
        ("3d evaluation within common sort sample", df["in_evaluation_sample"] & df["has_level_and_trend_signals"]),
        ("3e evaluation with all contribution trends", df["in_evaluation_sample"] & df["has_all_contribution_trends"]),
    ]
    rows = []
    for label, mask in steps:
        m = mask.astype(bool)
        rows.append({"step": label, "rows": int(m.sum()), "firms": int(df.loc[m, "symbol"].nunique()),
                     "quarters": int(df.loc[m, "period_key"].nunique()),
                     "first_quarter": df.loc[m, "period_key"].min() if m.any() else None,
                     "last_quarter": df.loc[m, "period_key"].max() if m.any() else None})
    return pd.DataFrame(rows)


def sample_flow_by_quarter(df: pd.DataFrame) -> pd.DataFrame:
    d = df[~df["is_benchmark"]]
    g = d.groupby("period_key")
    out = pd.DataFrame({
        "company_rows": g.size(),
        "population": g["in_population"].sum(),
        "pop_with_roic_pretax": g.apply(lambda x: int((x["in_population"] & x["has_roic_pretax_level"]).sum())),
        "pop_with_roic_posttax": g.apply(lambda x: int((x["in_population"] & x["has_roic_posttax_level"]).sum())),
        "pop_with_roic_trend": g.apply(lambda x: int((x["in_population"] & x["has_roic_trend"]).sum())),
        "pop_common_sort_sample": g.apply(lambda x: int((x["in_population"] & x["has_level_and_trend_signals"]).sum())),
        "pop_all_contribution_trends": g.apply(lambda x: int((x["in_population"] & x["has_all_contribution_trends"]).sum())),
        "pop_signal_lag_below_min": g.apply(lambda x: int((x["in_population"] & x["signal_lag_below_min"]).sum())),
        "evaluation_rows": g["in_evaluation_sample"].sum(),
        "evaluation_common_sort_sample": g.apply(lambda x: int((x["in_evaluation_sample"] & x["has_level_and_trend_signals"]).sum())),
        "return_window_unrealized_rows": g["fwd_return_window_unrealized"].sum(),
    }).reset_index()
    return out


def benchmark_series(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    b = df[df["is_benchmark"]]
    cols = ["symbol", "period_key", "formation_date", "return_window_end", cfg["columns"]["fiscal_end"],
            "fwd_return", "fwd_return_window_unrealized", "momentum_3q", cfg["columns"]["forward_return"], cfg["columns"]["momentum"]]
    return b[cols].sort_values(["symbol", "period_key"]).reset_index(drop=True)

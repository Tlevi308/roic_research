"""Data validation: structure, coverage, precision, dictionary formulas, return field,
timing, optional cross-check against the full-precision xlsx, and column mapping.

Validation functions DESCRIBE the data. They never drop or alter rows.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("roic.validation")


# ---------------------------------------------------------------------------
# structure and coverage
# ---------------------------------------------------------------------------
def duplicates_report(df: pd.DataFrame, fiscal_col: str = "fiscal_period_end_date") -> pd.DataFrame:
    rows = []
    for keys in (["symbol", "period_key"], ["symbol", fiscal_col], ["KEY"]):
        dup = df.duplicated(keys, keep=False)
        rows.append({"key": " + ".join(keys), "duplicated_rows": int(dup.sum()),
                     "examples": "; ".join(df.loc[dup, "KEY"].astype(str).head(10))})
    return pd.DataFrame(rows)


def firms_per_quarter(df: pd.DataFrame) -> pd.DataFrame:
    d = df[~df["is_benchmark"]]
    g = d.groupby("period_key")
    return pd.DataFrame({
        "company_rows": g.size(),
        "distinct_firms": g["symbol"].nunique(),
        "population_firms": g["in_population"].sum(),
        "rows_not_in_index": g["in_population"].apply(lambda s: int((~s).sum())),
    }).reset_index()


def quarters_per_firm(df: pd.DataFrame) -> pd.DataFrame:
    d = df[~df["is_benchmark"]]
    g = d.groupby("symbol")
    out = pd.DataFrame({
        "company": g["company"].first(),
        "sector": g["sector"].first(),
        "n_quarters": g.size(),
        "first_period": g["period_key"].min(),
        "last_period": g["period_key"].max(),
        "span_quarters": (g["q_ord"].max() - g["q_ord"].min() + 1).astype("Int64"),
        "n_breaks_in_sequence": g["is_consecutive_prev"].apply(lambda s: int((~s).sum()) - 1),
        "max_key_gap": g["prev_key_gap"].max(),
        "population_quarters": g["in_population"].sum(),
    }).reset_index()
    out["missing_quarters_inside_span"] = out["span_quarters"] - out["n_quarters"]
    return out


def sequence_breaks(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Rows whose link to the previous row is not a consecutive quarter (first rows excluded)."""
    d = df[~df["is_benchmark"] & df["prev_key_gap"].notna() & ~df["is_consecutive_prev"]]
    cols = ["KEY", "symbol", "period_key", "fiscal_period_end_date", "prev_key_gap", "prev_day_gap",
            cfg["columns"]["decomposition_status"], "in_population"]
    out = d[cols].copy()
    out["reason"] = np.where(out["prev_key_gap"] != cfg["panel"]["consecutive"]["key_gap_quarters"],
                             "period_key gap", "fiscal-end day gap outside range")
    out["pipeline_says_nonconsecutive"] = out[cfg["columns"]["decomposition_status"]] == "UNCLASSIFIED_NONCONSECUTIVE"
    return out.reset_index(drop=True)


def consecutiveness_agreement(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    d = df[~df["is_benchmark"]]
    pipe_nc = d[cfg["columns"]["decomposition_status"]] == "UNCLASSIFIED_NONCONSECUTIVE"
    ours_nc = ~d["is_consecutive_prev"]
    tab = pd.crosstab(ours_nc.rename("ours_not_consecutive"), pipe_nc.rename("pipeline_NONCONSECUTIVE"))
    return tab.reset_index()


def signal_funnel_by_quarter(df: pd.DataFrame, coverage: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Index members -> panel row -> IC -> ROIC -> 4q trend -> realised return, per quarter."""
    c = cfg["columns"]
    w = int(cfg["features"]["trend_window"])
    pop = df[df["in_population"]]
    g = pop.groupby("period_key")
    out = pd.DataFrame({
        "index_members": coverage.set_index("period_key")["index_members"],
        "with_panel_row": g.size(),
        "with_ic_raw": g[c["ic_raw"]].count(),
        "with_avg_ic": g[c["avg_ic"]].count(),
        "with_roic_posttax": g["roic_posttax_q"].count(),
        "with_roic_trend": g[f"roic_trend_{w}q"].count(),
        "common_sort_sample_with_return": (pop["has_level_and_trend_signals"] & pop["in_evaluation_sample"]).groupby(pop["period_key"]).sum(),
    })
    return out.fillna(0).astype(int).reset_index().rename(columns={"index": "period_key"})


def roic_missing_reasons(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """First reason (in formula order) why post-tax ROIC is missing for population rows; overall and by sector."""
    c = cfg["columns"]
    pop = df[df["in_population"] & df["roic_posttax_q"].isna()].copy()
    no_wc = pop["total_current_assets"].isna() | pop["total_current_liabilities"].isna()
    conditions = [
        (no_wc, "1 current assets or current liabilities missing (IC undefined)"),
        (pop["goodwill"].isna(), "2 goodwill missing, other IC inputs present"),
        (pop["net_ppe"].isna(), "3 net PPE missing"),
        (pop[c["ic_raw"]].notna() & pop[c["avg_ic"]].isna(), "4 IC present but previous quarter missing / not consecutive"),
        (pop[c["nopat"]].isna(), "5 NOPAT missing (EBIT, tax or pretax income)"),
        (pop[c["avg_ic"]] == 0, "6 average IC equals zero"),
    ]
    reason = pd.Series("7 other", index=pop.index)
    for mask, label in reversed(conditions):
        reason = reason.mask(mask.fillna(False).astype(bool), label)
    pop["reason"] = reason
    overall = pop["reason"].value_counts().sort_index().rename_axis("reason").reset_index(name="rows")
    overall["rows_per_quarter"] = overall["rows"] / pop["period_key"].nunique()
    base = df[df["in_population"]].groupby("sector").size()
    by_sector = pd.crosstab(pop["sector"], pop["reason"])
    by_sector.insert(0, "population_rows", base.reindex(by_sector.index))
    by_sector.insert(1, "share_without_roic", by_sector.drop(columns="population_rows").sum(axis=1) / by_sector["population_rows"])
    return overall, by_sector.sort_values("share_without_roic", ascending=False).reset_index()


# ---------------------------------------------------------------------------
# precision
# ---------------------------------------------------------------------------
def _max_decimals(values: np.ndarray, max_check: int = 9) -> int:
    if values.size == 0:
        return -1
    for d in range(max_check + 1):
        if np.all(np.abs(values - np.round(values, d)) <= 1e-9 * np.maximum(1.0, np.abs(values))):
            return d
    return max_check + 1


def precision_audit(df: pd.DataFrame, columns: list[str], cfg: dict[str, Any]) -> pd.DataFrame:
    thr = cfg["validation"]["max_decimals_rounded_threshold"]
    rows = []
    for col in columns:
        s = df[col]
        if not pd.api.types.is_float_dtype(s) and not pd.api.types.is_integer_dtype(s):
            continue
        v = s.dropna().to_numpy(dtype="float64")
        v = v[np.isfinite(v)]
        md = _max_decimals(v)
        med = float(np.median(np.abs(v))) if v.size else np.nan
        rows.append({"column": col, "n_non_null": int(v.size), "n_distinct": int(pd.Series(v).nunique()),
                     "max_decimals": md, "median_abs_value": med,
                     "ratio_like_and_rounded": bool(v.size and pd.Series(v).nunique() > 1 and md <= thr and med < 5)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# dictionary formulas vs data
# ---------------------------------------------------------------------------
def _check(name: str, ref: str, tested: pd.Series, dev: pd.Series, tol: pd.Series | float, note: str = "") -> dict[str, Any]:
    m = tested.fillna(False).astype(bool) & dev.notna()
    d = dev[m].abs()
    t = tol[m] if isinstance(tol, pd.Series) else tol
    passed = d <= t
    return {"check": name, "dictionary_reference": ref, "n_tested": int(m.sum()), "n_pass": int(passed.sum()),
            "share_pass": float(passed.mean()) if m.any() else np.nan, "max_abs_dev": float(d.max()) if m.any() else np.nan,
            "median_abs_dev": float(d.median()) if m.any() else np.nan,
            "tolerance": "row-specific (rounding propagated)" if isinstance(tol, pd.Series) else tol, "note": note}


def formula_checks(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Re-derive documented formulas from the (rounded) CSV inputs. Tolerances reflect CSV rounding."""
    c = cfg["columns"]
    d = df[~df["is_benchmark"]].copy()
    step = cfg["validation"]["rounding_step_2dp"]
    step6 = cfg["validation"]["rounding_step_6dp"]
    g = d.groupby("symbol", sort=False)
    rows = []

    rows.append(_check("tax expense = -tax_provision", "4 calc_tax_expense_quarterly",
                       d["tax_provision"].notna(), d["calc_tax_expense_quarterly"] + d["tax_provision"], 1e-9))
    T = d["calc_tax_expense_quarterly"] / d["pretax_income"].where(d["pretax_income"] != 0)
    tol_T = step + step * (1 + T.abs()) / d["pretax_income"].abs()
    rows.append(_check("tax rate = tax expense / pretax income", "4 calc_raw_tax_rate_quarterly",
                       d["calc_raw_tax_rate_quarterly"].notna(), d["calc_raw_tax_rate_quarterly"] - T, tol_T,
                       "CSV rounds the tax rate to 0.01"))
    nopat = d["ebit"] * (1 - T)
    tol_n = step + d["ebit"].abs() * step * (1 + T.abs()) / d["pretax_income"].abs() + step * (1 - T).abs()
    rows.append(_check("NOPAT = EBIT x (1 - T)", "4 calc_nopat_quarterly", d[c["nopat"]].notna(), d[c["nopat"]] - nopat, tol_n))
    ic = d["total_current_assets"] - d["total_current_liabilities"] + d["net_ppe"] + d["goodwill"]
    rows.append(_check("IC raw = TCA - TCL + net PPE + goodwill", "4 calc_ic_raw", d[c["ic_raw"]].notna(), d[c["ic_raw"]] - ic, 5 * step))
    rows.append({"check": "IC raw NaN pattern equals NaN of any input", "dictionary_reference": "4 calc_ic_raw",
                 "n_tested": len(d), "n_pass": int((ic.isna() == d[c["ic_raw"]].isna()).sum()),
                 "share_pass": float((ic.isna() == d[c["ic_raw"]].isna()).mean()), "max_abs_dev": np.nan,
                 "median_abs_dev": np.nan, "tolerance": "exact", "note": ""})
    pipe_consec = d["prev_key_gap"] == 1
    avg = (d[c["ic_raw"]] + g[c["ic_raw"]].shift(1)) / 2
    rows.append(_check("average IC = (IC[t-1] + IC[t]) / 2 (previous quarter in sequence)", "4 calc_average_ic_raw_quarterly",
                       d[c["avg_ic"]].notna() & pipe_consec, d[c["avg_ic"]] - avg, 3 * step))
    nonc = d["prev_key_gap"].isna() | (d["prev_key_gap"] != 1)
    rows.append({"check": "average IC is NaN after a sequence break", "dictionary_reference": "4 calc_average_ic_raw_quarterly",
                 "n_tested": int(nonc.sum()), "n_pass": int((nonc & d[c["avg_ic"]].isna()).sum()),
                 "share_pass": float(d.loc[nonc, c["avg_ic"]].isna().mean()), "max_abs_dev": np.nan,
                 "median_abs_dev": np.nan, "tolerance": "exact", "note": ""})
    for lab, col_file, col_new in (("pre-tax", c["roic_pretax_file"], "roic_pretax_q"), ("post-tax", c["roic_posttax_file"], "roic_posttax_q")):
        rows.append(_check(f"{lab} ROIC (file, rounded) = recomputed ROIC rounded to 0.01", f"4 {col_file}",
                           d[col_file].notna(), d[col_file] - d[col_new], step + 1e-9 + 2 * step / d[c["avg_ic"]].abs(),
                           "file value is display-rounded; recomputed value keeps full precision"))
    roic_prev = g["roic_posttax_q"].shift(1)
    tol_chg = step6 + 2 * step * (1 + d["roic_posttax_q"].abs()) / d[c["avg_ic"]].abs()
    rows.append(_check("ROIC change = ROIC[t] - ROIC[t-1]", "6.1 calc_roic_posttax_change_quarterly",
                       d[c["roic_change"]].notna(), d[c["roic_change"]] - (d["roic_posttax_q"] - roic_prev), tol_chg))
    rows.append(_check("Shapley: C_EBIT + C_TAX + C_IC = change in ROIC", "6.2 / 11",
                       d[c["roic_change"]].notna(), d[c["c_ebit"]] + d[c["c_tax"]] + d[c["c_ic"]] - d[c["roic_change"]], 4 * step6))
    rows.append(_check("decomposition residual = 0", "6.2 calc_roic_decomposition_residual",
                       d["calc_roic_decomposition_residual"].notna(), d["calc_roic_decomposition_residual"], step6))

    # Shapley contributions rebuilt from E, Q, I with the dictionary weights 1/3 and 1/6
    E1, E0 = d["ebit"], g["ebit"].shift(1)
    Q1 = 1 - T
    Q0 = Q1.groupby(d["symbol"], sort=False).shift(1)
    I1, I0 = d[c["avg_ic"]], g[c["avg_ic"]].shift(1)
    ce = (E1 - E0) * (Q0 / I0 / 3 + Q1 / I0 / 6 + Q0 / I1 / 6 + Q1 / I1 / 3)
    ct = (Q1 - Q0) * (E0 / I0 / 3 + E1 / I0 / 6 + E0 / I1 / 6 + E1 / I1 / 3)
    ci = (1 / I1 - 1 / I0) * (E0 * Q0 / 3 + E1 * Q0 / 6 + E0 * Q1 / 6 + E1 * Q1 / 3)
    valid = d[c["decomposition_status"]] == "VALID"
    for lab, rebuilt, col in (("EBIT", ce, c["c_ebit"]), ("TAX", ct, c["c_tax"]), ("IC", ci, c["c_ic"])):
        rows.append(_check(f"Shapley C_{lab} rebuilt from E, Q, I (weights 1/3, 1/6)", "6.2 calc_roic_*_contribution",
                           valid & d[col].notna(), d[col] - rebuilt, 1e-5,
                           "confirms the OMML formula reading; inputs are CSV-rounded"))

    rows.append(_check("debt = short-term + long-term debt incl. leases", "4 calc_debt_value_quarterly",
                       d[c["debt"]].notna(), d[c["debt"]] - d["short_term_debt_and_capital_lease"] - d["long_term_debt_and_capital_lease"], 3 * step))
    tol_de = step + 1e-9 + 2 * step * (1 + d["debt_to_equity_q"].abs()) / d[c["stockholders_equity"]].abs()
    rows.append(_check("D/E (file, rounded) = debt / stockholders equity", "4 calc_debt_to_equity_quarterly",
                       d[c["de_file"]].notna(), d[c["de_file"]] - d["debt_to_equity_q"], tol_de, "file value rounded to 0.01"))
    ev = d[c["econ_valid"]]
    tested = valid & ev.notna()
    agree = ev.astype("boolean") == (d[c["quality_flag"]] == "VALID")
    rows.append({"check": "economic_interpretation_valid == (quality_flag == VALID)", "dictionary_reference": "6.7",
                 "n_tested": int(tested.sum()), "n_pass": int((agree & tested).sum()),
                 "share_pass": float(agree[tested].mean()), "max_abs_dev": np.nan, "median_abs_dev": np.nan,
                 "tolerance": "exact", "note": ""})
    ann = (1 + d["roic_posttax_q"]) ** 4 - 1
    rows.append(_check("annualised post-tax ROIC = (1 + q)^4 - 1", "7.1 calc_roic_posttax_annualized_ic_raw",
                       d["calc_roic_posttax_annualized_ic_raw"].notna(), d["calc_roic_posttax_annualized_ic_raw"] - ann,
                       step6 + 8 * step * (1 + d["roic_posttax_q"].abs()) ** 3 / d[c["avg_ic"]].abs()))

    fr = d[c["forward_return"]]
    three_prev = (d["prev_key_gap"] == 1) & (g["prev_key_gap"].shift(1) == 1) & (g["prev_key_gap"].shift(2) == 1)
    mom = (1 + g[c["forward_return"]].shift(1)) * (1 + g[c["forward_return"]].shift(2)) * (1 + g[c["forward_return"]].shift(3)) - 1
    rows.append(_check("Momentum_3Q = prod(1 + FQR[t-3..t-1]) - 1", "not in dictionary",
                       d[c["momentum"]].notna() & three_prev & fr.notna(), d[c["momentum"]] - mom, 1e-6,
                       "momentum is built only from returns realised before formation(t)"))

    shifted = d["fiscal_period_end_date"] - pd.DateOffset(months=cfg["timing"]["quarter_shift_months"])
    rule = shifted.dt.year.astype("Int64").astype(str) + "Q" + shifted.dt.quarter.astype("Int64").astype(str)
    ok = rule == d["period_key"]
    rows.append({"check": "period_key = calendar quarter of (fiscal end - 2 months)", "dictionary_reference": "1 alignment",
                 "n_tested": len(d), "n_pass": int(ok.sum()), "share_pass": float(ok.mean()), "max_abs_dev": np.nan,
                 "median_abs_dev": np.nan, "tolerance": "exact", "note": ""})
    ok = df["KEY"] == df["period_key"].astype(str) + "_" + df["symbol"].astype(str)
    rows.append({"check": "KEY = period_key + '_' + symbol", "dictionary_reference": "not in dictionary",
                 "n_tested": len(df), "n_pass": int(ok.sum()), "share_pass": float(ok.mean()), "max_abs_dev": np.nan,
                 "median_abs_dev": np.nan, "tolerance": "exact", "note": "all rows incl. benchmarks"})
    yq = d["period_year"].astype("Int64").astype(str) + d["period_quarter"].astype(str)
    ok = yq == d["period_key"]
    rows.append({"check": "period_year + period_quarter = period_key", "dictionary_reference": "1",
                 "n_tested": len(d), "n_pass": int(ok.sum()), "share_pass": float(ok.mean()), "max_abs_dev": np.nan,
                 "median_abs_dev": np.nan, "tolerance": "exact", "note": ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# return field identification and verification
# ---------------------------------------------------------------------------
RETURN_NAME_PATTERN = r"(?i)(return|\bret\b|_ret|fwd|forward|future|momentum|price|close|\bmom)"


def return_field_candidates(df: pd.DataFrame, file_columns: list[str], catalogue: pd.DataFrame) -> pd.DataFrame:
    docs = catalogue.set_index("variable")
    rows = []
    for col in file_columns:
        if not re.search(RETURN_NAME_PATTERN, col) or col not in df.columns:
            continue
        s = df.loc[~df["is_benchmark"], col].astype("float64")
        in_dict = col in docs.index
        rows.append({"column": col, "in_dictionary": in_dict,
                     "dictionary_meaning": docs.loc[col, "meaning"] if in_dict else "",
                     "non_null_company_rows": int(s.notna().sum()), "exact_zero": int((s == 0).sum()),
                     "mean": s.mean(), "std": s.std(), "p01": s.quantile(.01), "median": s.median(), "p99": s.quantile(.99)})
    return pd.DataFrame(rows)


def verify_return_window(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Evidence on what Future_Quarterly_Return(t) measures, using only in-file prices and dates."""
    c = cfg["columns"]
    rows = []
    b = df[df["is_benchmark"]]
    same = (b[c["fiscal_end"]] == b["formation_date"])
    rows.append({"evidence": "SPY/QQQ rows: fiscal_period_end_date equals formation_date (quarter end + 2 months)",
                 "n": int(len(b)), "value": float(same.mean()), "metric": "share equal"})
    d = df[~df["is_benchmark"]].copy()
    g = d.groupby("symbol", sort=False)
    px = d[c["price"]].where(d[c["price"]] > 0)
    nxt = g[c["price"]].shift(-1).where(lambda s: s > 0)
    prv = g[c["price"]].shift(1).where(lambda s: s > 0)
    link_next = g["is_consecutive_prev"].shift(-1).astype("boolean").fillna(False).astype(bool)
    r_fwd, r_bwd = nxt / px - 1, px / prv - 1
    fr = d[c["forward_return"]]
    realized = ~d["fwd_return_window_unrealized"]
    for label, mask in (("fiscal end = formation date (lag 0 days)", d["signal_lag_days"] == 0),
                        ("fiscal end = formation - ~1 month", d["signal_lag_days"].between(25, 35)),
                        ("fiscal end = formation - ~2 months (calendar quarters)", d["signal_lag_days"].between(55, 65))):
        mf = (mask.fillna(False).astype(bool) & link_next & r_fwd.notna() & fr.notna() & realized)
        mb = (mask.fillna(False).astype(bool) & d["is_consecutive_prev"] & r_bwd.notna() & fr.notna() & realized)
        diff = (fr - r_fwd)[mf]
        rows.append({"evidence": f"{label}: corr(FQR[t], price[t+1]/price[t]-1)", "n": int(mf.sum()),
                     "value": float(np.corrcoef(fr[mf], r_fwd[mf])[0, 1]) if mf.sum() > 2 else np.nan, "metric": "correlation"})
        rows.append({"evidence": f"{label}: median |FQR[t] - (price[t+1]/price[t]-1)|", "n": int(mf.sum()),
                     "value": float(diff.abs().median()) if mf.any() else np.nan, "metric": "median abs diff"})
        rows.append({"evidence": f"{label}: corr(FQR[t], price[t]/price[t-1]-1) (wrong alignment)", "n": int(mb.sum()),
                     "value": float(np.corrcoef(fr[mb], r_bwd[mb])[0, 1]) if mb.sum() > 2 else np.nan, "metric": "correlation"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------
def timing_audit(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, pd.DataFrame]:
    c = cfg["columns"]
    d = df[~df["is_benchmark"]].copy()
    lag = (d[c["filing_date"]] - d[c["fiscal_end"]]).dt.days
    q = lag.quantile([0, .01, .05, .25, .5, .75, .9, .95, .99, 1])
    filing = pd.DataFrame({"statistic": ["n_non_null"] + [f"q{int(p * 100):02d}" for p in q.index] + [
        "share_filing_after_formation", "share_filing_lag_over_365d", "share_negative_lag"],
        "value": [int(lag.notna().sum())] + q.tolist() + [
            float((d[c["filing_date"]] > d["formation_date"])[d[c["filing_date"]].notna()].mean()),
            float((lag > 365).mean()), float((lag < 0).mean())]})
    by_year = d.assign(filing_after_formation=d[c["filing_date"]] > d["formation_date"], lag=lag).groupby("period_year").agg(
        rows=("KEY", "size"), share_filing_after_formation=("filing_after_formation", "mean"), median_filing_lag_days=("lag", "median")).reset_index()
    sig = d.assign(fiscal_end_month=d[c["fiscal_end"]].dt.month).groupby("fiscal_end_month").agg(
        rows=("KEY", "size"), population_rows=("in_population", "sum"),
        lag_months=("signal_lag_months", "median"), min_lag_days=("signal_lag_days", "min"),
        max_lag_days=("signal_lag_days", "max"), rows_flagged=("signal_lag_below_min", "sum")).reset_index()
    return {"filing_lag_summary": filing, "filing_lag_by_year": by_year, "signal_lag_by_fiscal_month": sig}


# ---------------------------------------------------------------------------
# xlsx cross-check (read-only)
# ---------------------------------------------------------------------------
def xlsx_crosscheck(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    path: Path = cfg["_paths"]["crosscheck_xlsx"]
    c = cfg["columns"]
    if not path.exists():
        return {"available": False}
    LOGGER.info("Cross-checking against %s (read-only)", path.name)
    sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
    price = pd.read_excel(path, sheet_name="Price", engine="openpyxl")
    full_cols = ["KEY", "symbol", "period_key", c["ebit"], c["avg_ic"], "calc_raw_tax_rate_quarterly", c["nopat"],
                 c["roic_pretax_file"], c["roic_posttax_file"], c["roic_change"], c["c_ebit"], c["c_tax"], c["c_ic"],
                 c["decomposition_status"], "calc_roic_posttax_annualized_ic_raw", c["de_file"], c["forward_return"], c["momentum"]]
    data = pd.read_excel(path, sheet_name="Data", engine="openpyxl", usecols=full_cols, dtype={"symbol": str, "period_key": str})
    full_checks = full_precision_formula_checks(data, cfg)
    data = data[["KEY", c["roic_pretax_file"], c["roic_posttax_file"], c["de_file"], c["forward_return"], c["momentum"]]]
    manifest = pd.read_excel(path, sheet_name="Manifest", engine="openpyxl") if "Manifest" in sheets else pd.DataFrame()
    checks = pd.read_excel(path, sheet_name="Checks", engine="openpyxl") if "Checks" in sheets else pd.DataFrame()

    rows = []
    m = df[["KEY", "period_key", "is_benchmark", "formation_date", c["forward_return"], c["momentum"], "fwd_return",
            "momentum_3q", "roic_pretax_q", "roic_posttax_q", "debt_to_equity_q", c["roic_posttax_file"]]].merge(
        price[["KEY", "As_Of_Date", c["forward_return"], c["momentum"]]].rename(
            columns={c["forward_return"]: "fqr_price_sheet", c["momentum"]: "mom_price_sheet"}), on="KEY", how="left").merge(
        data.rename(columns={c["roic_pretax_file"]: "roic_pretax_xlsx", c["roic_posttax_file"]: "roic_posttax_xlsx",
                             c["de_file"]: "de_xlsx", c["forward_return"]: "fqr_data_xlsx", c["momentum"]: "mom_data_xlsx"}),
        on="KEY", how="left")
    has_price = m["As_Of_Date"].notna()
    rows.append({"check": "CSV keys found in xlsx Price sheet", "value": float(has_price.mean()), "n": int(len(m))})
    rows.append({"check": "Price.As_Of_Date == formation_date", "value": float((m.loc[has_price, "As_Of_Date"] == m.loc[has_price, "formation_date"]).mean()), "n": int(has_price.sum())})
    z = (m[c["forward_return"]] == 0) & m["fqr_price_sheet"].isna() & has_price
    rows.append({"check": "CSV FQR == 0 where Price sheet FQR is NaN (zero-coded missing)", "value": int(z.sum()), "n": int(has_price.sum()),
                 "detail": str(m.loc[z, "period_key"].value_counts().to_dict())})
    rows.append({"check": "cleaned fwd_return NaN where Price sheet NaN (company rows with price data)",
                 "value": float((m.loc[has_price & m["fqr_price_sheet"].isna(), "fwd_return"].isna()).mean()),
                 "n": int((has_price & m["fqr_price_sheet"].isna()).sum())})
    both = m["fwd_return"].notna() & m["fqr_price_sheet"].notna()
    rows.append({"check": "max |fwd_return - Price sheet FQR|", "value": float((m.loc[both, "fwd_return"] - m.loc[both, "fqr_price_sheet"]).abs().max()), "n": int(both.sum())})
    zm = (m[c["momentum"]] == 0) & m["mom_price_sheet"].isna() & has_price
    zm_real = (m[c["momentum"]] == 0) & m["mom_price_sheet"].notna()
    rows.append({"check": "CSV Momentum_3Q == 0 where Price sheet is NaN (zero-coded missing)", "value": int(zm.sum()), "n": int(has_price.sum())})
    rows.append({"check": "CSV Momentum_3Q == 0 where Price sheet also has a value (genuine zero, set NaN by rule)", "value": int(zm_real.sum()), "n": int(has_price.sum())})
    for lab, new, full, rounded in (("pre-tax", "roic_pretax_q", "roic_pretax_xlsx", None),
                                    ("post-tax", "roic_posttax_q", "roic_posttax_xlsx", c["roic_posttax_file"])):
        ok = m[new].notna() & m[full].notna()
        dev = (m.loc[ok, new] - m.loc[ok, full]).abs()
        rows.append({"check": f"{lab} ROIC recomputed vs xlsx full precision: median |diff|", "value": float(dev.median()), "n": int(ok.sum())})
        rows.append({"check": f"{lab} ROIC recomputed vs xlsx full precision: 99th pct |diff|", "value": float(dev.quantile(.99)), "n": int(ok.sum())})
        rows.append({"check": f"{lab} ROIC recomputed vs xlsx full precision: max |diff|", "value": float(dev.max()), "n": int(ok.sum())})
        if rounded:
            devr = (m.loc[ok, rounded] - m.loc[ok, full]).abs()
            rows.append({"check": f"{lab} ROIC CSV rounded vs xlsx full precision: median |diff|", "value": float(devr.median()), "n": int(ok.sum())})
        rows.append({"check": f"{lab} ROIC NaN pattern recomputed == xlsx", "value": float((m[new].isna() == m[full].isna()).mean()), "n": int(len(m))})
    ok = m["debt_to_equity_q"].notna() & m["de_xlsx"].notna()
    rows.append({"check": "D/E recomputed vs xlsx: 99th pct |diff|", "value": float((m.loc[ok, "debt_to_equity_q"] - m.loc[ok, "de_xlsx"]).abs().quantile(.99)), "n": int(ok.sum())})
    return {"available": True, "sheets": sheets, "summary": pd.DataFrame(rows), "manifest": manifest,
            "pipeline_checks": checks, "full_precision_checks": full_checks}


def full_precision_formula_checks(data: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Dictionary formulas on the full-precision xlsx values: separates rounding noise from real mismatches."""
    c = cfg["columns"]
    d = data[data[c["avg_ic"]].notna() | data[c["ebit"]].notna()].copy()
    d["_q"] = pd.PeriodIndex(d["period_key"], freq="Q").asi8
    d = d.sort_values(["symbol", "_q"], kind="mergesort")
    g = d.groupby("symbol", sort=False)
    step_ok = (d["_q"] - g["_q"].shift(1)) == 1
    E1, E0 = d[c["ebit"]], g[c["ebit"]].shift(1)
    Q1 = 1 - d["calc_raw_tax_rate_quarterly"]
    Q0 = Q1.groupby(d["symbol"], sort=False).shift(1)
    I1, I0 = d[c["avg_ic"]], g[c["avg_ic"]].shift(1)
    rebuilt = {
        c["c_ebit"]: (E1 - E0) * (Q0 / I0 / 3 + Q1 / I0 / 6 + Q0 / I1 / 6 + Q1 / I1 / 3),
        c["c_tax"]: (Q1 - Q0) * (E0 / I0 / 3 + E1 / I0 / 6 + E0 / I1 / 6 + E1 / I1 / 3),
        c["c_ic"]: (1 / I1 - 1 / I0) * (E0 * Q0 / 3 + E1 * Q0 / 6 + E0 * Q1 / 6 + E1 * Q1 / 3),
    }
    valid = (d[c["decomposition_status"]] == "VALID") & step_ok

    def rel_tol(x: pd.Series) -> pd.Series:
        return 1e-9 * np.maximum(1.0, x.abs())

    rows = []
    for col, val in rebuilt.items():
        rows.append(_check(f"[full precision] {col} = Shapley formula (1/3, 1/6)", "6.2", valid & d[col].notna(), d[col] - val, rel_tol(d[col])))
    roic = d[c["nopat"]] / d[c["avg_ic"]].where(d[c["avg_ic"]] != 0)
    rows.append(_check("[full precision] post-tax ROIC = NOPAT / avg IC", "4", d[c["roic_posttax_file"]].notna(), d[c["roic_posttax_file"]] - roic, rel_tol(roic)))
    rows.append(_check("[full precision] pre-tax ROIC = EBIT / avg IC", "4", d[c["roic_pretax_file"]].notna(),
                       d[c["roic_pretax_file"]] - d[c["ebit"]] / d[c["avg_ic"]].where(d[c["avg_ic"]] != 0), rel_tol(d[c["roic_pretax_file"]])))
    prev = g[c["roic_posttax_file"]].shift(1)
    rows.append(_check("[full precision] ROIC change = ROIC[t] - ROIC[t-1]", "6.1", d[c["roic_change"]].notna() & step_ok,
                       d[c["roic_change"]] - (d[c["roic_posttax_file"]] - prev), rel_tol(prev)))
    rows.append(_check("[full precision] C_EBIT + C_TAX + C_IC = change", "6.2", d[c["roic_change"]].notna(),
                       d[c["c_ebit"]] + d[c["c_tax"]] + d[c["c_ic"]] - d[c["roic_change"]], rel_tol(d[c["roic_change"]])))
    ann = (1 + d[c["roic_posttax_file"]]) ** 4 - 1
    rows.append(_check("[full precision] annualised = (1 + q)^4 - 1", "7.1", d["calc_roic_posttax_annualized_ic_raw"].notna(),
                       d["calc_roic_posttax_annualized_ic_raw"] - ann, rel_tol(ann)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# column mapping
# ---------------------------------------------------------------------------
def reconcile_columns(catalogue: pd.DataFrame, file_columns: list[str], duplicate_cols: pd.DataFrame) -> pd.DataFrame:
    documented = catalogue.set_index("variable")
    dup_map = dict(zip(duplicate_cols["duplicate_column"], duplicate_cols["base_column"])) if len(duplicate_cols) else {}
    names = list(dict.fromkeys(list(catalogue["variable"]) + list(file_columns)))
    rows = []
    for n in names:
        in_d, in_f = n in documented.index, n in file_columns
        if in_d and in_f:
            status = "matched"
        elif in_d:
            status = "dictionary_only"
        elif n in dup_map:
            status = f"file_only: duplicate of {dup_map[n]}"
        else:
            status = "file_only: undocumented"
        rows.append({"column": n, "in_dictionary": in_d, "in_csv": in_f, "status": status,
                     "dictionary_section": documented.loc[n, "section"] if in_d else ""})
    return pd.DataFrame(rows)


def build_column_mapping(cfg: dict[str, Any], catalogue: pd.DataFrame, file_columns: list[str],
                         derived_columns: list[str], notes_by_column: dict[str, list[str]]) -> pd.DataFrame:
    documented = set(catalogue["variable"])
    rows = []
    for item in cfg["concepts"]:
        dict_name, col = item.get("dictionary"), item.get("column")
        in_file = bool(col) and col in file_columns
        derived = bool(col) and col in derived_columns
        notes = list(notes_by_column.get(col, [])) if col else []
        if dict_name and dict_name not in documented:
            notes.append(f"dictionary name '{dict_name}' NOT found in the dictionary")
        if not dict_name and in_file:
            notes.append("column exists in the CSV but is NOT documented in the dictionary")
        if derived:
            notes.append("not in source files - computed in Stage 1 (src/features.py)")
        if not col:
            notes.append("not available in any provided file - requires an additional data source")
        if dict_name and col and dict_name != col:
            notes.append(f"name differs: dictionary '{dict_name}' vs column '{col}'")
        if item.get("note"):
            notes.append(item["note"])
        rows.append({"research_concept": item["concept"],
                     "dictionary_column": dict_name if dict_name else "",
                     "actual_column": col if col else "",
                     "in_dictionary": bool(dict_name) and dict_name in documented,
                     "found": in_file or derived,
                     "source": "csv" if in_file else ("computed" if derived else "missing"),
                     "notes": " | ".join(notes)})
    return pd.DataFrame(rows)

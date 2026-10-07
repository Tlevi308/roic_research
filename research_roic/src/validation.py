"""Validation functions DESCRIBE the panel. They never drop or alter rows.

Every table answers one question and names the measurement behind it, so a
claim in the report can always be traced to a file.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import explain, labels as L, schema
from .keys import Grid

LOGGER = logging.getLogger("roic.validation")


def _numeric(panel: pd.DataFrame) -> list[str]:
    return [c for c in panel.columns if panel[c].dtype == "float64"]


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------
def coverage_by_column(panel: pd.DataFrame) -> pd.DataFrame:
    """Availability and distribution of every column, plus exact-zero counts."""
    rows = []
    n = len(panel)
    for col in panel.columns:
        s = panel[col]
        non_null = int(s.notna().sum())
        row: dict[str, Any] = {
            "column": col, "dtype": str(s.dtype), "n": n, "n_non_null": non_null,
            "share_available": non_null / n if n else np.nan,
        }
        if s.dtype == "float64":
            v = s.to_numpy()
            finite = v[np.isfinite(v)]
            row["n_exact_zero"] = int((finite == 0).sum())
            row["n_negative"] = int((finite < 0).sum())
            if finite.size:
                qs = np.percentile(finite, [1, 25, 50, 75, 99])
                row.update({"mean": finite.mean(), "std": finite.std(),
                            "min": finite.min(), "p01": qs[0], "p25": qs[1],
                            "median": qs[2], "p75": qs[3], "p99": qs[4],
                            "max": finite.max()})
        else:
            row["n_distinct"] = int(s.nunique(dropna=True))
        rows.append(row)
    return pd.DataFrame(rows)


def coverage_by_quarter(panel: pd.DataFrame, panel_keys: pd.DataFrame) -> pd.DataFrame:
    """Rows, firms and headline-column availability per period."""
    headline = ["ebit", "calc_raw_tax_rate_quarterly", "calc_nopat_quarterly",
                "calc_ic_raw", "calc_average_ic_raw_quarterly",
                "calc_roic_posttax_quarterly_ic_raw", "calc_debt_value_quarterly",
                "equity", "calc_debt_to_equity_quarterly", "market_cap"]
    d = pd.DataFrame({"period_key": panel_keys["period_key"].to_numpy(),
                      "gvkey": panel_keys["gvkey"].to_numpy()})
    for col in headline:
        d[col] = panel[col].notna().to_numpy()
    agg = d.groupby("period_key", sort=True).agg(
        rows=("gvkey", "size"), firms=("gvkey", "nunique"),
        **{c: (c, "sum") for c in headline})
    return agg.reset_index()


# ---------------------------------------------------------------------------
# labels and statuses
# ---------------------------------------------------------------------------
def status_distributions(panel: pd.DataFrame) -> pd.DataFrame:
    """Value counts of every status and quality flag."""
    cols = ["calc_nopat_decomposition_status", "calc_roic_decomposition_status",
            "calc_tax_rate_quality_flag", "calc_roic_quality_flag",
            "calc_roic_effect_structure", "calc_roic_dominant_driver",
            "calc_roic_dominant_driver_effect"]
    rows = []
    for col in cols:
        counts = panel[col].value_counts(dropna=False)
        for level, k in counts.items():
            rows.append({"column": col, "level": str(level), "rows": int(k),
                         "share": k / len(panel)})
    return pd.DataFrame(rows)


def label_family_census(panel: pd.DataFrame) -> pd.DataFrame:
    """Every declared level of every label family, reached or not.

    This is the 9/9 and 27/27 evidence. A level with zero rows is reported as
    unreached rather than quietly missing from the output.
    """
    rows = []
    for col, levels in schema.LABEL_LEVELS.items():
        counts = panel[col].value_counts(dropna=False)
        for level in levels:
            k = int(counts.get(level, 0))
            rows.append({"family": col, "n_declared_levels": len(levels),
                         "level": level, "rows": k, "share": k / len(panel),
                         "reached": k > 0})
    return pd.DataFrame(rows)


def explanation_census(panel: pd.DataFrame, catalogue: pd.DataFrame | None) -> pd.DataFrame:
    """Which catalogue sentences occurred, and which never did."""
    if catalogue is None:
        return pd.DataFrame(columns=["explanation", "rows", "reached"])
    counts = panel["calc_roic_explanation"].value_counts(dropna=False)
    out = catalogue.copy()
    out["rows"] = [int(counts.get(s, 0)) for s in out["explanation"]]
    out["reached"] = out["rows"] > 0
    return out.sort_values("rows", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# identities
# ---------------------------------------------------------------------------
def _check(name: str, ok: np.ndarray, tested: np.ndarray, note: str = "") -> dict[str, Any]:
    n = int(tested.sum())
    passed = int((ok & tested).sum())
    return {"check": name, "n_tested": n, "n_passed": passed,
            "n_failed": n - passed, "share_passed": passed / n if n else np.nan,
            "note": note}


def identity_checks(panel: pd.DataFrame, grid: Grid,
                    panel_keys: pd.DataFrame) -> pd.DataFrame:
    """The dictionary's formulas, re-derived from the emitted columns."""
    rows = []
    g = panel

    def close(a, b, rel=1e-9, floor=1e-9):
        a, b = np.asarray(a, "float64"), np.asarray(b, "float64")
        return np.abs(a - b) <= np.maximum(floor, rel * np.maximum(np.abs(a), np.abs(b)))

    tax = g["calc_tax_expense_quarterly"].to_numpy()
    pretax = g["pretax_income"].to_numpy()
    rate = g["calc_raw_tax_rate_quarterly"].to_numpy()
    t = np.isfinite(rate)
    rows.append(_check("calc_raw_tax_rate_quarterly = tax expense / pretax income",
                       close(rate, tax / np.where(pretax == 0, np.nan, pretax)), t))

    ebit, nopat = g["ebit"].to_numpy(), g["calc_nopat_quarterly"].to_numpy()
    t = np.isfinite(nopat)
    rows.append(_check("calc_nopat_quarterly = ebit * (1 - tax rate)",
                       close(nopat, ebit * (1 - rate)), t))

    ic = g["calc_ic_raw"].to_numpy()
    rebuilt = (g["total_current_assets"].to_numpy() - g["total_current_liabilities"].to_numpy()
               + np.nan_to_num(g["net_ppe"].to_numpy())
               + np.nan_to_num(g["goodwill"].to_numpy()))
    t = np.isfinite(ic)
    rows.append(_check("calc_ic_raw = TCA - TCL + PPE + goodwill (PPE/GW missing = 0)",
                       close(ic, rebuilt), t))

    avg = g["calc_average_ic_raw_quarterly"].to_numpy()
    t = np.isfinite(avg)
    rows.append(_check("calc_average_ic_raw_quarterly = (IC(t-1) + IC(t)) / 2",
                       close(avg, (grid.lag(ic) + ic) / 2), t,
                       "defined only across a consecutive pair"))

    for stem, num in (("pretax", ebit), ("posttax", nopat)):
        q = g[f"calc_roic_{stem}_quarterly_ic_raw"].to_numpy()
        t = np.isfinite(q)
        rows.append(_check(f"calc_roic_{stem}_quarterly_ic_raw = numerator / avg IC",
                           close(q, num / np.where(avg == 0, np.nan, avg)), t))
        a = g[f"calc_roic_{stem}_annualized_ic_raw"].to_numpy()
        t = np.isfinite(a)
        rows.append(_check(f"calc_roic_{stem}_annualized_ic_raw = (1+r)^4 - 1",
                           close(a, (1 + q) ** 4 - 1), t, "NaN when 1+r <= 0"))
        rows.append(_check(f"calc_roic_{stem}_annualized is NaN exactly when 1+r <= 0",
                           np.isnan(a) == ~(np.isfinite(q) & (1 + q > 0)),
                           np.ones(len(g), dtype=bool)))

    c_e = g["calc_nopat_ebit_contribution"].to_numpy()
    c_t = g["calc_nopat_tax_contribution"].to_numpy()
    change = g["calc_nopat_change_quarterly"].to_numpy()
    t = np.isfinite(change) & np.isfinite(c_e) & np.isfinite(c_t)
    rows.append(_check("NOPAT bridge: C_EBIT + C_TAX = change (Shapley efficiency)",
                       close(change, c_e + c_t, rel=1e-9, floor=1e-6), t))

    r_e = g["calc_roic_ebit_contribution"].to_numpy()
    r_t = g["calc_roic_tax_contribution"].to_numpy()
    r_i = g["calc_roic_ic_contribution"].to_numpy()
    r_c = g["calc_roic_posttax_change_quarterly"].to_numpy()
    t = np.isfinite(r_c) & np.isfinite(r_e) & np.isfinite(r_t) & np.isfinite(r_i)
    rows.append(_check("ROIC bridge: C_EBIT + C_TAX + C_IC = change (Shapley efficiency)",
                       close(r_c, r_e + r_t + r_i, rel=1e-9, floor=1e-12), t))

    total = g["calc_roic_total_absolute_contribution"].to_numpy()
    t = np.isfinite(total)
    rows.append(_check("calc_roic_total_absolute_contribution = sum of |C_i|",
                       close(total, np.abs(r_e) + np.abs(r_t) + np.abs(r_i)), t))

    shares = np.vstack([g[f"calc_roic_{s}_absolute_share"].to_numpy()
                        for s in ("ebit", "tax", "ic")])
    t = np.isfinite(shares).all(axis=0)
    rows.append(_check("absolute shares sum to 1 where they are defined",
                       close(shares.sum(axis=0), np.ones(len(g)), floor=1e-9), t))

    counts = np.vstack([g[f"calc_roic_{s}_driver_count"].astype("float64").to_numpy()
                        for s in ("positive", "negative", "neutral")])
    t = np.isfinite(counts).all(axis=0)
    rows.append(_check("positive + negative + neutral driver counts = 3",
                       counts.sum(axis=0) == 3, t))

    offset = g["calc_roic_offset_ratio"].to_numpy()
    t = np.isfinite(offset)
    rows.append(_check("calc_roic_offset_ratio is inside [0, 1]",
                       (offset >= 0) & (offset <= 1), t,
                       "triangle inequality |dROIC| <= sum |C_i|"))

    debt = g["calc_debt_value_quarterly"].to_numpy()
    st, lt = g["short_term_debt"].to_numpy(), g["long_term_debt"].to_numpy()
    t = np.isfinite(debt)
    rows.append(_check("calc_debt_value_quarterly = short + long (missing leg = 0)",
                       close(debt, np.nan_to_num(st) + np.nan_to_num(lt)), t))
    rows.append(_check("calc_debt_value_quarterly is NaN exactly when both legs are",
                       np.isnan(debt) == (~np.isfinite(st) & ~np.isfinite(lt)),
                       np.ones(len(g), dtype=bool)))

    eq = g["equity"].to_numpy()
    de = g["calc_debt_to_equity_quarterly"].to_numpy()
    t = np.isfinite(de)
    rows.append(_check("calc_debt_to_equity_quarterly = debt / equity",
                       close(de, debt / np.where(eq == 0, np.nan, eq)), t,
                       "zero or missing equity -> NaN"))

    # the period rule, re-proved on the emitted dates
    end = pd.to_datetime(g["fiscal_period_end_date"])
    shifted = end - pd.DateOffset(months=2)
    rule = (shifted.dt.year.astype(str) + "Q" + shifted.dt.quarter.astype(str)).to_numpy()
    rows.append(_check("period_key = calendar quarter of (fiscal end - 2 months)",
                       rule == panel_keys["period_key"].to_numpy(),
                       np.ones(len(g), dtype=bool)))
    sym = g["symbol"].astype("string")
    expected_key = (panel_keys["period_key"].astype("string") + "_" + sym)
    has_symbol = sym.notna().to_numpy()
    rows.append(_check("KEY = period_key + '_' + symbol",
                       (g["KEY"].astype("string") == expected_key).to_numpy(), has_symbol,
                       "rows with no ticker carry no KEY"))
    rows.append(_check("KEY is unique among rows that have a ticker",
                       np.repeat(bool(g.loc[has_symbol, "KEY"].is_unique), len(g)),
                       has_symbol))
    return pd.DataFrame(rows)


def no_inf_check(panel: pd.DataFrame) -> pd.DataFrame:
    """Every float column must be finite-or-NaN. inf would mean a missed guard."""
    rows = []
    for col in _numeric(panel):
        v = panel[col].to_numpy()
        rows.append({"column": col, "n_inf": int(np.isinf(v).sum()),
                     "n_nan": int(np.isnan(v).sum()),
                     "n_exact_zero": int((v == 0).sum())})
    return pd.DataFrame(rows)


def precision_audit(panel: pd.DataFrame) -> pd.DataFrame:
    """decimal128(18,4) -> float64 is exact below 2^53/1e4 ~ 9.0e11.

    ``within_limit`` is a statement about the **source cast** only, so it is
    evaluated for the fields read straight from the file. Derived ratios are
    listed as ``role = derived`` and exempt: a ROIC on a near-zero capital base
    is legitimately enormous, and clipping it would be an unrequested filter.
    """
    limit = 2.0 ** 53 / 1e4
    source = set(schema.PASSTHROUGH_FIELDS)
    rows = []
    for col in _numeric(panel):
        v = panel[col].to_numpy()
        finite = v[np.isfinite(v)]
        mx = float(np.abs(finite).max()) if finite.size else np.nan
        is_source = col in source
        rows.append({"column": col, "role": "source" if is_source else "derived",
                     "max_abs": mx, "exact_cast_limit": limit,
                     "within_limit": (bool(np.isnan(mx) or mx <= limit)
                                      if is_source else ""),
                     "headroom_x": (limit / mx if is_source and mx and mx > 0
                                    else "")})
    return pd.DataFrame(rows)


def extreme_ratio_census(panel: pd.DataFrame) -> pd.DataFrame:
    """Rows whose ratios are enormous because the capital base is near zero.

    Nothing is clipped or removed - the researcher's rules forbid it - so the
    magnitudes are reported instead, with the capital base that produced them.
    """
    roic = panel["calc_roic_posttax_quarterly_ic_raw"].to_numpy()
    avg_ic = panel["calc_average_ic_raw_quarterly"].to_numpy()
    de = panel["calc_debt_to_equity_quarterly"].to_numpy()
    defined = np.isfinite(roic)
    rows = []
    for threshold in (1.0, 10.0, 100.0, 1e6):
        hit = defined & (np.abs(roic) > threshold)
        rows.append({"measure": f"|quarterly ROIC| > {threshold:g}",
                     "rows": int(hit.sum()),
                     "share_of_defined": hit.sum() / defined.sum() if defined.sum() else np.nan,
                     "median_abs_avg_ic_on_those_rows":
                         float(np.nanmedian(np.abs(avg_ic[hit]))) if hit.any() else np.nan})
    fin_de = np.isfinite(de)
    rows.append({"measure": "|D/E| > 100", "rows": int((fin_de & (np.abs(de) > 100)).sum()),
                 "share_of_defined": (fin_de & (np.abs(de) > 100)).sum() / fin_de.sum()
                 if fin_de.sum() else np.nan,
                 "median_abs_avg_ic_on_those_rows": np.nan})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# sign censuses the researcher asked for explicitly
# ---------------------------------------------------------------------------
def tax_sign_census(panel: pd.DataFrame) -> pd.DataFrame:
    """Negative tax, and *why* the rate is negative.

    A tax benefit on a profit and a positive tax on a pretax loss both land in
    NEGATIVE_TAX_RATE but are different phenomena, so they are counted apart.
    """
    tax = panel["calc_tax_expense_quarterly"].to_numpy()
    pretax = panel["pretax_income"].to_numpy()
    rate = panel["calc_raw_tax_rate_quarterly"].to_numpy()
    present, defined = np.isfinite(tax), np.isfinite(rate)
    n = len(panel)
    rows = [
        ("tax expense present", int(present.sum()), n),
        ("tax expense < 0 (benefit)", int((tax < 0).sum()), int(present.sum())),
        ("tax expense == 0", int((tax == 0).sum()), int(present.sum())),
        ("tax expense > 0", int((tax > 0).sum()), int(present.sum())),
        ("tax rate defined", int(defined.sum()), n),
        ("tax rate < 0", int((rate < 0).sum()), int(defined.sum())),
        ("tax rate in [0, 1]", int(((rate >= 0) & (rate <= 1)).sum()), int(defined.sum())),
        ("tax rate > 1 (above 100%)", int((rate > 1).sum()), int(defined.sum())),
        ("negative rate: benefit on a profit (tax<0, pretax>0)",
         int(((tax < 0) & (pretax > 0)).sum()), int((rate < 0).sum())),
        ("negative rate: tax on a pretax loss (tax>0, pretax<0)",
         int(((tax > 0) & (pretax < 0)).sum()), int((rate < 0).sum())),
    ]
    out = pd.DataFrame(rows, columns=["measure", "rows", "denominator"])
    out["share_of_denominator"] = out["rows"] / out["denominator"].replace(0, np.nan)
    return out


def equity_sign_census(panel: pd.DataFrame) -> pd.DataFrame:
    """Zero and negative equity, and what they do to D/E."""
    eq = panel["equity"].to_numpy()
    de = panel["calc_debt_to_equity_quarterly"].to_numpy()
    ic = panel["calc_ic_raw"].to_numpy()
    n = len(panel)
    rows = [
        ("equity present", int(np.isfinite(eq).sum()), n),
        ("equity == 0 (D/E is NaN by the denominator rule)", int((eq == 0).sum()), n),
        ("equity < 0 (D/E negative and mechanical)", int((eq < 0).sum()), n),
        ("D/E present", int(np.isfinite(de).sum()), n),
        ("D/E negative", int((de < 0).sum()), int(np.isfinite(de).sum())),
        ("calc_ic_raw < 0 (ratio mechanical, flagged)", int((ic < 0).sum()),
         int(np.isfinite(ic).sum())),
    ]
    out = pd.DataFrame(rows, columns=["measure", "rows", "denominator"])
    out["share_of_denominator"] = out["rows"] / out["denominator"].replace(0, np.nan)
    return out


def consecutiveness_summary(grid: Grid, panel: pd.DataFrame,
                            panel_keys: pd.DataFrame) -> pd.DataFrame:
    """Quarter-step and day-gap distribution behind the consecutiveness rule."""
    end = pd.to_datetime(panel["fiscal_period_end_date"])
    step = np.diff(panel_keys["quarter_index"].to_numpy(), prepend=np.nan)
    days = end.diff().dt.days.to_numpy(dtype="float64")
    same_firm = np.zeros(len(panel), dtype=bool)
    same_firm[1:] = grid.firm_codes[1:] == grid.firm_codes[:-1]
    within = same_firm & np.isfinite(days)
    rows = [
        {"measure": "rows", "value": float(len(panel))},
        {"measure": "rows linked to the previous quarter", "value": float(grid.prev_ok.sum())},
        {"measure": "share linked", "value": float(grid.prev_ok.mean())},
        {"measure": "within-firm pairs", "value": float(within.sum())},
        {"measure": "pairs with quarter step == 1", "value": float((within & (step == 1)).sum())},
        {"measure": "pairs with day gap in [60, 130]",
         "value": float((within & (days >= 60) & (days <= 130)).sum())},
        {"measure": "day gap p01", "value": float(np.nanpercentile(days[within], 1))},
        {"measure": "day gap median", "value": float(np.nanmedian(days[within]))},
        {"measure": "day gap p99", "value": float(np.nanpercentile(days[within], 99))},
        {"measure": "rows with run_len >= 2", "value": float((grid.run_len >= 2).sum())},
        {"measure": "rows with run_len >= 4", "value": float((grid.run_len >= 4).sum())},
    ]
    return pd.DataFrame(rows)


def duplicates_summary(report: dict[str, Any]) -> pd.DataFrame:
    """The merge, measured again every run so a new pull cannot change it quietly."""
    d = report["dedup"]
    return pd.DataFrame([
        {"measure": "rows in source", "value": d["rows_in"]},
        {"measure": "removed in step 1 (same gvkey and date, values merged)",
         "value": d["removed_step1_same_date"]},
        {"measure": "rows after step 1", "value": d["rows_after_step1"]},
        {"measure": "removed in step 2 (same period, different date, first row wins)",
         "value": d["removed_step2_same_period"]},
        {"measure": "rows in panel", "value": d["rows_out"]},
    ])


# ---------------------------------------------------------------------------
# external cross-check
# ---------------------------------------------------------------------------
NUMERIC_CROSSCHECK = (
    "pretax_income", "ebit", "total_current_assets", "total_current_liabilities",
    "goodwill", "net_ppe", "short_term_debt", "long_term_debt", "market_cap",
    "equity", "calc_tax_expense_quarterly", "calc_raw_tax_rate_quarterly",
    "calc_nopat_quarterly", "calc_ic_raw", "calc_average_ic_raw_quarterly",
    "calc_roic_pretax_quarterly_ic_raw", "calc_roic_posttax_quarterly_ic_raw",
    "calc_debt_value_quarterly", "calc_debt_to_equity_quarterly",
    "calc_nopat_change_quarterly", "calc_roic_posttax_change_quarterly",
    "calc_roic_ebit_contribution", "calc_roic_tax_contribution",
    "calc_roic_ic_contribution",
)
LABEL_CROSSCHECK = tuple(schema.LABEL_LEVELS)


def crosscheck_gurufocus(panel: pd.DataFrame, gurufocus: pd.DataFrame | None,
                         panel_keys: pd.DataFrame, cfg: dict[str, Any]
                         ) -> dict[str, pd.DataFrame]:
    """Compare the panel against GuruFocus on overlapping (symbol, period_key).

    GuruFocus publishes its own ``calc_*`` columns, so the labels can be
    compared as confusion matrices against a panel computed from a different
    source - a far stronger check than any synthetic fixture.

    Definitional gaps are pre-registered in the config and reported as notes,
    not failures. An *unexpected* breach is raised by the caller.
    """
    empty = {"numeric": pd.DataFrame(), "labels": pd.DataFrame(),
             "overlap": pd.DataFrame([{"measure": "gurufocus file", "value": "absent"}])}
    if gurufocus is None or "symbol" not in gurufocus.columns:
        return empty

    mine = panel.copy()
    mine["period_key"] = panel_keys["period_key"].to_numpy()
    mine["symbol_plain"] = mine["symbol"].astype("string").str.split(".").str[0]
    # a stripped symbol is not unique on our side (BF.A and BF.B both give BF);
    # ambiguous rows are excluded so a many-to-one join cannot inflate agreement
    dup = mine.duplicated(["symbol_plain", "period_key"], keep=False)
    left = mine[~dup]
    gf = gurufocus.copy()
    gf["symbol"] = gf["symbol"].astype("string")
    gf = gf[~gf.duplicated(["symbol", "period_key"], keep=False)]
    joined = left.merge(gf, left_on=["symbol_plain", "period_key"],
                        right_on=["symbol", "period_key"],
                        suffixes=("", "_gf"), how="inner")
    overlap = pd.DataFrame([
        {"measure": "panel rows", "value": len(panel)},
        {"measure": "panel rows with an ambiguous stripped symbol", "value": int(dup.sum())},
        {"measure": "gurufocus rows", "value": len(gurufocus)},
        {"measure": "matched rows", "value": len(joined)},
    ])
    if joined.empty:
        return {**empty, "overlap": overlap}

    expected = cfg["validation"].get("expected_gaps") or {}
    lo, hi = cfg["validation"]["median_ratio_range"]
    min_corr = float(cfg["validation"]["min_correlation"])

    rows = []
    for col in NUMERIC_CROSSCHECK:
        gf_col = f"{col}_gf" if f"{col}_gf" in joined.columns else col
        if gf_col not in joined.columns or gf_col == col:
            continue
        a = pd.to_numeric(joined[col], errors="coerce").to_numpy("float64")
        b = pd.to_numeric(joined[gf_col], errors="coerce").to_numpy("float64")
        both = np.isfinite(a) & np.isfinite(b)
        rec: dict[str, Any] = {"column": col, "n_overlap": int(both.sum()),
                               "nan_pattern_agreement": float(
                                   (np.isfinite(a) == np.isfinite(b)).mean())}
        if both.sum() >= 30:
            ratio = a[both] / np.where(b[both] == 0, np.nan, b[both])
            dev = np.abs(a[both] - b[both]) / np.maximum(1e-9, np.abs(b[both]))
            rec.update({
                "correlation": float(np.corrcoef(a[both], b[both])[0, 1]),
                "median_ratio": float(np.nanmedian(ratio)),
                "median_rel_deviation": float(np.nanmedian(dev)),
                "share_within_1pct": float(np.nanmean(dev <= 0.01)),
                "share_within_5pct": float(np.nanmean(dev <= 0.05)),
                "max_abs_deviation": float(np.abs(a[both] - b[both]).max()),
            })
            corr_ok = rec["correlation"] >= min_corr
            ratio_ok = lo <= rec["median_ratio"] <= hi
            rec["within_thresholds"] = bool(corr_ok and ratio_ok)
        else:
            rec["within_thresholds"] = True
        rec["expected_gap"] = col in expected
        rec["note"] = expected.get(col, "")
        rows.append(rec)
    numeric = pd.DataFrame(rows)

    # a label can only agree where the inputs behind it agree: this is the
    # ceiling against which the label agreement below should be read
    ceiling = _input_agreement(joined)

    label_rows = []
    for col in LABEL_CROSSCHECK:
        gf_col = f"{col}_gf" if f"{col}_gf" in joined.columns else None
        if gf_col is None:
            continue
        a = joined[col].astype("string")
        b = joined[gf_col].astype("string")
        both = a.notna() & b.notna()
        if not both.any():
            continue
        agree = (a[both] == b[both])
        confusion = (pd.crosstab(a[both], b[both]).stack()
                     .rename("rows").reset_index())
        confusion.columns = ["mine", "gurufocus", "rows"]
        confusion = confusion[(confusion.rows > 0)
                              & (confusion["mine"] != confusion["gurufocus"])]
        label_rows.append({"column": col, "n_overlap": int(both.sum()),
                           "exact_agreement": float(agree.mean()),
                           "input_agreement_ceiling": ceiling["all_inputs"],
                           "top_disagreements": "; ".join(
                               f"{r.mine}->{r.gurufocus}:{r.rows}" for r in
                               confusion.nlargest(5, "rows").itertuples(index=False)),
                           "expected_gap": col in expected,
                           "note": expected.get(col, "")})
    return {"numeric": numeric, "labels": pd.DataFrame(label_rows),
            "overlap": overlap, "input_agreement": ceiling["table"]}


def _input_agreement(joined: pd.DataFrame) -> dict[str, Any]:
    """How often the two vendors agree on the inputs a label is built from.

    Labels are coarse (three buckets), so they agree far more often than the
    numbers do - but the numbers are the ceiling on how much agreement is even
    available, and without it a 65% label agreement cannot be read.
    """
    fields = ("ebit", "pretax_income", "calc_tax_expense_quarterly",
              "total_current_assets", "total_current_liabilities", "net_ppe", "goodwill")
    rows = []
    masks = []
    for col in fields:
        gf_col = f"{col}_gf"
        if gf_col not in joined.columns:
            continue
        a = pd.to_numeric(joined[col], errors="coerce").to_numpy("float64")
        b = pd.to_numeric(joined[gf_col], errors="coerce").to_numpy("float64")
        both = np.isfinite(a) & np.isfinite(b)
        close = both & (np.abs(a - b) <= 0.01 * np.maximum(1e-9, np.abs(b)))
        masks.append(close | ~both)
        rows.append({"input": col, "n_comparable": int(both.sum()),
                     "share_within_1pct": float(close.sum() / both.sum()) if both.sum() else np.nan,
                     "n_only_mine": int((np.isfinite(a) & ~np.isfinite(b)).sum()),
                     "n_only_gurufocus": int((~np.isfinite(a) & np.isfinite(b)).sum())})
    all_inputs = float(np.all(masks, axis=0).mean()) if masks else np.nan
    rows.append({"input": "ALL of the above within 1% (the label ceiling)",
                 "n_comparable": len(joined), "share_within_1pct": all_inputs,
                 "n_only_mine": "", "n_only_gurufocus": ""})
    return {"table": pd.DataFrame(rows), "all_inputs": all_inputs}


def unexpected_breaches(crosscheck: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rows that failed a threshold without a pre-registered explanation."""
    numeric = crosscheck.get("numeric")
    if numeric is None or numeric.empty or "within_thresholds" not in numeric.columns:
        return pd.DataFrame()
    return numeric[~numeric["within_thresholds"] & ~numeric["expected_gap"]]

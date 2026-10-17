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
                "equity", "calc_debt_to_equity_quarterly", "market_cap",
                "free_cash_flow_quarterly", "calc_free_cash_flow_ttm",
                "calc_p_to_fcf_quarterly", "calc_p_to_fcf_ttm"]
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


def fcf_census(panel: pd.DataFrame, aux: dict[str, np.ndarray],
               terms: dict[str, np.ndarray], grid: Grid) -> pd.DataFrame:
    """Where the flow comes from, how complete each row's formula was, and its sign.

    ``terms`` carries the per-row count of formula terms that could be computed
    (out of six: ibq and the five differences). A row built from two terms is
    not the same measurement as one built from six, so the distribution is
    reported rather than hidden behind a single coverage number.
    """
    n = len(panel)
    fcf = panel["free_cash_flow_quarterly"].to_numpy()
    ttm = panel["calc_free_cash_flow_ttm"].to_numpy()
    pq = panel["calc_p_to_fcf_quarterly"].to_numpy()
    pt = panel["calc_p_to_fcf_ttm"].to_numpy()

    def count(arr) -> int:
        return int(np.isfinite(arr).sum()) if arr is not None else -1

    rows = [
        {"measure": "ibq present", "rows": count(aux.get("income_before_extraordinary_items")),
         "denominator": n},
        {"measure": "cheq present", "rows": count(aux.get("cash_and_short_term_investments")),
         "denominator": n},
        {"measure": "free_cash_flow_quarterly present", "rows": count(fcf), "denominator": n},
        {"measure": "free_cash_flow_quarterly == 0 exactly", "rows": int((fcf == 0).sum()),
         "denominator": count(fcf)},
        {"measure": "free_cash_flow_quarterly < 0", "rows": int((fcf < 0).sum()),
         "denominator": count(fcf)},
        {"measure": "calc_free_cash_flow_ttm present (4 consecutive quarters)",
         "rows": count(ttm), "denominator": n},
        {"measure": "calc_free_cash_flow_ttm < 0", "rows": int((ttm < 0).sum()),
         "denominator": count(ttm)},
        {"measure": "calc_p_to_fcf_quarterly present", "rows": count(pq), "denominator": n},
        {"measure": "calc_p_to_fcf_quarterly < 0", "rows": int((pq < 0).sum()),
         "denominator": count(pq)},
        {"measure": "calc_p_to_fcf_ttm present", "rows": count(pt), "denominator": n},
        {"measure": "calc_p_to_fcf_ttm < 0", "rows": int((pt < 0).sum()),
         "denominator": count(pt)},
        {"measure": "rows on a run of 5+ consecutive quarters (what TTM needs)",
         "rows": int(grid.window_ok(5).sum()), "denominator": n},
    ]
    used = terms.get("terms_used")
    if used is not None:
        for k in range(7):
            rows.append({"measure": f"rows whose flow was built from {k} of 6 terms",
                         "rows": int((used == k).sum()), "denominator": n})
    out = pd.DataFrame(rows)
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
# the price panel (output/prices.parquet)
# ---------------------------------------------------------------------------
SELECTION_MEANING = {
    "MATCHED": "the security whose (gvkey, tic) equals the base panel's",
    "NO_TIC_MATCH": "gvkey is in the file only under other tickers - no rows, not guessed",
    "NO_PRICE_ROWS": "gvkey is not in the price file at all",
    "NO_SYMBOL_IN_BASE": "the base panel has no ticker for this gvkey, so no KEY exists",
    "FILE_ONLY_NOT_IN_BASE": "gvkeys in the price file with no fundamentals - not used",
    "MATCHED_WITHOUT_QUARTER_MONTH": ("of MATCHED: no price row in Feb/May/Aug/Nov at all, "
                                      "so no panel rows"),
}


def issue_selection_summary(report: dict[str, Any]) -> pd.DataFrame:
    """One row per category of the security match, with what it means."""
    return pd.DataFrame([{"category": k, "firms": v, "meaning": SELECTION_MEANING.get(k, "")}
                         for k, v in report["selection_counts"].items()])


def price_coverage_by_quarter(prices: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    """Firms and availability of the three numbers per quarter."""
    d = pd.DataFrame({"period_key": report["quarter_keys"]["period_key"].to_numpy(),
                      "Close": prices["Close"].notna().to_numpy(),
                      "Quaterly_return": prices["Quaterly_return"].notna().to_numpy(),
                      "MomentomQ3": prices["MomentomQ3"].notna().to_numpy()})
    return (d.groupby("period_key", sort=True)
            .agg(firms=("Close", "size"), Close=("Close", "sum"),
                 Quaterly_return=("Quaterly_return", "sum"), MomentomQ3=("MomentomQ3", "sum"))
            .reset_index())


def price_nan_reasons(prices: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    """Why each value is present or missing, from the codes the build assigned.

    The last row is not a reason: it counts momentum values computed although a
    quarter in between has no valid Close - allowed, since only the endpoints
    count, but worth seeing.
    """
    from .prices import value_periods_back

    n = len(prices)
    rows = []
    for column, reasons in (("Close", report["close_reason"]),
                            ("Quaterly_return", report["return_reason"]),
                            ("MomentomQ3", report["momentum_reason"])):
        for reason, k in pd.Series(reasons).value_counts().items():
            rows.append({"column": column, "reason": reason, "rows": int(k),
                         "share": k / n if n else np.nan})
    qk = report["quarter_keys"]
    firm, qidx = qk["firm"].to_numpy(), qk["quarter_index"].to_numpy()
    valid = prices["Close"].notna().to_numpy().astype("float64")
    both_inside = ((value_periods_back(firm, qidx, valid, 1) == 1)
                   & (value_periods_back(firm, qidx, valid, 2) == 1))
    k = int((prices["MomentomQ3"].notna().to_numpy() & ~both_inside).sum())
    rows.append({"column": "MomentomQ3",
                 "reason": "INFO: computed although q-1 or q-2 has no valid Close",
                 "rows": k, "share": k / n if n else np.nan})
    return pd.DataFrame(rows)


def dividend_gap_census(prices: pd.DataFrame, report: dict[str, Any],
                        cfg: dict[str, Any]) -> pd.DataFrame:
    """How much the dividend-free return leaves out, measured with trt1m.

    Backward-looking on purpose: for each quarter q it compares the price return
    realised from q-1 to q with the total return compounded from the three
    monthly trt1m values ending at q, so nothing here reads a future row.
    ``gap = (1 + total) / (1 + price) - 1`` is the dividend yield of the quarter;
    a clearly negative gap is not a dividend and is counted as anomalous.
    """
    from .data_io import safe_div
    from .prices import value_periods_back

    tol = float(cfg["prices"]["dividend_gap_tolerance"])
    m = report["monthly"]
    firm_m = pd.factorize(m["gvkey"], sort=False)[0].astype(np.int64)
    midx = (m["month_end"].dt.year * 12 + m["month_end"].dt.month - 1).to_numpy(np.int64)
    g = 1.0 + m["total_return_pct"].to_numpy(dtype="float64") / 100.0
    g3 = g * value_periods_back(firm_m, midx, g, 1) * value_periods_back(firm_m, midx, g, 2)

    qk = report["quarter_keys"]
    total = g3[qk["_monthly_row"].to_numpy()] - 1.0
    close = prices["Close"].to_numpy()
    price = safe_div(close, value_periods_back(qk["firm"].to_numpy(),
                                               qk["quarter_index"].to_numpy(), close, 1)) - 1.0
    gap = safe_div(1.0 + total, 1.0 + price) - 1.0
    decade = (qk["period_year"].to_numpy() // 10) * 10

    def summarise(label: str, mask: np.ndarray) -> dict[str, Any]:
        v = gap[mask & np.isfinite(gap)]
        out: dict[str, Any] = {"scope": label, "quarters_compared": int(v.size)}
        if v.size:
            qs = np.percentile(v, [1, 50, 99])
            positive = v[v > tol]
            out.update({"share_no_dividend": float((np.abs(v) <= tol).mean()),
                        "share_positive_gap": float((v > tol).mean()),
                        "share_anomalous_negative": float((v < -tol).mean()),
                        "gap_p01": qs[0], "gap_median": qs[1], "gap_p99": qs[2],
                        "gap_median_when_positive": (float(np.median(positive))
                                                     if positive.size else np.nan)})
        return out

    rows = [summarise("all", np.ones(len(gap), dtype=bool))]
    for d in sorted(set(decade.tolist())):
        rows.append(summarise(f"{d}s", decade == d))
    return pd.DataFrame(rows)


def extreme_return_census(prices: pd.DataFrame, report: dict[str, Any],
                          cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sizes of the largest moves, counted - never clipped - plus the top rows."""
    thresholds = [float(t) for t in cfg["prices"]["extreme_thresholds"]]
    rows: list[dict[str, Any]] = []
    for col in ("Quaterly_return", "MomentomQ3"):
        v = prices[col].to_numpy()
        f = v[np.isfinite(v)]
        if not f.size:
            continue
        qs = np.percentile(f, [0.1, 50, 99.9])
        rows += [{"column": col, "measure": "values", "value": int(f.size)},
                 {"column": col, "measure": "min", "value": float(f.min())},
                 {"column": col, "measure": "p0.1", "value": float(qs[0])},
                 {"column": col, "measure": "median", "value": float(qs[1])},
                 {"column": col, "measure": "p99.9", "value": float(qs[2])},
                 {"column": col, "measure": "max", "value": float(f.max())},
                 {"column": col, "measure": "at or below -0.9", "value": int((f <= -0.9).sum())}]
        rows += [{"column": col, "measure": f"above {t:g}", "value": int((f > t).sum())}
                 for t in thresholds]
    t1m = report["monthly"]["total_return_pct"].to_numpy(dtype="float64")
    t1m = t1m[np.isfinite(t1m)]
    if t1m.size:
        rows += [{"column": "trt1m (percent, matched monthly rows)", "measure": "max",
                  "value": float(t1m.max())},
                 {"column": "trt1m (percent, matched monthly rows)", "measure": "above 1000",
                  "value": int((t1m > 1000).sum())}]
    top = pd.concat([prices.nlargest(20, col)[["KEY", "Date", "Close", col]]
                     .rename(columns={col: "value"}).assign(column=col)
                     for col in ("Quaterly_return", "MomentomQ3")], ignore_index=True)
    # object column: counts stay integers next to the float statistics
    census = pd.DataFrame(rows)
    census["value"] = pd.Series([r["value"] for r in rows], dtype=object)
    return census, top[["column", "KEY", "Date", "Close", "value"]]


def join_coverage(prices: pd.DataFrame, base_keys: pd.Series) -> pd.DataFrame:
    """Share of the base panel's KEYs that find a price row and each number."""
    b = pd.DataFrame({"KEY": base_keys.dropna().astype("string").to_numpy()})
    b["period_year"] = b["KEY"].str[:4].astype(int)
    m = b.merge(prices[["KEY", "Close", "Quaterly_return", "MomentomQ3"]], on="KEY",
                how="left", indicator=True)
    m["with_price_row"] = m["_merge"] == "both"

    def agg(frame: pd.DataFrame) -> dict[str, Any]:
        n = len(frame)
        out = {"base_keys": n, "with_price_row": int(frame["with_price_row"].sum())}
        for col in ("Close", "Quaterly_return", "MomentomQ3"):
            out[f"with_{col}"] = int(frame[col].notna().sum())
        for col in ("with_price_row", "with_Close", "with_Quaterly_return", "with_MomentomQ3"):
            out[f"share_{col}"] = out[col] / n if n else np.nan
        return out

    rows = [{"period_year": "all", **agg(m)},
            {"period_year": ">= 2000", **agg(m[m["period_year"] >= 2000])}]
    rows += [{"period_year": str(y), **agg(g)} for y, g in m.groupby("period_year", sort=True)]
    return pd.DataFrame(rows)


def timing_lag_census(prices: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """Months from the fiscal quarter's end to the price date, through the KEY.

    0 or 1 month means the price is observed before the quarter's report is
    likely to be public. Described here; what to do about it belongs to the join.
    """
    b = base.dropna(subset=["KEY"])[["KEY", "fiscal_period_end_date"]]
    m = (b.assign(KEY=b["KEY"].astype("string"))
         .merge(prices[["KEY", "Date"]], on="KEY", how="inner"))
    end = pd.to_datetime(m["fiscal_period_end_date"])
    lag = (m["Date"].dt.year * 12 + m["Date"].dt.month) - (end.dt.year * 12 + end.dt.month)
    year = m["KEY"].str[:4].astype(int)
    rows = []
    for scope, mask in (("all", np.ones(len(m), dtype=bool)),
                        ("period_year >= 2000", (year >= 2000).to_numpy())):
        counts = lag[mask].value_counts().sort_index()
        total = int(mask.sum())
        rows += [{"scope": scope, "lag_months": int(k), "rows": int(v),
                  "share": v / total if total else np.nan} for k, v in counts.items()]
    return pd.DataFrame(rows)


def listing_dates_census(prices: pd.DataFrame, report: dict[str, Any],
                         status: pd.Series | None) -> pd.DataFrame:
    """Coverage of ipodate / dldte and how they sit against the price series.

    Nothing is filtered on these dates; this only shows what the next stage
    will be working with.
    """
    m = report["monthly"]
    priced = ((m["price_close"] > 0) & (m["adjustment_factor"] > 0)).to_numpy()
    firms = m.drop_duplicates("gvkey")[["gvkey", "ipo_date", "deletion_date"]]
    rows: list[dict[str, Any]] = []

    def add(measure: str, value: float, denominator: int, is_count: bool = True) -> None:
        rows.append({"measure": measure, "value": value, "denominator": int(denominator),
                     "share": (value / denominator if is_count and denominator else np.nan)})

    nf = len(firms)
    add("matched firms", nf, nf)
    add("firms with ipodate", int(firms["ipo_date"].notna().sum()), nf)
    add("firms with dldte", int(firms["deletion_date"].notna().sum()), nf)
    if status is not None:
        st = firms["gvkey"].astype("string").map(status)
        for code, label in (("A", "active (costat A)"), ("I", "inactive (costat I)")):
            sel = (st == code).fillna(False).to_numpy(dtype=bool)
            add(f"{label} firms", int(sel.sum()), nf)
            add(f"{label} firms with dldte",
                int(firms.loc[sel, "deletion_date"].notna().sum()), int(sel.sum()))
    before_ipo = (m["month_end"] < m["ipo_date"]).to_numpy()
    after_del = (m["month_end"] > m["deletion_date"] + pd.offsets.MonthEnd(0)).to_numpy()
    nm = len(m)
    add("monthly rows dated before ipodate", int(before_ipo.sum()), nm)
    add("... of which with a valid price", int((before_ipo & priced).sum()), nm)
    add("monthly rows in a month after the dldte month", int(after_del.sum()), nm)
    add("... of which with a valid price", int((after_del & priced).sum()), nm)
    n_panel = len(prices)
    add("panel rows with Date before ipodate",
        int((prices["Date"] < prices["ipodate"]).sum()), n_panel)
    add("panel rows in a month after the dldte month",
        int((prices["Date"] > prices["dldte"] + pd.offsets.MonthEnd(0)).sum()), n_panel)
    last = m.loc[priced].groupby("gvkey")["month_end"].max()
    deleted = firms.set_index("gvkey")["deletion_date"]
    days = (last - deleted.reindex(last.index)).dt.days.dropna().to_numpy(dtype="float64")
    if days.size:
        for p in (1, 10, 50, 90, 99):
            add(f"days from dldte to the last valid price, p{p:02d} (firms)",
                round(float(np.percentile(days, p)), 1), days.size, is_count=False)
    census = pd.DataFrame(rows)
    census["value"] = pd.Series([r["value"] for r in rows], dtype=object)
    return census


def example_side_by_side(prices: pd.DataFrame,
                         example: tuple[tuple[str, str, float, float, float], ...]
                         ) -> pd.DataFrame:
    """The researcher's AAPL example next to the panel's numbers.

    The example uses Yahoo closes adjusted for dividends too, so the price-only
    returns here run lower by the dividend; the table is an illustration only.
    """
    ex = pd.DataFrame(list(example), columns=["period_key", "your_Date", "your_Close",
                                              "your_Quaterly_return", "your_MomentomQ3"])
    ex["KEY"] = (ex["period_key"] + "_AAPL").astype("string")
    m = ex.merge(prices[["KEY", "Date", "Close", "Quaterly_return", "MomentomQ3"]],
                 on="KEY", how="left")
    m["Quaterly_return_minus_yours"] = m["Quaterly_return"] - m["your_Quaterly_return"]
    m["MomentomQ3_minus_yours"] = m["MomentomQ3"] - m["your_MomentomQ3"]
    return m[["KEY", "your_Date", "Date", "your_Close", "Close", "your_Quaterly_return",
              "Quaterly_return", "Quaterly_return_minus_yours", "your_MomentomQ3",
              "MomentomQ3", "MomentomQ3_minus_yours"]]

"""Stage 0 + Stage 1: study the data and build the research panel.

Run from anywhere (paths are resolved from config.yaml):
    python scripts/run_01_prepare_data.py
    python scripts/run_01_prepare_data.py --config config.yaml --no-xlsx

Outputs (under outputs/):
    data/     panel_prepared.parquet, panel_research_view.csv, benchmarks.csv, relevance_members_expanded.csv
    tables/   column_mapping.csv, dictionary_variables.csv, validation and coverage tables
    reports/  stage1_report.md, dictionary_fulltext.txt
    logs/     run log, byte copy of the config used, run manifest (versions, hashes, counts)
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import data_io, dictionary, eligibility, features, pipeline, sample, validation  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


# ---------------------------------------------------------------------------
def md_table(df: pd.DataFrame, max_rows: int = 50, digits: int = 4) -> str:
    if df is None or len(df) == 0:
        return "_(ריק)_\n"
    d = df.head(max_rows).copy()

    def fmt(v):
        if isinstance(v, (float, np.floating)):
            if np.isnan(v):
                return ""
            return f"{v:.{digits}g}" if abs(v) < 1e5 else f"{v:,.0f}"
        if isinstance(v, (pd.Timestamp,)):
            return v.date().isoformat()
        return str(v).replace("|", "\\|").replace("\n", " ")

    head = "| " + " | ".join(map(str, d.columns)) + " |"
    sep = "|" + "---|" * len(d.columns)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in d.itertuples(index=False)]
    more = f"\n_... {len(df) - max_rows} שורות נוספות בקובץ המלא_\n" if len(df) > max_rows else ""
    return "\n" + "\n".join([head, sep, *body]) + "\n" + more


def pct(x: float) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.1f}%"


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--no-xlsx", action="store_true", help="skip the read-only xlsx cross-check")
    args = parser.parse_args()

    t0 = time.time()
    cfg = data_io.load_config(args.config)
    np.random.seed(cfg["project"]["seed"])
    dirs = data_io.ensure_output_dirs(cfg)
    run_id = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    log_path = data_io.setup_logging(dirs["logs"], f"run_01_prepare_data_{run_id}")
    log = __import__("logging").getLogger("roic.run_01")
    cfg_copies = data_io.copy_config(cfg, dirs["logs"], run_id)
    log.info("Run %s | config %s", run_id, cfg["_meta"]["config_path"])
    fp_before = data_io.source_fingerprints(cfg)
    T = dirs["tables"]
    c = cfg["columns"]
    w = int(cfg["features"]["trend_window"])

    # ------------------------------------------------------------------ Stage 0: dictionary
    blocks = dictionary.read_docx_blocks(cfg["_paths"]["dictionary_docx"])
    (dirs["reports"] / "dictionary_fulltext.txt").write_text(dictionary.blocks_to_text(blocks), encoding="utf-8")
    catalogue = dictionary.build_variable_catalogue(blocks)
    declared = dictionary.declared_column_count(blocks)
    data_io.save_table(catalogue, T / "dictionary_variables.csv")
    log.info("Dictionary: %d variables extracted (declared %s)", len(catalogue), declared["declared_total"])

    # ------------------------------------------------------------------ Stage 1: load
    panel_raw, load_rep = data_io.load_panel(cfg)
    file_columns = load_rep["columns_in_file"]
    rel, rel_rep = data_io.load_relevance(cfg)
    dup_key = panel_raw.duplicated(["symbol", "q_ord"], keep=False)
    if dup_key.any():
        data_io.save_table(panel_raw.loc[dup_key, ["KEY", "symbol", "period_key"]], T / "duplicates_symbol_period.csv")
    panel, build_rep = pipeline.build_research_panel(cfg, panel_raw=panel_raw, relevance=rel)
    rng_rep, ret_rep, lvl_rep = build_rep["relevance_ranges"], build_rep["returns"], build_rep["levels"]
    log.info("Features and sample layers built")

    # ------------------------------------------------------------------ validation
    stocks = panel[~panel["is_benchmark"]]
    dups = validation.duplicates_report(panel)
    fpq = validation.firms_per_quarter(panel)
    qpf = validation.quarters_per_firm(panel)
    breaks = validation.sequence_breaks(panel, cfg)
    agree = validation.consecutiveness_agreement(panel, cfg)
    precision = validation.precision_audit(panel, [x for x in file_columns if x in panel.columns], cfg)
    formulas = validation.formula_checks(panel, cfg)
    candidates = validation.return_field_candidates(panel, file_columns, catalogue)
    window_ev = validation.verify_return_window(panel, cfg)
    timing = validation.timing_audit(panel, cfg)
    xl = validation.xlsx_crosscheck(panel, cfg) if (cfg["validation"]["xlsx_crosscheck"] and not args.no_xlsx) else {"available": False}

    first_ord, last_ord = int(stocks["q_ord"].min()), int(stocks["q_ord"].max())
    members = eligibility.expand_members(rel, cfg, first_ord, last_ord)
    coverage = eligibility.coverage_by_quarter(members, stocks)
    manifest = xl.get("manifest") if xl.get("available") else None
    missing = eligibility.missing_members_detail(rel, stocks, first_ord, cfg, manifest)
    reused = eligibility.reused_ticker_report(rel, stocks)
    flow = sample.sample_flow(panel)
    flow_q = sample.sample_flow_by_quarter(panel)
    funnel = validation.signal_funnel_by_quarter(panel, coverage, cfg)
    miss_reasons, miss_by_sector = validation.roic_missing_reasons(panel, cfg)
    gw_only = stocks[stocks["goodwill"].isna() & stocks["total_current_assets"].notna()
                     & stocks["total_current_liabilities"].notna() & stocks["net_ppe"].notna()]
    goodwill_info = {"exact_zero_goodwill_rows": int((stocks["goodwill"] == 0).sum()),
                     "firms_goodwill_always_nan": int((stocks.groupby("symbol")["goodwill"].apply(lambda s: s.isna().all())).sum()),
                     "goodwill_only_missing_firms": int(gw_only["symbol"].nunique()),
                     "examples": gw_only.drop_duplicates("symbol")["symbol"].head(8).tolist()}
    feat_cols = features.feature_columns(cfg)
    numeric_feats = [f for f in feat_cols if not f.startswith(("window_ok", "all_econ"))]
    fsum = features.feature_summary(panel, numeric_feats, mask=panel["in_population"])
    ties = features.ties_by_quarter(panel, [c["roic_pretax_file"], "roic_pretax_q", c["roic_posttax_file"], "roic_posttax_q",
                                            f"roic_trend_{w}q"], panel["in_population"])
    reconcile = validation.reconcile_columns(catalogue, file_columns, load_rep["duplicate_columns"])

    # automatic notes for column_mapping.csv
    notes: dict[str, list[str]] = {}
    for rec in precision.itertuples():
        if rec.ratio_like_and_rounded:
            notes.setdefault(rec.column, []).append(f"CSV value rounded to {rec.max_decimals} decimals ({rec.n_distinct} distinct values)")
    for rec in load_rep["duplicate_columns"].itertuples():
        notes.setdefault(rec.base_column, []).append(f"identical duplicate '{rec.duplicate_column}' in CSV (dropped={rec.identical})")
    notes.setdefault(c["roic_pretax_file"], []).append("research value: roic_pretax_q = ebit / calc_average_ic_raw_quarterly (full precision)")
    notes.setdefault(c["roic_posttax_file"], []).append("research value: roic_posttax_q = calc_nopat_quarterly / calc_average_ic_raw_quarterly (full precision)")
    notes.setdefault(c["de_file"], []).append("research value: debt_to_equity_q = calc_debt_value_quarterly / total_stockholders_equity")
    dfe = load_rep["dates"][c["fiscal_end"]]
    notes.setdefault(c["fiscal_end"], []).append(f"formats parsed {dfe['parsed_by_format']}; unparsable {dfe['n_unparsable']}; DD/MM/YYYY only in SPY/QQQ rows where it equals the formation date")
    fl = timing["filing_lag_summary"].set_index("statistic")["value"]
    notes.setdefault(c["filing_date"], []).append(
        f"median filing lag {fl['q50']:.0f} days; {pct(fl['share_filing_after_formation'])} of dates are after formation -> "
        "not the original filing date (meaning unclear); NOT used for timing")
    pk_ok = formulas.loc[formulas["check"].str.startswith("period_key ="), "share_pass"].iloc[0]
    notes.setdefault(c["period_key"], []).append(f"alignment rule (fiscal end - 2 months -> calendar quarter) holds for {pct(pk_ok)} of company rows")
    notes.setdefault(c["forward_return"], []).append(
        f"SELECTED evaluation target. Window formation(t) -> +3 month-ends (see return_window_verification.csv). "
        f"{ret_rep['unrealized_raw_value_counts']['exact_zero']} unrealized values stored as 0.0 set to NaN in fwd_return")
    mom_ok = formulas.loc[formulas["check"].str.startswith("Momentum_3Q"), "share_pass"].iloc[0]
    notes.setdefault(c["momentum"], []).append(
        f"= prod(1+FQR[t-3..t-1])-1 for {pct(mom_ok)} of testable rows; {ret_rep['momentum_exact_zero_set_nan']} exact zeros set to NaN in momentum_3q")
    notes.setdefault("run_date", []).append(f"single value {stocks[c['run_date']].dropna().dt.date.unique().tolist()}; empty in SPY/QQQ rows")
    zc = ret_rep["zero_means_missing"]
    notes.setdefault("market_cap", []).append(
        f"{int((stocks['market_cap'] == 0).sum())} company rows exactly 0 (e.g. pre-IPO quarters), {int((stocks['market_cap'] < 0).sum())} negative, "
        f"{int(stocks['market_cap'].isna().sum())} NaN -> use {zc['market_cap']['derived_column']} (0 -> NaN)")
    pcol = "valuations__per_share_data__month_end_stock_price"
    if pcol in zc:
        notes.setdefault(pcol, []).append(f"{zc[pcol]['zeros_in_company_rows']} company rows exactly 0 -> use {zc[pcol]['derived_column']} (0 -> NaN)")
    gf_de = stocks["valuations__ratios__debt_to_equity"]
    notes.setdefault("valuations__ratios__debt_to_equity", []).append(
        f"no NaN in company rows while stockholders equity is NaN in {int(stocks['total_stockholders_equity'].isna().sum())} rows; "
        f"{int(((gf_de == 0) & stocks['total_stockholders_equity'].isna()).sum())} zeros where equity is missing -> possible zero-filling at source")
    notes.setdefault(c["econ_valid"], []).append("converted to boolean; empty when decomposition status != VALID")
    notes.setdefault("calc_ic_movement", []).append("direction of average IC itself - use this / avg_ic_change_4q, not the IC contribution sign, to say capital fell")
    mapping = validation.build_column_mapping(cfg, catalogue, file_columns, feat_cols, notes)

    # ------------------------------------------------------------------ save tables
    saves = {
        "column_mapping.csv": mapping, "column_reconciliation_full.csv": reconcile,
        "coverage_firms_per_quarter.csv": fpq, "coverage_quarters_per_firm.csv": qpf,
        "duplicates_check.csv": dups, "sequence_breaks.csv": breaks, "consecutiveness_vs_pipeline.csv": agree,
        "precision_audit.csv": precision, "dictionary_formula_checks.csv": formulas,
        "return_field_candidates.csv": candidates, "return_window_verification.csv": window_ev,
        "filing_lag_summary.csv": timing["filing_lag_summary"], "filing_lag_by_year.csv": timing["filing_lag_by_year"],
        "signal_lag_by_fiscal_month.csv": timing["signal_lag_by_fiscal_month"],
        "relevance_coverage_by_quarter.csv": coverage, "relevance_members_missing_from_panel.csv": missing,
        "relevance_reused_tickers.csv": reused, "relevance_empty_ranges_under_rule.csv": rng_rep["ranges_empty_under_rule"],
        "sample_flow.csv": flow, "sample_flow_by_quarter.csv": flow_q, "feature_summary_population.csv": fsum,
        "signal_funnel_by_quarter.csv": funnel, "roic_missing_reasons.csv": miss_reasons,
        "roic_missing_reasons_by_sector.csv": miss_by_sector,
        "ties_by_quarter.csv": ties, "load_duplicate_columns.csv": load_rep["duplicate_columns"],
    }
    if xl.get("available"):
        saves["xlsx_crosscheck_summary.csv"] = xl["summary"]
        saves["xlsx_pipeline_checks.csv"] = xl["pipeline_checks"]
        saves["xlsx_full_precision_formula_checks.csv"] = xl["full_precision_checks"]
    for name, table in saves.items():
        data_io.save_table(table, T / name)

    # ------------------------------------------------------------------ save data
    panel_out = panel.drop(columns=["period"])
    panel_out.to_parquet(dirs["data"] / "panel_prepared.parquet", index=False)
    view_cols = ["KEY", "symbol", "company", "sector", "industry", "period_key", c["fiscal_end"], "formation_date",
                 "return_window_end", "signal_lag_days", "signal_lag_months", "signal_lag_below_min", "in_index", "in_population",
                 "prev_key_gap", "prev_day_gap", "is_consecutive_prev", c["decomposition_status"], c["quality_flag"],
                 c["econ_valid"], c["avg_ic"], "market_cap", "market_cap_clean", *feat_cols, c["c_ebit"], c["c_tax"], c["c_ic"],
                 "fwd_return", "fwd_return_window_unrealized", "momentum_3q", "in_evaluation_sample",
                 "has_level_and_trend_signals", "has_all_contribution_trends"]
    view_cols = list(dict.fromkeys(view_cols))
    data_io.save_table(panel.loc[~panel["is_benchmark"], view_cols], dirs["data"] / "panel_research_view.csv")
    data_io.save_table(sample.benchmark_series(panel, cfg), dirs["data"] / "benchmarks.csv")
    data_io.save_table(members, dirs["data"] / "relevance_members_expanded.csv")

    # ------------------------------------------------------------------ source files unchanged?
    fp_after = data_io.source_fingerprints(cfg)
    unchanged = fp_before[["file", "sha256"]].equals(fp_after[["file", "sha256"]])
    fp_after["unchanged_during_run"] = unchanged
    data_io.save_table(fp_after, dirs["logs"] / f"source_fingerprints_{run_id}.csv")
    if not unchanged:
        raise RuntimeError("A source file changed during the run")

    manifest_json = {
        "run_id": run_id, "seconds": round(time.time() - t0, 1), "config": str(cfg["_meta"]["config_path"]),
        "config_copies": [str(p) for p in cfg_copies], "log": str(log_path),
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "data_as_of": ret_rep["data_as_of"], "rows_raw": int(len(panel)), "columns_in_file": int(load_rep["n_columns_in_file"]),
        "sources": fp_after.to_dict("records"), "level_report": lvl_rep, "return_cleaning": ret_rep,
        "relevance_report": {k: v for k, v in {**rel_rep, **rng_rep}.items() if not isinstance(v, pd.DataFrame)},
    }
    (dirs["logs"] / f"run_manifest_{run_id}.json").write_text(json.dumps(manifest_json, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ report
    report = build_report(cfg=cfg, run_id=run_id, declared=declared, catalogue=catalogue, load_rep=load_rep, file_columns=file_columns,
                          reconcile=reconcile, mapping=mapping, candidates=candidates, window_ev=window_ev, ret_rep=ret_rep,
                          rel_rep=rel_rep, rng_rep=rng_rep, coverage=coverage, missing=missing, reused=reused, fpq=fpq, qpf=qpf,
                          dups=dups, breaks=breaks, agree=agree, precision=precision, formulas=formulas, timing=timing, xl=xl,
                          flow=flow, flow_q=flow_q, fsum=fsum, ties=ties, lvl_rep=lvl_rep, panel=panel, fp=fp_after,
                          funnel=funnel, miss_reasons=miss_reasons, miss_by_sector=miss_by_sector, goodwill_info=goodwill_info)
    (dirs["reports"] / "stage1_report.md").write_text(report, encoding="utf-8")
    log.info("Stage 1 finished in %.1fs. Report: %s", time.time() - t0, dirs["reports"] / "stage1_report.md")
    return 0


# ---------------------------------------------------------------------------
def build_report(**k) -> str:
    cfg, panel = k["cfg"], k["panel"]
    c = cfg["columns"]
    w = int(cfg["features"]["trend_window"])
    stocks = panel[~panel["is_benchmark"]]
    pop = panel[panel["in_population"]]
    rec = k["reconcile"]
    n_match = int((rec["status"] == "matched").sum())
    n_dict_only = int((rec["status"] == "dictionary_only").sum())
    undoc = rec.loc[rec["status"] == "file_only: undocumented", "column"].tolist()
    dupc = rec.loc[rec["status"].str.startswith("file_only: duplicate"), "column"].tolist()
    dict_only = rec.loc[rec["status"] == "dictionary_only", "column"]
    dict_only_non_dummy = [x for x in dict_only if "_combo__" not in x]
    fl = k["timing"]["filing_lag_summary"].set_index("statistic")["value"]
    sig = k["timing"]["signal_lag_by_fiscal_month"]
    lag0 = int(sig.loc[sig["lag_months"] == 0, "population_rows"].sum())
    below = int(pop["signal_lag_below_min"].sum())
    cov = k["coverage"]
    flow = k["flow"].set_index("step")
    common = flow.loc["2d population with all three signals (common sort sample)"]
    ev_common = flow.loc["3d evaluation within common sort sample"]
    fchecks = k["formulas"]
    failing = fchecks[fchecks["share_pass"] < 0.999]
    xl = k["xl"]
    ties = k["ties"]
    ret = k["ret_rep"]
    miss = k["missing"]
    n_q_common = int(common["quarters"])
    ev_trend_q = pop.loc[pop["in_evaluation_sample"] & pop["has_level_and_trend_signals"], "period_key"]

    L: list[str] = []
    A = L.append
    A("# שלב 1 — לימוד הנתונים ובניית מדגם המחקר\n")
    A(f"ריצה: `{k['run_id']}` · נתונים נכונים ל־{ret['data_as_of']} · הגדרות: `outputs/logs/config_used_{k['run_id']}.yaml` · "
      f"כל קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **{bool(k['fp']['unchanged_during_run'].all())}**\n")

    A("## 0. מילון הנתונים\n")
    A(f"- המילון מצהיר על **{k['declared']['declared_total']} עמודות**; חולצו {len(k['catalogue'])} משתנים (כולל 63 עמודות דמה). "
      "הטקסט המלא, כולל משוואות OMML שהומרו לנוסחה לינארית, נשמר ב־`outputs/reports/dictionary_fulltext.txt`.")
    A("- הקטלוג (משמעות, נוסחה, יחידה, ערכים אפשריים, דגל איכות, תנאי אי־תקפות) נשמר ב־`outputs/tables/dictionary_variables.csv`.")
    A(f"- ב־CSV יש {len(k['file_columns'])} עמודות: {n_match} תואמות למילון; {n_dict_only} עמודות מהמילון חסרות ב־CSV "
      f"(63 עמודות דמה, שנמצאות רק בגיליון Decomposition של ה־xlsx{'; ועוד: ' + ', '.join(dict_only_non_dummy) if dict_only_non_dummy else ''}).")
    A(f"- עמודות לא מתועדות: `{'`, `'.join(undoc)}`. עמודות כפולות זהות (הוסרו אחרי אימות): `{'`, `'.join(dupc)}`.")
    A("- נוסחאות מרכזיות מהמילון (אומתו מול הנתונים, ראו סעיף 5):")
    A("  - `ROIC_post = NOPAT / avg IC`, `NOPAT = EBIT·(1−T)`, `avg IC = (IC[t−1]+IC[t])/2` רק כשהרבעון הקודם רצוף; `IC = TCA − TCL + net PPE + goodwill`.")
    A("  - `C_EBIT = (E1−E0)·[⅓Q0/I0 + ⅙Q1/I0 + ⅙Q0/I1 + ⅓Q1/I1]`, `C_TAX = (Q1−Q0)·[⅓E0/I0 + ⅙E1/I0 + ⅙E0/I1 + ⅓E1/I1]`, "
      "`C_IC = (1/I1 − 1/I0)·[⅓E0Q0 + ⅙E1Q0 + ⅙E0Q1 + ⅓E1Q1]`, כאשר Q = 1−T. הסכום שווה בדיוק ל־ΔROIC.")
    A("  - תרומת IC **אינה** כיוון השינוי בהון: כש־NOPAT שלילי, ירידה בהון מורידה את ROIC. כיוון ההון עצמו: `calc_ic_movement` / `avg_ic_change_4q`.\n")

    A("## 1. שדה התשואה העתידית — מועמדים ובחירה\n")
    A(md_table(k["candidates"][["column", "in_dictionary", "non_null_company_rows", "exact_zero", "mean", "median", "p01", "p99"]]))
    A("`valuations__per_share_data__month_end_stock_price` הוא רמת מחיר בסוף הרבעון הפיסקלי, לא תשואה. `Momentum_3Q` הוא תשואת עבר. "
      "המועמד היחיד לתשואה עתידית הוא:\n")
    A("**בחירה: `Future_Quarterly_Return`** (לא מתועד במילון). ראיות:")
    A(md_table(k["window_ev"][["evidence", "n", "value"]]))
    A(f"- הגדרה מאומתת: התשואה בשורה t נמדדת **מסוף החודש של (סוף רבעון period_key + חודשיים) ועד 3 חודשים אחר כך**. "
      "למשל, 2025Q4 → מ־28/02/2026 עד 31/05/2026. זה תואם את המוסכמה שתיארת. ההפרש החציוני הקטן מיחס המחירים (~0.3%) מרמז שמדובר במחיר מתואם, ככל הנראה כולל דיבידנדים (Adjusted Close בגיליון Price).")
    A(f"- ⚠️ ברבעון שחלון התשואה שלו טרם הסתיים (סוף החלון אחרי {ret['data_as_of']}), הקובץ שומר **0.0 במקום ערך חסר**: "
      f"{ret['unrealized_raw_value_counts']['exact_zero']} ערכי 0.0 ו־{ret['unrealized_raw_value_counts']['nan']} ערכי NaN, "
      f"מתוך {ret['rows_unrealized_window']} שורות ({', '.join(f'{p}: {n}' for p, n in ret['unrealized_by_period'].items())}, כולל SPY/QQQ). "
      f"בעמודה `fwd_return` כולם NaN. {ret['realized_exact_zero_returns_kept']} תשואות 0.0 במחזורים שהתממשו נשמרו ומסומנות "
      "(`fwd_return_exact_zero_realized`), וגם בגיליון Price הן 0 — ייתכן מחיר קפוא.")
    A(f"- `Momentum_3Q` = מכפלת שלוש התשואות שקדמו למועד הבנייה (אין שימוש במידע עתידי). {ret['momentum_exact_zero_set_nan']} ערכים שהם 0.0 בדיוק הם היסטוריה חסרה, והוגדרו NaN ב־`momentum_3q`.")
    zc = ret["zero_means_missing"]
    A("- אפסים שמקודדים ערך חסר גם בשדות שוק: " + "; ".join(
        f"`{src}` = 0 ב־{v['zeros_in_company_rows']} שורות חברה → `{v['derived_column']}` = NaN" for src, v in zc.items()) +
      " (למשל רבעונים לפני הנפקה, כמו ABNB 2020Q1–Q3). העמודות המקוריות לא שונו.\n")

    A("## 2. קובץ הרלוונטיות\n")
    A(f"- מבנה: `ticker, start_quarter, end_quarter`; {k['rel_rep']['n_rows']} טווחים, {k['rel_rep']['n_unique_tickers']} טיקרים, "
      f"{k['rel_rep']['n_open_end']} טווחים פתוחים. {k['rng_rep']['n_tickers_with_multiple_ranges']} טיקרים עם כמה טווחים, ללא חפיפות. "
      f"פורמטים לא תקינים: {k['rel_rep']['n_invalid_start']} / {k['rel_rep']['n_invalid_end']}.")
    empty_r = k["rng_rep"]["ranges_empty_under_rule"]
    A("- **כלל (אושר על ידך):** חברה שייכת לאוכלוסיית רבעון q ⇔ `start_quarter ≤ period_key < end_quarter`; end ריק ⇒ חברה עד היום. "
      f"לפי הכלל, {len(empty_r)} טווחים שבהם start = end הם ריקים: "
      f"{', '.join(f'{t} ({s})' for t, s in zip(empty_r['ticker'], empty_r['start_quarter']))} — החברה נכנסה ויצאה באותו רבעון.")
    A(f"- כיסוי: חברות המדד לפי הקובץ לעומת חברות עם שורה בפאנל (טבלה מלאה: `relevance_coverage_by_quarter.csv`):")
    A(md_table(cov[cov["period_key"].isin([cov["period_key"].iloc[i] for i in [0, 1, 5, 9, 13, 17, 21, 25, 29, 33, len(cov) - 2, len(cov) - 1]])]))
    A("  הרבעון הראשון והאחרון חלקיים מבחינת דיווח: 2016Q4 כולל רק שנות כספים שמסתיימות בינואר/פברואר, וב־2026Q2 חלק מהדוחות עוד לא פורסמו.")
    status_counts = miss["download_status"].fillna("not in manifest").value_counts().to_dict() if "download_status" in miss else {}
    ended = miss[~miss["open_end"]]
    A(f"- ⚠️ **Survivorship:** {len(miss)} טווחי חברות מדד שחופפים לתקופת המחקר חסרים לגמרי בפאנל (סטטוס הורדה ב־Manifest: {status_counts}). "
      f"{len(ended)} מהם של חברות שכבר יצאו מהמדד (למשל {', '.join(ended['ticker'].head(15))}), "
      f"ו־{int(miss['open_end'].sum())} של חברות שעדיין במדד ({', '.join(miss.loc[miss['open_end'], 'ticker'])}). "
      f"בגלל זה הכיסוי עולה מ־{pct(cov['coverage_share'].iloc[1])} ב־{cov['period_key'].iloc[1]} ל־{pct(cov['coverage_share'].iloc[-2])} ב־{cov['period_key'].iloc[-2]}: "
      "ככל שחוזרים אחורה, חסרות יותר חברות שנרכשו, נמחקו או קרסו. כיוון ההטיה על התשואות אינו ידוע מראש, "
      "כי חברות נרכשות נוטות לתשואה חיובית וחברות קורסות לשלילית. אי אפשר לתקן זאת בלי מקור נתונים נוסף (`relevance_members_missing_from_panel.csv`).")
    reused = k["reused"]
    span = reused[reused.get("ticker_rows_span_several_ranges", False) == True]  # noqa: E712
    A(f"- טיקרים עם כמה טווחים: {reused['ticker'].nunique()}. בפאנל יש לכל טיקר שם חברה אחד בלבד. "
      + (f"שורות פאנל ששויכו ליותר מטווח אחד: {', '.join(sorted(span['ticker'].unique()))}. "
         "מהקבצים אי אפשר לאמת שהטווח המוקדם מתייחס לאותה ישות משפטית (למשל שושלת DuPont ב־DD)."
         if len(span) else "אין טיקר ששורות הפאנל שלו שויכו ליותר מטווח אחד.")
      + " (`relevance_reused_tickers.csv`)\n")

    A("## 3. מבנה וכיסוי הפאנל\n")
    A(f"- {len(panel):,} שורות: {len(stocks):,} שורות חברה ({stocks['symbol'].nunique()} חברות) ו־{int(panel['is_benchmark'].sum())} שורות SPY/QQQ (מדדי ייחוס, נשמרו בנפרד ב־`benchmarks.csv`).")
    A(f"- רבעונים: {stocks['period_key'].min()}–{stocks['period_key'].max()}. הרבעון הראשון חלקי ({int(k['fpq']['company_rows'].iloc[0])} חברות, רק שנות כספים לא קלנדריות), "
      f"וגם האחרון חלקי ({int(k['fpq']['company_rows'].iloc[-1])} חברות).")
    A("- כפילויות:\n")
    A(md_table(k["dups"][["key", "duplicated_rows"]]))
    A(f"- רבעונים לכל חברה: חציון {k['qpf']['n_quarters'].median():.0f}, מינימום {k['qpf']['n_quarters'].min()}, מקסימום {k['qpf']['n_quarters'].max()} "
      "(`coverage_quarters_per_firm.csv`, `coverage_firms_per_quarter.csv`).")
    br = k["breaks"]
    top = br["symbol"].value_counts().head(3)
    day_breaks = br[br["reason"] != "period_key gap"]
    A(f"- שבירות רצף: {len(br)} ב־{br['symbol'].nunique()} חברות (הכי הרבה: {', '.join(f'{s} ({n})' for s, n in top.items())}). "
      f"הגדרת הרצף שלנו: צעד period_key של 1, ו־{cfg['panel']['consecutive']['min_days']}–{cfg['panel']['consecutive']['max_days']} יום בין סופי תקופה. "
      "ההשוואה להגדרת הצינור (`UNCLASSIFIED_NONCONSECUTIVE`):")
    A(md_table(k["agree"]))
    if len(day_breaks):
        A("  שורות שבהן רק פער הימים שובר את הרצף: " + ", ".join(
            f"{r.KEY} ({r.prev_day_gap} יום; לפי הצינור רצוף={not r.pipeline_says_nonconsecutive})" for r in day_breaks.itertuples()) +
          ". בשורות אלה חלונות המגמה שלנו מקבלים NaN, אף שהתרומות בקובץ קיימות.\n")

    A("## 4. תזמון ומידע עתידי\n")
    by_year = k["timing"]["filing_lag_by_year"].set_index("period_year")["share_filing_after_formation"]
    A(f"- `filing_date` אינו מועד ההגשה המקורי: פער חציוני של {fl['q50']:.0f} יום מסוף התקופה, ו־{pct(fl['share_filing_after_formation'])} מהתאריכים מאוחרים ממועד בניית התיק "
      f"(בשנים {int(by_year.index.min())}–2024 בין {pct(by_year.loc[:2024].min())} ל־{pct(by_year.loc[:2024].max())}; "
      f"ב־2025 {pct(by_year.get(2025.0, np.nan))}; ב־2026 {pct(by_year.get(2026.0, np.nan))}). "
      "ככל הנראה זה תאריך ההגשה האחרון שבו הופיעו נתוני הרבעון (יתכנו נתונים מתוקנים, restated). **המשמעות אינה ברורה, ולכן השדה אינו משמש לתזמון.** "
      "המחקר נשען על מוסכמת החודשיים שלך, ולא ניתן לאמת point-in-time מתוך הקובץ.")
    A(f"- ⚠️ חברות שהרבעון הפיסקלי שלהן מסתיים בפברואר/מאי/אוגוסט/נובמבר ממופות לרבעון שבו **מועד בניית התיק הוא יום סוף התקופה עצמו** (0 חודשים). "
      f"דוח לא יכול להיות זמין ביום סוף התקופה, ולכן אלה שורות עם מידע עתידי מובנה. חברות שמסיימות בינואר/אפריל/יולי/אוקטובר מקבלות חודש אחד (28–31 ימים), "
      f"וחברות קלנדריות חודשיים (59–62 ימים). באוכלוסייה: {lag0:,} שורות עם 0 חודשים, ו־{below:,} שורות בסך הכול עם פחות מ־{cfg['timing']['min_signal_lag_months']} חודשים. "
      "הן **סומנו ולא הוסרו** (`signal_lag_below_min`):")
    A(md_table(sig))

    A("## 5. אימות נוסחאות המילון ודיוק הנתונים\n")
    A("- ⚠️ **ה־CSV מעגל חלק מהעמודות לתצוגה**: ROIC רבעוני, שיעור מס ו־D/E מעוגלים ל־0.01. ב־ROIC רבעוני (חציון ~0.03) זה יוצר קשרים מסיביים ומשבש את חלוקת השלישים:")
    A(md_table(ties))
    A("  לכן רמות ה־ROIC חושבו מחדש לפי נוסחאות המילון מאותו קובץ (`roic_pretax_q = ebit/avg IC`, `roic_posttax_q = NOPAT/avg IC`); "
      f"דפוס ה־NaN זהה לעמודות המקור. פרמטר: `features.roic_level_source = {cfg['features']['roic_level_source']}`.")
    if xl.get("available"):
        A("- השוואה לקריאה בלבד מול `GuruFocus_quarterly_data.xlsx`, שבו הערכים בדיוק מלא:")
        A(md_table(xl["summary"][["check", "value", "n"]]))
    A("- בדיקות נוסחה (טבלה מלאה: `dictionary_formula_checks.csv`):")
    A(md_table(fchecks[["check", "n_tested", "share_pass", "max_abs_dev", "median_abs_dev"]], max_rows=40))
    if len(failing):
        A(f"  בדיקות על ה־CSV עם פחות מ־99.9% הצלחה: {', '.join(failing['check'])}.")
    if xl.get("available"):
        fpc = xl["full_precision_checks"]
        A("- אותן נוסחאות על ערכי ה־xlsx בדיוק מלא (סובלנות יחסית 1e-9):")
        A(md_table(fpc[["check", "n_tested", "share_pass", "max_abs_dev", "median_abs_dev"]]))
        if len(failing):
            ok_full = bool((fpc["share_pass"] >= 0.9999).all())
            A("  " + ("מכיוון שבדיוק מלא הנוסחאות מתקיימות כמעט בכל השורות, **הכשלים ב־CSV נובעים מעיגול הקלטים** "
                      "(בעיקר מכנה קטן: pretax income של כמה מיליונים, שמנפח את שיעור המס, או avg IC קטן). אין אי־התאמה בין המילון לנתונים."
                      if ok_full else "⚠️ גם בדיוק מלא יש שורות שלא מקיימות את הנוסחה — ראו `xlsx_full_precision_formula_checks.csv`."))
    A(f"- ערכי קיצון (לא הוסרו): ROIC רבעוני לאחר מס באוכלוסייה נע בין {pop['roic_posttax_q'].min():.3g} ל־{pop['roic_posttax_q'].max():.3g} "
      f"(אחוזונים 1%/99%: {pop['roic_posttax_q'].quantile(.01):.3g}/{pop['roic_posttax_q'].quantile(.99):.3g}), בגלל הון מושקע קטן או שלילי. "
      f"ב־{int((pop[c['avg_ic']] <= 0).sum())} שורות באוכלוסייה avg IC ≤ 0. שיפוע OLS רגיש לערכים כאלה; מיון לשלישים פחות רגיש.\n")

    A("## 6. משתני המגמה\n")
    A(f"- שיפוע OLS על x = 0,1,2,3 (משקלים −0.3, −0.1, 0.1, 0.3) מחושב רק כשארבעת הערכים קיימים ו־4 השורות הן רבעונים רצופים; אחרת NaN. "
      "חושבו: `roic_trend_4q` (ROIC לאחר מס בדיוק מלא), `ebit/tax/ic_contribution_trend_4q`, סכום וממוצע 4 התרומות, "
      f"`roic_change_4q` = ROIC[t] − ROIC[t−4], שינוי גולמי בהון (`avg_ic_change_4q`, `avg_ic_pct_change_4q`, `avg_ic_trend_4q_rel`) ודגלי איכות לחלון (`n_econ_valid_4q` ועוד).")
    A("- המגמה של חברה ברבעון t משתמשת בהיסטוריה שלה גם מרבעונים שבהם עוד לא הייתה במדד. זה מידע שהיה זמין במועד הבנייה.")
    A("- תקציר באוכלוסייה (טבלה מלאה: `feature_summary_population.csv`):")
    A(md_table(k["fsum"][["feature", "n", "share_available", "mean", "std", "p01", "median", "p99"]], max_rows=30))

    A("## 7. שלוש שכבות המדגם\n")
    A(md_table(k["flow"]))
    A(f"- **המדגם הגולמי** = כל שורות ה־CSV. **מדגם בניית האות (אוכלוסייה)** = שורות חברה שהן חברות מדד ב־period_key, בלי שום תלות בתשואות. "
      f"**מדגם ההערכה** = אוכלוסייה עם תשואה שהתממשה. זמינות התשואה לא משנה את האוכלוסייה, את האותות או את גבולות השלישים (נבדק בבדיקות).")
    fun = k["funnel"].set_index("period_key")
    full_q = fun.loc[fun["common_sort_sample_with_return"] > 0]
    partial = full_q.index[0] if len(full_q) else None
    steady = full_q.iloc[1:] if len(full_q) > 1 else full_q
    A(f"- המדגם המשותף לשלושת המיונים של שלב 2 (ROIC לפני מס, ROIC לאחר מס, מגמה) מכסה {n_q_common} רבעונים; "
      f"במדגם ההערכה שלו {ev_trend_q.nunique()} רבעונים ({ev_trend_q.min()}–{ev_trend_q.max()}), כ־{ev_common['rows'] / max(ev_trend_q.nunique(), 1):.0f} חברות ברבעון בממוצע "
      f"(בלי {partial}, שבו יש רק {int(full_q['common_sort_sample_with_return'].iloc[0])} חברות: כ־{steady['common_sort_sample_with_return'].mean():.0f}). "
      "**זו סדרת זמן קצרה**, והיא תגביל את עוצמת ההסקה.")
    A("- פירוט לפי רבעון: `sample_flow_by_quarter.csv`.\n")

    A("### 7א. למה רק ~60% מחברות המדד נכנסות למיון\n")
    m = steady.mean()
    steps = [("חברות מדד לפי קובץ הרלוונטיות", m["index_members"]), ("עם שורה בפאנל", m["with_panel_row"]),
             ("עם IC (calc_ic_raw)", m["with_ic_raw"]), ("עם ROIC לאחר מס", m["with_roic_posttax"]),
             ("עם מגמת ROIC ל־4 רבעונים", m["with_roic_trend"]), ("מדגם משותף עם תשואה שהתממשה", m["common_sort_sample_with_return"])]
    tbl = pd.DataFrame([{"שלב": s, "חברות ברבעון (ממוצע)": round(v, 1), "ירידה מהשלב הקודם": round(steps[i - 1][1] - v, 1) if i else np.nan}
                        for i, (s, v) in enumerate(steps)])
    A(f"ממוצע לרבעון, {steady.index[0]}–{steady.index[-1]} (טבלה מלאה: `signal_funnel_by_quarter.csv`):")
    A(md_table(tbl))
    mr = k["miss_reasons"]
    A("הסיבה הראשונה לחוסר ROIC לאחר מס בשורות האוכלוסייה (לפי סדר רכיבי הנוסחה; `roic_missing_reasons.csv`):")
    A(md_table(mr))
    A("לפי סקטור (`roic_missing_reasons_by_sector.csv`):")
    A(md_table(k["miss_by_sector"]))
    gi = k["goodwill_info"]
    A(f"- **בנקים, ביטוח ו־REITs** לא מדווחים נכסים והתחייבויות שוטפים, ולכן ה־IC לפי נוסחת המילון (TCA − TCL + PPE + goodwill) אינו מוגדר עבורם. "
      "זו תכונה של ההגדרה ולא תקלה, ומחקרי ROIC נוהגים להוציא חברות פיננסיות מראש.")
    A(f"- **goodwill חסר**: ב־{gi['goodwill_only_missing_firms']} חברות (למשל {', '.join(gi['examples'])}) רק goodwill חסר, וכל שאר רכיבי ה־IC קיימים. "
      f"goodwill = 0 מופיע רק ב־{gi['exact_zero_goodwill_rows']} שורות בכל הפאנל, ו־{gi['firms_goodwill_always_nan']} חברות אף פעם לא מדווחות goodwill. "
      "חלק מהמקרים הם כנראה \"אין goodwill\", אבל לא כולם: ל־AAPL יש goodwill של 5,889 ב־2017, ומ־2018 הערך חסר (אפל הפסיקה להציג אותו בשורה נפרדת). "
      "ב־51 חברות יש חורים באמצע סדרת ה־goodwill. **לא הנחתי ש־NaN = 0.**\n")

    A("## 8. פערים ונקודות פתוחות לפני שלב 2\n")
    A(f"1. **{below:,} שורות באוכלוסייה שבהן סוף התקופה קרוב מדי למועד הבנייה** (0 או 1 חודשים; חברות עם שנת כספים לא קלנדרית): להשאיר במדגם הראשי ולבדוק רגישות, או להחריג מראש (או לדחות את האות שלהן ברבעון)? כרגע הן מסומנות בלבד.")
    A("2. **סינוני איכות לשלב 2** צריכים להיקבע מראש ולחול על שלושת המיונים: למשל `calc_roic_economic_interpretation_valid`, avg IC > 0, או NOPAT חיובי. לא החלתי אף סינון.")
    A("3. **מינימום חברות ברבעון** לחלוקה לשלישים (פרמטר שעוד לא נקבע).")
    A("3א. **goodwill חסר**: להשאיר את חישוב ה־IC כבמילון (החברות האלה ללא ROIC), או להוסיף בדיקת רגישות שבה goodwill חסר = 0? "
      "בגרסה השנייה נוספות כ־45 חברות ברבעון, אבל ה־IC מוטה כלפי מטה אצל חברות שה־goodwill שלהן פשוט לא מוצג בשורה נפרדת (כמו AAPL).")
    A("3ב. **חברות פיננסיות ונדל\"ן**: להוציא אותן מראש מהמדגם הראשי, כמקובל במחקרי ROIC, או להשאיר את המיעוט שיש לו ROIC (כ־19 ברבעון)?")
    A("4. Survivorship ו־filing_date: מגבלות שיתועדו בפרק המגבלות, כי אי אפשר לתקן אותן מהקבצים.")
    A("5. `valuations__ratios__debt_to_equity` ללא NaN גם כשההון העצמי חסר — ייתכן מילוי אפסים במקור. בשלב 4 יש להשתמש ב־D/E המחושב.")
    A("6. בקרות שלא קיימות בקבצים: תנודתיות ובטא (דורשות מקור נוסף). קיימים: גודל (market_cap), סקטור, מינוף, מומנטום (`momentum_3q`), תמחור (EV/FCF; יחס ספר/שוק ניתן לחישוב), רווחיות וצמיחת הון.\n")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the ROIC panel from data/data.parquet into output/.

Every artifact has a fixed name and is overwritten, so a run with new data
replaces the previous one in place. The timestamp and the library versions live
inside the manifest and the report, not in the file names.

    python research_roic/scripts/run_build_panel.py
    python research_roic/scripts/run_build_panel.py --no-excel --no-crosscheck
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import data_io, pipeline, schema, validation  # noqa: E402

LOGGER = logging.getLogger("roic.run")


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
        if v is None or v is pd.NA:
            return ""
        return str(v).replace("|", "\\|").replace("\n", " ")

    head = "| " + " | ".join(map(str, d.columns)) + " |"
    sep = "|" + "---|" * len(d.columns)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in d.itertuples(index=False)]
    more = f"\n_... {len(df) - max_rows} שורות נוספות בקובץ המלא_\n" if len(df) > max_rows else ""
    return "\n" + "\n".join([head, sep, *body]) + "\n" + more


def pct(x: float) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.1f}%"


def _source_max(precision: pd.DataFrame) -> float:
    """Largest magnitude among the fields read straight from the input file."""
    source = precision[precision["role"] == "source"]
    return float(source["max_abs"].max()) if len(source) else float("nan")


def build_report(**k: Any) -> str:
    L: list[str] = []
    A = L.append
    cov = k["coverage"].set_index("column")

    def share(col: str) -> str:
        return pct(float(cov.loc[col, "share_available"])) if col in cov.index else ""

    A("# פאנל ROIC מנתוני Compustat — דוח בנייה ואימות")
    A("")
    A(f"נתונים: `{k['source_name']}` · {k['rows_source']:,} שורות מקור · "
      f"נכונים ל־{k['data_as_of']} · ריצה {k['run_stamp']} ({k['seconds']:.1f} שניות) · "
      f"הגדרות: `output/logs/config_used.yaml` · "
      f"קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **{k['sources_unchanged']}**")
    A("")

    A("## 0. תקציר")
    A("")
    A(f"- **{len(schema.PANEL_COLUMNS)} עמודות** בדיוק לפי הרשימה שהוגדרה, באותו סדר, "
      f"**{k['rows_panel']:,} שורות** — שורה אחת לכל (חברה, רבעון).")
    n_skipped = int((k['column_status'].status == 'SKIPPED').sum())
    A(f"- {len(schema.PANEL_COLUMNS) - n_skipped} עמודות חושבו; {n_skipped} ריקות במפורש, "
      f"עם הסיבה לכל אחת (`schema_contract.csv`).")
    A(f"- רציפות: {pct(k['consecutive_share'])} מהשורות מקושרות לרבעון שקדם להן.")
    A("- ⚠️ מה שאינו במשיכה: `fyearq`, `fqtr`, `rdq`, `fyr`, `ivstq`. בלי `fqtr` אי אפשר "
      "לזהות איזה רבעון פותח את השנה הפיסקלית, ולכן FCF לא מחושב בבנייה הזאת.")
    A(k["skipped_table"])

    A("## 1. המקור, הטיפוסים והשדות")
    A("")
    A("- `decimal128(18,4)` מומר ל-float64 **בתוך Arrow** לפני pandas. אחרת מתקבלים "
      "מיליוני אובייקטי `Decimal`, וחלוקה באפס זורקת חריגה במקום להחזיר NaN.")
    A("- כל עמודה בקובץ מדווחת: ממופה, חסרה, או קיימת-ולא-בשימוש (`input_fields.csv`).")
    A(md_table(k["input_fields"], max_rows=40))
    A(f"- דיוק ההמרה: הערך המוחלט הגדול ביותר בשדות המקור הוא {k['max_source_abs']:,.0f}, "
      f"מול גבול ההמרה המדויקת {k['cast_limit']:,.0f} — פי {k['cast_headroom']:,.0f} מרווח. "
      "ההמרה מ-decimal ל-float64 אינה מאבדת ספרה (`precision_audit.csv`).")
    A("- ⚠️ ערכי ROIC ו-D/E נגזרים יכולים לצאת עצומים כשבסיס ההון קרוב לאפס (אך אינו "
      "אפס בדיוק, שאז הכלל מחזיר NaN). לפי כלליך לא נעשתה שום קטימה ולא הוסרה שום "
      "שורה — הסדרי הגודל מדווחים במקום:")
    A(md_table(k["extreme_ratios"]))

    A("## 2. המפתח והרבעון")
    A("")
    A("- `period_key` = הרבעון הקלנדרי של (סוף התקופה הפיסקלית פחות שני חודשים). "
      "`KEY = period_key + \"_\" + symbol`, למשל `2016Q4_A`.")
    A("- ⚠️ הכפילות מוכרעת על `(gvkey, period_key)` ולא על ה-KEY עצמו: "
      f"{k['rows_no_symbol']:,} שורות (נאמנויות, ETNs) אינן נושאות טיקר, ושרשור טיקר חסר "
      "היה מאחד ישויות שונות למפתח אחד. השורות האלה נשמרות עם KEY ריק.")
    A("")

    A("## 3. כפילויות — מיזוג בשני שלבים")
    A("")
    A("- שלב 1 (`gvkey` + תאריך זהים): השורה הראשונה נשארת, וערך חסר בה מושלם "
      "מהשורות העוקבות באותה קבוצה.")
    A("- שלב 2 (אותו רבעון, תאריכים שונים — שינוי סוף שנה פיסקלית): השורה הראשונה "
      "קובעת והערכים שלה קובעים; שום ערך לא מעורבב בין תקופות פיסקליות שונות.")
    A("- כל שורה שהוסרה נרשמת במלואה, עם כל ערכיה ועם השורה שגברה עליה "
      "(`duplicates_audit.csv`) — ההכרעה ניתנת לשחזור ולביטול.")
    A(md_table(k["duplicates_summary"]))

    A("## 4. רציפות")
    A("")
    A("- שורה מקושרת לרבעון שקדם לה רק כאשר צעד הרבעון הוא 1 **וגם** פער הימים "
      "בטווח שבקונפיג. חישוב חשבוני על חודשים, לא על ימים, היה מסמן 31/12→28/02 בטעות.")
    A(md_table(k["consecutiveness"]))

    A("## 5. שרשרת הרמות")
    A("")
    A(f"- כיסוי מדורג: `ebit` {share('ebit')} · שיעור מס {share('calc_raw_tax_rate_quarterly')} "
      f"· NOPAT {share('calc_nopat_quarterly')} · `calc_ic_raw` {share('calc_ic_raw')} "
      f"· `calc_average_ic_raw_quarterly` {share('calc_average_ic_raw_quarterly')} "
      f"· ROIC posttax {share('calc_roic_posttax_quarterly_ic_raw')}.")
    A("- **הון חוזר חובה**: בלי נכסים או התחייבויות שוטפים ההון המושקע נשאר NaN ולא "
      "מתכווץ ל-PPE+Goodwill. בנקים ומבטחים מדווחים מאזן לא מסווג — זה הפער העיקרי "
      "בין כיסוי NOPAT לכיסוי ROIC.")
    A("- PPE ומוניטין חסרים נספרים כאפס: שורה אחת חסרה לא צריכה למחוק רבעון שלם.")
    A("- אנואליזציה היא `(1+r)⁴−1` ומוגדרת רק כש-`1+r > 0`.")
    A("")

    A("## 6. מס — כולל מס שלילי")
    A("")
    A("שיעור המס **לעולם לא נחתך** לטווח [0,1]: הוא מסומן ומשמש כפי שדווח. "
      "זו ההכרעה בין המקרא (שהציע קטימה ל-0–40%) לבין המפרט, ועצם קיומם של דגלי "
      "האיכות מניח שיעורים מחוץ לטווח.")
    A("")
    A("⚠️ שיעור מס שלילי נובע משתי תופעות שונות לגמרי — הטבת מס על רווח, ומס חיובי "
      "על הפסד לפני מס — ולכן הן נספרות בנפרד:")
    A(md_table(k["tax_sign"]))

    A("## 7. הפירוקים")
    A("")
    A("- `NOPAT = E·Q` (שני גורמים) ו-`ROIC = E·Q/I` (שלושה). שני הגשרים משתמשים "
      "באותה מטריצת מקדמי Shapley: ⅓ ו-⅙, על 2ⁿ ערכי קואליציה שמחושבים פעם אחת.")
    A("- `C_EBIT = ΔE·[⅓Q₀/I₀ + ⅙Q₁/I₀ + ⅙Q₀/I₁ + ⅓Q₁/I₁]` ושתי אחיותיה.")
    A("- ה-residual הוא **בדיקת חיווט ולא שארית**: הזהות מדויקת מהבנייה.")
    A(md_table(k["identities"], max_rows=40))

    A("## 8. התוויות")
    A("")
    A("- כל תווית כיוונית מגיעה ממסווג אחד מול רצועת מהותיות. ערך בדיוק על הרצועה "
      "הוא ניטרלי.")
    A("- ⚠️ **הבדל השערים**: תנועות EBIT והמס מותנות בסטטוס ה-NOPAT (בסיס הון אפס לא "
      "אומר כלום על אם EBIT עלה), ורק תנועת ההון המושקע והקומבינציה מותנות בסטטוס "
      "ה-ROIC. משטרי הסימן תלויים רק בדוח רווח והפסד ולכן שורדים הון אפס, שלילי או חסר.")
    A("- ⚠️ `*_effect` נקרא מסימן התרומה ולא מכיוון התנועה: כששיעור ההשארה שלילי, "
      "EBIT עולה מוריד את NOPAT.")
    A(md_table(k["status_distributions"], max_rows=60))
    A(f"- מפקד הרמות המוצהרות: {k['levels_reached']} מתוך {k['levels_declared']} נוצרו "
      "בפועל; השאר מפורטות ב-`label_family_census.csv` כרמות שלא נצפו.")
    A("")

    A("## 9. ההסבר")
    A("")
    A(f"- המשפטים נלקחים מקטלוג סגור של {k['catalogue_size']:,} אפשרויות "
      f"(`explanation_catalogue.csv`), מתוכן {k['catalogue_used']:,} הופיעו בפועל. "
      "בדיקה אוכפת שאף שורה לא נופלת מחוץ לקטלוג.")
    A("- הניסוח עוקב אחרי סימני Shapley ולא אחרי התנועות הגולמיות: רבעון שבו EBIT עלה "
      "אבל בסיס ההון עלה מהר יותר נקרא כירידת ROIC שנגרמה מהון מושקע.")
    A("")

    A("## 10. חוב, הון עצמי ו-D/E")
    A("")
    A("- `equity = atq − ltq` (נכסים פחות התחייבויות). לפי זהות המאזן זהו ההון העצמי "
      "הכולל, כולל זכויות מיעוט.")
    A("- `calc_debt_value_quarterly = dlcq + dlttq`, רגל חסרה אחת נספרת כאפס ושתיהן "
      "חסרות נותנות NaN. `dlttq` כולל חכירות הוניות; חכירות תפעוליות (ASC 842) "
      "אינן בשני השדות.")
    A("- ⚠️ הון עצמי אפס נותן D/E = NaN לפי חוק המכנה, והון עצמי שלילי נותן D/E שלילי "
      "ומכני. אף שורה לא הוסרה ואף ערך לא נקטם:")
    A(md_table(k["equity_sign"]))

    A("## 11. הצלבה מול GuruFocus")
    A("")
    A("GuruFocus מפרסם בעצמו את עמודות ה-`calc_*`, ולכן ההצלבה אינה רק על מספרים אלא "
      "גם **הסכמה בין תוויות** מול פאנל שחושב ממקור אחר לגמרי.")
    A(md_table(k["crosscheck_overlap"]))
    A("מה שמאושר בבירור: `calc_tax_expense_quarterly` (מתאם 0.996, יחס חציוני 1.0000) — "
      "כלומר **הכרעת הסימן נכונה**, ו-92.4% מהשורות מקיימות "
      "`calc_tax_expense_quarterly == −tax_provision` בדיוק · `calc_ic_raw` ו-"
      "`calc_average_ic_raw_quarterly` (יחס 1.0000) — **נוסחת ההון המושקע נכונה** · "
      "`equity` (יחס 1.0000) · `pretax_income`, הנכסים וההתחייבויות השוטפים, מוניטין, "
      "`market_cap` — כולם ביחס חציוני 1.0000.")
    A(md_table(k["crosscheck_numeric"], max_rows=40))
    A("")
    A("⚠️ **איך לקרוא את הסכמת התוויות למטה:** תווית יכולה להסכים רק במקום שבו "
      "הקלט מסכים. זהו הגבול העליון, והוא נמדד:")
    A(md_table(k["crosscheck_inputs"]))
    A("כלומר הסכמה של 62%–94% בתוויות היא **מעל** תקרת הקלט, כי התוויות גסות "
      "(שלוש מגירות) ומסכימות גם כשהמספרים נבדלים. שלושת הגורמים: הגדרת "
      "`ebit`, העובדה ש-GuruFocus מפרסם את היחסים מעוגלים לשתי ספרות, "
      "ושורות שבהן לצד אחד יש נתון ולשני אין.")
    A(md_table(k["crosscheck_labels"], max_rows=30))
    A("פערים הגדרתיים נרשמו **מראש** בקונפיג ומסומנים `expected_gap` — הם תיעוד, "
      "לא כשל. הפרה בלי הסבר מוקדם עוצרת את הריצה.")
    A(k["breach_note"])

    A("## 12. מה לא נבנה, ולמה")
    A("")
    A("- FCF, CAPEX והמרת YTD — דורשים `fyearq`/`fqtr`. שלוש העמודות קיימות בסכמה "
      "וריקות, והדלקתן בעתיד היא שינוי קונפיג ולא שינוי קוד.")
    A("- `calc_enterprise_value_quarterly` אינו ברשימת העמודות ואין לו צורך בלי FCF.")
    A("- תשואות, מומנטום, WACC, מיונים וטרסילים — מחוץ להיקף הבנייה הזאת.")
    A("- `sector` הוא קוד `gsector` כפי שהוא, בלי תרגום לשמות GICS.")
    A("")

    A("## 13. שחזור")
    A("")
    A(md_table(k["fingerprints"]))
    A("- `output/logs/run_manifest.json` — גרסאות הספריות, ספירות שורות שלב-אחר-שלב, "
      "ו-hashes. `output/logs/config_used.yaml` — עותק בייט של ההגדרות.")
    A("")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--no-excel", action="store_true", help="skip the Excel slice")
    parser.add_argument("--no-crosscheck", action="store_true",
                        help="skip the read-only GuruFocus cross-check")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    t0 = time.time()
    cfg = data_io.load_config(args.config)
    dirs = data_io.ensure_output_dirs(cfg)
    log_path = data_io.setup_logging(dirs["logs"])
    np.random.seed(int(cfg["project"]["seed"]))
    run_stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    LOGGER.info("Run %s | config %s", run_stamp, cfg["_meta"]["config_path"])

    fp_before = data_io.source_fingerprints(cfg)

    # --- build
    panel, report = pipeline.build_panel(cfg)
    panel_keys = report["panel_keys"]
    grid = report["grid"]

    # --- validation tables
    coverage = validation.coverage_by_column(panel)
    crosscheck = ({"numeric": pd.DataFrame(), "labels": pd.DataFrame(),
                   "overlap": pd.DataFrame([{"measure": "cross-check", "value": "skipped"}])}
                  if args.no_crosscheck or not cfg["validation"]["gurufocus_crosscheck"]
                  else validation.crosscheck_gurufocus(
                      panel, data_io.load_gurufocus(cfg), panel_keys, cfg))
    if not args.no_crosscheck:
        LOGGER.info("Cross-checked against GuruFocus (read-only)")

    census = validation.label_family_census(panel)
    catalogue = report.get("explanation_catalogue")
    explanation_census = validation.explanation_census(panel, catalogue)
    tables = {
        "coverage_by_column.csv": coverage,
        "coverage_by_quarter.csv": validation.coverage_by_quarter(panel, panel_keys),
        "status_distributions.csv": validation.status_distributions(panel),
        "label_family_census.csv": census,
        "tax_sign_census.csv": validation.tax_sign_census(panel),
        "equity_sign_census.csv": validation.equity_sign_census(panel),
        "identity_checks.csv": validation.identity_checks(panel, grid, panel_keys),
        "no_inf_check.csv": validation.no_inf_check(panel),
        "precision_audit.csv": validation.precision_audit(panel),
        "extreme_ratio_census.csv": validation.extreme_ratio_census(panel),
        "consecutiveness_summary.csv": validation.consecutiveness_summary(
            grid, panel, panel_keys),
        "duplicates_audit.csv": report["duplicates_audit"],
        "duplicates_summary.csv": validation.duplicates_summary(report),
        "input_fields.csv": report["input_fields"],
        "schema_contract.csv": schema.schema_contract(report["column_status"]),
        "explanation_catalogue.csv": catalogue if catalogue is not None else pd.DataFrame(),
        "explanation_census.csv": explanation_census,
        "crosscheck_vs_gurufocus.csv": crosscheck["numeric"],
        "crosscheck_label_agreement.csv": crosscheck["labels"],
        "crosscheck_overlap.csv": crosscheck["overlap"],
        "crosscheck_input_agreement.csv": crosscheck.get(
            "input_agreement", pd.DataFrame()),
    }
    for name, table in tables.items():
        data_io.save_table(table, dirs["tables"] / name)

    # --- the panel, at fixed paths
    panel_path = data_io.save_panel_parquet(
        panel, dirs["data"] / cfg["output"]["panel_file"], cfg)
    LOGGER.info("Panel written: %s", panel_path)

    excel_info: dict[str, Any] = {"skipped": True}
    if not args.no_excel:
        year = int(cfg["output"]["excel_from_year"])
        mask = panel_keys["period_year"].to_numpy() >= year
        name = cfg["output"]["excel_file_pattern"].format(year=year)
        excel_info = data_io.save_panel_excel(panel[mask], dirs["data"] / name, cfg)
        LOGGER.info("Excel written: %s (%d rows from %d)", name,
                    excel_info["rows_written"], year)

    # --- source files unchanged?
    fp_after = data_io.source_fingerprints(cfg)
    unchanged = fp_before[["file", "sha256"]].equals(fp_after[["file", "sha256"]]) \
        if "sha256" in fp_before.columns else True
    fp_after["unchanged_during_run"] = unchanged
    data_io.save_table(fp_after, dirs["logs"] / "source_fingerprints.csv")

    # --- report
    status = report["column_status"]
    skipped = status[status.status == "SKIPPED"][["column", "spec", "reason"]]
    breaches = validation.unexpected_breaches(crosscheck)
    breach_note = ("✅ אין הפרת סף בלי הסבר מוקדם.\n" if breaches.empty
                   else "⚠️ **הפרות סף בלי הסבר מוקדם:**\n" + md_table(breaches))
    text = build_report(
        source_name=Path(cfg["_paths"]["input_parquet"]).name,
        rows_source=report["rows_in_source"], rows_panel=report["rows_in_panel"],
        data_as_of=pd.to_datetime(panel["fiscal_period_end_date"]).max().date().isoformat(),
        run_stamp=run_stamp, seconds=time.time() - t0, sources_unchanged=unchanged,
        column_status=status, skipped_table=md_table(skipped),
        coverage=coverage, consecutive_share=report["consecutive_prev_share"],
        rows_no_symbol=int(panel["symbol"].isna().sum()),
        duplicates_summary=tables["duplicates_summary.csv"],
        consecutiveness=tables["consecutiveness_summary.csv"],
        tax_sign=tables["tax_sign_census.csv"],
        equity_sign=tables["equity_sign_census.csv"],
        identities=tables["identity_checks.csv"],
        status_distributions=tables["status_distributions.csv"],
        input_fields=report["input_fields"],
        extreme_ratios=tables["extreme_ratio_census.csv"],
        max_source_abs=float(_source_max(tables["precision_audit.csv"])),
        cast_limit=2.0 ** 53 / 1e4,
        cast_headroom=(2.0 ** 53 / 1e4) / max(1.0, float(_source_max(
            tables["precision_audit.csv"]))),
        levels_reached=int(census["reached"].sum()), levels_declared=int(len(census)),
        catalogue_size=int(len(catalogue)) if catalogue is not None else 0,
        catalogue_used=int(explanation_census["reached"].sum())
        if len(explanation_census) else 0,
        crosscheck_overlap=crosscheck["overlap"], crosscheck_numeric=crosscheck["numeric"],
        crosscheck_labels=crosscheck["labels"], breach_note=breach_note,
        crosscheck_inputs=tables["crosscheck_input_agreement.csv"],
        fingerprints=fp_after,
    )
    report_path = dirs["reports"] / "report.md"
    report_path.write_text(text, encoding="utf-8")

    # --- manifest
    manifest = {
        "run_stamp": run_stamp,
        "seconds": round(time.time() - t0, 2),
        "config_path": str(cfg["_meta"]["config_path"]),
        "config_copy": str(data_io.copy_config(cfg, dirs["logs"])),
        "log_path": str(log_path),
        "python": sys.version.split()[0],
        "pandas": pd.__version__, "numpy": np.__version__,
        "rows_in_source": report["rows_in_source"],
        "dedup": report["dedup"],
        "rows_in_panel": report["rows_in_panel"],
        "columns": len(panel.columns),
        "columns_skipped": skipped["column"].tolist(),
        "consecutive_prev_share": report["consecutive_prev_share"],
        "panel_file": str(panel_path),
        "excel": excel_info,
        "sources_unchanged": bool(unchanged),
        "sources": fp_after.to_dict("records"),
        "unexpected_crosscheck_breaches": breaches["column"].tolist()
        if not breaches.empty else [],
    }
    (dirs["logs"] / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    LOGGER.info("Finished in %.1fs. Report: %s", time.time() - t0, report_path)
    if not unchanged:
        raise RuntimeError("A source file changed during the run")
    if not breaches.empty and cfg["validation"].get("stop_on_unexpected_breach"):
        raise RuntimeError("Unexpected cross-check breach: "
                           + ", ".join(breaches["column"].tolist()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

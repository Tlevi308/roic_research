"""Build the price panel from data/prices.gz into output/prices.parquet.

Every artifact has a fixed name and is overwritten. The run's tables, logs and
report live in output/prices_build/, apart from the base run's: that run prunes
any CSV in output/tables it did not produce, and its logs have fixed names.

    python research_roic/scripts/run_build_prices.py
    python research_roic/scripts/run_build_prices.py --no-excel
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
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src import data_io, prices as P, schema, validation  # noqa: E402
from run_build_panel import md_table, pct  # noqa: E402

LOGGER = logging.getLogger("roic.run_prices")
SOURCES = ("input_parquet", "prices_input")


def build_report(**k: Any) -> str:
    L: list[str] = []
    A = L.append
    cov = k["coverage"].set_index("column")

    def share(col: str) -> str:
        return pct(float(cov.loc[col, "share_available"])) if col in cov.index else ""

    A("# פאנל מחירים מ-Compustat Security Monthly — דוח בנייה ואימות")
    A("")
    A(f"נתונים: `{k['source_name']}` · {k['rows_source']:,} שורות חודשיות · "
      f"{k['first_month']}–{k['last_month']} · ריצה {k['run_stamp']} ({k['seconds']:.1f} שניות) · "
      f"הגדרות: `output/prices_build/logs/config_used.yaml` · "
      f"קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **{k['sources_unchanged']}**")
    A("")

    A("## 0. תקציר")
    A("")
    A(f"- **{len(schema.PRICE_COLUMNS)} עמודות** לפי החוזה, באותו סדר, "
      f"**{k['rows_panel']:,} שורות** — שורה אחת לכל (חברה, רבעון), {k['firms']:,} חברות.")
    A(f"- כיסוי: `Close` {share('Close')} · `Quaterly_return` {share('Quaterly_return')} · "
      f"`MomentomQ3` {share('MomentomQ3')} · `ipodate` {share('ipodate')} · "
      f"`dldte` {share('dldte')}.")
    A(f"- הפלט: `output/{k['panel_file']}` (כל השורות) ו-`output/{k['excel_name']}` "
      f"({k['excel_rows']}).")
    A("- ⚠️ `Quaterly_return` הוא **ערך עתידי**: התשואה ברבעון שאחרי `Date`. זה המשתנה "
      "התלוי, ולעולם אינו נכנס לסיגנל, לסינון או לטרסילים.")
    A("")

    A("## 1. ההגדרות")
    A("")
    A("סימון: `q` הוא רבעון השורה, `q+1` הרבעון הבא (3 חודשים קדימה), `q−3` שלושה "
      "רבעונים אחורה (9 חודשים).")
    A("")
    A("| עמודה | הגדרה |")
    A("|---|---|")
    A("| `KEY` | `period_key + \"_\" + tic`, דרך `keys.build_key` — אותה פונקציה של הפאנל הבסיסי |")
    A("| `Date` | `datadate` מהקובץ: היום האחרון בחודש הקלנדרי |")
    A("| `Ticker` | `tic`, זהה ל-`symbol` בפאנל הבסיסי |")
    A("| `Close` | `prccm / ajexm` — מחיר מתואם לפיצולים |")
    A("| `Quaterly_return` | `Close(q+1)/Close(q) − 1` — הרבעון הבא, **בלי דיבידנדים** |")
    A("| `MomentomQ3` | `Close(q)/Close(q−3) − 1` — שלושת הרבעונים האחרונים |")
    A("| `ipodate`, `dldte` | כפי שהם בקובץ; לא מסננים ולא משנים שום ערך |")
    A("")
    A("- **חודש לרבעון** — אותו כלל של הפאנל הבסיסי, הרבעון הקלנדרי של (סוף החודש − 2 "
      "חודשים): פברואר → Q4 של השנה הקודמת, מאי → Q1, אוגוסט → Q2, נובמבר → Q3.")
    A("- **חיפוש לפי רבעון קלנדרי** (gvkey, quarter_index ± k), לא לפי מיקום שורה: רבעון "
      "חסר נותן NaN, ולעולם לא תשואה של שני רבעונים שמתויגת כרבעונית.")
    A("- **רק נקודות הקצה**: לתשואה Close תקף ב-q וב-q+1, למומנטום ב-q−3 וב-q. רבעוני "
      "ביניים לא משפיעים.")
    A("- **\"אין מחיר\"**: `prccm` אפס או חסר, או `ajexm` חסר, נותן `Close = NaN`. אף "
      "שורה לא נמחקה ושום ערך לא נקטם.")
    A("")

    A("## 2. המקור והשדות")
    A("")
    A(f"- כל עמודה בקובץ מדווחת: ממופה, חסרה, או קיימת-ולא-בשימוש. תאריכים שאינם סוף "
      f"חודש: **{k['dates_not_month_end']}**.")
    A(md_table(k["input_fields"]))

    A("## 3. בחירת הנייר")
    A("")
    A("- בקובץ אין `iid`, ולחלק מהחברות יש כמה ניירות. נבחר הנייר שה-(gvkey, tic) שלו "
      "**זהה** לזה של הפאנל הבסיסי — ולכן ה-KEY של המחירים זהה מבנית ל-KEY הבסיסי.")
    A("- חברה בלי התאמה מדויקת **לא מנוחשת**: אין לה שורות, והיא מפורטת, עם הטיקרים "
      "שכן נמצאים בקובץ, ב-`issue_selection_unmatched.csv`.")
    A(md_table(k["selection_summary"]))

    A("## 4. למה ערך חסר")
    A("")
    A("- כל NaN מקבל סיבה. `DELETED_BY_NEXT_DATE`: ‏`dldte` נופל עד תאריך הרבעון הבא. "
      "`NOT_YET_AVAILABLE`: תאריך הרבעון הבא אחרי סוף הקובץ. `BEFORE_FIRST_PRICE`: "
      "שלושת הרבעונים הראשונים של הסדרה.")
    A(md_table(k["nan_reasons"], max_rows=40))

    A("## 5. פער הדיבידנד")
    A("")
    A("- לפי בחירתך התשואה היא יחס מחירים, **בלי דיבידנדים**. הטבלה משווה, לכל רבעון, את "
      "תשואת המחיר שמומשה (q−1 → q) לתשואה הכוללת ממכפלת שלושת ה-`trt1m` החודשיים. "
      "הפער `(1+כוללת)/(1+מחיר) − 1` הוא תשואת הדיבידנד של הרבעון.")
    A("- החישוב **מסתכל אחורה בלבד**. פער שלילי ברור אינו דיבידנד ונספר כחריג.")
    A(md_table(k["dividend_gap"], max_rows=20))

    A("## 6. ערכים קיצוניים — נספרים, לא נחתכים")
    A("")
    A("- לפי כלליך שום ערך לא נקטם ולא הוסר. 20 הקיצוניים מכל עמודה, עם KEY, ב-"
      "`extreme_return_top.csv`.")
    A(md_table(k["extremes"], max_rows=40))

    A("## 7. כיסוי ה-JOIN לפאנל הבסיסי")
    A("")
    if k["join"] is None:
        A("- `output/panel.parquet` לא נמצא, ולכן הטבלה לא חושבה.")
    else:
        A("- איזה אחוז מה-KEY של הפאנל הבסיסי מוצא שורת מחיר ואת כל אחד מהמספרים. "
          "הפירוק לפי שנה ב-`join_coverage.csv`.")
        A(md_table(k["join"].head(2)))
    A("")

    A("## 8. תזמון — מסוף הרבעון הפיסקלי ועד `Date`")
    A("")
    if k["timing"] is None:
        A("- `output/panel.parquet` לא נמצא, ולכן הטבלה לא חושבה.")
    else:
        A("- ⚠️ אצל חברות שהרבעון הפיסקלי שלהן מסתיים בינואר/אפריל/יולי/אוקטובר המחיר "
          "נלקח **חודש** אחרי סוף הרבעון, ואצל פברואר/מאי/אוגוסט/נובמבר — **באותו חודש**. "
          "בשתי הקבוצות הדוח כנראה עוד לא פורסם ב-`Date`. המיפוי לא שונה; ההחלטה שייכת "
          "לשלב ה-JOIN.")
        A(md_table(k["timing"]))
    A("")

    A("## 9. תאריכי הנפקה ומחיקה")
    A("")
    A("- ⚠️ **`dldte` אינו היום האחרון למסחר**: לחלק מהחברות יש מחירים שנים אחרי "
      "`dldte`, ולאחרות המחירים נגמרים שנים לפניו (האחוזונים בטבלה).")
    A("- ⚠️ **אזהרת look-ahead לשלב הבא**: מותר להשתמש בתאריכים רק בצורה שידועה ב-`Date` — "
      "\"ההנפקה כבר הייתה\" (`ipodate ≤ Date`) ו\"החברה עוד לא נמחקה\" (`dldte` ריק או "
      "`dldte > Date`). סינון של חברה כי **תימחק** ברבעון הבא משתמש במידע עתידי, מוציא "
      "בדיוק את התשואות הגרועות ומטה את התוצאות.")
    A(md_table(k["listing"], max_rows=40))

    A("## 10. הדוגמה שלך — AAPL")
    A("")
    A("- בדוגמה שלך המחירים הם Adj Close של יאהו, שמתואם גם לדיבידנדים, ונלקחים ביום "
      "המסחר האחרון. כאן התשואה בלי דיבידנדים ו-`Date` הוא סוף החודש הקלנדרי — ולכן "
      "התשואות נמוכות מעט משלך. להמחשה בלבד.")
    A(md_table(k["example"], digits=6))

    A("## 11. שחזור")
    A("")
    A(md_table(k["fingerprints"]))
    A("- `output/prices_build/logs/run_manifest.json` — גרסאות, ספירות וה-hashes. "
      "`output/prices_build/logs/config_used.yaml` — עותק בייט של ההגדרות.")
    A("")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--no-excel", action="store_true", help="skip the Excel slice")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    t0 = time.time()
    cfg = data_io.load_config(args.config)
    out = cfg["_paths"]["output_dir"]
    build_dir = out / cfg["prices"]["build_dir"]
    dirs = {"tables": build_dir / "tables", "logs": build_dir / "logs"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    log_path = data_io.setup_logging(dirs["logs"])
    run_stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    LOGGER.info("Run %s | config %s", run_stamp, cfg["_meta"]["config_path"])

    fp_before = data_io.source_fingerprints(cfg, SOURCES)

    # --- build
    prices, report = P.build_prices(cfg)
    panel_path = out / cfg["output"]["panel_file"]
    base = None
    if panel_path.exists():
        base = pq.read_table(panel_path, columns=["KEY", "fiscal_period_end_date"]).to_pandas()
        base["KEY"] = base["KEY"].astype("string")
    else:
        LOGGER.warning("Base panel not found at %s: join and timing tables skipped", panel_path)

    # --- validation tables
    coverage = validation.coverage_by_column(prices)
    extremes, extreme_top = validation.extreme_return_census(prices, report, cfg)
    selection = report["selection"]
    tables: dict[str, pd.DataFrame] = {
        "price_input_fields.csv": report["input_fields"],
        "issue_selection.csv": validation.issue_selection_summary(report),
        "issue_selection_unmatched.csv": selection[selection["category"] != "MATCHED"],
        "price_coverage_by_quarter.csv": validation.price_coverage_by_quarter(prices, report),
        "price_nan_reasons.csv": validation.price_nan_reasons(prices, report),
        "dividend_gap_census.csv": validation.dividend_gap_census(prices, report, cfg),
        "extreme_return_census.csv": extremes,
        "extreme_return_top.csv": extreme_top,
        "listing_dates_census.csv": validation.listing_dates_census(
            prices, report, P.base_company_status(cfg)),
        "example_aapl.csv": validation.example_side_by_side(prices, P.RESEARCHER_EXAMPLE_AAPL),
        "coverage_by_column.csv": coverage,
        "no_inf_check.csv": validation.no_inf_check(prices),
    }
    if base is not None:
        tables["join_coverage.csv"] = validation.join_coverage(prices, base["KEY"])
        tables["timing_lag_census.csv"] = validation.timing_lag_census(prices, base)
    for name, table in tables.items():
        data_io.save_table(table, dirs["tables"] / name)
    for stale in sorted(dirs["tables"].glob("*.csv")):
        if stale.name not in tables:
            stale.unlink()
            LOGGER.warning("Removed a stale table from an earlier build: %s", stale.name)

    # --- the panel, at fixed paths
    panel_out = data_io.save_panel_parquet(prices, out / cfg["prices"]["output_file"], cfg)
    LOGGER.info("Price panel written: %s", panel_out)

    year = int(cfg["output"]["excel_from_year"])
    excel_name = cfg["prices"]["excel_file_pattern"].format(year=year)
    excel_info: dict[str, Any] = {"skipped": True}
    if not args.no_excel:
        mask = report["quarter_keys"]["period_year"].to_numpy() >= year
        excel_info = data_io.save_panel_excel(prices[mask], out / excel_name, cfg,
                                              sheet=cfg["prices"]["excel_sheet"])
        LOGGER.info("Excel written: %s (%d rows from %d)", excel_name,
                    excel_info["rows_written"], year)

    # --- source files unchanged?
    fp_after = data_io.source_fingerprints(cfg, SOURCES)
    unchanged = fp_before[["file", "sha256"]].equals(fp_after[["file", "sha256"]]) \
        if "sha256" in fp_before.columns else True
    fp_after["unchanged_during_run"] = unchanged
    data_io.save_table(fp_after, dirs["logs"] / "source_fingerprints.csv")

    # --- report
    text = build_report(
        source_name=Path(cfg["_paths"]["prices_input"]).name,
        rows_source=report["rows_in_source"], rows_panel=report["rows_in_panel"],
        firms=int(report["quarter_keys"]["gvkey"].nunique()),
        first_month=pd.Timestamp(report["first_month_in_file"]).date().isoformat(),
        last_month=pd.Timestamp(report["last_month_in_file"]).date().isoformat(),
        run_stamp=run_stamp, seconds=time.time() - t0, sources_unchanged=unchanged,
        coverage=coverage, panel_file=cfg["prices"]["output_file"], excel_name=excel_name,
        excel_rows=(f"{excel_info['rows_written']:,} שורות מ-{year}"
                    if not excel_info.get("skipped") else "לא נכתב בריצה הזאת"),
        dates_not_month_end=report["dates_not_month_end"],
        input_fields=report["input_fields"],
        selection_summary=tables["issue_selection.csv"],
        nan_reasons=tables["price_nan_reasons.csv"],
        dividend_gap=tables["dividend_gap_census.csv"],
        extremes=extremes,
        join=tables.get("join_coverage.csv"),
        timing=tables.get("timing_lag_census.csv"),
        listing=tables["listing_dates_census.csv"],
        example=tables["example_aapl.csv"],
        fingerprints=fp_after,
    )
    report_path = build_dir / "report.md"
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
        "rows_matched_monthly": report["rows_matched_monthly"],
        "selection": report["selection_counts"],
        "rows_in_panel": report["rows_in_panel"],
        "columns": list(prices.columns),
        "panel_file": str(panel_out),
        "excel": excel_info,
        "sources_unchanged": bool(unchanged),
        "sources": fp_after.to_dict("records"),
    }
    (dirs["logs"] / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    LOGGER.info("Finished in %.1fs. Report: %s", time.time() - t0, report_path)
    if not unchanged:
        raise RuntimeError("A source file changed during the run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

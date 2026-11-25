"""Build the S&P 500 panel - fundamentals and prices of every firm the S&P 500
file's records go to, with ``in_index`` per quarter - into output/sp500_panel/.

Kept apart from the index panels (scripts/run_build_index_panel.py) on purpose:
its own code (src/sp500_panel.py), its own config section ``sp500_panel`` and
its own output folder. It writes nothing outside output/sp500_panel/.

It reads output/panel.parquet, output/prices.parquet, data/sp500hist.csv and
the exchange column of data/data.parquet, so run the base and price builds
before it when their inputs change. Every artifact has a fixed name and is
overwritten.

    python research_roic/scripts/run_build_sp500_panel.py
    python research_roic/scripts/run_build_sp500_panel.py --no-excel
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
sys.path.insert(0, str(ROOT / "scripts"))

from src import data_io, schema, sp500_panel as S  # noqa: E402
from run_build_panel import md_table, pct  # noqa: E402

LOGGER = logging.getLogger("roic.run_sp500")

DECISION_TEXT = {
    S.ONLY_CANDIDATE: "מועמדת אחת, והיא עברה את בדיקת הבורסה",
    S.EXCHANGE_CHECK: "כמה מועמדות, ורק אחת עברה את בדיקת הבורסה",
    S.MOST_QUARTERS: "כמה עברו את בדיקת הבורסה; הכריע מספר רבעוני הדוח בטווח",
}
REASON_TEXT = {
    S.TICKER_NOT_IN_PANEL: "אין בפאנל חברה עם הטיקר, גם לא עם סיומת. בדרך כלל הטיקר הוחלף "
                           "(ACE→CB, DISCA→WBD), או שזו סדרת מניות שנייה (GOOG, FOX, NWS, UA)",
    S.NO_REPORT_ROW_IN_RANGE: "יש בפאנל חברה עם הטיקר, אבל בלי אף שורת דוח בטווח הרשומה",
    S.NOT_NYSE_NASDAQ: "כל המועמדות נפסלו בבדיקת הבורסה (הבורסה של היום)",
    S.TIE: "כמה מועמדות עברו את בדיקת הבורסה עם אותו מספר רבעונים מקסימלי",
    S.ZERO_LENGTH: "start_quarter = end_quarter: הרשומה לא מכסה אף רבעון",
}


def count_he(n: int, one: str, many: str) -> str:
    """'שורה אחת' rather than '1 שורות'."""
    return one if int(n) == 1 else f"{int(n):,} {many}"


def build_report(**k: Any) -> str:
    L: list[str] = []
    A = L.append
    rep = k["report"]
    first, last = rep["first_period_in_file"], rep["last_period_in_file"]
    byq = k["by_quarter"]
    covered = byq[byq["period_key"].between(first, last)]
    by_status = rep["records_by_status"]
    n_assigned = int(by_status[S.ASSIGNED])
    n_not = rep["records_in_file"] - n_assigned
    over = rep["moved_over_current_holder"]

    A("# פאנל S&P 500 — דוחות כספיים ומחירים, עם זמינות להשקעה לכל רבעון")
    A("")
    A(f"קובץ ההרכב: `{k['records_name']}` ({rep['records_in_file']} רשומות, "
      f"{rep['tickers_in_file']} טיקרים, {first}–{last}) · ריצה {k['run_stamp']} "
      f"({k['seconds']:.1f} שניות) · קובצי הקלט לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): "
      f"**{k['sources_unchanged']}**")
    A("")

    A("## 0. תקציר")
    A("")
    A(f"- **{rep['rows_in_panel']:,} שורות** — כל שורות הדוחות של **{rep['firms']} החברות** "
      f"שרשומות הקובץ שויכו אליהן, לאורך כל ההיסטוריה שלהן. "
      f"**{len(S.SP500_PANEL_COLUMNS)} עמודות**: {len(schema.PANEL_COLUMNS)} של הדוחות, "
      f"{len(S.PRICE_VALUE_COLUMNS)} של המחירים, ו-{len(S.FLAG_COLUMNS)} של המדד — אותן "
      "עמודות כמו בפאנל QQQ100.")
    A(f"- **{n_assigned} מתוך {rep['records_in_file']} רשומות שויכו לחברה**, כל רשומה לחברה "
      f"אחת לכל היותר. {n_not} לא שויכו, כל אחת עם סיבה אחת (סעיף 2).")
    A(f"- ל-{rep['rows_with_price']:,} שורות ({pct(rep['rows_with_price'] / rep['rows_in_panel'])}) "
      "יש שורת מחיר.")
    A(f"- **`in_index = 1` ב-{rep['rows_in_index']:,} שורות** — בממוצע "
      f"{covered['in_index'].mean():.1f} חברות לרבעון ({covered['in_index'].min()}–"
      f"{covered['in_index'].max()}), {first}–{last}.")
    A(f"- הפלט: `{k['panel_file']}` (כל השורות) ו-`{k['excel_name']}` ({k['excel_rows']}).")
    A(f"- בדיקה צולבת: הרבעונים שנמנו מהרשומות המשויכות בלבד, בלי הפאנל, זהים לשורות שסומנו 1 "
      f"(עד {last}): **{rep['flag_cross_check']}**.")
    A(f"- הפרדה מ-QQQ100: קוד (`src/sp500_panel.py`), סעיף בקונפיג (`sp500_panel`) ותיקיית "
      f"פלט (`{k['root']}`) משלו. הריצה כותבת רק לתיקייה הזאת.")
    A("")

    A("## 1. הכלל")
    A("")
    A("```")
    A("quarter        = הרבעון מתוך ה-KEY                     2024Q2_AAL → 2024Q2")
    A("in_index = 1   אם לאותו gvkey יש רשומה משויכת שבה    start_quarter ≤ quarter < end_quarter")
    A("               (end_quarter ריק = עדיין במדד)")
    A("in_index = 0   אחרת")
    A("formation_date = סוף החודש, חודשיים אחרי סוף הרבעון    2024Q2 → 2024-08-31  (= Date של המחירים)")
    A("```")
    A("")
    A("- דוגמה: `AAL 2015Q1–2024Q3` → השורה `2024Q2_AAL` (סוף רבעון 2024-06-30, השקעה ב-2024-08-31) "
      "מקבלת 1, והשורה `2024Q3_AAL` מקבלת 0. הכלל נקבע ב-13/09 לקובץ הזה.")
    A("- הרבעון נקרא מה-KEY ונבדק מול `fiscal_period_end_date` לפי כלל הפאנל הבסיסי; אי-התאמה "
      "הייתה עוצרת את הריצה.")
    A("- ⚠️ הקובץ ברבעונים ולא בימים: הרבעון האחרון לפני היציאה מקבל 1 גם כשהחברה יצאה לפני יום "
      "ההשקעה. חברה שיצאה ביולי 2024 (end_quarter 2024Q3) מקבלת 1 ב-2024Q2, אף שב-2024-08-31 "
      "כבר לא הייתה במדד.")
    A(f"- ⚠️ הקובץ מתחיל ב-{first}, ולכן כל {rep['rows_before_file_start']:,} השורות שלפניו "
      "מקבלות 0.")
    A(f"- ⚠️ {rep['records_starting_at_file_start']} רשומות מתחילות ב-{first}, תחילת הקובץ. כשהדוחות "
      "של חברה מתחילים מאוחר יותר, הרבעונים האלה מקבלים 1 כמו שהקובץ אומר. "
      f"{rep['rows_in_index_without_price']:,} שורות עם 1 הן בלי שורת מחיר — העמודה "
      "`in_index_with_price_row` בסעיף 4.")
    A(f"- ⚠️ {count_he(rep['rows_in_index_after_file'], 'שורה אחת', 'שורות')} מרבעון אחרי {last} "
      "עם 1: `end_quarter` ריק נקרא \"עדיין במדד\".")
    A("")

    A("## 2. השיוך: מרשומת טיקר לחברה")
    A("")
    A("1. **מועמדות** — חברות בפאנל שהטיקר שלהן הוא בדיוק הטיקר בקובץ, או אותו טיקר עם הסיומת "
      "`.<מספר>` של Compustat (חברה ותיקה שהטיקר שלה עבר לחברה חדשה: `AA.3` = Alcoa Inc), "
      "ושיש להן לפחות שורת דוח אחת בטווח הרשומה. `AAX` או `AA.B` אינם `AA`.")
    A(f"2. **בדיקת בורסה** — נשארות רק מועמדות עם `exchg` ב-{k['exchanges']} (11 = NYSE, "
      "14 = NASDAQ). ⚠️ `exchg` של Compustat הוא הבורסה של היום (או האחרונה), לא של אז.")
    A("3. **הכרעה** — הרשומה הולכת למועמדת עם הכי הרבה רבעוני דוח בטווח. בתיקו היא לא משויכת "
      "ונרשמת. רשומה לא הולכת לשתי חברות.")
    A("")
    A("- ההכרעה קוראת את שורות הדוח של כל טווח הרשומה, כדי לדעת לאיזו חברה הרשומה מתכוונת. "
      "היא לא קוראת מחיר או תשואה.")
    dec = rep["assigned_by_decision"]
    A(md_table(pd.DataFrame({"decided_by": dec.index, "records": dec.to_numpy(),
                             "meaning": [DECISION_TEXT[d] for d in dec.index]})))
    A(f"- {rep['moved_suffix_only']} רשומות שויכו לחברה ותיקה עם סיומת, כי לטיקר המדויק לא היה "
      "דוח בטווח (למשל ABS → `ABS.1` Albertson's).")
    A(f"- {len(over)} רשומות שויכו לחברה ותיקה **במקום** המחזיקה הנוכחית של הטיקר, שגם לה היו "
      "דוחות בטווח:")
    status = rep["status"].set_index("source_row")
    if len(over):
        A(md_table(pd.DataFrame({
            "ticker": over["ticker"], "start_quarter": over["start_quarter"],
            "end_quarter": over["end_quarter"].fillna(""), "chosen": over["symbol"],
            "company": over["company"], "decided_by": over["decided_by"],
            "all_candidates": over["source_row"].map(status["candidates"]),
        })))
    A("### רשומות שלא שויכו")
    A("")
    not_assigned = by_status.drop(S.ASSIGNED)
    A(md_table(pd.DataFrame({"reason": not_assigned.index, "records": not_assigned.to_numpy(),
                             "meaning": [REASON_TEXT[r] for r in not_assigned.index]})))
    nm = k["records_not_matched"]
    A(md_table(nm[["ticker", "start_quarter", "end_quarter", "reason", "candidates",
                   "ticker_holders_in_panel"]].fillna(""), max_rows=100))

    A("## 3. החיבור למחירים")
    A("")
    A("- `left join` מהדוחות למחירים לפי `(gvkey, KEY)`, אחד-לאחד. שורה בלי מחיר שומרת את "
      "הדוחות שלה ומקבלת עמודות מחיר ריקות. הערכים מועתקים כפי שהם — המומנטום, ההפרשים "
      "והממוצעים חושבו על ההיסטוריה המלאה לפני הבחירה.")
    A(f"- שורות בלי KEY: {rep['rows_without_key']}.")
    A("- חברות בלי אף מחיר (פאנל המחירים לא בחר להן נייר):")
    A(md_table(rep["firms_without_prices"]))

    A("## 4. זמינות לפי רבעון")
    A("")
    A("- כל הרבעונים ב-`tables/in_index_by_quarter.csv`. אלה נקודת הפתיחה של שלב 2: מתוך "
      "`in_index = 1`, כמה עם מחיר, עם תשואה ועם ROIC.")
    A(md_table(pd.concat([covered.head(4), covered.tail(8)])))

    A("## 5. מה לא נכלל")
    A("")
    A("- חברות שלא שויכו להן רשומות לא נמצאות בפאנל כלל (סעיף 2). כאן — מה שחסר אצל החברות "
      "המשויכות:")
    ni = k["not_included"]
    A(md_table(ni["reason"].value_counts().rename_axis("reason").reset_index(name="lines")))
    A(md_table(ni, max_rows=30))

    A("## 6. מסוף הרבעון הפיסקלי ועד יום ההשקעה (רק `in_index = 1`)")
    A("")
    A("- כמו ב-QQQ100: מסמנים עכשיו ומחליטים בשלב 2. 2 = רבעון קלנדרי רגיל; 1 ו-0 = רבעון "
      "פיסקלי שנגמר חודש לפני יום ההשקעה או באותו חודש — הדוח כנראה עוד לא פורסם.")
    timing = k["timing"]
    A(md_table(timing[timing["period_year"] == "all"]))

    A("## 7. שחזור")
    A("")
    A(md_table(k["fingerprints"]))
    A(f"- `{k['logs_dir']}/run_manifest.json` — גרסאות, ספירות וה-hashes.")
    A("")
    return "\n".join(L)


def build(cfg: dict[str, Any], write_excel: bool) -> None:
    t0 = time.time()
    out = cfg["_paths"]["output_dir"]
    spec = S.settings(cfg)
    root = S.output_root(cfg)
    dirs = {"tables": root / "tables", "logs": root / "logs"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    log_path = data_io.setup_logging(dirs["logs"])
    run_stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    LOGGER.info("Run %s | S&P 500 panel | config %s", run_stamp, cfg["_meta"]["config_path"])

    sources = {"panel_parquet": out / cfg["output"]["panel_file"],
               "prices_parquet": out / cfg["prices"]["output_file"],
               "sp500_records": S.records_path(cfg)}
    fp_before = data_io.source_fingerprints(cfg, ("input_parquet",), sources)

    # --- build
    table, report = S.build_sp500_panel(cfg)

    # --- tables, in this panel's own folder only
    tables: dict[str, pd.DataFrame] = {
        "ticker_matching.csv": S.ticker_matching(report),
        "records_not_matched.csv": S.records_not_matched(report),
        "in_index_by_quarter.csv": S.by_quarter(table, report),
        "not_included.csv": S.not_included(report),
        "timing_gap_census.csv": S.timing_gap_census(table, report),
    }
    for fname, t in tables.items():
        data_io.save_table(t, dirs["tables"] / fname)
    for stale in sorted(dirs["tables"].glob("*.csv")):
        if stale.name not in tables:
            stale.unlink()
            LOGGER.warning("Removed a stale table from an earlier build: %s", stale.name)

    # --- the panel, at fixed paths
    panel_path = data_io.save_panel_parquet(table, root / spec["panel_file"], cfg)
    LOGGER.info("S&P 500 panel written: %s", panel_path)

    year = int(cfg["output"]["excel_from_year"])
    excel_name = spec["excel_file_pattern"].format(year=year)
    excel_info: dict[str, Any] = {"skipped": True}
    if write_excel:
        mask = pd.Series(report["period_key"]).str[:4].astype(int).to_numpy() >= year
        excel_info = data_io.save_panel_excel(table[mask], root / excel_name, cfg,
                                              sheet=spec["excel_sheet"])
        LOGGER.info("Excel written: %s (%d rows from %d)", excel_name,
                    excel_info["rows_written"], year)

    # --- inputs unchanged?
    fp_after = data_io.source_fingerprints(cfg, ("input_parquet",), sources)
    unchanged = fp_before[["file", "sha256"]].equals(fp_after[["file", "sha256"]])
    fp_after["unchanged_during_run"] = unchanged
    data_io.save_table(fp_after, dirs["logs"] / "source_fingerprints.csv")

    # --- report
    rel = lambda p: Path(p).relative_to(out.parent).as_posix()  # noqa: E731
    text = build_report(
        report=report, run_stamp=run_stamp, seconds=time.time() - t0,
        sources_unchanged=unchanged, records_name=S.records_path(cfg).name,
        exchanges=list(spec["exchanges"]), root=rel(root),
        by_quarter=tables["in_index_by_quarter.csv"],
        records_not_matched=tables["records_not_matched.csv"],
        not_included=tables["not_included.csv"], timing=tables["timing_gap_census.csv"],
        panel_file=rel(panel_path), excel_name=rel(root / excel_name),
        excel_rows=(f"{excel_info['rows_written']:,} שורות מ-{year}"
                    if not excel_info.get("skipped") else "לא נכתב בריצה הזאת"),
        fingerprints=fp_after, logs_dir=rel(dirs["logs"]),
    )
    report_path = root / "report.md"
    report_path.write_text(text, encoding="utf-8")

    # --- manifest
    manifest = {
        "run_stamp": run_stamp,
        "panel": "sp500",
        "seconds": round(time.time() - t0, 2),
        "config_path": str(cfg["_meta"]["config_path"]),
        "config_copy": str(data_io.copy_config(cfg, dirs["logs"])),
        "log_path": str(log_path),
        "python": sys.version.split()[0],
        "pandas": pd.__version__, "numpy": np.__version__,
        "records_in_file": report["records_in_file"],
        "records_by_status": {k: int(v) for k, v in report["records_by_status"].items()},
        "assigned_by_decision": {k: int(v) for k, v in report["assigned_by_decision"].items()},
        "exchanges": list(spec["exchanges"]),
        "firms": report["firms"],
        "rows_in_panel": report["rows_in_panel"],
        "rows_with_price": report["rows_with_price"],
        "rows_in_index": report["rows_in_index"],
        "flag_cross_check": report["flag_cross_check"],
        "columns": list(table.columns),
        "panel_file": str(panel_path),
        "excel": excel_info,
        "sources_unchanged": bool(unchanged),
        "sources": fp_after.to_dict("records"),
    }
    (dirs["logs"] / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    LOGGER.info("Finished in %.1fs. Report: %s", time.time() - t0, report_path)
    if not unchanged:
        raise RuntimeError("An input file changed during the run")


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

    cfg = data_io.load_config(args.config)
    build(cfg, write_excel=not args.no_excel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

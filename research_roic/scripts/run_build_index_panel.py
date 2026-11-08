"""Build the panel of one index - fundamentals and prices of every firm that was
ever in it, with ``in_index`` per quarter - into output/index_panels/<name>/.

It only reads output/panel.parquet and output/prices.parquet, so run the two
builds before it when their inputs change. Every artifact has a fixed name and
is overwritten.

    python research_roic/scripts/run_build_index_panel.py                 # qqq100
    python research_roic/scripts/run_build_index_panel.py --index qqq100
    python research_roic/scripts/run_build_index_panel.py --no-excel
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

from src import data_io, index_panel as I, schema, validation  # noqa: E402
from run_build_panel import md_table, pct  # noqa: E402

LOGGER = logging.getLogger("roic.run_index")


def count_he(n: int, one: str, many: str) -> str:
    """'שורה אחת' rather than '1 שורות'."""
    return one if int(n) == 1 else f"{int(n):,} {many}"


def build_report(**k: Any) -> str:
    L: list[str] = []
    A = L.append
    rep = k["report"]
    name = rep["index"]
    byq = k["by_quarter"]
    covered = byq[byq["period_key"].between(k["first_period"], rep["last_period_in_file"])]

    A(f"# פאנל המדד `{name}` — דוחות כספיים ומחירים, עם זמינות להשקעה לכל רבעון")
    A("")
    A(f"קובץ ההרכב: `{k['constituents_name']}` ({rep['records_in_file']} רשומות, "
      f"{rep['first_date_in_file'].date()}–{rep['last_date_in_file'].date()}) · "
      f"ריצה {k['run_stamp']} ({k['seconds']:.1f} שניות) · "
      f"קובצי הקלט לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **{k['sources_unchanged']}**")
    A("")

    A("## 0. תקציר")
    A("")
    A(f"- **{rep['rows_in_panel']:,} שורות** — כל שורות הדוחות של **{rep['firms_in_file']} "
      f"החברות** שהיו אי פעם במדד, לאורך כל ההיסטוריה שלהן. "
      f"**{len(schema.INDEX_PANEL_COLUMNS)} עמודות**: 74 של הדוחות, "
      f"{len(schema.PRICE_COLUMNS) - len(schema.PRICE_JOIN_KEYS)} של המחירים, ו-4 של המדד.")
    A(f"- ל-{rep['rows_with_price']:,} שורות ({pct(rep['rows_with_price'] / rep['rows_in_panel'])}) "
      "יש שורת מחיר.")
    A(f"- **`in_index = 1` ב-{rep['rows_in_index']:,} שורות** — בממוצע "
      f"{covered['in_index'].mean():.1f} חברות לרבעון ({covered['in_index'].min()}–"
      f"{covered['in_index'].max()}), {k['first_period']}–{rep['last_period_in_file']}.")
    A(f"- הפלט: `{k['panel_file']}` (כל השורות) ו-`{k['excel_name']}` ({k['excel_rows']}).")
    A(f"- בדיקה צולבת: הרבעונים שנמנו מהטווחים בלבד, בלי הפאנל, זהים לשורות שסומנו 1 "
      f"(עד {rep['last_period_in_file']}): **{rep['flag_cross_check']}**.")
    A("")

    A("## 1. הכלל")
    A("")
    A("רבעון זמין להשקעה כאשר **שני** התאריכים נמצאים **באותו טווח** `from`–`thru` של "
      "אותה חברה (`thru` ריק = עדיין במדד):")
    A("")
    A("```")
    A("quarter_end    = סוף הרבעון מתוך ה-KEY          2025Q1_AAPL → 2025-03-31")
    A("formation_date = סוף החודש, חודשיים אחריו        → 2025-05-31  (= Date של המחירים)")
    A("in_index = 1   אם יש טווח של אותו gvkey שבו  from ≤ quarter_end  וגם  formation_date ≤ thru")
    A("in_index = 0   אחרת")
    A("```")
    A("")
    A("- הרבעון נקרא מה-KEY ונבדק מול `fiscal_period_end_date` לפי כלל הפאנל הבסיסי; אי-התאמה "
      "הייתה עוצרת את הריצה.")
    A("- `thru` עצמו — היום שבו החברה **תצא** מהמדד — לא נכנס לפאנל.")
    A(f"- ⚠️ הקובץ מתחיל ב-{rep['first_date_in_file'].date()}, ולכן כל שורות 1994Q4 "
      f"({k['rows_1994q4']}) מקבלות 0: סוף הרבעון 1994-12-31 לא נמצא באף טווח.")
    A(f"- ⚠️ {count_he(rep['rows_in_index_after_file'], 'שורה אחת', 'שורות')} מרבעון אחרי "
      f"{rep['last_period_in_file']} עם 1: יום ההשקעה אחרי התאריך האחרון בקובץ "
      f"({rep['last_date_in_file'].date()}), ו-`thru` ריק נקרא \"עדיין במדד\".")
    A("")

    A("## 2. יציאה וחזרה — רשומות חוזרות")
    A("")
    rpf = rep["records_per_firm"]
    A("- רשומות לחברה: " + " · ".join(
        f"{count_he(n, 'רשומה אחת', 'רשומות')}: {count_he(c, 'חברה אחת', 'חברות')}"
        for n, c in rpf.items()) + ".")
    A(f"- יציאה וחזרה אמיתיות נשארות טווחים נפרדים. רשומות של אותה חברה שחופפות, או "
      f"שהבאה מתחילה עד {k['gap_days']} יום אחרי סוף הקודמת, הן **שהייה רציפה אחת** ומאוחדות: "
      f"{rep['records_in_file']} רשומות → {len(rep['stays'])} טווחים.")
    A("- `OVERLAPS_PREVIOUS` — שתי סדרות מניות של אותה חברה (GOOG/GOOGL) או החלפת טיקר באותו "
      "יום. `STARTS_RIGHT_AFTER_PREVIOUS` — הרשומה הבאה מתחילה יום אחרי. בלי האיחוד, "
      "`2002Q3_CMCSA` (סוף הרבעון ברשומה אחת, יום ההשקעה בבאה) היה מקבל 0 למרות רצף.")
    merged = k["spells_merged"][["gvkey", "company", "from", "thru", "relation",
                                 "days_after_previous_thru"]].copy()
    for col in ("from", "thru"):
        merged[col] = pd.to_datetime(merged[col]).dt.strftime("%Y-%m-%d").fillna("ריק (עדיין במדד)")
    A(md_table(merged, max_rows=40))

    A("## 3. החיבור למחירים")
    A("")
    A("- `left join` מהדוחות למחירים לפי `(gvkey, KEY)`, אחד-לאחד. שורה בלי מחיר שומרת את "
      "הדוחות שלה ומקבלת עמודות מחיר ריקות. הערכים מועתקים כפי שהם — המומנטום, ההפרשים "
      "והממוצעים חושבו על ההיסטוריה המלאה לפני הבחירה.")
    A(f"- שורות בלי KEY: {rep['rows_without_key']}.")
    A("- חברות בלי אף מחיר — הטיקר שלהן בפונדמנטלס הוא קוד-ממלא של Compustat ולכן פאנל "
      "המחירים לא בחר להן נייר:")
    A(md_table(rep["firms_without_prices"]))

    A("## 4. זמינות לפי רבעון")
    A("")
    A("- כל הרבעונים ב-`tables/in_index_by_quarter.csv`. אלה נקודת הפתיחה של שלב 2: "
      "מתוך `in_index = 1`, כמה עם מחיר, עם תשואה ועם ROIC.")
    A(md_table(pd.concat([covered.head(4), covered.tail(8)])))

    A("## 5. מה לא נכלל")
    A("")
    A(md_table(k["not_included"], max_rows=30))

    A("## 6. מסוף הרבעון הפיסקלי ועד יום ההשקעה (רק `in_index = 1`)")
    A("")
    A("- לפי החלטתך (19/10): מסמנים עכשיו ומחליטים בשלב 2. 2 = רבעון קלנדרי רגיל; 1 ו-0 = "
      "רבעון פיסקלי שנגמר חודש לפני יום ההשקעה או באותו חודש — הדוח כנראה עוד לא פורסם.")
    timing = k["timing"]
    A(md_table(timing[timing["period_year"] == "all"]))

    A("## 7. שחזור")
    A("")
    A(md_table(k["fingerprints"]))
    A(f"- `{k['logs_dir']}/run_manifest.json` — גרסאות, ספירות וה-hashes.")
    A("")
    return "\n".join(L)


def build_one(cfg: dict[str, Any], name: str, write_excel: bool) -> None:
    t0 = time.time()
    out = cfg["_paths"]["output_dir"]
    root = out / cfg["index_panel"]["output_dir"] / name
    dirs = {"tables": root / "tables", "logs": root / "logs"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    log_path = data_io.setup_logging(dirs["logs"])
    run_stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    LOGGER.info("Run %s | index %s | config %s", run_stamp, name, cfg["_meta"]["config_path"])

    sources = {"panel_parquet": out / cfg["output"]["panel_file"],
               "prices_parquet": out / cfg["prices"]["output_file"],
               f"{name}_constituents": I.constituents_path(cfg, name)}
    fp_before = data_io.source_fingerprints(cfg, (), sources)

    # --- build
    table, report = I.build_index_panel(cfg, name)

    # --- tables
    tables: dict[str, pd.DataFrame] = {
        "spells_merged.csv": report["spell_audit"],
        "in_index_by_quarter.csv": validation.index_by_quarter(table, report),
        "not_included.csv": validation.index_not_included(report),
        "timing_gap_census.csv": validation.index_timing_gap_census(table, report),
    }
    for fname, t in tables.items():
        data_io.save_table(t, dirs["tables"] / fname)
    for stale in sorted(dirs["tables"].glob("*.csv")):
        if stale.name not in tables:
            stale.unlink()
            LOGGER.warning("Removed a stale table from an earlier build: %s", stale.name)

    # --- the panel, at fixed paths
    panel_path = data_io.save_panel_parquet(
        table, root / cfg["index_panel"]["file_pattern"].format(name=name), cfg)
    LOGGER.info("Index panel written: %s", panel_path)

    year = int(cfg["output"]["excel_from_year"])
    excel_name = cfg["index_panel"]["excel_file_pattern"].format(name=name, year=year)
    excel_info: dict[str, Any] = {"skipped": True}
    if write_excel:
        mask = pd.Series(report["period_key"]).str[:4].astype(int).to_numpy() >= year
        excel_info = data_io.save_panel_excel(table[mask], root / excel_name, cfg,
                                              sheet=cfg["index_panel"]["excel_sheet"])
        LOGGER.info("Excel written: %s (%d rows from %d)", excel_name,
                    excel_info["rows_written"], year)

    # --- inputs unchanged?
    fp_after = data_io.source_fingerprints(cfg, (), sources)
    unchanged = fp_before[["file", "sha256"]].equals(fp_after[["file", "sha256"]])
    fp_after["unchanged_during_run"] = unchanged
    data_io.save_table(fp_after, dirs["logs"] / "source_fingerprints.csv")

    # --- report
    period = pd.Series(report["period_key"])
    first_period = str(period[table["in_index"].to_numpy() == 1].min())
    rel = lambda p: Path(p).relative_to(out.parent).as_posix()  # noqa: E731
    text = build_report(
        report=report, run_stamp=run_stamp, seconds=time.time() - t0,
        sources_unchanged=unchanged,
        constituents_name=I.constituents_path(cfg, name).name,
        by_quarter=tables["in_index_by_quarter.csv"], first_period=first_period,
        spells_merged=tables["spells_merged.csv"], not_included=tables["not_included.csv"],
        timing=tables["timing_gap_census.csv"],
        gap_days=int(cfg["index_panel"]["contiguous_gap_days"]),
        rows_1994q4=int(period.str.startswith("1994Q4").sum()),
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
        "index": name,
        "seconds": round(time.time() - t0, 2),
        "config_path": str(cfg["_meta"]["config_path"]),
        "config_copy": str(data_io.copy_config(cfg, dirs["logs"])),
        "log_path": str(log_path),
        "python": sys.version.split()[0],
        "pandas": pd.__version__, "numpy": np.__version__,
        "records_in_file": report["records_in_file"],
        "stays": int(len(report["stays"])),
        "firms_in_file": report["firms_in_file"],
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

    LOGGER.info("Finished %s in %.1fs. Report: %s", name, time.time() - t0, report_path)
    if not unchanged:
        raise RuntimeError("An input file changed during the run")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--index", nargs="+", default=["qqq100"],
                        help="one or more names under `indices` in config.yaml")
    parser.add_argument("--no-excel", action="store_true", help="skip the Excel slice")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    cfg = data_io.load_config(args.config)
    for name in args.index:
        build_one(cfg, name, write_excel=not args.no_excel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

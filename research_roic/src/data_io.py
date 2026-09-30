"""I/O layer: configuration, raw-file loading, explicit type conversion, saving.

Rules enforced here
-------------------
* Source files are opened read-only; their SHA-256 hashes are recorded so the
  pipeline can prove they were not modified.
* CSV files are read with ``low_memory=False`` and ``float_precision="round_trip"``.
* Tickers are kept as exact strings (a ticker such as ``NA`` must never become NaN).
* Dates are converted with explicit formats only; unparsable values are reported,
  never guessed.
* Missing values stay NaN. Nothing is filled with zero.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

LOGGER = logging.getLogger("roic.data_io")

REQUIRED_CONFIG_SECTIONS = (
    "project", "paths", "columns", "io", "timing", "panel",
    "relevance", "features", "returns", "validation", "concepts",
)
OUTPUT_SUBDIRS = ("data", "tables", "figures", "reports", "logs")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def project_root() -> Path:
    """Folder that contains ``config.yaml`` (the ``research_roic`` folder)."""
    return Path(__file__).resolve().parents[1]


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load config.yaml and resolve every entry of ``paths`` relative to the file."""
    cfg_path = Path(path) if path is not None else project_root() / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    missing = [s for s in REQUIRED_CONFIG_SECTIONS if s not in cfg]
    if missing:
        raise KeyError(f"config.yaml is missing sections: {missing}")
    base = cfg_path.resolve().parent
    cfg["_meta"] = {"config_path": cfg_path.resolve(), "base_dir": base}
    cfg["_paths"] = {name: (base / rel).resolve() for name, rel in cfg["paths"].items()}
    return cfg


def ensure_output_dirs(cfg: dict[str, Any]) -> dict[str, Path]:
    out = cfg["_paths"]["output_dir"]
    dirs = {name: out / name for name in OUTPUT_SUBDIRS}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def setup_logging(log_dir: Path, run_name: str) -> Path:
    """Log to console and to a UTF-8 file under outputs/logs."""
    log_path = log_dir / f"{run_name}.log"
    logger = logging.getLogger("roic")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return log_path


def copy_config(cfg: dict[str, Any], dest_dir: Path, run_name: str) -> list[Path]:
    """Save a byte-identical copy of the config used in this run."""
    src = Path(cfg["_meta"]["config_path"])
    targets = [dest_dir / f"config_used_{run_name}.yaml", dest_dir / "config_used_latest.yaml"]
    for t in targets:
        shutil.copy2(src, t)
    return targets


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def source_fingerprints(cfg: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name in ("panel_csv", "relevance_csv", "dictionary_docx", "crosscheck_xlsx"):
        p = cfg["_paths"].get(name)
        if p is None or not p.exists():
            rows.append({"file": name, "path": str(p), "exists": False})
            continue
        stat = p.stat()
        rows.append({"file": name, "path": str(p), "exists": True, "bytes": stat.st_size,
                     "modified": pd.Timestamp(stat.st_mtime, unit="s").isoformat(),
                     "sha256": sha256_file(p)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# explicit conversions
# ---------------------------------------------------------------------------
def parse_dates_explicit(values: pd.Series, formats: list[str]) -> tuple[pd.Series, dict[str, Any]]:
    """Parse strings with the given formats, in order. Returns (datetime series, report)."""
    raw = values.astype("string")
    non_empty = raw.notna() & (raw.str.strip() != "")
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    remaining = non_empty.copy()
    by_format: dict[str, int] = {}
    for fmt in formats:
        parsed = pd.to_datetime(raw.where(remaining), format=fmt, errors="coerce")
        hit = remaining & parsed.notna()
        out.loc[hit] = parsed.loc[hit]
        by_format[fmt] = int(hit.sum())
        remaining &= ~hit
    bad = raw[remaining]
    report = {
        "n_rows": int(len(values)),
        "n_missing_or_empty": int((~non_empty).sum()),
        "parsed_by_format": by_format,
        "n_unparsable": int(remaining.sum()),
        "unparsable_examples": bad.dropna().unique()[:10].tolist(),
    }
    return out, report


def parse_period_key(values: pd.Series, regex: str) -> tuple[pd.Series, dict[str, Any]]:
    """'2025Q4' -> Period('2025Q4', 'Q-DEC'). Non-matching strings become NaT and are reported."""
    raw = values.astype("string").str.strip()
    ok = raw.str.fullmatch(regex).fillna(False).astype(bool)
    cleaned = [v if good else None for v, good in zip(raw.tolist(), ok.tolist())]
    periods = pd.Series(pd.PeriodIndex(cleaned, freq="Q"), index=values.index)
    invalid = raw[~ok & raw.notna()]
    report = {"n_rows": int(len(values)), "n_missing": int(raw.isna().sum()),
              "n_invalid": int(len(invalid)), "invalid_examples": invalid.unique()[:10].tolist()}
    return periods, report


def quarter_ordinal(periods: pd.Series) -> pd.Series:
    """Integer quarter counter (Period ordinal); <NA> when the period is missing."""
    idx = pd.PeriodIndex(periods, freq="Q")
    ordinal = pd.Series(idx.asi8, index=periods.index).astype("Int64")
    ordinal[idx.isna()] = pd.NA
    return ordinal


def to_boolean(values: pd.Series) -> tuple[pd.Series, dict[str, Any]]:
    mapping = {True: True, False: False, "True": True, "False": False, "TRUE": True, "FALSE": False}
    mapped = values.map(lambda v: mapping.get(v, pd.NA) if not (isinstance(v, float) and np.isnan(v)) else pd.NA)
    unmapped = values.notna() & mapped.isna()
    return mapped.astype("boolean"), {"n_unmapped": int(unmapped.sum()),
                                      "unmapped_examples": values[unmapped].astype(str).unique()[:5].tolist()}


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------
def read_csv_raw(path: Path, **kwargs: Any) -> pd.DataFrame:
    """The single entry point for CSV reading (mandatory options applied)."""
    return pd.read_csv(path, low_memory=False, float_precision="round_trip", **kwargs)


def find_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Columns named ``<base>.<n>`` whose base also exists, and whether they are identical."""
    rows = []
    for col in df.columns:
        m = re.fullmatch(r"(.+)\.(\d+)", col)
        if not m or m.group(1) not in df.columns:
            continue
        base = m.group(1)
        a, b = df[base], df[col]
        identical = bool(((a == b) | (a.isna() & b.isna())).all())
        rows.append({"duplicate_column": col, "base_column": base, "identical": identical})
    return pd.DataFrame(rows, columns=["duplicate_column", "base_column", "identical"])


def load_panel(cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the quarterly panel with explicit typing. Returns (panel, load report)."""
    c, io = cfg["columns"], cfg["io"]
    path = cfg["_paths"]["panel_csv"]
    enc = io.get("csv_encoding", "utf-8-sig")
    df = read_csv_raw(path, encoding=enc, dtype={c["symbol"]: str, c["key"]: str, c["period_key"]: str})
    report: dict[str, Any] = {"path": str(path), "n_rows": len(df), "n_columns_in_file": df.shape[1],
                              "columns_in_file": df.columns.tolist()}

    # exact ticker strings: re-read identifier columns without NA conversion
    ids = read_csv_raw(path, encoding=enc, usecols=[c["symbol"], c["key"]], dtype=str, keep_default_na=False)
    na_like = int((df[c["symbol"]].isna() & (ids[c["symbol"]] != "")).sum())
    df[c["symbol"]] = ids[c["symbol"]].astype("string").str.strip()
    df[c["key"]] = ids[c["key"]].astype("string").str.strip()
    report["symbols_rescued_from_na_conversion"] = na_like
    report["symbols_empty"] = int((df[c["symbol"]] == "").sum())

    # duplicate columns
    dups = find_duplicate_columns(df)
    report["duplicate_columns"] = dups
    if io.get("drop_identical_duplicate_columns", True) and len(dups):
        drop = dups.loc[dups["identical"], "duplicate_column"].tolist()
        df = df.drop(columns=drop)
        report["dropped_identical_duplicate_columns"] = drop
        non_identical = dups.loc[~dups["identical"], "duplicate_column"].tolist()
        if non_identical:
            LOGGER.warning("Duplicate-named columns that differ from their base were KEPT: %s", non_identical)

    # dates
    report["dates"] = {}
    for col, formats in io["date_formats"].items():
        parsed, rep = parse_dates_explicit(df[col], formats)
        df[f"{col}_raw"] = df[col]
        df[col] = parsed
        report["dates"][col] = rep
        if rep["n_unparsable"]:
            LOGGER.warning("%s: %d values could not be parsed, e.g. %s", col, rep["n_unparsable"],
                           rep["unparsable_examples"])

    # period key
    periods, rep = parse_period_key(df[c["period_key"]], io["period_key_regex"])
    df["period"] = periods
    df["q_ord"] = quarter_ordinal(periods)
    report["period_key"] = rep

    # booleans
    report["booleans"] = {}
    for col in io.get("boolean_columns", []):
        if col in df.columns:
            df[col], rep = to_boolean(df[col])
            report["booleans"][col] = rep

    LOGGER.info("Panel loaded: %d rows x %d columns (%d in file)", len(df), df.shape[1], report["n_columns_in_file"])
    return df, report


def load_relevance(cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load index-membership ranges. Every field is read as an exact string."""
    c, io = cfg["columns"], cfg["io"]
    path = cfg["_paths"]["relevance_csv"]
    raw = read_csv_raw(path, dtype=str, keep_default_na=False, encoding=io.get("csv_encoding", "utf-8-sig"))
    rel = raw.rename(columns={c["relevance_ticker"]: "ticker", c["relevance_start"]: "start_quarter",
                              c["relevance_end"]: "end_quarter"})
    for col in ("ticker", "start_quarter", "end_quarter"):
        rel[col] = rel[col].astype("string").str.strip()
    start, rep_s = parse_period_key(rel["start_quarter"], io["period_key_regex"])
    end_present = rel["end_quarter"] != ""
    end, rep_e = parse_period_key(rel["end_quarter"].where(end_present), io["period_key_regex"])
    rel["start_period"], rel["end_period"] = start, end
    rel["start_ord"], rel["end_ord"] = quarter_ordinal(start), quarter_ordinal(end)
    rel["open_end"] = ~end_present
    rel["range_id"] = np.arange(len(rel))
    report = {
        "path": str(path), "n_rows": len(rel), "columns": raw.columns.tolist(),
        "n_unique_tickers": int(rel["ticker"].nunique()),
        "n_empty_ticker": int((rel["ticker"] == "").sum()),
        "n_invalid_start": rep_s["n_invalid"], "invalid_start_examples": rep_s["invalid_examples"],
        "n_open_end": int((~end_present).sum()),
        "n_invalid_end": int(rep_e["n_invalid"]), "invalid_end_examples": rep_e["invalid_examples"],
    }
    return rel, report


def save_table(df: pd.DataFrame, path: Path, index: bool = False) -> Path:
    """CSV with UTF-8 BOM so Hebrew text opens correctly in Excel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=index, encoding="utf-8-sig")
    return path

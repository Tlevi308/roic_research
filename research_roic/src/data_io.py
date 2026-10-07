"""I/O layer: configuration, parquet loading, explicit type conversion, saving.

Rules enforced here
-------------------
* Source files are opened read-only; their SHA-256 hashes are recorded so the
  pipeline can prove they were not modified.
* ``decimal128`` columns are cast to float64 *inside Arrow*, before pandas sees
  them. Left as ``Decimal`` objects they cost gigabytes, refuse to multiply with
  floats, and raise on division by zero instead of yielding the NaN this design
  depends on.
* A mapped field that is absent is reported, never invented. An unmapped column
  in the file is reported, never a failure.
* Missing values stay NaN. Nothing is filled with zero.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

LOGGER = logging.getLogger("roic.data_io")

REQUIRED_CONFIG_SECTIONS = (
    "project", "paths", "input", "timing", "panel",
    "bands", "decomposition", "calcs", "output", "validation",
)
OUTPUT_SUBDIRS = ("data", "tables", "reports", "logs")


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
    """Create ``output/{data,tables,reports,logs}`` and return the paths.

    ``data`` is the output root itself: the panel sits directly under
    ``output/`` so there is one obvious place to look.
    """
    out = cfg["_paths"]["output_dir"]
    dirs = {name: out / name for name in OUTPUT_SUBDIRS if name != "data"}
    dirs["data"] = out
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def setup_logging(log_dir: Path, run_name: str = "run") -> Path:
    """Log to console and to a UTF-8 file under output/logs (fixed name)."""
    log_path = log_dir / f"{run_name}.log"
    logger = logging.getLogger("roic")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return log_path


def copy_config(cfg: dict[str, Any], dest_dir: Path) -> Path:
    """Save a byte-identical copy of the config used in this run (fixed name)."""
    src = Path(cfg["_meta"]["config_path"])
    target = dest_dir / "config_used.yaml"
    shutil.copy2(src, target)
    return target


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def source_fingerprints(cfg: dict[str, Any]) -> pd.DataFrame:
    """Path / size / mtime / SHA-256 of every source file, for before-after proof."""
    rows = []
    for name in ("input_parquet", "gurufocus_csv"):
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
# loading
# ---------------------------------------------------------------------------
def cast_decimals_to_float64(table: pa.Table) -> pa.Table:
    """Cast every decimal128 column to float64 before pandas sees the table."""
    fields = [f.with_type(pa.float64()) if pa.types.is_decimal(f.type) else f
              for f in table.schema]
    return table.cast(pa.schema(fields))


def mapped_source_columns(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """Logical field name -> list of candidate source columns, in priority order."""
    out: dict[str, list[str]] = {}
    for section in ("identifier_fields", "field_map"):
        for logical, source in (cfg["input"].get(section) or {}).items():
            out[logical] = list(source) if isinstance(source, (list, tuple)) else [source]
    return out


def load_raw(cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the input parquet and report every column, mapped or not.

    Returns the frame (all source columns, decimals already float64) and
    ``input_fields``: one row per logical field and per unmapped source column,
    so a future pull that adds or drops a field is named explicitly.
    """
    path = cfg["_paths"]["input_parquet"]
    table = cast_decimals_to_float64(pq.read_table(path))
    present = set(table.schema.names)
    df = table.to_pandas(date_as_object=False, split_blocks=True, self_destruct=True)
    del table

    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for logical, candidates in mapped_source_columns(cfg).items():
        found = [c for c in candidates if c in present]
        used.update(found)
        rows.append({
            "logical_field": logical,
            "source_candidates": ", ".join(candidates),
            "source_found": ", ".join(found),
            "status": "MAPPED" if found else "MISSING_INPUT",
            "n_non_null": int(sum(df[c].notna().sum() for c in found)) if found else 0,
        })
    for col in sorted(present):
        if col not in used:
            rows.append({"logical_field": "", "source_candidates": "",
                         "source_found": col, "status": "UNMAPPED_SOURCE_COLUMN",
                         "n_non_null": int(df[col].notna().sum())})
    input_fields = pd.DataFrame(rows)
    LOGGER.info("Input loaded: %d rows x %d columns from %s", len(df), df.shape[1], path.name)
    missing = input_fields.loc[input_fields.status == "MISSING_INPUT", "logical_field"].tolist()
    if missing:
        LOGGER.warning("Mapped fields absent from the file (dependent calcs stay NaN): %s",
                       ", ".join(missing))
    return df, input_fields


def load_gurufocus(cfg: dict[str, Any]) -> pd.DataFrame | None:
    """Read the GuruFocus panel for the cross-check, or None when it is absent."""
    path = cfg["_paths"].get("gurufocus_csv")
    if path is None or not path.exists():
        return None
    return pd.read_csv(path, low_memory=False, float_precision="round_trip",
                       encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------
def save_table(df: pd.DataFrame, path: Path, index: bool = False) -> Path:
    """CSV with UTF-8 BOM so Hebrew text opens correctly in Excel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=index, encoding="utf-8-sig")
    return path


def save_panel_parquet(panel: pd.DataFrame, path: Path, cfg: dict[str, Any]) -> Path:
    """Write the panel to a fixed path, overwriting any previous run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path, engine="pyarrow", index=False,
                     compression=cfg["output"]["parquet_compression"],
                     row_group_size=int(cfg["output"]["parquet_row_group_size"]))
    return path


def save_panel_excel(panel: pd.DataFrame, path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Write the panel slice with openpyxl in write-only mode.

    Streaming row by row keeps memory flat; nothing is installed for this.
    Categorical and nullable dtypes are rendered as plain Python values so
    openpyxl never sees a pandas NA it cannot write.
    """
    from openpyxl import Workbook

    limit = int(cfg["output"]["excel_max_rows"])
    truncated = len(panel) > limit
    body = panel.iloc[:limit] if truncated else panel

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("panel")
    ws.append(list(body.columns))
    for row in body.astype(object).where(body.notna(), None).itertuples(index=False, name=None):
        ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()
    return {"path": str(path), "rows_written": int(len(body)),
            "rows_available": int(len(panel)), "truncated": bool(truncated)}


# ---------------------------------------------------------------------------
# small numeric helper used across the package
# ---------------------------------------------------------------------------
def safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """``num / den`` with a zero or missing denominator giving NaN, never inf.

    The masked ``np.divide`` also means no invalid-value warning is ever raised,
    so a genuine future bug is not hidden behind a blanket ``errstate``.
    """
    num = np.asarray(num, dtype="float64")
    den = np.asarray(den, dtype="float64")
    out = np.full(np.broadcast(num, den).shape, np.nan, dtype="float64")
    ok = np.isfinite(den) & (den != 0) & np.isfinite(num)
    np.divide(num, den, out=out, where=ok)
    return out

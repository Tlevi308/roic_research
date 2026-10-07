"""One call from the input parquet to the finished panel.

Load (unless given) -> rename to logical fields -> periods and KEY ->
merge duplicates -> build the quarter grid -> run the registry -> finalize.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import data_io, keys, registry, schema

LOGGER = logging.getLogger("roic.pipeline")


def to_logical_fields(raw: pd.DataFrame, cfg: dict[str, Any]
                      ) -> tuple[pd.DataFrame, set[str]]:
    """Rename source columns to logical field names, coalescing listed fallbacks.

    A mapped field that is absent becomes an all-NaN column and is left out of
    the returned ``available`` set, so every spec that needs it is skipped with
    an explicit reason instead of silently producing nonsense.
    """
    mapping = data_io.mapped_source_columns(cfg)
    out: dict[str, Any] = {}
    available: set[str] = set()
    for logical, candidates in mapping.items():
        found = [c for c in candidates if c in raw.columns]
        if not found:
            out[logical] = np.full(len(raw), np.nan, dtype="float64")
            continue
        series = raw[found[0]]
        for extra in found[1:]:
            series = series.combine_first(raw[extra])
        out[logical] = series.to_numpy() if series.dtype != object else series
        available.add(logical)
    df = pd.DataFrame(out, index=raw.index)
    df["fiscal_period_end_date"] = pd.to_datetime(df["fiscal_period_end_date"])
    for col in ("gvkey", "symbol", "company", "sector"):
        if col in df.columns:
            df[col] = df[col].astype("string")
    return df, available


def build_panel(cfg: dict[str, Any], raw: pd.DataFrame | None = None
                ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the panel. ``raw`` is injectable so tests can perturb the input."""
    report: dict[str, Any] = {}
    if raw is None:
        raw, input_fields = data_io.load_raw(cfg)
    else:
        input_fields = pd.DataFrame(columns=["logical_field", "status"])
    report["rows_in_source"] = int(len(raw))

    df, available = to_logical_fields(raw, cfg)
    del raw
    df = keys.add_period_columns(df, cfg)

    value_columns = [c for c in df.columns
                     if c not in ("gvkey", "symbol", "company", "sector",
                                  "fiscal_period_end_date", "period_key",
                                  "period_year", "period_quarter", "quarter_index")]
    df, duplicates_audit, dedup_report = keys.merge_duplicates(df, cfg, value_columns)
    report["dedup"] = dedup_report
    report["rows_in_panel"] = int(len(df))

    df["KEY"] = keys.build_key(df["period_key"], df["symbol"], cfg)
    df["run_date"] = pd.Timestamp.now().strftime("%Y-%m-%d")

    grid = keys.build_grid(df, cfg)
    report["consecutive_prev_share"] = float(grid.prev_ok.mean())

    ctx = registry.Ctx(cfg=cfg, grid=grid)
    for col in schema.IDENTITY_COLUMNS:
        ctx.data[col] = df[col].to_numpy() if col != "fiscal_period_end_date" \
            else df[col].to_numpy("datetime64[ns]")
    for col in value_columns:
        ctx.data[col] = df[col].to_numpy(dtype="float64")

    disabled = set(cfg["calcs"].get("disabled") or [])
    column_status = registry.run_registry(ctx, available, _disabled_specs(disabled))

    panel = schema.finalize(ctx.data, n_rows=len(df))
    report.update({
        "input_fields": input_fields,
        "duplicates_audit": duplicates_audit,
        "column_status": column_status,
        "explanation_catalogue": ctx.inter.get("explanation_catalogue"),
        "grid": grid,
        "panel_keys": df[["gvkey", "period_key", "period_year", "quarter_index"]],
    })
    LOGGER.info("Panel built: %d rows x %d columns", len(panel), panel.shape[1])
    return panel, report


def _disabled_specs(disabled_columns: set[str]) -> set[str]:
    """``calcs.disabled`` names columns; the engine switches whole specs off."""
    names = set()
    for spec in registry.REGISTRY:
        if disabled_columns & set(spec.produces):
            names.add(spec.name)
    return names | {d for d in disabled_columns
                    if d in {s.name for s in registry.REGISTRY}}

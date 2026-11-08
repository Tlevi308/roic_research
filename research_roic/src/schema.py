"""The output contract: column order, dtypes and declared category levels.

Rules enforced here
-------------------
* The columns the researcher asked for, in the order they asked for them.
  This module is the single place that order is written down.
* The frame is built in **one** construction. Seventy-odd incremental
  ``df[col] = ...`` assignments at 1.9M rows repeatedly consolidate blocks and
  can triple peak memory.
* Label columns are categoricals carrying their whole declared level set, so a
  level that never occurs is still a declared level and the census can report it
  as unreached.
* The price panel (``output/prices.parquet``) has its own contract,
  ``PRICE_COLUMNS``, kept here so both contracts live in one file. The column
  names are the researcher's own, spelling included.
* Both panels carry ``gvkey`` right after ``KEY`` (researcher 2026-10-27): a
  six-digit string with its leading zeros, never a number, so it joins
  against any Compustat file as it stands there.
* The index panel (``output/index_panels/<name>/``) is derived from the two:
  every base column, every price column but the join key, then the four
  columns of the index build. It has no hand-written column list.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import labels as L

# ---- the contract --------------------------------------------------------
PANEL_COLUMNS: tuple[str, ...] = (
    "KEY", "gvkey", "symbol", "company", "sector", "fiscal_period_end_date", "run_date",
    "calc_tax_expense_quarterly", "pretax_income", "calc_raw_tax_rate_quarterly",
    "ebit", "calc_nopat_quarterly", "calc_nopat_decomposition_status",
    "calc_nopat_change_quarterly", "calc_nopat_change_direction",
    "calc_nopat_ebit_contribution", "calc_nopat_tax_contribution",
    "calc_nopat_decomposition_residual", "calc_nopat_ebit_effect",
    "calc_nopat_tax_effect", "calc_nopat_effect_combination",
    "total_current_assets", "total_current_liabilities", "goodwill", "net_ppe",
    "calc_ic_raw", "calc_average_ic_raw_quarterly",
    "calc_roic_pretax_quarterly_ic_raw", "calc_roic_pretax_annualized_ic_raw",
    "calc_roic_posttax_quarterly_ic_raw", "calc_roic_posttax_annualized_ic_raw",
    "calc_roic_decomposition_status", "calc_roic_posttax_change_quarterly",
    "calc_roic_posttax_change_direction", "calc_roic_ebit_contribution",
    "calc_roic_tax_contribution", "calc_roic_ic_contribution",
    "calc_roic_decomposition_residual", "calc_roic_ebit_effect",
    "calc_roic_tax_effect", "calc_roic_ic_effect", "calc_roic_effect_combination",
    "calc_ebit_movement", "calc_tax_rate_movement", "calc_ic_movement",
    "calc_raw_movement_combination", "calc_roic_has_opposing_effects",
    "calc_roic_positive_driver_count", "calc_roic_negative_driver_count",
    "calc_roic_neutral_driver_count", "calc_roic_active_driver_count",
    "calc_roic_effect_structure", "calc_roic_total_absolute_contribution",
    "calc_roic_ebit_absolute_share", "calc_roic_tax_absolute_share",
    "calc_roic_ic_absolute_share", "calc_roic_dominant_driver",
    "calc_roic_dominant_driver_effect", "calc_roic_offset_ratio",
    "calc_ebit_sign_regime", "calc_nopat_sign_regime",
    "calc_tax_rate_quality_flag", "calc_roic_quality_flag", "calc_roic_explanation",
    "short_term_debt", "long_term_debt", "calc_debt_value_quarterly", "equity",
    "calc_debt_to_equity_quarterly", "market_cap", "free_cash_flow_quarterly",
    "calc_free_cash_flow_ttm", "calc_p_to_fcf_quarterly", "calc_p_to_fcf_ttm",
)

# columns read straight from the input, no calculation
PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "pretax_income", "ebit", "total_current_assets", "total_current_liabilities",
    "goodwill", "net_ppe", "short_term_debt", "long_term_debt", "market_cap",
)

IDENTITY_COLUMNS: tuple[str, ...] = (
    "KEY", "gvkey", "symbol", "company", "sector", "fiscal_period_end_date", "run_date",
)

LABEL_LEVELS: dict[str, tuple[str, ...]] = {
    "calc_nopat_decomposition_status": L.NOPAT_STATUS_LEVELS,
    "calc_nopat_change_direction": L.DIRECTION_LEVELS,
    "calc_nopat_ebit_effect": L.EFFECT_LEVELS,
    "calc_nopat_tax_effect": L.EFFECT_LEVELS,
    "calc_nopat_effect_combination": L.nopat_effect_combination_levels(),
    "calc_roic_decomposition_status": L.ROIC_STATUS_LEVELS,
    "calc_roic_posttax_change_direction": L.DIRECTION_LEVELS,
    "calc_roic_ebit_effect": L.EFFECT_LEVELS,
    "calc_roic_tax_effect": L.EFFECT_LEVELS,
    "calc_roic_ic_effect": L.EFFECT_LEVELS,
    "calc_roic_effect_combination": L.roic_effect_combination_levels(),
    "calc_ebit_movement": L.MOVEMENT_LEVELS,
    "calc_tax_rate_movement": L.MOVEMENT_LEVELS,
    "calc_ic_movement": L.MOVEMENT_LEVELS,
    "calc_raw_movement_combination": L.raw_movement_combination_levels(),
    "calc_roic_effect_structure": L.STRUCTURE_LEVELS,
    "calc_roic_dominant_driver": L.DOMINANT_LEVELS,
    "calc_roic_dominant_driver_effect": L.EFFECT_LEVELS,
    "calc_ebit_sign_regime": L.sign_regime_levels(),
    "calc_nopat_sign_regime": L.sign_regime_levels(),
    "calc_tax_rate_quality_flag": L.TAX_FLAG_LEVELS,
    "calc_roic_quality_flag": L.ROIC_FLAG_LEVELS,
}

COUNT_COLUMNS: tuple[str, ...] = (
    "calc_roic_positive_driver_count", "calc_roic_negative_driver_count",
    "calc_roic_neutral_driver_count", "calc_roic_active_driver_count",
)
BOOLEAN_COLUMNS: tuple[str, ...] = ("calc_roic_has_opposing_effects",)
STRING_COLUMNS: tuple[str, ...] = ("KEY", "gvkey", "symbol", "company", "sector", "run_date")
DATE_COLUMNS: tuple[str, ...] = ("fiscal_period_end_date",)


def panel_column_order() -> list[str]:
    return list(PANEL_COLUMNS)


def declared_dtype(column: str) -> str:
    if column in LABEL_LEVELS or column == "calc_roic_explanation":
        return "category"
    if column in COUNT_COLUMNS:
        return "Int8"
    if column in BOOLEAN_COLUMNS:
        return "boolean"
    if column in STRING_COLUMNS:
        return "string"
    if column in DATE_COLUMNS:
        return "datetime64[ns]"
    return "float64"


def finalize(data: dict[str, Any], n_rows: int) -> pd.DataFrame:
    """Build the panel in one construction, in contract order and dtype."""
    missing = [c for c in PANEL_COLUMNS if c not in data]
    if missing:
        raise KeyError(f"schema columns not produced: {missing}")
    columns: dict[str, Any] = {}
    for col in PANEL_COLUMNS:
        value = data[col]
        dtype = declared_dtype(col)
        if dtype == "float64":
            columns[col] = np.asarray(value, dtype="float64")
        elif dtype == "string":
            columns[col] = pd.array(pd.Series(value).astype("string"), dtype="string")
        elif dtype == "datetime64[ns]":
            columns[col] = pd.to_datetime(pd.Series(value)).to_numpy("datetime64[ns]")
        else:
            columns[col] = value
    panel = pd.DataFrame(columns, copy=False)
    if len(panel) != n_rows:
        raise ValueError(f"panel has {len(panel)} rows, expected {n_rows}")
    return panel


# ---- the price contract --------------------------------------------------
PRICE_COLUMNS: tuple[str, ...] = (
    "KEY", "gvkey", "Date", "Ticker", "Close", "Quaterly_return", "MomentomQ3",
    "ipodate", "dldte",
)
PRICE_STRING_COLUMNS: tuple[str, ...] = ("KEY", "gvkey", "Ticker")
PRICE_DATE_COLUMNS: tuple[str, ...] = ("Date", "ipodate", "dldte")


def price_declared_dtype(column: str) -> str:
    if column in PRICE_STRING_COLUMNS:
        return "string"
    if column in PRICE_DATE_COLUMNS:
        return "datetime64[ns]"
    return "float64"


def finalize_prices(data: dict[str, Any], n_rows: int) -> pd.DataFrame:
    """Build the price panel in one construction, in contract order and dtype."""
    missing = [c for c in PRICE_COLUMNS if c not in data]
    if missing:
        raise KeyError(f"price columns not produced: {missing}")
    columns: dict[str, Any] = {}
    for col in PRICE_COLUMNS:
        dtype = price_declared_dtype(col)
        if dtype == "float64":
            columns[col] = np.asarray(data[col], dtype="float64")
        elif dtype == "string":
            columns[col] = pd.array(pd.Series(data[col]).astype("string"), dtype="string")
        else:
            columns[col] = pd.to_datetime(pd.Series(data[col])).to_numpy("datetime64[ns]")
    prices = pd.DataFrame(columns, copy=False)
    if len(prices) != n_rows:
        raise ValueError(f"price panel has {len(prices)} rows, expected {n_rows}")
    return prices


# ---- the index-panel contract --------------------------------------------
# Every base column, then every price column except the join key, then the
# four columns the index build adds. Derived, so no column is listed twice.
PRICE_JOIN_KEYS: tuple[str, ...] = ("gvkey", "KEY")
INDEX_FLAG_COLUMNS: tuple[str, ...] = (
    "quarter_end", "formation_date", "in_index", "months_fiscal_end_to_formation",
)
INDEX_PANEL_COLUMNS: tuple[str, ...] = (
    PANEL_COLUMNS
    + tuple(c for c in PRICE_COLUMNS if c not in PRICE_JOIN_KEYS)
    + INDEX_FLAG_COLUMNS
)


def finalize_index_panel(joined: pd.DataFrame, flags: dict[str, Any]) -> pd.DataFrame:
    """The joined frame in contract order, with the flag columns in their dtypes.

    The base and price columns keep the dtypes they were read with, so a value
    in the index panel is the very value in ``panel.parquet``/``prices.parquet``.
    """
    missing = [c for c in INDEX_PANEL_COLUMNS
               if c not in joined.columns and c not in flags]
    if missing:
        raise KeyError(f"index-panel columns not produced: {missing}")
    columns: dict[str, Any] = {c: joined[c] for c in INDEX_PANEL_COLUMNS
                               if c not in INDEX_FLAG_COLUMNS}
    columns["quarter_end"] = pd.to_datetime(pd.Series(flags["quarter_end"],
                                                      index=joined.index)).astype("datetime64[ns]")
    columns["formation_date"] = pd.to_datetime(pd.Series(flags["formation_date"],
                                                         index=joined.index)).astype("datetime64[ns]")
    columns["in_index"] = pd.Series(np.asarray(flags["in_index"], dtype="int8"), index=joined.index)
    columns["months_fiscal_end_to_formation"] = pd.Series(
        flags["months_fiscal_end_to_formation"], index=joined.index).astype("Int8")
    out = pd.DataFrame(columns, index=joined.index)[list(INDEX_PANEL_COLUMNS)]
    return out.reset_index(drop=True)


def schema_contract(column_status: pd.DataFrame) -> pd.DataFrame:
    """The contract as a table: order, dtype, declared levels, producing spec."""
    status = column_status.set_index("column") if len(column_status) else None
    rows = []
    for position, col in enumerate(PANEL_COLUMNS, start=1):
        levels = LABEL_LEVELS.get(col)
        row = {
            "position": position,
            "column": col,
            "dtype": declared_dtype(col),
            "n_declared_levels": len(levels) if levels else "",
            "source": ("input field" if col in PASSTHROUGH_FIELDS
                       else "identity" if col in IDENTITY_COLUMNS else "calc"),
            "spec": "", "status": "", "reason": "", "note": "",
        }
        if status is not None and col in status.index:
            rec = status.loc[col]
            row.update({"spec": rec["spec"], "status": rec["status"],
                        "reason": rec["reason"], "note": rec["note"]})
        rows.append(row)
    return pd.DataFrame(rows)

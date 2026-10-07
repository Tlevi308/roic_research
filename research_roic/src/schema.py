"""The output contract: column order, dtypes and declared category levels.

Rules enforced here
-------------------
* The 71 columns the researcher asked for, in the order they asked for them.
  This module is the single place that order is written down.
* The frame is built in **one** construction. Seventy-one incremental
  ``df[col] = ...`` assignments at 1.9M rows repeatedly consolidate blocks and
  can triple peak memory.
* Label columns are categoricals carrying their whole declared level set, so a
  level that never occurs is still a declared level and the census can report it
  as unreached.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import labels as L

# ---- the contract --------------------------------------------------------
PANEL_COLUMNS: tuple[str, ...] = (
    "KEY", "symbol", "company", "sector", "fiscal_period_end_date", "run_date",
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
    "calc_debt_to_equity_quarterly", "market_cap", "free_cash_flow",
    "calc_free_cash_flow_ttm", "calc_ev_to_fcf_quarterly",
)

# columns read straight from the input, no calculation
PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "pretax_income", "ebit", "total_current_assets", "total_current_liabilities",
    "goodwill", "net_ppe", "short_term_debt", "long_term_debt", "market_cap",
)

IDENTITY_COLUMNS: tuple[str, ...] = (
    "KEY", "symbol", "company", "sector", "fiscal_period_end_date", "run_date",
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
STRING_COLUMNS: tuple[str, ...] = ("KEY", "symbol", "company", "sector", "run_date")
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

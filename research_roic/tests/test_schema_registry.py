"""The contract, and the two ways the pipeline is meant to be extended."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import data_io, pipeline, registry, schema
from tests.conftest import ROOT, build, build_with_report, make_source

REQUESTED = (
    "KEY symbol company sector fiscal_period_end_date run_date "
    "calc_tax_expense_quarterly pretax_income calc_raw_tax_rate_quarterly ebit "
    "calc_nopat_quarterly calc_nopat_decomposition_status calc_nopat_change_quarterly "
    "calc_nopat_change_direction calc_nopat_ebit_contribution calc_nopat_tax_contribution "
    "calc_nopat_decomposition_residual calc_nopat_ebit_effect calc_nopat_tax_effect "
    "calc_nopat_effect_combination total_current_assets total_current_liabilities goodwill "
    "net_ppe calc_ic_raw calc_average_ic_raw_quarterly calc_roic_pretax_quarterly_ic_raw "
    "calc_roic_pretax_annualized_ic_raw calc_roic_posttax_quarterly_ic_raw "
    "calc_roic_posttax_annualized_ic_raw calc_roic_decomposition_status "
    "calc_roic_posttax_change_quarterly calc_roic_posttax_change_direction "
    "calc_roic_ebit_contribution calc_roic_tax_contribution calc_roic_ic_contribution "
    "calc_roic_decomposition_residual calc_roic_ebit_effect calc_roic_tax_effect "
    "calc_roic_ic_effect calc_roic_effect_combination calc_ebit_movement "
    "calc_tax_rate_movement calc_ic_movement calc_raw_movement_combination "
    "calc_roic_has_opposing_effects calc_roic_positive_driver_count "
    "calc_roic_negative_driver_count calc_roic_neutral_driver_count "
    "calc_roic_active_driver_count calc_roic_effect_structure "
    "calc_roic_total_absolute_contribution calc_roic_ebit_absolute_share "
    "calc_roic_tax_absolute_share calc_roic_ic_absolute_share calc_roic_dominant_driver "
    "calc_roic_dominant_driver_effect calc_roic_offset_ratio calc_ebit_sign_regime "
    "calc_nopat_sign_regime calc_tax_rate_quality_flag calc_roic_quality_flag "
    "calc_roic_explanation short_term_debt long_term_debt calc_debt_value_quarterly "
    "equity calc_debt_to_equity_quarterly market_cap free_cash_flow "
    "calc_free_cash_flow_ttm calc_ev_to_fcf_quarterly"
).split()


def test_column_order_is_exactly_the_requested_list():
    assert list(schema.PANEL_COLUMNS) == REQUESTED
    assert len(set(schema.PANEL_COLUMNS)) == len(schema.PANEL_COLUMNS)


def test_panel_matches_the_contract(synthetic):
    assert list(synthetic.columns) == list(schema.PANEL_COLUMNS)
    for col in schema.PANEL_COLUMNS:
        declared = schema.declared_dtype(col)
        actual = str(synthetic[col].dtype)
        if declared == "category":
            assert actual == "category", col
        elif declared == "string":
            assert actual.startswith("string"), col
        else:
            assert actual == declared, col


def test_every_column_is_produced_by_exactly_one_spec():
    owners: dict[str, list[str]] = {}
    for spec in registry.REGISTRY:
        for col in spec.produces:
            owners.setdefault(col, []).append(spec.name)
    doubled = {c: o for c, o in owners.items() if len(o) > 1}
    assert doubled == {}
    calc_columns = set(schema.PANEL_COLUMNS) - set(schema.PASSTHROUGH_FIELDS) \
        - set(schema.IDENTITY_COLUMNS)
    assert calc_columns == set(owners)


def test_registry_has_no_cycles_and_orders_dependencies():
    order = registry._in_dependency_order()
    assert len(order) == len(registry.REGISTRY)
    produced_by = {c: s.name for s in registry.REGISTRY for c in s.produces}
    seen: set[str] = set()
    for spec in order:
        for req in spec.requires:
            owner = produced_by.get(req)
            if owner is not None and owner != spec.name:
                assert owner in seen, f"{spec.name} runs before {owner}"
        seen.add(spec.name)


def test_disabling_a_spec_keeps_the_column_and_empties_it(cfg, seed):
    """`calcs.disabled` is a decision, not a schema change."""
    local = copy.deepcopy(cfg)
    local["calcs"]["disabled"] = list(local["calcs"]["disabled"]) + ["calc_ic_raw"]
    panel, report = pipeline.build_panel(local, raw=make_source(seed))
    assert list(panel.columns) == list(schema.PANEL_COLUMNS)
    assert int(panel["calc_ic_raw"].notna().sum()) == 0
    status = report["column_status"].set_index("column")
    assert "DISABLED" in status.loc["calc_ic_raw", "reason"]
    # and everything downstream is skipped with the cause named
    assert "UPSTREAM_SKIPPED" in status.loc["calc_roic_posttax_quarterly_ic_raw", "reason"]


def test_a_missing_input_field_empties_its_columns_and_is_reported(cfg, seed):
    """Dropping atq is what the equity columns looked like before 2026-10-05."""
    src = make_source(seed).drop(columns=["atq"])
    panel, report = pipeline.build_panel(cfg, raw=src)
    assert list(panel.columns) == list(schema.PANEL_COLUMNS)
    assert int(panel["equity"].notna().sum()) == 0
    assert int(panel["calc_debt_to_equity_quarterly"].notna().sum()) == 0
    status = report["column_status"].set_index("column")
    assert "MISSING_INPUT:total_assets" in status.loc["equity", "reason"]


def test_adding_the_input_back_lights_the_columns_up_with_no_code_change(cfg, seed):
    """equity = atq - ltq the moment the fields are present."""
    panel = build(cfg, make_source(seed))
    src = make_source(seed)
    assert np.allclose(panel["equity"].to_numpy(),
                       (src["atq"] - src["ltq"]).to_numpy())
    expected = panel["calc_debt_value_quarterly"] / panel["equity"]
    assert np.allclose(panel["calc_debt_to_equity_quarterly"].to_numpy(),
                       expected.to_numpy(), rtol=1e-12)


def test_an_unmapped_source_column_does_not_break_the_loader(cfg, seed):
    src = make_source(seed)
    src["some_future_field"] = 1.0
    panel, _ = pipeline.build_panel(cfg, raw=src)
    assert list(panel.columns) == list(schema.PANEL_COLUMNS)


def test_schema_contract_table_covers_every_column(cfg, seed):
    _, report = build_with_report(cfg, make_source(seed))
    contract = schema.schema_contract(report["column_status"])
    assert list(contract["column"]) == list(schema.PANEL_COLUMNS)
    assert contract["position"].tolist() == list(range(1, len(schema.PANEL_COLUMNS) + 1))


def test_parquet_round_trip_preserves_dtypes(cfg, synthetic, tmp_path):
    path = tmp_path / "panel.parquet"
    data_io.save_panel_parquet(synthetic, path, cfg)
    back = pd.read_parquet(path)
    assert list(back.columns) == list(synthetic.columns)
    for col in schema.LABEL_LEVELS:
        assert str(back[col].dtype) == "category", col
        assert list(back[col].cat.categories) == list(synthetic[col].cat.categories), col
    for col in schema.COUNT_COLUMNS:
        assert str(back[col].dtype) == "Int8", col
    assert str(back["calc_roic_has_opposing_effects"].dtype) == "boolean"


def test_excel_writer_handles_nullable_and_categorical(cfg, synthetic, tmp_path):
    info = data_io.save_panel_excel(synthetic.head(20), tmp_path / "slice.xlsx", cfg)
    assert info["rows_written"] == 20
    assert (tmp_path / "slice.xlsx").stat().st_size > 0


def test_no_lookahead_anywhere_in_the_source():
    """No module may read a future row."""
    offenders = []
    pattern = re.compile(r"shift\(\s*-|\.shift\(-1\)|iloc\[\s*\w+\s*\+\s*1\s*\]")
    for path in sorted((ROOT / "src").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{i}")
    assert offenders == []


def test_output_paths_are_fixed_and_overwritten(cfg):
    """A new run must land on the same names, not a timestamped family."""
    assert cfg["output"]["panel_file"] == "panel.parquet"
    assert "{year}" in cfg["output"]["excel_file_pattern"]
    assert int(cfg["output"]["excel_from_year"]) >= 1961

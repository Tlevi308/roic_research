"""The measured facts, pinned, so a new data pull cannot change them quietly."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import schema, validation

# measured on data/data.parquet as of 2026-10-05; tolerance from the config
COVERAGE = {
    "ebit": 0.767,
    "calc_raw_tax_rate_quarterly": 0.805,
    "calc_nopat_quarterly": 0.763,
    "calc_ic_raw": 0.610,
    "calc_average_ic_raw_quarterly": 0.571,
    "calc_roic_pretax_quarterly_ic_raw": 0.563,
    "calc_roic_posttax_quarterly_ic_raw": 0.561,
    "calc_debt_value_quarterly": 0.750,
    "equity": 0.736,
    "calc_debt_to_equity_quarterly": 0.730,
    "market_cap": 0.247,
}
CONSECUTIVE_SHARE = 0.977
NEGATIVE_IC_ROWS = 87_637
EXACT_CAST_LIMIT = 2.0 ** 53 / 1e4


def _tolerance(cfg) -> float:
    return float(cfg["validation"]["coverage_tolerance_pp"]) / 100.0


def test_real_coverage_matches_the_measurement(real_panel, cfg):
    panel, _ = real_panel
    tol = _tolerance(cfg)
    for col, expected in COVERAGE.items():
        got = float(panel[col].notna().mean())
        assert abs(got - expected) <= tol, f"{col}: {got:.4f} vs {expected:.3f}"


def test_real_consecutive_share(real_panel, cfg):
    _, report = real_panel
    assert abs(report["consecutive_prev_share"] - CONSECUTIVE_SHARE) <= _tolerance(cfg)


def test_real_period_rule_holds_everywhere(real_panel):
    """(fiscal end - 2 months) -> calendar quarter, on every single row."""
    panel, report = real_panel
    end = pd.to_datetime(panel["fiscal_period_end_date"])
    shifted = end - pd.DateOffset(months=2)
    rule = shifted.dt.year.astype(str) + "Q" + shifted.dt.quarter.astype(str)
    assert (rule.to_numpy() == report["panel_keys"]["period_key"].to_numpy()).all()


def test_real_january_and_february_roll_back_a_year(real_panel):
    """A fiscal period ending in February belongs to the previous year's Q4."""
    panel, report = real_panel
    end = pd.to_datetime(panel["fiscal_period_end_date"])
    pk = report["panel_keys"]["period_key"].to_numpy()
    for month in (1, 2):
        mask = (end.dt.month == month).to_numpy()
        assert mask.any()
        years = end.dt.year.to_numpy()[mask]
        expected = np.array([f"{y - 1}Q4" for y in years])
        assert (pk[mask] == expected).all(), month


def test_real_month_to_quarter_map(real_panel):
    """Quarter-end months 3,4,5 -> Q1 and so on; 12,1,2 -> Q4."""
    panel, report = real_panel
    end = pd.to_datetime(panel["fiscal_period_end_date"])
    quarter = report["panel_keys"]["period_key"].str[-1].astype(int).to_numpy()
    expected = {3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2,
                9: 3, 10: 3, 11: 3, 12: 4, 1: 4, 2: 4}
    for month, want in expected.items():
        mask = (end.dt.month == month).to_numpy()
        assert mask.any(), month
        assert (quarter[mask] == want).all(), month


def test_real_all_identity_checks_pass(real_panel):
    panel, report = real_panel
    checks = validation.identity_checks(panel, report["grid"], report["panel_keys"])
    failed = checks[checks["n_failed"] > 0]
    assert failed.empty, failed.to_string(index=False)
    assert (checks["n_tested"] > 0).all()


def test_real_residuals_are_machine_zero(real_panel):
    """Shapley efficiency on 1.9M real rows, relative to the size of the change."""
    panel, _ = real_panel
    for residual_col, change_col in (
            ("calc_nopat_decomposition_residual", "calc_nopat_change_quarterly"),
            ("calc_roic_decomposition_residual", "calc_roic_posttax_change_quarterly")):
        residual = panel[residual_col].to_numpy()
        change = panel[change_col].to_numpy()
        tested = np.isfinite(residual)
        relative = np.abs(residual[tested]) / np.maximum(1.0, np.abs(change[tested]))
        assert np.percentile(relative, 99.99) <= 1e-12
        assert relative.max() <= 1e-9


def test_real_source_cast_is_lossless(real_panel):
    """Every value read from the file is far inside the exact decimal->float range."""
    panel, _ = real_panel
    audit = validation.precision_audit(panel)
    source = audit[audit["role"] == "source"]
    assert len(source) == len(schema.PASSTHROUGH_FIELDS)
    assert source["max_abs"].max() <= EXACT_CAST_LIMIT
    assert all(v is True for v in source["within_limit"])


def test_real_negative_invested_capital_is_flagged_not_removed(real_panel):
    panel, _ = real_panel
    ic = panel["calc_ic_raw"].to_numpy()
    assert int((ic < 0).sum()) == NEGATIVE_IC_ROWS
    flagged = panel.loc[panel["calc_roic_quality_flag"] == "NEGATIVE_IC_MECHANICAL_ONLY"]
    assert len(flagged) > 0
    assert (flagged["calc_roic_decomposition_status"] == "VALID").all()


def test_real_no_column_is_entirely_empty_except_the_disabled_three(real_panel):
    panel, _ = real_panel
    empty = [c for c in panel.columns if int(panel[c].notna().sum()) == 0]
    assert sorted(empty) == sorted(["free_cash_flow", "calc_free_cash_flow_ttm",
                                    "calc_ev_to_fcf_quarterly"])


def test_real_sector_is_the_raw_code(real_panel):
    """gsector is shown exactly as it appears in the data, untranslated."""
    panel, _ = real_panel
    present = panel["sector"].dropna().unique()
    assert set(present) <= {"10", "15", "20", "25", "30", "35", "40", "45",
                            "50", "55", "60"}


def test_real_run_date_is_a_single_iso_date(real_panel):
    panel, _ = real_panel
    values = panel["run_date"].dropna().unique()
    assert len(values) == 1
    pd.Timestamp(values[0])

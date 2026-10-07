"""The NOPAT status and the ROIC status are different gates, on purpose."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tests.conftest import build, make_source


def _blank_capital(src: pd.DataFrame, row: int) -> pd.DataFrame:
    """Remove the capital base while leaving the income statement intact."""
    out = src.copy()
    out.loc[out.index[row], "actq"] = np.nan
    return out


def test_ebit_and_tax_movements_survive_a_missing_capital_base(cfg, seed):
    """A zero or missing capital base says nothing about whether EBIT rose."""
    panel = build(cfg, _blank_capital(make_source(seed), 4))
    assert panel.loc[4, "calc_nopat_decomposition_status"] == "VALID"
    assert panel.loc[4, "calc_ebit_movement"] in ("UP", "DOWN", "FLAT")
    assert panel.loc[4, "calc_tax_rate_movement"] in ("UP", "DOWN", "FLAT")


def test_ic_movement_and_combination_are_gated_on_roic_status(cfg, seed):
    panel = build(cfg, _blank_capital(make_source(seed), 4))
    assert panel.loc[4, "calc_roic_decomposition_status"] != "VALID"
    assert panel.loc[4, "calc_ic_movement"] == "UNCLASSIFIED"
    assert panel.loc[4, "calc_raw_movement_combination"] == "UNCLASSIFIED"


def test_nopat_bridge_survives_zero_and_negative_capital(cfg, seed):
    """The NOPAT bridge is not gated on invested capital at all."""
    src = make_source(seed)
    for row in (4, 5):
        src.loc[src.index[row], ["actq", "lctq", "ppentq", "gdwlq"]] = [10.0, 500.0, 0.0, 0.0]
    panel = build(cfg, src)
    assert panel.loc[5, "calc_average_ic_raw_quarterly"] < 0
    assert panel.loc[5, "calc_nopat_decomposition_status"] == "VALID"
    assert np.isfinite(panel.loc[5, "calc_nopat_ebit_contribution"])
    assert np.isfinite(panel.loc[5, "calc_nopat_tax_contribution"])


def test_sign_regimes_survive_a_missing_capital_base(cfg, seed):
    """Both regimes depend only on the income statement."""
    panel = build(cfg, _blank_capital(make_source(seed), 4))
    assert panel.loc[4, "calc_ebit_sign_regime"] != "UNCLASSIFIED"
    assert panel.loc[4, "calc_nopat_sign_regime"] != "UNCLASSIFIED"


def test_driver_statistics_are_na_unless_roic_is_valid(cfg, seed):
    panel = build(cfg, _blank_capital(make_source(seed), 4))
    for col in ("calc_roic_positive_driver_count", "calc_roic_negative_driver_count",
                "calc_roic_neutral_driver_count", "calc_roic_active_driver_count",
                "calc_roic_has_opposing_effects", "calc_roic_total_absolute_contribution",
                "calc_roic_ebit_absolute_share", "calc_roic_offset_ratio"):
        assert pd.isna(panel.loc[4, col]), col
    assert panel.loc[4, "calc_roic_effect_structure"] == "UNCLASSIFIED"
    assert panel.loc[4, "calc_roic_dominant_driver"] == "UNCLASSIFIED"


def test_roic_valid_requires_three_consecutive_quarters(cfg, seed):
    """Row 1 has no average IC at t-1, so the first classifiable row is row 2."""
    panel = build(cfg, make_source(seed, tickers=("AAA",), n_quarters=5))
    assert panel.loc[0, "calc_roic_decomposition_status"] == "UNCLASSIFIED_NONCONSECUTIVE"
    assert panel.loc[1, "calc_roic_decomposition_status"] == "UNCLASSIFIED_MISSING_DATA"
    assert panel.loc[2, "calc_roic_decomposition_status"] == "VALID"
    # the NOPAT bridge needs only two
    assert panel.loc[1, "calc_nopat_decomposition_status"] == "VALID"


def test_zero_ic_status_is_distinct_from_missing(cfg, seed):
    """A capital base of exactly zero is its own status, checked after MISSING."""
    src = make_source(seed, tickers=("AAA",), n_quarters=6)
    for row in (2, 3):
        src.loc[src.index[row], ["actq", "lctq", "ppentq", "gdwlq"]] = [100.0, 100.0, 0.0, 0.0]
    panel = build(cfg, src)
    assert panel.loc[3, "calc_average_ic_raw_quarterly"] == 0.0
    assert panel.loc[3, "calc_roic_decomposition_status"] == "UNCLASSIFIED_ZERO_IC"


def test_dominant_driver_effect_distinguishes_none_from_balanced(cfg, seed):
    """NONE is an honest NEUTRAL; BALANCED has no single driver to attribute to."""
    panel = build(cfg, make_source(seed))
    rows = panel[panel.calc_roic_dominant_driver == "BALANCED"]
    if len(rows):
        assert (rows.calc_roic_dominant_driver_effect == "UNCLASSIFIED").all()
    rows = panel[panel.calc_roic_dominant_driver == "NONE"]
    if len(rows):
        assert (rows.calc_roic_dominant_driver_effect == "NEUTRAL").all()

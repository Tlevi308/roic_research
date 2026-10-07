"""The guard at -100%: an even power must not return the sign."""
from __future__ import annotations

import numpy as np

from src import levels


def test_annualized_matches_compounding():
    r = np.array([-0.5, -0.1, 0.0, 0.0125, 0.05, 2.0])
    assert np.allclose(levels.annualize(r), (1 + r) ** 4 - 1, rtol=1e-14)


def test_annualization_guard_at_minus_one_hundred_percent():
    """r = -1 and r = -1.5 are undefined, not 0 and not a positive number."""
    out = levels.annualize(np.array([-1.0, -1.5, -2.0, -3.0]))
    assert np.isnan(out).all()


def test_the_boundary_is_strict():
    """Just above -1 is defined; exactly -1 is not."""
    just_above = np.nextafter(-1.0, 0.0)
    assert np.isfinite(levels.annualize(np.array([just_above]))[0])
    assert np.isnan(levels.annualize(np.array([-1.0]))[0])


def test_missing_stays_missing():
    assert np.isnan(levels.annualize(np.array([np.nan]))[0])


def test_real_annualized_is_nan_exactly_when_the_guard_bites(real_panel):
    panel, _ = real_panel
    for stem in ("pretax", "posttax"):
        q = panel[f"calc_roic_{stem}_quarterly_ic_raw"].to_numpy()
        a = panel[f"calc_roic_{stem}_annualized_ic_raw"].to_numpy()
        defined = np.isfinite(q) & (1 + q > 0)
        assert np.array_equal(np.isfinite(a), defined), stem

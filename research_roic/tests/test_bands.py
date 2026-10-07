"""The band is the materiality rule, and the ratio band has no unit floor."""
from __future__ import annotations

import numpy as np

from src import labels as L


def _reference(x0: float, x1: float, rel: float, abs_floor: float,
               unit_floor: float) -> float:
    return max(abs_floor, rel * max(abs(x0), abs(x1), unit_floor))


def test_money_band_matches_a_pure_python_reference(cfg):
    rng = np.random.default_rng(3)
    a = rng.normal(0, 1e4, 500)
    b = rng.normal(0, 1e4, 500)
    got = L.band(a, b, "money", cfg)
    want = [_reference(x, y, 0.005, 1e-9, 1.0) for x, y in zip(a, b)]
    assert np.allclose(got, want, rtol=0, atol=0)


def test_ratio_band_has_no_unit_floor(cfg):
    """ROIC lives near 0.05; a floor of 1.0 would swamp the relative term."""
    assert cfg["bands"]["ratio"]["unit_floor"] == 0.0
    got = L.band(np.array([0.0001]), np.array([0.0002]), "ratio", cfg)[0]
    assert got == 1e-4
    big = L.band(np.array([0.40]), np.array([0.50]), "ratio", cfg)[0]
    assert np.isclose(big, 0.001 * 0.50)


def test_sign_band_is_one_part_in_a_billion(cfg):
    got = L.band(np.array([1.0]), np.array([1.0]), "sign", cfg)[0]
    assert got == 1e-9


def test_exactly_on_the_band_is_neutral(cfg):
    width = L.band(np.array([100.0]), np.array([100.0]), "money", cfg)
    neutral = L.EFFECT_LEVELS.index("NEUTRAL")
    assert L.classify_three_way(width, width)[0] == neutral
    assert L.classify_three_way(-width, width)[0] == neutral


def test_one_ulp_above_the_band_is_directional(cfg):
    width = L.band(np.array([100.0]), np.array([100.0]), "money", cfg)
    just_above = np.nextafter(width, np.inf)
    assert L.classify_three_way(just_above, width)[0] == 0        # positive side
    assert L.classify_three_way(-just_above, width)[0] == 1       # negative side


def test_missing_value_or_band_is_unclassifiable(cfg):
    width = np.array([1.0, np.nan])
    value = np.array([np.nan, 5.0])
    assert (L.classify_three_way(value, width) == -1).all()


def test_band_tolerates_one_missing_endpoint(cfg):
    """Row validity is decided by the status columns, not by the band."""
    got = L.band(np.array([np.nan]), np.array([1000.0]), "money", cfg)[0]
    assert np.isclose(got, 0.005 * 1000.0)

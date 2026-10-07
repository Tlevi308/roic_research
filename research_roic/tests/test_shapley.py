"""The algebra is exact: both bridges are order-independent and efficient."""
from __future__ import annotations

from itertools import permutations

import numpy as np
import pytest

from src import shapley


def test_coefficient_rows_sum_to_efficiency():
    """Summing the factor columns leaves v(all t) - v(all t-1) and nothing else."""
    for n in (2, 3):
        total = shapley.shapley_coefficients(n).sum(axis=1)
        expected = np.zeros(1 << n)
        expected[0], expected[-1] = -1.0, 1.0
        assert np.allclose(total, expected, atol=1e-15)


def test_coefficient_columns_sum_to_zero():
    """A constant metric must produce zero contributions."""
    for n in (2, 3):
        assert np.allclose(shapley.shapley_coefficients(n).sum(axis=0), 0.0, atol=1e-15)


def test_order_independence_equals_permutation_average():
    """The matmul equals the mean marginal contribution over all 3! orderings."""
    rng = np.random.default_rng(7)
    e = rng.normal(50, 20, 200)
    q = rng.uniform(0.3, 0.95, 200)
    i = rng.uniform(100, 900, 200)
    e2, q2, i2 = e * 1.1, q * 0.9, i * 1.2
    got = shapley.roic_bridge(e, e2, q, q2, i, i2)

    def value(state):
        vals = [(e, e2)[state[0]], (q, q2)[state[1]], (i, i2)[state[2]]]
        return vals[0] * vals[1] / vals[2]

    manual = {0: np.zeros(200), 1: np.zeros(200), 2: np.zeros(200)}
    for order in permutations(range(3)):
        state = [0, 0, 0]
        for factor in order:
            before = value(state)
            state[factor] = 1
            manual[factor] += (value(state) - before) / 6
    for factor, key in enumerate(("ebit", "tax", "ic")):
        assert np.allclose(got[key], manual[factor], rtol=1e-12, atol=1e-14)


def test_roic_contributions_match_closed_form():
    """The 8-coalition matmul reproduces the hand-derived 1/3 and 1/6 formula."""
    rng = np.random.default_rng(11)
    e, e2 = rng.normal(40, 15, 500), rng.normal(40, 15, 500)
    q, q2 = rng.uniform(-0.2, 1.2, 500), rng.uniform(-0.2, 1.2, 500)
    i, i2 = rng.uniform(50, 800, 500), rng.uniform(50, 800, 500)
    got = shapley.roic_bridge(e, e2, q, q2, i, i2)
    want = shapley.roic_closed_form(e, e2, q, q2, i, i2)
    for key in ("ebit", "tax", "ic"):
        assert np.allclose(got[key], want[key], rtol=1e-12, atol=1e-14)


def test_nopat_contributions_match_closed_form():
    """``dE*(Q0+Q1)/2`` and ``dQ*(E0+E1)/2``."""
    rng = np.random.default_rng(13)
    e, e2 = rng.normal(30, 10, 500), rng.normal(30, 10, 500)
    q, q2 = rng.uniform(-0.5, 1.5, 500), rng.uniform(-0.5, 1.5, 500)
    got = shapley.nopat_bridge(e, e2, q, q2)
    assert np.allclose(got["ebit"], (e2 - e) * (q + q2) / 2, rtol=1e-13, atol=1e-13)
    assert np.allclose(got["tax"], (q2 - q) * (e + e2) / 2, rtol=1e-13, atol=1e-13)


@pytest.mark.parametrize("n_rows", [100_000])
def test_residual_is_machine_zero_for_both_bridges(n_rows):
    """Efficiency holds to float precision on adversarial random input.

    The residual is scaled by the size of the *terms*, not of their difference:
    when ``v(all t)`` and ``v(all t-1)`` nearly cancel, the change is tiny while
    the rounding error stays proportional to the terms that produced it. That is
    cancellation in the subtraction, not a defect in the decomposition.
    """
    rng = np.random.default_rng(17)
    e, e2 = rng.normal(0, 1e4, n_rows), rng.normal(0, 1e4, n_rows)
    q, q2 = rng.normal(0.7, 0.6, n_rows), rng.normal(0.7, 0.6, n_rows)
    i, i2 = rng.normal(0, 1e4, n_rows), rng.normal(0, 1e4, n_rows)

    for bridge in (shapley.nopat_bridge(e, e2, q, q2),
                   shapley.roic_bridge(e, e2, q, q2, i, i2)):
        scale = np.maximum.reduce([np.ones(n_rows), np.abs(bridge["before"]),
                                   np.abs(bridge["after"])])
        assert np.nanmax(np.abs(bridge["residual"]) / scale) <= 1e-13


def test_coalition_endpoints_are_the_roic_levels():
    """v(none) and v(all) are exactly ROIC at t-1 and t."""
    rng = np.random.default_rng(19)
    e, e2 = rng.normal(40, 10, 100), rng.normal(40, 10, 100)
    q, q2 = rng.uniform(0.2, 0.9, 100), rng.uniform(0.2, 0.9, 100)
    i, i2 = rng.uniform(100, 500, 100), rng.uniform(100, 500, 100)
    got = shapley.roic_bridge(e, e2, q, q2, i, i2)
    assert np.allclose(got["before"], e * q / i, rtol=0, atol=0)
    assert np.allclose(got["after"], e2 * q2 / i2, rtol=0, atol=0)


def test_any_missing_factor_blanks_every_contribution():
    """A partial bridge is never produced."""
    e = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    e2 = np.array([12.0, np.nan, 12.0, 12.0, 12.0, 12.0])
    q = np.array([0.7, 0.7, np.nan, 0.7, 0.7, 0.7])
    q2 = np.full(6, 0.8)
    i = np.array([100.0, 100.0, 100.0, np.nan, 100.0, 0.0])
    i2 = np.array([110.0, 110.0, 110.0, 110.0, np.nan, 110.0])
    got = shapley.roic_bridge(e, e2, q, q2, i, i2)
    for key in ("ebit", "tax", "ic", "change"):
        assert np.isfinite(got[key][0])
        assert np.isnan(got[key][1:]).all(), key

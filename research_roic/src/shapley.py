"""Shapley bridges: one coefficient matrix serves both decompositions.

Rules enforced here
-------------------
* A factor's contribution averages over **all** orderings. A sequential bridge
  ("EBIT first, then tax") dumps the whole interaction term onto whichever
  factor moved last, so the answer depends on an arbitrary order.
* The 2^n coalition values are evaluated once and multiplied by the
  combinatorial weights ``|S|!(n-|S|-1)!/n!``. The 3! = 6 orderings are never
  enumerated row by row.
* Efficiency holds exactly by construction: the weights on every coalition
  value cancel telescopically until only ``v(all t) - v(all t-1)`` survives.
  The residual column is therefore a wiring check, not a true remainder.
* Any missing factor makes every contribution of that row NaN - never a partial
  bridge.
"""
from __future__ import annotations

from math import factorial

import numpy as np

from .data_io import safe_div


def shapley_coefficients(n_factors: int) -> np.ndarray:
    """``(2**n, n)`` matrix: contribution i = coalition_values @ coeff[:, i].

    Row ``s`` is the coalition whose bit ``j`` means "factor j takes its time-t
    value". Factor ``i`` gains ``+w`` from every coalition that contains it and
    ``-w`` from the same coalition without it, with
    ``w = |S|!(n-|S|-1)!/n!`` evaluated on the coalition *without* ``i``.
    """
    size = 1 << n_factors
    coeff = np.zeros((size, n_factors), dtype="float64")
    for i in range(n_factors):
        bit = 1 << i
        for s in range(size):
            if s & bit:
                continue
            k = bin(s).count("1")                      # |S| excluding factor i
            w = factorial(k) * factorial(n_factors - k - 1) / factorial(n_factors)
            coeff[s | bit, i] += w
            coeff[s, i] -= w
    return coeff


def nopat_bridge(ebit_before: np.ndarray, ebit_after: np.ndarray,
                 retention_before: np.ndarray, retention_after: np.ndarray
                 ) -> dict[str, np.ndarray]:
    """NOPAT = E * Q, two factors.

    Closed form, which ``shapley_coefficients(2)`` reproduces exactly:
    ``C_EBIT = dE * (Q0 + Q1) / 2`` and ``C_TAX = dQ * (E0 + E1) / 2``.
    """
    e0, e1 = np.asarray(ebit_before, "float64"), np.asarray(ebit_after, "float64")
    q0, q1 = np.asarray(retention_before, "float64"), np.asarray(retention_after, "float64")
    coeff = shapley_coefficients(2)
    # coalition order: bit0 = EBIT at t, bit1 = Q at t
    values = np.stack([e0 * q0, e1 * q0, e0 * q1, e1 * q1], axis=1)
    contrib = values @ coeff
    change = values[:, 3] - values[:, 0]
    return {
        "before": values[:, 0],
        "after": values[:, 3],
        "change": change,
        "ebit": contrib[:, 0],
        "tax": contrib[:, 1],
        "residual": change - contrib[:, 0] - contrib[:, 1],
    }


def roic_bridge(ebit_before: np.ndarray, ebit_after: np.ndarray,
                retention_before: np.ndarray, retention_after: np.ndarray,
                capital_before: np.ndarray, capital_after: np.ndarray
                ) -> dict[str, np.ndarray]:
    """ROIC = E * Q / I, three factors.

    The eight coalition values are built once. Division goes through
    ``safe_div``, so a zero or missing capital base yields NaN rather than inf,
    and the NaN then propagates through the matmul to all three contributions.
    """
    e = (np.asarray(ebit_before, "float64"), np.asarray(ebit_after, "float64"))
    q = (np.asarray(retention_before, "float64"), np.asarray(retention_after, "float64"))
    i = (np.asarray(capital_before, "float64"), np.asarray(capital_after, "float64"))

    coeff = shapley_coefficients(3)
    # coalition order: bit0 = EBIT, bit1 = Q, bit2 = I; bit set -> time t
    columns = []
    for s in range(8):
        columns.append(safe_div(e[(s >> 0) & 1] * q[(s >> 1) & 1], i[(s >> 2) & 1]))
    values = np.stack(columns, axis=1)
    contrib = values @ coeff
    change = values[:, 7] - values[:, 0]
    return {
        "before": values[:, 0],
        "after": values[:, 7],
        "change": change,
        "ebit": contrib[:, 0],
        "tax": contrib[:, 1],
        "ic": contrib[:, 2],
        "residual": change - contrib.sum(axis=1),
    }


def roic_closed_form(e0: np.ndarray, e1: np.ndarray, q0: np.ndarray, q1: np.ndarray,
                     i0: np.ndarray, i1: np.ndarray) -> dict[str, np.ndarray]:
    """The hand-derived three-factor form, kept only so a test can compare.

    ``C_EBIT = dE * (Q0/I0/3 + Q1/I0/6 + Q0/I1/6 + Q1/I1/3)`` and siblings.
    """
    third, sixth = 1.0 / 3.0, 1.0 / 6.0
    inv0, inv1 = safe_div(np.ones_like(i0), i0), safe_div(np.ones_like(i1), i1)
    c_ebit = (e1 - e0) * (q0 * inv0 * third + q1 * inv0 * sixth
                          + q0 * inv1 * sixth + q1 * inv1 * third)
    c_tax = (q1 - q0) * (e0 * inv0 * third + e1 * inv0 * sixth
                         + e0 * inv1 * sixth + e1 * inv1 * third)
    c_ic = (inv1 - inv0) * (e0 * q0 * third + e1 * q0 * sixth
                            + e0 * q1 * sixth + e1 * q1 * third)
    return {"ebit": c_ebit, "tax": c_tax, "ic": c_ic}

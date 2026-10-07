"""Bands and labels: one comparison helper, so nine families cannot drift apart.

Rules enforced here
-------------------
* Every directional label comes from ``classify_three_way``. Nothing else in the
  package compares a number against a band.
* A value exactly on the band is the neutral label; one ulp above it is
  directional.
* ``first_match`` ladders are ordered deliberately - ``SINGLE_POSITIVE_DRIVER``
  is checked before ``ALL_POSITIVE`` because both hold for a lone positive
  contribution and the specific label is the useful one.
* Labels are produced as int8 codes and only turned into categoricals at the
  end, with the **full** declared level set, so a level that never occurs is
  still a declared level rather than a silent absence.
* The ``*_effect`` families read the sign of the Shapley contribution, not the
  direction of the raw move: when the retention rate is negative, rising EBIT
  lowers NOPAT.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

UNCLASSIFIED = "UNCLASSIFIED"

# ---- declared level sets -------------------------------------------------
# Code order is fixed by classify_three_way: 0 = positive side, 1 = negative
# side, 2 = neutral. Every tuple below must follow that order.
EFFECT_TOKENS = ("POS", "NEG", "ZERO")        # inside combination labels
MOVEMENT_TOKENS = ("UP", "DOWN", "FLAT")
SIGN_TOKENS = ("PROFIT", "LOSS", "ZERO")

EFFECT_LEVELS = ("POSITIVE", "NEGATIVE", "NEUTRAL", UNCLASSIFIED)
DIRECTION_LEVELS = ("INCREASE", "DECREASE", "STABLE", UNCLASSIFIED)
MOVEMENT_LEVELS = MOVEMENT_TOKENS + (UNCLASSIFIED,)

NOPAT_STATUS_LEVELS = ("VALID", "UNCLASSIFIED_NONCONSECUTIVE", "UNCLASSIFIED_MISSING_DATA")
ROIC_STATUS_LEVELS = ("VALID", "UNCLASSIFIED_NONCONSECUTIVE",
                      "UNCLASSIFIED_MISSING_DATA", "UNCLASSIFIED_ZERO_IC")
TAX_FLAG_LEVELS = ("VALID", "MISSING", "NEGATIVE_TAX_RATE", "ABOVE_100_PERCENT")
ROIC_FLAG_LEVELS = ("VALID", "NEGATIVE_IC_MECHANICAL_ONLY",
                    "TAX_RATE_OUT_OF_RANGE", UNCLASSIFIED)
STRUCTURE_LEVELS = ("NO_MATERIAL_CHANGE", "SINGLE_POSITIVE_DRIVER", "ALL_POSITIVE",
                    "SINGLE_NEGATIVE_DRIVER", "ALL_NEGATIVE", "MIXED_NET_INCREASE",
                    "MIXED_NET_DECREASE", "MIXED_FULL_OFFSET", UNCLASSIFIED)
DOMINANT_LEVELS = ("EBIT", "TAX", "IC", "BALANCED", "NONE", UNCLASSIFIED)


def nopat_effect_combination_levels() -> tuple[str, ...]:
    return tuple(f"EBIT_{a}__TAX_{b}" for a in EFFECT_TOKENS for b in EFFECT_TOKENS) \
        + (UNCLASSIFIED,)


def roic_effect_combination_levels() -> tuple[str, ...]:
    return tuple(f"EBIT_{a}__TAX_{b}__IC_{c}"
                 for a in EFFECT_TOKENS for b in EFFECT_TOKENS for c in EFFECT_TOKENS) \
        + (UNCLASSIFIED,)


def raw_movement_combination_levels() -> tuple[str, ...]:
    return tuple(f"EBIT_{a}__TAX_{b}__IC_{c}"
                 for a in MOVEMENT_TOKENS for b in MOVEMENT_TOKENS for c in MOVEMENT_TOKENS) \
        + (UNCLASSIFIED,)


def sign_regime_levels() -> tuple[str, ...]:
    return tuple(f"{a}_TO_{b}" for a in SIGN_TOKENS for b in SIGN_TOKENS) + (UNCLASSIFIED,)


# ---------------------------------------------------------------------------
# bands
# ---------------------------------------------------------------------------
def band(before: np.ndarray, after: np.ndarray, kind: str,
         cfg: dict[str, Any]) -> np.ndarray:
    """Materiality band from the larger of the two endpoints.

    ``money`` -> ``max(abs_floor, rel * max(|x0|, |x1|, unit_floor))`` with
    ``unit_floor = 1.0``; ``ratio`` keeps ``unit_floor = 0.0`` on purpose,
    because ROIC lives around 0.05 and a floor of 1.0 would let the relative
    term swamp the absolute one and call a 90bp move immaterial.
    ``np.fmax`` is used so one missing endpoint still yields a usable band; row
    validity is decided by the status columns, not by the band.
    """
    spec = cfg["bands"][kind]
    scale = np.fmax(np.abs(np.asarray(before, "float64")),
                    np.abs(np.asarray(after, "float64")))
    scale = np.fmax(scale, float(spec["unit_floor"]))
    return np.maximum(float(spec["abs_floor"]), float(spec["rel"]) * scale)


# ---------------------------------------------------------------------------
# classification primitives
# ---------------------------------------------------------------------------
def classify_three_way(value: np.ndarray, width: np.ndarray) -> np.ndarray:
    """0 = positive side, 1 = negative side, 2 = neutral, -1 = unclassifiable.

    ``value`` or ``width`` missing gives -1. A value exactly on the band is
    neutral; ``np.nextafter(width, inf)`` is directional.
    """
    v = np.asarray(value, "float64")
    w = np.asarray(width, "float64")
    codes = np.full(v.shape, -1, dtype="int8")
    usable = np.isfinite(v) & np.isfinite(w)
    codes[usable] = 2
    codes[usable & (v > w)] = 0
    codes[usable & (v < -w)] = 1
    return codes


def classify_sign(value: np.ndarray, width: np.ndarray) -> np.ndarray:
    """0 = PROFIT, 1 = LOSS, 2 = ZERO, -1 = missing. Same shape as the three-way."""
    return classify_three_way(value, width)


def first_match(conditions: Sequence[np.ndarray], codes: Sequence[int],
                default: int, n: int) -> np.ndarray:
    """Resolve an ordered ladder: the first condition that holds wins."""
    out = np.full(n, default, dtype="int8")
    assigned = np.zeros(n, dtype=bool)
    for cond, code in zip(conditions, codes):
        take = np.asarray(cond, dtype=bool) & ~assigned
        out[take] = code
        assigned |= take
    return out


def gate(codes: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Blank a label wherever the governing status is not VALID."""
    out = codes.copy()
    out[~np.asarray(valid, dtype=bool)] = -1
    return out


def combine(code_arrays: Sequence[np.ndarray], token_sets: Sequence[Sequence[str]],
            levels: Sequence[str]) -> np.ndarray:
    """Mixed-radix cross-product of label codes; any -1 component gives UNCLASSIFIED.

    ``levels`` must list the cross-product in the same nesting order as
    ``token_sets``, with UNCLASSIFIED last - which is how the ``*_levels()``
    helpers above build them.
    """
    n = len(code_arrays[0])
    radices = [len(t) for t in token_sets]
    flat = np.zeros(n, dtype="int64")
    bad = np.zeros(n, dtype=bool)
    for codes, radix in zip(code_arrays, radices):
        c = np.asarray(codes)
        bad |= c < 0
        flat = flat * radix + np.where(c < 0, 0, c).astype("int64")
    unclassified = len(levels) - 1
    return np.where(bad, unclassified, flat).astype("int16")


def to_categorical(codes: np.ndarray, levels: Sequence[str],
                   unclassified_code: int | None = -1) -> pd.Categorical:
    """int codes -> Categorical with the full declared level set.

    ``unclassified_code`` is mapped to the ``UNCLASSIFIED`` level when the level
    set declares one, and to NA otherwise.
    """
    levels = list(levels)
    codes = np.asarray(codes, dtype="int64").copy()
    if unclassified_code is not None:
        target = levels.index(UNCLASSIFIED) if UNCLASSIFIED in levels else -1
        codes[codes == unclassified_code] = target
    codes[(codes < 0) | (codes >= len(levels))] = -1
    return pd.Categorical.from_codes(codes.astype("int16"), categories=levels)

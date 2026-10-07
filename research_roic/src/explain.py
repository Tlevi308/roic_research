"""The explanation sentence, drawn from a closed enumerated catalogue.

Rules enforced here
-------------------
* Every sentence the panel can hold is enumerated up front: headline x driver
  pattern x dominance, plus one sentence per invalid status. Rows carry an id
  into that catalogue, and a check asserts no row falls outside it.
* The wording follows the Shapley signs, not the raw movements. A quarter where
  EBIT rose but the capital base rose faster reads as a ROIC decline caused by
  invested capital, not as an EBIT improvement.
* Building the catalogue once turns ~1.9M string concatenations into ~1,000.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import labels as L

# ---- the closed vocabulary ------------------------------------------------
HEADLINES = (
    "Invested capital was negative, so the ratio is mechanical rather than economic.",
    "NOPAT turned positive.",
    "NOPAT turned negative.",
    "ROIC became less negative, but the change was mechanical.",
    "ROIC became more negative.",
    "ROIC increased.",
    "ROIC decreased.",
    "ROIC was essentially unchanged.",
)
HEADLINE_KEYS = (
    "NEGATIVE_IC", "LOSS_TO_PROFIT", "PROFIT_TO_LOSS", "NEGATIVE_NOPAT_INCREASE",
    "NEGATIVE_NOPAT_DECREASE", "INCREASE", "DECREASE", "UNCHANGED",
)

DRIVER_NAMES = ("EBIT", "the tax rate", "invested capital")
DRIVER_NAMES_LEADING = ("EBIT", "The tax rate", "Invested capital")
# same order as labels.DOMINANT_LEVELS minus UNCLASSIFIED, so the dominant-driver
# code indexes straight into this tuple
DOMINANCE_KEYS = ("EBIT", "TAX", "IC", "BALANCED", "NONE")
INVALID_TEMPLATE = "No comparison is available ({status})."


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _capitalise(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def driver_sentence(effect_codes: tuple[int, int, int]) -> str:
    """Sentence for one (EBIT, TAX, IC) effect pattern. 0 = positive, 1 = negative."""
    positive = [DRIVER_NAMES[i] for i, c in enumerate(effect_codes) if c == 0]
    negative = [DRIVER_NAMES[i] for i, c in enumerate(effect_codes) if c == 1]
    if not positive and not negative:
        return "No driver moved materially."
    if positive and not negative:
        return _capitalise(f"{_join(positive)} contributed positively.")
    if negative and not positive:
        return _capitalise(f"{_join(negative)} worked against the change.")
    return _capitalise(f"{_join(positive)} contributed positively, "
                       f"while {_join(negative)} worked against the change.")


def dominance_sentence(key: str) -> str:
    if key == "BALANCED":
        return "No single driver dominated."
    if key in ("EBIT", "TAX", "IC"):
        name = DRIVER_NAMES_LEADING[("EBIT", "TAX", "IC").index(key)]
        return f"{name} was the dominant driver."
    return ""


def build_catalogue() -> pd.DataFrame:
    """Every sentence the panel can hold, with its id and its components.

    Ids 0 .. 8*27*5-1 are the valid-row cross-product in mixed-radix order
    (headline, driver pattern, dominance); the invalid-status sentences follow.
    """
    rows: list[dict[str, Any]] = []
    for h, (hkey, headline) in enumerate(zip(HEADLINE_KEYS, HEADLINES)):
        for d, pattern in enumerate(_driver_patterns()):
            for m, mkey in enumerate(DOMINANCE_KEYS):
                parts = [headline, driver_sentence(pattern), dominance_sentence(mkey)]
                rows.append({
                    "explanation_id": (h * 27 + d) * len(DOMINANCE_KEYS) + m,
                    "headline_key": hkey,
                    "effect_combination": "EBIT_{}__TAX_{}__IC_{}".format(
                        *[L.EFFECT_TOKENS[c] for c in pattern]),
                    "dominant_driver": mkey,
                    "explanation": " ".join(p for p in parts if p),
                })
    base = len(rows)
    for s, status in enumerate(L.ROIC_STATUS_LEVELS):
        if status == "VALID":
            continue
        rows.append({"explanation_id": base + s, "headline_key": "INVALID",
                     "effect_combination": L.UNCLASSIFIED, "dominant_driver": L.UNCLASSIFIED,
                     "explanation": INVALID_TEMPLATE.format(status=status)})
    return pd.DataFrame(rows).sort_values("explanation_id").reset_index(drop=True)


def _driver_patterns() -> list[tuple[int, int, int]]:
    """The 27 (EBIT, TAX, IC) effect patterns, in the combination label's order."""
    return [(a, b, c) for a in range(3) for b in range(3) for c in range(3)]


def explanation_ids(headline_code: np.ndarray, effect_combo_code: np.ndarray,
                    dominance_code: np.ndarray, roic_status_code: np.ndarray,
                    valid: np.ndarray) -> np.ndarray:
    """Row -> catalogue id. Invalid rows get their status sentence."""
    n = len(headline_code)
    ids = ((headline_code.astype("int64") * 27 + effect_combo_code.astype("int64"))
           * len(DOMINANCE_KEYS) + dominance_code.astype("int64"))
    base = len(HEADLINES) * 27 * len(DOMINANCE_KEYS)
    invalid_ids = base + roic_status_code.astype("int64")
    return np.where(np.asarray(valid, dtype=bool), ids, invalid_ids)


def render(ids: np.ndarray, catalogue: pd.DataFrame) -> pd.Categorical:
    """Map ids to sentences as a Categorical over the whole catalogue.

    Declaring the full catalogue as the level set is what makes the column a
    closed list: a census can then report which sentences never occurred.
    """
    sentences = catalogue["explanation"].to_numpy()
    level_of = {}
    levels: list[str] = []
    for s in sentences:
        if s not in level_of:
            level_of[s] = len(levels)
            levels.append(s)
    lookup = np.full(int(catalogue["explanation_id"].max()) + 1, -1, dtype="int64")
    lookup[catalogue["explanation_id"].to_numpy()] = [level_of[s] for s in sentences]
    ids = np.asarray(ids, dtype="int64")
    if ids.size and (ids.min() < 0 or ids.max() >= len(lookup)):
        raise ValueError("explanation id outside the catalogue")
    codes = lookup[ids]
    if (codes < 0).any():
        raise ValueError("explanation id not present in the catalogue")
    return pd.Categorical.from_codes(codes.astype("int32"), categories=levels)

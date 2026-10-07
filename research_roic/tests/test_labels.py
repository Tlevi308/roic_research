"""Nine is nine, twenty-seven is twenty-seven, and the ladders are ordered."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import labels as L, schema


def test_declared_level_counts():
    assert len(L.nopat_effect_combination_levels()) == 9 + 1
    assert len(L.roic_effect_combination_levels()) == 27 + 1
    assert len(L.raw_movement_combination_levels()) == 27 + 1
    assert len(L.sign_regime_levels()) == 9 + 1


def test_every_family_has_unique_levels():
    for col, levels in schema.LABEL_LEVELS.items():
        assert len(set(levels)) == len(levels), col


def test_combine_is_a_mixed_radix_cross_product():
    """Each code triple maps to its own label, and any -1 gives UNCLASSIFIED."""
    levels = L.roic_effect_combination_levels()
    triples = [(a, b, c) for a in range(3) for b in range(3) for c in range(3)]
    codes = L.combine([np.array([t[i] for t in triples]) for i in range(3)],
                      [L.EFFECT_TOKENS] * 3, levels)
    assert sorted(codes) == list(range(27))
    for code, (a, b, c) in zip(codes, triples):
        assert levels[code] == (f"EBIT_{L.EFFECT_TOKENS[a]}__TAX_{L.EFFECT_TOKENS[b]}"
                                f"__IC_{L.EFFECT_TOKENS[c]}")
    with_missing = L.combine([np.array([0]), np.array([-1]), np.array([0])],
                             [L.EFFECT_TOKENS] * 3, levels)
    assert levels[with_missing[0]] == L.UNCLASSIFIED


def test_first_match_is_ordered():
    """Both conditions hold; the earlier one must win."""
    out = L.first_match([np.array([True]), np.array([True])], [1, 2], default=9, n=1)
    assert out[0] == 1


def test_effect_structure_ladder_order_is_load_bearing():
    """A lone positive contribution is SINGLE_POSITIVE_DRIVER, not ALL_POSITIVE."""
    idx = {name: i for i, name in enumerate(L.STRUCTURE_LEVELS)}
    assert idx["SINGLE_POSITIVE_DRIVER"] < idx["ALL_POSITIVE"]
    assert idx["SINGLE_NEGATIVE_DRIVER"] < idx["ALL_NEGATIVE"]


def test_to_categorical_maps_unclassified_and_keeps_all_levels():
    cat = L.to_categorical(np.array([0, 1, 2, -1]), L.EFFECT_LEVELS)
    assert list(cat.categories) == list(L.EFFECT_LEVELS)
    assert list(cat.astype(str)) == ["POSITIVE", "NEGATIVE", "NEUTRAL", L.UNCLASSIFIED]


def test_gate_blanks_on_invalid_status():
    codes = np.array([0, 1, 2])
    gated = L.gate(codes, np.array([True, False, True]))
    assert list(gated) == [0, -1, 2]


def test_synthetic_panel_declares_full_level_sets(synthetic):
    """Every label column carries its whole declared level set, used or not."""
    for col, levels in schema.LABEL_LEVELS.items():
        assert list(synthetic[col].cat.categories) == list(levels), col


def test_real_every_declared_level_is_reached(real_panel):
    """On the real panel all 9/9, 27/27 and 3/3 cells actually occur."""
    panel, _ = real_panel
    unreached = []
    for col, levels in schema.LABEL_LEVELS.items():
        counts = panel[col].value_counts(dropna=False)
        unreached += [(col, lv) for lv in levels if int(counts.get(lv, 0)) == 0]
    assert unreached == []


def test_real_no_label_column_is_na(real_panel):
    """Labels use an explicit UNCLASSIFIED level rather than a missing value."""
    panel, _ = real_panel
    for col in schema.LABEL_LEVELS:
        assert int(panel[col].isna().sum()) == 0, col

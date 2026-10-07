"""One sentence per row, from a closed catalogue, driven by the Shapley signs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import explain, labels as L
from tests.conftest import build, make_source


def test_catalogue_is_closed_and_complete():
    """Every headline x driver pattern x dominance cell, plus the invalid statuses."""
    cat = explain.build_catalogue()
    valid_cells = len(explain.HEADLINES) * 27 * len(explain.DOMINANCE_KEYS)
    invalid_cells = len([s for s in L.ROIC_STATUS_LEVELS if s != "VALID"])
    assert len(cat) == valid_cells + invalid_cells
    assert cat["explanation_id"].is_unique
    assert cat["explanation"].str.len().min() > 0


def test_dominance_keys_match_the_label_order():
    """The dominant-driver code indexes straight into DOMINANCE_KEYS."""
    assert explain.DOMINANCE_KEYS == tuple(
        lv for lv in L.DOMINANT_LEVELS if lv != L.UNCLASSIFIED)


def test_driver_sentence_reads_the_effect_pattern():
    assert explain.driver_sentence((0, 1, 2)) == (
        "EBIT contributed positively, while the tax rate worked against the change.")
    assert explain.driver_sentence((2, 2, 2)) == "No driver moved materially."
    assert explain.driver_sentence((0, 0, 0)) == (
        "EBIT, the tax rate and invested capital contributed positively.")
    assert explain.driver_sentence((1, 1, 2)) == (
        "EBIT and the tax rate worked against the change.")


def test_dominance_sentence():
    assert explain.dominance_sentence("BALANCED") == "No single driver dominated."
    assert explain.dominance_sentence("NONE") == ""
    assert explain.dominance_sentence("IC") == "Invested capital was the dominant driver."


def test_render_rejects_an_id_outside_the_catalogue():
    cat = explain.build_catalogue()
    try:
        explain.render(np.array([len(cat) + 10_000]), cat)
    except ValueError as exc:
        assert "catalogue" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_every_row_has_a_sentence(cfg, seed):
    panel = build(cfg, make_source(seed))
    assert panel["calc_roic_explanation"].notna().all()
    invalid = panel["calc_roic_decomposition_status"] != "VALID"
    for status, text in zip(panel.loc[invalid, "calc_roic_decomposition_status"],
                            panel.loc[invalid, "calc_roic_explanation"]):
        assert text == f"No comparison is available ({status})."


def test_drivers_read_shapley_signs_not_raw_movements(cfg, seed):
    """A falling tax rate that still hurt the change is named among the negatives.

    The divergence needs a negative numerator: with ``Q = 1 - T`` a falling rate
    raises Q, and raising Q lowers ROIC only when EBIT is negative. That is the
    whole reason the effect family is read from the contribution's sign.
    """
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    src["oiadpq"] = -60.0                      # loss-making throughout
    src["piq"] = -55.0
    src["txtq"] = [-11.0, -11.0, -11.0, -4.0, -11.0, -11.0, -11.0, -11.0]
    panel = build(cfg, src)
    rows = panel[(panel.calc_roic_decomposition_status == "VALID")
                 & (panel.calc_tax_rate_movement == "DOWN")
                 & (panel.calc_roic_tax_effect == "NEGATIVE")]
    assert len(rows) > 0, "no row exercises the divergence"
    for text in rows["calc_roic_explanation"]:
        assert "worked against the change" in text
        # the negatives are listed before that clause; the list may open the
        # sentence, in which case it is capitalised
        negatives = text.split("worked against the change")[0].lower()
        assert "the tax rate" in negatives


def test_real_divergence_between_movement_and_effect_exists(real_panel):
    """The two families really do disagree on the real panel, by the thousand."""
    panel, _ = real_panel
    valid = panel["calc_roic_decomposition_status"] == "VALID"
    down = panel["calc_tax_rate_movement"] == "DOWN"
    negative = panel["calc_roic_tax_effect"] == "NEGATIVE"
    up = panel["calc_tax_rate_movement"] == "UP"
    positive = panel["calc_roic_tax_effect"] == "POSITIVE"
    assert int((valid & down & negative).sum()) > 0
    assert int((valid & up & positive).sum()) > 0


def test_negative_capital_headline_wins_the_ladder(cfg, seed):
    """A negative capital base is announced as mechanical before anything else."""
    src = make_source(seed, tickers=("AAA",), n_quarters=8)
    for row in range(2, 8):
        src.loc[src.index[row], ["actq", "lctq", "ppentq", "gdwlq"]] = \
            [10.0, 900.0 + row, 0.0, 0.0]
    panel = build(cfg, src)
    rows = panel[panel.calc_roic_quality_flag == "NEGATIVE_IC_MECHANICAL_ONLY"]
    assert len(rows) > 0
    for text in rows["calc_roic_explanation"]:
        assert text.startswith("Invested capital was negative")


def test_real_every_sentence_is_in_the_catalogue(real_panel):
    panel, report = real_panel
    catalogue = set(report["explanation_catalogue"]["explanation"])
    used = set(panel["calc_roic_explanation"].cat.categories)
    assert used <= catalogue
    assert int(panel["calc_roic_explanation"].isna().sum()) == 0


def test_real_explanation_is_categorical_and_bounded(real_panel):
    panel, report = real_panel
    assert str(panel["calc_roic_explanation"].dtype) == "category"
    assert len(panel["calc_roic_explanation"].cat.categories) <= \
        len(report["explanation_catalogue"])

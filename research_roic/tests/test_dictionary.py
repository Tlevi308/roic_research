"""The Word dictionary is read completely, including OMML equations."""
import pytest

from src import dictionary


@pytest.fixture(scope="module")
def blocks(cfg):
    if not cfg["_paths"]["dictionary_docx"].exists():
        pytest.skip("dictionary not available")
    return dictionary.read_docx_blocks(cfg["_paths"]["dictionary_docx"])


def test_declared_total_equals_extracted(blocks):
    cat = dictionary.build_variable_catalogue(blocks)
    declared = dictionary.declared_column_count(blocks)
    assert declared["declared_total"] == len(cat) == sum(declared["declared_groups"].values())


def test_shapley_formula_recovered_from_omml(blocks):
    cat = dictionary.build_variable_catalogue(blocks).set_index("variable")
    f = cat.loc["calc_roic_ebit_contribution", "formula"]
    assert "⅓" in f and "⅙" in f and "(Q_0)/(I_0)" in f
    assert "(1)/(I_1) - (1)/(I_0)" in cat.loc["calc_roic_ic_contribution", "formula"]


def test_numbers_inside_text_are_not_lost(blocks):
    text = dictionary.blocks_to_text(blocks)
    assert "לעולם לא 0" in text          # "missing is always NaN, never 0"
    assert "ברירת מחדל 2" in text        # alignment.quarter_shift_months default

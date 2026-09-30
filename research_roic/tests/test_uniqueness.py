"""Firm-quarter uniqueness."""
import pandas as pd
import pytest

from src import features, pipeline


def test_real_panel_unique_firm_quarter(real_panel):
    assert not real_panel.duplicated(["symbol", "period_key"]).any()
    assert not real_panel.duplicated(["symbol", "fiscal_period_end_date"]).any()
    assert real_panel["KEY"].is_unique


def test_real_panel_row_count_preserved(real_panel, raw_csv):
    """The pipeline never drops records: prepared rows == CSV rows, same KEYs."""
    assert len(real_panel) == len(raw_csv)
    assert set(real_panel["KEY"]) == set(raw_csv["KEY"].astype(str))


def test_assert_sorted_rejects_duplicate_quarter(synthetic):
    dup = pd.concat([synthetic, synthetic.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        features.assert_sorted(features.sort_panel(dup))


def test_pipeline_rejects_duplicate_firm_quarter(cfg, real_inputs):
    panel_raw, rel, _ = real_inputs
    dup = pd.concat([panel_raw, panel_raw.iloc[[100]]], ignore_index=True)
    with pytest.raises(ValueError, match="share \\(symbol, period_key\\)"):
        pipeline.build_research_panel(cfg, panel_raw=dup, relevance=rel)

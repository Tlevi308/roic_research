"""Single entry point that builds the Stage-1 research panel (used by the run script and by the tests)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import data_io, eligibility, features, sample


def build_research_panel(cfg: dict[str, Any], panel_raw: pd.DataFrame | None = None,
                         relevance: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load (unless given), then: sort -> sequence -> relevance -> timing -> returns -> levels -> trends -> layers.

    ``panel_raw`` / ``relevance`` may be passed in already loaded (tests use this to perturb inputs).
    """
    reports: dict[str, Any] = {}
    if panel_raw is None:
        panel_raw, reports["load"] = data_io.load_panel(cfg)
    if relevance is None:
        relevance, reports["relevance_load"] = data_io.load_relevance(cfg)
    reports["relevance_ranges"] = eligibility.validate_ranges(relevance, cfg)

    dup = panel_raw.duplicated(["symbol", "q_ord"], keep=False)
    if dup.any():
        raise ValueError(f"{int(dup.sum())} rows share (symbol, period_key): {panel_raw.loc[dup, 'KEY'].head().tolist()}")

    panel = features.sort_panel(panel_raw)
    panel = features.add_sequence_info(panel, cfg)
    panel = pd.concat([panel, eligibility.membership(panel, relevance, cfg)], axis=1)
    panel = sample.add_timing(panel, cfg)
    as_of = sample.resolve_data_as_of(panel, cfg)
    panel, reports["returns"] = sample.clean_returns(panel, cfg, as_of)
    panel, reports["levels"] = features.add_levels(panel, cfg)
    panel = features.add_trend_features(panel, cfg)
    panel = sample.add_sample_layers(panel, cfg)
    reports["relevance"] = relevance
    return panel, reports

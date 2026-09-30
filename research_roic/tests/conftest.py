"""Shared fixtures: config, a seeded synthetic panel, and the real prepared panel (skipped if data is absent)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import data_io, features, pipeline  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return data_io.load_config(ROOT / "config.yaml")


@pytest.fixture(scope="session")
def seed(cfg):
    return int(cfg["project"]["seed"])


def make_synthetic_panel(seed: int, symbols=("AAA", "BBB", "CCC"), first="2018Q1", n_quarters=10) -> pd.DataFrame:
    """Calendar-quarter firms with random fundamentals. Columns mirror the real CSV names used by the code."""
    rng = np.random.default_rng(seed)
    periods = pd.period_range(first, periods=n_quarters, freq="Q")
    rows = []
    for sym in symbols:
        for p in periods:
            fiscal_end = (p + 0).end_time.normalize()   # calendar quarter end -> period_key = p (shift -2 months stays in p)
            rows.append({
                "KEY": f"{p}_{sym}", "symbol": sym, "company": f"{sym} Inc", "sector": "Tech", "period_key": str(p),
                "fiscal_period_end_date": fiscal_end, "run_date": pd.Timestamp("2026-09-05"),
                "ebit": rng.normal(100, 20), "calc_nopat_quarterly": rng.normal(80, 15),
                "calc_average_ic_raw_quarterly": rng.uniform(500, 1500),
                "calc_roic_ebit_contribution": rng.normal(0, 0.01), "calc_roic_tax_contribution": rng.normal(0, 0.01),
                "calc_roic_ic_contribution": rng.normal(0, 0.005),
                "calc_roic_decomposition_status": "VALID", "calc_roic_economic_interpretation_valid": True,
                "calc_debt_value_quarterly": rng.uniform(0, 500), "total_stockholders_equity": rng.uniform(100, 900),
                "Future_Quarterly_Return": rng.normal(0.02, 0.1), "Momentum_3Q": rng.normal(0.05, 0.2),
            })
    df = pd.DataFrame(rows)
    df["calc_roic_pretax_quarterly_ic_raw"] = (df["ebit"] / df["calc_average_ic_raw_quarterly"]).round(2)
    df["calc_roic_posttax_quarterly_ic_raw"] = (df["calc_nopat_quarterly"] / df["calc_average_ic_raw_quarterly"]).round(2)
    df["calc_debt_to_equity_quarterly"] = (df["calc_debt_value_quarterly"] / df["total_stockholders_equity"]).round(2)
    df["calc_roic_economic_interpretation_valid"] = df["calc_roic_economic_interpretation_valid"].astype("boolean")
    df["period"] = pd.PeriodIndex(df["period_key"], freq="Q")
    df["q_ord"] = data_io.quarter_ordinal(df["period"])
    df["symbol"] = df["symbol"].astype("string")
    return df


def build_features(cfg, df: pd.DataFrame) -> pd.DataFrame:
    out = features.sort_panel(df)
    out = features.add_sequence_info(out, cfg)
    out, _ = features.add_levels(out, cfg)
    return features.add_trend_features(out, cfg)


@pytest.fixture()
def synthetic(seed):
    return make_synthetic_panel(seed)


@pytest.fixture(scope="session")
def real_inputs(cfg):
    if not cfg["_paths"]["panel_csv"].exists() or not cfg["_paths"]["relevance_csv"].exists():
        pytest.skip("source data not available")
    panel_raw, load_rep = data_io.load_panel(cfg)
    rel, _ = data_io.load_relevance(cfg)
    return panel_raw, rel, load_rep


@pytest.fixture(scope="session")
def real_panel(cfg, real_inputs):
    panel_raw, rel, _ = real_inputs
    panel, _ = pipeline.build_research_panel(cfg, panel_raw=panel_raw.copy(), relevance=rel)
    return panel


@pytest.fixture(scope="session")
def raw_csv(cfg):
    if not cfg["_paths"]["panel_csv"].exists():
        pytest.skip("source data not available")
    return data_io.read_csv_raw(cfg["_paths"]["panel_csv"], encoding="utf-8-sig", dtype={"symbol": str})

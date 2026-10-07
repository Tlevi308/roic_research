"""Shared fixtures: config, a seeded synthetic panel, and the real panel.

Synthetic data for logic, the real parquet for facts. Tests that need the real
file skip rather than fail when it is absent.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import data_io, pipeline, schema  # noqa: E402

SOURCE_COLUMNS = ("gvkey", "tic", "conm", "gsector", "datadate", "actq", "lctq",
                  "ppentq", "gdwlq", "atq", "ltq", "oiadpq", "piq", "txtq",
                  "dlcq", "dlttq", "mkvaltq")


@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    return data_io.load_config(ROOT / "config.yaml")


@pytest.fixture(scope="session")
def seed(cfg: dict[str, Any]) -> int:
    return int(cfg["project"]["seed"])


def make_source(seed: int, tickers: tuple[str, ...] = ("AAA", "BBB", "CCC"),
                first: str = "2018Q1", n_quarters: int = 12,
                month_offset: int = 0) -> pd.DataFrame:
    """Random but seeded source rows, with the column names the loader expects.

    ``month_offset`` shifts the fiscal calendar so a non-December fiscal year
    can be exercised; the period rule then maps the dates on its own.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i, tic in enumerate(tickers):
        period = pd.Period(first, freq="Q")
        for q in range(n_quarters):
            end = (period + q).end_time.normalize() + pd.DateOffset(months=month_offset)
            rows.append({
                "gvkey": f"{i + 1:06d}", "tic": tic, "conm": f"{tic} CORP",
                "gsector": "45", "datadate": end.normalize(),
                "actq": 500 + rng.normal(0, 40), "lctq": 300 + rng.normal(0, 30),
                "ppentq": 400 + rng.normal(0, 25), "gdwlq": 100 + rng.normal(0, 5),
                "atq": 2000 + rng.normal(0, 90), "ltq": 1200 + rng.normal(0, 70),
                "oiadpq": 60 + rng.normal(0, 8), "piq": 55 + rng.normal(0, 8),
                "txtq": 13 + rng.normal(0, 2),
                "dlcq": 50 + rng.normal(0, 5), "dlttq": 400 + rng.normal(0, 20),
                "mkvaltq": 5000 + rng.normal(0, 400),
            })
    return pd.DataFrame(rows)[list(SOURCE_COLUMNS)]


def build(cfg: dict[str, Any], source: pd.DataFrame) -> pd.DataFrame:
    """Run the pipeline on an injected source frame."""
    return pipeline.build_panel(cfg, raw=source.copy())[0]


def build_with_report(cfg: dict[str, Any], source: pd.DataFrame):
    return pipeline.build_panel(cfg, raw=source.copy())


@pytest.fixture()
def synthetic(cfg: dict[str, Any], seed: int) -> pd.DataFrame:
    return build(cfg, make_source(seed))


@pytest.fixture(scope="session")
def real_panel(cfg: dict[str, Any]):
    """The real panel, built once. Skips when the source file is absent."""
    if not cfg["_paths"]["input_parquet"].exists():
        pytest.skip("source data not available")
    panel, report = pipeline.build_panel(cfg)
    return panel, report


@pytest.fixture(scope="session")
def real_tables(cfg: dict[str, Any]):
    """Validation tables from the last recorded run, if the run has happened."""
    tables = cfg["_paths"]["output_dir"] / "tables"
    if not tables.exists():
        pytest.skip("no run output available")
    return tables


def column_values(panel: pd.DataFrame, column: str) -> np.ndarray:
    return panel[column].astype("float64").to_numpy()


def labels_of(panel: pd.DataFrame, column: str) -> pd.Series:
    return panel[column].astype("string")

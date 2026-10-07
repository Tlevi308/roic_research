"""Level computations: tax, NOPAT, invested capital, ROIC, debt, equity.

Rules enforced here
-------------------
* A zero or missing denominator gives NaN. Never 0, never inf.
* The tax rate is **never** clipped to [0, 1]. Out-of-range rates are kept as
  reported and flagged, which is the whole point of the quality-flag columns.
* Working capital is mandatory: missing current assets or current liabilities
  leaves invested capital NaN rather than collapsing it to PPE + goodwill.
  Missing PPE or goodwill counts as zero.
* ``calc_average_ic_raw_quarterly`` is the only averaging in the package. The
  bridges read that column at t and t-1 and never average again.
* Annualisation is defined only when ``1 + r > 0``: at r <= -100% the even power
  would return the sign and invert the answer.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .data_io import safe_div
from .keys import Grid

LOGGER = logging.getLogger("roic.levels")


def tax_expense(tax_provision_raw: np.ndarray) -> np.ndarray:
    """Compustat ``txtq`` is already positive-for-expense, so nothing is flipped.

    The dictionary writes ``calc_tax_expense_quarterly = -tax_provision`` because
    GuruFocus reports the provision negative; with ``tax_provision = -txtq`` the
    two sign flips cancel and the column is ``txtq`` itself.
    """
    return np.asarray(tax_provision_raw, dtype="float64")


def raw_tax_rate(tax_expense_q: np.ndarray, pretax_income: np.ndarray) -> np.ndarray:
    """``tax expense / pretax income``, unclipped. Zero pretax income -> NaN."""
    return safe_div(tax_expense_q, pretax_income)


def nopat(ebit: np.ndarray, tax_rate: np.ndarray) -> np.ndarray:
    """``E * (1 - T)``. Kept multiplicative so the bridge stays separable."""
    return np.asarray(ebit, "float64") * (1.0 - np.asarray(tax_rate, "float64"))


def invested_capital(total_current_assets: np.ndarray, total_current_liabilities: np.ndarray,
                     net_ppe: np.ndarray, goodwill: np.ndarray) -> np.ndarray:
    """``TCA - TCL + PPE + Goodwill``, with PPE and goodwill missing counted as 0.

    Banks and insurers report an unclassified balance sheet and have no current
    assets or liabilities; those firms keep NaN instead of an invented capital
    base. A single missing goodwill line, by contrast, should not delete a
    quarter - Apple stopped reporting goodwill separately.
    """
    tca = np.asarray(total_current_assets, "float64")
    tcl = np.asarray(total_current_liabilities, "float64")
    ppe = np.nan_to_num(np.asarray(net_ppe, "float64"), nan=0.0)
    gw = np.nan_to_num(np.asarray(goodwill, "float64"), nan=0.0)
    return tca - tcl + ppe + gw


def average_invested_capital(ic: np.ndarray, grid: Grid) -> np.ndarray:
    """``(IC(t-1) + IC(t)) / 2``, only across a consecutive quarter pair."""
    prev = grid.lag(ic)
    return (prev + np.asarray(ic, "float64")) / 2.0


def annualize(quarterly: np.ndarray, periods: int = 4) -> np.ndarray:
    """``(1 + r)^periods - 1``, NaN when ``1 + r <= 0``.

    Compounding, not ``r * 4``, so the screen compares two rates on the same
    horizon. The guard matters: a quarterly -200% would otherwise come back as
    a positive number through the even power.
    """
    r = np.asarray(quarterly, "float64")
    base = 1.0 + r
    out = np.full(r.shape, np.nan, dtype="float64")
    ok = np.isfinite(base) & (base > 0)
    out[ok] = base[ok] ** periods - 1.0
    return out


def debt_value(short_term_debt: np.ndarray, long_term_debt: np.ndarray) -> np.ndarray:
    """``dlcq + dlttq``, a missing leg counted as 0, NaN when both are missing.

    ``dlttq`` includes capitalised lease obligations and ``dlcq`` the current
    portion of long-term debt; ASC 842 operating leases are in neither.
    """
    st = np.asarray(short_term_debt, "float64")
    lt = np.asarray(long_term_debt, "float64")
    present = np.isfinite(st) | np.isfinite(lt)
    total = np.nan_to_num(st, nan=0.0) + np.nan_to_num(lt, nan=0.0)
    return np.where(present, total, np.nan)


def equity_from_balance_sheet(total_assets: np.ndarray,
                              total_liabilities: np.ndarray) -> np.ndarray:
    """``assets - liabilities`` (researcher 2026-10-05).

    By the balance-sheet identity this is total equity *including* minority
    interest - the ``teqq`` concept - and it matches ``teqq`` to within 0.01 on
    93.1% of the rows where both exist, at 2.7x the coverage.
    """
    return np.asarray(total_assets, "float64") - np.asarray(total_liabilities, "float64")


def ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Public alias for the guarded division, used by the registry specs."""
    return safe_div(numerator, denominator)

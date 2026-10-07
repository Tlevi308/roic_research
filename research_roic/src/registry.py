"""The calc registry: one declared spec per block of output columns.

Rules enforced here
-------------------
* Every output column is produced by exactly one ``CalcSpec``. The engine sorts
  the specs by their declared dependencies, so adding a column means adding one
  spec - the column list is never written twice.
* A spec whose input is absent from the file emits its columns as NaN with
  reason ``MISSING_INPUT:<field>``; a spec named in ``calcs.disabled`` emits NaN
  with reason ``DISABLED``. The two are reported separately, so an empty column
  always says *why* it is empty.
* The gating difference is deliberate: EBIT and tax-rate movements are gated on
  the NOPAT status, because a zero or missing capital base says nothing about
  whether EBIT rose. Only the IC movement and the raw combination are gated on
  the ROIC status, and the sign regimes are gated on neither.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import explain, labels as L, levels, shapley
from .data_io import safe_div
from .keys import Grid

LOGGER = logging.getLogger("roic.registry")


# ---------------------------------------------------------------------------
# the context handed to every spec
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    """Shared state: the config, the quarter grid, and the columns built so far."""
    cfg: dict[str, Any]
    grid: Grid
    data: dict[str, Any] = field(default_factory=dict)
    codes: dict[str, np.ndarray] = field(default_factory=dict)
    inter: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return self.grid.n

    def num(self, name: str) -> np.ndarray:
        return np.asarray(self.data[name], dtype="float64")

    def lag(self, name: str) -> np.ndarray:
        return self.grid.lag(self.num(name))

    def band(self, before: np.ndarray, after: np.ndarray, kind: str) -> np.ndarray:
        return L.band(before, after, kind, self.cfg)

    def put_label(self, name: str, codes: np.ndarray, levels_: Any) -> None:
        self.codes[name] = np.asarray(codes)
        self.data[name] = L.to_categorical(codes, levels_)

    def put_num(self, name: str, values: np.ndarray, valid: np.ndarray | None = None) -> None:
        v = np.asarray(values, dtype="float64")
        if valid is not None:
            v = np.where(np.asarray(valid, dtype=bool), v, np.nan)
        self.data[name] = v

    def nan(self) -> np.ndarray:
        return np.full(self.n, np.nan, dtype="float64")

    def put_counts(self, name: str, counts: np.ndarray, valid: np.ndarray) -> None:
        """Nullable Int8 column: pd.NA, not 0, wherever the status is not VALID."""
        arr = pd.array(np.asarray(counts, dtype="int8"), dtype="Int8")
        arr[~np.asarray(valid, dtype=bool)] = pd.NA
        self.data[name] = arr

    def put_flag(self, name: str, flag: np.ndarray, valid: np.ndarray) -> None:
        """Nullable boolean column: pd.NA wherever the status is not VALID."""
        arr = pd.array(np.asarray(flag, dtype=bool), dtype="boolean")
        arr[~np.asarray(valid, dtype=bool)] = pd.NA
        self.data[name] = arr


@dataclass(frozen=True)
class CalcSpec:
    """One block of output columns, with its dependencies declared."""
    name: str
    produces: tuple[str, ...]
    requires: tuple[str, ...]
    fn: Callable[[Ctx], None]
    note: str = ""


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------
def _tax(ctx: Ctx) -> None:
    expense = levels.tax_expense(ctx.num("tax_provision_raw"))
    ctx.put_num("calc_tax_expense_quarterly", expense)
    ctx.put_num("calc_raw_tax_rate_quarterly",
                levels.raw_tax_rate(expense, ctx.num("pretax_income")))


def _nopat(ctx: Ctx) -> None:
    ctx.put_num("calc_nopat_quarterly",
                levels.nopat(ctx.num("ebit"), ctx.num("calc_raw_tax_rate_quarterly")))


def _invested_capital(ctx: Ctx) -> None:
    ic = levels.invested_capital(ctx.num("total_current_assets"),
                                 ctx.num("total_current_liabilities"),
                                 ctx.num("net_ppe"), ctx.num("goodwill"))
    ctx.put_num("calc_ic_raw", ic)
    ctx.put_num("calc_average_ic_raw_quarterly",
                levels.average_invested_capital(ic, ctx.grid))


def _roic(ctx: Ctx) -> None:
    periods = int(ctx.cfg["decomposition"]["annualize_periods"])
    avg_ic = ctx.num("calc_average_ic_raw_quarterly")
    for stem, numerator in (("pretax", ctx.num("ebit")),
                            ("posttax", ctx.num("calc_nopat_quarterly"))):
        quarterly = safe_div(numerator, avg_ic)
        ctx.put_num(f"calc_roic_{stem}_quarterly_ic_raw", quarterly)
        ctx.put_num(f"calc_roic_{stem}_annualized_ic_raw",
                    levels.annualize(quarterly, periods))


def _nopat_bridge(ctx: Ctx) -> None:
    e0, e1 = ctx.lag("ebit"), ctx.num("ebit")
    t0, t1 = ctx.lag("calc_raw_tax_rate_quarterly"), ctx.num("calc_raw_tax_rate_quarterly")
    q0, q1 = 1.0 - t0, 1.0 - t1

    missing = ~(np.isfinite(e0) & np.isfinite(e1) & np.isfinite(t0) & np.isfinite(t1))
    status = L.first_match(
        [~ctx.grid.prev_ok, missing],
        [L.NOPAT_STATUS_LEVELS.index("UNCLASSIFIED_NONCONSECUTIVE"),
         L.NOPAT_STATUS_LEVELS.index("UNCLASSIFIED_MISSING_DATA")],
        default=L.NOPAT_STATUS_LEVELS.index("VALID"), n=ctx.n)
    valid = status == L.NOPAT_STATUS_LEVELS.index("VALID")
    ctx.put_label("calc_nopat_decomposition_status", status, L.NOPAT_STATUS_LEVELS)
    ctx.codes["_nopat_valid"] = valid

    bridge = shapley.nopat_bridge(e0, e1, q0, q1)
    width = ctx.band(bridge["before"], bridge["after"], "money")
    ctx.put_num("calc_nopat_change_quarterly", bridge["change"], valid)
    ctx.put_num("calc_nopat_ebit_contribution", bridge["ebit"], valid)
    ctx.put_num("calc_nopat_tax_contribution", bridge["tax"], valid)
    ctx.put_num("calc_nopat_decomposition_residual", bridge["residual"], valid)

    ctx.put_label("calc_nopat_change_direction",
                  L.gate(L.classify_three_way(bridge["change"], width), valid),
                  L.DIRECTION_LEVELS)
    effects = []
    for stem, series in (("ebit", bridge["ebit"]), ("tax", bridge["tax"])):
        codes = L.gate(L.classify_three_way(series, width), valid)
        ctx.put_label(f"calc_nopat_{stem}_effect", codes, L.EFFECT_LEVELS)
        effects.append(codes)
    ctx.put_label("calc_nopat_effect_combination",
                  L.combine(effects, [L.EFFECT_TOKENS] * 2,
                            L.nopat_effect_combination_levels()),
                  L.nopat_effect_combination_levels())


def _roic_bridge(ctx: Ctx) -> None:
    e0, e1 = ctx.lag("ebit"), ctx.num("ebit")
    t0, t1 = ctx.lag("calc_raw_tax_rate_quarterly"), ctx.num("calc_raw_tax_rate_quarterly")
    i0, i1 = ctx.lag("calc_average_ic_raw_quarterly"), ctx.num("calc_average_ic_raw_quarterly")
    q0, q1 = 1.0 - t0, 1.0 - t1

    missing = ~(np.isfinite(e0) & np.isfinite(e1) & np.isfinite(t0) & np.isfinite(t1)
                & np.isfinite(i0) & np.isfinite(i1))
    zero_ic = ~missing & ((i0 == 0) | (i1 == 0))
    status = L.first_match(
        [~ctx.grid.prev_ok, missing, zero_ic],
        [L.ROIC_STATUS_LEVELS.index("UNCLASSIFIED_NONCONSECUTIVE"),
         L.ROIC_STATUS_LEVELS.index("UNCLASSIFIED_MISSING_DATA"),
         L.ROIC_STATUS_LEVELS.index("UNCLASSIFIED_ZERO_IC")],
        default=L.ROIC_STATUS_LEVELS.index("VALID"), n=ctx.n)
    valid = status == L.ROIC_STATUS_LEVELS.index("VALID")
    ctx.put_label("calc_roic_decomposition_status", status, L.ROIC_STATUS_LEVELS)
    ctx.codes["_roic_valid"] = valid
    ctx.codes["_roic_status"] = status
    ctx.inter["ic_before"] = i0
    ctx.inter["ic_after"] = i1
    ctx.inter["tax_before"] = t0

    bridge = shapley.roic_bridge(e0, e1, q0, q1, i0, i1)
    width = ctx.band(bridge["before"], bridge["after"], "ratio")
    ctx.put_num("calc_roic_posttax_change_quarterly", bridge["change"], valid)
    ctx.put_num("calc_roic_decomposition_residual", bridge["residual"], valid)
    direction = L.gate(L.classify_three_way(bridge["change"], width), valid)
    ctx.put_label("calc_roic_posttax_change_direction", direction, L.DIRECTION_LEVELS)

    effects = []
    for stem in ("ebit", "tax", "ic"):
        ctx.put_num(f"calc_roic_{stem}_contribution", bridge[stem], valid)
        codes = L.gate(L.classify_three_way(bridge[stem], width), valid)
        ctx.put_label(f"calc_roic_{stem}_effect", codes, L.EFFECT_LEVELS)
        effects.append(codes)
    ctx.codes["_roic_effects"] = np.stack(effects)
    ctx.put_label("calc_roic_effect_combination",
                  L.combine(effects, [L.EFFECT_TOKENS] * 3,
                            L.roic_effect_combination_levels()),
                  L.roic_effect_combination_levels())


def _movements(ctx: Ctx) -> None:
    nopat_valid = ctx.codes["_nopat_valid"]
    roic_valid = ctx.codes["_roic_valid"]

    e0, e1 = ctx.lag("ebit"), ctx.num("ebit")
    t0, t1 = ctx.lag("calc_raw_tax_rate_quarterly"), ctx.num("calc_raw_tax_rate_quarterly")
    i0, i1 = ctx.inter["ic_before"], ctx.inter["ic_after"]

    ebit_mv = L.gate(L.classify_three_way(e1 - e0, ctx.band(e0, e1, "money")), nopat_valid)
    tax_mv = L.gate(L.classify_three_way(t1 - t0, ctx.band(t0, t1, "ratio")), nopat_valid)
    ic_mv = L.gate(L.classify_three_way(i1 - i0, ctx.band(i0, i1, "money")), roic_valid)
    ctx.put_label("calc_ebit_movement", ebit_mv, L.MOVEMENT_LEVELS)
    ctx.put_label("calc_tax_rate_movement", tax_mv, L.MOVEMENT_LEVELS)
    ctx.put_label("calc_ic_movement", ic_mv, L.MOVEMENT_LEVELS)

    combo = L.combine([L.gate(ebit_mv, roic_valid), L.gate(tax_mv, roic_valid), ic_mv],
                      [L.MOVEMENT_TOKENS] * 3, L.raw_movement_combination_levels())
    ctx.put_label("calc_raw_movement_combination", combo,
                  L.raw_movement_combination_levels())


def _driver_stats(ctx: Ctx) -> None:
    valid = ctx.codes["_roic_valid"]
    eff = ctx.codes["_roic_effects"]                      # (3, n) of 0/1/2/-1
    pos = (eff == 0).sum(axis=0)
    neg = (eff == 1).sum(axis=0)
    zer = (eff == 2).sum(axis=0)
    act = pos + neg
    ctx.codes["_pos"], ctx.codes["_neg"], ctx.codes["_act"] = pos, neg, act

    for name, counts in (("positive", pos), ("negative", neg),
                         ("neutral", zer), ("active", act)):
        ctx.put_counts(f"calc_roic_{name}_driver_count", counts, valid)
    ctx.put_flag("calc_roic_has_opposing_effects", (pos > 0) & (neg > 0), valid)

    direction = ctx.codes["calc_roic_posttax_change_direction"]
    inc = L.DIRECTION_LEVELS.index("INCREASE")
    dec = L.DIRECTION_LEVELS.index("DECREASE")
    idx = {name: i for i, name in enumerate(L.STRUCTURE_LEVELS)}
    structure = L.first_match(
        [~valid, act == 0, (neg == 0) & (act == 1), neg == 0,
         (pos == 0) & (act == 1), pos == 0, direction == inc, direction == dec],
        [idx[L.UNCLASSIFIED], idx["NO_MATERIAL_CHANGE"], idx["SINGLE_POSITIVE_DRIVER"],
         idx["ALL_POSITIVE"], idx["SINGLE_NEGATIVE_DRIVER"], idx["ALL_NEGATIVE"],
         idx["MIXED_NET_INCREASE"], idx["MIXED_NET_DECREASE"]],
        default=idx["MIXED_FULL_OFFSET"], n=ctx.n)
    ctx.put_label("calc_roic_effect_structure", structure, L.STRUCTURE_LEVELS)


def _dominance(ctx: Ctx) -> None:
    valid = ctx.codes["_roic_valid"]
    act = ctx.codes["_act"]
    contrib = np.stack([ctx.num(f"calc_roic_{s}_contribution") for s in ("ebit", "tax", "ic")])
    absolute = np.abs(contrib)
    total = absolute.sum(axis=0)                 # NaN propagates: min_count = 3
    ctx.put_num("calc_roic_total_absolute_contribution", total, valid)

    material = valid & (act > 0)
    shares = np.vstack([safe_div(absolute[i], total) for i in range(3)])
    for i, stem in enumerate(("ebit", "tax", "ic")):
        ctx.put_num(f"calc_roic_{stem}_absolute_share", shares[i], material)

    # rank the shares only where all three are present: a row with a missing
    # share is never dominance-classified anyway (the ladder catches it first),
    # and subtracting two sentinels would raise an invalid-value warning
    ranked = np.where(np.isfinite(shares), shares, 0.0)
    ordered = np.sort(ranked, axis=0)
    gap = ordered[2] - ordered[1]
    top = np.argmax(ranked, axis=0)
    idx = {name: i for i, name in enumerate(L.DOMINANT_LEVELS)}
    dominant = L.first_match(
        [~valid, act == 0, gap <= float(ctx.cfg["decomposition"]["dominance_gap"])],
        [idx[L.UNCLASSIFIED], idx["NONE"], idx["BALANCED"]],
        default=-1, n=ctx.n)
    resolved = dominant < 0
    dominant[resolved] = top[resolved]           # argmax order matches EBIT/TAX/IC = 0/1/2
    ctx.put_label("calc_roic_dominant_driver", dominant, L.DOMINANT_LEVELS)

    eff = ctx.codes["_roic_effects"]
    neutral = L.EFFECT_LEVELS.index("NEUTRAL")
    effect = np.full(ctx.n, -1, dtype="int8")
    effect[dominant == idx["NONE"]] = neutral
    for i in range(3):
        pick = dominant == i
        effect[pick] = eff[i][pick]
    ctx.put_label("calc_roic_dominant_driver_effect", effect, L.EFFECT_LEVELS)

    change = np.abs(ctx.num("calc_roic_posttax_change_quarterly"))
    offset = np.clip(1.0 - safe_div(change, total), 0.0, 1.0)
    ctx.put_num("calc_roic_offset_ratio", offset, material)


def _sign_regimes(ctx: Ctx) -> None:
    for stem, column in (("ebit", "ebit"), ("nopat", "calc_nopat_quarterly")):
        x0, x1 = ctx.lag(column), ctx.num(column)
        width = ctx.band(x0, x1, "sign")
        before = L.classify_sign(x0, width)
        after = L.classify_sign(x1, width)
        ctx.codes[f"_{stem}_sign_before"] = before
        ctx.codes[f"_{stem}_sign_after"] = after
        ctx.put_label(f"calc_{stem}_sign_regime",
                      L.combine([before, after], [L.SIGN_TOKENS] * 2,
                                L.sign_regime_levels()),
                      L.sign_regime_levels())


def _quality(ctx: Ctx) -> None:
    t1 = ctx.num("calc_raw_tax_rate_quarterly")
    idx = {name: i for i, name in enumerate(L.TAX_FLAG_LEVELS)}
    tax_flag = L.first_match(
        [~np.isfinite(t1), t1 < 0, t1 > 1],
        [idx["MISSING"], idx["NEGATIVE_TAX_RATE"], idx["ABOVE_100_PERCENT"]],
        default=idx["VALID"], n=ctx.n)
    ctx.put_label("calc_tax_rate_quality_flag", tax_flag, L.TAX_FLAG_LEVELS)

    valid = ctx.codes["_roic_valid"]
    i0, i1 = ctx.inter["ic_before"], ctx.inter["ic_after"]
    t0 = ctx.inter["tax_before"]
    out_of_range = ((t0 < 0) | (t0 > 1) | (t1 < 0) | (t1 > 1))
    ridx = {name: i for i, name in enumerate(L.ROIC_FLAG_LEVELS)}
    roic_flag = L.first_match(
        [~valid, (i0 < 0) | (i1 < 0), out_of_range],
        [ridx[L.UNCLASSIFIED], ridx["NEGATIVE_IC_MECHANICAL_ONLY"],
         ridx["TAX_RATE_OUT_OF_RANGE"]],
        default=ridx["VALID"], n=ctx.n)
    ctx.put_label("calc_roic_quality_flag", roic_flag, L.ROIC_FLAG_LEVELS)


def _explanation(ctx: Ctx) -> None:
    valid = ctx.codes["_roic_valid"]
    roic_flag = ctx.codes["calc_roic_quality_flag"]
    negative_ic = roic_flag == L.ROIC_FLAG_LEVELS.index("NEGATIVE_IC_MECHANICAL_ONLY")
    before = ctx.codes["_nopat_sign_before"]
    after = ctx.codes["_nopat_sign_after"]
    profit, loss = L.SIGN_TOKENS.index("PROFIT"), L.SIGN_TOKENS.index("LOSS")
    direction = ctx.codes["calc_roic_posttax_change_direction"]
    inc = L.DIRECTION_LEVELS.index("INCREASE")
    dec = L.DIRECTION_LEVELS.index("DECREASE")

    headline = L.first_match(
        [negative_ic,
         (before == loss) & (after == profit),
         (before == profit) & (after == loss),
         (after == loss) & (direction == inc),
         (after == loss) & (direction == dec),
         direction == inc,
         direction == dec],
        list(range(7)), default=7, n=ctx.n)

    ids = explain.explanation_ids(headline, ctx.codes["calc_roic_effect_combination"],
                                  ctx.codes["calc_roic_dominant_driver"],
                                  ctx.codes["_roic_status"], valid)
    catalogue = explain.build_catalogue()
    ctx.data["calc_roic_explanation"] = explain.render(ids, catalogue)
    ctx.inter["explanation_catalogue"] = catalogue
    ctx.inter["explanation_ids"] = ids


def _debt(ctx: Ctx) -> None:
    ctx.put_num("calc_debt_value_quarterly",
                levels.debt_value(ctx.num("short_term_debt"), ctx.num("long_term_debt")))


def _equity(ctx: Ctx) -> None:
    equity = levels.equity_from_balance_sheet(ctx.num("total_assets"),
                                              ctx.num("total_liabilities"))
    ctx.put_num("equity", equity)
    ctx.put_num("calc_debt_to_equity_quarterly",
                safe_div(ctx.num("calc_debt_value_quarterly"), equity))


def _free_cash_flow(ctx: Ctx) -> None:
    """Quarterly FCF from the year-to-date items - disabled in this build.

    ``oancfy``/``capxy`` are fiscal-year-to-date and the pull carries no
    ``fyearq``/``fqtr``, so the quarter that opens the fiscal year cannot be
    identified from the data alone. Enabling this spec requires those fields.
    """
    for name in ("free_cash_flow", "calc_free_cash_flow_ttm", "calc_ev_to_fcf_quarterly"):
        ctx.put_num(name, ctx.nan())


# ---------------------------------------------------------------------------
# the declared registry
# ---------------------------------------------------------------------------
REGISTRY: tuple[CalcSpec, ...] = (
    CalcSpec("tax", ("calc_tax_expense_quarterly", "calc_raw_tax_rate_quarterly"),
             ("tax_provision_raw", "pretax_income"), _tax,
             "txtq is already positive-for-expense; no sign flip"),
    CalcSpec("nopat", ("calc_nopat_quarterly",),
             ("ebit", "calc_raw_tax_rate_quarterly"), _nopat),
    CalcSpec("invested_capital", ("calc_ic_raw", "calc_average_ic_raw_quarterly"),
             ("total_current_assets", "total_current_liabilities", "net_ppe", "goodwill"),
             _invested_capital, "working capital mandatory; PPE/goodwill missing = 0"),
    CalcSpec("roic", ("calc_roic_pretax_quarterly_ic_raw",
                      "calc_roic_pretax_annualized_ic_raw",
                      "calc_roic_posttax_quarterly_ic_raw",
                      "calc_roic_posttax_annualized_ic_raw"),
             ("ebit", "calc_nopat_quarterly", "calc_average_ic_raw_quarterly"), _roic),
    CalcSpec("nopat_bridge",
             ("calc_nopat_decomposition_status", "calc_nopat_change_quarterly",
              "calc_nopat_change_direction", "calc_nopat_ebit_contribution",
              "calc_nopat_tax_contribution", "calc_nopat_decomposition_residual",
              "calc_nopat_ebit_effect", "calc_nopat_tax_effect",
              "calc_nopat_effect_combination"),
             ("ebit", "calc_raw_tax_rate_quarterly"), _nopat_bridge,
             "2-factor Shapley; not gated on invested capital"),
    CalcSpec("roic_bridge",
             ("calc_roic_decomposition_status", "calc_roic_posttax_change_quarterly",
              "calc_roic_posttax_change_direction", "calc_roic_ebit_contribution",
              "calc_roic_tax_contribution", "calc_roic_ic_contribution",
              "calc_roic_decomposition_residual", "calc_roic_ebit_effect",
              "calc_roic_tax_effect", "calc_roic_ic_effect",
              "calc_roic_effect_combination"),
             ("ebit", "calc_raw_tax_rate_quarterly", "calc_average_ic_raw_quarterly"),
             _roic_bridge, "3-factor Shapley over 8 coalitions"),
    CalcSpec("movements", ("calc_ebit_movement", "calc_tax_rate_movement",
                           "calc_ic_movement", "calc_raw_movement_combination"),
             ("calc_nopat_decomposition_status", "calc_roic_decomposition_status"),
             _movements, "EBIT/tax gated on NOPAT status, IC on ROIC status"),
    CalcSpec("driver_stats", ("calc_roic_has_opposing_effects",
                              "calc_roic_positive_driver_count",
                              "calc_roic_negative_driver_count",
                              "calc_roic_neutral_driver_count",
                              "calc_roic_active_driver_count",
                              "calc_roic_effect_structure"),
             ("calc_roic_effect_combination", "calc_roic_posttax_change_direction"),
             _driver_stats),
    CalcSpec("dominance", ("calc_roic_total_absolute_contribution",
                           "calc_roic_ebit_absolute_share",
                           "calc_roic_tax_absolute_share",
                           "calc_roic_ic_absolute_share",
                           "calc_roic_dominant_driver",
                           "calc_roic_dominant_driver_effect",
                           "calc_roic_offset_ratio"),
             ("calc_roic_ebit_contribution", "calc_roic_tax_contribution",
              "calc_roic_ic_contribution", "calc_roic_effect_structure"), _dominance),
    CalcSpec("sign_regimes", ("calc_ebit_sign_regime", "calc_nopat_sign_regime"),
             ("ebit", "calc_nopat_quarterly"), _sign_regimes,
             "income-statement only: survives zero, negative or missing capital"),
    CalcSpec("quality", ("calc_tax_rate_quality_flag", "calc_roic_quality_flag"),
             ("calc_raw_tax_rate_quarterly", "calc_roic_decomposition_status"), _quality),
    CalcSpec("explanation", ("calc_roic_explanation",),
             ("calc_roic_quality_flag", "calc_nopat_sign_regime",
              "calc_roic_dominant_driver", "calc_roic_effect_combination"), _explanation,
             "closed catalogue; wording follows the Shapley signs"),
    CalcSpec("debt", ("calc_debt_value_quarterly",),
             ("short_term_debt", "long_term_debt"), _debt),
    CalcSpec("equity", ("equity", "calc_debt_to_equity_quarterly"),
             ("total_assets", "total_liabilities", "calc_debt_value_quarterly"), _equity,
             "equity = assets - liabilities (researcher 2026-10-05)"),
    CalcSpec("free_cash_flow", ("free_cash_flow", "calc_free_cash_flow_ttm",
                                "calc_ev_to_fcf_quarterly"),
             ("oancfy", "capxy"), _free_cash_flow,
             "needs fyearq/fqtr to split the year-to-date items"),
)


def run_registry(ctx: Ctx, available_fields: set[str],
                 disabled: set[str]) -> pd.DataFrame:
    """Run every spec in dependency order and report why any column is empty.

    A spec is skipped when it is disabled, when one of its required input
    fields is absent, or when a requirement is produced by a skipped spec. Its
    columns are then emitted as NaN - not dropped, so the schema never moves.
    """
    produced_by = {col: spec.name for spec in REGISTRY for col in spec.produces}
    skipped: dict[str, str] = {}
    rows: list[dict[str, Any]] = []

    for spec in _in_dependency_order():
        reasons = []
        if spec.name in disabled:
            reasons.append("DISABLED")
        for req in spec.requires:
            if req in produced_by:
                owner = produced_by[req]
                if owner in skipped:
                    reasons.append(f"UPSTREAM_SKIPPED:{owner}")
            elif req not in available_fields:
                reasons.append(f"MISSING_INPUT:{req}")
        reason = "; ".join(dict.fromkeys(reasons))
        if reason:
            skipped[spec.name] = reason
            for col in spec.produces:
                ctx.data[col] = ctx.nan()
            LOGGER.warning("Spec %-16s skipped (%s)", spec.name, reason)
        else:
            spec.fn(ctx)
        for col in spec.produces:
            rows.append({"column": col, "spec": spec.name,
                         "status": "SKIPPED" if reason else "COMPUTED",
                         "reason": reason, "note": spec.note})
    return pd.DataFrame(rows)


def _in_dependency_order() -> list[CalcSpec]:
    """Topological sort of the registry by declared produces/requires."""
    produced_by = {col: spec.name for spec in REGISTRY for col in spec.produces}
    by_name = {spec.name: spec for spec in REGISTRY}
    order: list[CalcSpec] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise ValueError(f"cyclic dependency through spec {name!r}")
        visiting.add(name)
        for req in by_name[name].requires:
            owner = produced_by.get(req)
            if owner is not None and owner != name:
                visit(owner)
        visiting.discard(name)
        done.add(name)
        order.append(by_name[name])

    for spec in REGISTRY:
        visit(spec.name)
    return order

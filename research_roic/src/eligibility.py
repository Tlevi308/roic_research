"""Index relevance: which firm-quarters belong to the investable population.

Rule (confirmed by the researcher, stored in config.yaml -> relevance):
    member(ticker, q)  <=>  start_quarter <= q < end_quarter
    empty end_quarter  =>   member from start_quarter through the latest quarter
q is the panel's ``period_key`` (the signal quarter).

Nothing here looks at returns: the population at portfolio formation is fixed
before, and independently of, the availability of realised future returns.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("roic.eligibility")


def validate_ranges(rel: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """Structural checks on the relevance file. Raises on overlapping ranges for one ticker."""
    r = cfg["relevance"]
    end_ok = rel["end_ord"].isna() | (rel["end_ord"] >= rel["start_ord"])
    same_q = rel["end_ord"].notna() & (rel["end_ord"] == rel["start_ord"])
    # a range [s, e) with e == s is empty under the exclusive-end rule
    empty_under_rule = same_q & (not r["end_inclusive"])
    overlaps = []
    for ticker, grp in rel.sort_values(["ticker", "start_ord"]).groupby("ticker"):
        if len(grp) < 2:
            continue
        prev_end = None
        for _, row in grp.iterrows():
            if prev_end is not None:
                last_member_prev = prev_end if r["end_inclusive"] else prev_end - 1
                if pd.isna(prev_end) or row["start_ord"] <= last_member_prev:
                    overlaps.append(ticker)
            prev_end = row["end_ord"]
    report = {
        "n_ranges": int(len(rel)),
        "n_tickers": int(rel["ticker"].nunique()),
        "n_tickers_with_multiple_ranges": int((rel["ticker"].value_counts() > 1).sum()),
        "n_end_before_start": int((~end_ok).sum()),
        "n_start_equals_end": int(same_q.sum()),
        "ranges_empty_under_rule": rel.loc[empty_under_rule, ["ticker", "start_quarter", "end_quarter"]],
        "overlapping_tickers": sorted(set(overlaps)),
        "n_na_like_tickers": int(rel["ticker"].str.upper().isin(["NA", "NAN", "NULL", "NONE", "N/A", "TRUE", "FALSE"]).sum()),
    }
    if report["n_end_before_start"]:
        raise ValueError("Relevance file has end_quarter < start_quarter rows")
    if report["overlapping_tickers"]:
        raise ValueError(f"Overlapping relevance ranges for: {report['overlapping_tickers']}")
    return report


def membership(panel: pd.DataFrame, rel: pd.DataFrame, cfg: dict[str, Any],
               symbol_col: str = "symbol", ord_col: str = "q_ord") -> pd.DataFrame:
    """Return a frame aligned to ``panel.index`` with ``in_index`` (bool) and ``relevance_range_id``."""
    r = cfg["relevance"]
    if r.get("compare_to", "period_key") != "period_key":
        raise ValueError("Only relevance.compare_to = period_key is implemented")
    left = pd.DataFrame({"_row": np.arange(len(panel)), "ticker": panel[symbol_col].astype("string").values,
                         "q": panel[ord_col].astype("Int64").values})
    m = left.merge(rel[["ticker", "start_ord", "end_ord", "open_end", "range_id"]], on="ticker", how="inner")
    after_start = (m["q"] >= m["start_ord"]) if r["start_inclusive"] else (m["q"] > m["start_ord"])
    if r["end_inclusive"]:
        before_end = m["end_ord"].isna() | (m["q"] <= m["end_ord"])
    else:
        before_end = m["end_ord"].isna() | (m["q"] < m["end_ord"])
    if not r.get("open_end_is_member_through_latest", True):
        before_end &= m["end_ord"].notna()
    hit = m.loc[(after_start & before_end).fillna(False).astype(bool) & m["q"].notna()]
    if hit["_row"].duplicated().any():
        raise ValueError("A firm-quarter matched more than one relevance range")
    out = pd.DataFrame({"in_index": False, "relevance_range_id": pd.array([pd.NA] * len(panel), dtype="Int64"),
                        "ticker_in_relevance_file": left["ticker"].isin(rel["ticker"]).values}, index=panel.index)
    out.iloc[hit["_row"].to_numpy(), out.columns.get_loc("in_index")] = True
    out.iloc[hit["_row"].to_numpy(), out.columns.get_loc("relevance_range_id")] = hit["range_id"].to_numpy()
    out["in_index"] = out["in_index"].astype(bool)
    return out


def expand_members(rel: pd.DataFrame, cfg: dict[str, Any], first_ord: int, last_ord: int) -> pd.DataFrame:
    """All (ticker, quarter) memberships implied by the file within [first_ord, last_ord]."""
    r = cfg["relevance"]
    rows = []
    for rec in rel.itertuples(index=False):
        s = int(rec.start_ord) if r["start_inclusive"] else int(rec.start_ord) + 1
        if pd.isna(rec.end_ord):
            e = last_ord
        else:
            e = int(rec.end_ord) if r["end_inclusive"] else int(rec.end_ord) - 1
        lo, hi = max(s, first_ord), min(e, last_ord)
        for q in range(lo, hi + 1):
            rows.append((rec.ticker, q, rec.range_id))
    out = pd.DataFrame(rows, columns=["ticker", "q_ord", "range_id"])
    labels = {q: str(pd.Period(ordinal=int(q), freq="Q")) for q in out["q_ord"].unique()}
    out["period_key"] = out["q_ord"].map(labels)
    return out


def coverage_by_quarter(members: pd.DataFrame, panel: pd.DataFrame, symbol_col: str = "symbol") -> pd.DataFrame:
    """Index members per quarter (relevance file) versus members that have a panel row."""
    have = panel[[symbol_col, "q_ord"]].dropna().drop_duplicates()
    have = have.assign(has_row=True).rename(columns={symbol_col: "ticker"})
    have["q_ord"] = have["q_ord"].astype("int64")
    j = members.merge(have, on=["ticker", "q_ord"], how="left")
    j["has_row"] = j["has_row"].astype("boolean").fillna(False).astype(bool)
    j["ticker_in_panel"] = j["ticker"].isin(panel[symbol_col].unique())
    out = j.groupby(["q_ord", "period_key"]).agg(
        index_members=("ticker", "size"),
        members_with_panel_row=("has_row", "sum"),
        members_ticker_absent_from_panel=("ticker_in_panel", lambda s: int((~s).sum())),
    ).reset_index()
    out["members_ticker_in_panel_but_no_row"] = out["index_members"] - out["members_with_panel_row"] - out["members_ticker_absent_from_panel"]
    out["coverage_share"] = out["members_with_panel_row"] / out["index_members"]
    return out.drop(columns="q_ord")


def missing_members_detail(rel: pd.DataFrame, panel: pd.DataFrame, first_ord: int, cfg: dict[str, Any],
                           manifest: pd.DataFrame | None = None, symbol_col: str = "symbol") -> pd.DataFrame:
    """Relevance ranges overlapping the study window whose ticker never appears in the panel."""
    r = cfg["relevance"]
    last_member = rel["end_ord"] if r["end_inclusive"] else rel["end_ord"] - 1
    overlaps = (rel["end_ord"].isna() | (last_member >= first_ord).fillna(False)).astype(bool)
    miss = rel.loc[overlaps & ~rel["ticker"].isin(panel[symbol_col].unique()),
                   ["ticker", "start_quarter", "end_quarter", "open_end"]].copy()
    if manifest is not None and len(manifest):
        info = manifest.rename(columns={"symbol": "ticker"})[["ticker", "status", "error"]]
        miss = miss.merge(info, on="ticker", how="left").rename(columns={"status": "download_status", "error": "download_error"})
    return miss.sort_values(["open_end", "end_quarter", "ticker"], ascending=[False, False, True]).reset_index(drop=True)


def reused_ticker_report(rel: pd.DataFrame, panel: pd.DataFrame, symbol_col: str = "symbol") -> pd.DataFrame:
    """Tickers with several ranges: which company/periods the panel holds for them."""
    multi = rel[rel["ticker"].duplicated(keep=False)]
    stocks = panel[panel[symbol_col].isin(multi["ticker"])]
    info = stocks.groupby(symbol_col).agg(panel_company=("company", lambda s: "; ".join(s.dropna().unique())),
                                          panel_first=("period_key", "min"), panel_last=("period_key", "max"))
    out = multi[["ticker", "start_quarter", "end_quarter", "range_id"]].merge(info, left_on="ticker", right_index=True, how="left")
    if "relevance_range_id" in stocks.columns:
        matched = stocks.groupby("relevance_range_id").agg(panel_rows_matched=("period_key", "size"),
                                                           matched_first=("period_key", "min"), matched_last=("period_key", "max"))
        out = out.merge(matched, left_on="range_id", right_index=True, how="left")
        out["panel_rows_matched"] = out["panel_rows_matched"].fillna(0).astype(int)
        n_ranges_used = out[out["panel_rows_matched"] > 0].groupby("ticker").size()
        out["ticker_rows_span_several_ranges"] = out["ticker"].map(n_ranges_used).fillna(0).astype(int) > 1
    return out.drop(columns="range_id").reset_index(drop=True)

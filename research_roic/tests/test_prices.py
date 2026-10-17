"""The price panel: the researcher's example, the endpoint rules, the security
match, the no-lookahead property, and the measured facts of the real file."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import keys, prices as P, schema

EXAMPLE = P.RESEARCHER_EXAMPLE_AAPL


# ---------------------------------------------------------------------------
# synthetic input, with the column names of prices.gz
# ---------------------------------------------------------------------------
def firm_rows(gvkey: str, tic: str, price: pd.Series, ajexm: float | pd.Series = 1.0,
              ipodate: str | None = None, dldte: str | None = None) -> pd.DataFrame:
    """One security's monthly rows; ``price`` (prccm) is indexed by month-end."""
    p = price.astype("float64")
    a = ajexm if isinstance(ajexm, pd.Series) else pd.Series(float(ajexm), index=p.index)
    adj = p / a
    return pd.DataFrame({
        "tic": tic, "datadate": p.index, "gvkey": gvkey, "conm": f"{tic} CORP",
        "exchg": 11.0,
        "dldte": pd.Timestamp(dldte) if dldte else pd.NaT,
        "ipodate": pd.Timestamp(ipodate) if ipodate else pd.NaT,
        "ajexm": a.to_numpy(), "prccm": p.to_numpy(),
        "trt1m": ((adj / adj.shift(1) - 1.0) * 100.0).to_numpy(),
    })


def random_price(seed: int, start: str = "2013-01-31", n: int = 60) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="ME")
    return pd.Series(50.0 * np.exp(np.cumsum(rng.normal(0.005, 0.08, n))), index=idx)


def tickers(*rows: tuple[str, str | None]) -> pd.DataFrame:
    return pd.DataFrame({"gvkey": [g for g, _ in rows],
                         "symbol": pd.array([s for _, s in rows], dtype="string"),
                         "company": [f"FIRM {g}" for g, _ in rows]})


def build(cfg, raw: pd.DataFrame, tick: pd.DataFrame):
    return P.build_prices(cfg, raw=raw.copy(), tickers=tick)


def three_firms(seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.concat([firm_rows("000001", "AAA", random_price(seed)),
                     firm_rows("000002", "BBB", random_price(seed + 1)),
                     firm_rows("000003", "CCC", random_price(seed + 2, n=40))],
                    ignore_index=True)
    return raw, tickers(("000001", "AAA"), ("000002", "BBB"), ("000003", "CCC"))


def as_strings(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.astype("string").reset_index(drop=True)


# ---------------------------------------------------------------------------
# the contract and the key
# ---------------------------------------------------------------------------
def test_contract_columns_and_dtypes(cfg, seed):
    out, _ = build(cfg, *three_firms(seed))
    assert list(out.columns) == list(schema.PRICE_COLUMNS)
    for col in schema.PRICE_COLUMNS:
        declared = schema.price_declared_dtype(col)
        actual = str(out[col].dtype)
        assert actual.startswith("string") if declared == "string" else actual == declared, col
    assert out["KEY"].notna().all() and not out["KEY"].duplicated().any()
    for col in ("Close", "Quaterly_return", "MomentomQ3"):
        assert not np.isinf(out[col].to_numpy()).any(), col


def test_months_map_to_quarters_and_key_matches_the_base_rule(cfg, seed):
    price = random_price(seed, start="2014-01-31", n=12)
    out, _ = build(cfg, firm_rows("000001", "AAA", price), tickers(("000001", "AAA")))
    assert out["KEY"].tolist() == ["2013Q4_AAA", "2014Q1_AAA", "2014Q2_AAA", "2014Q3_AAA"]
    assert out["Date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2014-02-28", "2014-05-31", "2014-08-31", "2014-11-30"]
    periods = keys.add_period_columns(out[["Date"]], cfg, date_col="Date")["period_key"]
    rebuilt = keys.build_key(periods, out["Ticker"], cfg)
    assert rebuilt.astype(str).tolist() == out["KEY"].astype(str).tolist()


# ---------------------------------------------------------------------------
# the researcher's own example
# ---------------------------------------------------------------------------
def test_reproduces_the_researcher_example(cfg):
    """prccm = the example's closes, ajexm = 1: every return and momentum matches."""
    idx = pd.date_range("2014-02-28", "2017-08-31", freq="ME")
    price = pd.Series(20.0, index=idx)            # months in between do not matter
    for period, _, close, _, _ in EXAMPLE:
        quarter_end = pd.Period(period, freq="Q").end_time.normalize()
        price[quarter_end + pd.offsets.MonthEnd(2)] = close      # Feb/May/Aug/Nov
    last_close, last_ret = EXAMPLE[-1][2], EXAMPLE[-1][3]
    price[pd.Timestamp("2017-08-31")] = last_close * (1.0 + last_ret)

    out, _ = build(cfg, firm_rows("001690", "AAPL", price), tickers(("001690", "AAPL")))
    got = out.set_index("KEY")
    for period, _, close, ret, mom in EXAMPLE:
        row = got.loc[f"{period}_AAPL"]
        assert row["Close"] == pytest.approx(close, abs=1e-12)
        assert row["Quaterly_return"] == pytest.approx(ret, abs=1e-8), period
        if np.isnan(mom):
            assert np.isnan(row["MomentomQ3"]), period
        else:
            assert row["MomentomQ3"] == pytest.approx(mom, abs=1e-8), period


def test_momentum_reads_three_quarters_back(cfg, seed):
    out, _ = build(cfg, *three_firms(seed))
    a = out[out["Ticker"] == "AAA"].set_index("KEY")
    assert a.loc["2014Q3_AAA", "MomentomQ3"] == pytest.approx(
        a.loc["2014Q3_AAA", "Close"] / a.loc["2013Q4_AAA", "Close"] - 1.0)
    assert a.loc["2013Q4_AAA", "Quaterly_return"] == pytest.approx(
        a.loc["2014Q1_AAA", "Close"] / a.loc["2013Q4_AAA", "Close"] - 1.0)


def test_momentum_is_the_product_of_the_three_previous_returns(cfg, seed):
    out, _ = build(cfg, *three_firms(seed))
    for _, g in out.groupby("Ticker"):
        r = g["Quaterly_return"].to_numpy()
        m = g["MomentomQ3"].to_numpy()
        for i in range(3, len(g)):
            assert m[i] == pytest.approx((1 + r[i - 3]) * (1 + r[i - 2]) * (1 + r[i - 1]) - 1,
                                         rel=1e-12)


# ---------------------------------------------------------------------------
# adjustment, "no price", and missing quarters
# ---------------------------------------------------------------------------
def test_split_leaves_close_and_returns_unchanged(cfg, seed):
    plain = random_price(seed)
    factor = pd.Series(np.where(plain.index < pd.Timestamp("2015-04-30"), 2.0, 1.0),
                       index=plain.index)
    split = plain * factor                         # 2:1 split: quoted price halves
    a, _ = build(cfg, firm_rows("000001", "AAA", plain), tickers(("000001", "AAA")))
    b, _ = build(cfg, firm_rows("000001", "AAA", split, ajexm=factor), tickers(("000001", "AAA")))
    for col in ("Close", "Quaterly_return", "MomentomQ3"):
        np.testing.assert_allclose(a[col].to_numpy(), b[col].to_numpy(), rtol=1e-12)


def test_zero_price_is_no_price_and_only_its_ratios_go_missing(cfg, seed):
    price = random_price(seed)
    base, _ = build(cfg, firm_rows("000001", "AAA", price), tickers(("000001", "AAA")))
    hit = price.copy()
    hit[pd.Timestamp("2015-05-31")] = 0.0          # quarter 2015Q1
    out, rep = build(cfg, firm_rows("000001", "AAA", hit), tickers(("000001", "AAA")))
    expect_nan = {("Close", "2015Q1_AAA"), ("Quaterly_return", "2015Q1_AAA"),
                  ("Quaterly_return", "2014Q4_AAA"), ("MomentomQ3", "2015Q1_AAA"),
                  ("MomentomQ3", "2015Q4_AAA")}
    b, o = base.set_index("KEY"), out.set_index("KEY")
    for col in ("Close", "Quaterly_return", "MomentomQ3"):
        for key in o.index:
            if (col, key) in expect_nan:
                assert np.isnan(o.loc[key, col]), (col, key)
            else:
                assert o.loc[key, col] == pytest.approx(b.loc[key, col], nan_ok=True), (col, key)
    assert rep["close_reason"][list(o.index).index("2015Q1_AAA")] == "ZERO_PRICE"

    between = price.copy()
    between[pd.Timestamp("2015-03-31")] = 0.0      # a month between two quarter months
    same, _ = build(cfg, firm_rows("000001", "AAA", between), tickers(("000001", "AAA")))
    pd.testing.assert_frame_equal(as_strings(same), as_strings(base))


def test_missing_quarter_rows_follow_the_endpoint_rule(cfg, seed):
    price = random_price(seed)
    base, _ = build(cfg, firm_rows("000001", "AAA", price), tickers(("000001", "AAA")))
    out, rep = build(cfg, firm_rows("000001", "AAA", price.drop(pd.Timestamp("2015-05-31"))),
                     tickers(("000001", "AAA")))
    b, o = base.set_index("KEY"), out.set_index("KEY")
    assert "2015Q1_AAA" not in o.index
    # q+1 missing: NaN, never Close(Aug)/Close(Feb) - 1
    assert np.isnan(o.loc["2014Q4_AAA", "Quaterly_return"])
    reasons = dict(zip(out["KEY"], rep["return_reason"]))
    assert reasons["2014Q4_AAA"] == "NEXT_QUARTER_MISSING"
    # q-3 missing: NaN
    assert np.isnan(o.loc["2015Q4_AAA", "MomentomQ3"])
    assert dict(zip(out["KEY"], rep["momentum_reason"]))["2015Q4_AAA"] == "BACK_QUARTER_MISSING"
    # q-1 missing: the endpoints q-3 and q are there, so momentum is unchanged
    assert o.loc["2015Q2_AAA", "MomentomQ3"] == pytest.approx(b.loc["2015Q2_AAA", "MomentomQ3"])
    assert o.loc["2015Q3_AAA", "MomentomQ3"] == pytest.approx(b.loc["2015Q3_AAA", "MomentomQ3"])


# ---------------------------------------------------------------------------
# no lookahead, no leakage, no hidden filter
# ---------------------------------------------------------------------------
def test_future_prices_change_only_the_forward_return(cfg, seed):
    raw, tick = three_firms(seed)
    base, _ = build(cfg, raw, tick)
    cutoff = pd.Timestamp("2015-05-31")
    shocked = raw.copy()
    future = shocked["datadate"] > cutoff
    rng = np.random.default_rng(seed + 7)
    shocked.loc[future, "prccm"] = rng.uniform(1, 500, int(future.sum()))
    after, _ = build(cfg, shocked, tick)
    before = base["Date"] < cutoff
    pd.testing.assert_frame_equal(as_strings(after[before.to_numpy()]),
                                  as_strings(base[before]))
    at = (base["Date"] == cutoff).to_numpy()
    for col in ("Close", "MomentomQ3", "ipodate", "dldte"):
        assert as_strings(after.loc[at, [col]]).equals(as_strings(base.loc[at, [col]])), col
    assert not np.allclose(after.loc[at, "Quaterly_return"], base.loc[at, "Quaterly_return"])


def test_one_firm_at_a_time_equals_the_full_run(cfg, seed):
    raw, tick = three_firms(seed)
    whole, _ = build(cfg, raw, tick)
    parts = [build(cfg, raw[raw["tic"] == t].reset_index(drop=True), tick)[0]
             for t in ("AAA", "BBB", "CCC")]
    pd.testing.assert_frame_equal(as_strings(pd.concat(parts)), as_strings(whole))


def test_listing_dates_pass_through_and_filter_nothing(cfg, seed):
    price = random_price(seed)
    plain, _ = build(cfg, firm_rows("000001", "AAA", price), tickers(("000001", "AAA")))
    dated, _ = build(cfg, firm_rows("000001", "AAA", price, ipodate="2014-06-15",
                                    dldte="2015-01-20"), tickers(("000001", "AAA")))
    assert (dated["ipodate"] == pd.Timestamp("2014-06-15")).all()
    assert (dated["dldte"] == pd.Timestamp("2015-01-20")).all()
    for col in ("KEY", "Date", "Ticker", "Close", "Quaterly_return", "MomentomQ3"):
        assert as_strings(dated[[col]]).equals(as_strings(plain[[col]])), col


@pytest.mark.filterwarnings("ignore:The behavior of DataFrame concatenation:FutureWarning")
def test_nan_reasons_name_the_gap(cfg, seed):
    gone = firm_rows("000001", "AAA", random_price(seed, n=27), dldte="2015-04-10")
    alive = firm_rows("000002", "BBB", random_price(seed + 1, n=60))
    out, rep = build(cfg, pd.concat([gone, alive], ignore_index=True),
                     tickers(("000001", "AAA"), ("000002", "BBB")))
    reasons = dict(zip(out["KEY"], rep["return_reason"]))
    assert reasons["2014Q4_AAA"] == "DELETED_BY_NEXT_DATE"    # last AAA row: Feb 2015
    assert reasons[out["KEY"][out["Ticker"] == "BBB"].iloc[-1]] == "NOT_YET_AVAILABLE"
    momentum = dict(zip(out["KEY"], rep["momentum_reason"]))
    for key in ("2012Q4_BBB", "2013Q1_BBB", "2013Q2_BBB"):   # the series starts Jan 2013
        assert momentum[key] == "BEFORE_FIRST_PRICE", key
    assert momentum["2013Q3_BBB"] == "OK"


# ---------------------------------------------------------------------------
# the security match
# ---------------------------------------------------------------------------
def test_only_the_exact_gvkey_and_ticker_is_used(cfg, seed):
    raw = pd.concat([
        firm_rows("000001", "XA", random_price(seed)),
        firm_rows("000001", "XB", random_price(seed + 1) * 3),   # a second security
        firm_rows("000002", "Y2", random_price(seed + 2)),       # base says Y1
        firm_rows("000009", "FO", random_price(seed + 3)),       # not in the base
    ], ignore_index=True)
    tick = tickers(("000001", "XA"), ("000002", "Y1"), ("000003", "Z"), ("000004", None))
    out, rep = build(cfg, raw, tick)
    assert set(out["Ticker"]) == {"XA"}
    xa = firm_rows("000001", "XA", random_price(seed))
    only, _ = build(cfg, xa, tickers(("000001", "XA")))
    pd.testing.assert_frame_equal(as_strings(out), as_strings(only))
    cat = rep["selection"].set_index("gvkey")["category"]
    assert cat["000001"] == "MATCHED" and cat["000002"] == "NO_TIC_MATCH"
    assert cat["000003"] == "NO_PRICE_ROWS" and cat["000004"] == "NO_SYMBOL_IN_BASE"
    assert rep["selection"].set_index("gvkey").loc["000002", "tickers_in_file"] == "Y2"
    assert rep["selection_counts"]["FILE_ONLY_NOT_IN_BASE"] == 1


def test_a_duplicate_month_raises_instead_of_choosing(cfg, seed):
    rows = firm_rows("000001", "AAA", random_price(seed))
    with pytest.raises(ValueError, match="share a"):
        build(cfg, pd.concat([rows, rows.iloc[[5]]], ignore_index=True),
              tickers(("000001", "AAA")))


def test_backward_lookup_refuses_to_look_forward():
    firm, idx, val = np.zeros(3, dtype=np.int64), np.arange(3), np.arange(3.0)
    np.testing.assert_array_equal(P.value_periods_back(firm, idx, val, 1)[1:], [0.0, 1.0])
    for k in (0, -1):
        with pytest.raises(ValueError):
            P.value_periods_back(firm, idx, val, k)


# ---------------------------------------------------------------------------
# the real file - measured on data/prices.gz as of 2026-10-17
# ---------------------------------------------------------------------------
SELECTION = {"MATCHED": 38_589, "NO_TIC_MATCH": 169, "NO_PRICE_ROWS": 2_150,
             "NO_SYMBOL_IN_BASE": 17, "FILE_ONLY_NOT_IN_BASE": 15_004,
             "MATCHED_WITHOUT_QUARTER_MONTH": 137}
COVERAGE = {"Close": 0.9752, "Quaterly_return": 0.9491, "MomentomQ3": 0.9076}


@pytest.fixture(scope="session")
def real_prices(cfg):
    if not cfg["_paths"]["prices_input"].exists() or not cfg["_paths"]["input_parquet"].exists():
        pytest.skip("price or base source not available")
    return P.build_prices(cfg)


def test_real_selection_and_row_counts(real_prices):
    out, rep = real_prices
    assert rep["selection_counts"] == SELECTION
    assert rep["rows_in_source"] == 8_572_102
    assert rep["rows_matched_monthly"] == 5_783_673
    assert len(out) == 1_925_749
    assert out["KEY"].notna().all() and not out["KEY"].duplicated().any()


def test_real_coverage_matches_the_measurement(real_prices, cfg):
    out, _ = real_prices
    tol = float(cfg["validation"]["coverage_tolerance_pp"]) / 100.0
    for col, expected in COVERAGE.items():
        assert abs(float(out[col].notna().mean()) - expected) <= tol, col


def test_real_aapl(real_prices):
    a = real_prices[0].set_index("KEY")
    assert a.loc["2013Q4_AAPL", "Close"] == pytest.approx(18.794286, abs=1e-6)
    assert a.loc["2013Q4_AAPL", "Quaterly_return"] == pytest.approx(0.202873, abs=1e-6)
    assert a.loc["2014Q3_AAPL", "MomentomQ3"] == pytest.approx(0.581997, abs=1e-6)


def test_real_dldte_exactly_on_inactive_firms(real_prices, cfg):
    out, rep = real_prices
    status = P.base_company_status(cfg)
    if status is None:
        pytest.skip("costat not in the base source")
    firms = pd.DataFrame({"gvkey": rep["quarter_keys"]["gvkey"].astype("string").to_numpy(),
                          "has_dldte": out["dldte"].notna().to_numpy()})
    per = firms.groupby("gvkey")["has_dldte"].agg(["min", "max"])
    assert (per["min"] == per["max"]).all()          # constant within a firm
    inactive = per.index.to_series().map(status).eq("I")
    assert (per["max"] == inactive).all()

# ROIC panel from Compustat Fundamentals Quarterly

**What this builds:** one analysis panel of 73 columns — ROIC levels, a Shapley
decomposition of the change in NOPAT and in ROIC, nine families of labels,
quality flags and an English explanation sentence — from `data/data.parquet`.

**Status:** built and validated. 163 pytest tests pass (143 for the base panel, 20 for the price panel). 1,903,609 rows, **all 73
columns computed**. No external reference data is read: the panel validates
itself.

## Run

```powershell
C:\Users\User\anaconda3\python.exe research_roic\scripts\run_build_panel.py
C:\Users\User\anaconda3\python.exe research_roic\scripts\run_build_prices.py
C:\Users\User\anaconda3\python.exe -m pytest research_roic\tests -q
```

`--no-excel` skips the Excel slice. A base run takes about 80 seconds, a price
run about 40. The price run reads the base panel only for its join and timing
tables, so run the base build first when both inputs change.

## Layout

```
data/                        input, read-only (SHA-256 checked before and after)
  data.parquet
  prices.gz                  Compustat Security Monthly, gzip CSV (price panel)
research_roic/
  config.yaml                every parameter: field map, bands, paths, Excel year
  src/
    data_io.py               config, logging, decimal128->float64, hashes, writers
    registry.py              one declared CalcSpec per block of output columns
    keys.py                  period_key, KEY, duplicate merging, the quarter grid
    levels.py                tax, NOPAT, invested capital, ROIC, debt, equity
    shapley.py               one coefficient matrix for both bridges
    labels.py                bands, the single classifier, the ladders, the levels
    explain.py               the closed explanation catalogue
    schema.py                the 73-column contract, and the 8-column price contract
    validation.py            describes the panels; never alters them
    pipeline.py              build_panel(cfg, raw=None) -> (panel, report)
    prices.py                build_prices(cfg, raw=None, tickers=None) -> (prices, report)
  scripts/run_build_panel.py
  scripts/run_build_prices.py
  tests/                     13 files, one invariant each
output/                      everything the runs produce (fixed names, overwritten)
  panel.parquet              the panel, all rows
  panel_from_2025.xlsx       the same columns from a configurable year
  tables/                    18 validation tables (stale ones are pruned each run)
  reports/report.md          the Hebrew build-and-validation report
  logs/                      run.log, run_manifest.json, config_used.yaml
  prices.parquet             the price panel, all rows
  prices_from_2025.xlsx      the price panel from the same year
  prices_build/              the price run's tables, logs and Hebrew report
```

Every output has a **fixed name and is overwritten**, so a run with new data
replaces the previous one in place. The timestamp and library versions live
inside `run_manifest.json`, not in file names.

## Conventions (confirmed with the researcher)

| Item | Definition |
|---|---|
| Period | `period_key` = calendar quarter of (fiscal period end − 2 months). `2026-03-31 → 2026Q1`, `2025-09-30 → 2025Q3`, `2026-01-31 → 2025Q4`. Holds on 100% of rows. |
| Key | `KEY = period_key + "_" + symbol`, e.g. `2016Q4_A`. Decided on `(gvkey, period_key)` because 383 rows carry no ticker. |
| EBIT | `oiadpq` only. |
| Invested capital | `TCA − TCL + net PPE + goodwill`. Working capital mandatory; PPE and goodwill missing count as zero. |
| Equity | `atq − ltq` (assets − liabilities), which is total equity including minority interest. |
| Tax | `calc_tax_expense_quarterly = txtq`. Compustat is already positive-for-expense, so no sign is flipped. The rate is **never** clipped to [0,1]. |
| Duplicates | Same `gvkey` + date: first row stays, its gaps filled from the following rows. Same quarter, different dates: the first row decides and its values decide. Every removed row is written to `duplicates_audit.csv`. |
| Consecutive | Quarter step of exactly 1 **and** a day gap inside 60–130. |
| Annualisation | `(1+r)^4 − 1`, defined only when `1+r > 0`. |
| Free cash flow | FCFE, from quarterly items only: `NWC = (actq − cheq) − (lctq − dlcq)`, then `FCFE = ibq − ΔNWC − ΔPPENT`. Assembled term by term; an unavailable term is omitted rather than dropping the row. |
| P/FCF | Two columns: `calc_p_to_fcf_quarterly = market_cap / free_cash_flow_quarterly` and `calc_p_to_fcf_ttm = market_cap / calc_free_cash_flow_ttm`. Equity value over a levered flow — pairing FCFE with enterprise value would double-count the debt. |

## Rules that hold everywhere

1. A zero or missing denominator gives NaN — never 0, never `inf`.
2. A window crossing a gap gives NaN for the whole window, never a partial sum.
3. Nothing is clipped, winsorised, filtered or removed. Problems are flagged.
4. A fully classified ROIC row needs three consecutive quarters, because the
   average capital base at t−1 is itself empty right after a gap.
5. EBIT and tax-rate movements are gated on the NOPAT status; only the invested
   capital movement and the raw combination are gated on the ROIC status; the
   sign regimes are gated on neither.
6. `*_effect` reads the sign of the Shapley contribution, not the direction of
   the raw move — when the retention rate is negative, rising EBIT lowers NOPAT.

## How to extend it

* **A new input field** — add one entry under `input.field_map` in
  `config.yaml`. A mapped field that is absent is reported in
  `input_fields.csv` and every column that needs it is emitted as NaN with the
  reason `MISSING_INPUT:<field>`; the moment the field appears the calculation
  runs. That is exactly what happened when `atq`/`ltq` were added on 2026-10-05.
* **A new calc column** — add one `CalcSpec` in `src/registry.py` and its name
  to `schema.PANEL_COLUMNS`. The engine topologically sorts the specs by their
  declared dependencies. No column list exists anywhere else.
* **Switching a calculation off** — name its columns under `calcs.disabled`.
  The columns stay in the schema and hold NaN, so the contract never moves.
  `enabled: false` and a missing input are reported separately, so an empty
  column always says *why* it is empty.
* **The Excel slice** — change `output.excel_from_year`; the file name follows.

## Free cash flow

Built as **FCFE from quarterly fields only**, which is what removes the need for
`fyearq`/`fqtr`: the change in net PPE stands in for capital expenditure net of
depreciation, so no year-to-date series is read at all.

```
NWC_t  = (actq_t − cheq_t) − (lctq_t − dlcq_t)
FCFE_t = ibq_t − (NWC_t − NWC_t−1) − (ppentq_t − ppentq_t−1)

free_cash_flow_quarterly  the single quarter
calc_free_cash_flow_ttm   sum of 4 consecutive quarters, else NaN
calc_p_to_fcf_quarterly   market_cap / free_cash_flow_quarterly
calc_p_to_fcf_ttm         market_cap / calc_free_cash_flow_ttm
```

**Missing inputs do not drop the row.** The formula expands into six signed
terms — income, and one difference per balance-sheet leg — and each is included
only when it can be computed. A term that cannot simply contributes nothing, and
a genuine result of zero is kept.

Two deliberate limits on that:

* A difference whose previous quarter is unavailable is **omitted**, never
  replaced by the level. `ppentq_t − 0` would read as if the firm had bought its
  whole fixed-asset base in one quarter: a fabricated cash flow, not a
  conservative one.
* A row where **no** term is computable stays NaN. Zero out of nothing is not a
  cash flow of zero.

⚠️ This means rows differ in how completely the formula was applied, so
`fcf_census.csv` reports the distribution rather than a single coverage number.
As measured: 54.6% of rows carry all six terms, 17.6% carry none (NaN), and
27.8% are partial — including 10.9% where the flow is nothing but `ibq`. Treat a
partial row as a weaker measurement than a complete one.

⚠️ The trailing sum stays **strict** — four consecutive quarters or NaN. A
three-quarter total labelled TTM is a different quantity, which is a separate
matter from a missing field. In practice it needs five consecutive rows, because
each quarterly flow already consumes its own predecessor.

⚠️ `calc_p_to_fcf_quarterly` divides by a single quarter, so it runs roughly 4×
a conventional annual P/FCF multiple (Apple 2026Q2: 147.5 quarterly vs 35.2
trailing). The two are not interchangeable.

## Price panel

`scripts/run_build_prices.py` builds `output/prices.parquet` from
`data/prices.gz` (Compustat Security Monthly): one row per firm and quarter,
joinable to the base panel on `KEY`. Decided with the researcher on 2026-10-17.

| Column | Definition |
|---|---|
| `KEY` | `period_key + "_" + tic`, from `keys.build_key` — the base panel's own function |
| `Date` | `datadate`, the calendar month-end of Feb / May / Aug / Nov |
| `Ticker` | `tic`, identical to the base panel's `symbol` |
| `Close` | `prccm / ajexm`, adjusted for splits |
| `Quaterly_return` | `Close(q+1) / Close(q) − 1` — the **next** quarter, without dividends |
| `MomentomQ3` | `Close(q) / Close(q−3) − 1` — the last three quarters |
| `ipodate`, `dldte` | passed through untouched for the next stage; nothing filters on them |

* **Month to quarter** is the base panel's rule, `keys.add_period_columns`
  applied to the price month-end: Feb → Q4 of the previous year, May → Q1,
  Aug → Q2, Nov → Q3.
* **Calendar lookups, endpoints only.** Returns and momentum are found by
  `(gvkey, quarter_index ± k)`, never by row position. The return needs a
  valid Close at q and q+1, momentum at q−3 and q; the quarters in between do
  not matter (7,695 momentum values are computed across a missing one).
* **No price.** A zero or missing `prccm`, or a missing `ajexm`, makes Close
  NaN, and with it every ratio that uses it. Nothing is filled or clipped.
* **The security** is the one whose `(gvkey, tic)` equals the base panel's —
  the file has no `iid`. A firm without an exact match gets no rows and is
  listed in `issue_selection_unmatched.csv`, never matched by guesswork.
* ⚠️ `Quaterly_return` **reads the future**: it is the dependent variable.
  `prices.forward_quarterly_return` is the only forward read in `src`, and a
  test confines it there.
* ⚠️ Returns exclude dividends (the researcher's choice). `dividend_gap_census.csv`
  measures the gap against `trt1m`: about 38% of firm-quarters pay one, with a
  median of 0.87% when they do.
* ⚠️ `dldte` is not the last trading day — 4.3% of matched monthly rows sit in a
  month after it. Use it only as "not deleted yet at `Date`"; excluding a firm
  because it **will** be deleted is look-ahead.

## Not computed

Enterprise value, WACC, sorts and terciles. Returns and momentum live in the
separate price panel above. `sector` is the raw `gsector` code, untranslated.
`oancfy` and `capxy` stay unread.

## Validation

No external reference is read any more (2026-10-11). The panel validates itself,
so the run depends on nothing but `data/data.parquet`:

* **22 identity checks** re-derive every formula from the emitted columns and
  pass on 100% of the rows they apply to (`identity_checks.csv`).
* **All 160 declared label levels occur** on the real panel — the 9/9, 27/27 and
  3/3 exhaustiveness evidence (`label_family_census.csv`).
* **Shapley efficiency** holds to machine precision: the 99.99th percentile of
  the relative residual is below 1e-12 in both bridges.
* **No `inf` anywhere**, and the decimal→float64 cast has 200,000× headroom on
  every source field.
* **143 pytest tests** (plus 20 for the price panel) recompute the formulas independently of the pipeline —
  including FCFE worked out by hand from the raw parquet, the sign of each of
  its six terms probed one at a time, and Shapley compared against the mean over
  all 3! orderings. Nine of them pin measured facts (row counts, the merge, the
  tax and equity censuses, the term-completeness distribution, coverage), so a
  changed input makes them fail and name what moved.

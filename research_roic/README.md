# ROIC panel from Compustat Fundamentals Quarterly

**What this builds:** one analysis panel of 72 columns — ROIC levels, a Shapley
decomposition of the change in NOPAT and in ROIC, nine families of labels,
quality flags and an English explanation sentence — from `data/data.parquet`.

**Status:** built and validated. 118 pytest tests pass. 1,903,381 rows, 69 of
the 72 columns computed; the three free-cash-flow columns are empty by decision
(see *Not computed*).

## Run

```powershell
C:\Users\User\anaconda3\python.exe research_roic\scripts\run_build_panel.py
C:\Users\User\anaconda3\python.exe -m pytest research_roic\tests -q
```

`--no-excel` skips the Excel slice, `--no-crosscheck` skips the GuruFocus
comparison. A run takes about 100 seconds.

## Layout

```
data/                        input, read-only (SHA-256 checked before and after)
  data.parquet
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
    schema.py                the 72-column contract: order, dtypes, levels
    validation.py            describes the panel; never alters it
    pipeline.py              build_panel(cfg, raw=None) -> (panel, report)
  scripts/run_build_panel.py
  tests/                     11 files, one invariant each
output/                      everything the run produces (fixed names, overwritten)
  panel.parquet              the panel, all rows
  panel_from_2025.xlsx       the same columns from a configurable year
  tables/                    21 validation tables
  reports/report.md          the Hebrew build-and-validation report
  logs/                      run.log, run_manifest.json, config_used.yaml
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

## Not computed

`free_cash_flow`, `calc_free_cash_flow_ttm` and `calc_ev_to_fcf_quarterly`.
`oancfy` and `capxy` are fiscal-year-to-date, and the pull has no `fyearq` or
`fqtr`, so the quarter that opens each firm's fiscal year cannot be identified
from the data. The researcher chose to drop this rather than infer it. The three
columns exist in the schema and are empty; adding the two fields to the pull
turns them on through `config.yaml` alone.

Also out of scope: enterprise value, returns, momentum, WACC, sorts and
terciles. `sector` is the raw `gsector` code, untranslated.

## Validation

* **22 identity checks** re-derive every formula from the emitted columns and
  pass on 100% of the rows they apply to (`identity_checks.csv`).
* **All 160 declared label levels occur** on the real panel — the 9/9, 27/27 and
  3/3 exhaustiveness evidence (`label_family_census.csv`).
* **Shapley efficiency** holds to machine precision: the 99.99th percentile of
  the relative residual is below 1e-12 in both bridges.
* **No `inf` anywhere**, and the decimal→float64 cast has 200,000× headroom on
  every source field.
* **Cross-check against GuruFocus** on 22,638 overlapping `(symbol, period_key)`
  rows. `calc_tax_expense_quarterly`, `calc_ic_raw`,
  `calc_average_ic_raw_quarterly`, `equity`, `pretax_income`, current assets and
  liabilities, goodwill and `market_cap` all come back at a median ratio of
  1.0000 — the sign, the capital formula and the equity definition are
  confirmed against an independent vendor. The remaining gaps are definitional,
  pre-registered in `config.yaml`, and dominated by the EBIT definition, which
  alone caps input agreement at 17.7%. An unexplained breach stops the run.

# ROIC, ROIC trend and future stock returns

Reproducible research pipeline. **Research question:** does the 4-quarter trend in ROIC carry information
about future returns beyond the ROIC level, and does that depend on the economic source of the change
(Shapley contributions of EBIT, tax rate and invested capital)?

Status: **Stage 0 + Stage 1 done** (data study, validation, research panel). Stage 2 onwards starts only after explicit approval.

## Run

From VS Code terminal (any working directory — all paths come from `config.yaml` via `pathlib`):

```powershell
C:\Users\User\anaconda3\python.exe research_roic\scripts\run_01_prepare_data.py      # ~40 s, all Stage 0-1 outputs
C:\Users\User\anaconda3\python.exe research_roic\scripts\run_01_prepare_data.py --no-xlsx   # skip read-only xlsx cross-check (~5 s)
cd research_roic; C:\Users\User\anaconda3\python.exe -m pytest                          # 39 tests
```

No package was installed; versions of the existing environment are recorded in `requirements.txt`.

## Structure

```
research_roic/
  config.yaml              every research parameter (paths, timing, relevance rule, windows, flags)
  src/
    data_io.py             config, explicit loading (tickers as exact strings, explicit date formats), SHA-256 of sources
    dictionary.py          full Word dictionary reader incl. OMML equations -> variable catalogue
    validation.py          structure, coverage, precision, dictionary formulas, return field, timing, xlsx cross-check, column mapping
    eligibility.py         index-membership ranges joined to the panel
    features.py            consecutiveness, full-precision ROIC, 4q OLS slopes, sums/means, raw IC change
    sample.py              formation / return-window dates, return cleaning, sample layers
    pipeline.py            one function that builds the panel (shared by script and tests)
  scripts/run_01_prepare_data.py
  tests/                   uniqueness, window continuity, no look-ahead, relevance join, NaN preservation, dictionary
  outputs/{data,tables,figures,reports,logs}
```

Source files in `../data` are never written; the run stores their SHA-256 before and after and stops if anything changed.

## Conventions (confirmed with the researcher)

| Item | Definition |
|---|---|
| Signal quarter | `period_key` = calendar quarter of (fiscal period end − 2 months) (dictionary rule; holds for 100% of rows) |
| Portfolio formation | month-end of (quarter end of `period_key` + 2 months). 2025Q4 → 2026-02-28 |
| Forward return | `Future_Quarterly_Return` (undocumented in the dictionary; verified): formation → +3 month-ends. 2025Q4 → 2026-02-28 … 2026-05-31 |
| Index relevance | member in q ⇔ `start_quarter ≤ period_key < end_quarter`; empty end = member through today |
| Consecutive quarters | previous row has `period_key` − 1 and fiscal ends 60–130 days apart |
| 4q trend | OLS slope on x = 0,1,2,3; only when the 4 values exist and the 4 rows are consecutive; else NaN |

## Three sample layers

1. **Raw sample** – every CSV row (companies + SPY/QQQ benchmark rows).
2. **Signal-construction sample (population)** – company rows that are index members in `period_key`.
   Defined without any reference to returns (tested by randomising returns).
3. **Evaluation sample** – population rows with a realised forward return. Only this layer depends on returns.

Signals may use a firm's history from before it joined the index (that information existed at formation).

## Data issues found in Stage 1 (details in `outputs/reports/stage1_report.md`)

* CSV rounds quarterly ROIC, tax rate and D/E to 0.01 → ~89% ties per quarter. ROIC is recomputed with the dictionary formulas
  (`ebit / avg IC`, `NOPAT / avg IC`); verified against the full-precision xlsx.
* Unrealised forward returns (2026Q2) are stored as 0.0 → set to NaN. `Momentum_3Q` exact zeros = missing history → NaN.
  `market_cap` / month-end price = 0 (e.g. pre-IPO quarters) → `market_cap_clean` / `price_clean` = NaN. Originals kept.
* `filing_date` is not the original filing date (median 399 days after period end) → not used for timing.
* Fiscal quarters ending Feb/May/Aug/Nov map to a formation date equal to the period end (Jan/Apr/Jul/Oct: 1 month) → flagged `signal_lag_below_min`, not removed.
* 102 index-member ranges overlapping the study window have no data (download failures, mostly delisted/acquired firms) → survivorship limitation.
* No outliers or records were removed; no filters were applied.

## Outputs of Stage 1

* `outputs/tables/column_mapping.csv` – research concept → dictionary name → actual column → found → notes
* `outputs/tables/dictionary_variables.csv` – meaning, formula, unit, possible values, quality flag, invalid conditions
* `outputs/tables/*` – coverage, duplicates, sequence breaks, precision, formula checks, return-field evidence, timing, relevance coverage, sample flow, feature summary
* `outputs/data/panel_prepared.parquet` – full panel with derived columns and layer flags; `panel_research_view.csv` – research columns only
* `outputs/logs/` – run log, byte copy of the config used, run manifest (versions, hashes, counts)

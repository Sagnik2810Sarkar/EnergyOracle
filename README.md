# EnergyOracle

Day-ahead forecasting of Singapore's half-hourly electricity system demand.

EnergyOracle covers the whole path from raw data to a model comparison: a crawler downloads the weekly demand spreadsheets published by Singapore's Energy Market Authority (EMA), a parser turns them into one clean time series, and two notebooks handle feature engineering, exploratory analysis and model comparison.

| | |
|---|---|
| **Target** | `system_demand_mw`, half-hourly system demand in megawatts |
| **Coverage** | 7 Nov 2022 to 1 Nov 2026 (69,888 rows, 208 full weeks, no gaps) |
| **Task** | Regression, 1-day (48-step) forecast horizon |
| **Models** | 2 naive baselines, plus multiple linear, polynomial, Ridge, Lasso, Elastic Net, Random Forest and XGBoost |
| **Validation** | 4-fold expanding-window time-series cross-validation, 182-day test windows |

## Contents

1. [Repository structure](#repository-structure)
2. [Environment setup](#environment-setup)
3. [Workflow: what to run and when](#workflow-what-to-run-and-when)
4. [Command reference](#command-reference)
5. [Configuration](#configuration)
6. [Data](#data)
7. [Results snapshot](#results-snapshot)
8. [Known issues](#known-issues)
9. [Upcoming](#upcoming)
10. [Data source and attribution](#data-source-and-attribution)

## Repository structure

```
EnergyOracle/
├── config/
│   └── singapore.yaml                  # crawler/parser settings for the EMA source
├── data/
│   ├── raw/                            # downloaded spreadsheets (not tracked by git)
│   └── processed/
│       └── singapore/
│           ├── half_hourly.csv         # parsed demand series (output of `parse`)
│           └── dataframe.parquet       # engineered features (output of the EDA notebook)
├── manifests/
│   └── singapore_half_hourly_manifest.csv   # log of every download attempt
├── notes/
│   └── singapore_half_hourly_issues.csv     # parser warnings and errors (created by `parse`)
├── src/
│   ├── sg_collect.py                   # download + parse command-line tool
│   ├── feat_engg_eda.ipynb             # cleaning, feature engineering, EDA
│   └── modelling.ipynb                 # baselines, models, tuning, evaluation
├── requirements.txt
└── README.md
```

Raw data is not tracked. The processed CSV and the feature table are tracked, so you can run the notebooks without downloading anything.

## Environment setup

**Requirements:** Python 3.11 or newer (the pinned pandas and NumPy versions need a recent Python) and `git`.

```bash
git clone https://github.com/Sagnik2810Sarkar/EnergyOracle.git
cd EnergyOracle

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

`requirements.txt` does not currently list every package the code imports. Install these as well:

```bash
pip install scikit-learn xgboost pyarrow xlrd
```

| Package | Needed for |
|---|---|
| `scikit-learn`, `xgboost` | the models in `modelling.ipynb` |
| `pyarrow` | reading and writing `dataframe.parquet` |
| `xlrd` | parsing older `.xls` EMA files (newer `.xlsx` files use `openpyxl`, which is already listed) |

To open the notebooks, run `jupyter lab` (or `jupyter notebook`) from the project root, or open them in VS Code with the `.venv` kernel selected.

## Workflow: what to run and when

| Step | What | Run when | Reads | Writes |
|---|---|---|---|---|
| 1 | `python src/sg_collect.py download` | First setup, or to refresh data | EMA website, `config/singapore.yaml` | `data/raw/singapore/half_hourly/<year>/<monday>.xls(x)`, manifest |
| 2 | `python src/sg_collect.py parse` | After any download | raw spreadsheets | `data/processed/singapore/half_hourly.csv`, `notes/..._issues.csv` |
| 3 | `src/feat_engg_eda.ipynb` | After step 2, or to change features | `half_hourly.csv` | `dataframe.parquet` |
| 4 | `src/modelling.ipynb` | After step 3, or to change models or horizon | `dataframe.parquet` | results tables and plots in the notebook |

**Quick start (no download):** the repository already includes `half_hourly.csv`. Skip steps 1 and 2 and run the two notebooks top to bottom, in order.

**Full refresh:** run steps 1 to 4 in order. Re-run step 3 whenever `half_hourly.csv` changes, because the modelling notebook only reads the parquet file.

## Command reference

Run all commands from the project root.

**Download the weekly files**

```bash
python src/sg_collect.py download                              # config start date to last Monday
python src/sg_collect.py download --start 2024-01-01           # from a given date
python src/sg_collect.py download --start 2024-01-01 --end 2024-06-30
```

- Files are weekly and named after the Monday that starts the week; the script tries `.xls` first, then `.xlsx`.
- Files older than the refresh window that already exist are skipped, so repeat runs are cheap.
- The most recent 8 weeks are always downloaded again to pick up late revisions. Changed files are kept as `.prev-<hash>` copies and logged as `revised`.
- Every attempt is appended to the manifest with its status, size, SHA-256 hash and a note (`new`, `unchanged`, `revised`, `not_found`, `error_or_bad_content`).
- A summary of counts is printed at the end.

**Parse into one table**

```bash
python src/sg_collect.py parse
```

- Finds the time and demand columns in each sheet, checks the period-ending label convention (00:30 to 00:00) and builds a long-then-wide table.
- Writes `half_hourly.csv` with columns `ts_end_singaporetime`, `date`, `period`, `system_demand_mw`, `nem_forecast_mw`, `source_file`, `file_hash`.
- If two files cover the same timestamp, the later file wins.
- Problems go to `notes/singapore_half_hourly_issues.csv` as `WARN` or `ERROR` rows, including values outside 1,000 to 15,000 MW. Check this file after each parse.

**Use a different project root**

```bash
python src/sg_collect.py --root /path/to/EnergyOracle download
```

## Configuration

**Collector:** `config/singapore.yaml`

| Key | Meaning |
|---|---|
| `url_template`, `extensions` | where weekly files live and which extensions to try, in order |
| `start` | first Monday to download (`2004-01-05` by default) |
| `request_delay_seconds`, `timeout_seconds`, `retries` | politeness and robustness; keep the delay at 1 second or more |
| `refresh_weeks` | how many recent weeks are always re-downloaded |
| `plausible_mw` | range outside which values are flagged |
| `raw_dir`, `manifest`, `processed`, `issues` | input and output locations |

The script sends a `User-Agent` string that includes a contact address (`UA` at the top of `src/sg_collect.py`). Change it to your own contact details before running large downloads.

**Modelling:** settings at the top of `src/modelling.ipynb`

| Setting | Default | Meaning |
|---|---|---|
| `HORIZON` | `"1D"` | forecast distance; lags shorter than this are dropped automatically |
| `N_SPLITS` | `4` | outer cross-validation folds |
| `TEST_DAYS` | `182` | length of each fold's test window |
| `TUNE` | `True` | switch hyperparameter search on or off (off is much faster) |
| `TUNE_SCOPE` | `"once"` | tune on the first fold and reuse the parameters on later folds |
| `INNER_SPLITS`, `N_ITER_RF`, `N_ITER_XGB` | `3`, `8`, `12` | tuning cross-validation folds and random-search draws |
| `RANDOM_STATE` | `42` | reproducibility |

**Feature engineering:** `FeatureConfig` in `src/feat_engg_eda.ipynb` sets the lags (`30min`, `1h`, `1D`, `7D`), rolling windows (`3h`, `1D`, `7D`) and cyclical features.

## Data

- **Source:** EMA Singapore, half-hourly system demand data.
- **Timestamps:** EMA labels each period by its *end* time. The feature notebook shifts timestamps to interval-start, so the first period of a day is 00:00 to 00:30.
- **Quality:** the audit found no duplicates, nulls, non-positive values or gaps, so no imputation is applied.
- **`nem_forecast_mw`:** EMA's own demand forecast is kept in the processed CSV but is not used as a model input yet.

## Results snapshot

Mean over four folds at a 1-day horizon (lower is better):

| Model | MAE (MW) | RMSE (MW) | MAPE (%) |
|---|---|---|---|
| Polynomial Regression | 132.14 | 178.78 | 1.87 |
| Multiple Linear Regression | 136.14 | 181.68 | 1.95 |
| Ridge | 137.91 | 183.97 | 1.97 |
| Lasso | 139.68 | 191.24 | 1.99 |
| Elastic Net | 141.54 | 187.87 | 2.02 |
| XGBoost | 154.55 | 211.05 | 2.13 |
| Naive (same time last week) | 170.06 | 240.20 | 2.44 |
| Random Forest | 171.65 | 245.38 | 2.34 |
| Naive (same time yesterday) | 209.30 | 317.80 | 2.94 |

Regularised linear models with lag and rolling features beat both baselines and the tree models. Demand keeps trending upward, and from early 2026 all models under-forecast. Multiple linear regression is the steadiest across folds.

## Known issues

- **Windows-style paths in the notebooks.** The notebooks use paths such as `..\data\processed\singapore\half_hourly.csv`. On macOS or Linux, edit the `CSV_PATH`, `OUT_PATH` and `DF_PATH` lines to use forward slashes (or `pathlib`).


## Upcoming

### Holiday analysis

The EDA shows strong weekday and weekend structure and sharp short dips around the turn of each year, but the models have no information about public holidays. The next step is to bring Singapore's holiday calendar into the analysis:

- **Holiday calendar:** add a `config/` entry or a small processed table listing Singapore public holidays and observed (substitute) holidays for 2022 to 2026.
- **Holiday features:** a holiday flag, plus days-to-next and days-since-last holiday, and bridge-day flags where a holiday sits next to a weekend.
- **Holiday EDA:** compare holiday intraday profiles with ordinary weekday and weekend profiles, and test whether the flagged week-over-week outliers (3.66% of rows) line up with holidays.
- **Holiday-aware lags:** because holidays that follow the lunar calendar move from year to year, `lag_7D` is a poor reference on and after those dates; evaluate holiday-adjusted comparison days.
- **Evaluation:** report error separately on holidays and non-holidays, and re-run the comparison with and without the new features.

### More processed datasets

The repository is organised so that each data source has its own configuration and processed output. Planned additions:

- **Weather:** temperature and humidity series aligned to the half-hourly grid, since weather is the largest missing driver of demand.
- **Additional markets:** other electricity markets, each with a config file in `config/<site>.yaml` and a processed table under `data/processed/<site>/`, using the same collect, parse and feature pipeline.
- **EMA forecast benchmark:** use the `nem_forecast_mw` column already collected to benchmark the models against EMA's own forecast.
- **Shared schema:** a common column layout for all processed datasets (timestamp, demand, source file, file hash) so the notebooks can switch datasets by changing one setting.
- **Packaging:** move the cleaning and feature functions out of the notebook into importable modules under `src/`, and pin all dependencies in a UTF-8 `requirements.txt`.

## Data source and attribution

Demand data is published by the Energy Market Authority of Singapore at
<https://www.ema.gov.sg/resources/statistics/half-hourly-system-demand-data>.
Check EMA's terms of use before redistributing the data. This project requests files slowly and identifies itself in the `User-Agent` header; please keep that behaviour when you adapt the crawler.
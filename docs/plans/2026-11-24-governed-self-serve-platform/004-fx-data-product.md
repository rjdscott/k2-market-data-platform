# Phase P4: the FX reference domain and the FX data product

**Depends on:** P1 (descriptors, contracts, generated DDL), P2 (Airflow), P3 (Trino, dbt)
**Decision it implements:** ADR-036 (the file-source ingestion pattern: immutable landing, then write-audit-publish where a bad write can wait) and ADR-037 (FX reference domain: the source, the conventions, and where derivation lives), both written in this phase. ADR-037 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §4 and appends an Outcome there.
**Delivers:** a reusable way to onboard a file source, used twice; a fourth domain publishing ECB reference rates as landed; and a consumer product built on it in dbt: a cross-rate matrix, returns and volatility, an implied stablecoin basis, daily bars valued in reporting currencies, and a return decomposition. It is the plan's worked example of one team publishing a source and the platform turning it into something every other product can use.
**Exit:** `make parity-fx` passes (dbt crosses, returns and volatility equal a pure-Python reference on the committed sample); a corrupted FX file fails its audit, `main` keeps its prior snapshot id and `k2lake.pin()` still returns that id; every dbt unit test listed under "Tests" passes; a fresh clone with no network builds every FX mart from the committed sample.

## Why this split

The source team owns one thing: what the ECB published, typed and nothing more. Every
derived number is a dbt model, in SQL, with a test beside it. That is the Spark and dbt
boundary from ADR-035 applied to a new product: Spark where the write must be audited and
atomic, dbt where the logic is set-based and will change.

The platform's contribution is the reusable part. A currency dimension, a calendar, and one
macro, `fx_rate(base, quote, on_date)`, let any product convert an amount without knowing how
a cross is built or what happens on a Sunday.

## Conventions (recorded in ADR-037)

| Topic | Convention |
|---|---|
| Source | ECB euro foreign exchange reference rates. A reference fixing from a concertation at about 14:10 CET, published about 16:00 CET on TARGET working days. Not a market close, and the ECB advises against transaction use. Every model description says so |
| Quote | `rate` is units of `quote` per one unit of `base`. Both are ISO 4217 codes. The ECB's own figures are `base = EUR` |
| Crosses | Triangulated through EUR: `rate(A, B) = eur_rate(B) / eur_rate(A)`. Both legs come from the same fixing, so the cross is exact to the rounding of its inputs. Flagged `is_derived` |
| Precision | Published strings are parsed to `DECIMAL(20, 10)` and never rounded. Derived rates are `DECIMAL(38, 18)`. Rounding is for presentation only |
| Non-fixing days | The last fixing is carried forward. Each row keeps `rate_date` (the fixing it came from) and `days_stale` |
| Point in time | Daily bars only. A UTC day closes after that day's fixing is published, so day D's bar uses day D's fixing with no look-ahead. Intraday conversion is out of scope and the macro refuses a timestamp |
| Returns | Log returns between consecutive fixings. Carried days are excluded, so a weekend does not add zero returns |
| Volatility | Sample standard deviation of log returns over 30 and 90 observations, annualised by the square root of 252. Null until the window is full |
| Stablecoin | USDT is not assumed to be one dollar. A daily USDT/USD is implied from assets traded against both |
| Conversion of a bar | One daily rate is applied to open, high, low and close. That is an approximation: the fixing is one instant in the day. `quote_volume` converts the same way; base `volume` does not change |

Window lengths, the annualisation factor and the reporting currencies are dbt variables.

## Scope

**Pull request 1: the file-source ingestion pattern, ADR-036**

- Spike first: a branch write and fast-forward from Spark on a scratch table; and a locked,
  versioned MinIO bucket that refuses an overwrite.
- `notebooks/k2lake.py:84` and `scripts/parity_ohlcv.py:66-69` resolve the snapshot `main`
  points at, with a test, before any branch exists.
- A contract declares a file source in its custom properties: `url`, `format`, `schedule`,
  `calendar`, `parser`, and the audits to run. `k2gen` emits the DAG config; one factory,
  `docker/airflow/dags/factory.py`, builds every file-source DAG from it.
- The generated DAG has four tasks, the same for every source:
  1. **fetch**: download with the standard library, compute sha256, write to the landing zone
     at `landing/<domain>/<product>/<fetch date>/<sha256>.<ext>`. Same content, same key: a
     re-fetch of an unchanged file writes nothing. The landing bucket is versioned and
     locked, so a landed file cannot be altered. It is the verbatim record.
  2. **load**: `docker/lake/file_source.py` in the Spark container reads the landed object,
     calls the product's parser (`docker/lake/sources/<name>.py`, one function from bytes to
     rows), and writes bronze to a branch created from `main`. Every row carries
     `source_file_sha256`. Takes the writer lock blocking, with a bounded wait.
  3. **audit**: generic checks (parses, non-empty, keys unique, no key outside the declared
     domain, a published value is never changed by a later file) plus the product's own.
  4. **publish**: fast-forward `main`, then merge to silver. A failed audit leaves the branch,
     writes an `audit.checks` row and alerts; `main` does not move.
- `bronze.source_files`: one row per landed file for every source: product, URL, fetch time,
  sha256, landing path, byte size. The file itself stays in the landing zone, not in a table
  row.
- Parsers use the standard library after asserting the bundled expat is 2.4.1 or newer (the
  image has no pip layer, and that version already refuses entity expansion).
- A fresh clone seeds the landing zone from committed sample files, so every product builds
  with no network. No test reaches the network.
- `maintenance.py` already takes its table lists from the contracts (P3), so new tables are
  compacted and expired with no further change.

**Pull request 2: two products on the pattern, ADR-037**

The pattern has two users from its first merge, so its seams are real.

- **ECB FX rates**, domain `fx-reference`.
  - `bronze.ecb_fx_daily`: one row per date and currency as published, the rate as a string.
  - `silver.fx_rates_ecb`: `rate_date`, `currency`, `eur_rate` as a decimal, `fetched_at`,
    `available_at` (16:00 Frankfurt time on `rate_date`, stated as nominal, not observed),
    `source_file_sha256`. Keyed on date and currency.
  - Product audits: every rate positive; no date in the future; the latest date's currency
    set equals the previous date's (a difference warns: the ECB does add and retire
    currencies); no TARGET working day missing since the last stored date.
  - Schedule: TARGET working days at 15:30 UTC, retrying until 18:00. A `history` run loads
    the full file since 1999.
  - Sample: the ECB 90-day file (about 70 KB, attributed) and a corrupted copy for the test.
  - Classification `public`; freshness SLO "the day's fixing by 18:00 UTC on TARGET working
    days".
- **ISO 4217 currency list**, domain `reference-data`: the published XML list of currency
  codes, names and minor units, landed as `bronze.iso4217_currency` and
  `silver.currency_master`. Monthly schedule. It replaces a hand-written currency seed, so
  the FX models' currency dimension is itself a governed product. Its terms of use are read
  before a sample is committed; if they forbid it, the sample is synthetic and says so.
- DDL for all of it emitted by `k2gen` into `docker/lake/ddl/generated/`. Nothing here
  touches `raw.messages`.
- `scripts/chaos/fx-bad-file.sh`: lands the corrupted copy and checks the three exit
  conditions.
- `scripts/trace-product.sh <output> <date>`: walks one served value back through silver and
  bronze to the landed file and prints its hash and path.

**Pull request 3: FX models in dbt**

`dbt/models/fx/`, with `stg_ref__currencies` over `silver.currency_master` and
`dbt/seeds/target_holidays.csv` (generated by a committed script: the fixed TARGET closing
days and the two that follow Easter).

| Model | Grain | What it is |
|---|---|---|
| `stg_fx__ecb_rates` | date, currency | View over `silver.fx_rates_ecb`, plus the `EUR = 1` row |
| `int_fx__calendar` | date | Every calendar day from the first fixing to today, with `is_fixing_day` |
| `int_fx__rates_filled` | date, currency | Rates carried forward, with `rate_date` and `days_stale` |
| `fx_rates_daily` | date, base, quote | The cross matrix for bases USD, EUR and the reporting currencies against all quotes |
| `fx_returns_daily` | fixing date, base, quote | Log return, 30 and 90 observation volatility, observation count |
| `stablecoin_basis_daily` | date | Implied USDT/USD: per asset traded against both USD and USDT, the ratio of the two daily volume-weighted prices; then the median across assets, with the count and the spread between assets. Falls back to 1 with `peg_assumed` when fewer than three assets overlap |
| `ohlcv_1d_fx` | date, exchange, symbol, reporting currency | Daily bars valued in each reporting currency (AUD, EUR, GBP, JPY to start), carrying the rate, its `rate_date`, `days_stale`, the basis used and `peg_assumed` |
| `return_decomposition_daily` | date, exchange, symbol, reporting currency | Return in the reporting currency split into the asset's USD return, the FX return and the cross term, in simple returns; and the two-term sum in log returns |

- Macro `fx_rate(base, quote, on_date)`: the one supported way to convert. It reads
  `fx_rates_daily`, and fails at compile time if handed a timestamp column.
- `fx_rates_daily` and `fx_returns_daily` are classified `public`. The three models built on
  crypto bars inherit the classification of `gold.ohlcv_1d`. `k2gen validate` gains the rule
  that makes this automatic: a product is at least as restricted as its strictest upstream.
- `notebooks/k2lake.py`: `fx(base, quote, start, end)` reads the pinned mart; `marts` joins
  the pinned namespaces.

**Tests**

- dbt unit tests, each with hand-computed expected rows:
  - inverse: `rate(A, B) * rate(B, A) = 1`;
  - triangle: `rate(A, C) = rate(A, B) * rate(B, C)`;
  - carry forward across a weekend and across Easter, with the right `days_stale`;
  - returns skip carried days; volatility is null on a short window, zero on a constant
    series and matches a five-row worked example;
  - basis: the median of three assets, and the fallback with two;
  - decomposition: `(1 + r_reporting) = (1 + r_asset) * (1 + r_fx)`.
- Data tests: the grain is unique; rates are positive; base-EUR rows equal the source
  exactly; `days_stale` above five warns; a daily log return beyond fifteen percent warns
  (the 2015 franc move is in the history, so this cannot be an error); the basis more than
  fifty basis points from one warns.
- `scripts/parity_fx.py` and `make parity-fx`: a pure-Python reference recomputes crosses,
  returns and volatility from the committed sample and compares them with the marts. It joins
  `make gates`.

**Documents.** ADR-036, ADR-037; ADR-028 Outcome; chapter 18 (the FX product as the mesh's
worked example) and chapters 12 and 13; `dbt/models/fx/README.md` carrying the conventions
table; runbooks for a failed FX audit and a late fixing.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test fx_ingest
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make parity-fx
bash scripts/chaos/fx-bad-file.sh
bash scripts/trace-product.sh fx_rates_daily "$(date -u -d yesterday +%F)"
docker exec k2-airflow-scheduler airflow dags test currency_master_ingest
docker exec k2-trino trino --execute "SELECT rate, rate_date, days_stale FROM lake.marts.fx_rates_daily WHERE base = 'USD' AND quote = 'AUD' ORDER BY as_of_date DESC LIMIT 3"
make generate && test -z "$(git status --porcelain)"
make gates
```

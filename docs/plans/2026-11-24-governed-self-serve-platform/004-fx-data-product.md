# Phase P4: the file-source ingestion pattern and the FX data product

**Depends on:** P1 (descriptors, contracts, generated DDL), P2 (Airflow), P3 (Trino, dbt)
**Decision it implements:** ADR-036 (the file-source ingestion pattern: immutable landing, then write-audit-publish where a bad write can wait) and ADR-037 (FX reference domain: the source, the conventions, and where derivation lives), both written in this phase. ADR-037 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §4 and appends an Outcome there.
**Delivers:** a reusable way to onboard a file source, used twice; a fourth domain publishing ECB reference rates as landed; and a consumer product built on it in dbt: a cross-rate matrix, returns and volatility, an implied stablecoin basis, daily bars valued in reporting currencies, and a return decomposition. It is the plan's worked example of one team publishing a source and the platform turning it into something every other product can use.
**Exit:** `make parity-fx` passes (dbt crosses, returns and volatility agree with a pure-Python reference to a relative 1e-12, and the hand anchor below holds); a corrupted FX file fails its audit, `main` keeps its prior snapshot id and `k2lake.pin()` still returns that id; the full history since 1999 loads through the audits; every dbt unit test listed under "Tests" passes; a fresh clone with no network builds every FX mart from the committed samples.

## Why this split

The source team owns one thing: what the ECB published, typed and nothing more. Every
derived number is a dbt model, in SQL, with a test beside it. That is the Spark and dbt
boundary from ADR-035 applied to a new product: Spark where the write must be audited and
atomic, dbt where the logic is set-based and will change.

The platform's contribution is the reusable part: the ingestion pattern, a currency master, a
calendar, and one macro, `fx_rate(base, quote, on_date)`, that lets any product convert an
amount without knowing how a cross is built or what happens on a Sunday.

## Conventions (recorded in ADR-037)

These were checked against the full ECB history (7,142 fixing dates, 1999-01-04 to
2026-11-23, 41 currencies ever published) before being written down.

| Topic | Convention |
|---|---|
| Source | ECB euro foreign exchange reference rates: a reference fixing from a concertation at about 14:10 Frankfurt time (13:10 UTC in winter, 12:10 UTC in summer), published about 16:00 Frankfurt time on TARGET working days. Not a market close, and the ECB strongly discourages transaction use. Every model description says so |
| Quote | `rate` is units of `quote` per one unit of `base`, both ISO 4217 codes. The ECB's own figures are `base = EUR`. Anchor: on 2026-11-23, `rate(USD, AUD) = 1.6532 / 1.0939 = 1.51129` AUD per USD. `USDT` is not a currency code and appears only in the basis model |
| Crosses | `rate(A, B) = eur_rate(B) / eur_rate(A)`, for any pair, from the filled euro legs. Inverse and triangle identities hold to a relative 1e-12, not exactly. A cross carries both legs' rounding, about one basis point for five-significant-figure legs. A cross's `rate_date` is its older leg's and its `days_stale` the larger. Flagged `is_derived` |
| Precision | Published values parse to `DECIMAL(20, 10)`, which holds every rate in the history. Stored crosses are `DECIMAL(38, 18)`, asserted by a `typeof` test because the result type of decimal division depends on the Trino version. Returns, volatility, basis and decomposition are DOUBLE. A conversion casts the rate to `DECIMAL(24, 12)` before multiplying, so the product keeps its scale. Rounding is half up, and for presentation only |
| Non-fixing days | The last fixing is carried forward for at most five TARGET working days. After that the rate is null: twelve currencies in the history have been retired or suspended, and a four-year-old rouble rate is not a rate. Rows keep `rate_date`, `days_stale` and `expected_rate_date` |
| Point in time | Daily bars only. The converted close of UTC day D has no look-ahead: D's fixing is published hours before D closes. The converted open, high and low use a rate published after the open and are not point-in-time. The asset is sampled at 24:00 UTC and the rate about eleven hours earlier, which damps same-day correlation between them. Rows carry `rate_available_at` (16:00 Europe/Berlin, nominal) |
| Returns | Log returns between consecutive fixing dates on which both legs are published. Null across a longer gap (the Icelandic krona stopped for nine years). Carried days are not observations |
| Volatility | Sample standard deviation of fixing-day log returns over 30 and 90 observations, annualised by the square root of 252. The ECB averages 256 fixings a year, so this understates by under one percent; said so in the model description. Never applied to a calendar-day series. Null until the window is full; the window restarts after a null return |
| Stablecoin | USDT is not assumed to be one dollar. Daily USDT/USD (USD per USDT) is the median over overlapping assets of `close_usd / close_usdt` from the daily bars, using only pairs whose last trade on both venues falls in the final minute of the UTC day, with at least three such assets; otherwise 1 with `peg_assumed`. No venue in the registry lists both quotes, so the figure includes any venue premium, and the description says so |
| Conversion of a bar | One daily rate, times the basis for USDT pairs, is applied to open, high, low and close, which preserves their ordering. The converted high and low are not the true extremes in the reporting currency; the error is bounded by that day's FX range. `quote_volume` converts the same way; base `volume` does not change |

Why closes and not volume-weighted prices for the basis: the two venues' intraday volume
profiles differ, so a ratio of daily volume-weighted prices mostly measures that difference.
The ratio of closes tracks a directly traded USDT/USD far more closely. The comparison is a
script in this phase (`scripts/basis-estimator-check.py`), run on the user's stack; its
results are written to a platform table and read there, not quoted in a document.

Window lengths, the annualisation factor, the carry horizon and the reporting currencies are
dbt variables.

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
     Values are compared as decimals, never as strings: the same rate is written `137.20` in
     one ECB file and `137.2` in another.
  4. **publish**: fast-forward `main`, then merge to silver. A failed audit leaves the branch,
     writes an `audit.checks` row and alerts; `main` does not move.
- `bronze.source_files`: one row per landed file for every source: product, URL, fetch time,
  sha256, landing path, byte size. The file itself stays in the landing zone, not in a table
  row.
- Parsers use the standard library after asserting the bundled expat is 2.4.1 or newer (the
  image has no pip layer, and that version already refuses entity expansion).
- A fresh clone seeds the landing zone from committed sample files, so every product builds
  with no network. No test reaches the network.
- Idempotency test per source: load the same landed file twice and assert the second load
  adds no row and changes no value.

**Pull request 2: two products on the pattern, ADR-037**

The pattern has two users from its first merge, so its seams are real.

- **ISO 4217 currency list**, domain `reference-data`: the published XML lists of current
  and historic currency codes, names and minor units, landed as `bronze.iso4217_currency`
  and `silver.currency_master`, with `status` (active, historic). Monthly schedule. Both
  lists are needed: twelve codes the ECB once published are no longer current, and the FX
  audit below must know them. The committed sample is synthetic and named so; the real
  list is fetched by the user's stack.
- **ECB FX rates**, domain `fx-reference`.
  - `bronze.ecb_fx_daily`: one row per date and currency as published, the rate as a string.
  - `silver.fx_rates_ecb`: `rate_date`, `currency`, `eur_rate` as a decimal, `fetched_at`,
    `source_file_sha256`. Keyed on date and currency.
  - Product audits:
    - every rate positive; no date in the future;
    - no currency code outside the currency master;
    - on every date in the file, a change in the currency set from the previous date warns
      and is recorded (the history has fifteen such changes);
    - a daily log return above ten percent on a published euro leg, for dates new in this
      file, warns (the history has twenty-two; a threshold of fifteen would sit on top of
      the 2015 franc move);
    - on scheduled runs only, no TARGET working day missing since the last stored date. The
      history load does not run this check: the TARGET calendar changed in its first years.
  - Schedule: TARGET working days at 15:30 UTC, retrying until 18:00. A `history` run loads
    the full file since 1999.
  - Samples: the ECB 90-day file (about 70 KB, attributed), a corrupted copy for the audit
    test (named as modified, as the ECB's terms require), and a small excerpt of the history file chosen for its awkward cases: Easter 2026,
    the 2025 year end including a currency leaving the set, the krona stopping in 2008, and
    the franc on 2015-01-15.
  - Classification `public`; freshness SLO "the day's fixing by 18:00 UTC on TARGET working
    days".
- DDL for all of it emitted by `k2gen` into `docker/lake/ddl/generated/`. Nothing here
  touches `raw.messages`.
- `scripts/chaos/fx-bad-file.sh`: lands the corrupted copy and checks the three exit
  conditions.
- `scripts/trace-product.sh <output> <date>`: walks one served value back through silver and
  bronze to the landed file and prints its hash and path.

**Pull request 3: FX models in dbt**

`dbt/models/fx/`, with `stg_ref__currencies` over `silver.currency_master` and
`dbt/seeds/target_holidays.csv`: the six TARGET closing days under the current rule (1
January, Good Friday, Easter Monday, 1 May, 25 and 26 December), generated by a committed
script. The seed drives only `expected_rate_date` and the missing-day audit. Whether a day
was a fixing day is read from the data.

| Model | Grain | What it is |
|---|---|---|
| `stg_fx__ecb_rates` | `rate_date`, currency | View over `silver.fx_rates_ecb`, plus the `EUR = 1` row |
| `int_fx__calendar` | `as_of_date` | Every calendar day from the first fixing to the last fixing plus the carry horizon (not the wall clock, so a rebuild at a pinned snapshot is reproducible), with `is_fixing_day` and `expected_rate_date` |
| `int_fx__rates_filled` | `as_of_date`, currency | Euro legs carried forward within the horizon, with `rate_date` and `days_stale`; null beyond it |
| `fx_rates_daily` | `as_of_date`, base, quote | The cross matrix for bases USD, EUR and the reporting currencies against all quotes: about two million rows. A convenience for direct readers |
| `fx_returns_daily` | fixing date, base, quote | Log return, 30 and 90 observation volatility, observation count |
| `stablecoin_basis_daily` | `as_of_date` | Implied USD per USDT, the count of assets used, the spread between them, `peg_assumed` |
| `ohlcv_1d_fx` | `as_of_date`, exchange, symbol, reporting currency | Daily bars valued in each reporting currency (AUD, EUR, GBP, JPY to start), carrying the rate, its `rate_date`, `days_stale`, `rate_available_at`, the basis used and `peg_assumed` |
| `return_decomposition_daily` | `as_of_date`, exchange, symbol, reporting currency | Return in the reporting currency as three factors: the asset's return in its quote currency, the basis return (zero for USD pairs), and the FX return; in simple returns with their cross terms, and as a three-term sum in log returns |

- Macro `fx_rate(base, quote, on_date)`: the one supported way to convert. It divides two
  filled euro legs, so it answers for any pair, not only those in the matrix, and returns
  null with a reason column when a leg is beyond the carry horizon. Marts that call it
  declare the date column's type in a dbt model contract, which is what keeps a timestamp
  out.
- In the decomposition the FX leg comes from the filled series on the calendar grain: zero
  on a carried day, the whole move on the next fixing day. It is never joined from
  `fx_returns_daily`, which is on the fixing grain. A flip of `peg_assumed` is a null basis
  return for that day, not a jump.
- `fx_rates_daily` and `fx_returns_daily` are classified `public`. The three models built on
  crypto bars inherit the classification of `gold.ohlcv_1d` through the generator rule from
  P1.
- `notebooks/k2lake.py`: `fx(base, quote, start, end)` reads the pinned mart; `marts` joins
  the pinned namespaces.

**Tests**

- dbt unit tests, each with hand-computed expected rows, identities to a relative 1e-12:
  - the anchor: `rate(USD, AUD)` on 2026-11-23 is 1.511289880244994972, which fixes the
    direction (inverse and triangle alone would pass with every rate upside down);
  - inverse and triangle;
  - carry forward across a weekend and across Easter; null on the sixth TARGET day;
  - returns skip carried days and are null across a gap; volatility is null on a short
    window, zero on a constant series, matches a five-row worked example, and restarts
    after a gap;
  - basis: the median of three assets, the fallback with two, and a bar whose last trade
    was early is left out;
  - decomposition: the three-factor identity for a USDT pair and the two-factor one for a
    USD pair, including a weekend.
- Data tests: each grain is unique; rates are positive; base-EUR rows equal the source as
  decimals; `rate_date` earlier than `expected_rate_date` warns for an active currency (the
  longest normal gap in the history is five calendar days); the basis more than fifty basis
  points from one warns.
- A `typeof` test pins the column types the precision row promises, so a Trino upgrade that
  changes decimal typing fails loudly instead of rounding quietly.
- `scripts/parity_fx.py` and `make parity-fx`: a pure-Python reference (decimal arithmetic,
  half-up rounding) recomputes crosses, returns and volatility from the committed samples
  and compares them with the marts at a relative 1e-12. It is a second implementation of the
  same specification: it catches a wrong query, not a wrong convention. The anchor and the
  hand-computed unit rows are what guard the conventions. It joins `make gates`.

**Documents.** ADR-036, ADR-037; ADR-028 Outcome; a research note giving the
command behind each ECB history statistic quoted above (the ECB's terms allow them to be
quoted with attribution); the basis estimator check as a script whose results stay on the
platform; chapter 18 (the
FX product as the mesh's worked example) and chapters 12 and 13; `dbt/models/fx/README.md`
carrying the conventions table; runbooks for a failed file-source audit and a late fixing.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test currency_master_ingest
docker exec k2-airflow-scheduler airflow dags test fx_ingest
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make parity-fx
bash scripts/chaos/fx-bad-file.sh
bash scripts/trace-product.sh fx_rates_daily "$(date -u -d yesterday +%F)"
docker exec k2-trino trino --execute "SELECT rate, rate_date, days_stale FROM lake.marts.fx_rates_daily WHERE base = 'USD' AND quote = 'AUD' ORDER BY as_of_date DESC LIMIT 3"
make generate && test -z "$(git status --porcelain)"
make gates
```

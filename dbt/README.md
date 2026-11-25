# dbt

The domain marts: SQL models dbt builds on Trino, each in its domain's own catalog
([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)). Spark keeps
everything from raw to gold; a model here is one `SELECT` over lake tables or other marts.

| Pinned | Version | Why this one |
|---|---|---|
| `dbt-core` | 1.12.7 (2026-11-12) | the newest on PyPI on 2026-11-25. dbt v2, the Rust engine, lists no Trino adapter |
| `dbt-trino` | 1.12.0 (2026-11-13) | the newest; requires `dbt-core` 1.12. Its README says tested on Trino 478; this project runs it on 484 (below) |
| `metaplane/dbt_expectations` | 0.10.10 (2025-12-02), with `godatadriven/dbt_date` 0.21.0 | the maintained fork, whose README lists Trino; `package-lock.yml`. `dbt_date` is named in `packages.yml` too, because Airflow runs dbt without the lock file (below) |
| `sqlfluff` | 4.5.0 (2026-10-30) | the newest; dev group only |
| `astronomer-cosmos` | 1.16.0 (2026-11-03), in the Airflow image | the newest; its compatibility policy lists Airflow 3.3 and dbt Core 1.12 (`docker/airflow/Dockerfile`) |

## Run it

From this directory, against the stack's Trino (`127.0.0.1:${K2_TRINO_PORT:-8088}`):

```bash
uv sync --locked
uv run --locked dbt deps
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt build --selector market_analytics
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt source freshness --selector market_analytics
```

`make test-dbt` runs the lint, compile, manifest check and a build on a throwaway Trino
with empty sources, no stack (`scripts/dbt-test.sh`, CI job `dbt`).

Run on the stack on 2026-11-25 (Trino 484, 2 CPU / 4 GiB), timings from dbt's own output of
the commands above: `dbt build --selector market_analytics` passed 10 of 10 (one view, one
table, two unit tests, six data tests) in 16.2 s, of which the table, a CTAS over all of
`lake.gold.trades`, took 11.8 s. A second build, 22 minutes later, took 9.8 s, the table 6.8 s.
`dbt source freshness` passed in 1.7 s. With the three marts, later that day: 25 of 25 (three
views, two tables, five unit tests, fifteen data tests) in 18.4 s, the two tables, each over all
of `lake.gold.trades` and built at once, in 13.4 s and 12.8 s; with the delayed view over the
lake's candles, 30 of 30 (four views, two tables, eight unit tests, sixteen data tests) in 12.8 s. `on_table_exists: replace` makes a rebuild `CREATE OR
REPLACE`: one Iceberg table whose snapshots are its builds, each by `dbt_market_analytics`:

```bash
docker exec k2-trino trino --execute \
  "SELECT summary['trino_user'] FROM market_analytics.candles.\"ohlcv_1m\$snapshots\" ORDER BY committed_at"
#   "dbt_market_analytics", one line per build
```

## Identity and catalogs

Trino has no authentication here: the user is the `X-Trino-User` header, and it decides what
may be written ([`docker/trino/etc/rules.json`](../docker/trino/etc/rules.json)). Every user
reads every catalog. Only `dbt_<catalog>` creates, replaces or drops in catalog `<catalog>`,
and nobody writes `lake`. `profiles.yml` has one target, `trino`, whose host, port and user
come from `K2_TRINO_HOST`, `K2_TRINO_PORT` and `K2_TRINO_USER` (defaults `localhost`, `8088`,
`dbt_market_analytics`); there is no password because there is nothing to check it.

So a run writes one domain: `K2_TRINO_USER=dbt_<catalog> dbt build --selector <catalog>`.
[`selectors.yml`](selectors.yml) is generated from the contracts, one selector per domain
with marts: its models, plus the checks of every lake table its marts read. A model of
another domain in the same run fails on the fence, with `Access Denied`.

## How a model picks its place

Its folder (`macros/naming.sql`):

| File | Becomes |
|---|---|
| `models/<catalog>/staging/<model>.sql` | view `<catalog>.staging.<model>`: one per lake table the domain reads, the only models that may select from a source |
| `models/<catalog>/intermediate/<model>.sql` | view `<catalog>.intermediate.<model>`, when a mart needs a step |
| `models/<catalog>/marts/<product>/<model>.sql` | table `<catalog>.<product>.<model>`: one schema per data product |

`<catalog>` is the domain id with underscores, `<product>` the product name with underscores.
A mart is declared in its product's `product.yaml` under `marts`, with its grain and the lake
tables it reads (`contracts/market-analytics/candles/product.yaml`). A product may build marts
and no lake table, with `outputs: []`, as `delayed-candles` and `venue-quality` do; `k2gen new`
scaffolds a commented `marts:` block at the product's place. A mart that is a view says
`materialized: view` in its contract and `{{ config(materialized='view') }}` in its model. A product cannot be named
`staging` or `intermediate`: those schemas are the layers'. A second domain adds its
folder, a three-line block in `dbt_project.yml` that makes its marts tables, and its marts in
its contracts; `make generate` gives it a selector.

`scripts/dbt_manifest_check.py` holds the project to that on the compiled manifest: only
staging selects from a source; every model is in a domain's catalog; every mart is declared at
its relation, is a table unless its contract entry says `materialized: view` (as ADR-035's
delayed product will), reads exactly the tables its contract says, and has a uniqueness test on
exactly its contract grain that fails the build: whole table, severity error, any duplicate.

## Generated, do not edit

From the contracts by `make generate` (`platform/k2gen/k2gen/generate.py`), under the CI drift
gate:

- `models/_sources.yml`: every lake table a mart reads, with the column types Trino reads, the
  contract's freshness (`slaProperties` latency) and a uniqueness test on lake.sql's identifier
  fields, where the contract and lake.sql declare them: `audit.checks` has neither, so it gets
  its columns only. That test covers the last 6 hours by the table's time partition, because over all of
  `gold.trades`, and over its last 2 days, it ran out of Trino's 844.80MB per-node query memory
  (the default at a 2816M heap). The whole table is the nightly Spark audit's
  (`duplicate_identifiers`). On 2026-11-25:

  ```bash
  docker exec k2-trino trino --execute "SELECT count(*) = 0 FROM (SELECT exchange, canonical_symbol,
    trade_id FROM lake.gold.trades WHERE exchange_ts >= current_timestamp - INTERVAL '2' DAY
    GROUP BY 1, 2, 3 HAVING count(*) > 1)"
  #   Query exceeded per-node memory limit of 844.80MB    (the same with INTERVAL '1000' DAY)
  #   with INTERVAL '6' HOUR: "true", in 2.6 s
  ```
- `selectors.yml`, above.

## The first mart: `market_analytics.candles.ohlcv_1m`

The port of `gold.candles()` (`docker/lake/gold.py`) at one minute. Two things differ from the
Spark text, and both are written in the model:

- `quote_volume`: the lake holds it with 6 decimals in a `decimal(38,10)` column: no
  2026-11-24 bucket has a seventh. Spark's decimal rules type `gold.py`'s `DECIMAL(38,10)`
  product `decimal(38,6)` (Spark 3.5.5, 2026-11-25: `spark-sql -e "select typeof(cast(1 as decimal(38,10)) * cast(1 as decimal(38,10)))"` in `k2-spark-iceberg` prints `decimal(38,6)`), which explains
  it. Ported verbatim, the product fails in Trino with `Decimal overflow`; the model multiplies
  the `_e8` integers as `decimal(38,0)` and rounds the quotient half up at 6 decimals. So
  rounded, every 2026-11-24 bucket matches the lake (the parity run below); rounded at 10
  decimals, it does not:

  ```bash
  docker exec k2-trino trino --execute "SELECT bool_and(quote_volume = round(quote_volume, 6))
    FROM lake.gold.ohlcv_1m WHERE CAST(window_start AS date) = DATE '2026-11-24'"
  #   "true"
  docker exec k2-trino trino --execute "SELECT bool_and(t.q10 = l.quote_volume) FROM (SELECT exchange,
    canonical_symbol, date_trunc('minute', exchange_ts) AS window_start, CAST(CAST(sum(CAST(price_e8
    AS decimal(38, 0)) * qty_e8) AS decimal(38, 10)) / 10000000000000000 AS decimal(38, 10)) AS q10
    FROM lake.gold.trades WHERE CAST(exchange_ts AS date) = DATE '2026-11-24' GROUP BY 1, 2, 3) t
    JOIN lake.gold.ohlcv_1m l USING (exchange, canonical_symbol, window_start)"
  #   "false"
  ```
- The bucket is `date_trunc('minute', exchange_ts)` in a UTC session (`profiles.yml`).

It is tagged `min_by_max_by`: it would not port unchanged to an engine without `min_by`,
`max_by` and row comparison. Everything else in it is plain SQL.

Its tests: two unit tests on made-up rows (the bucket edges, and ties on `exchange_ts` broken by
`recv_ts_ns` then `trade_seq`), each of which failed when the model was changed to break it; the
grain's uniqueness and not-null tests; and the candle's envelope. Its parity with the lake:
`scripts/parity-ohlcv.sh --dbt` (below). dbt's unit materialization runs each unit test as the
domain's writer in the tested model's catalog and schema, creating and dropping a table
`<unit test name>__dbt_tmp` there (`market_analytics.candles`; `logs/dbt.log` shows the
`create table` and the `drop table if exists`).

## `market_analytics.delayed_candles.ohlcv_delayed_20m`

ADR-035's delayed product, as its Decision has it: a view over the lake's own candles,
`lake.gold.ohlcv_1m` (through `staging.stg_gold__ohlcv_1m`), in a product of its own
(`contracts/market-analytics/delayed-candles/`) so it has its own schema, which the
entitlements phase can grant on its own. Same columns as the lake table without its lineage
pair, same grain `(exchange, canonical_symbol, window_start)`, and as current as the lake.
Readable through Trino only, like every view ([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md)).

A candle is served once both hold, each on `window_start` alone:

- **the delay**: its whole minute, `[window_start, window_start + 1 minute)`, ended at least 20
  minutes before the query, so no trade in it is younger than 20 minutes
  (`window_start <= current_timestamp - INTERVAL '21' MINUTE`, which also prunes the month
  partitions);
- **the horizon**: it is older than its venue's newest minute in the lake
  (`window_start < max(window_start)` per exchange). `docker/lake/gold.py` MERGEs every minute an
  ingest batch touches, recomputed over all of `gold.trades`, so the newest minute is the one an
  ingest may have read only part of, and it is rewritten when its later trades land. Every
  earlier minute was complete when the ingest read past it. A stalled ingest stops the horizon,
  so the view never serves a minute the lake has not finished.

The horizon is per venue: a venue whose capture or ingest lags holds back only its own candles.
Not covered: a trade delivered after its minute was passed (the view then shows the lake's
corrected candle), and partitions read to different points by a capped catch-up ingest.

The first version read this domain's hourly `candles.ohlcv_1m` instead. That mart is a snapshot
of the lake at its build, whose newest minutes are partial, and they crossed the 20-minute cut
later and were served with fewer trades than the lake held (found in review, 2026-11-25).

`now` is dbt's cross-database `current_timestamp()`, which the unit tests override to fix it
on made-up candles: the venue's newest minute is hidden however old it is; the minute that ended
exactly 20 minutes before is shown and the next one is not; a venue stalled for two hours shows
nothing past its horizon while a current one is unaffected. Each failed when the model was
broken that way (no horizon; 20 instead of 21; one horizon for all venues). Its data tests: the
grain, and the delay itself on whatever the view returns.

On the stack, every served candle of the last hour has the trade count a recount of
`lake.gold.trades` gives, and no venue's newest served minute is its newest minute in the lake
(2026-11-25, after the view was rebuilt):

```bash
docker exec -i k2-trino trino <<'SQL'
WITH served AS (SELECT exchange, canonical_symbol, window_start, trade_count
  FROM market_analytics.delayed_candles.ohlcv_delayed_20m WHERE window_start >= current_timestamp - INTERVAL '60' MINUTE),
recount AS (SELECT exchange, canonical_symbol, date_trunc('minute', exchange_ts) AS window_start, count(*) AS n
  FROM lake.gold.trades WHERE exchange_ts >= current_timestamp - INTERVAL '61' MINUTE GROUP BY 1, 2, 3)
SELECT count(*) > 0, bool_and(s.trade_count = r.n), count_if(r.n IS NULL) = 0,
       max(window_start) <= current_timestamp - INTERVAL '21' MINUTE
FROM served s LEFT JOIN recount r USING (exchange, canonical_symbol, window_start)
SQL
#   "true","true","true","true"      some served, all equal, none without trades, the delay holds
docker exec k2-trino trino --execute "SELECT bool_and(v.newest < l.newest)
  FROM (SELECT exchange, max(window_start) AS newest FROM market_analytics.delayed_candles.ohlcv_delayed_20m GROUP BY 1) v
  JOIN (SELECT exchange, max(window_start) AS newest FROM lake.gold.ohlcv_1m GROUP BY 1) l USING (exchange)"
#   "true"                           every venue's newest lake minute is held back
```

## `market_analytics.venue_quality.venue_quality_scorecard`

Per venue and UTC day, how complete the lake is: one table row per `(exchange, utc_date)` on
which the venue has a trade in `gold.trades` or a check in `audit.checks`. Nothing in it is
estimated; each column is a count of something the lake already records.

| Column | From | What it counts |
|---|---|---|
| `trades` | `gold.trades`, by the UTC date of `exchange_ts` | trades |
| `trades_after_gap` | `seq_gap` | trades whose previous trade id for the symbol was more than one below: trades the archive never received came before them |
| `trade_ids_missing` | `missing_before` | how many trade ids those gaps leave out |
| `trades_gap_unknown` | `seq_gap IS NULL` | trades with no earlier trade of the symbol in silver's lookback, where a gap is unknowable |
| `day_audits` | `audit.checks`, `job = 'maintenance'`, day-scoped checks, under the day before the run | audits of that day on the venue's tables: `bronze_schema_drift`, which samples the 24 hours before the run, 21 of them in the previous UTC day at the 03:00 schedule (`ohlcv_parity` and `bars_parity` audit the previous day too, but their scopes are cross-venue) |
| `day_audits_failed` | the same, `NOT passed` | of those, the failed ones, a check that raised included |
| `whole_table_audits` | every other maintenance audit, under the day it ran | audits over the venue's whole tables or topic partitions as they stood at the run, `silver_flags` left out (it publishes rates and cannot fail). They are about every day to date, not the row's day alone |
| `whole_table_audits_failed` | the same, `NOT passed` | of those, the failed ones |
| `raw_frames_lost` | `job = 'ingest'`, `offset_gap` on `market.crypto.v3.raw.<venue>`, under the day of the ingest | raw frames Redpanda evicted before the lake read them; bronze, silver and `gold.trades` are decoded from these |
| `trade_records_lost` | the same on `market.crypto.v3.trades.<venue>` | the typed trade records |
| `book_records_lost` | the same on `market.crypto.v3.book.<venue>` | the typed book records |

A check is a venue's when its scope names the venue as a whole token, in each of the shapes the
lake writes (`lake.bronze.kraken_trade`, `lake.silver.trades_kraken`, `lake.gold.trades/kraken`,
`market.crypto.v3.raw.kraken/7`); a cross-venue scope such as `lake.gold.trades` is no venue's,
and the venues are those with trades. `regexp_like` and `split_part` are Trino's, so the model
is tagged `regexp_like`; the rest is plain SQL.

**Owned by `market-analytics`**, by ADR-044's rule that a mart joining two domains belongs to
the one that consumes it: it joins market-capture's `audit.checks` with this domain's
`gold.trades`, and the reader who needs it, deciding which venue's day to trust, reads this
domain's candles. Owned by market-capture, it would make that domain read this one, the
both-directions dependency [ADR-033](../docs/adr/ADR-033-data-mesh-four-domains.md) names as its
revisit trigger. `audit.checks` needed a contract to be read (`contracts/market-capture/lake-audit/`),
filed under market-capture, whose tables most checks are about, until the platform-owned
governance domain ADR-044 records as intent is decided; layer `audit` joined the contract
schema's layers for it.

Its tests: three unit tests on made-up venues (the UTC day edge, gaps and unknowable trades; each
scope shape, a near-miss venue name, the rows that do not count, an audit-only day; the day each
check is about and the losses by topic family), each made to fail by breaking the model one way (a
substring match, `silver_flags` counted, unknowable trades dropped, a left join, a day in another
zone, a day-scoped audit under its run date, the losses summed across families); the grain; and
two things the data must hold that the SQL does not force: a gap trade leaves out at least one id
(`trades_after_gap <= trade_ids_missing`), and no loss count is negative.

## Scheduled: the `dbt_marts` DAG

Airflow runs every domain every hour at minute 10 (`docker/airflow/dags/dbt_marts.py`,
[runbook](../docs/runbooks/dbt-marts.md)). astronomer-cosmos renders one task group per selector
in `selectors.yml` from a `dbt ls` of this directory, cached in an Airflow Variable keyed on the
project's files, and runs one task per node as that domain's writer: `dbt build --select <node>`
for a model, which runs its unit tests, the model and its data tests; `dbt source freshness` and
the source tests for a lake table a mart reads. dbt itself is `/opt/k2/dbt-venv/bin/dbt`, a
virtualenv built from this `uv.lock` in the Airflow image, so its pins never meet Airflow's,
and the packages are the image's, installed from `package-lock.yml` at build time
(`K2_DBT_PACKAGES`). This directory is mounted read-only at `/opt/k2/dbt` in the scheduler and
the dag-processor; cosmos runs each command in a temporary directory of symlinks to it, without
`target/`, `logs/` or `package-lock.yml`, where dbt writes its own. A change to a model is live
at the next parse; a change to `uv.lock`, `packages.yml` or `package-lock.yml` needs the image
rebuilt, and `docker/airflow/tests/test_dbt_image.py` fails on an image behind them
([runbook](../docs/runbooks/dbt-marts.md)).

Once a day, in the 04:10 run, each domain's writer expires its marts' snapshots:

```bash
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt run-operation expire_mart_snapshots
#   expire_mart_snapshots: "market_analytics"."candles"."ohlcv_1m" expire_snapshots at 7d
#   expire_mart_snapshots: "market_analytics"."candles"."ohlcv_1m" remove_orphan_files at 7d
#   expire_mart_snapshots: "market_analytics"."venue_quality"."venue_quality_scorecard" expire_snapshots at 7d
#   expire_mart_snapshots: "market_analytics"."venue_quality"."venue_quality_scorecard" remove_orphan_files at 7d
```

`macros/expire_mart_snapshots.sql`: every table model in the writer's own catalog, `ALTER TABLE
... EXECUTE expire_snapshots(retention_threshold => '7d')`, then `remove_orphan_files` at the
same. A rebuild is one snapshot, so an hourly mart keeps a week of builds for time travel. Seven
days is the connector's floor and nobody may set the session properties that lower it (ADR-044),
so a shorter threshold fails instead of running. `make test-dbt` runs it on the throwaway Trino.

With no marts at all, `make generate` writes `sources: []` and `selectors: []`; `make test-dbt`
parses that with no models, and the DAG then has no task group.

## Parity

`scripts/parity_ohlcv.py --dbt` adds a side D: this project's compiled SQL for
`ohlcv_1m`, its staging view inlined, run through Trino over `lake.gold.trades FOR VERSION AS
OF` the newest snapshot the lake's own candles for the day were computed from, and compared
with `lake.gold.ohlcv_1m` at tolerance zero on open, high, low, close and trade count as
integers, and on volume and `quote_volume` as decimals, digit for digit; `quote_volume` typed
differently by the two engines fails the run. (Sides A, B and C compare volume as a double
rounded to 8 decimals, as they always have.) It proves the SQL dbt builds with, on the input
Spark read; it does not read the table dbt last built, which holds the lake as it was at build
time.

On 2026-11-24 at the current snapshots, 2026-11-25:

```bash
uv run --no-project --with duckdb==1.4.4 --with clickhouse-connect==0.8.18 --with trino==0.340.0 \
  python scripts/parity_ohlcv.py --day 2026-11-24 --ohlcv-snapshot <lake.gold.ohlcv_1m current id> \
  --skip-silver --skip-clickhouse --dbt
#   quote_volume: lake decimal(38,10), dbt ['decimal(38,10)']
#   lake.gold.ohlcv_1m vs dbt.ohlcv_1m: <n> buckets, 0 differ      (n: every bucket of the day)
#   PARITY: PASS
```

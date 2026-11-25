# dbt

The domain marts: SQL models dbt builds on Trino, each in its domain's own catalog
([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)). Spark keeps
everything from raw to gold; a model here is one `SELECT` over lake tables or other marts.

| Pinned | Version | Why this one |
|---|---|---|
| `dbt-core` | 1.12.7 (2026-11-12) | the newest on PyPI on 2026-11-25. dbt v2, the Rust engine, lists no Trino adapter |
| `dbt-trino` | 1.12.0 (2026-11-13) | the newest; requires `dbt-core` 1.12. Its README says tested on Trino 478; this project runs it on 484 (below) |
| `metaplane/dbt_expectations` | 0.10.10 (2025-12-02), with `godatadriven/dbt_date` 0.21.0 | the maintained fork, whose README lists Trino; `package-lock.yml` |
| `sqlfluff` | 4.5.0 (2026-10-30) | the newest; dev group only |

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

Run on the stack on 2026-11-25 (Trino 484, 2 CPU / 4 GiB): `dbt build --selector
market_analytics` passed 10 of 10 (one view, one table, two unit tests, six data tests) in
16.2 s; `market_analytics.candles.ohlcv_1m` was a 77,463-row CTAS over all of
`lake.gold.trades` in 11.8 s. A second build, 22 minutes later, took 9.8 s, the table 6.8 s
for 78,118 rows. `on_table_exists: replace` makes a rebuild `CREATE OR REPLACE`: one Iceberg
table whose snapshots are its builds (two after the second, each by `dbt_market_analytics`).
`dbt source freshness` passed in 1.7 s.

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
tables it reads (`contracts/market-analytics/candles/product.yaml`). A second domain adds its
folder, a three-line block in `dbt_project.yml` that makes its marts tables, and its marts in
its contracts; `make generate` gives it a selector.

`scripts/dbt_manifest_check.py` holds the project to that on the compiled manifest: only
staging selects from a source; every model is in a domain's catalog; every mart is a table, is
declared at its relation, reads exactly the tables its contract says, and has a uniqueness test
over the whole table on exactly its contract grain.

## Generated, do not edit

From the contracts by `make generate` (`platform/k2gen/k2gen/generate.py`), under the CI drift
gate:

- `models/_sources.yml`: every lake table a mart reads, with the column types Trino reads, the
  contract's freshness (`slaProperties` latency) and a uniqueness test on lake.sql's identifier
  fields. That test covers the last 6 hours by the table's time partition: over all of
  `gold.trades` (25.4M rows), and over its last 2 days, it failed with
  `EXCEEDED_LOCAL_MEMORY_LIMIT` at Trino's 844.80MB per-node default (2026-11-25). The whole
  table is the nightly Spark audit's (`duplicate_identifiers`).
- `selectors.yml`, above.

## The first mart: `market_analytics.candles.ohlcv_1m`

The port of `gold.candles()` (`docker/lake/gold.py`) at one minute. Two things differ from the
Spark text, and both are written in the model:

- `quote_volume`: the lake holds it with 6 decimals in a `decimal(38,10)` column: no
  2026-11-24 bucket has a seventh. Spark's decimal rules type `gold.py`'s `DECIMAL(38,10)`
  product `decimal(38,6)`, which would explain it (VERIFY: not run on Spark). Ported verbatim,
  the product fails in Trino with `Decimal overflow`; the model multiplies the `_e8` integers as
  `decimal(38,0)` and rounds the quotient half up at 6 decimals. On 2026-11-24, 0 of 20,144
  buckets differed from the lake, against 10,819 when rounded at 10.
- The bucket is `date_trunc('minute', exchange_ts)` in a UTC session (`profiles.yml`).

It is tagged `min_by_max_by`: it would not port unchanged to an engine without `min_by`,
`max_by` and row comparison. Everything else in it is plain SQL.

Its tests: two unit tests on made-up rows (the bucket edges, and ties on `exchange_ts` broken by
`recv_ts_ns` then `trade_seq`), each of which failed when the model was changed to break it; the
grain's uniqueness and not-null tests; and the candle's envelope. Its parity with the lake:
`scripts/parity-ohlcv.sh --dbt` (below).

## Parity

`scripts/parity_ohlcv.py --dbt` adds a side D: this project's compiled SQL for
`ohlcv_1m`, its staging view inlined, run through Trino over `lake.gold.trades FOR VERSION AS
OF` the newest snapshot the lake's own candles for the day were computed from, and compared
with `lake.gold.ohlcv_1m` at tolerance zero on open, high, low, close, trade count, volume and
`quote_volume`. It proves the SQL dbt builds with, on the input Spark read; it does not read the
table dbt last built, which holds the lake as it was at build time.

On 2026-11-24 at the current snapshots, 2026-11-25:

```bash
uv run --no-project --with duckdb==1.4.4 --with clickhouse-connect==0.8.18 --with trino==0.340.0 \
  python scripts/parity_ohlcv.py --day 2026-11-24 --ohlcv-snapshot <lake.gold.ohlcv_1m current id> \
  --skip-silver --skip-clickhouse --dbt
#   quote_volume: lake decimal(38,10), dbt ['decimal(38,10)']: compared
#   lake.gold.ohlcv_1m vs dbt.ohlcv_1m: 20144 buckets, 0 differ
#   PARITY: PASS
```

# 19. The lakehouse and its domain warehouses

> **You will learn** what a lakehouse table, a catalog and a warehouse are, why K2 keeps one producer lake beside one mart warehouse per domain, and who may read or write each.
> **Read this if** you are new to Iceberg or Trino, you will query through Trino, or you will build a mart.
> **Before this** chapter 09; chapter 18 helps.

**Retired, 2026-11-26.** The structure this chapter describes still runs: MinIO, Lakekeeper with
warehouse `k2` and one warehouse per domain, Trino's read-only `lake` catalog and a writable catalog
per domain fenced by `rules.json`. What retired is what filled it: the v3 lake ingest and its
namespaces in `k2`, the dbt marts and their DAG, and the notebooks
([ADR-056](../adr/ADR-056-unified-data-product-platform.md); archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/)),
dropped or archived after a backup. No live crypto data lands in any warehouse until phase 004 of the
[platform plan](../plans/2026-11-26-unified-product-platform/README.md), and the target's layout is a
namespace per product ([chapter 20](20-target-data-product-platform.md)). Examples below that name a
lake table or a mart ran on the dates given, against tables since dropped.

**From release `v3.0.0-alpha.3`** the catalogs, writers and rules this chapter describes are
replaced: each domain has a read-only reader catalog and a build catalog, the rules are compiled per
release and deny by default, and Trino logins are passwords over TLS
([chapter 22](22-access-and-identity.md), [ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)).
The producer lake, a warehouse per domain and their names stand.

## The picture

K2 keeps its data as files in one bucket, `k2-lake`. Six parts work on it:

- **MinIO** is object storage: a flat store of files addressed by key, with the same API as
  Amazon S3. It holds the bucket.
- **Lakekeeper** is the catalog service. It records which files make up each table.
- **Warehouses** are separate areas inside the catalog, each with its own tables. There are
  six: `k2`, the producer lake, and one per data domain for that domain's marts. A data domain
  is a team's area of data. A mart is a table built for consumers out of lake tables.
- **Spark** runs the batch jobs that write the lake. **Airflow**, the scheduler, starts them. The
  jobs that ran retired with the v3 ingest; none runs until the plan adds one.
- **Trino** is a SQL server. It reads every warehouse and writes only the domain warehouses.
- **DuckDB** was a library inside the notebooks, archived with the ingest. It read the lake.

Spark, Trino and DuckDB are engines: the programs that run queries. dbt, a tool that turns SQL
`SELECT` files into tables, builds marts through Trino, the v3 ones now retired ([`dbt/`](../../dbt/),
[phase P3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md)).

```mermaid
flowchart TB
  AF["Airflow: the v3 lake DAGs retired, none yet"]
  SP["Spark jobs, docker/lake/"]
  DBT["dbt, dbt/"]
  TR["Trino, one node"]
  DK["DuckDB, in notebooks, archived"]
  subgraph LK ["Lakekeeper, the catalog service"]
    WK["warehouse k2: its v3 namespaces retired"]
    WD["warehouses fx_reference, governance,<br/>market_analytics, market_capture, reference_data"]
  end
  S3[("MinIO bucket k2-lake<br/>warehouse/k2/ and warehouse/&lt;domain&gt;/")]
  AF -.-> SP
  SP -->|"writes"| WK
  DBT -->|"sends SQL"| TR
  TR -->|"reads, as catalog lake"| WK
  TR -->|"writes, as dbt_&lt;domain&gt;"| WD
  DK -.->|"read, ATTACH 'k2'"| WK
  WK & WD -.->|"point at files in"| S3
```

The catalog never holds data. An engine asks it where a table's files are, then reads or writes
those files in MinIO itself, with the MinIO keys from `.env`
([`spark_conf.py`](../../docker/lake/spark_conf.py), [`lake.properties`](../../platform/k2gen/k2gen/emit/trino_etc.py)).

## First principles

**A table is files plus metadata.** In a lakehouse, a table is data files plus metadata files in
object storage. The data files are Parquet, a column-oriented format. The metadata files say
which data files are the table right now. They can carry statistics per column; K2 kept them
only for the columns that range filters used ([chapter 09](09-lake-layers.md)). Apache Iceberg is the
table format that defines the metadata. The retired `lake.gold.ohlcv_1m` was 19 Parquet files under
`s3://k2-lake/warehouse/k2/<uuid>/data/`, with its metadata beside them under `metadata/`.

**A snapshot is one version of the table.** Every write that succeeds adds a snapshot: the
complete list of the table's files at that moment. The old files stay in place.
The retired `lake.gold.ohlcv_1m` had 196 snapshots when this was written. A snapshot also carries a
summary of properties. Each ingest into the lake's raw table stored there the bus positions it read
up to (Kafka offsets), as `k2.kafka-offsets` ([chapter 08](08-lake-ingest.md)).

**The catalog holds the pointer.** A table has many metadata files, one per version. The catalog
says which one is current. Lakekeeper keeps that pointer per table in PostgreSQL
(`LAKEKEEPER__PG_DATABASE_URL_WRITE` in `docker-compose.yml`). A commit, the step that publishes
a new version, swaps the pointer. A reader sees the old version or the new one, never half of
each. Two writers need this. Both start from the same version, and the catalog accepts only the
first swap. The second retries on top of the new version: this is optimistic concurrency
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). The retry re-applies the losing
append. That is fine for plain rows, and wrong for K2's ingest, whose append also recorded where
it stopped reading. So the v3 Spark jobs also took one writer lock, a `flock` in
[`lock.py`, archived](../../legacy/v3-lake-ingest/lake/lock.py). Lakekeeper speaks the Iceberg REST protocol, which every
engine here speaks ([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)).

**A warehouse is a fenced area of storage with its own table list.** Each Lakekeeper warehouse
has a storage location, a bucket plus a key prefix, and its own namespaces and tables.
[`init-lake.sh`](../../docker/lake/init-lake.sh) creates `k2` at `warehouse/k2`, then one warehouse
per line of `warehouses.txt` at `warehouse/<name>`.
A connection opens one warehouse; a name in one does not resolve in another. Lakekeeper also
chooses where a new table's files go, under its warehouse's prefix. A Trino table placed inside
`warehouse/k2/` from a writable catalog was refused with `Failed to create transaction`
([ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)).

**A namespace is a folder of tables; SQL calls it a schema.** Warehouse `k2` had five, `raw`,
`bronze`, `silver`, `gold` and `audit`, until the retirement dropped them. **A Trino catalog is a named connection to one
warehouse**, one file under `docker/trino/etc/catalog/` ([README](../../docker/trino/README.md)).
The file names a connector, the Trino plugin for one kind of storage: here always
`connector.name=iceberg`. Catalog `lake` opens warehouse `k2`; catalog `market_analytics` opens
warehouse `market_analytics`. A full name is `<catalog>.<schema>.<table>`, so the table the
Lakekeeper UI showed under `k2` and `gold` was the retired `lake.gold.trades`. Spark and DuckDB also call `k2`
by the name `lake`.

**Time travel reads an older snapshot.** Old snapshots stay until they are expired, so a query can
read the table as it was: `FOR VERSION AS OF <snapshot id>` or `FOR TIMESTAMP AS OF <time>`.
The nightly Spark job `maintenance.py`, retired with the ingest, expired snapshots older than
seven days, keeping at least ten. K2's parity checks, which compare engines' answers and are stood
down until phase 004, pinned a snapshot id
([chapter 03](03-data-engineering-concepts.md#iceberg-snapshots)).

## Two zones, and why the fence sits between them

| | Producer lake | Domain warehouses |
|---|---|---|
| Warehouse | `k2` | one per domain, for example `market_analytics` |
| Holds | the layers `raw` to `gold`, and `audit`, until they retired ([chapter 09](09-lake-layers.md)) | marts, one schema per data product |
| Written by | Spark jobs only | that domain's writer, `dbt_<domain>`, through Trino |
| Rebuildable | bronze to gold from the raw table; the raw table itself, no | always, from the lake |
| Fence | Trino's `lake` catalog is read-only at the connector | `rules.json`, one writer per domain |

A fence is a boundary software enforces, not one people promise to respect. `rules.json` is
Trino's file of access rules.

**The lake can lose data for good.** Redpanda, the message bus, keeps raw frames for 48 hours or
2 GiB per partition ([chapter 07](07-wire-contracts.md)). After that, the lake's raw table held
the only copy. The ingest read its start from the newest ingest snapshot (ADR-022). So a stray
`rollback_to_snapshot`, which sets a table back to an older snapshot, would have hidden rows while
the ingest resumed after them. Once the bus has expired those frames, they are lost (ADR-044).

**A mart cannot.** A mart is a `SELECT` over lake tables and other marts. If one is damaged, dbt
builds it again from the lake. The cost of a lost mart is a rebuild.

So the lake gets the strongest fence: Trino's connector refuses every commit to it. The domain
warehouses get a fence per writer, which is weaker, as [the limits](#the-honest-limits) show. The
lake stayed one warehouse because one pipeline built all its layers. Splitting it by domain would
have moved every table and re-based every audit, for fences nobody stands behind (ADR-044); the
target gives each product its own namespace instead.

## Domains: one entry, one fence

A data domain is a body of data that one team owns and answers for
([ADR-033](../adr/ADR-033-data-mesh-four-domains.md)). A data product is one owned unit inside it,
such as `candles`. [`contracts/domains.yaml`](../../contracts/domains.yaml) lists five domains:
`market-capture`, `reference-data`, `fx-reference`, `market-analytics` and `governance`. The lake's tables
belonged to the first four as owners and lived in `k2` until they retired ([chapter 18](18-data-mesh.md)). Every
domain warehouse is empty until its first mart. Until the retirement `market_analytics` had three, each in its
product's schema: `candles.ohlcv_1m`, the 1-minute candles; `delayed_candles.ohlcv_delayed_20m`,
a view of the lake's own candles without the newest 20 minutes; and `venue_quality.venue_quality_scorecard`, per
venue and day, how complete the lake is ([the dbt README, archived](../../legacy/v3-lake-ingest/dbt/README.md)). `governance` had one,
`quality_results.dq_results`, below.

One entry in `domains.yaml` gives a domain its whole fence. `make generate` runs
`generate.py`, which turns the id `market-analytics`
into the name `market_analytics` and writes:

```mermaid
flowchart TB
  Y["contracts/domains.yaml: market-analytics"]
  G["make generate: platform/k2gen/k2gen/generate.py"]
  W["docker/lake/generated/warehouses.txt"]
  C["docker/trino/etc/catalog/market_analytics.properties"]
  R["docker/trino/etc/rules.json: dbt_market_analytics owns it"]
  I["init-lake.sh creates warehouse market_analytics"]
  T["Trino, at start: catalog market_analytics"]
  Y --> G
  G --> W & C & R
  W --> I
  C & R --> T
```

It also writes the catalog's twin for `make test-trino`, under `docker/trino/test/local/`.
`lake.properties`, the rest of `docker/trino/etc/` and `init-lake.sh` are hand-written. CI runs
`make generate` and fails on any difference. `k2gen validate` refuses a domain whose name would
be `lake`, `system` or `k2`: those names are taken.

Inside a domain catalog there is one schema per data product, for example the retired
`market_analytics.delayed_candles.ohlcv_delayed_20m`. A product rebuild can then drop one
schema without touching its siblings. The platform adds two schemas that are not products, in
every domain catalog: `staging`, dbt's views over the lake tables the domain's marts read, and
`dq`, where the domain's own writer records its dbt test results; `k2gen validate` refuses a
product with either name ([`dbt/`](../../dbt/)). A mart that joins two domains belongs
to the domain that consumes it, and lives in that domain's warehouse; nobody writes into a
producer's warehouse. The retired scorecard was one: it joined market-capture's audit table with
market-analytics' gold trades, and lived in `market_analytics`, which read both.

Governance is a domain stored the same way, owned by the platform team
([ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md)). Its quality results show
how the fences shape a cross-domain product: every dbt invocation that runs a test writes the
results into `dq.dbt_test_results` of its own catalog, as its own writer, through an
`on-run-end` hook, failed tests included. The retired `governance.quality_results.dq_results` was
a view over those tables and the lake's audit table, built by `dbt_governance` after every other
domain in each run of the retired `dbt_marts` DAG. Nothing crosses a fence but a read, and a definer view lets any reader see
it through its owner.

## Who can do what

| Who | Lake, warehouse `k2` | Its own domain warehouse | Another domain's warehouse |
|---|---|---|---|
| Spark jobs in `docker/lake/` | **write**, the only writer. While the v3 jobs ran, one at a time: the `flock` in the archived `lock.py`, and one run at a time per Airflow job | none today: `spark_conf.py` opens `k2` only | none today |
| `dbt_<domain>`, through Trino | **read**. Write **denied**: `rules.json` grants only `SELECT` there, and the connector is read-only (`iceberg.security=read_only`) | **write** with SQL statements: create and drop schemas, tables and views; insert, delete, update. `rules.json` makes it the owner | **read**. SQL writes **denied** by `rules.json` |
| Any other Trino user, `trino` included | **read**. Write **denied**, as above | none: no rule names it | **read**. SQL writes **denied** by `rules.json` |
| DuckDB, in the notebooks and parity scripts (archived) | **read** while they ran, through `ATTACH 'k2' AS lake`. No Trino rule applies; the code only reads | not attached today | not attached today |

In [`rules.json`](../../platform/k2gen/k2gen/emit/trino_rules.py) the first matching rule wins. A writer's own
rule comes first; everyone else falls through to the last, `SELECT` and `GRANT_SELECT` only. The
same file also denies, to every user: `CALL` of any Iceberg procedure, by leaving out a
`procedures` list, so only Trino's built-in procedures run; and the two session properties that
set the 7-day retention floors. And every user sees and kills only its own queries. Two cells
are weaker than they look: any identity can be claimed, and table procedures (maintenance
commands such as `optimize`) inside the domain warehouses are open to every user, at or above
the floors. Both are in [the honest limits](#the-honest-limits).

## How a query travels

Both walk-throughs below ran on 2026-11-25, against tables since retired; the path they trace is
unchanged.

**A read.** Say `market_analytics` holds a mart `candle_counts`, candles per exchange, in the
scratch schema this chapter used. A user joins it to the venue dimension in the lake:

```sql
SELECT c.exchange, v.subscribed
FROM market_analytics.chapter_check.candle_counts c
JOIN lake.gold.dim_venue v ON v.exchange = c.exchange AND v.is_current  -- table retired 2026-11-26
```

1. The CLI sends the SQL to Trino over HTTP with the header `X-Trino-User: trino`, its default
   user inside the container.
2. Trino reads the first part of each name as a catalog, and each catalog file names one
   warehouse. `rules.json` gives user `trino` `SELECT` on both tables.
3. For each table, the catalog's Iceberg connector asks Lakekeeper to load it. Lakekeeper
   answers with the current metadata and the location of its file.
4. From the current snapshot, Trino reads the table's manifests, the files listing its data
   files. Partition values and column statistics let it skip files the query cannot need.
5. Trino reads the remaining Parquet files from MinIO, joins them in memory and returns rows.
   `EXPLAIN (TYPE IO, FORMAT JSON)` lists the two inputs: catalog `market_analytics` and `lake`.

**A write.** A dbt model is one `SELECT` in a file. dbt wraps it in a statement such as
`CREATE TABLE ... AS` and sends it to Trino as the domain's writer. The same statement by hand:

```sql
CREATE TABLE market_analytics.chapter_check.candle_counts AS
SELECT exchange, count(*) AS candles FROM lake.gold.ohlcv_1m GROUP BY exchange  -- table retired 2026-11-26
```

1. The header says `X-Trino-User: dbt_market_analytics`. `rules.json` makes that user the owner
   of every schema in `market_analytics`. Trino reads `lake.gold.ohlcv_1m` (since retired) as in the read above.
2. Trino asks Lakekeeper to create the table in warehouse `market_analytics`. Lakekeeper picks
   the location, `s3://k2-lake/warehouse/market_analytics/<uuid>`, because the catalog file sets
   `iceberg.rest-catalog.server-assigned-table-location-enabled=true`.
3. Trino writes the Parquet files, then new metadata, then commits: Lakekeeper swaps the
   pointer. The snapshot summary records `engine-name` `trino` and `trino_user`
   `dbt_market_analytics`.
4. Warehouse `k2` is untouched: the since-retired `lake.gold.ohlcv_1m` had no snapshot written by Trino.

To rebuild a mart, drop it and build it again. A `DROP TABLE` through Trino is a purge drop:
Lakekeeper's `tabular_purge` task deletes the files within seconds (ADR-044).

The Airflow DAG `dbt_marts`, retired with the ingest, built every mart again, whole, every hour
at minute 10, each domain as its own writer. A build was a `CREATE OR REPLACE`, so each one added a
snapshot; once a day the same writer expired snapshots and removed orphan files older than 7 days.
Seven days is the Iceberg connector's floor, and nobody may set the session properties that lower it
(ADR-044; [the runbook, archived](../../legacy/v3-lake-ingest/docs/runbooks/dbt-marts.md)). The same daily step compacted each
domain's `dq.dbt_test_results`, which grew by an append per tested node per run, before expiring it.

## Views

A view is a stored query, not stored data; reading it runs the query. Trino saves a view in the
catalog as an Iceberg view whose SQL is in Trino's dialect
([ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md)). That is why the delayed product, candles
held back by 20 minutes, can be a view: it needs no schedule of its own. The chapter's first
check was a view over `lake.gold`, which is as current as the lake:

```sql
CREATE VIEW market_analytics.chapter_check.candles_delayed AS
SELECT exchange, canonical_symbol, window_start FROM lake.gold.ohlcv_1m  -- table retired 2026-11-26
WHERE window_start < current_timestamp - INTERVAL '20' MINUTE
```

The product that shipped, `market_analytics.delayed_candles.ohlcv_delayed_20m`, retired with the
lake, was that view built by dbt, with two cuts. It showed a candle once its whole minute ended 20
minutes before the query, so no trade in it was younger than 20 minutes (`window_start <=
current_timestamp - INTERVAL '21' MINUTE`), and only before its venue's newest minute in the lake.
The ingest rewrote a minute when more of its trades landed, so the newest one could be partial;
without the second cut a stalled ingest would have let it through as final
([the dbt README, archived](../../legacy/v3-lake-ingest/dbt/README.md)). It was what a delayed-data entitlement was to be
limited to.

Trino checks a view's base tables against the view's owner, not its reader: a definer view. Across
domains, that needs the owner to have `all` on each base table's catalog. A read-only catalog
rule broke it with `Access Denied: View owner does not have sufficient privileges`. So
`rules.json` keeps every catalog `all` and fences in the schema and table rules (ADR-044).

The limit: views are readable through Trino only. DuckDB 1.4.4 lists a domain's tables but not its
views. Spark 3.5.5 reads a view only when its SQL is also valid Spark SQL (ADR-035). So consumers
read marts through Trino (ADR-044). One who needs the rows in another engine reads the gold
table, or a scheduled table if one is ever declared.

## The honest limits

1. **Identity is a claim, not a proof.** Trino takes the user from the `X-Trino-User` header and
   does not check it. Anyone on the host can say `--user dbt_market_analytics`. The port listens
   on `127.0.0.1` only (`K2_TRINO_PORT`, default 8088). The fences stop mistakes, not a liar
   (ADR-044; [ADR-028](../adr/ADR-028-non-goals-and-honest-limits.md) section 8 and its Outcome).
   The trigger for authentication is a second person with access to the host.
2. **Table procedures are not fenced between domain catalogs.** They run as `ALTER TABLE ...
   EXECUTE`: `optimize`, `rollback_to_snapshot`, `expire_snapshots` and others. No built-in
   access control in Trino 484 restricts one per user on a writable catalog. As
   `dbt_market_analytics`, `EXPLAIN ALTER TABLE reference_data.scratch_check.candles EXECUTE
   optimize` planned ([`docker/trino/README.md`](../../docker/trino/README.md)). Expiry and
   orphan cleaning stay at or above their 7-day floor, because nobody may lower it: the two
   session properties that would were open to every user until a probe on 2026-11-25 expired a
   scratch table's snapshots at `0s`, and `rules.json` now denies them. That is acceptable for
   now: the lake is not exposed, and a mart is rebuilt from the lake. `make test-trino` asserts
   the gap, so a Trino release that closes it fails there first (ADR-044).
3. **One set of storage keys.** Trino, Spark and DuckDB all hold the MinIO root keys, and
   Lakekeeper is unauthenticated. Spark and DuckDB reach both directly, so Trino's rules bind
   only Trino clients. A MinIO key per identity, scoped to its own prefix, comes with the
   entitlements phase, P5 ([ADR-031](../adr/ADR-031-governed-self-serve-platform.md), ADR-044).
   Until 2026-11-26 MinIO's API and console also listened on every interface and Trino's
   startup log printed the access key, so any machine on the local network holding that key
   could rewrite the lake without going through Trino. Every port is on `127.0.0.1` now and
   the log is silent ([SECURITY.md](../../SECURITY.md)); loopback keeps other machines out,
   not other users of the host. The scoped keys, and the root rotation that must come with
   them, are the next change.

## On a cloud

[Chapter 17](17-scale-out-path.md) maps MinIO to S3 and Lakekeeper to tasks on ECS with a
Multi-AZ PostgreSQL. A warehouse is a bucket plus a key prefix, so each domain's warehouse would
move as a prefix. Chapter 17 places Trino on ECS and the domain warehouses as prefixes of the
lake's bucket (§3, §4), and rebuilds the marts after a region loss rather than replicating
them (§7). One gap to know, also in its §3.1: the Trino catalog files hard-code the Lakekeeper
URL, the MinIO endpoint and region `local-01` (`lake.properties`, `generate.py`), where
`docker/lake/` reads each from the environment.

## See it yourself

The step-by-step version, with every write on a throwaway Trino, was the
[lakehouse labs](../../legacy/v3-lake-ingest/docs/development/lakehouse-labs.md), archived with the ingest.

Read-only, against the running stack, on 2026-11-25; the two catalog listings re-run on
2026-11-26, after the governance domain's rollout. Counts grew with every commit. `SHOW CATALOGS`
and the warehouse listing read structure the retirement left in place; every other command reads
a namespace or table it dropped, and is the record of what it printed.

```bash
docker exec k2-trino trino --execute 'SHOW CATALOGS'
#   "fx_reference" "governance" "lake" "market_analytics" "market_capture" "reference_data" "system"
docker exec k2-trino trino --execute 'SHOW SCHEMAS FROM lake'
#   "audit" "bronze" "gold" "information_schema" "raw" "silver" "system"
docker exec k2-trino trino --execute 'SHOW SCHEMAS FROM market_analytics'
#   "candles" "delayed_candles" "dq" "information_schema" "staging" "system" "venue_quality"
#                                     one schema per mart product, dbt's staging views, the test results
docker exec k2-trino trino --execute \
  "SELECT max(close_time) <= current_timestamp - INTERVAL '20' MINUTE FROM market_analytics.delayed_candles.ohlcv_delayed_20m"
#   "true"                                            the delay holds
docker exec k2-trino trino --execute \
  'SELECT (SELECT count(*) FROM lake.gold."ohlcv_1m$files"), (SELECT count(*) FROM lake.gold."ohlcv_1m$snapshots")'
#   "19","196"                                        data files, snapshots
docker exec k2-trino trino --execute 'SELECT file_path FROM lake.gold."ohlcv_1m$files" LIMIT 1'
#   "s3://k2-lake/warehouse/k2/<uuid>/data/exchange=<exchange>/window_start_month=2026-11/<file>.parquet"
docker exec k2-trino trino --execute \
  "SELECT summary['engine-name'] FROM lake.raw.\"messages\$snapshots\" ORDER BY committed_at DESC LIMIT 1"
#   "spark"
docker exec k2-trino trino --execute \
  "SELECT count(*) > 0 FROM lake.gold.ohlcv_1m FOR TIMESTAMP AS OF current_timestamp - INTERVAL '1' HOUR"  # retired table
#   "true"                                            time travel
curl -s 'http://localhost:18181/management/v1/warehouse?project-id=00000000-0000-0000-0000-000000000000' \
  | jq -r '.warehouses[] | [.name, ."storage-profile"."key-prefix"] | @tsv'
#   k2  warehouse/k2, then one line per domain warehouse              the catalog's five
p=$(curl -s 'http://localhost:18181/catalog/v1/config?warehouse=k2' | jq -r .defaults.prefix)
curl -s "http://localhost:18181/catalog/v1/$p/namespaces/gold/tables/ohlcv_1m" | jq -r '."metadata-location"'
#   s3://k2-lake/warehouse/k2/<uuid>/metadata/00399-<uuid>.metadata.json   higher after each commit
```

The read, the write and the view above were run for this chapter as `dbt_market_analytics` in
`market_analytics.chapter_check`, then dropped; nothing of theirs remains.

## Common mistakes and what you will see

Each line below was printed by `k2-trino`, or by DuckDB 1.4.4, on 2026-11-25, some against
tables since retired; the errors are the same for any table.

- **Naming the warehouse, or no catalog.** `SELECT count(*) FROM k2.gold.trades` (retired) gives
  `Catalog 'k2' not found`; `FROM gold.trades` (retired) gives `Catalog must be specified when
  session catalog is not set`. The warehouse is `k2`; the catalog is `lake`. Write
  `lake.<schema>.<table>` (the retired example was `lake.gold.trades`), or pass `--catalog lake`.
- **Writing to the lake.** In the retired `gold` namespace, `CREATE TABLE lake.gold.chapter_check
  (x integer)` gave `Access Denied: Cannot create table lake.gold.chapter_check` (retired), for every
  user, and a table procedure on the retired candles gave `Access Denied: Cannot execute table
  procedure OPTIMIZE on gold.ohlcv_1m`, retired with them. That message has no
  catalog prefix because it comes from the read-only connector, which names a table by schema and
  table only; `rules.json`'s check for table procedures allows everything, so it is not the one
  refusing.
- **Forgetting `--user`.** Inside the container the CLI's user is `trino`, and no rule lets it
  write. `CREATE SCHEMA market_analytics.chapter_check` gives `Access Denied: Cannot create
  schema market_analytics.chapter_check`.
- **Writing to another domain.** As `dbt_market_analytics`, `CREATE TABLE
  reference_data.chapter_check.intruder (x integer)` gives `Access Denied: Cannot create table
  reference_data.chapter_check.intruder`. As `dbt_reference_data`, an insert into a
  `market_analytics` table gives `Access Denied: Cannot insert into table ...`.
- **Asking for DDL you do not own.** `SHOW CREATE TABLE lake.gold.ohlcv_1m` (retired) gave `Access
  Denied: Cannot show create table for lake.gold.ohlcv_1m` (retired). Trino shows DDL only to a
  table's owner, and nobody owns a lake table. Use `DESCRIBE`: `DESCRIBE lake.gold.ohlcv_1m`, retired since,
  worked.
- **Reading a view from DuckDB.** With warehouse `market_analytics` attached, the view above
  gave `Catalog Error: Table with name candles_delayed does not exist!`, while the table beside
  it was listed. Read views through Trino.

## Glossary

| Term | Meaning |
|---|---|
| Object storage, bucket, key prefix | a flat store of files by key (MinIO, S3); its top-level container; a key's start, like a folder |
| Parquet | the column-oriented file format of every lake data file |
| Apache Iceberg | the table format: metadata files that turn a set of Parquet files into a table |
| Lakehouse | tables made of files in object storage plus a table format, queried like a database |
| Metadata file, manifest | Iceberg's description of one table version; the files listing its data files |
| Snapshot, snapshot summary | one version of a table; properties stored on it, such as `k2.kafka-offsets` |
| Commit | the step that publishes a new version: the atomic swap of the table's pointer |
| Catalog service | the server holding each table's pointer: Lakekeeper, speaking Iceberg REST |
| Optimistic concurrency | writers start freely; the catalog accepts the first commit and the second retries |
| Writer lock | the `flock` in `lock.py` (archived) that ran the v3 Spark jobs one at a time |
| Warehouse | a fenced area in the catalog: its own storage prefix and its own tables |
| Namespace, schema | a group of tables in a warehouse; Iceberg's word and SQL's word for one thing |
| Trino catalog | a named connection in Trino to one warehouse; one file in `docker/trino/etc/catalog/` |
| Engine | a program that runs queries over the tables: Spark, Trino, DuckDB |
| Time travel, snapshot expiry | reading an older snapshot; dropping snapshots past seven days, ten kept at least |
| Producer lake | warehouse `k2`, written by Spark only; its `raw` to `gold` namespaces retired on 2026-11-26 |
| Mart | a table for consumers, built from lake tables, in a domain warehouse |
| Test results, `dq` | one row per dbt test per run, in each domain catalog's `dq.dbt_test_results`, written by its own writer |
| Data domain, data product | a body of data one team owns; one owned unit in it, one schema per mart product |
| Writer identity, `X-Trino-User` | `dbt_<domain>`, the only user whose SQL statements may change its catalog; the unchecked header naming a user |
| Fence | a boundary software enforces, not one people promise to respect |
| Read-only connector | `iceberg.security=read_only` on catalog `lake`: no statement that would commit, and no table procedure |
| File-based access control | Trino's rules in `rules.json`: lists of rules, first match wins |
| Connector, table procedure | the Trino plugin a catalog uses; maintenance run as `ALTER TABLE ... EXECUTE` |
| View, definer view | a stored query; one whose base tables Trino checks against the view's owner |
| dbt, model | a tool that builds tables from SQL; one `SELECT` in a file, which becomes one table |
| Airflow | the scheduler; its jobs are DAGs, and the four that ran here, `lake_ingest`, `lake_maintenance`, `lake_rebuild` and `dbt_marts`, retired with the v3 ingest |
| Purge drop | a `DROP TABLE` that deletes the files too, as Trino's do |
| Entitlements phase | phase P5 of the platform plan, which brings a storage key per identity |

## Key points

- A lakehouse table is files plus metadata. The catalog holds the pointer to the current
  metadata, and a commit swaps it.
- One producer lake, written by Spark alone and read-only to Trino at the connector, because
  losing it can be permanent.
- One warehouse, catalog and writer per domain, generated from `contracts/domains.yaml`, because
  a mart has an owner and can always be rebuilt from the lake.
- Three limits, recorded in ADR-044: identity is an unchecked header, table procedures are open
  between domain catalogs at or above the retention floors, and every engine holds the root
  storage keys.

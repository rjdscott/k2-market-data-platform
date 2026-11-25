# ADR-035: Trino and dbt for marts; the Spark and dbt boundary; the ceiling is 25 CPU / 48 GiB

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Query engine, Infrastructure

---

## Context

The platform layer ([ADR-031](ADR-031-governed-self-serve-platform.md)) needs a home for
consumer marts and one SQL engine that every consumer and dbt can share. Today the lake has
one writer, Spark, which runs the ingest every 5 minutes on a 3.0 CPU quota with a median
run of 168 s of its 300 s slot ([ADR-034](ADR-034-airflow-3-replaces-prefect.md), Outcome
2026-11-25). ClickHouse serves `gold` as a derived hot tier and originates nothing
([ADR-025](ADR-025-clickhouse-derived-hot-tier.md)). DuckDB reads the lake in-process in each
notebook. A consumer's SQL model has nowhere to run but the ingest job.

Three earlier decisions stand in the way:

- [ADR-006](ADR-006-spark-batch-only.md) rejected Trino as a replacement for Spark in batch
  ETL: always-on, a 2 to 4 GB floor for the coordinator, built for interactive queries.
- [ADR-028](ADR-028-non-goals-and-honest-limits.md) section 6: no serving layer beyond
  ClickHouse SQL.
- [ADR-029](ADR-029-research-production-parity-contract.md) section 3: every materialised
  product is computed a second and a third time and compared at tolerance zero.

The budget: [ADR-034](ADR-034-airflow-3-replaces-prefect.md) set 20 CPU / 36 GiB at the
bootstrap peak, and the stack declares 19.30 CPU / 32.812 GiB there
(`python3 scripts/check-budget.py`, 2026-11-25). The plan estimated Trino at 2.0 CPU / 4 GiB,
which does not fit. [ADR-031](ADR-031-governed-self-serve-platform.md), still Proposed,
committed the platform to 20 CPU / 36 GiB at the bootstrap peak and named a default-profile
bootstrap total above either figure as its revisit trigger. That trigger has fired, and this
ADR is the response.

The engine on the reference host has 28 CPUs and 53,149,339,648 bytes (49.50 GiB)
(`docker info --format '{{.NCPU}} {{.MemTotal}}'`, 2026-11-25). Measured use of the whole
stack is far below any of these sums. Over the 6 hours to 2026-11-25 12:17 UTC, a window that
includes the Trino spike containers and the running `k2-trino`, the peak was **3.84 cores**
and **10.12 GiB** (10,864,435,200 bytes) of process memory, from Prometheus over cAdvisor:

```
max_over_time(sum(rate(container_cpu_usage_seconds_total{job="cadvisor"}[1m]))[6h:1m])
max_over_time(sum(container_memory_rss{job="cadvisor"})[6h:1m])
```

The ceiling is a sum of declared limits, not of use.

What Trino does on this lake was measured before deciding, on the running stack, with
Trino 484 in throwaway containers (commands and results in
[`docker/trino/README.md`](../../docker/trino/README.md), "Verified 2026-11-25"):

| Question | Measured | Command |
|---|---|---|
| Catalog through Lakekeeper 0.13.9, static keys | namespaces and tables listed; no vended credentials, the warehouse has `sts-enabled: false` | `trino --execute 'SHOW SCHEMAS FROM lake'` |
| Types | `timestamp(6) with time zone`, `decimal(38,10)`, `decimal(28,10)`, `map(varchar, varbinary)`, `array(row(px decimal(28,10), qty decimal(28,10)))` | `SELECT typeof(...)` and `DESCRIBE` on gold, silver, raw |
| Time travel | works | `SELECT count(*) FROM lake.gold.ohlcv_1m FOR VERSION AS OF <id from "ohlcv_1m$refs">` |
| Writes in a scratch namespace of warehouse `k2` (the spike, before any rules) | CTAS, INSERT, a view and `DROP` all committed; tables at `<warehouse>/<uuid>` once `iceberg.rest-catalog.server-assigned-table-location-enabled=true` (without it, `<warehouse>/<name>-<random>`, flat across namespaces); metadata written gzipped (`*.gz.metadata.json`) | `CREATE TABLE ... AS SELECT`, then the table's `metadata.location` over REST |
| Table procedures under file-based rules alone | allowed on every table: `EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` planned with no denial while `EXPLAIN INSERT` into it was denied; `checkCanExecuteTableProcedure` is an empty method in `FileBasedSystemAccessControl` (line 1043) and `FileBasedAccessControl` (line 727) at tag 484 | `EXPLAIN` of each, through catalog `lake` with the first draft of the rules |
| `iceberg.security=read_only` on catalog `lake` | `Access Denied: Cannot execute table procedure OPTIMIZE` and `ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m` (the rollback run for real at the current `main` id, which stayed put); `CREATE TABLE`, `INSERT`, `DELETE`, `CREATE SCHEMA`, `CALL lake.system.unregister_table` denied; `SELECT` and `FOR VERSION AS OF` work | `ALTER TABLE ... EXECUTE` and each statement through `lake`, 2026-11-25 |
| A second warehouse `marts` on the same bucket | created by `docker/lake/init-lake.sh` with key prefix `warehouse/marts`, idempotent on re-run; a Trino CTAS and a view over `lake.gold.ohlcv_1m` landed under `s3://k2-lake/warehouse/marts/<uuid>`; DuckDB 1.4.4 (a second `ATTACH`) and Spark 3.5.5 (a second `SparkCatalog`) both read the table | the init script against the live Lakekeeper, then reads from each engine |
| `DROP TABLE` | a purge drop: Lakekeeper's `tabular_purge` task deleted the files within seconds (`Data at location ... deleted`) | `docker logs k2-lakekeeper` |
| Iceberg REST view | created and read back, stored with dialect `trino`; DuckDB 1.4.4 does not list it (`Table with name ... does not exist!`); Spark 3.5.5 reads it only when its SQL also parses as Spark SQL (`to_iso8601` failed with `Cannot load function`) | `CREATE VIEW` in Trino, then a read from each engine |
| A Trino-written table, read elsewhere | DuckDB 1.4.4 and Spark 3.5.5 both read it, types intact | DuckDB `ATTACH` as `scripts/parity_ohlcv.py` does; `lake_session()` in `k2-spark-iceberg` |
| Snapshot summary | `engine-name: trino`, `engine-version: 484`, `trino_query_id`, `trino_user`, the standard totals; no `k2.*` key | the table's `snapshots` over REST |
| Session time zone | the client's zone; a client that sends none gets the JVM's, pinned by `-Duser.timezone=UTC` even with a non-UTC zone (for example `TZ=America/New_York`) set in the container | `SELECT current_timezone()` from the CLI and from a raw HTTP request |
| Decimal arithmetic | `DECIMAL(38,10) * DECIMAL(38,10)` is typed `decimal(38,6)` and fails with `Decimal overflow` once the exact product passes about 1e18; `gold.candles()`'s `quote_volume` expression, ported verbatim, fails on `gold.trades` | `SELECT CAST(10000000000 AS DECIMAL(38,10)) * CAST(10000000000 AS DECIMAL(38,10))` |
| 1 CPU / 2 GiB, heap 1280M | the `gold.candles()` aggregation over all of `gold.trades` in its integer-product form (`DECIMAL(38,0)` product, not the verbatim expression that overflows), as a CTAS into the spike's scratch namespace, since dropped: 21.3 s, peak 1.41 GiB, no OOM | `docker stats` sampled during the query |
| 2 CPU / 4 GiB, heap 2816M | idle 1.42 GiB; the candle aggregation (open, high, low, close, volume, count) over all of `gold.trades`, read only: 6.6 s alone, four at once 12.5 s each at most, peak 2.86 GiB and 2.15 cores, no OOM | `system.runtime.queries`, `docker stats` |
| The closed paths, probed on `k2-trino` (the compose service, this configuration) | `CREATE TABLE marts.esc.t (x integer) WITH (location='s3://k2-lake/warehouse/k2/_escape_test')`: `Failed to create transaction`, Lakekeeper refused a location outside warehouse `marts` and nothing was created; `CALL marts.system.register_table(...)` and `CALL lake.system.unregister_table('gold','nope')`: `Access Denied: Cannot execute procedure ...`; `ALTER TABLE marts.esc.a EXECUTE add_files(location => 's3://k2-lake/warehouse/k2/_escape_test', format => 'PARQUET')`: `add_files procedure is disabled` | each statement, 2026-11-25 |

---

## Decision

**We will run one Trino 484 node beside Spark as the lake's SQL engine for consumers and for
dbt-built marts, with the lake's catalog read-only at the connector and marts in a second
Lakekeeper warehouse of their own, and raise the default profile's ceiling to 25 CPU / 48 GiB
at the bootstrap peak, because consumer marts need an engine that reads this lake through its
REST catalog without sharing the ingest's container, nothing that engine does may commit to
the system of record, and the platform is to run always-on as one whole.** Scope: SQL
serving and set-based marts. Spark's jobs, ADR-022's exactly-once ingest and ClickHouse's hot
tier do not change.

**Two catalogs, two warehouses.**

| Trino catalog | Lakekeeper warehouse | Key prefix in `k2-lake` | Trino may |
|---|---|---|---|
| `lake` | `k2` (`raw`, `bronze`, `silver`, `gold`, `audit`) | `warehouse/k2/` | read and time travel; `iceberg.security=read_only` refuses every commit, table procedures included |
| `marts` | `marts`, created by `docker/lake/init-lake.sh` beside `k2`; no schema until dbt creates one | `warehouse/marts/` | read and write |

**This departs from the phase file**, which puts the marts in a namespace `marts` of the lake
([phase P3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md), pull
request 1). The plan is a dated snapshot and is left as written. The reason: a namespace in
warehouse `k2` needs a writable catalog over `k2`, and through a writable catalog Trino 484's
file-based rules cannot stop `ALTER TABLE ... EXECUTE` on any table of it (the measurement
above). A second warehouse lets the catalog over `k2` be read-only at the connector, which
does stop them, and keeps the marts' files under a prefix of their own.

**The boundary.**

| Work | Where | Why there |
|---|---|---|
| Redpanda to raw, bronze, silver, gold; backfill; rebuild; compaction, expiry, audits | Spark (`docker/lake/`), unchanged | needs Kafka offsets, the writer lock, per-record decoding, or `maintenance.py` |
| Consumer marts: one SQL statement per model over lake tables | dbt on Trino, tables in catalog `marts` | set-based, versioned with tests, no lock |
| Ad hoc SQL over every lake table | Trino, catalog `lake`, read only | one engine and one configuration for every client |
| The hot tier | ClickHouse, unchanged | ADR-025 |

A model that needs an offset, the lock or code per record is a Spark job. A model that is a
`SELECT` over lake tables is a dbt model. Spark does not write warehouse `marts`; Trino
writes nothing else. Every object Trino owns, views included, lives in catalog `marts`. So the
phase's planned `governance.dq_results` view, which the phase file places in a `governance`
namespace of the lake, lands in catalog `marts` too (for example `marts.governance.dq_results`),
reading `lake.audit.checks` and the dbt test results from there: a second, named departure
from the phase file's wording, for the same reason as the first. `maintenance.py` will compact and expire the marts like every other
table once the first one exists (P3, the next pull request).

**What the spike fixed in the configuration** (`docker/trino/etc/`):

- `trinodb/trino:484`, released 15 Oct 2026, the newest release that day.
- Both catalogs: `iceberg.catalog.type=rest`, native S3 (`fs.s3.enabled`) with the static
  MinIO keys from the environment, path-style, region `local-01`, no vended credentials.
  `marts` lets Lakekeeper assign table locations, so Trino's tables sit at
  `<warehouse>/<uuid>` as Spark's do.
- 2.0 CPU / 4 GiB, heap 2816M: 1.25 GiB of the limit for metaspace, code cache, thread
  stacks and direct buffers. One query fits in half of that; the rest is for dbt threads.
- The JVM in UTC, and clients set UTC themselves: the session zone is the client's.
- File-based access control (`etc/rules.json`) at the catalog level only: `marts` all,
  `lake` and `system` read-only, cluster information readable, no Iceberg `CALL`
  procedures. It is the second layer on `lake` and the only rule on `marts`. Identity is the
  `X-Trino-User` header, not authenticated, so no rule names a user; the port is published on
  `127.0.0.1` only.
- `make test-trino` asserts both layers in CI: the connector setting on a local Iceberg
  catalog (every table procedure denied, the writable twin's `optimize` runs), the rules on
  memory catalogs named `lake` and `marts`.

**The delayed product is a view in `marts`.** A view over `lake.gold` with a 20-minute
predicate is always current with no schedule, and on the Trino path it is where entitlement
is enforced (ADR-039, P5). Measured: created in `marts`, stored under `warehouse/marts/`, its
SQL naming `lake.gold.ohlcv_1m`. The cost is that only Trino reads it reliably: DuckDB does
not see Iceberg views, and Spark read this one only because its SQL is also Spark SQL and
Spark's catalog over `k2` is also named `lake`. A consumer on another engine reads the gold
table its rights allow, or a scheduled table if one is ever declared.

**`quote_volume` joins the `ohlcv_1m` parity only as Trino computes it exactly.** The
verbatim port overflows. The candidate is a `DECIMAL(38,0)` product of the two `_e8`
integers, divided by 1e16, which Trino types `decimal(38,6)`. P3's second pull request
compares `typeof` on both engines and leaves the column out, and says so, if they differ.

**Parity for marts, and what is lifted.** A table in `marts` is covered by dbt tests on its
grain and columns, unit tests on its logic, and a reference computation where one is cheap,
not by three engines: ADR-029 clause 3 is scoped that way for marts and stands for gold.
dbt's `ohlcv_1m`, the worked example, keeps the full check against Spark's `gold.ohlcv_1m` at
a pinned snapshot. ADR-028 section 6 is lifted for SQL serving only; an HTTP API stays a
non-goal, and section 8 stands.

**The ceiling.** 25 CPU / 48 GiB at the bootstrap peak, summed over every default-profile
service's `deploy.resources.limits`, one-shots included, gated by
`scripts/check-budget.py` in CI. It supersedes ADR-034's 20 CPU / 36 GiB clause and nothing
else in ADR-034. The maintainer's direction is one always-on platform with no opt-in
profiles, so ADR-034's sentence that the `governance` profile is outside the ceiling no
longer describes it.

Why 25 / 48: the plan's estimate for the always-on catalog phase is +3.00 CPU / +7.000 GiB
(the OpenMetadata services at +2.50 / +5.500 and their migrate one-shot at +0.50 / +1.500,
plan README lines 356 to 357). On today's 21.30 CPU / 36.812 GiB that is 24.30 CPU /
43.812 GiB, and 25 / 48 covers it with 0.70 CPU and 4.188 GiB to spare. The catalog phase
still measures on first boot and records its own figures in its ADR; it no longer has to
reopen the ceiling on the plan's estimate.

The honest cost: Docker has 28 CPUs and 49.50 GiB on this host, so a stack that ever reached
all its memory limits at once would leave 3 cores and 1.50 GiB for the engine and the
operating system. That is accepted because the ceiling is a sum of declared limits and
measured use is far below it: 3.84 cores and 10.12 GiB at the 6-hour peak above.

| | Services | CPU | GiB |
|---|---|---|---|
| Before, steady | 18 | 17.30 | 30.312 |
| Before, bootstrap peak | 23 | 19.30 | 32.812 |
| This pull request, steady | 19 | 19.30 | 34.312 |
| This pull request, bootstrap peak (gated) | 24 | **21.30** | **36.812** |
| With `--profile legacy` (reported) | 26 | 22.80 | 38.312 |

Headroom at the gated peak: 3.70 CPU and 11.188 GiB (`python3 scripts/check-budget.py`,
2026-11-25).

---

## Rationale

- **It was measured on this catalog first.** Every property, type and failure above came from
  the running lake, not from documentation; the three that would have bitten later (open
  table procedures, purge drop, decimal overflow) were found that way.
- **The connector, not the rule file, is what can refuse a table procedure.** Trino's
  read-only connector control leaves `checkCanExecuteTableProcedure` at the interface
  default, which denies; the file-based controls override it with an empty method.
- **A server, not a library.** Entitlements are enforced on a server in P5, and the rules here
  are the first of them. DuckDB in-process has no place to hold a rule between a consumer and
  the lake.
- **Out of the ingest's container.** dbt on Spark would run through the Thrift Server inside
  `spark-iceberg`, on the 3.0 CPU quota the ingest already throttles on. Trino has its own
  quota and its own heap, and a mart build cannot slow the system of record.
- **ADR-006's objection holds for ETL and not for this.** Trino does not take over the ingest,
  backfill or compaction. Its floor was predicted at 2 to 4 GB; one query ran at 2 GiB, and
  4 GiB carried four concurrent aggregations at 2.86 GiB.
- **The ceiling follows the stated aim and the measured use.** The maintainer's decision
  (2026-11-25): one always-on platform, sized so the next planned phase fits on its own
  estimate, on a host whose measured use (3.84 cores, 10.12 GiB) is a small fraction of the
  declared sum.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| One warehouse, marts as a namespace `marts` of `k2`, a writable catalog fenced by file-based rules (the phase file's shape, and this pull request's first draft) | the rules deny DDL and DML outside `marts` but not `ALTER TABLE ... EXECUTE`: `checkCanExecuteTableProcedure` is empty in both file-based controls at tag 484, and `EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` planned on the live lake. `rollback_to_snapshot` on `raw.messages` would lose offsets |
| One warehouse with `lake` read-only and a second writable catalog over the same warehouse | the writable catalog sees every namespace of `k2`, so its table procedures reach gold again |
| dbt-spark through the Spark Thrift Server | shares the ingest's 3.0 CPU quota and its container; a slow mart becomes a late ingest |
| dbt-duckdb against the REST catalog | in-process per client: no shared server for consumers and nowhere to enforce a rule; this lake's views would be invisible to it |
| ClickHouse as the mart engine | ClickHouse is a derived tier that originates nothing (ADR-025); 24.3 reads Iceberg and does not write it |
| Trino at 1 CPU / 2 GiB | ran every single query in the spike; concurrency was not measured at that size, and dbt runs models in parallel |
| Trino 483 or older | 484 is the release the spike measured; nothing in it failed that an older one would fix |
| 24 CPU / 42 GiB, this pull request's first figure | covers today's 21.30 / 36.812 but not the catalog phase's estimate of 24.30 / 43.812, which would have reopened the ceiling one phase later |
| Keep 20 CPU / 36 GiB and trim elsewhere | 1.30 CPU and 0.812 GiB would come out of other services' limits, and ADR-034 keeps Airflow's at the plan's figures until real ingests are measured; ADR-031 rejected opt-in profiles for the platform layer |

---

## Consequences

**Easier:** SQL over every lake table for any client with a Trino driver, at a pinned snapshot
when it matters; consumer marts as reviewed dbt models with tests, outside the ingest; no
statement through Trino can commit to the lake; the marts' files are separable by prefix.

**Harder:** one more JVM always on, 2.0 CPU / 4 GiB declared, about 1.4 GiB idle. Every engine
that reads or maintains marts needs a second catalog: Spark, DuckDB, `maintenance.py`,
`lake-metrics` and the contracts learn about warehouse `marts` in the next pull request, when
the first mart exists. Views are Trino-only. The candle expression cannot be copied from
Spark without the decimal change. A `DROP TABLE` in `marts` deletes the files at once; a
dropped mart is rebuilt by dbt.

**Committed to:** `iceberg.security=read_only` on catalog `lake` and writes only in catalog
`marts`, checked by `make test-trino` and the CI `trino` job; warehouse `marts` created by
`init-lake.sh`; the 25 CPU / 48 GiB bootstrap ceiling in CI; Trino pinned by hand
(Dependabot tracks Dockerfiles, not compose images); the Spark and dbt boundary above.

**Not in this decision: a storage key for Trino.** Trino holds the MinIO root keys, so the
read-only connector is a guard rail, not a boundary: a client that reached MinIO with those
keys could still overwrite lake files. The second layer is a MinIO credential for Trino that
can read `warehouse/k2/` and write only `warehouse/marts/`. It is deferred to the
entitlements work of P5 (ADR-039), because on one host, with identity asserted by a header
and one operator, Lakekeeper itself is unauthenticated and any process here can reach the
same storage with the same keys; a scoped key for Trino alone moves no boundary until the
catalog has one.

**Risks:**

- **Concurrency beyond four queries is unmeasured**, and so is a real dbt run: dbt arrives in
  P3's second pull request.
- **The decimal rule may not be the only one.** Division and multiplication scales differ
  between Trino and Spark; every arithmetic step in a ported model casts explicitly.
- **A view in `marts` names catalog `lake` in its SQL.** An engine whose catalog over `k2` has
  another name cannot expand it; Spark and the DuckDB recipes use `lake` today.

**Revisit when:**

- a Trino query fails with `EXCEEDED_LOCAL_MEMORY_LIMIT` or the container is OOM-killed, or
  the `dbt_marts` run takes longer than its schedule interval: measure, then raise inside
  the 3.70 CPU / 11.188 GiB of headroom;
- P5 enforces entitlements on Trino (ADR-039): give Trino the scoped storage key above in
  the same pull request;
- a second person gets access to the host: authentication on Trino before anything else;
- the declared bootstrap sum passes 95% of either figure: 23.75 CPU or 45.6 GiB
  (`python3 scripts/check-budget.py`);
- measured process memory of the whole stack stays above 32 GiB for an hour, which is
  `min_over_time(sum(container_memory_rss{job="cadvisor"})[1h:1m]) > 34359738368` in
  Prometheus: the margin for the engine was accepted on use being far below the limits;
- the host changes: `docker info --format '{{.NCPU}} {{.MemTotal}}'` prints anything but
  `28 53149339648`;
- Trino 485 is released: upgrade by hand after `make test-trino` and the checks in
  `docker/trino/README.md`.

---

## References

- Plan: [phase P3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md) of
  the [governed self-serve platform](../plans/2026-11-24-governed-self-serve-platform/README.md)
- Umbrella: [ADR-031](ADR-031-governed-self-serve-platform.md). Departs from
  [ADR-006](ADR-006-spark-batch-only.md); supersedes the ceiling clause of
  [ADR-034](ADR-034-airflow-3-replaces-prefect.md); lifts
  [ADR-028](ADR-028-non-goals-and-honest-limits.md) section 6 for SQL serving; scopes
  [ADR-029](ADR-029-research-production-parity-contract.md) section 3 for marts. Each carries
  an Outcome dated 2026-11-25.
- Code: `docker/trino/`, `scripts/trino-rules-test.sh`, `scripts/check-budget.py`,
  `docker/lake/init-lake.sh`
- Upstream: Trino 484 documentation for the Iceberg connector, the REST catalog, the S3 file
  system and file-based access control; `lib/trino-plugin-toolkit` (the file-based and
  read-only access controls) and `core/trino-main/.../StatementAnalyzer.java` at tag 484

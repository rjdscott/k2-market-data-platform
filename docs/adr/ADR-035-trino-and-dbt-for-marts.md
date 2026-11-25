# ADR-035: Trino and dbt for marts; the Spark and dbt boundary; the ceiling is 24 CPU / 42 GiB

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
which does not fit. The engine on the reference host has 28 CPUs and 53,149,339,648 bytes
(49.50 GiB) (`docker info --format '{{.NCPU}} {{.MemTotal}}'`, 2026-11-25). Whole-stack use
over 6 hours on 2026-11-25 peaked near 3.6 cores and 7.3 GiB of process memory (the owner's
reading of the cAdvisor series on the K2 Resources dashboard). The ceiling is a sum of declared
limits, not of use.

What Trino does on this lake was measured before deciding, on the running stack, with
Trino 484 in throwaway containers (commands and results in
[`docker/trino/README.md`](../../docker/trino/README.md), "Verified 2026-11-25"):

| Question | Measured | Command |
|---|---|---|
| Catalog through Lakekeeper 0.13.9, static keys | namespaces and tables listed; no vended credentials, the warehouse has `sts-enabled: false` | `trino --execute 'SHOW SCHEMAS FROM lake'` |
| Types | `timestamp(6) with time zone`, `decimal(38,10)`, `decimal(28,10)`, `map(varchar, varbinary)`, `array(row(px decimal(28,10), qty decimal(28,10)))` | `SELECT typeof(...)` and `DESCRIBE` on gold, silver, raw |
| Time travel | works | `SELECT count(*) FROM lake.gold.ohlcv_1m FOR VERSION AS OF <id from "ohlcv_1m$refs">` |
| Writes in a scratch namespace | CTAS, INSERT, a view and `DROP` all committed; tables at `<warehouse>/<uuid>` once `iceberg.rest-catalog.server-assigned-table-location-enabled=true` (without it, `<warehouse>/<name>-<random>`, flat across namespaces); metadata written gzipped (`*.gz.metadata.json`) | `CREATE TABLE ... AS SELECT`, then the table's `metadata.location` over REST |
| `DROP TABLE` | a purge drop: Lakekeeper's `tabular_purge` task deleted the files within seconds (`Data at location ... deleted`) | `docker logs k2-lakekeeper` |
| Iceberg REST view | created and read back, stored with dialect `trino`; DuckDB 1.4.4 does not list it (`Table with name ... does not exist!`); Spark 3.5.5 reads it only when its SQL also parses as Spark SQL (`to_iso8601` failed with `Cannot load function`) | `CREATE VIEW` in Trino, then a read from each engine |
| A Trino-written table, read elsewhere | DuckDB 1.4.4 and Spark 3.5.5 both read it, types intact | DuckDB `ATTACH` as `scripts/parity_ohlcv.py` does; `lake_session()` in `k2-spark-iceberg` |
| Snapshot summary | `engine-name: trino`, `engine-version: 484`, `trino_query_id`, `trino_user`, the standard totals; no `k2.*` key | the table's `snapshots` over REST |
| Session time zone | the client's zone; a client that sends none gets the JVM's, pinned by `-Duser.timezone=UTC` even with `TZ=Australia/Sydney` in the container | `SELECT current_timezone()` from the CLI and from a raw HTTP request |
| Decimal arithmetic | `DECIMAL(38,10) * DECIMAL(38,10)` is typed `decimal(38,6)` and fails with `Decimal overflow` once the exact product passes about 1e18; `gold.candles()`'s `quote_volume` expression, ported verbatim, fails on `gold.trades` | `SELECT CAST(10000000000 AS DECIMAL(38,10)) * CAST(10000000000 AS DECIMAL(38,10))` |
| 1 CPU / 2 GiB, heap 1280M | the `gold.candles()` aggregation over all of `gold.trades` as a CTAS: 21.3 s, peak 1.41 GiB, no OOM | `docker stats` sampled during the query |
| 2 CPU / 4 GiB, heap 2816M | idle 1.42 GiB; the same aggregation read only: 6.6 s alone, four at once 12.5 s each at most, peak 2.86 GiB and 2.15 cores, no OOM | `system.runtime.queries`, `docker stats` |

---

## Decision

**We will run one Trino 484 node beside Spark as the lake's SQL engine for consumers and for
dbt-built marts, writing only namespace `marts`, and raise the default profile's ceiling to
24 CPU / 42 GiB at the bootstrap peak, because consumer marts need an engine that reads and
writes this lake through its REST catalog without sharing the ingest's container, and the
platform is to run always-on as one whole.** Scope: SQL serving and set-based marts. Spark's
jobs, ADR-022's exactly-once ingest and ClickHouse's hot tier do not change.

**The boundary.**

| Work | Where | Why there |
|---|---|---|
| Redpanda to raw, bronze, silver, gold; backfill; rebuild; compaction, expiry, audits | Spark (`docker/lake/`), unchanged | needs Kafka offsets, the writer lock, per-record decoding, or `maintenance.py` |
| Consumer marts: one SQL statement per model over lake tables | dbt on Trino, tables in `marts` | set-based, versioned with tests, no lock |
| Ad hoc SQL over every namespace | Trino, read only outside `marts` | one engine and one rule file for every client |
| The hot tier | ClickHouse, unchanged | ADR-025 |

A model that needs an offset, the lock or code per record is a Spark job. A model that is a
`SELECT` over lake tables is a dbt model. Spark does not write `marts`; Trino writes nothing
else. `maintenance.py` compacts and expires `marts` like every other table (plan P3).

**What the spike fixed in the configuration** (`docker/trino/etc/`):

- `trinodb/trino:484`, released 15 Oct 2026, the newest release that day.
- Catalog `lake`: `iceberg.catalog.type=rest`, warehouse `k2`, native S3 (`fs.s3.enabled`)
  with the static MinIO keys from the environment, path-style, region `local-01`, no vended
  credentials, server-assigned table locations so Trino's tables sit where Spark's do.
- 2.0 CPU / 4 GiB, heap 2816M: 1.25 GiB of the limit for metaspace, code cache, thread
  stacks and direct buffers. One query fits in half of that; the rest is for dbt threads.
- The JVM in UTC, and clients set UTC themselves: the session zone is the client's.
- File-based access control (`etc/rules.json`): every table readable, `marts` read-write,
  no DDL or DML anywhere else, no Iceberg `CALL` procedures, `system` read-only. Asserted by
  `make test-trino` in CI. Identity is the `X-Trino-User` header, not authenticated, so no
  rule names a user; the port is published on `127.0.0.1` only.
- Namespace `marts` is created by `docker/lake/init-lake.sh`, not by dbt.

**The delayed product is a view.** A view over gold with a 20-minute predicate is always
current with no schedule, and on the Trino path it is where entitlement is enforced (ADR-039,
P5). The cost is that only Trino reads it reliably: DuckDB does not see Iceberg views, and
Spark reads one only when its SQL is also Spark SQL. A consumer on another engine reads the
gold table its rights allow, or a scheduled table if one is ever declared.

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

**The ceiling.** 24 CPU / 42 GiB at the bootstrap peak, summed over every default-profile
service's `deploy.resources.limits`, one-shots included, gated by
`scripts/check-budget.py` in CI. It supersedes ADR-034's 20 CPU / 36 GiB clause and nothing
else in ADR-034. Four of the host's 28 cores and 7.5 of its 49.5 GiB stay outside it for the
operating system and the engine. Later phases run always-on inside it: there is no opt-in
profile for the platform layer, so ADR-034's sentence that the `governance` profile is
outside the ceiling no longer describes the direction.

| | Services | CPU | GiB |
|---|---|---|---|
| Before, steady | 18 | 17.30 | 30.312 |
| Before, bootstrap peak | 23 | 19.30 | 32.812 |
| This pull request, steady | 19 | 19.30 | 34.312 |
| This pull request, bootstrap peak (gated) | 24 | **21.30** | **36.812** |
| With `--profile legacy` (reported) | 26 | 22.80 | 38.312 |

Headroom at the gated peak: 2.70 CPU and 5.188 GiB (`python3 scripts/check-budget.py`,
2026-11-25).

---

## Rationale

- **It was measured on this catalog first.** Every property, type and failure above came from
  the running lake, not from documentation; the two that would have bitten later (purge drop,
  decimal overflow) were found that way.
- **A server, not a library.** Entitlements are enforced on a server in P5, and the rules here
  are the first of them. DuckDB in-process has no place to hold a rule between a consumer and
  the lake.
- **Out of the ingest's container.** dbt on Spark would run through the Thrift Server inside
  `spark-iceberg`, on the 3.0 CPU quota the ingest already throttles on. Trino has its own
  quota and its own heap, and a mart build cannot slow the system of record.
- **ADR-006's objection holds for ETL and not for this.** Trino does not take over the ingest,
  backfill or compaction. Its floor was predicted at 2 to 4 GB; one query ran at 2 GiB, and
  4 GiB carried four concurrent aggregations at 2.86 GiB.
- **The ceiling follows the host and the stated aim.** The owner's decision (2026-11-25): a
  platform that runs as one always-on whole, on a host that gives Docker 28 cores and 49.5 GiB,
  with measured use a small fraction of either. The ceiling is set by the host, not fitted to
  the sum of the day.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| dbt-spark through the Spark Thrift Server | shares the ingest's 3.0 CPU quota and its container; a slow mart becomes a late ingest |
| dbt-duckdb against the REST catalog | in-process per client: no shared server for consumers and nowhere to enforce a rule; this lake's views would be invisible to it |
| ClickHouse as the mart engine | ClickHouse is a derived tier that originates nothing (ADR-025); 24.3 reads Iceberg and does not write it |
| Trino at 1 CPU / 2 GiB | ran every single query in the spike; concurrency was not measured at that size, and dbt runs models in parallel |
| Trino 483 or older | 484 is the release the spike measured; nothing in it failed that an older one would fix |
| Keep 20 CPU / 36 GiB and trim elsewhere | 1.30 CPU and 0.812 GiB would come out of other services' limits, and ADR-034 keeps Airflow's at the plan's figures until real ingests are measured; ADR-031 rejected opt-in profiles for the platform layer |
| Marts in their own Lakekeeper warehouse, `lake` read-only at the connector | closes the table-procedure gap below; costs a second catalog for Spark maintenance, DuckDB and every client. The escape hatch, with its trigger |

---

## Consequences

**Easier:** SQL over every lake table for any client with a Trino driver, at a pinned snapshot
when it matters; consumer marts as reviewed dbt models with tests, outside the ingest; one
rule file that later carries entitlements.

**Harder:** one more JVM always on, 2.0 CPU / 4 GiB declared, about 1.4 GiB idle. Two engines
write the lake, split by namespace. Views are Trino-only. The candle expression cannot be
copied from Spark without the decimal change. A `DROP TABLE` in `marts` deletes the files at
once; a dropped mart is rebuilt by dbt.

**Committed to:** `rules.json` with writes only in `marts`, checked by `make test-trino` and
the CI `trino` job; the 24 CPU / 42 GiB bootstrap ceiling in CI; Trino pinned by hand (Dependabot
tracks Dockerfiles, not compose images); the Spark and dbt boundary above.

**Risks:**

- **Table procedures are not governed.** Trino 484's file-based access control allows
  `ALTER TABLE ... EXECUTE` on every table: `checkCanExecuteTableProcedure` is empty in
  `FileBasedSystemAccessControl` (line 1043) and `FileBasedAccessControl` (line 727).
  `EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` planned with no denial on the
  live lake, while `EXPLAIN INSERT` into the same table was denied. `expire_snapshots` and
  `remove_orphan_files` keep a 7-day floor by default and `add_files` is off; `optimize` and
  `rollback_to_snapshot` are open. A rollback of `raw.messages` would move `main` back while
  the ingest resumes from the newest ingest snapshot on any ref (`docker/lake/offsets.py`),
  so the offsets after it would never be read again. The catalog itself is unauthenticated
  (ADR-028 section 8), so this is a guard-rail gap, not a new hole: anything on the host can
  do the same over Lakekeeper's API.
- **Concurrency beyond four queries is unmeasured**, and so is a real dbt run: dbt arrives in
  P3's second pull request.
- **The decimal rule may not be the only one.** Division and multiplication scales differ
  between Trino and Spark; every arithmetic step in a ported model casts explicitly.

**Revisit when:**

- a Trino query fails with `EXCEEDED_LOCAL_MEMORY_LIMIT` or the container is OOM-killed, or
  the `dbt_marts` run takes longer than its schedule interval: measure, then raise inside
  the 2.70 CPU / 5.188 GiB of headroom;
- a Trino release governs `checkCanExecuteTableProcedure` in file-based access control, or
  catalog-level authorization lands (ADR-039's target): close the gap there; if neither has
  happened when P5 enforces entitlements, take the separate-warehouse escape hatch;
- a second person gets access to the host: authentication on Trino before anything else;
- the default bootstrap peak would pass 22.8 CPU or 39.9 GiB (95% of the ceiling);
- Trino 485 is released: upgrade by hand after re-running the checks in
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
  system and file-based access control; `lib/trino-plugin-toolkit` and
  `core/trino-main/.../StatementAnalyzer.java` at tag 484

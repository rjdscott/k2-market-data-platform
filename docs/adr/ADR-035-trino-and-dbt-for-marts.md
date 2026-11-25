# ADR-035: Trino and dbt for marts; the Spark and dbt boundary

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Query engine

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

What Trino does on this lake was measured before deciding, with Trino 484 in throwaway
containers on the running stack (commands and results in
[`docker/trino/README.md`](../../docker/trino/README.md), "Verified 2026-11-25"):

| Question | Measured | Command |
|---|---|---|
| Version | `trinodb/trino:484`, released 15 Oct 2026, the newest that day; Temurin 25 | Docker Hub tags, trino.io release notes |
| Catalog through Lakekeeper 0.13.9, static keys | namespaces and tables listed; no vended credentials (the warehouse has `sts-enabled: false`) | `trino --execute 'SHOW SCHEMAS FROM lake'` |
| Types | `timestamp(6) with time zone`, `decimal(38,10)`, `decimal(28,10)`, `map(varchar, varbinary)`, `array(row(px decimal(28,10), qty decimal(28,10)))` | `SELECT typeof(...)` and `DESCRIBE` on gold, silver, raw |
| Time travel | works, also through a read-only catalog | `SELECT count(*) FROM lake.gold.ohlcv_1m FOR VERSION AS OF <id from "ohlcv_1m$refs">` |
| Writes | CTAS, INSERT, views and `DROP` commit through Lakekeeper; metadata written gzipped (`*.gz.metadata.json`), which DuckDB 1.4.4 and Spark 3.5.5 both read | a scratch namespace, since dropped |
| Iceberg REST view | created and read back, stored with dialect `trino`; DuckDB 1.4.4 does not list it (`Table with name ... does not exist!`); Spark 3.5.5 reads one only when its SQL also parses as Spark SQL (`to_iso8601` failed with `Cannot load function`) | `CREATE VIEW` in Trino, then a read from each engine |
| Snapshot summary | `engine-name: trino`, `engine-version: 484`, `trino_query_id`, `trino_user`, the standard totals; no `k2.*` key, so `offsets.latest_summary` skips it | the table's `snapshots` over REST |
| Session time zone | the client's zone; a client that sends none gets the JVM's, pinned by `-Duser.timezone=UTC` even with a non-UTC zone (for example `TZ=America/New_York`) set in the container | `SELECT current_timezone()` from the CLI and from a raw HTTP request |
| Decimal arithmetic | `DECIMAL(38,10) * DECIMAL(38,10)` is typed `decimal(38,6)` and fails with `Decimal overflow` once the exact product passes about 1e18, so `gold.candles()`'s `quote_volume` expression ported verbatim fails on `gold.trades` | `SELECT CAST(10000000000 AS DECIMAL(38,10)) * CAST(10000000000 AS DECIMAL(38,10))` |
| 1 CPU / 2 GiB, heap 1280M | the `gold.candles()` aggregation over all of `gold.trades` in its integer-product form (`DECIMAL(38,0)` product, not the verbatim expression), as a CTAS into the spike's scratch namespace, since dropped: 21.3 s, peak 1.41 GiB, no OOM | `docker stats` sampled during the query |
| 2 CPU / 4 GiB, heap 2816M | idle 1.42 GiB; the candle aggregation (open, high, low, close, volume, count) over all of `gold.trades`, read only: 6.6 s alone, four at once 12.5 s each at most, peak 2.86 GiB and 2.15 cores, no OOM | `system.runtime.queries`, `docker stats` |

Where Trino may write, and how that is fenced, is [ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md).
The budget Trino fits in is [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md).

---

## Decision

**We will run one Trino 484 node beside Spark as the SQL engine for consumers and the engine
dbt builds set-based marts on, while Spark keeps every job that writes the lake, because
consumer marts need a shared engine that reads this lake through its REST catalog without
sharing the ingest's container, and the jobs that need Kafka offsets, the writer lock or code
per record already have one.** Scope: SQL serving and set-based marts. Spark's jobs, ADR-022's
exactly-once ingest and ClickHouse's hot tier do not change.

**The boundary.**

| Work | Where | Why there |
|---|---|---|
| Redpanda to raw, bronze, silver, gold; backfill; rebuild; compaction, expiry, audits | Spark (`docker/lake/`), unchanged | needs Kafka offsets, the writer lock, per-record decoding, or `maintenance.py` |
| Consumer marts: one SQL statement per model over lake tables and other marts | dbt on Trino, in the owning domain's warehouse (ADR-044) | set-based, versioned with tests, no lock |
| Ad hoc SQL over every lake table and every mart | Trino, the front door for marts | one engine and one rule file for every client |
| The hot tier | ClickHouse, unchanged | ADR-025 |

A model that needs an offset, the lock or code per record is a Spark job. A model that is a
`SELECT` over lake tables or marts is a dbt model. Spark does not write the marts' rows;
Trino writes nothing else. Who compacts and expires mart tables is decided in the next pull
request of P3, between Spark's `maintenance.py` reading the domain warehouses and a scheduled
platform job through Trino; today nothing does, and nothing needs to, because no mart exists.

**Trino is the front door for marts.** Consumers read marts through Trino, where the fences of
ADR-044 are; an engine that attaches a domain warehouse directly holds the storage keys and
bypasses them.

**The engine, as configured** (`docker/trino/etc/`): one node, coordinator and worker in one
JVM, 2.0 CPU / 4 GiB with a 2816M heap (1.25 GiB of the limit for metaspace, code cache,
thread stacks and direct buffers: one query fits in half of it, the rest is for dbt threads);
the JVM in UTC, and every client sets UTC itself, because the session zone is the client's;
native S3 with the static MinIO keys from the environment, path-style, region `local-01`;
the image's 2GB filesystem cache and sample catalogs left out.

**The delayed product is a view.** A view over `lake.gold` with a 20-minute predicate is
always current with no schedule. Consumers read marts through Trino, so a view
is enough: DuckDB does not see Iceberg views, and Spark expands one only when its SQL is also
Spark SQL. A consumer who needs the rows in another engine reads the gold table its rights
allow, or a scheduled table if one is ever declared.

**`quote_volume` joins the `ohlcv_1m` parity only as Trino computes it exactly.** The
verbatim port overflows. The candidate is a `DECIMAL(38,0)` product of the two `_e8`
integers, divided by 1e16, which Trino types `decimal(38,6)`. P3's second pull request
compares `typeof` on both engines and leaves the column out, and says so, if they differ.

**Parity for marts, and what is lifted.** A mart is covered by dbt tests on its grain and
columns, unit tests on its logic, and a reference computation where one is cheap, not by
three engines: ADR-029 clause 3 is scoped that way for marts and stands for gold. dbt's
`ohlcv_1m`, the worked example, keeps the full check against Spark's `gold.ohlcv_1m` at a
pinned snapshot. ADR-028 section 6 is lifted for SQL serving only; an HTTP API stays a
non-goal.

---

## Rationale

- **It was measured on this catalog first.** Every property, type and failure above came from
  the running lake, not from documentation; the decimal overflow would have surfaced as a
  failed parity run otherwise.
- **A server, not a library.** Entitlements are enforced on a server (P5); DuckDB in-process
  has no place to hold a rule between a consumer and the lake.
- **Out of the ingest's container.** dbt on Spark would run through the Thrift Server inside
  `spark-iceberg`, on the 3.0 CPU quota the ingest already throttles on. Trino has its own
  quota and heap, so a mart build cannot slow the system of record.
- **ADR-006's objection holds for ETL and not for this.** Trino takes over no ETL. Its floor was
  predicted at 2 to 4 GB; one query ran at 2 GiB, and 4 GiB carried four concurrent
  aggregations at 2.86 GiB.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| dbt-spark through the Spark Thrift Server | shares the ingest's 3.0 CPU quota and its container; a slow mart becomes a late ingest |
| dbt-duckdb against the REST catalog | in-process per client: no shared server for consumers and nowhere to enforce a rule; Iceberg views invisible to it |
| ClickHouse as the mart engine | a derived tier that originates nothing (ADR-025); 24.3 reads Iceberg and does not write it. Trino over ClickHouse is decided after the first marts |
| Trino at 1 CPU / 2 GiB | ran every single query in the spike; dbt runs models in parallel, and concurrency was measured only at 2 CPU / 4 GiB |
| Trino 483 or older | 484 is the release measured; nothing in it failed that an older one would fix |

---

## Consequences

**Easier:** SQL over every lake table and mart for any client with a Trino driver, at a pinned
snapshot when it matters; consumer marts as reviewed dbt models with tests, outside the ingest.

**Harder:** one more JVM always on, 2.0 CPU / 4 GiB declared, about 1.4 GiB idle. Two engines
write Iceberg, split by warehouse (ADR-044). Views are Trino-only. The candle expression cannot
be copied from Spark without the decimal change. A `DROP TABLE` through Trino is a purge drop.

**Committed to:** Trino pinned by hand (Dependabot tracks Dockerfiles, not compose images);
`make test-trino` in CI; the boundary above.

**Risks:** concurrency beyond four queries is unmeasured, and so is a real dbt run (P3, next
pull request). Division and multiplication scales differ between Trino and Spark beyond the one
case found; every arithmetic step in a ported model casts explicitly.

**Revisit when:** a Trino query fails with `EXCEEDED_LOCAL_MEMORY_LIMIT` or the container is
OOM-killed, or the `dbt_marts` run takes longer than its schedule interval (measure, then raise
inside ADR-045's headroom); Trino 485 is released (upgrade by hand after `make test-trino` and
the checks in `docker/trino/README.md`).

---

## References

- Plan: [phase P3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md) of
  the [governed self-serve platform](../plans/2026-11-24-governed-self-serve-platform/README.md)
- Umbrella: [ADR-031](ADR-031-governed-self-serve-platform.md). Departs from
  [ADR-006](ADR-006-spark-batch-only.md); lifts [ADR-028](ADR-028-non-goals-and-honest-limits.md)
  section 6 for SQL serving; scopes [ADR-029](ADR-029-research-production-parity-contract.md)
  section 3 for marts. Each carries an Outcome dated 2026-11-25.
- Layout and fences: [ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md). Ceiling:
  [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md).
- Code: `docker/trino/`, `scripts/trino-rules-test.sh`

---

## Outcome (2026-11-26)

**The revisit trigger fired, on a test rather than a model.** The uniqueness test k2gen
generates on `gold.trades`'s identifier fields, `(exchange, canonical_symbol, trade_id)`, failed
over the whole table with `Query exceeded per-node memory limit of 844.80MB`, Trino's default
per-node query memory at the 2816M heap, and failed the same way over the newest 2 days; over the
newest 6 hours it passed in 2.6 s (k2-trino, 2026-11-25; the command, with the window varied, is in
[`dbt/README.md`](../../dbt/README.md)). No memory was raised. The source test was scoped to the
newest 6 hours by the table's time partition (`_RECENT` in `platform/k2gen/k2gen/generate.py`),
and the whole table stays the nightly Spark audit's, `duplicate_identifiers` in
`docker/lake/maintenance.py`, which checked it before Trino existed. The marts did not hit the
limit: the 1-minute candles and the venue scorecard each aggregate all of `gold.trades` on every
build, and both passed on the live stack (`dbt build --selector market_analytics`, 2026-11-25).

**Two things landed differently from the Decision.**

- The delayed product is a view over the 1-minute mart, `market_analytics.candles.ohlcv_1m`,
  not over `lake.gold`, in its own product and schema
  (`market_analytics.delayed_candles.ohlcv_delayed_20m`). It is as current as that mart's last
  hourly build, not as the lake: always at least 20 minutes old, and at most the build's age
  older. It shows a candle once its whole minute ended 20 minutes before the query.
- Who maintains mart tables, left open above, is the domain's own writer through Trino:
  `dbt_marts` runs `expire_snapshots` and then `remove_orphan_files` at 7 days as `dbt_<domain>`,
  once a day (`dbt/macros/expire_mart_snapshots.sql`). Spark's `maintenance.py` is not taught the
  domain warehouses. The 7-day floor holds because the session properties that lower it are
  denied to everyone ([ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md)); an
  expiry at `1d` as the writer failed with `Retention specified (1.00d) is shorter than the
  minimum retention configured in the system (7.00d)` (scratch table, 2026-11-25).

**The memory limit was raised from 4 GiB to 5 GiB**, the heap unchanged at 2816M. After the
builds the container's anonymous memory was 4,072,116,224 bytes against the 4,294,967,296-byte
limit (`docker exec k2-trino cat /sys/fs/cgroup/memory.stat`, the `anon` line), about 0.2 GiB
of room; the JVM held 3.82 GiB resident (`docker exec k2-trino cat /proc/1/status`, `VmRSS`),
`memory.peak` was 3.95 GiB and `oom_kill` 0 (`docker exec k2-trino cat
/sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events`), all 2026-11-25. That is the heap
committed to its 2816M maximum and about 1.07 GiB beside it, inside the 1.25 GiB the Decision
left for that but close to it, and `jvm.config` does not cap direct memory; the 1.42 GiB idle
figure in the Context is a fresh JVM's. An hourly, unattended DAG should not wait for an
out-of-memory kill to find out, so the limit moved first: 2.25 GiB now sits beside the heap
([ADR-010](ADR-010-resource-budget.md) Outcome; the gated bootstrap peak goes to 21.30 CPU /
37.812 GiB of 25 / 48, `python3 scripts/check-budget.py`). A whole `dbt_marts` run took 31 to
40 s in a throwaway Airflow container against this Trino (`airflow dags list-runs dbt_marts`,
start to end, 2026-11-25).

**Revisit when** the `anon` line of `docker exec k2-trino cat /sys/fs/cgroup/memory.stat`
passes 4.5 GiB (4,831,838,208 bytes) or the `oom_kill` count in `memory.events` is above 0;
or a mart model, not a test, fails with `EXCEEDED_LOCAL_MEMORY_LIMIT`; or a `dbt_marts` run is
ended by its 50-minute `dagrun_timeout` (`docker/airflow/dags/dbt_marts.py`). Then measure,
and cap direct memory in `jvm.config`, or raise the limit or `query.max-memory-per-node`
inside ADR-045's headroom, 3.70 CPU and 10.188 GiB at the gated peak after this change.

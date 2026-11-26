# 03. Data engineering concepts

> **You will learn** the ideas the lake and the serving tier rest on: exactly-once, idempotency, snapshots, layers, lineage, evolution, partitioning, compaction, batch versus streaming, audits, backpressure, rebuildability.
> **Read this if** you are new to lakehouses or streaming ingestion, or a term in chapters 08 to 10 is unfamiliar.
> **Before this** chapter 01; chapter 02 helps.

Same shape as chapter 02: the problem, the usual options, what K2 chose and why, where to
see it.

**Retired, 2026-11-26.** The v3 lake ingest is retired ([ADR-056](../adr/ADR-056-unified-data-product-platform.md),
phase 000 of the [platform plan](../plans/2026-11-26-unified-product-platform/README.md)): its DAGs, its raw,
bronze, silver, gold and audit tables and its dbt marts were backed up and dropped, and its code, tests and
chapters 08 and 09 are archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). No live crypto
data lands in the lake until phase 004, and ClickHouse is paused
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). The concepts stand; each
**K2.** paragraph below says, in the past tense, how the retired pipeline applied one, and
[chapter 20](20-target-data-product-platform.md) is the target that applies them next.

## Exactly-once

**Problem.** Moving records from a log (Kafka) into a store must not duplicate them or skip
them when a run dies mid-way. The failure is always the same: the data write and the
"where I got to" write are two separate operations, and a crash lands between them.

**Options.** At-least-once with downstream dedup (works if every consumer dedups; most do
not). A checkpoint store beside the data (a table, a file, a ZooKeeper node) with careful
ordering. Put the position inside the same atomic commit as the data.

**K2.** The consumed Kafka offsets were properties on the Iceberg snapshot that held the
rows. One metadata swap committed both or neither; the next run read its start from the last
snapshot. There was no second store to disagree. `docker/lake/offsets.py`, which does this, is
kept for the target's Kafka connector. See [08](08-lake-ingest.md#stage-1-offsets-in-the-snapshot),
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md).

## Idempotency

**Problem.** Anything that can be retried will be. An operation is idempotent if doing it
twice leaves the same state as doing it once; without that, every retry, replay and
overlapping reload is a correctness bug.

**Options.** Application-side "have I seen this" checks (state to lose). Unique constraints
and upserts (fine in an OLTP database, expensive in a column store). Make the storage
engine's merge rule the dedup rule.

**K2.** The retired lake committed by snapshot id, so re-running a stage added nothing.
ClickHouse tables (paused) are `ReplacingMergeTree` keyed on the logical event with a version
that makes the earliest delivery win; reload from the lake over the topic head collapsed to one
row under `FINAL`. `make lake-verify` proved a second run added zero rows; it now prints a SKIP
naming phase 004. See [10](10-clickhouse-gold.md).

## Iceberg snapshots

**Problem.** A table on object storage is a pile of Parquet files. Readers need a consistent
view while writers add files; writers need atomic commits and a way to say "what changed
since X".

**Options.** Directory listings (no atomicity, no history). Hive-style partitions plus a
metastore (atomic per partition at best). A table format with a metadata tree: every
commit is a new immutable snapshot pointing at the files it contains.

**K2.** Apache Iceberg on a Lakekeeper REST catalog. In the retired ingest every ingest,
compaction and expiry was a snapshot; each layer read its parent "from snapshot A to snapshot B"
and recorded B in its own commit; maintenance snapshots were skipped by a property K2 set
(`k2.job`), not by guessing at Iceberg's operation field. Snapshots also pinned parity: the
three-way OHLCV check named a snapshot id, never `latest`. See [08](08-lake-ingest.md), [ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md).

## Medallion layers

**Problem.** One table cannot serve "what arrived", "what the venue said", "what it means"
and "what do I join against" at the same time: each needs a different schema, a different
identifier and a different tolerance for duplicates.

**Options.** One normalised table (fast to build; fidelity lost at the first transform).
Three layers with normalisation at bronze (the usual pattern; venue vocabulary is gone
before it can be inspected). Four layers: verbatim, per-venue vendor schema, per-venue
typed and flagged, canonical.

**K2.** The retired lake was raw / bronze-per-venue / silver-per-venue / gold-canonical, each
derived only from the one above, the boundary where the question changes. The target keeps
only the layers that add a guarantee, per product ([20](20-target-data-product-platform.md)). See [09](09-lake-layers.md),
[ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md).

## Lineage and identifiers

**Problem.** Every row should be traceable to the bytes it came from, and every table needs
a key that is actually unique in the data, not merely declared so.

**Options.** Trust the venue's id as unique everywhere (it is not: replays). Synthesise ids
(collide, as v2's Kraken ids did). Carry a pointer one layer up and make uniqueness a
property of exactly one layer.

**K2.** In the retired lake every derived row carried `src_topic / src_partition / src_offset`
(or the equivalent) to its parent, and `(conn_id, conn_msg_seq)` was the archive-wide frame key.
Below gold the only honest identifier was lineage; gold made `(exchange, canonical_symbol,
trade_id)` unique and the nightly audit proved it. See [09](09-lake-layers.md).

## Schema evolution

**Problem.** Contracts change. A field added in one place and not another fails silently at
the boundary between them, usually at 3 a.m., not at build time.

**Options.** Free-form JSON (no contract to break, nothing to trust). Break-and-migrate.
Additive-only changes checked by a registry and moved everywhere in one change.

**K2.** Avro with `BACKWARD_TRANSITIVE` compatibility on the registry; in the retired lake,
add-nullable-only at every layer and the raw table frozen. A schema change moves the Avro file,
ClickHouse DDL, docs and `tests/test_wire_format.py` in one PR, via the `/schema-change` skill;
until the retirement it moved the lake DDL and the layer projections too. Bronze kept the venue's
own keys, and a new undeclared key failed the drift audit rather than being dropped. See [07](07-wire-contracts.md), [13](13-schema-design.md).

## Partitioning and pruning

**Problem.** A query that reads every file is slow no matter how good the engine is. Layout
must let the engine skip files by the columns queries filter on, without creating so many
partitions that metadata dominates.

**Options.** No partitions (scan everything). Partition by every filter column (millions of
tiny files). Partition by time at day granularity plus the one dimension queries always
name, and rely on per-file column statistics for the rest.

**K2.** In the retired lake: raw by `days(kafka_ts), topic`; every bronze table and the silver books by
`days(recv_ts)`; silver trades by `days(exchange_ts)`; gold trades by
`exchange, days(exchange_ts)`, gold books by `exchange, days(second)`, candles by
`exchange, months(window_start)` (`ohlcv_1d` by `exchange` alone). Column metrics were off by default and switched on only for
the columns range scans used, so manifests stayed small. ClickHouse keys mirror the query
shape: `(exchange, canonical_symbol, exchange_ts, trade_id)`. See [14](14-partitioning-strategy.md).

## Files and compaction

**Problem.** Frequent small commits produce many small files; each costs an open, a
footer read and a manifest entry, and query time grows with file count rather than bytes.

**Options.** Write less often (latency). Write once, never touch (files stay small
forever). Commit small, rewrite later into target-sized files behind a lock.

**K2.** The retired ingest committed every 5 minutes; nightly maintenance binpacked raw to
256 MB and sort-rewrote the last two days of every derived table to 128 MB, expired old snapshots
and removed orphans, all behind the same writer lock the ingest took. See [08](08-lake-ingest.md),
[14](14-partitioning-strategy.md).

## Batch vs streaming

**Problem.** Freshness costs money and complexity. A streaming writer needs its own state,
checkpoints and always-on compute; a batch writer needs a way to know where it stopped.

**Options.** Structured Streaming into the lake (v1: five always-on jobs, most of the
host). Micro-batch with an external checkpoint. Scheduled batch that reads the log by
explicit offset range and stores its position with the data.

**K2.** The retired lake was batch every 5 minutes; freshness for dashboards came from
ClickHouse reading the same topics directly, until its pause. Two consumers, one contract, no
stream processor.
See [08](08-lake-ingest.md), [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md).

## Audits as tests

**Problem.** A pipeline that is "working" because nothing crashed can still be wrong.
Correctness needs assertions that run against the data, fail loudly, and leave a record.

**Options.** Manual spot checks. Dashboards someone might look at. Scheduled assertions
whose exit code is the product and whose results are a table.

**K2.** The retired `maintenance.py` ran offset continuity, per-layer parity, duplicate
identifiers, schema drift, checksum pass rates and product parity every night; any failure
exited non-zero and fired `LakeAuditFailed`; every check, pass or fail, was a row in the lake's
audit table. All three retired with the ingest.
See [08](08-lake-ingest.md#proof), [11](11-observability.md).

## Backpressure and loss

**Problem.** When a downstream stalls, an upstream must either block, buffer, or drop.
Blocking a socket reader loses the socket; unbounded buffering loses the process; dropping
loses data. The only wrong answer is to lose data without knowing.

**Options.** Block. Buffer to disk (a second store to reconcile). Bound the buffer, drop
past it, count every drop, and prove the hole later.

**K2.** Capture has one bounded buffer (librdkafka, 32 MiB) and drops past it with a
counter per reason; Redpanda retention is the buffer below that; the retired lake's
offset-continuity audit was the proof of what was lost, and the target's Kafka connector audits
continuity on its landing ([20](20-target-data-product-platform.md)). See [05](05-capture.md), [16](16-failure-modes.md).

## Rebuildability

**Problem.** Bugs in transforms are certain. If the only copy of a product is the product
itself, a bug is permanent.

**Options.** Back up the products. Keep the inputs and make every product a pure function
of them, with a command to recompute.

**K2.** The retired raw table was never expired, in the sense `maintenance.py` made literal:
nothing there deleted a row, `expire_snapshots` dropped only metadata and files a rewrite already
superseded, so every raw frame in the current snapshot stayed. `rebuild.py --layer` recomputed any
lake layer from its parent, and ClickHouse was reloaded from lake gold by one runbook; both are
archived. Everything except raw was disposable by design; when the ingest retired, raw went
too, after a backup, a cost phase 000 of the plan states and accepts for a lab. See [08](08-lake-ingest.md),
[10](10-clickhouse-gold.md), [12](12-data-strategy.md).

## Key points

- Put the position in the same commit as the data; then exactly-once is a property, not a
  protocol.
- Make dedup a storage rule (snapshot ids, merge-tree keys) so retries are free.
- Layers exist where the question changes; uniqueness is true at exactly one of them.
- Partition by time plus one dimension; compact behind a lock; keep metrics only where
  scans use them.
- Assert against the data every night and keep the answer; count every drop; keep raw
  forever so everything else can be thrown away.

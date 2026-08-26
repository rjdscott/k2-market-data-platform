# ADR-018: A lake-first v3 with a Rust capture tier

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Architecture (umbrella)

---

## Context

v2 works: three exchanges, ~150 msg/s, p99 trade → Silver 170–197 ms, 15.1 CPU /
21.875 GB against a 16 CPU / 40 GB budget
([`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md)).
It is a good streaming demo. It is not yet a platform a quant can do research on,
and an audit of the code — not the docs — says why:

- **The lake is a lossy copy of the serving DB, not the system of record.** The
  offload reads ClickHouse over JDBC (`docker/offload/offload_generic.py:172`)
  and appends to Iceberg. Everything downstream inherits ClickHouse's
  normalisation, its 7-day Bronze TTL, and the JDBC driver's type limitations —
  `silver_trades`' `Array`/`Map` columns are already dropped at that boundary.
  Nothing durably holds what the exchange actually sent.
- **OHLCV open/high/low/close are arbitrary.** The Gold tables are
  `SummingMergeTree((volume, quote_volume, trade_count))`
  (`docker/clickhouse/ddl/01-k2-schema.sql:178`). The MV's `argMin`/`argMax`
  resolve open and close *within one insert block*; when a merge collapses two
  blocks for the same window, the non-summed columns are picked arbitrarily. A
  candle spanning a block boundary can carry a close that never traded last.
  This is a correctness bug, not a rounding difference.
- **Bronze cannot survive a replay.** All three Bronze tables are plain
  `MergeTree()` (`docker/clickhouse/ddl/01-k2-schema.sql:88`). Re-consuming a
  Redpanda topic duplicates every row; there is no key, no version, no dedup.
- **No receive timestamp.** The only wall clock on the trade path is taken
  *after* JSON parse and normalisation
  (`services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt:28`);
  Kraken alone stamps anything, and it does so at raw-publish time
  (`.../KrakenWebSocketClient.kt:229`). Exchange-clock skew and platform latency
  are therefore not separable in any stored row.
- **Kraken is on WS v1 with synthesised, colliding trade IDs.** The endpoint is
  `wss://ws.kraken.com` (`services/feed-handler-kotlin/src/main/resources/application.conf:42`)
  and the ID is `"KRAKEN-${timestampMs}-${pair.hashCode()}"`
  (`.../TradeNormalizer.kt:60`) — two trades in the same millisecond on the same
  pair are indistinguishable. v2 (`wss://ws.kraken.com/v2`) carries a real
  `trade_id` and a CRC32 book checksum.
- **Coinbase sequencing is read and thrown away.** `sequence_num` is parsed
  (`.../CoinbaseWebSocketClient.kt:178`) and copied into the payload, but never
  compared to the previous value. A dropped message is silent.
- **The Avro contract is broken and unused.** `logicalType` sits as a sibling of
  `type` in `schemas/avro/normalized-trade.avsc:60`, where Avro ignores it, and
  price/quantity are `string`. ClickHouse never reads the Avro topic anyway —
  every Kafka engine table is `kafka_format = 'JSONAsString'` over `.raw`
  (`docker/clickhouse/ddl/01-k2-schema.sql:39`).
- **Trades only, no book, and two of three raw streams are single-partition.**
  There is no L2 product at all, and the raw producer keys by exchange name
  (`.../KafkaProducerService.kt:155`), pinning Kraken and Coinbase to one
  partition each.
- **The catalog is a bind-mounted directory.** `spark.sql.catalog.k2.type =
  "hadoop"` over `/home/iceberg/warehouse`
  (`docker/offload/create_bronze_table_sql.py:10-11`) — no catalog service, no
  concurrent writers, MinIO provisioned but unused by the offload (ADR-007
  Outcome, ADR-013).

The constraint has not changed: one host, 16 CPU / 40 GB (ADR-010). The target
audience has: this is a **quantitative-research** platform on public WebSocket
feeds over the open internet. It is explicitly not a trading path, and no
decision below should be read as latency engineering.

---

## Decision

**We will rebuild K2 as a lake-first platform with a Rust capture tier —
Iceberg on MinIO behind a Lakekeeper REST catalog becomes the system of record,
ClickHouse becomes a derived and rebuildable hot tier, and a single
`k2-capture` binary per exchange replaces the Kotlin feed handlers — because
research needs a durable, verbatim, correctly-sequenced archive, and v2's system
of record is a JDBC copy of a serving database.**

This ADR is the umbrella. It fixes the shape; ADR-019 through ADR-028 fix the
details, each landing with its phase.

Concretely, v3 commits to:

1. **Lake-first.** Spark batch reads Redpanda by offset range and writes
   `raw.messages` (payload verbatim, never expired) then `bronze.*`. Exactly-once
   comes from storing the consumed offsets in the Iceberg snapshot summary
   (`snapshot-property.k2.kafka-offsets`), so the commit and the offsets move
   atomically. The PostgreSQL watermark table goes away.
2. **One Rust binary per exchange.** `k2-capture` handles trades *and* L2 top-20
   book on one connection: `recv_ts_ns` taken as the first statement on frame
   receipt (before parse), per-exchange sequencing and gap counters, Kraken v2
   CRC32 checksum verification with resync on mismatch, top-20 snapshots at 1 Hz
   as the canonical L2 product.
3. **ClickHouse as a derived hot tier.** `ReplacingMergeTree` on trades and
   book snapshots with a 7-day TTL; OHLCV computed on read over `FINAL`, not
   materialised into a `SummingMergeTree`. Losing the whole ClickHouse volume
   costs a rebuild from the lake, not data.
4. **DuckDB + PyIceberg notebooks** as the query layer. No query service —
   ADR-005 stays deferred, and now has a better answer than "not yet".
5. **16 CPU / 40 GB, single host, retained.** ADR-010's budget is a stated
   constraint of the project, not an accident of what fit.

---

## Rationale

The three things a quant asks of a market data platform are: *is it complete*,
*is it correct*, and *can I reproduce a number from six months ago*. v2 cannot
answer any of them with evidence. It has no gap detection, one confirmed
aggregation bug, and an archive whose contents depend on what a JDBC driver
could carry that day.

Lake-first answers the third directly: if the raw bytes are on disk with their
offsets, every derived table is a function of the archive and can be rebuilt and
diffed. That is also what makes the OHLCV bug *fixable* rather than *patchable* —
candles become a view over deduplicated trades, so the aggregation is defined by
a query anyone can read, and a CI test over two insert blocks catches the
regression class that produced the original bug.

Rust for capture is not about latency; at 150 msg/s over the public internet the
capture tier is not the bottleneck and never will be. It is about (a) taking the
receive timestamp before the parser touches the frame, which is a discipline the
current code cannot retrofit without the same rewrite, (b) doing trades and book
on one connection per exchange with a full-depth book in memory, and (c) three
containers at ~40 MB instead of three JVMs. One language for capture keeps the
book, checksum and sequencing logic in one place rather than duplicated per
exchange per runtime.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep v2, patch the gaps in place** (ReplacingMergeTree Bronze, OHLCV view, add recv_ts to Kotlin, Kraken v2 adapter) | Fixes the symptoms and leaves the disease: the system of record is still a lossy JDBC copy of a serving database with a 7-day TTL, so nothing is reproducible and nothing is rebuildable. Cheaper now, and every later fix pays the same tax again. |
| **Streaming writes to the lake** (Flink, or Spark Structured Streaming into Iceberg) | Costs a resident streaming runtime against a binding CPU budget — ADR-004 deleted exactly this to buy 13.5 CPU back. Batch every 5 minutes is inside the freshness a research platform needs, and the offset-in-snapshot commit gives exactly-once without a checkpoint store. |
| **Kotlin for the L2 book tier**, keeping the existing handlers for trades | Two languages in the capture tier, book/sequencing/checksum logic split across both, and three more JVMs (~3× the footprint of the Rust target) on a host where CPU is the binding constraint. The receive-timestamp-before-parse requirement forces a rewrite of the frame path either way. |
| **Full-depth order books in the lake** (every delta, reconstructable to any depth) | Storage and rebuild cost far beyond a single host, for depth beyond 20 levels that no planned research uses. `raw.messages` already holds every delta verbatim, so full depth is recoverable by replay if it is ever needed; top-20 snapshots at 1 Hz are the queryable product. |

---

## Consequences

**Easier:** reproducing any historical number (raw bytes + offsets are on disk);
rebuilding ClickHouse from scratch; proving completeness (gap counters, CRC32
pass rates, offset-continuity audits); adding an exchange (one Rust adapter, one
`handle_frame`); answering "how stale is this candle" honestly.

**Harder:** the capture tier is now Rust — a language the rest of the repo does
not use, with a build the CI has to learn. The lake gains a catalog service to
operate (Lakekeeper + its Postgres DB) where a bind mount needed nothing.
Debugging moves from "read the JSON in Redpanda Console" to "decode Avro by
schema id".

**Committed to:** a ~2-week Rust rewrite (Phase C, the long pole); a parallel-run
period where Rust capture and Kotlin handlers both produce and are compared per
symbol over 24 h before cutover; retiring `services/feed-handler-kotlin/` to
`legacy/v2-kotlin/` once parity holds; one wire format (Avro + registry,
fixed-point `int64` at 1e-8) across every topic. When this ADR is accepted,
ADR-002, ADR-007, ADR-009, ADR-013 and ADR-014 are superseded by the follow-on
ADRs below — the v2 reasoning stays on the record unedited.

**Risks:** Lakekeeper ↔ Iceberg client version compatibility, ClickHouse 24.3's
`AvroConfluent` handling of arrays and Kafka virtual columns, Coinbase's
unverified WS rate limits, and `icebergS3()` on 24.3 for the rebuild path. Each
is a verify-first spike in Phase B of the plan, with a named fallback; none is
allowed to start Phase C unanswered. Two unchanged non-risks worth stating: no
HA (still one broker, one ClickHouse, one host), and Prefect + Spark are
retained rather than replaced.

**Revisit when:** the Phase C 24-hour burn-in numbers are published in
`docs/benchmarks/` — if gaps are non-zero and unexplained, or Kraken checksum
pass rate is below 100 %, or the three capture containers exceed 1.5 CPU
combined, the capture design is wrong and this ADR gets an Outcome section
before Phase D starts.

### Follow-on ADRs

To be written when each phase lands, not before:

| ADR | Title | Supersedes |
|-----|-------|------------|
| 019 | Rust capture tier replaces Kotlin feed handlers | ADR-002 |
| 020 | Avro-only contracts: fixed-point int64 @1e-8, recv_ts in body | — |
| 021 | Raw-first archive with per-record lineage | — |
| 022 | Exactly-once ingest via Kafka offsets in the Iceberg snapshot summary | — |
| 023 | Lakekeeper REST catalog on MinIO | ADR-013 |
| 024 | Unified bronze tables in the lake | ADR-011 (lake only) |
| 025 | ClickHouse as a derived, rebuildable hot tier | ADR-009 |
| 026 | OHLCV computed on read + the ReplacingMergeTree dedup contract | — |
| 027 | L2 book snapshot model and per-exchange resync policy | — |
| 028 | Non-goals and honest limits of a single-host research platform | — |

---

## References

- [`../plans/2026-08-26-v3-quant-research-platform/`](../plans/2026-08-26-v3-quant-research-platform/README.md) — phases, exit criteria, verify-first spikes
- [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md) — the v2 numbers this argues against
- [ADR-004](ADR-004-eliminate-spark-streaming.md) — why a resident streaming runtime is not affordable here
- [ADR-010](ADR-010-resource-budget.md) — the 16 CPU / 40 GB constraint v3 keeps
- [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) — why the Hadoop catalog was chosen, and what it cost

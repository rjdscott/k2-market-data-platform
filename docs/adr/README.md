# Architectural Decision Records

Twenty-eight ADRs covering the design and rebuild of the K2 Market Data Platform: exchange capture → Redpanda → ClickHouse (medallion via materialized views) and an Iceberg lake on a Lakekeeper REST catalog over MinIO, fed by a Spark batch ingest orchestrated by Prefect. The v2 shape those ADRs describe — a Hadoop catalog on a bind-mounted warehouse, fed by a JDBC offload out of ClickHouse — was deleted in Phase D; ADR-014 and ADR-017 carry the Outcome sections that record it. ADR-001 to ADR-010 were written up front in February 2026, before any of it was built; ADR-011 to ADR-017 came out of implementation. The status column below records what actually happened — including the one decision that was reversed (ADR-008) and the one never built (ADR-005). Each ADR that deviated from its own design carries an `Outcome` section at the end explaining why. ADR-018 opens the v3 series: an umbrella decision, still Proposed, arguing that the v2 shape is wrong for research work; ADR-019, ADR-020 and ADR-027 landed with Phase C, and ADR-021 to ADR-025 with Phase D's lake tier. ADR-019 is Accepted and its Outcome records the Kotlin retirement — the capture tier is Rust only, and the v2 `k2.*` medallion is frozen until the Phase E cutover drops it. Where a v3 ADR supersedes a v2 one, the v2 body is left exactly as written and only its status line changes — the [supersession chain](#supersession-chain) below is the map.

Measured as-built at the v2 baseline: **15.1 CPU / 21.875 GB** across 14 long-running services (+2 one-shot), against a 16 CPU / 40 GB budget. With the capture tier swapped for v3's Rust one, Lakekeeper and the `lake-metrics` exporter added, and the v2 offload deleted, the declared steady state is 14.60 CPU / 21.625 GiB across 15 long-running services (+4 one-shot, bootstrap peak 16.10 CPU / 23.125 GiB across 19) ([ADR-010](ADR-010-resource-budget.md) Outcome addenda). End-to-end p99 trade → ClickHouse Silver: **170–197 ms** across Binance, Kraken and Coinbase. All 6 failure-mode tests pass, max MTTR 32 s. Every figure here traces to [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md).

## Conventions

- One decision per file: `ADR-NNN-slug.md`, sequential, never reused, never renumbered.
  The slug states the decision (`ADR-015-clickhouse-lts-downgrade.md`, not `ADR-015-ch.md`).
- Structure comes from [`template.md`](./template.md): Context · Decision · Options
  considered · Consequences · Outcome.
- **The test is cost of reversal**: would unwinding this six months on cost more than a
  day? If not, it is not an ADR. A corpus padded with cheap choices is one nobody reads.
- **Consequences state what the decision costs**, not only what it buys.
- **Accepted ADRs are immutable.** Reversing one means a new ADR; the only permitted edit
  to an accepted ADR is its status line and an appended `Outcome`.
- **`Outcome` is where honesty lives.** When implementation diverged, the original
  reasoning stays as written and the Outcome says what was built instead, and why. The
  recorded wrong prediction is the most valuable thing in this directory.
- An ADR lands in the same PR as the work it governs; the index tables below update with it.
- Write it with the `/adr` skill — it handles numbering, the header block and the index.

---

## Design decisions (written 2026-02-09, pre-build)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [001](ADR-001-replace-kafka-with-redpanda.md) | Replace Kafka with Redpanda | Accepted — Implemented | Redpanda v25.3.4 + Console v3.5.1; Kafka and Schema Registry never deployed |
| [002](ADR-002-kotlin-feed-handlers.md) | Kotlin feed handlers | Accepted — Implemented, deviations — **superseded by [ADR-019](ADR-019-rust-capture-tier.md)** | Kotlin 2.3.10 + **Ktor** (not Spring Boot), **3 containers** (not one JVM); ~0.03 CPU / 134 MiB each. Retired to [`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md) on 2026-08-26 |
| [003](ADR-003-clickhouse-warm-storage.md) | ClickHouse as warm storage | Accepted — Implemented; 3 clauses **superseded by [ADR-025](ADR-025-clickhouse-derived-hot-tier.md)** | Runs 24.3 LTS, not the researched latest — see ADR-015 |
| [004](ADR-004-eliminate-spark-streaming.md) | Eliminate Spark Streaming | Accepted — Implemented, deviation | 5 streaming jobs never built; replacement is **ClickHouse MVs**, not Kotlin processors |
| [005](ADR-005-kotlin-spring-boot-api.md) | Spring Boot query API | **Deferred — Not implemented** | Scored 3/10 ROI; Phase 8 not started; no query API exists |
| [006](ADR-006-spark-batch-only.md) | Spark for batch only | Accepted — Implemented | One on-demand Spark container; the planned Kotlin hourly writer was dropped (ADR-014) |
| [007](ADR-007-iceberg-cold-storage.md) | Iceberg cold storage | Accepted — Implemented, deviation | **Hadoop catalog** on a bind-mounted local warehouse (MinIO provisioned, unused), not REST catalog + Postgres; 15-min offload, not hourly |
| [008](ADR-008-eliminate-prefect-orchestration.md) | Eliminate Prefect | **Partially rejected** — superseded by ADR-014, ADR-017 | MVs did replace all 5 OHLCV flows; Prefect was **retained** for offload + maintenance |
| [009](ADR-009-medallion-in-clickhouse.md) | Four-layer medallion in ClickHouse | Accepted — Implemented, amended by ADR-011; medallion + offload-direction clauses **superseded by [ADR-025](ADR-025-clickhouse-derived-hot-tier.md)**; string-numerics rule by [ADR-020](ADR-020-avro-fixed-point-contracts.md) | Built as **3 layers, no RAW** — Redpanda is the replay log |
| [010](ADR-010-resource-budget.md) | Resource budget (16 CPU / 40 GB) | Accepted — Implemented | Budget held: 15.1 CPU / 21.875 GB; composition differs (Prefect in, API + Silver processor out) |

## Implementation decisions (2026-02-10 → 2026-02-18)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [011](ADR-011-multi-exchange-bronze-architecture.md) | Multi-exchange Bronze architecture | Accepted — **superseded by [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) for the lake only**; in force for ClickHouse until Phase E | Per-exchange Bronze tables, unified at Silver — the pattern that scaled to a 3rd exchange in a day |
| [012](ADR-012-spark-iceberg-version-upgrade.md) | Spark 4.1.1 + Iceberg 1.10.1 upgrade | Superseded by ADR-013 | Abandoned same day after 2+ h of version incompatibility |
| [013](ADR-013-pragmatic-iceberg-version-strategy.md) | Pragmatic Iceberg version strategy | Accepted — Implemented — **superseded by [ADR-023](ADR-023-lakekeeper-rest-catalog.md)** | Proven `tabulario/spark-iceberg` image + Hadoop catalog; working stack in ~45 min |
| [014](ADR-014-spark-based-iceberg-offload.md) | Spark-based Iceberg offload | Accepted — Implemented — **superseded by [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)**; **deleted 2026-08-27**, Outcome appended | PySpark instead of a custom Kotlin service; 15-min cycle; 236 K rows/s measured. The mechanism worked; copying a serving database into the archive was the wrong thing to do well |
| [015](ADR-015-clickhouse-lts-downgrade.md) | ClickHouse 24.3 LTS downgrade | Accepted — Implemented | Latest ClickHouse broke every Spark JDBC driver; LTS is the version that has drivers |
| [016](ADR-016-add-coinbase-exchange.md) | Add Coinbase as 3rd exchange | Accepted — Implemented | 11 pairs live; validated the ADR-011 Bronze pattern end to end |
| [017](ADR-017-iceberg-maintenance-pipeline.md) | Iceberg daily maintenance pipeline | Accepted — Implemented; watermark + audit stores **superseded by [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)** for the lake; **deleted 2026-08-27**, Outcome appended | Nightly compact → expire → audit; audit gate fails the run on missing data. The compact → expire → audit ordering carried into `docker/lake/maintenance.py` unchanged |

## v3 decisions (2026-08-26 →)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [018](ADR-018-v3-lake-first-rust-capture.md) | v3: lake-first, Rust capture tier | **Proposed** | Umbrella for the v3 rebuild; supersedes ADR-002/007/009/013/014 when accepted, via follow-on ADRs 019–028 |
| [019](ADR-019-rust-capture-tier.md) | Rust capture tier replaces Kotlin feed handlers | **Accepted** — supersedes [ADR-002](ADR-002-kotlin-feed-handlers.md) | Phase C. Not a latency argument: one connection per exchange for trades + book, recv_ts before parse, replay determinism, 42.8 MB vs 3 JVMs. Kotlin retired to `legacy/v2-kotlin/` on parity; Outcome records the window and the 1.5 CPU / 1.5 GB returned |
| [020](ADR-020-avro-fixed-point-contracts.md) | Avro-only contracts: fixed-point int64 @1e-8, recv_ts in body | **Proposed** — supersedes [ADR-009](ADR-009-medallion-in-clickhouse.md)'s string-numerics rule (scoped; the medallion decision goes to ADR-025) | Phase B/C. One wire format on the only path; `recv_ts_ns` in body *and* header, body authoritative; `market.crypto.v3.*` prefix because the v2 subject rejects the new schema |
| [021](ADR-021-raw-first-archive-and-lineage.md) | Raw-first archive with per-record lineage | **Proposed** | Phase D. `raw.messages` is the system of record, kept forever (Q8); `bronze.*` are pure functions of it; three lineage columns instead of a lineage system; no `book_deltas` table — the deltas are already in the archive |
| [022](ADR-022-exactly-once-via-snapshot-offsets.md) | Exactly-once ingest via Kafka offsets in the Iceberg snapshot summary | **Proposed** — supersedes [ADR-014](ADR-014-spark-based-iceberg-offload.md) | Phase D. One atomic commit carries the data and the position, so the PostgreSQL watermark table goes. `failOnDataLoss=true`; compaction snapshots skipped when resuming |
| [023](ADR-023-lakekeeper-rest-catalog.md) | Lakekeeper REST catalog on MinIO | **Proposed** — supersedes [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) | Phase D. The Hadoop catalog has no atomic commit, no multi-writer, and is unreadable from ClickHouse. ADR-013's method — spike the exact versions — is what chose the replacement |
| [024](ADR-024-unified-bronze-tables-in-the-lake.md) | Unified bronze tables in the lake, partitioned by exchange | **Proposed** — supersedes [ADR-011](ADR-011-multi-exchange-bronze-architecture.md) (lake only) | Phase D. One contract across three venues makes unification cheap; native shape is preserved by `raw.messages`, not by a table per venue. Symbol prunes by sort order, never by partition |
| [025](ADR-025-clickhouse-derived-hot-tier.md) | ClickHouse as a derived, rebuildable hot tier | **Proposed** — supersedes clauses of [ADR-009](ADR-009-medallion-in-clickhouse.md) and [ADR-003](ADR-003-clickhouse-warm-storage.md) | Written in Phase D, built in Phase E. ClickHouse originates nothing; reload is a pull through `iceberg()`; the `s3()` glob is banned in writing because it returns plausible wrong numbers |
| [026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | Four lake layers per venue, gold canonical, gold served indefinitely from ClickHouse | **Accepted** — supersedes [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) and ADR-025's 7-day-TTL clause | Decided 2026-08-27 after the first day of Phase D; Phase E implements. Strategy in `docs/architecture/12-data-strategy.md` |
| [027](ADR-027-book-snapshot-and-sequencing.md) | L2 book snapshot model and per-exchange resync policy | **Accepted** — Outcome 2026-08-28 | Phase C. Top-20 @ 1 Hz is the queryable product, deltas stay verbatim in `raw.messages`; three sequencing mechanisms kept explicit; states plainly what 1 Hz cannot see |
| [029](ADR-029-research-production-parity-contract.md) | The research/production parity contract | **Accepted** | Phase G. One parser for live and replay (`k2-capture replay`), golden fixtures hashed in CI, three-way parity at tolerance zero on pinned snapshots for candles and bars, notebooks read pinned views only. ADR-028 (non-goals) is still unwritten; the fidelity limits live in `docs/research/2026-08-28-replay-fidelity-limits.md` meanwhile |

---

## Supersession chain

An Accepted ADR is never edited; a reversal is a new ADR and a changed status line. This
is every supersession in the corpus, and how much of the original each one takes.

| Superseded | By | Scope |
|---|---|---|
| [ADR-012](ADR-012-spark-iceberg-version-upgrade.md) | [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) | whole decision — abandoned the same day |
| [ADR-008](ADR-008-eliminate-prefect-orchestration.md) | [ADR-014](ADR-014-spark-based-iceberg-offload.md), [ADR-017](ADR-017-iceberg-maintenance-pipeline.md) | partially rejected: the MV half held, Prefect was retained |
| [ADR-002](ADR-002-kotlin-feed-handlers.md) | [ADR-019](ADR-019-rust-capture-tier.md) | whole decision, on Phase C parity |
| [ADR-009](ADR-009-medallion-in-clickhouse.md) | [ADR-020](ADR-020-avro-fixed-point-contracts.md) | the Raw layer's string-numerics rule only |
| [ADR-014](ADR-014-spark-based-iceberg-offload.md) | [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) | the offload mechanism and its PostgreSQL watermark — implementation deleted 2026-08-27 |
| [ADR-017](ADR-017-iceberg-maintenance-pipeline.md) | [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) | the PostgreSQL audit store, for the lake; the compact → expire → audit ordering is kept — implementation deleted 2026-08-27 |
| [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) | [ADR-023](ADR-023-lakekeeper-rest-catalog.md) | the Hadoop catalog; the version-spiking method it taught is what chose the replacement |
| [ADR-011](ADR-011-multi-exchange-bronze-architecture.md) | [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) | **the lake only** — still in force for ClickHouse until Phase E |
| [ADR-009](ADR-009-medallion-in-clickhouse.md) | [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) | medallion-in-ClickHouse and offload-from-ClickHouse; the layer model is [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md); OHLCV-on-read gets its own ADR |
| [ADR-003](ADR-003-clickhouse-warm-storage.md) | [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) | three of five Decision clauses: 30-day MergeTree, primary query engine, TTL-to-Iceberg |
| [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) | [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | in full — unified bronze becomes the gold core; bronze and silver are per venue |
| [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) | [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | the 7-day-TTL clause only — derived, rebuildable, reload-by-pull stands; gold is kept indefinitely |

Two numbers in the v3 series are reserved and not yet written: **026** (OHLCV computed on
read + the `ReplacingMergeTree` dedup contract, Phase E) and **028** (non-goals and honest
limits of a single-host research platform). They are listed in
[ADR-018's follow-on table](ADR-018-v3-lake-first-rust-capture.md#follow-on-adrs); numbers
are never reused or renumbered, so they stay empty until their phase lands.

---

## Supporting documents

| Document | Description |
|----------|-------------|
| [../research/2026-02-09-v2-investment-analysis.md](../research/2026-02-09-v2-investment-analysis.md) | Pre-build risk/reward ranking of each proposed change (2026-02-09) — predictions left unedited |
| [../MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md) | What those predictions were actually worth, measured after the build |

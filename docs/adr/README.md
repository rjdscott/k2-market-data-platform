# Architectural Decision Records

Eighteen ADRs covering the design and rebuild of the K2 Market Data Platform: Kotlin/Ktor feed handlers → Redpanda → ClickHouse (medallion via materialized views) → Iceberg with a Hadoop catalog on a bind-mounted local warehouse (MinIO provisioned, unused by the offload), with Spark batch offload orchestrated by Prefect. ADR-001 to ADR-010 were written up front in February 2026, before any of it was built; ADR-011 to ADR-017 came out of implementation. The status column below records what actually happened — including the one decision that was reversed (ADR-008) and the one never built (ADR-005). Each ADR that deviated from its own design carries an `Outcome` section at the end explaining why. ADR-018 opens the v3 series: an umbrella decision, still Proposed, arguing that the v2 shape is wrong for research work.

Measured as-built: **15.1 CPU / 21.875 GB** across 14 long-running services (+2 one-shot), against a 16 CPU / 40 GB budget. End-to-end p99 trade → ClickHouse Silver: **170–197 ms** across Binance, Kraken and Coinbase. All 6 failure-mode tests pass, max MTTR 32 s. Every figure here traces to [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md).

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
| [002](ADR-002-kotlin-feed-handlers.md) | Kotlin feed handlers | Accepted — Implemented, deviations | Kotlin 2.3.10 + **Ktor** (not Spring Boot), **3 containers** (not one JVM); ~0.03 CPU / 134 MiB each |
| [003](ADR-003-clickhouse-warm-storage.md) | ClickHouse as warm storage | Accepted — Implemented | Runs 24.3 LTS, not the researched latest — see ADR-015 |
| [004](ADR-004-eliminate-spark-streaming.md) | Eliminate Spark Streaming | Accepted — Implemented, deviation | 5 streaming jobs never built; replacement is **ClickHouse MVs**, not Kotlin processors |
| [005](ADR-005-kotlin-spring-boot-api.md) | Spring Boot query API | **Deferred — Not implemented** | Scored 3/10 ROI; Phase 8 not started; no query API exists |
| [006](ADR-006-spark-batch-only.md) | Spark for batch only | Accepted — Implemented | One on-demand Spark container; the planned Kotlin hourly writer was dropped (ADR-014) |
| [007](ADR-007-iceberg-cold-storage.md) | Iceberg cold storage | Accepted — Implemented, deviation | **Hadoop catalog** on a bind-mounted local warehouse (MinIO provisioned, unused), not REST catalog + Postgres; 15-min offload, not hourly |
| [008](ADR-008-eliminate-prefect-orchestration.md) | Eliminate Prefect | **Partially rejected** — superseded by ADR-014, ADR-017 | MVs did replace all 5 OHLCV flows; Prefect was **retained** for offload + maintenance |
| [009](ADR-009-medallion-in-clickhouse.md) | Four-layer medallion in ClickHouse | Accepted — Implemented, amended by ADR-011 | Built as **3 layers, no RAW** — Redpanda is the replay log |
| [010](ADR-010-resource-budget.md) | Resource budget (16 CPU / 40 GB) | Accepted — Implemented | Budget held: 15.1 CPU / 21.875 GB; composition differs (Prefect in, API + Silver processor out) |

## Implementation decisions (2026-02-10 → 2026-02-18)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [011](ADR-011-multi-exchange-bronze-architecture.md) | Multi-exchange Bronze architecture | Accepted | Per-exchange Bronze tables, unified at Silver — the pattern that scaled to a 3rd exchange in a day |
| [012](ADR-012-spark-iceberg-version-upgrade.md) | Spark 4.1.1 + Iceberg 1.10.1 upgrade | Superseded by ADR-013 | Abandoned same day after 2+ h of version incompatibility |
| [013](ADR-013-pragmatic-iceberg-version-strategy.md) | Pragmatic Iceberg version strategy | Accepted — Implemented | Proven `tabulario/spark-iceberg` image + Hadoop catalog; working stack in ~45 min |
| [014](ADR-014-spark-based-iceberg-offload.md) | Spark-based Iceberg offload | Accepted — Implemented | PySpark instead of a custom Kotlin service; 15-min cycle; 236 K rows/s measured |
| [015](ADR-015-clickhouse-lts-downgrade.md) | ClickHouse 24.3 LTS downgrade | Accepted — Implemented | Latest ClickHouse broke every Spark JDBC driver; LTS is the version that has drivers |
| [016](ADR-016-add-coinbase-exchange.md) | Add Coinbase as 3rd exchange | Accepted — Implemented | 11 pairs live; validated the ADR-011 Bronze pattern end to end |
| [017](ADR-017-iceberg-maintenance-pipeline.md) | Iceberg daily maintenance pipeline | Accepted — Implemented | Nightly compact → expire → audit; audit gate fails the run on missing data |

## v3 decisions (2026-08-26 →)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [018](ADR-018-v3-lake-first-rust-capture.md) | v3: lake-first, Rust capture tier | **Proposed** | Umbrella for the v3 rebuild; supersedes ADR-002/007/009/013/014 when accepted, via follow-on ADRs 019–028 |

---

## Supporting documents

| Document | Description |
|----------|-------------|
| [../research/2026-02-09-v2-investment-analysis.md](../research/2026-02-09-v2-investment-analysis.md) | Pre-build risk/reward ranking of each proposed change (2026-02-09) — predictions left unedited |
| [../MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md) | What those predictions were actually worth, measured after the build |

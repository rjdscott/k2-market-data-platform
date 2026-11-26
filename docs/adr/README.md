# Architectural Decision Records

Forty-one ADRs covering the design and rebuild of the K2 Market Data Platform: exchange capture → Redpanda → ClickHouse (medallion via materialized views) and an Iceberg lake on a Lakekeeper REST catalog over MinIO, fed by a Spark batch ingest orchestrated by Airflow 3 (by Prefect until the 2026-11 cutover, ADR-034). The v2 shape those ADRs describe (a Hadoop catalog on a bind-mounted warehouse, fed by a JDBC offload out of ClickHouse) was deleted in Phase D; ADR-014 and ADR-017 carry the Outcome sections that record it. ADR-001 to ADR-010 were written up front in February 2026, before any of it was built; ADR-011 to ADR-017 came out of implementation. The status column below records what actually happened, including the one decision that was reversed (ADR-008) and the one never built (ADR-005). Each ADR that deviated from its own design carries an `Outcome` section at the end explaining why. ADR-018 opens the v3 series: an umbrella decision, still Proposed, arguing that the v2 shape is wrong for research work; ADR-019, ADR-020 and ADR-027 landed with Phase C, and ADR-021 to ADR-025 with Phase D's lake tier. ADR-019 is Accepted and its Outcome records the Kotlin retirement: the capture tier is Rust only, and the v2 `k2.*` medallion is frozen until the Phase E cutover drops it. Where a v3 ADR supersedes a v2 one, the v2 body is left exactly as written and only its status line changes; the [supersession chain](#supersession-chain) below is the map.

Measured as-built at the v2 baseline: **15.1 CPU / 21.875 GB** across 14 long-running services (+2 one-shot), against a 16 CPU / 40 GB budget. With the capture tier swapped for v3's Rust one, Lakekeeper and the `lake-metrics` exporter added, and the v2 offload deleted, the declared steady state is 14.60 CPU / 25.625 GiB across 15 long-running services (+4 one-shot, bootstrap peak 16.10 CPU / 27.125 GiB across 19) ([ADR-010](ADR-010-resource-budget.md) Outcome addenda). With Airflow, cAdvisor and Trino it is 19.30 CPU / 35.312 GiB across 19 (+5 one-shot, bootstrap peak 21.30 CPU / 37.812 GiB across 24) under a 25 CPU / 48 GiB ceiling ([ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)). End-to-end p99 trade → ClickHouse Silver: **170–197 ms** across Binance, Kraken and Coinbase. All 6 failure-mode tests pass, max MTTR 32 s. Every figure here traces to [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md).

> **New to the vocabulary?** The [glossary](glossary.md) explains every internal label and tool
> concept these ADRs use, from "stage 2d" and "exit 75" to snapshots, selectors and definer views.
> The five most recent decisions are also told [in plain words](#recent-decisions-in-plain-words).

## Conventions

- One decision per file: `ADR-NNN-slug.md`, sequential, never reused, never renumbered.
  The slug states the decision (`ADR-015-clickhouse-lts-downgrade.md`, not `ADR-015-ch.md`).
- Structure comes from [`template.md`](./template.md): In plain words · Context · Decision ·
  Options considered · Consequences · Outcome.
- **An ADR is understood from its own text.** Technical depth is welcome; the test is whether a
  reader who has not opened the code can follow it and learn from it. So every ADR opens with
  `## In plain words` and one bold decision sentence under a plain title, and explains each
  internal label, tool concept and mechanism where it first appears or links it to the
  [glossary](glossary.md), which gains any new term in the same pull request. Accepted ADRs
  written before 2026-11-26 predate this rule and cannot be edited, so read them with the glossary.
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
| [006](ADR-006-spark-batch-only.md) | Spark for batch only | Accepted, Implemented, Outcome 2026-11-25 | One on-demand Spark container; the planned Kotlin hourly writer was dropped (ADR-014). Trino joined beside Spark for SQL serving and dbt marts ([ADR-035](ADR-035-trino-and-dbt-for-marts.md)); Spark keeps every ETL job |
| [007](ADR-007-iceberg-cold-storage.md) | Iceberg cold storage | Accepted — Implemented, deviation | **Hadoop catalog** on a bind-mounted local warehouse (MinIO provisioned, unused), not REST catalog + Postgres; 15-min offload, not hourly |
| [008](ADR-008-eliminate-prefect-orchestration.md) | Eliminate Prefect | **Partially rejected**, superseded by ADR-014, ADR-017; orchestration clauses **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** | MVs did replace all 5 OHLCV flows; Prefect was **retained** for offload + maintenance, until Airflow 3 replaced it at the 2026-11 cutover |
| [009](ADR-009-medallion-in-clickhouse.md) | Four-layer medallion in ClickHouse | Accepted — Implemented, amended by ADR-011; medallion + offload-direction clauses **superseded by [ADR-025](ADR-025-clickhouse-derived-hot-tier.md)**; string-numerics rule by [ADR-020](ADR-020-avro-fixed-point-contracts.md) | Built as **3 layers, no RAW** — Redpanda is the replay log |
| [010](ADR-010-resource-budget.md) | Resource budget (16 CPU / 40 GB) | Accepted, Implemented; the ceiling **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** (20 CPU / 36 GiB at the bootstrap peak), then by **[ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)** (25 CPU / 48 GiB) | Budget held: 15.1 CPU / 21.875 GB; composition differs (Prefect in, API + Silver processor out) |

## Implementation decisions (2026-02-10 → 2026-02-18)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [011](ADR-011-multi-exchange-bronze-architecture.md) | Multi-exchange Bronze architecture | Accepted — **superseded by [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) for the lake only**; in force for ClickHouse until Phase E | Per-exchange Bronze tables, unified at Silver — the pattern that scaled to a 3rd exchange in a day |
| [012](ADR-012-spark-iceberg-version-upgrade.md) | Spark 4.1.1 + Iceberg 1.10.1 upgrade | Superseded by ADR-013 | Abandoned same day after 2+ h of version incompatibility |
| [013](ADR-013-pragmatic-iceberg-version-strategy.md) | Pragmatic Iceberg version strategy | Accepted — Implemented — **superseded by [ADR-023](ADR-023-lakekeeper-rest-catalog.md)** | Proven `tabulario/spark-iceberg` image + Hadoop catalog; working stack in ~45 min |
| [014](ADR-014-spark-based-iceberg-offload.md) | Spark-based Iceberg offload | Accepted, Implemented, **superseded by [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)**; **deleted 2026-08-27**, Outcome appended; Prefect scheduling **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** | PySpark instead of a custom Kotlin service; 15-min cycle; 236 K rows/s measured. The mechanism worked; copying a serving database into the archive was the wrong thing to do well |
| [015](ADR-015-clickhouse-lts-downgrade.md) | ClickHouse 24.3 LTS downgrade | Accepted — Implemented | Latest ClickHouse broke every Spark JDBC driver; LTS is the version that has drivers |
| [016](ADR-016-add-coinbase-exchange.md) | Add Coinbase as 3rd exchange | Accepted — Implemented | 11 pairs live; validated the ADR-011 Bronze pattern end to end |
| [017](ADR-017-iceberg-maintenance-pipeline.md) | Iceberg daily maintenance pipeline | Accepted, Implemented; watermark + audit stores **superseded by [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)** for the lake; **deleted 2026-08-27**, Outcome appended; Prefect orchestration **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** | Nightly compact → expire → audit; audit gate fails the run on missing data. The compact → expire → audit ordering carried into `docker/lake/maintenance.py` unchanged |

## v3 decisions (2026-08-26 →)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [018](ADR-018-v3-lake-first-rust-capture.md) | v3: lake-first, Rust capture tier | **Proposed**; "Prefect is retained" **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** | Umbrella for the v3 rebuild; supersedes ADR-002/007/009/013/014 when accepted, via follow-on ADRs 019–028 |
| [019](ADR-019-rust-capture-tier.md) | Rust capture tier replaces Kotlin feed handlers | **Accepted** — supersedes [ADR-002](ADR-002-kotlin-feed-handlers.md) | Phase C. Not a latency argument: one connection per exchange for trades + book, recv_ts before parse, replay determinism, 42.8 MB vs 3 JVMs. Kotlin retired to `legacy/v2-kotlin/` on parity; Outcome records the window and the 1.5 CPU / 1.5 GB returned |
| [020](ADR-020-avro-fixed-point-contracts.md) | Avro-only contracts: fixed-point int64 @1e-8, recv_ts in body | **Proposed** — supersedes [ADR-009](ADR-009-medallion-in-clickhouse.md)'s string-numerics rule (scoped; the medallion decision goes to ADR-025) | Phase B/C. One wire format on the only path; `recv_ts_ns` in body *and* header, body authoritative; `market.crypto.v3.*` prefix because the v2 subject rejects the new schema |
| [021](ADR-021-raw-first-archive-and-lineage.md) | Raw-first archive with per-record lineage | **Proposed** | Phase D. `raw.messages` is the system of record, kept forever (Q8); `bronze.*` are pure functions of it; three lineage columns instead of a lineage system; no `book_deltas` table — the deltas are already in the archive |
| [022](ADR-022-exactly-once-via-snapshot-offsets.md) | Exactly-once ingest via Kafka offsets in the Iceberg snapshot summary | **Proposed** — supersedes [ADR-014](ADR-014-spark-based-iceberg-offload.md) | Phase D. One atomic commit carries the data and the position, so the PostgreSQL watermark table goes. `failOnDataLoss=true`; compaction snapshots skipped when resuming |
| [023](ADR-023-lakekeeper-rest-catalog.md) | Lakekeeper REST catalog on MinIO | **Proposed** — supersedes [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) | Phase D. The Hadoop catalog has no atomic commit, no multi-writer, and is unreadable from ClickHouse. ADR-013's method — spike the exact versions — is what chose the replacement |
| [024](ADR-024-unified-bronze-tables-in-the-lake.md) | Unified bronze tables in the lake, partitioned by exchange | **Proposed** — supersedes [ADR-011](ADR-011-multi-exchange-bronze-architecture.md) (lake only) | Phase D. One contract across three venues makes unification cheap; native shape is preserved by `raw.messages`, not by a table per venue. Symbol prunes by sort order, never by partition |
| [025](ADR-025-clickhouse-derived-hot-tier.md) | ClickHouse as a derived, rebuildable hot tier | **Proposed** — supersedes clauses of [ADR-009](ADR-009-medallion-in-clickhouse.md) and [ADR-003](ADR-003-clickhouse-warm-storage.md) | Written in Phase D, built in Phase E. ClickHouse originates nothing; reload is a pull through `iceberg()`; the `s3()` glob is banned in writing because it returns plausible wrong numbers |
| [026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | Four lake layers per venue, gold canonical, gold served indefinitely from ClickHouse | **Accepted** — supersedes [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) and ADR-025's 7-day-TTL clause | Decided 2026-08-27 after the first day of Phase D; Phase E implements. Strategy in `docs/architecture/12-data-strategy.md` |
| [027](ADR-027-book-snapshot-and-sequencing.md) | L2 book snapshot model and per-exchange resync policy | **Accepted** — Outcome 2026-08-28 | Phase C. Top-20 @ 1 Hz is the queryable product, deltas stay verbatim in `raw.messages`; three sequencing mechanisms kept explicit; states plainly what 1 Hz cannot see |
| [028](ADR-028-non-goals-and-honest-limits.md) | Non-goals and honest limits of a single-host research platform | **Accepted**, Outcome 2026-11 | The one list the v3 series pointed at: twelve things K2 deliberately does not do (no HA, no L3, no full-depth book, crypto only, public feeds only, no API, no simulator, no multi-tenancy, no cloud, no error budgets, no schema automation, no retention policy), each with its cost, a concrete revisit trigger, and the nearest thing K2 does offer. The Outcome records § 11 lifted for the lake's product contracts by [ADR-032](ADR-032-odcs-contracts-and-k2gen.md), § 12 kept, and § 6 lifted for SQL serving by [ADR-035](ADR-035-trino-and-dbt-for-marts.md), with § 8 kept |
| [029](ADR-029-research-production-parity-contract.md) | The research/production parity contract | **Accepted**, Outcome 2026-11 | Phase G. The golden fixtures became synthetic in 2026-11 and their hashes were re-pinned (Outcome). One parser for live and replay (`k2-capture replay`), golden fixtures hashed in CI, three-way parity at tolerance zero on pinned snapshots for candles and bars, notebooks read pinned views only. Clause 3 is scoped for dbt marts by [ADR-035](ADR-035-trino-and-dbt-for-marts.md) (Outcome 2026-11-25). The research consequences of the limits in [ADR-028](ADR-028-non-goals-and-honest-limits.md) are in `docs/research/2026-08-28-replay-fidelity-limits.md` |
| [030](ADR-030-scd2-security-master.md) | SCD2 security master for the instrument and venue dimensions | **Accepted** | The two gold dimensions become Kimball type-2: deterministic `instrument_id` over `(exchange, canonical_symbol)` so a venue rename is a version and not a new instrument, `9999-12-31` sentinel over NULL because `ts < NULL` is not TRUE, `attr_hash` as the change predicate, Kraken's published precisions from `bronze.kraken_instrument`. Facts carry no `instrument_id`; the dimensions become the one part of gold a rebuild does not touch. Analysis in [`../research/2026-08-29-scd2-security-master.md`](../research/2026-08-29-scd2-security-master.md) |

## Platform decisions (2026-11-24 →)

| ADR | Title | Status | Outcome |
|-----|-------|--------|---------|
| [031](ADR-031-governed-self-serve-platform.md) | A governed, self-serve platform layer over the lake | **Proposed**; its 20 CPU / 36 GiB ceiling **superseded by [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)** (trigger fired 2026-11-25) | Umbrella for the [governed self-serve platform plan](../plans/2026-11-24-governed-self-serve-platform/README.md). One data contract per product drives generated orchestration, quality, access, SLO and catalog artefacts. Reserves 032 to 042; each lands Accepted in the pull request that builds it |
| [032](ADR-032-odcs-contracts-and-k2gen.md) | Product descriptors and ODCS v3 contracts, checked and generated by k2gen | **Accepted** | Lifts [ADR-028](ADR-028-non-goals-and-honest-limits.md) § 11 for the lake. A descriptor per product and an ODCS v3.2.0 contract per table under `contracts/`; `k2gen validate` checks them against the standard, `lake.sql` (which stays the DDL of record) and three rules across products. `retention` is snapshot expiry, so § 12 stands. Outcome 2026-11-26: the nightly `contract_schema` audit holds each live table to its contract too |
| [033](ADR-033-data-mesh-four-domains.md) | Data mesh, four domains, and a platform team that owns the paved road | **Accepted**, Outcome 2026-11-25; "the platform owns the road, not the products" **superseded for the governance domain by [ADR-046](ADR-046-governance-domain-and-quality-results.md)** | `market-capture`, `reference-data`, `fx-reference` (declared, first product in P4) and `market-analytics`, registered in `contracts/domains.yaml` and `.github/CODEOWNERS`; every team is the one account until a second contributor arrives. The Outcome records each domain's mart warehouse ([ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md)) and a pending governance domain, to be decided in its own ADR |
| [034](ADR-034-airflow-3-replaces-prefect.md) | Airflow 3 replaces Prefect; the default profile's ceiling is 20 CPU / 36 GiB | **Accepted**, Outcome 2026-11; its ingest-duration trigger **fired** 2026-11-25 (p90 253 s against 240 s), answered by [ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md) (Outcome 2026-11-26); the ceiling **superseded by [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)**; supersedes [ADR-010](ADR-010-resource-budget.md)'s 16 CPU / 40 GB clause and the orchestration clauses of ADR-008, ADR-014, ADR-017 and ADR-018 | Airflow 3.3.2, the newest cosmos supports outside beta, LocalExecutor, three hand-written DAGs over the same `docker exec` dispatch: one run at a time, no replay, a timeout that stops the Spark driver, exit 75 to skipped for the ingest, catch-up by params. Landed as a schedule shadow beside Prefect, then a cutover that left Prefect stopped behind the compose `legacy` profile as a documented rollback (Outcome); the ceiling is gated at the bootstrap peak in CI. A later Outcome corrects the Spark thread-count claim: the JVM already sized itself to the 3.0 CPU quota, and 2 threads made runs 11 percent slower. The 2026-11-26 Outcome records the standing trigger firing: run time grew through the UTC day with the gold candle and bar step |
| [035](ADR-035-trino-and-dbt-for-marts.md) | Trino and dbt for marts; the Spark and dbt boundary | **Accepted**, Outcome 2026-11-26; departs from [ADR-006](ADR-006-spark-batch-only.md), lifts [ADR-028](ADR-028-non-goals-and-honest-limits.md) § 6 for SQL serving, scopes [ADR-029](ADR-029-research-production-parity-contract.md) clause 3 for marts | Trino 484, one node at 2.0 CPU / 4 GiB, serves SQL over the lake and builds dbt marts; Spark keeps every job that writes the lake. Measured first: types and time travel through Lakekeeper, views work but only Trino reads them, the verbatim candle `quote_volume` overflows, four concurrent aggregations fit in 4 GiB. Outcome: the memory trigger fired on the whole-table source test (scoped to 6 hours, the Spark audit keeps the table); the delayed product is a view over the lake's candles, held to each venue's newest complete minute; marts are expired by their own writer through Trino; the memory limit went from 4 to 5 GiB, heap unchanged |
| [044](ADR-044-producer-lake-and-domain-mart-warehouses.md) | One producer lake, one mart warehouse per domain, fenced per writer | **Accepted**, Outcome 2026-11-25; extends [ADR-033](ADR-033-data-mesh-four-domains.md) (Outcome 2026-11-25); outside ADR-031's reserved block | Warehouse `k2` stays Spark's, read-only to Trino at the connector; each domain in `contracts/domains.yaml` gets a warehouse, a Trino catalog and a writer `dbt_<domain>`, generated by `make generate`. A cross-domain mart belongs to its consumer. Retention-floor session properties denied to all; table procedures inside domain catalogs are the stated gap |
| [045](ADR-045-ceiling-25-cpu-48-gib.md) | The default profile's ceiling is 25 CPU / 48 GiB at the bootstrap peak | **Accepted**, Outcome 2026-11-26; supersedes ADR-034's ceiling clause; outside ADR-031's reserved block | ADR-031's 20 / 36 trigger fired with Trino; 25 / 48 covers the catalog phase's estimate. Measured use 3.84 cores and 10.12 GiB at the 6 h peak. Its claim that the host keeps 12.69 GiB beyond the declared limits was wrong: the Docker VM grows to its whole 50.5 GiB setting, and the host also runs a desktop. The host killed the VM for memory on 2026-11-25; the Outcome adds the rule that the VM setting plus everything else must stay under host RAM, and the owner kept the VM size |
| [046](ADR-046-governance-domain-and-quality-results.md) | Governance is a domain like any other; quality results are recorded by each domain's own writer and unified by a governance view | **Accepted**; supersedes ADR-033's "the platform owns the road, not the products" for the governance domain; outside ADR-031's reserved block | Domain `governance` (`platform-team`) gets its fence from `make generate`. An `on-run-end` hook writes every test and unit test of a dbt invocation, failed and skipped ones included, to `<catalog>.dq.dbt_test_results` as the domain's own writer (a task Airflow never starts records nothing); view `governance.quality_results.dq_results` unions them with `lake.audit.checks` as pass, fail, warn or error, built after every other domain. Spark appending `run_results.json`, a shared table, files in object storage, `dbt_artifacts` and Elementary considered |
| [047](ADR-047-ingest-batch-audit-after-publish.md) | The ingest audits each batch after publishing it, and never blocks the commit | **Accepted**; outside ADR-031's reserved block (036 stays write-audit-publish for file sources) | Stage 1 checks the batch it just committed, per topic and partition: rows equal the planned range, first and last offsets match. A mismatch is a `batch_offsets` row in `audit.checks` and `LakeIngestBatchAuditFailed`; a clean batch files nothing. Not write-audit-publish: a blocked archive stops reading Redpanda, and raw retention evicts before a person can answer |
| [048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md) | Gold step reads only touched data and writes bars once | **Accepted** 2026-11-26, after an independent design review (#215); outside ADR-031's reserved block | The candle and bar step of each ingest run (stage 2d) read the whole of `gold.trades` ten times, computed the bars twice and wrote them in two commits. It will take its touched keys from the `gold.trades` snapshot recorded on its last publish and run whenever `gold.trades` moved past it, read only their `exchange_ts` range, compute once, and publish bars by `DELETE` and `append` on a branch then a fast-forward of `main`. A `MERGE` with `NOT MATCHED BY SOURCE` was rejected: Spark then rewrites every file. No schema change; rows identical, checked old against new on real data before merge |
| [049](ADR-049-event-bars-extended-not-recomputed.md) | Event bars are extended by new trades, not recomputed | **Proposed**, not ready to accept: depends on ADR-048 and a measured gate | In-order batches are folded into the stored bars; late or tied batches, a threshold change and a non-append repair of `gold.trades` recompute the day. Adds `quote_volume_e16`, each bar's exact dollar value, in the lake and ClickHouse. Dropped if, after ADR-048, the window stage stays under 370 MiB of execution memory and 256 MiB of spill at the last run of a UTC day (proposed thresholds) |

---

## Recent decisions in plain words

The tables above are written for someone who already knows the platform. These paragraphs are
for someone who does not; each term links to the [glossary](glossary.md).

**[ADR-035](ADR-035-trino-and-dbt-for-marts.md), Trino and dbt for marts.** Consumers needed
one shared SQL engine and somewhere to build their own tables, and the only engine writing the
lake, Spark, already spends most of each 5-minute slot on the ingest. One
[Trino](glossary.md#engines) node now runs beside Spark: it serves SQL over every lake table and
builds the [marts](glossary.md#mart) with [dbt](glossary.md#dbt-and-models), while Spark keeps
every job that writes the lake, because those need Kafka offsets, the
[writer lock](glossary.md#writer-lock-and-exit-75) or code per record. A mart is a `SELECT` over
lake tables, held to [data tests](glossary.md#data-test) and [unit tests](glossary.md#unit-test)
rather than the three-engine [parity](glossary.md#parity-three-way-and-pinned) gold has. The cost
is one more always-on JVM, and the Outcome records the first memory limit it hit: a whole-table
[source test](glossary.md#source-test) exceeded [Trino's per-node query memory](glossary.md#trino-per-node-query-memory)
and was narrowed to the newest 6 hours.

**[ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md), one producer lake and a
warehouse per domain.** Once Trino can write, one stray commit to the lake, such as rolling a
table back to an older [snapshot](glossary.md#snapshot), would make the ingest skip Kafka offsets
for good. So the [producer lake](glossary.md#producer-lake) stays one
[warehouse](glossary.md#warehouse-catalog-and-schema), written by Spark alone and read by Trino
through a [read-only connector](glossary.md#read-only-connector), and every
[data domain](glossary.md#data-domain-and-data-product) gets its own warehouse, Trino catalog and
[writer](glossary.md#domain-writer) `dbt_<domain>`, all generated from `contracts/domains.yaml`.
A writer can change only its own catalog, so a mart that joins two domains is owned and stored by
the one that consumes it. Two limits are stated, not hidden: identity is an unchecked header, and
[table procedures](glossary.md#table-procedure) inside the domain catalogs are open to every user.

**[ADR-045](ADR-045-ceiling-25-cpu-48-gib.md), the ceiling is 25 CPU / 48 GiB.** Adding Trino
pushed the declared resource limits past the previous 20 CPU / 36 GiB
[ceiling](glossary.md#the-ceiling) at the [bootstrap peak](glossary.md#bootstrap-peak), the
trigger ADR-031 had set for revisiting it. The ceiling is now 25 CPU / 48 GiB, enough for the
next planned phase on its own estimate, with the platform kept as one always-on whole rather
than opt-in profiles. The ceiling is a sum of declared limits inside the Docker VM, and actual use
is far below it. The ADR also claimed the host kept a comfortable margin beyond the limits; that
was wrong, and its [Outcome](ADR-045-ceiling-25-cpu-48-gib.md#outcome-2026-11-26-the-host-margin-was-wrong)
says why: the [Docker VM](glossary.md#docker-vm-and-its-memory-setting) grows to its whole memory
setting whatever the containers use, the host also runs a desktop, and on 2026-11-25 the host's
[out-of-memory killer](glossary.md#host-out-of-memory-kill) killed the VM. The VM's setting plus
everything else the host runs must stay under the host's RAM.

**[ADR-046](ADR-046-governance-domain-and-quality-results.md), one place for quality results.**
Quality results lived in two places that could not see each other: the lake's audits in
[`audit.checks`](glossary.md#auditchecks-and-the-nightly-audits), and dbt's test results, which
were printed to a log and lost. Governance becomes a fifth domain, stored like the others; each
domain's dbt runs record their own test results in their own catalog, as their own writer,
through an [on-run-end hook](glossary.md#on-run-end-hook); and one
[definer view](glossary.md#definer-view) in the governance catalog unions those tables with
`audit.checks`. No writer ever writes outside its own catalog; the only step that crosses
domains is a read. The catch: a test whose Airflow task never started leaves no row, so "no
failures" in the view does not mean "everything passed".

**[ADR-047](ADR-047-ingest-batch-audit-after-publish.md), the ingest audits each batch after
committing it.** [Stage 1](glossary.md#stage-1) commits each batch together with the Kafka
offsets it read ([exactly-once](glossary.md#exactly-once-via-snapshot-offsets)), and the next
run starts after them, so a record the offsets claim but the commit lacks is never read again;
the only check was nightly, by when Redpanda may have [evicted](glossary.md#raw-retention-and-eviction)
it. Now a [batch audit](glossary.md#batch-audit) checks, right after each commit, that the commit
holds exactly the planned range, and a mismatch is a recorded finding and a critical alert. The
audit never blocks the commit, which is why this is not
[write-audit-publish](glossary.md#write-audit-publish): holding a batch would hold the offsets
while retention keeps deleting unread records. A hole is found within one run, while the
records can still be re-read; a clean batch leaves no row.

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
| [ADR-010](ADR-010-resource-budget.md) | [ADR-034](ADR-034-airflow-3-replaces-prefect.md) | the 16 CPU / 40 GB ceiling only, now 20 CPU / 36 GiB at the bootstrap peak; the allocation method and the Outcome record stand |
| [ADR-034](ADR-034-airflow-3-replaces-prefect.md) | [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md) | the 20 CPU / 36 GiB ceiling only, now 25 CPU / 48 GiB at the bootstrap peak; Airflow and everything else in ADR-034 stand |
| [ADR-033](ADR-033-data-mesh-four-domains.md) | [ADR-046](ADR-046-governance-domain-and-quality-results.md) | the line "the platform owns the road, not the products", for the governance domain only; the four domains, their owners and the registry stand |
| [ADR-008](ADR-008-eliminate-prefect-orchestration.md), [ADR-014](ADR-014-spark-based-iceberg-offload.md), [ADR-017](ADR-017-iceberg-maintenance-pipeline.md), [ADR-018](ADR-018-v3-lake-first-rust-capture.md) | [ADR-034](ADR-034-airflow-3-replaces-prefect.md) | orchestration only: Prefect as the scheduler of the lake jobs, and ADR-018's "Prefect is retained". Airflow 3 runs the same `docker exec` dispatch from the 2026-11 cutover; Spark and the jobs themselves stand |

Every number in [ADR-018's follow-on table](ADR-018-v3-lake-first-rust-capture.md#follow-on-adrs)
is now written, one of them not as that table planned it. **026** was pencilled in for
OHLCV-computed-on-read plus the `ReplacingMergeTree` dedup contract; the number went instead
to the four-layer lake decision on 2026-08-27, and the on-read contract has no ADR of its
own — it lives in [`10-clickhouse-gold.md`](../architecture/10-clickhouse-gold.md) and the
comments of `docker/clickhouse/ddl/10-gold-tables.sql`. Numbers are never reused or
renumbered, so the plan's label is left wrong rather than shuffled.

---

## Supporting documents

| Document | Description |
|----------|-------------|
| [../research/2026-02-09-v2-investment-analysis.md](../research/2026-02-09-v2-investment-analysis.md) | Pre-build risk/reward ranking of each proposed change (2026-02-09) — predictions left unedited |
| [../MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md) | What those predictions were actually worth, measured after the build |

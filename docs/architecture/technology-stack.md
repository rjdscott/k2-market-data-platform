# Technology Stack

Every component that runs, the version pinned, what it does here, and the decision record that chose it. Versions are read from [`docker-compose.yml`](../../docker-compose.yml), the Dockerfiles under `docker/`, and [`services/feed-handler-kotlin/build.gradle.kts`](../../services/feed-handler-kotlin/build.gradle.kts).

## Infrastructure

| Component | Version | Role | ADR |
|---|---|---|---|
| Redpanda | v25.3.4 | Streaming backbone. Kafka API, single broker, `--smp 1 --memory 1500M`. Schema registry is built in — no separate registry process | [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) |
| Redpanda Console | v3.5.1 | Topic / consumer-group / schema browser on `:8080` | [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) |
| ClickHouse | 24.3-alpine (LTS) | Hot store *and* stream processor. Kafka-engine tables + materialized views implement the whole medallion | [ADR-003](../adr/ADR-003-clickhouse-warm-storage.md), [ADR-009](../adr/ADR-009-medallion-in-clickhouse.md), [ADR-015](../adr/ADR-015-clickhouse-lts-downgrade.md) |
| Apache Spark | 3.5.5 (`tabulario/spark-iceberg:3.5.5_1.8.1`) | Batch only — reads ClickHouse over JDBC, appends to Iceberg. No streaming jobs. v2 offload verified working on this base — `docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py --source-table k2.bronze_trades_binance ...` printed `Rows offloaded: 2,526`, 2026-08-26 | [ADR-006](../adr/ADR-006-spark-batch-only.md), [ADR-014](../adr/ADR-014-spark-based-iceberg-offload.md) |
| Apache Iceberg | 1.8.1 | Cold-tier table format. v2: Hadoop (file) catalog, Parquet + zstd level 3, 128 MB target files | [ADR-007](../adr/ADR-007-iceberg-cold-storage.md), [ADR-013](../adr/ADR-013-pragmatic-iceberg-version-strategy.md) |
| Lakekeeper | v0.13.3 (`quay.io/lakekeeper/catalog`) | v3 Iceberg REST catalog on `127.0.0.1:18181`; metadata in a `lakekeeper` PostgreSQL database. Not yet wired to the v2 offload, which still writes the Hadoop-catalog bind mount | [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) |
| MinIO | RELEASE.2025-09-07T16-13-09Z | S3-compatible object store. Holds the v3 lake bucket (`k2-lake`); the v2 offload still writes to the bind-mounted `docker/iceberg/warehouse` instead | [ADR-007](../adr/ADR-007-iceberg-cold-storage.md), [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) |
| PostgreSQL | 15-alpine | Prefect metadata **and** the `offload_watermarks` / `maintenance_audit_log` tables that make the offload idempotent | [ADR-014](../adr/ADR-014-spark-based-iceberg-offload.md) |
| Prefect | 3 (`prefecthq/prefect:3-python3.12`) | Schedules the 15-minute offload and the daily maintenance flow. Workers, not agents | [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md), [ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md) |
| Prometheus | v3.2.0 | Scrapes capture ×3, ClickHouse, Redpanda, Grafana, the offload exporter. 30-day retention, 24 alert rules (14 v2 + 10 capture) | — |
| Grafana | 11.5.0 | 4 provisioned dashboards in `docker/grafana/dashboards/` | — |
| Docker Compose | — | The only deployment target. One file, 15 long-running service entries (+4 one-shot init containers), explicit CPU/memory limits per service | [ADR-010](../adr/ADR-010-resource-budget.md) |

## Feed handler (JVM)

| Library | Version | Role |
|---|---|---|
| Kotlin | 2.3.10 | Language; JVM toolchain 21, runtime `eclipse-temurin:21-jre-alpine` |
| kotlinx-coroutines | 1.10.1 | Concurrency model for the WebSocket read loop and producer dispatch |
| Ktor client | 3.1.0 | WebSocket client (CIO engine) against Binance, Kraken and Coinbase |
| Ktor server | 3.1.0 | Netty server exposing `/metrics` and `/health` on `:8082` |
| kafka-clients | 4.1.0 | Producer |
| kafka-avro-serializer | 7.8.2 | Confluent serializer against the Redpanda schema registry |
| Apache Avro | 1.12.0 | `NormalizedTrade` record format |
| kaml | 0.67.0 | Parses [`config/instruments.yaml`](../../config/instruments.yaml) |
| Micrometer Prometheus registry | 1.14.5 | Metrics — package is `io.micrometer.prometheusmetrics` in this version |
| kotlin-logging / Logback | 7.0.3 / 1.5.16 | Structured logging |

Build is a multi-stage Dockerfile: `gradle:8.12-jdk21` produces a fat JAR, the runtime stage is a JRE-only Alpine image. `./gradlew` is checked in — see [development/testing.md](../development/testing.md) for how tests are run.

## Batch / offload (Python)

| Library | Version | Role |
|---|---|---|
| PySpark | 3.5.5 (from base image) | Offload driver in `docker/offload/offload_generic.py` |
| clickhouse-jdbc | 0.4.6 (`-all` fat jar) | Baked into the Spark image at build time so no job hits Maven Central at runtime |
| psycopg2-binary | 2.9.9 | Watermark reads/writes against PostgreSQL |
| prometheus-client | 0.21.1 | Offload metrics exporter, running in the `iceberg-metrics` service |

## Choices worth defending

**Redpanda over Kafka.** Kafka plus a Confluent Schema Registry was 2.0 CPU / 2.77 GB before a single message moved, and its JVM GC pauses showed up in p99. Redpanda is a single C++ binary with the registry built in, Kafka-wire-compatible so the standard `kafka-clients` producer is unchanged. Cost: one broker, no replication, and a smaller operational-knowledge pool.

**ClickHouse as the stream processor, not just the store.** The transforms in this pipeline are stateless per-record maps and time-bucketed aggregates. ClickHouse already ingests from Kafka and already maintains incremental aggregates; adding a stream-processing framework to feed it would have been a second copy of machinery already present. This deleted five Spark Streaming jobs and a planned Kotlin service.

**Kotlin over Python for ingestion.** Not the throughput number — the exchanges do not send 5,000 msg/s. It is one typed normalizer per exchange with a compile-time contract against the Avro schema, in a runtime that costs 134 MiB. The measured 0.034 CPU for Binance is the same order as the Python handler it replaced; the win is the type system, not the CPU.

**Spark kept, batch only.** Spark was the thing being deleted, and it survived — for one job. ClickHouse → Iceberg needs a JDBC reader and a battle-tested Iceberg writer, and Spark has both. Writing a Kotlin service against the Iceberg Java SDK would have been 500+ lines and another JVM to keep alive; the Spark container idles at near-zero and wakes every 15 minutes.

**Hadoop catalog over Iceberg REST.** The REST catalog was in the design and lost a day to version incompatibilities. The file-based Hadoop catalog worked in ten minutes and, on a single host with one writer, gives up nothing but multi-engine metadata sharing. Swapping the `warehouse` property is the migration when that stops being true.

**Prefect kept, repurposed.** [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md) argued for deleting it, and was right about the workload it described — five cron-triggered OHLCV jobs that materialized views absorbed entirely. What replaced that workload was a batch offload that needs retries, run history, concurrency control and a place to see failures, which is Prefect's actual job. See [MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md).

## Not in the stack

No API framework, no query engine beyond ClickHouse and Spark SQL, no Alertmanager, no Kubernetes, no service mesh, no distributed tracing. The v1 stack's Kafka, Confluent Schema Registry, Spark Structured Streaming, DuckDB, FastAPI and Kafka UI are all gone; that codebase is archived at [`legacy/v1/`](../../legacy/v1/README.md).

# Technology Stack

Every component that runs, the version pinned, what it does here, and the decision record that chose it. Versions are read from [`docker-compose.yml`](../../docker-compose.yml), the Dockerfiles under `docker/`, and [`services/capture-rust/Cargo.toml`](../../services/capture-rust/Cargo.toml).

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

## Capture tier (Rust)

[`services/capture-rust/`](../../services/capture-rust/README.md) — one `k2-capture` binary, run once per exchange. Toolchain pinned to **1.98.0** in `rust-toolchain.toml`, edition 2024, so the container build, CI and a local `cargo` all agree.

| Crate | Version | Role |
|---|---|---|
| tokio | 1.48 | `current_thread` runtime — one connection per process, and a single-threaded scheduler makes frame order under a cgroup quota the same order a replay sees |
| tokio-tungstenite | 0.28 | WebSocket client, `rustls-tls-webpki-roots` |
| rdkafka | 0.39 | Producer. Features `cmake-build`, `libz-static`, `zstd`, `tokio` — not the defaults: distroless/cc has no `libz.so.1` and `zstd` is not in librdkafka's default set |
| schema_registry_converter | 5.0 | Confluent framing (magic byte + schema id) against Redpanda's registry; `rustls_tls` so the image carries one TLS stack, not two |
| apache-avro | 0.22 | Record encoding for `trade`, `book-snapshot-l2`, `raw-message` |
| serde / serde_json / serde_yaml | 1 / 1 / 0.9 | Frame parsing and the [`config/instruments.yaml`](../../config/instruments.yaml) loader |
| crc32fast | 1.5 | Kraken's book checksum, computed over decimal digit strings — never `f64` |
| metrics / metrics-exporter-prometheus | 0.24 / 0.17 | `k2_capture_*` exposition on `:8082` |
| clap | 4 | Subcommands `run`, `record`, `healthcheck` |
| anyhow | 1 | The only error type: every error is either propagated with context or counted and dropped, so a `thiserror` enum would be a type nobody reads |

Build is a multi-stage Dockerfile: `cargo-chef` caches the dependency layer, the runtime stage is `gcr.io/distroless/cc-debian12:nonroot` at ~43 MB. `[profile.release]` sets `lto="thin"`, `codegen-units=1`, `panic="abort"`, `strip=true`. The build context is the **repository root**, not the crate — `src/record.rs` compiles the wire contract in with `include_str!("../../../schemas/avro/trade.avsc")`. See [development/testing.md](../development/testing.md) for how tests are run.

> The v2 JVM feed handlers (Kotlin 2.3.10, Ktor 3.1.0, `kafka-clients` 4.1.0, Micrometer) that occupied this section retired on 2026-08-26 ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). Their dependency set is in [`legacy/v2-kotlin/build.gradle.kts`](../../legacy/v2-kotlin/build.gradle.kts).

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

**Rust for the capture tier, and not for speed.** K2 reads public WebSocket feeds over the open internet; transit dominates everything the process does by two orders of magnitude, and at ~150 msg/s capture is not the bottleneck. What Rust buys is three properties of the frame path that the JVM tier could not be extended into: `recv_ts_ns` before the parser, exact fixed-point arithmetic without a decimal library on the hot path, and bit-for-bit replay determinism (no `HashMap` iteration on emit paths, no `f64` on the record path, no wall-clock read outside the receipt stamp). It also replaced three JVMs with three ~43 MB containers. The full argument, including why Go and Python lose on the same two properties, is [ADR-019](../adr/ADR-019-rust-capture-tier.md); the Kotlin decision it supersedes is [ADR-002](../adr/ADR-002-kotlin-feed-handlers.md).

**Spark kept, batch only.** Spark was the thing being deleted, and it survived — for one job. ClickHouse → Iceberg needs a JDBC reader and a battle-tested Iceberg writer, and Spark has both. Writing a Kotlin service against the Iceberg Java SDK would have been 500+ lines and another JVM to keep alive; the Spark container idles at near-zero and wakes every 15 minutes.

**Hadoop catalog over Iceberg REST.** The REST catalog was in the design and lost a day to version incompatibilities. The file-based Hadoop catalog worked in ten minutes and, on a single host with one writer, gives up nothing but multi-engine metadata sharing. Swapping the `warehouse` property is the migration when that stops being true.

**Prefect kept, repurposed.** [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md) argued for deleting it, and was right about the workload it described — five cron-triggered OHLCV jobs that materialized views absorbed entirely. What replaced that workload was a batch offload that needs retries, run history, concurrency control and a place to see failures, which is Prefect's actual job. See [MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md).

## Not in the stack

No API framework, no query engine beyond ClickHouse and Spark SQL, no Alertmanager, no Kubernetes, no service mesh, no distributed tracing. The v1 stack's Kafka, Confluent Schema Registry, Spark Structured Streaming, DuckDB, FastAPI and Kafka UI are all gone; that codebase is archived at [`legacy/v1/`](../../legacy/v1/README.md).

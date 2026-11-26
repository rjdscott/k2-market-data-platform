# A1. Technology Stack

> **You will learn** every component, its pinned version, its job, and the ADR that chose it.
> **Read this if** reference; no reading order.
> **Before this** nothing.

Every component that runs, the version pinned, what it does here, and the decision record that chose it. The v3 lake ingest, its lake tables, its marts and its notebooks retired on 2026-11-26 ([ADR-056](../adr/ADR-056-unified-data-product-platform.md); archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/)): the components stay and their versions with them, and the rows below say where a job they ran retired. Versions are read from [`docker-compose.yml`](../../docker-compose.yml), the Dockerfiles under `docker/`, [`services/capture-rust/Cargo.toml`](../../services/capture-rust/Cargo.toml), and [`dbt/pyproject.toml`](../../dbt/pyproject.toml).

## Infrastructure

| Component | Version | Role | ADR |
|---|---|---|---|
| Redpanda | v25.3.4 | Streaming backbone. Kafka API, single broker, `--smp 1 --memory 1500M`. Schema registry is built in, no separate registry process | [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) |
| Redpanda Console | v3.5.1 | Topic / consumer-group / schema browser on `:8080` | [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) |
| ClickHouse | 24.3-alpine (LTS) | Paused behind the compose profile `hot-gold` while the platform is built ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)); the image tag stays, because `make test-clickhouse` runs the gold DDL on it in CI. Served `gold` only: `ReplacingMergeTree` fed by AvroConfluent Kafka engines, candles on read, history pulled from lake gold by `iceberg()` until lake gold retired, no TTL | [ADR-025](../adr/ADR-025-clickhouse-derived-hot-tier.md), [ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md), [ADR-015](../adr/ADR-015-clickhouse-lts-downgrade.md) |
| Apache Spark | 3.5.9 (the official `spark:3.5.9-scala2.12-java17-python3-ubuntu`, built as `k2-spark-iceberg:3.5.9_1.8.1`) | Batch only. Until the v3 lake ingest retired it read Redpanda by offset range, decoded Confluent-framed Avro and appended to Iceberg; the container runs no job until the platform plan adds one. No streaming jobs, and nothing reads ClickHouse. The base is pinned by digest, and the image bakes the Iceberg runtime and AWS bundle and the five Kafka/Avro jars it needs, each pinned by sha256, so no job resolves from Maven at runtime; it runs as a non-root user, with only the History Server between jobs ([`docker/spark/Dockerfile`](../../docker/spark/Dockerfile)) | [ADR-006](../adr/ADR-006-spark-batch-only.md), [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) |
| Apache Iceberg | 1.8.1 | Lake table format. The retired v3 tables were Parquet + zstd, copy-on-write deletes on every table, `commit.retry.num-retries=10` ([`lake.sql`, archived](../../legacy/v3-lake-ingest/lake/ddl/lake.sql)) | [ADR-007](../adr/ADR-007-iceberg-cold-storage.md), [ADR-013](../adr/ADR-013-pragmatic-iceberg-version-strategy.md) |
| Lakekeeper | v0.13.9 (`quay.io/lakekeeper/catalog`) | The Iceberg REST catalog, on `127.0.0.1:18181`; metadata in a `lakekeeper` PostgreSQL database. Sole catalog since Phase D, the v2 Hadoop-catalog bind mount is deleted | [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md), [ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md) |
| Trino | 484 (`trinodb/trino:484`) | SQL over the lake: one node on the Lakekeeper REST catalog with the static MinIO keys, `127.0.0.1:8088`. Catalog `lake` (warehouse `k2`) is read-only at the connector; it writes only the per-domain catalogs and warehouses generated from `contracts/domains.yaml`, each by its own writer identity; identity is the `X-Trino-User` header, not authenticated ([`docker/trino/`](../../docker/trino/README.md)) | [ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md), [ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md) |
| MinIO | RELEASE.2025-09-07T16-13-09Z | S3-compatible object store. Holds one bucket, `k2-lake`: the lake's warehouse `k2` and one mart warehouse per domain, each under its own key prefix `warehouse/<name>` ([chapter 19](19-lakehouse-and-domain-warehouses.md)) | [ADR-007](../adr/ADR-007-iceberg-cold-storage.md), [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) |
| PostgreSQL | 15.19-alpine, by digest | One server, container `k2-prefect-db`, with three databases: Airflow's metadata, Lakekeeper's catalog, and the default database `PREFECT_DB_NAME` names, where the retired scheduler kept its run state (the service and variable names are historical, kept by ADR-034). No pipeline bookkeeping of its own: the retired lake ingest kept its consumed offsets in the Iceberg snapshot summary | [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md), [ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md), [ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md) |
| Airflow | 3.3.2 (`apache/airflow:slim-3.3.2-python3.12`, built as `k2-airflow:3.3.2`) | The one scheduler. Its four v3 DAGs retired with the lake ingest and are archived: `lake_ingest` every 5 minutes, `lake_maintenance` nightly, `lake_rebuild` by hand, each a `docker exec` into the Spark container, and `dbt_marts` hourly, rendered from the dbt project by astronomer-cosmos 1.16.0 ([`docker/airflow/`](../../docker/airflow/Dockerfile)). No DAG runs until the plan adds one | [ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md), [ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md) |
| Prometheus | v3.2.0 | Scrapes capture ×3, Redpanda, Grafana, cAdvisor and `airflow-statsd`. 30-day retention; the alert rules are the files in `docker/prometheus/rules/`, the lake's retired with the ingest | |
| Grafana | 11.5.0 | The dashboards provisioned from `docker/grafana/dashboards/`; the lake's retired with the ingest | |
| Docker Compose | | The only deployment target. One file: the default profile's long-running services and one-shot init containers, ClickHouse behind `hot-gold`, explicit CPU/memory limits per service, and a bootstrap peak under a 25 CPU / 48 GiB ceiling; the counts and the peak are what `python3 scripts/check-budget.py` prints | [ADR-010](../adr/ADR-010-resource-budget.md), [ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md) |

## Capture tier (Rust)

[`services/capture-rust/`](../../services/capture-rust/README.md), one `k2-capture` binary, run once per exchange. Toolchain pinned to **1.98.0** in `rust-toolchain.toml`, edition 2024, so the container build, CI and a local `cargo` all agree.

| Crate | Version | Role |
|---|---|---|
| tokio | 1.48 | `current_thread` runtime, one connection per process, and a single-threaded scheduler makes frame order under a cgroup quota the same order a replay sees |
| tokio-tungstenite | 0.30 | WebSocket client, `rustls-tls-webpki-roots` |
| rdkafka | 0.39 | Producer. Features `cmake-build`, `libz-static`, `zstd`, `tokio`, not the defaults: distroless/cc has no `libz.so.1` and `zstd` is not in librdkafka's default set |
| schema_registry_converter | 5.0 | Confluent framing (magic byte + schema id) against Redpanda's registry; `rustls_tls` so the image carries one TLS stack, not two |
| apache-avro | 0.22 | Record encoding for `trade`, `book-snapshot-l2`, `raw-message` |
| serde / serde_json / serde_yaml | 1 / 1 / 0.9 | Frame parsing and the [`config/instruments.yaml`](../../config/instruments.yaml) loader |
| crc32fast | 1.5 | Kraken's book checksum, computed over decimal digit strings, never `f64` |
| metrics / metrics-exporter-prometheus | 0.24 / 0.18 | `k2_capture_*` exposition on `:8082` |
| clap | 4 | Subcommands `run`, `record`, `healthcheck` |
| anyhow | 1 | The only error type: every error is either propagated with context or counted and dropped, so a `thiserror` enum would be a type nobody reads |

Build is a multi-stage Dockerfile: `cargo-chef` caches the dependency layer, the runtime stage is `gcr.io/distroless/cc-debian12:nonroot` at ~43 MB. `[profile.release]` sets `lto="thin"`, `codegen-units=1`, `panic="abort"`, `strip=true`. The build context is the **repository root**, not the crate, `src/record.rs` compiles the wire contract in with `include_str!("../../../schemas/avro/trade.avsc")`. See [development/testing.md](../development/testing.md) for how tests are run.

> The v2 JVM feed handlers (Kotlin 2.3.10, Ktor 3.1.0, `kafka-clients` 4.1.0, Micrometer) that occupied this section retired on 2026-08-26 ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). Their dependency set is in [`legacy/v2-kotlin/build.gradle.kts`](../../legacy/v2-kotlin/build.gradle.kts).

## Batch / lake (Python)

| Library | Version | Role |
|---|---|---|
| PySpark | 3.5.9 (from base image) | Driver for the two v3 lake jobs, `ingest.py` and `maintenance.py`, both retired and archived in `legacy/v3-lake-ingest/lake/` |
| spark-sql-kafka-0-10 / spark-avro | 3.5.9 | The Redpanda read and the Avro decode. Baked into the Spark image with kafka-clients 3.4.1 and commons-pool2 2.11.1, the versions `spark-parent_2.12:3.5.9` declares |
| prometheus-client | 0.26.0 | The lake exporter `metrics.py` and its `lake-metrics` service, in an image of its own on `python:3.15.1-slim-trixie`, retired with the ingest ([the Dockerfile, archived](../../legacy/v3-lake-ingest/lake-metrics/Dockerfile)) |

The v3 lake jobs deliberately depended on nothing else: `metrics.py` read table metadata over the
catalog's REST API with `urllib` rather than PyIceberg, whose `PyArrowFileIO` costs 122 MB of
RSS on import alone against a 128 MB container limit.

## Marts (dbt on Trino)

[`dbt/`](../../dbt/) is its own uv project; the Airflow image installs it into a
virtualenv of its own from `dbt/uv.lock`, so dbt's pins never meet Airflow's. The marts it
built retired with the v3 lake ingest; the project stays, with no mart until the plan adds one.

| Library | Version | Role |
|---|---|---|
| dbt-core | 1.12.7 | Builds each mart from one `SELECT`, with its tests, as the domain's writer `dbt_<domain>` |
| dbt-trino | 1.12.0 | The adapter: every model runs as SQL on Trino 484 |
| astronomer-cosmos | 1.16.0 | Rendered the dbt project into the Airflow DAG `dbt_marts`, one task per node, until the DAG retired |
| dbt_expectations, dbt_date | 0.10.10, 0.21.0 (`dbt/packages.yml`) | Generic data tests, such as the generated uniqueness test on a source's identifier; installed at image build, never at task time |
| sqlfluff | 4.5.0 (dev) | Lints the models in `make test-dbt` and CI |

## Choices worth defending

**Redpanda over Kafka.** Kafka plus a Confluent Schema Registry was 2.0 CPU / 2.77 GB before a single message moved, and its JVM GC pauses showed up in p99. Redpanda is a single C++ binary with the registry built in, Kafka-wire-compatible so the standard `kafka-clients` producer is unchanged. Cost: one broker, no replication, and a smaller operational-knowledge pool.

**ClickHouse served, the lake computed.** v2 ran the medallion inside ClickHouse ([ADR-009](../adr/ADR-009-medallion-in-clickhouse.md)), which made the serving DB the record and put per-block `argMin` candles in the archive; v3 kept ClickHouse for sub-second dashboards and `ASOF JOIN` backtests over gold, and computed every product once in the lake ([ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)), until ClickHouse was paused and the lake ingest retired.

**Rust for the capture tier, and not for speed.** K2 reads public WebSocket feeds over the open internet; transit dominates everything the process does by two orders of magnitude, and at ~150 msg/s capture is not the bottleneck. What Rust buys is three properties of the frame path that the JVM tier could not be extended into: `recv_ts_ns` before the parser, exact fixed-point arithmetic without a decimal library on the hot path, and bit-for-bit replay determinism (no `HashMap` iteration on emit paths, no `f64` on the record path, no wall-clock read outside the receipt stamp). It also replaced three JVMs with three ~43 MB containers. The full argument, including why Go and Python lose on the same two properties, is [ADR-019](../adr/ADR-019-rust-capture-tier.md); the Kotlin decision it supersedes is [ADR-002](../adr/ADR-002-kotlin-feed-handlers.md).

**Spark kept, batch only.** Spark was the thing being deleted, and it survived, for one path. Redpanda → Iceberg needs a bounded Kafka reader, an Avro decoder and a battle-tested Iceberg writer, and Spark has all three in one session. Writing a Kotlin service against the Iceberg Java SDK would have been 500+ lines and another JVM to keep alive; the Spark container idled at near-zero and woke every five minutes until the v3 ingest retired.

**Iceberg REST, on the second attempt.** v2 ran a file-based Hadoop catalog because REST cost a day of version fights and bought nothing on one node ([ADR-013](../adr/ADR-013-pragmatic-iceberg-version-strategy.md)). It did not survive the lake becoming the system of record: a file catalog has no atomic commit, no multi-writer, and ClickHouse cannot read it. Lakekeeper replaced it ([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)), and the bind-mounted warehouse went with the v2 offload.

**An orchestrator kept, repurposed, then replaced.** [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md) argued for deleting the orchestrator, and was right about the workload it described, five cron-triggered OHLCV jobs that materialized views absorbed entirely. What replaced that workload is a batch ingest that needs retries, run history, concurrency control and a place to see failures. See [MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md).
That job then moved to Airflow 3, the orchestrator the platform plan chose, with the same
dispatch ([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md)); the orchestrator before
it is archived in [`legacy/prefect/`](../../legacy/prefect/README.md).

**Trino beside Spark, not instead of it.** [ADR-006](../adr/ADR-006-spark-batch-only.md)
rejected Trino as a replacement for Spark's ETL, and that still holds: every job that needs
Kafka offsets, the writer lock or code per record stays in Spark. Trino does two other jobs:
it serves SQL over the lake to every client, through a catalog that cannot commit, and it is
the engine dbt builds the consumer marts on, outside the ingest's container
([ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)).

## Not in the stack

No API framework, no query engine beyond ClickHouse, Spark SQL, Trino and the in-process DuckDB the research notebooks read the lake with, archived with them ([`k2lake.py`](../../legacy/v3-lake-ingest/notebooks/k2lake.py)), no Alertmanager, no Kubernetes, no service mesh, no distributed tracing. The v1 stack's Kafka, Confluent Schema Registry, Spark Structured Streaming, FastAPI and Kafka UI are all gone, as is v1's DuckDB query layer; that codebase is archived at [`legacy/v1/`](../../legacy/v1/README.md).

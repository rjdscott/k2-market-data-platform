# Architecture — As Built

K2 is a single-host crypto market-data platform: three exchange WebSocket feeds land in Redpanda, ClickHouse turns them into a Bronze → Silver → Gold medallion using nothing but Kafka-engine tables and materialized views, and a Prefect-scheduled Spark job appends the results to Iceberg every 15 minutes. The v2 pipeline runs in one Docker Compose file on **15.1 CPU / 21.875 GB across 14 long-lived containers** (+2 one-shot init containers), against a mandate of 16 cores / 40 GB. This branch also carries v3 foundations (Lakekeeper: +0.25 CPU / +256 MB, plus 2 more one-shot init containers), so what is actually deployed here is **15.35 CPU / 22.125 GB across 15 containers (+4 one-shot)**.

Everything below describes what actually runs on `main` today. Where the design intent and the built system diverge, the divergence is called out rather than papered over. The story of how it got here is in [MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md).

---

## System diagram

```mermaid
flowchart LR
    subgraph EX["Exchanges"]
        BIN["Binance<br/>12 pairs"]
        KRK["Kraken<br/>11 pairs"]
        CBS["Coinbase<br/>11 pairs"]
    end

    subgraph FH["Ingestion · Kotlin 2.3 + Ktor 3.1"]
        FHB["feed-handler-binance"]
        FHK["feed-handler-kraken"]
        FHC["feed-handler-coinbase"]
    end

    RP["Redpanda v25.3.4<br/>v2: 6 topics · 160 partitions<br/>+v3: 9 topics · 108 partitions<br/>built-in schema registry"]

    subgraph CH["Hot store · ClickHouse 24.3 LTS"]
        Q["3 Kafka-engine queues<br/>JSONAsString"]
        BR["3x bronze_trades_*<br/>MergeTree · TTL 7d"]
        SI["silver_trades<br/>MergeTree · TTL 30d"]
        GO["6x ohlcv_*<br/>AggregatingMergeTree"]
    end

    subgraph BATCH["Orchestration + batch"]
        PF["Prefect 3<br/>server · worker · Postgres"]
        SP["Spark 3.5 + Iceberg<br/>JDBC read, append write"]
    end

    ICE["Iceberg cold.*<br/>10 tables · Hadoop catalog<br/>Parquet + zstd"]

    subgraph OBS["Observability"]
        PR["Prometheus v3.2<br/>17 alert rules"]
        GR["Grafana 11.5<br/>4 dashboards"]
    end

    BIN -->|WebSocket| FHB
    KRK -->|WebSocket| FHK
    CBS -->|WebSocket| FHC
    FHB -->|"raw JSON + Avro"| RP
    FHK -->|"raw JSON + Avro"| RP
    FHC -->|"raw JSON + Avro"| RP
    RP --> Q
    Q -->|normalizing MV| BR
    BR -->|"3 MVs"| SI
    SI -->|"6 MVs"| GO
    PF -->|"cron */15"| SP
    BR -.->|JDBC| SP
    SI -.->|JDBC| SP
    GO -.->|JDBC| SP
    SP -->|append| ICE
    FHB -.->|"handlers :8082"| PR
    Q -.->|"ClickHouse :9363"| PR
    PR --> GR

    classDef exchange fill:#e5e7eb,stroke:#4b5563,color:#1f2937
    classDef kotlin fill:#c7d2fe,stroke:#4338ca,color:#1f2937
    classDef stream fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef ch fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef batch fill:#fed7aa,stroke:#c2410c,color:#1f2937
    classDef storage fill:#bae6fd,stroke:#0369a1,color:#1f2937
    classDef obs fill:#e9d5ff,stroke:#7e22ce,color:#1f2937

    class BIN,KRK,CBS exchange
    class FHB,FHK,FHC kotlin
    class RP stream
    class Q,BR,SI,GO ch
    class PF,SP batch
    class ICE storage
    class PR,GR obs
```

---

## Tiers

### Ingestion — Kotlin feed handlers

Three containers from one image (`services/feed-handler-kotlin/`), differing only by `K2_EXCHANGE`. Kotlin 2.3.10 on JVM 21, Ktor 3.1.0 WebSocket client, coroutines, `kafka-clients` 4.1.0 with the Confluent Avro serializer. Each handler subscribes to its instruments, then **dual-produces**: the untouched exchange payload to `market.crypto.trades.<exchange>.raw`, and a `NormalizedTrade` Avro record to `market.crypto.trades.<exchange>`.

- Instruments come from [`config/instruments.yaml`](../../config/instruments.yaml) — one file, all three exchanges, no per-service duplication.
- Reconnect is a fixed 5 s delay with unlimited retries (`reconnect-delay-ms`, `max-reconnect-attempts = -1` in `application.conf`) — the KDoc in the WebSocket clients still says "exponential backoff"; it isn't. Measured cost is ~0.03 CPU / 134 MiB for Binance at 100–200 trades/s, against a 0.5 CPU / 512 MB limit.
- Micrometer exposes `feed_handler_trades_produced_total`, `feed_handler_errors_total`, `feed_handler_reconnects_total` on `:8082/metrics`; `:8082/health` backs the Compose healthcheck.
- Why Kotlin over the v1 Python handlers: [ADR-002](../adr/ADR-002-kotlin-feed-handlers.md).

**As-built quirk:** the Bronze layer consumes the *raw JSON* topics, not the normalized Avro ones. The Avro path is live and schema-registered, but nothing downstream reads it today — normalization ended up in ClickHouse instead (see below). It is kept because it is the seam a non-ClickHouse consumer would attach to.

### Streaming backbone — Redpanda

Single-broker Redpanda v25.3.4 in `dev-container` mode, `--smp 1 --memory 1500M`, with the schema registry built in — no separate Confluent registry process ([ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md)).

Topics are created explicitly by the `redpanda-init` one-shot service rather than by auto-create, so partition counts are deterministic: 40 partitions for each Binance topic, 20 for each Kraken and Coinbase topic — v2: 6 topics, 160 partitions. This branch's v3 foundations add 9 more topics at 12 partitions each — `market.crypto.v3.{raw,trades,book}.<ex>` for each exchange — for +108 partitions, so `rpk topic list` shows 15 market topics / 268 partitions (plus `_schemas`). That job also hardens `_schemas` to `cleanup.policy=compact` with infinite retention, which fixed a real failure where the registry hit `offset_out_of_range` after a restart.

### Hot store — ClickHouse

ClickHouse 24.3 LTS ([ADR-015](../adr/ADR-015-clickhouse-lts-downgrade.md) — downgraded from 26.1 for Spark JDBC compatibility) is the whole stream processor. There is no stream-processing framework in this platform ([ADR-004](../adr/ADR-004-eliminate-spark-streaming.md), [ADR-009](../adr/ADR-009-medallion-in-clickhouse.md)).

Per exchange, the chain is four objects:

1. **Kafka-engine table** (`k2.trades_<exchange>_queue`) reading `JSONAsString` — one `message String` column, `kafka_flush_interval_ms = 7500`.
2. **Normalizing MV** — `JSONExtract*` + `toDecimal64(…, 8)` turns the exchange's JSON dialect into the shared bronze column set.
3. **Bronze table** — `MergeTree`, `PARTITION BY toYYYYMMDD(exchange_timestamp)`, `ORDER BY (symbol, exchange_timestamp, sequence_number)`, `TTL … + INTERVAL 7 DAY`.
4. **Silver MV** — `bronze_<exchange>_to_silver_mv` fans the per-exchange bronze into the single `silver_trades` table (canonical symbols, `is_valid`, `asset_class`).

Six more MVs read `silver_trades` and maintain `ohlcv_1m/5m/15m/30m/1h/1d` as `AggregatingMergeTree` tables keyed on `(exchange, canonical_symbol, window_start)`.

Separate bronze tables per exchange — rather than one normalized bronze — is [ADR-011](../adr/ADR-011-multi-exchange-bronze-architecture.md): native shapes survive to a layer you can diff against the exchange's own docs.

DDL lives in [`docker/clickhouse/ddl/01-k2-schema.sql`](../../docker/clickhouse/ddl/01-k2-schema.sql), auto-applied on a fresh volume (`docker/clickhouse/schema/` is the historical migration trail); see [schema-design.md](schema-design.md) for columns and [partitioning-strategy.md](partitioning-strategy.md) for the keys.

### Orchestration and batch — Prefect + Spark

Prefect 3 (`prefect-server`, `prefect-worker`, `prefect-db` on PostgreSQL 15) runs two deployments:

| Deployment | Cron | Does |
|---|---|---|
| `iceberg-offload-15min` | `*/15 * * * *` | Bronze (3 concurrent) → Silver (sequential) → Gold (6 concurrent) offload |
| `iceberg-maintenance-daily` | `0 2 * * *` | Compact (binpack, 128 MB) → expire snapshots (7 day) → row-count audit |

Both shell out to the shared `spark-iceberg` container. `docker/offload/offload_generic.py` reads ClickHouse over JDBC above a per-table watermark, appends to Iceberg, then advances the watermark in PostgreSQL `offload_watermarks` — so a killed run is a no-op and the next cycle resumes exactly where it stopped. Column lists are explicit in the flow, so a new ClickHouse column is ignored until it is added to both the flow and the Iceberg DDL.

Spark is batch-only ([ADR-006](../adr/ADR-006-spark-batch-only.md)); doing the offload in Spark rather than a bespoke Kotlin service is [ADR-014](../adr/ADR-014-spark-based-iceberg-offload.md).

### Cold store — Iceberg

10 tables under the `cold` namespace: 3 bronze, 1 silver, 6 gold. Bronze/silver partition by `days(...)`, gold by `months(window_start), exchange`; Parquet with zstd level 3, 128 MB target file size ([ADR-007](../adr/ADR-007-iceberg-cold-storage.md)).

**As-built divergence:** the catalog is the file-based **Hadoop catalog** over a bind-mounted warehouse (`docker/iceberg/warehouse` → `/home/iceberg/warehouse`), not the Iceberg REST catalog the original design assumed. The REST catalog cost a day of version-compatibility fights and bought nothing on a single node ([ADR-013](../adr/ADR-013-pragmatic-iceberg-version-strategy.md)). MinIO runs and holds S3 credentials for the S3FileIO path, but the offload writes to the local warehouse today — swapping the catalog `warehouse` property is the migration when this stops being a single host.

Two silver columns are absent from cold storage on purpose: `trade_conditions Array(String)` and `vendor_data Map(String,String)` cannot be deserialized by the Spark ClickHouse JDBC driver, so the Iceberg schema drops them.

### Observability

Prometheus v3.2 scrapes the three feed handlers (`:8082`), ClickHouse (`:9363`), Redpanda (`:9644`), and Grafana. Grafana 11.5 ships four provisioned dashboards in `docker/grafana/dashboards/`: pipeline overview, ClickHouse overview, Iceberg offload, v2 migration tracker.

**17 alert rules** are loaded from `docker/prometheus/rules/`: 3 feed-handler, 5 ClickHouse, 9 Iceberg-offload. The `iceberg-scheduler` Prometheus job scrapes the offload metrics exporter on `iceberg-metrics:8000`, so all 9 offload alerts have live series. One honest gap remains: no alert has been fire-tested end to end. There is no Alertmanager.

---

## Data model

| Layer | Table(s) | Shape |
|---|---|---|
| Bronze | `bronze_trades_binance`, `_kraken`, `_coinbase` | `exchange_timestamp, sequence_number, symbol, price Decimal(18,8), quantity, quote_volume, event_time, kafka_offset, kafka_partition, ingestion_timestamp` — identical across all three |
| Silver | `silver_trades` | `message_id, trade_id, exchange, symbol, canonical_symbol, asset_class, currency, price, quantity, quote_volume, side, timestamp, ingestion_timestamp, processed_at, source_sequence, platform_sequence, is_valid` (+ `trade_conditions`, `vendor_data`, `validation_errors`, hot tier only) |
| Gold | `ohlcv_{1m,5m,15m,30m,1h,1d}` | `exchange, canonical_symbol, window_start, open_time, open_price, close_time, close_price, high_price, low_price, volume, quote_volume, trade_count` |

Full field notes: [schema-design.md](schema-design.md).

---

## Trade lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant EX as Exchange WS
    participant FH as Feed handler
    participant RP as Redpanda
    participant KE as CH Kafka engine
    participant MV as CH MVs
    participant SP as Spark offload

    EX->>FH: trade JSON
    Note over FH: parse + normalize, sub-ms
    FH->>RP: raw JSON + Avro, ~2 ms produce
    RP->>KE: poll batch, flush every 7.5 s
    KE->>MV: normalizing MV to bronze
    MV->>MV: bronze to silver, sub-ms
    MV->>MV: silver to 6 OHLCV tables
    Note over EX,MV: measured p99 exchange to silver:<br/>191 / 197 / 170 ms
    SP-->>MV: JDBC read above watermark
    SP->>SP: append to Iceberg, advance watermark
    Note over SP: every 15 min, cold tier lag under 15 min
```

The end-to-end p99 is dominated by network RTT to the exchanges (~80 ms average), not by anything in the platform — bronze → silver is below the measurement resolution. Caveat on those numbers: they come from a 1-hour window on a cold-started stack with n ≈ 12–13 trades per exchange, and the 24-hour burn-in that would give them statistical weight is unfinished.

---

## Resource footprint

| Service | CPU limit | Memory limit |
|---|---|---|
| clickhouse | 4.0 | 8 GB |
| redpanda | 2.0 | 2 GB |
| spark-iceberg | 2.0 | 4 GB |
| prometheus | 1.0 | 2 GB |
| minio | 1.0 | 1 GB |
| prefect-db | 1.0 | 1 GB |
| prefect-server | 1.0 | 1 GB |
| redpanda-console | 0.5 | 256 MB |
| grafana | 0.5 | 512 MB |
| prefect-worker | 0.5 | 512 MB |
| feed-handler-binance | 0.5 | 512 MB |
| feed-handler-kraken | 0.5 | 512 MB |
| feed-handler-coinbase | 0.5 | 512 MB |
| iceberg-metrics | 0.1 | 128 MB |
| **Subtotal, v2 (14 services)** | **15.1** | **21.875 GB** |
| lakekeeper (v3) | 0.25 | 256 MB |
| **Total, as deployed on this branch (15 services)** | **15.35** | **22.125 GB** |

Only `lakekeeper` is new since the v2 baseline — +0.25 CPU / +256 MB. Four further entries — `redpanda-init` (v2, topic creation), `iceberg-init` (v2 cold.* Hadoop-catalog DDL), `lakekeeper-migrate` (v3 catalog DB schema) and `lake-init` (v3 bucket + warehouse + namespaces) — are one-shot containers that exit after startup and are not counted in the totals above; v2 alone carries 2 of these, this branch's v3 foundations add 2 more (4 total). Budget and reasoning: [ADR-010](../adr/ADR-010-resource-budget.md), [docs/operations/docker-resources.md](../operations/docker-resources.md).

| Forward look | [capacity-model.md](capacity-model.md) — msg/s per core, bytes/day per topic and lake table, and headroom against 16 CPU / 40 GB once the v3 capture tier lands. Predictions only, written before the burn-in that scores them. |
|---|---|

---

## What is not built

- **No query API.** Phase 8 is unstarted. Reads today are `clickhouse-client` / HTTP on `:8123` and Spark SQL against Iceberg. The Kotlin JVM query API proposed in [ADR-005](../adr/ADR-005-kotlin-spring-boot-api.md) was never written, and the ROI analysis that ranked it last is the reason.
- **No REST catalog.** Hadoop catalog on a bind mount, as above.
- **No Raw layer.** [ADR-009](../adr/ADR-009-medallion-in-clickhouse.md) specified a four-layer Raw → Bronze → Silver → Gold medallion. The built system has three: the Kafka-engine queue table plays the Raw role in flight, but nothing durably persists pre-normalization rows.
- **No Alertmanager, no alert fire test, no load testing above 1x.** The 5x/10x replay scenarios in the latency plan were never run.
- **Single broker, single ClickHouse node, single host.** No replication, no failover; recovery is container restart, verified by test rather than by design.

---

## v3 direction

The list above is what v2 chose not to build. Separately, an audit of the code found eight things v2 got *wrong* for research use — this is a quant-research platform on public internet feeds, not a trading path, and it still fails at completeness, correctness and reproducibility:

- The lake is a JDBC copy of ClickHouse, not the system of record ([`offload_generic.py:172`](../../docker/offload/offload_generic.py#L172)) — the archive inherits a serving DB's normalisation, TTL and dropped columns.
- OHLCV `SummingMergeTree` resolves open/high/low/close arbitrarily across merges ([`01-k2-schema.sql:178`](../../docker/clickhouse/ddl/01-k2-schema.sql#L178)) — a real correctness bug, not a rounding one.
- Bronze is plain `MergeTree` ([`01-k2-schema.sql:88`](../../docker/clickhouse/ddl/01-k2-schema.sql#L88)) — a topic replay duplicates every row.
- No receive timestamp before parse ([`TradeNormalizer.kt:28`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt#L28)) — clock skew and platform latency are inseparable.
- Kraken runs WS v1 with synthesised colliding trade IDs ([`TradeNormalizer.kt:60`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt#L60)).
- Coinbase `sequence_num` is parsed and never compared ([`CoinbaseWebSocketClient.kt:178`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/CoinbaseWebSocketClient.kt#L178)) — dropped messages are silent.
- The Avro schema puts `logicalType` beside `type` where Avro ignores it ([`normalized-trade.avsc:60`](../../schemas/avro/normalized-trade.avsc#L60)), and ClickHouse consumes raw JSON anyway ([`01-k2-schema.sql:39`](../../docker/clickhouse/ddl/01-k2-schema.sql#L39)).
- Trades only, no L2 book, and raw topics keyed by exchange name ([`KafkaProducerService.kt:155`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/KafkaProducerService.kt#L155)) pin two exchanges to a single partition.

v3 inverts the storage hierarchy — the lake becomes the system of record and everything else is derived and rebuildable — and replaces the Kotlin handlers with one Rust `k2-capture` binary per exchange doing trades and L2 book on a single connection:

```mermaid
flowchart LR
  EX["Exchanges · public WS<br/>Binance · Kraken · Coinbase"]
  CAP["k2-capture ×3 · Rust<br/>trades + L2 · recv_ts · seq · CRC32"]
  RP[("Redpanda<br/>Avro + registry")]
  IB[("Iceberg · Lakekeeper + MinIO<br/>system of record")]
  CH["ClickHouse hot tier<br/>derived · rebuildable · 7d TTL"]
  DD["DuckDB + PyIceberg<br/>notebooks"]
  GR["Grafana + Prometheus"]
  EX --> CAP --> RP
  RP -->|"Spark batch · offsets in snapshot"| IB
  RP --> CH
  IB -->|rebuild| CH
  IB --> DD
  CH --> GR
  CAP -.metrics.-> GR
```

Same 16 CPU / 40 GB single host ([ADR-010](../adr/ADR-010-resource-budget.md) holds). Phases, exit criteria and verify-first spikes: [the v3 plan](../plans/2026-08-26-v3-quant-research-platform/README.md). Decision and rejected alternatives: [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) (Proposed).

---

## Decision records

The full set is in [docs/adr/](../adr/). The ones that shaped this diagram:

| ADR | Decision |
|---|---|
| [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) | Kafka + Schema Registry → Redpanda |
| [ADR-002](../adr/ADR-002-kotlin-feed-handlers.md) | Python feed handlers → Kotlin |
| [ADR-003](../adr/ADR-003-clickhouse-warm-storage.md) | ClickHouse as the hot store |
| [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md) | Delete Spark Structured Streaming |
| [ADR-006](../adr/ADR-006-spark-batch-only.md) | Spark retained, batch only |
| [ADR-007](../adr/ADR-007-iceberg-cold-storage.md) | Iceberg as the cold tier |
| [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md) | Proposed dropping Prefect — **not adopted**, see the journey doc |
| [ADR-009](../adr/ADR-009-medallion-in-clickhouse.md) | Medallion as ClickHouse materialized views |
| [ADR-010](../adr/ADR-010-resource-budget.md) | The 16-core / 40 GB budget |
| [ADR-011](../adr/ADR-011-multi-exchange-bronze-architecture.md) | Bronze table per exchange |
| [ADR-013](../adr/ADR-013-pragmatic-iceberg-version-strategy.md) | Hadoop catalog over REST |
| [ADR-014](../adr/ADR-014-spark-based-iceberg-offload.md) | Spark offload, not a Kotlin service |
| [ADR-015](../adr/ADR-015-clickhouse-lts-downgrade.md) | ClickHouse 26.1 → 24.3 LTS |
| [ADR-016](../adr/ADR-016-add-coinbase-exchange.md) | Coinbase as the third exchange |
| [ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md) | Daily compaction + snapshot expiry |
| [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) | **Proposed** — v3: lake-first, Rust capture tier |

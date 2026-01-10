# K2 Market Data Platform - System Architecture

**Last Updated**: 2026-01-10
**Status**: Active
**Audience**: Staff/Principal Engineers, System Architects

---

## Executive Summary

This document provides a comprehensive visual architecture of the K2 Market Data Platform, including component interactions, data flow, latency budgets, and technology decisions. The platform implements a modern lakehouse architecture combining real-time streaming (Kafka) with ACID-compliant storage (Iceberg) for market data processing at scale.

**Design Goals**:
1. **Sub-500ms p99 latency** from exchange → query API
2. **Petabyte-scale storage** with ACID guarantees
3. **Time-travel queries** for compliance and backtesting
4. **Graceful degradation** under load (4-level cascade)
5. **Operational simplicity** (boring technology, embedded query engine)

---

## High-Level Architecture

```mermaid
graph TB
    subgraph "Data Producers"
        A1[Market Data Feeds<br/>ASX, NYSE, Binance]
        A2[Trading Venues]
        A3[Vendor Data<br/>Bloomberg, Refinitiv]
        A4[Internal Systems<br/>Quant Research]
    end

    subgraph "Ingestion Layer"
        B1[Kafka KRaft<br/>8.1.1]
        B2[Schema Registry<br/>8.1.1 HA]

        B1 -.->|Avro schema validation| B2
    end

    subgraph "Processing Layer"
        C1[Kafka Consumers<br/>Manual Commit]
        C2[Sequence Tracker<br/>Gap Detection]
        C3[Deduplication Cache<br/>24h Window]

        C1 --> C2
        C2 --> C3
    end

    subgraph "Storage Layer - Iceberg Lakehouse"
        D1[Apache Iceberg<br/>0.8.0]
        D2[MinIO S3 API<br/>Object Storage]
        D3[PostgreSQL 16<br/>Catalog + Audit]

        D1 -.->|Metadata| D3
        D1 -.->|Parquet Files| D2
    end

    subgraph "Query Layer"
        E1[DuckDB Engine<br/>1.4.0 OLAP]
        E2[FastAPI Server<br/>REST + OpenAPI]
        E3[Query CLI<br/>Typer + Rich]

        E1 --> E2
        E1 --> E3
    end

    subgraph "Observability"
        F1[Prometheus 3.9.1<br/>Metrics Collection]
        F2[Grafana 12.3.1<br/>Dashboards]
        F3[Structured Logging<br/>JSON + Correlation IDs]
    end

    A1 & A2 & A3 & A4 -->|< 10ms p99| B1
    B1 -->|< 20ms lag| C1
    C3 -->|Batch Write<br/>< 200ms p99| D1
    D1 -->|Zero-Copy Scan| E1
    E2 -->|< 500ms p99| Client[Client Applications]

    C1 & D1 & E2 -.->|RED Metrics| F1
    F1 --> F2

    style B1 fill:#e1f5fe
    style B2 fill:#e1f5fe
    style D1 fill:#f3e5f5
    style D2 fill:#f3e5f5
    style D3 fill:#f3e5f5
    style E1 fill:#e8f5e9
    style E2 fill:#e8f5e9
    style F1 fill:#fff3e0
    style F2 fill:#fff3e0
```

### Latency Annotations

| Path | Target p99 | Components | Degrades To |
|------|------------|------------|-------------|
| **Ingestion** | 10ms | Feed Handler → Kafka | Drop non-critical symbols |
| **Kafka Lag** | 20ms | Producer → Consumer | Increase batch size |
| **Processing** | 50ms | Sequence tracking + dedup | Skip enrichment |
| **Storage Write** | 200ms | Iceberg ACID commit | Spill to NVMe disk |
| **Query** | 300ms | DuckDB scan + API response | Reduce query complexity |
| **End-to-End** | **500ms** | Exchange → Query API | Circuit breaker |

---

## Detailed Component Architecture

### 1. Ingestion Layer - Kafka with Exchange-Level Topics

```mermaid
graph LR
    subgraph "Kafka Topics - Exchange + Asset Class Architecture"
        T1[market.equities.trades.asx<br/>30 partitions]
        T2[market.equities.quotes.asx<br/>30 partitions]
        T3[market.crypto.trades.binance<br/>40 partitions]
        T4[market.crypto.quotes.binance<br/>40 partitions]
    end

    subgraph "Schema Registry - Asset Class Level"
        S1[market.equities.trades-value<br/>Avro v1]
        S2[market.equities.quotes-value<br/>Avro v1]
        S3[market.crypto.trades-value<br/>Avro v1]
    end

    Producer1[ASX Producer] -->|symbol: BHP<br/>partition: hash| T1
    Producer2[Binance Producer] -->|symbol: BTCUSDT<br/>partition: hash| T3

    T1 -.->|BACKWARD compat| S1
    T3 -.->|BACKWARD compat| S3

    T1 --> Consumer1[ASX Consumer<br/>Manual Commit]
    T3 --> Consumer2[Binance Consumer<br/>Manual Commit]

    style T1 fill:#bbdefb
    style T2 fill:#bbdefb
    style T3 fill:#c5e1a5
    style T4 fill:#c5e1a5
```

**Key Design Decisions**:
- **Exchange-level topics**: Consumers subscribe only to needed exchanges (10-100x data reduction)
- **Symbol-based partitioning**: Preserves per-symbol message ordering (required for sequence tracking)
- **Asset-class schemas**: Shared schema evolution across exchanges (equities.trades used by ASX, NYSE, etc.)
- **BACKWARD compatibility**: New producers can add optional fields without breaking existing consumers

**Configuration**: `config/kafka/topics.yaml`
```yaml
asset_classes:
  equities:
    exchanges:
      asx:
        partitions: 30
      nyse:
        partitions: 100
  crypto:
    exchanges:
      binance:
        partitions: 40
```

---

### 2. Storage Layer - Iceberg Lakehouse

```mermaid
graph TB
    subgraph "Apache Iceberg 0.8.0"
        I1[Catalog Manager<br/>Table Metadata]
        I2[Snapshot Isolation<br/>Time-Travel]
        I3[Hidden Partitioning<br/>Daily by timestamp]
    end

    subgraph "PostgreSQL Catalog"
        P1[iceberg_tables<br/>Schema + Metadata]
        P2[iceberg_snapshots<br/>Version History]
        P3[audit_log<br/>Data Lineage]
    end

    subgraph "MinIO Object Storage"
        M1[/warehouse/trades/<br/>dt=2026-01-10/<br/>symbol=BHP/]
        M2[Parquet Files<br/>Zstd Compression]
        M3[Manifest Files<br/>File Statistics]
    end

    I1 -.->|JDBC Connection| P1
    I2 -.->|Version Tracking| P2
    I1 -->|Write Parquet| M1
    M1 --> M2
    M2 -.->|Metadata| M3

    Consumer[Kafka Consumer] -->|Batch 1000 records<br/>PyArrow Conversion| I1
    Query[DuckDB Query] -->|Zero-Copy Scan<br/>Predicate Pushdown| M2

    style I1 fill:#ce93d8
    style I2 fill:#ce93d8
    style I3 fill:#ce93d8
    style P1 fill:#90caf9
    style M1 fill:#a5d6a7
    style M2 fill:#a5d6a7
```

**Table Schema** (`trades` table):
```sql
CREATE TABLE trades (
  symbol STRING,
  exchange STRING,
  exchange_timestamp TIMESTAMP,
  exchange_sequence_number BIGINT,
  price DECIMAL(18,8),
  volume DECIMAL(18,8),
  -- Partitioned by: day(exchange_timestamp)
  -- Sorted by: (exchange_timestamp, exchange_sequence_number)
)
PARTITIONED BY (days(exchange_timestamp))
SORTED BY (exchange_timestamp, exchange_sequence_number)
```

**Partition Strategy**:
- **Daily partitioning**: Optimizes time-range queries (query 1 day = scan 1 partition)
- **Sorting**: Per-symbol sequence validation, efficient range scans
- **Target**: < 100 symbols per partition (manageable file sizes)

**ACID Guarantees**:
- **Atomicity**: All-or-nothing writes (1000 records commit together or rollback)
- **Consistency**: Schema evolution enforced by catalog
- **Isolation**: Snapshot isolation (concurrent readers see consistent view)
- **Durability**: Committed data persisted to S3 before ACK

---

### 3. Query Layer - DuckDB Embedded OLAP

```mermaid
graph LR
    subgraph "Query Routing"
        R1{Time Range?}
        R2[Realtime<br/>last 5 min]
        R3[Historical<br/>older than 5 min]
        R4[Hybrid<br/>spans boundary]
    end

    subgraph "Query Sources"
        Q1[Kafka Tail<br/>Real-time Stream]
        Q2[DuckDB + Iceberg<br/>Historical Scan]
        Q3[Merge Engine<br/>Union Results]
    end

    subgraph "Optimization"
        O1[Partition Pruning<br/>Skip 95%+ files]
        O2[Predicate Pushdown<br/>Filter at file level]
        O3[Projection Pushdown<br/>Read only needed columns]
        O4[3-Tier Cache<br/>Redis + Memory + Precomputed]
    end

    API[FastAPI REST] --> R1
    R1 -->|< 5 min| R2
    R1 -->|> 5 min| R3
    R1 -->|Overlaps| R4

    R2 --> Q1
    R3 --> Q2
    R4 --> Q3

    Q2 --> O1
    O1 --> O2
    O2 --> O3
    O3 --> O4

    style R1 fill:#fff59d
    style Q2 fill:#aed581
    style O1 fill:#81c784
    style O4 fill:#4caf50
```

**Query Performance Targets**:
| Query Type | Data Size | p99 Target | Achieved | Method |
|------------|-----------|------------|----------|--------|
| Realtime | Last 5 min | 200ms | TBD | Kafka tail consumer |
| Historical | 100K rows | 1s | TBD | DuckDB vectorized scan |
| Time-travel | 1M rows | 5s | TBD | Iceberg snapshot_id |
| Aggregation | 10M rows | 10s | TBD | DuckDB parallel execution |

**Pre-built Queries**:
```python
# High-performance query implementations
get_trades(symbol, start_time, end_time) → DataFrame
get_market_summary(symbol, date) → OHLCV
get_latest_price(symbol) → Scalar
replay_day(symbol, date) → Iterator[Trade]
```

---

### 4. Observability Stack

```mermaid
graph TB
    subgraph "Metrics Collection - Prometheus 3.9.1"
        M1[Kafka JMX Exporter<br/>Broker metrics]
        M2[MinIO Built-in<br/>Storage metrics]
        M3[Application /metrics<br/>RED metrics]
        M4[Schema Registry<br/>Compatibility checks]
    end

    subgraph "Prometheus Storage"
        P1[Time Series DB<br/>15-day retention]
        P2[Alerting Rules<br/>Consumer lag > 60s]
        P3[Recording Rules<br/>Aggregations]
    end

    subgraph "Grafana Dashboards 12.3.1"
        G1[System Overview<br/>Throughput + Lag]
        G2[Per-Exchange Drill-down<br/>ASX, Binance]
        G3[Query Performance<br/>Latency by mode]
        G4[Resource Utilization<br/>CPU, Memory, Disk]
    end

    M1 & M2 & M3 & M4 --> P1
    P1 --> P2
    P2 -.->|Alerts| AlertManager[Alert Manager<br/>PagerDuty]
    P1 --> G1 & G2 & G3 & G4

    style M3 fill:#ffcc80
    style P2 fill:#ff8a65
    style G1 fill:#4fc3f7
```

**Critical Alerts** (Page on-call):
```yaml
# Consumer lag
kafka_consumer_lag_seconds > 60

# Data loss detection
sequence_gaps_total > 100

# Storage bottleneck
iceberg_write_duration_p99 > 1000ms

# Circuit breaker
circuit_breaker_state == "OPEN"

# API degradation
http_request_duration_p99{endpoint="/trades"} > 500ms
```

**RED Metrics** (Rate, Errors, Duration):
- `http_requests_total{method, endpoint, status_code}` - Request rate
- `http_request_errors_total{method, endpoint, error_type}` - Error rate
- `http_request_duration_seconds{method, endpoint}` - Response time distribution

---

## Data Flow: End-to-End

### Happy Path - Trade Ingestion

```mermaid
sequenceDiagram
    participant E as Exchange (ASX)
    participant P as Kafka Producer
    participant K as Kafka Broker
    participant C as Kafka Consumer
    participant S as Sequence Tracker
    participant I as Iceberg Writer
    participant D as PostgreSQL Catalog
    participant M as MinIO S3

    E->>P: Trade: BHP @ $45.23<br/>seq=12345
    Note over P: Avro serialization<br/>2-3ms
    P->>K: Produce to market.equities.trades.asx<br/>partition: hash(BHP)
    Note over K: Replication<br/>5-7ms (acks=all)
    K-->>P: ACK

    K->>C: Poll batch (1000 msgs)
    Note over C: Avro deserialization<br/>10-15ms

    C->>S: Validate sequence: 12345
    Note over S: Gap detection<br/>5ms
    S-->>C: OK (no gaps)

    C->>I: Write batch (1000 trades)
    Note over I: PyArrow conversion<br/>20ms
    I->>D: BEGIN transaction
    I->>M: Write Parquet file
    Note over M: Compress (Zstd)<br/>100ms
    M-->>I: OK
    I->>D: COMMIT transaction
    D-->>I: Snapshot ID
    I-->>C: Write complete

    C->>K: Commit offset
    Note over C: Manual commit after<br/>successful Iceberg write
    K-->>C: OK

    Note over E,M: Total latency: ~150-200ms p99
```

### Failure Scenario - Sequence Gap Detected

```mermaid
sequenceDiagram
    participant E as Exchange
    participant K as Kafka
    participant C as Consumer
    participant S as Sequence Tracker
    participant A as Alert Manager
    participant O as On-Call Engineer

    E->>K: seq=10
    E->>K: seq=11
    Note over E: Network issue!<br/>seq=12, 13, 14 lost
    E->>K: seq=15

    K->>C: seq=10, 11
    C->>S: Validate
    S-->>C: OK

    K->>C: seq=15
    C->>S: Validate (expected 12, got 15)
    S-->>C: GAP DETECTED: missing 12-14

    alt Gap < 10
        S->>C: Log warning, continue
    else Gap 10-100
        S->>A: Alert: Page on-call
        A->>O: PagerDuty notification
        Note over O: Request recovery<br/>from exchange
    else Gap > 100
        S->>C: HALT consumer
        S->>A: Critical: Data loss
        Note over O: Manual investigation
    end
```

---

## Scaling Considerations

### Current State (Local Dev)

| Metric | Value | Hardware |
|--------|-------|----------|
| Throughput | 10K msg/sec | M1 Max, 32GB RAM |
| Kafka | 1 broker, 6 partitions | Docker (2 CPU, 3GB) |
| Storage | < 1GB | MinIO local |
| Latency p99 | < 100ms | NVMe SSD |
| Query Engine | DuckDB embedded | Single process |

### 100x Scale (Production)

| Metric | Target | Architecture Changes |
|--------|--------|---------------------|
| Throughput | 1M msg/sec | Kafka: 3-5 brokers, 50+ partitions |
| Storage | TBs | MinIO distributed cluster |
| Latency p99 | < 50ms | SSD storage, PostgreSQL read replicas |
| Query | Concurrent users | DuckDB + Presto (distributed) |
| HA | 99.9% uptime | Multi-AZ deployment |

**Bottleneck Mitigation**:
1. **Kafka throughput**: Add brokers, increase partitions (linear scaling)
2. **Storage writes**: Horizontal scaling of consumers (each writes to different partitions)
3. **Query latency**: Add Presto cluster (distributed parallel queries)
4. **Metadata**: PostgreSQL read replicas + caching (99%+ cache hit rate)

### 1000x Scale (Multi-Region)

| Metric | Target | Architecture Changes |
|--------|--------|---------------------|
| Throughput | 10M+ msg/sec | Kafka: 10+ brokers, 500+ partitions |
| Storage | PBs | S3 with lifecycle policies (Glacier) |
| Latency p99 | < 20ms | Edge caching, pre-aggregations |
| Query | Global users | Presto/Trino (100+ nodes) |
| HA | 99.99% uptime | Active-active multi-region |

---

## Technology Decision Matrix

| Component | Choice | Alternatives Considered | Decision Rationale |
|-----------|--------|-------------------------|-------------------|
| **Message Queue** | Kafka 8.1.1 (KRaft) | Pulsar, RabbitMQ | Industry standard, KRaft simplifies ops |
| **Schema Registry** | Confluent 8.1.1 | Apicurio, AWS Glue | Best Kafka integration, mature |
| **Lakehouse** | Apache Iceberg 0.8.0 | Delta Lake, Hudi | ACID + time-travel, proven at scale |
| **Object Store** | MinIO (S3 API) | Native S3, Azure Blob | S3-compatible, easy cloud migration |
| **Catalog DB** | PostgreSQL 16 | MySQL, DynamoDB | ACID guarantees for metadata |
| **Query Engine** | DuckDB 1.4.0 | Spark, Presto, Trino | Embedded simplicity, sub-second queries |
| **API Framework** | FastAPI 0.128.0 | Django, Flask | Modern async, auto OpenAPI docs |
| **Metrics** | Prometheus 3.9.1 | Datadog, New Relic | Open-source, de facto standard |
| **Dashboards** | Grafana 12.3.1 | Kibana, Tableau | Best Prometheus integration |

**Philosophy**: "Boring Technology" - Prefer proven systems over novel solutions.

---

## Security Architecture

### Current State (Phase 1 - Local Dev)

- ⚠️ No authentication (localhost-only access)
- ⚠️ No encryption in transit (plaintext Kafka, HTTP)
- ⚠️ Default credentials (MinIO: admin/password)
- ⚠️ No audit logging

### Production Requirements (Phase 2+)

```mermaid
graph TB
    subgraph "Authentication & Authorization"
        A1[OAuth2 + JWT<br/>Token-based auth]
        A2[RBAC Policies<br/>Query-level permissions]
        A3[API Key Management<br/>Rotate every 90 days]
    end

    subgraph "Encryption"
        E1[Kafka TLS/SASL<br/>Broker + client certs]
        E2[PostgreSQL SSL<br/>Require SSL connections]
        E3[MinIO Server-Side<br/>SSE-KMS encryption]
    end

    subgraph "Secrets Management"
        S1[HashiCorp Vault<br/>or AWS Secrets Manager]
        S2[Credential Rotation<br/>Automated every 90 days]
    end

    subgraph "Audit & Compliance"
        L1[Audit Log<br/>All data access logged]
        L2[Data Lineage<br/>Track provenance]
        L3[Compliance Reports<br/>GDPR, SOX, MiFID II]
    end

    style A1 fill:#ffccbc
    style E1 fill:#ffccbc
    style S1 fill:#ffccbc
    style L1 fill:#ffccbc
```

---

## Disaster Recovery Strategy

### Backup Strategy

| Data Type | Backup Frequency | Retention | RTO | RPO |
|-----------|-----------------|-----------|-----|-----|
| Kafka Messages | N/A (ephemeral) | 7 days in topic | N/A | 0 (replay from Iceberg) |
| Iceberg Data | Continuous (S3) | 90 days hot, 7 years cold | 4 hours | < 5 minutes |
| Iceberg Metadata | Daily snapshots | 30 days | 1 hour | 24 hours |
| PostgreSQL | Hourly WAL backup | 30 days | 1 hour | 1 hour |

### Multi-Region Replication (Phase 3)

```
Primary Region (us-east-1)      Secondary Region (us-west-2)
┌──────────────────────┐        ┌──────────────────────┐
│ Kafka Cluster        │        │ Kafka Cluster        │
│ (Active)             │◄──────►│ (Active)             │
└──────────────────────┘  MirrorMaker 2.0  └──────────────────────┘
         │                                          │
         ▼                                          ▼
┌──────────────────────┐        ┌──────────────────────┐
│ Iceberg + S3         │        │ Iceberg + S3         │
│ (Active Writes)      │────────►│ (Read Replicas)      │
└──────────────────────┘  S3 Cross-Region  └──────────────────────┘
                             Replication
```

**Failover Procedure**: See [Disaster Recovery Runbook](../operations/runbooks/disaster-recovery.md)

---

## References

### Related Documentation
- [Platform Principles](./platform-principles.md) - Core design philosophy
- [Kafka Topic Strategy](./kafka-topic-strategy.md) - Exchange-level topic architecture
- [Alternative Architectures](./alternatives.md) - Lambda vs Kappa vs K2 approach

### External Resources
- [Apache Iceberg Documentation](https://iceberg.apache.org/docs/latest/)
- [Kafka KRaft Mode](https://kafka.apache.org/documentation/#kraft)
- [DuckDB Iceberg Extension](https://duckdb.org/docs/extensions/iceberg.html)

---

**Last Updated**: 2026-01-10
**Maintained By**: Platform Engineering Team
**Next Review**: 2026-04-10 (quarterly)

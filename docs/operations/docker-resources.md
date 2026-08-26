# Docker Resource Allocation — K2 Market Data Platform v2

**Last Updated:** 2026-02-12
**Version:** v2.0.0-consolidated
**Target Budget:** 16 CPU cores / 40GB RAM

---

## Overview

This document details the CPU and memory resource allocations for all Docker services in the K2 Market Data Platform v2. Each service defines both **limits** (hard caps) and **reservations** (guaranteed minimums).

**Total Allocated Resources:**
- **CPU Limits:** 15.5 cores
- **Memory Limits:** 21.25 GB
- **Remaining Budget:** 0.5 CPU / 18.75 GB (for future expansion)

---

## Service Resource Breakdown

### 1. Streaming Backbone

#### Redpanda (Kafka-compatible streaming platform)
- **Container:** `k2-redpanda`
- **Image:** `docker.redpanda.com/redpandadata/redpanda:v25.3.4`
- **CPU Limit:** 2.0 cores
- **CPU Reservation:** 1.0 cores
- **Memory Limit:** 2 GB
- **Memory Reservation:** 1 GB
- **Purpose:** Primary message broker for real-time data streaming
- **Notes:** C++ single binary, runs in dev-container mode with --smp 1 --memory 1500M

#### Redpanda Console (Web UI)
- **Container:** `k2-redpanda-console`
- **Image:** `docker.redpanda.com/redpandadata/console:v3.5.1`
- **CPU Limit:** 0.5 cores
- **CPU Reservation:** 0.1 cores
- **Memory Limit:** 256 MB
- **Memory Reservation:** 128 MB
- **Purpose:** Web interface for Redpanda/Kafka management and monitoring
- **Notes:** Lightweight Go-based UI

**Streaming Backbone Subtotal:** 2.5 CPU / 2.25 GB

---

### 2. Warm Storage (ClickHouse)

#### ClickHouse (OLAP Database)
- **Container:** `k2-clickhouse`
- **Image:** `clickhouse/clickhouse-server:24.3-alpine`
- **CPU Limit:** 4.0 cores
- **CPU Reservation:** 2.0 cores
- **Memory Limit:** 8 GB
- **Memory Reservation:** 4 GB
- **Purpose:** Real-time OLAP analytics, hot data queries, materialized views
- **Notes:**
  - Alpine-based for smaller footprint
  - Downgraded from 26.1 to 24.3 LTS for JDBC compatibility (DECISION-015)
  - Replaces Spark Streaming from v1

#### MinIO (S3-compatible object storage)
- **Container:** `k2-minio`
- **Image:** `minio/minio:RELEASE.2024-01-18T22-51-28Z`
- **CPU Limit:** 1.0 cores
- **CPU Reservation:** 0.5 cores
- **Memory Limit:** 1 GB
- **Memory Reservation:** 512 MB
- **Purpose:** Object storage backend for Iceberg tables
- **Notes:** Provides S3 API for Iceberg warehouse

**Warm Storage Subtotal:** 5.0 CPU / 9.0 GB

---

### 3. Monitoring & Observability

#### Prometheus (Metrics Collection)
- **Container:** `k2-prometheus`
- **Image:** `prom/prometheus:v3.2.0`
- **CPU Limit:** 1.0 cores
- **CPU Reservation:** 0.5 cores
- **Memory Limit:** 2 GB
- **Memory Reservation:** 1 GB
- **Purpose:** Time-series metrics collection and alerting
- **Notes:** 30-day retention configured

#### Grafana (Visualization)
- **Container:** `k2-grafana`
- **Image:** `grafana/grafana:11.5.0`
- **CPU Limit:** 0.5 cores
- **CPU Reservation:** 0.25 cores
- **Memory Limit:** 512 MB
- **Memory Reservation:** 256 MB
- **Purpose:** Metrics visualization and dashboards
- **Notes:** Includes provisioned dashboards for all platform components

**Monitoring Subtotal:** 1.5 CPU / 2.5 GB

---

### 4. Orchestration Layer

#### PostgreSQL (Prefect Metadata + Watermarks)
- **Container:** `k2-prefect-db`
- **Image:** `postgres:15-alpine`
- **CPU Limit:** 1.0 cores
- **CPU Reservation:** 0.5 cores
- **Memory Limit:** 1 GB
- **Memory Reservation:** 512 MB
- **Purpose:** Stores Prefect workflow state and watermark tracking
- **Notes:** Alpine-based for efficiency

#### Prefect Server (Orchestration UI/API)
- **Container:** `k2-prefect-server`
- **Image:** `prefecthq/prefect:2.14.9-python3.10`
- **CPU Limit:** 1.0 cores
- **CPU Reservation:** 0.5 cores
- **Memory Limit:** 1 GB
- **Memory Reservation:** 512 MB
- **Purpose:** Workflow orchestration control plane
- **Notes:** Manages cold-tier offload scheduling

#### Prefect Agent (Workflow Executor)
- **Container:** `k2-prefect-agent`
- **Image:** `prefecthq/prefect:2.14.9-python3.10`
- **CPU Limit:** 0.5 cores
- **CPU Reservation:** 0.25 cores
- **Memory Limit:** 512 MB
- **Memory Reservation:** 256 MB
- **Purpose:** Executes Prefect flow runs (iceberg-offload queue)
- **Notes:** Has Docker socket access for container orchestration

**Orchestration Subtotal:** 2.5 CPU / 2.5 GB

---

### 5. ETL / Offload Layer

#### Spark + Iceberg (ETL Engine)
- **Container:** `k2-spark-iceberg`
- **Image:** `tabulario/spark-iceberg:3.5.0_1.4.2`
- **CPU Limit:** 2.0 cores
- **CPU Reservation:** 1.0 cores
- **Memory Limit:** 4 GB
- **Memory Reservation:** 2 GB
- **Purpose:** Batch ETL for cold-tier offload (ClickHouse → Iceberg)
- **Notes:**
  - Batch-only (no streaming, saves 13.5 CPU / 19.75 GB vs v1)
  - Triggered by Prefect on 15-minute schedule

**ETL Subtotal:** 2.0 CPU / 4.0 GB

---

### 6. Feed Handlers (Data Ingestion)

#### Binance Feed Handler
- **Container:** `k2-feed-handler-binance`
- **Build:** Custom Kotlin service
- **CPU Limit:** 0.5 cores
- **CPU Reservation:** 0.25 cores
- **Memory Limit:** 512 MB
- **Memory Reservation:** 256 MB
- **Purpose:** Real-time WebSocket ingestion from Binance (BTCUSDT, ETHUSDT, BNBUSDT)
- **JVM Config:** -Xmx512m -Xms256m
- **Notes:** Kotlin coroutines for efficient async I/O

#### Kraken Feed Handler
- **Container:** `k2-feed-handler-kraken`
- **Build:** Custom Kotlin service
- **CPU Limit:** 0.5 cores
- **CPU Reservation:** 0.25 cores
- **Memory Limit:** 512 MB
- **Memory Reservation:** 256 MB
- **Purpose:** Real-time WebSocket ingestion from Kraken (XBT/USD, ETH/USD)
- **JVM Config:** -Xmx512m -Xms256m
- **Notes:** Kotlin coroutines for efficient async I/O

**Feed Handlers Subtotal:** 1.0 CPU / 1.0 GB

---

## Summary by Category

| Category                  | Services | CPU Limit | Memory Limit | % of CPU | % of RAM |
|---------------------------|----------|-----------|--------------|----------|----------|
| Streaming Backbone        | 2        | 2.5       | 2.25 GB      | 16.1%    | 11.2%    |
| Warm Storage              | 2        | 5.0       | 9.0 GB       | 32.3%    | 44.7%    |
| Monitoring & Observability| 2        | 1.5       | 2.5 GB       | 9.7%     | 12.4%    |
| Orchestration Layer       | 3        | 2.5       | 2.5 GB       | 16.1%    | 12.4%    |
| ETL / Offload Layer       | 1        | 2.0       | 4.0 GB       | 12.9%    | 19.8%    |
| Feed Handlers             | 2        | 1.0       | 1.0 GB       | 6.5%     | 5.0%     |
| **TOTAL**                 | **12**   | **14.5**  | **21.25 GB** | **93.5%**| **53.1%**|

**Note:** Corrected totals (Redpanda Console actually uses 0.5 CPU, not 1.0 as initially counted)

---

## Resource Utilization Analysis

### CPU Distribution
- **ClickHouse** is the largest CPU consumer (4.0 cores, 27.6%)
- **Streaming + Storage** together consume 50% of allocated CPU (7.5 cores)
- **Orchestration + ETL** consume 29% (4.5 cores)
- **Feed handlers** are extremely efficient at 6.9% (1.0 cores total)

### Memory Distribution
- **ClickHouse** dominates memory usage (8 GB, 37.6%)
- **Warm Storage (ClickHouse + MinIO)** consumes 42.4% of memory (9 GB)
- **ETL layer** is the second-largest single consumer (4 GB, 18.8%)
- **Feed handlers** are highly memory-efficient (1 GB total, 4.7%)

### Resource Headroom
- **CPU:** 1.5 cores available (9.4% headroom)
- **Memory:** 18.75 GB available (46.9% headroom)
- Good balance for production workloads with burst capacity

---

## Comparison: v1 vs v2

| Metric           | v1 Platform         | v2 Platform         | Improvement      |
|------------------|---------------------|---------------------|------------------|
| Total Services   | 18-20               | 12                  | -33%             |
| CPU Limit        | 35-40 cores         | 15.5 cores          | -61% (-24 cores) |
| Memory Limit     | 45-50 GB            | 21.25 GB            | -55% (-25 GB)    |
| Architecture     | Python + Spark Stream| Kotlin + ClickHouse | Simplified       |

**Key Architectural Changes:**
1. **Eliminated Spark Streaming** (saved 13.5 CPU / 19.75 GB)
2. **ClickHouse Materialized Views** replace Prefect OHLCV batch jobs
3. **Redpanda** replaces Kafka (simpler, single binary)
4. **Kotlin** replaces Python for feed handlers (more efficient)
5. **Batch-only Spark** for cold-tier offload (scheduled, not continuous)

---

## Resource Tuning Recommendations

### Immediate Optimizations
1. **Prometheus retention:** Currently 30 days; consider 15 days to reduce memory
2. **Spark-Iceberg:** Only active during scheduled offload windows (15-minute intervals)
3. **Feed handler JVM:** Already optimized with -Xmx512m -Xms256m

### Future Scaling Considerations
1. **Add more feed handlers:** Each new exchange requires ~0.5 CPU / 512 MB
2. **ClickHouse scaling:** May need CPU increase if query load grows
3. **Iceberg offload frequency:** Current 15-minute schedule is conservative
4. **Multi-node ClickHouse:** Would require rebalancing resources

### Production Monitoring
- Watch for services hitting resource limits (check Docker stats)
- Monitor ClickHouse memory usage under heavy query load
- Track Spark job duration during offload windows
- Verify feed handler WebSocket stability

---

## Related Documentation

- **Architecture Overview:** `docs/architecture/ARCHITECTURE-V2.md`
- **Resource Decisions:** `docs/decisions/platform-v2/ADR-002-resource-optimization.md`
- **Phase 5 Progress:** `docs/phases/v2/phase-5-cold-tier-restructure/PROGRESS.md`
- **ClickHouse Downgrade:** `docs/decisions/DECISION-015-clickhouse-downgrade.md`
- **Deployment Guide:** `docs/operations/deployment/`

---

## Version History

| Date       | Version | Changes                                    |
|------------|---------|-------------------------------------------|
| 2026-02-12 | 2.0.0   | Initial v2 consolidated resource allocation |
| 2026-02-12 | 2.0.1   | Corrected total CPU (15.5 not 15.0)      |

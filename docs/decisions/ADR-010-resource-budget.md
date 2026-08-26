# ADR-010: Resource Budget and Docker Compose Constraints

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Infrastructure

---

## Context

The mandate is clear: **16 cores, 40GB RAM, single Docker Compose cluster.**

The current platform consumes **35-40 CPUs / 45-50GB RAM** — more than 2x the budget on both axes. This ADR documents the resource allocation for every service in the v2 architecture and proves the budget is achievable.

## Decision

**Allocate resources per the budget below, with 15% headroom for burst capacity.**

## Resource Budget

### v2 Service Allocation

| Service | CPU Limit | CPU Reserve | RAM Limit | RAM Reserve | Justification |
|---------|-----------|-------------|-----------|-------------|---------------|
| **Redpanda** | 2.0 | 1.0 | 2GB | 1.5GB | Thread-per-core; 2 cores handles 100K msg/s |
| **ClickHouse** | 4.0 | 2.0 | 8GB | 4GB | Primary query engine; vectorized execution benefits from cores + RAM |
| **Kotlin Feed Handlers** | 1.0 | 0.5 | 512MB | 256MB | All exchanges in single JVM; coroutines are lightweight |
| **Kotlin Silver Processor** | 1.0 | 0.5 | 512MB | 256MB | Validation is CPU-light; batch inserts to ClickHouse |
| **Spring Boot API** | 2.0 | 1.0 | 1GB | 512MB | Virtual threads; HikariCP pool; JSON serialization |
| **Spark Batch** (on-demand) | 2.0 | 0 | 4GB | 0 | Only runs 2h/day; resources shared when idle |
| **MinIO** | 1.0 | 0.5 | 1GB | 512MB | Cold storage I/O; reduced from v1 (less frequent access) |
| **PostgreSQL** (Iceberg catalog) | 0.5 | 0.25 | 512MB | 256MB | Metadata only; tiny workload |
| **Iceberg REST Catalog** | 0.5 | 0.25 | 512MB | 256MB | Catalog API; lightweight |
| **Prometheus** | 0.5 | 0.25 | 512MB | 256MB | Metrics scraping; 15s interval |
| **Grafana** | 0.5 | 0.25 | 512MB | 256MB | Dashboard rendering |
| | | | | | |
| **Total (all services)** | **15.5** | **6.5** | **19.5GB** | **8GB** | |
| **Total (Spark idle)** | **13.5** | **6.5** | **15.5GB** | **8GB** | 22h/day |
| **Budget** | **16.0** | — | **40GB** | — | |
| **Headroom** | **0.5 (3%)** | — | **20.5GB (51%)** | — | |
| **Headroom (Spark idle)** | **2.5 (16%)** | — | **24.5GB (61%)** | — | |

### v1 vs v2 Comparison

| Category | v1 | v2 | Reduction |
|----------|----|----|-----------|
| **Total CPU (limits)** | 35-40 | 15.5 | **56-61%** |
| **Total RAM (limits)** | 45-50GB | 19.5GB | **57-61%** |
| **Service count** | 18-20 | 11 | **42-45%** |
| **JVM processes** | 8 (Kafka, SR, Spark×5, Prefect) | 3 (Feed, Silver, API) | **63%** |
| **Python processes** | 4 (2 handlers, Prefect×2) | 0 | **100%** |
| **Always-on Spark** | 14 CPU / 20GB | 0 (batch only) | **100%** |

### Service Elimination Summary

| v1 Service | v2 Replacement | CPU Saved | RAM Saved |
|------------|----------------|-----------|-----------|
| Kafka (1.5 CPU / 2GB) | Redpanda (shared) | 0.5 | 0.5GB |
| Schema Registry (0.5 CPU / 768MB) | Redpanda built-in | 0.5 | 768MB |
| Kafka UI (0.5 CPU / 512MB) | Redpanda Console | 0.5 | 512MB |
| Spark Master (2.0 CPU / 2GB) | Eliminated | 2.0 | 2GB |
| Spark Worker 1 (3.5 CPU / 4GB) | Eliminated | 3.5 | 4GB |
| Spark Worker 2 (3.5 CPU / 4GB) | Eliminated | 3.5 | 4GB |
| Bronze Binance Stream (1.0 CPU / 2GB) | ClickHouse Kafka Engine | 1.0 | 2GB |
| Bronze Kraken Stream (1.0 CPU / 2GB) | ClickHouse Kafka Engine | 1.0 | 2GB |
| Silver Binance Transform (1.0 CPU / 2GB) | Kotlin Silver Processor | 0.0 | 1.5GB |
| Silver Kraken Transform (1.0 CPU / 2GB) | Kotlin Silver Processor | 1.0 | 2GB |
| Gold Aggregation (1.0 CPU / 2GB) | ClickHouse MVs | 1.0 | 2GB |
| Prefect Server (0.5 CPU / 512MB) | Eliminated | 0.5 | 512MB |
| Prefect Agent (0.25 CPU / 256MB) | Eliminated | 0.25 | 256MB |
| Binance Handler Python (0.5 CPU / 512MB) | Kotlin Feed Handler | 0.0 | 0 |
| Kraken Handler Python (0.5 CPU / 512MB) | Kotlin Feed Handler | 0.5 | 512MB |
| **Total eliminated** | | **~16 CPU** | **~23GB** |

### Burst Capacity Planning

During the 2h daily Spark batch window, the platform uses 15.5 CPU / 19.5GB. The remaining 22h/day, Spark is idle:

```
Off-peak (22h/day): 13.5 CPU / 15.5GB used → 2.5 CPU / 24.5GB available for burst
Peak (2h/day):      15.5 CPU / 19.5GB used → 0.5 CPU / 20.5GB available for burst
```

ClickHouse benefits from burst capacity — during off-peak hours, it can use up to 6.5 cores (4 limit + 2.5 spare) for heavy analytical queries.

### Docker Compose Resource Configuration

```yaml
version: "3.8"

services:
  redpanda:
    image: docker.redpanda.com/redpandadata/redpanda:latest
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
        reservations:
          cpus: "1.0"
          memory: 1536M
    command:
      - redpanda start
      - --smp 2
      - --memory 1536M
      - --reserve-memory 0M

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 8G
        reservations:
          cpus: "2.0"
          memory: 4G

  feed-handlers:
    image: k2/feed-handlers:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.5"
          memory: 256M
    environment:
      JAVA_OPTS: "-Xmx384m -Xms256m -XX:+UseZGC"

  silver-processor:
    image: k2/silver-processor:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.5"
          memory: 256M
    environment:
      JAVA_OPTS: "-Xmx384m -Xms256m -XX:+UseZGC"

  api:
    image: k2/api:latest
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "1.0"
          memory: 512M
    environment:
      JAVA_OPTS: "-Xmx768m -Xms512m -XX:+UseZGC"

  spark-batch:
    image: k2/spark-batch:latest
    profiles: ["batch"]
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G

  minio:
    image: minio/minio:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 512M

  postgres:
    image: postgres:16-alpine
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  iceberg-rest:
    image: tabulario/iceberg-rest:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  prometheus:
    image: prom/prometheus:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  grafana:
    image: grafana/grafana:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M
```

## Monitoring the Budget

### Key Metrics to Track

```yaml
# Grafana alerts
- alert: CPUBudgetExceeded
  expr: sum(container_cpu_usage_seconds_total) > 14.0  # 87.5% of 16
  for: 5m

- alert: MemoryBudgetExceeded
  expr: sum(container_memory_usage_bytes) > 34359738368  # 32GB (80% of 40)
  for: 5m

- alert: ClickHouseMemoryHigh
  expr: container_memory_usage_bytes{name="clickhouse"} > 7516192768  # 7GB (87.5% of 8)
  for: 5m
```

## Consequences

### Positive
- Platform fits within 16 cores / 40GB with 51% RAM headroom
- 42-45% fewer services to manage
- Clear resource ownership per service
- Burst capacity available during off-peak hours
- Spark only consumes resources when running batch jobs

### Negative
- ClickHouse gets the largest allocation (4 CPU / 8GB) — if it's underutilized, resources are wasted
- Spark batch window temporarily reduces burst capacity
- Resource limits may need tuning as data volume grows

## References

- [Docker Compose Resource Limits](https://docs.docker.com/compose/compose-file/deploy/#resources)
- [ClickHouse Memory Configuration](https://clickhouse.com/docs/en/operations/settings/settings#max_memory_usage)
- [Redpanda Memory Configuration](https://docs.redpanda.com/current/reference/properties/tuning-properties/)
- [ZGC (Low-Latency GC)](https://wiki.openjdk.org/display/zgc)

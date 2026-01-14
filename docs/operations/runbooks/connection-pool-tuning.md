# Connection Pool Tuning and Operations

**Component**: DuckDB Query Engine Connection Pool
**Audience**: DevOps, SREs, Platform Engineers
**Last Updated**: 2026-01-14
**Complexity**: Medium

---

## Overview

The K2 query engine uses a connection pool to enable concurrent query execution. This runbook covers operational procedures for monitoring, tuning, and troubleshooting the connection pool.

### Quick Facts

- **Current Pool Size**: 5 connections (configurable)
- **Throughput Improvement**: 5x (single → 5 connections)
- **Production Scaling**: 20-50 connections recommended
- **Timeout Default**: 30s acquisition timeout
- **Query Timeout**: 60s per query
- **Memory per Connection**: ~10-50 MB (query-dependent)

---

## Configuration

### Environment Variables

```bash
# Query Engine Configuration
K2_QUERY_ENGINE_POOL_SIZE=5          # Number of concurrent connections
K2_QUERY_ENGINE_POOL_TIMEOUT=30      # Seconds to wait for connection
K2_QUERY_ENGINE_QUERY_TIMEOUT=60     # Seconds per query
K2_QUERY_ENGINE_MEMORY_LIMIT=4GB     # Memory limit per connection
```

### pyproject.toml Configuration

```python
# src/k2/query/engine.py initialization
engine = QueryEngine(
    pool_size=5,                      # Production: 20-50
    pool_timeout=30.0,                # Acquisition timeout
    query_timeout_ms=60000,           # 60s
    memory_limit="4GB"
)
```

### Sizing Guidelines

| Environment | Concurrent Users | Recommended Pool Size | Memory Overhead |
|-------------|------------------|----------------------|-----------------|
| **Development** | 1-5 | 5 | 250 MB |
| **Demo** | 5-10 | 5-10 | 500 MB |
| **Production (Light)** | 10-50 | 20 | 1 GB |
| **Production (Heavy)** | 50-200 | 50 | 2.5 GB |

**Formula**: `pool_size = max_concurrent_queries * 1.2` (20% buffer)

---

## Monitoring

### Key Metrics

#### Pool Utilization
```promql
# Current active connections
k2_connection_pool_active_connections

# Available connections
k2_connection_pool_available_connections

# Pool size (configured)
k2_connection_pool_size

# Utilization percentage
(k2_connection_pool_active_connections / k2_connection_pool_size) * 100
```

#### Performance Metrics
```promql
# Wait time for connection acquisition (p99)
histogram_quantile(0.99, rate(k2_connection_pool_wait_time_seconds_bucket[5m]))

# Acquisition timeouts
rate(k2_connection_pool_acquisition_timeouts_total[5m])

# Connection creation errors
rate(k2_connection_pool_creation_errors_total[5m])
```

#### Derived Metrics
```promql
# Average wait time (last 5 min)
rate(k2_connection_pool_wait_time_seconds_sum[5m]) /
rate(k2_connection_pool_wait_time_seconds_count[5m])

# Peak utilization percentage
max_over_time(
  (k2_connection_pool_active_connections / k2_connection_pool_size)[5m:]
) * 100
```

### Grafana Dashboard Panels

**Panel 1: Pool Utilization Gauge**
```json
{
  "title": "Connection Pool Utilization",
  "type": "gauge",
  "targets": [{
    "expr": "(k2_connection_pool_active_connections / k2_connection_pool_size) * 100"
  }],
  "thresholds": {
    "mode": "absolute",
    "steps": [
      {"value": 0, "color": "green"},
      {"value": 70, "color": "yellow"},
      {"value": 90, "color": "red"}
    ]
  }
}
```

**Panel 2: Wait Time Histogram**
```json
{
  "title": "Connection Acquisition Wait Time",
  "type": "graph",
  "targets": [
    {"expr": "histogram_quantile(0.50, rate(k2_connection_pool_wait_time_seconds_bucket[5m]))", "legendFormat": "p50"},
    {"expr": "histogram_quantile(0.95, rate(k2_connection_pool_wait_time_seconds_bucket[5m]))", "legendFormat": "p95"},
    {"expr": "histogram_quantile(0.99, rate(k2_connection_pool_wait_time_seconds_bucket[5m]))", "legendFormat": "p99"}
  ]
}
```

### Alerting Rules

```yaml
# config/prometheus/rules/connection_pool.yml
groups:
  - name: connection_pool_alerts
    rules:
      - alert: ConnectionPoolHighUtilization
        expr: (k2_connection_pool_active_connections / k2_connection_pool_size) > 0.9
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Connection pool utilization > 90%"
          description: "Pool at {{ $value }}% utilization. Consider increasing pool_size."
          runbook: "docs/operations/runbooks/connection-pool-tuning.md"

      - alert: ConnectionPoolAcquisitionTimeouts
        expr: rate(k2_connection_pool_acquisition_timeouts_total[5m]) > 0
        for: 2m
        labels:
          severity: high
        annotations:
          summary: "Connection acquisition timeouts occurring"
          description: "{{ $value }} timeouts/sec. Pool exhausted or queries too slow."
          runbook: "docs/operations/runbooks/connection-pool-tuning.md"

      - alert: ConnectionPoolCreationErrors
        expr: rate(k2_connection_pool_creation_errors_total[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Connection creation failing"
          description: "Cannot create new connections. Database issue."
          runbook: "docs/operations/runbooks/connection-pool-tuning.md"
```

---

## Troubleshooting

### Issue: High Acquisition Timeouts

**Symptoms**:
- API returns `504 Gateway Timeout`
- Prometheus shows `k2_connection_pool_acquisition_timeouts_total` increasing
- Users report slow queries

**Diagnosis**:
```bash
# Check current pool stats
curl -s http://localhost:8000/query/stats | jq '.pool'

# Expected output:
{
  "pool_size": 5,
  "active_connections": 5,
  "available_connections": 0,
  "utilization_percentage": 100,
  "peak_utilization": 100,
  "average_wait_time_seconds": 12.5
}

# Check Prometheus metrics
curl -s 'http://localhost:9090/api/v1/query?query=k2_connection_pool_active_connections' | jq
```

**Root Causes**:
1. **Pool too small for load** (most common)
2. **Queries taking too long** (check query durations)
3. **Connection leaks** (connections not released)

**Resolution**:

**Option A: Increase Pool Size**
```bash
# Edit environment config
export K2_QUERY_ENGINE_POOL_SIZE=20

# Or edit pyproject.toml and restart
# pool_size=20

# Restart API service
docker compose restart api
```

**Option B: Investigate Slow Queries**
```bash
# Check p99 query latency
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99, rate(k2_query_duration_seconds_bucket[5m]))' | jq

# If > 10s, queries are too slow - tune queries or increase timeout
```

**Option C: Check for Connection Leaks**
```bash
# Look for connections stuck in "active" state
# Check logs for exceptions during query execution
docker logs api | grep -A 10 "Error executing query"
```

---

### Issue: High Memory Usage

**Symptoms**:
- OOM killer terminating API service
- Memory metrics show steady climb
- `k2_connection_pool_active_connections` high for extended periods

**Diagnosis**:
```bash
# Check memory usage per connection
docker stats api

# Check pool configuration
curl http://localhost:8000/query/stats | jq '.pool.pool_size'

# Calculate expected memory
# pool_size * 50 MB (worst case) = total memory
# Example: 20 connections * 50 MB = 1 GB
```

**Resolution**:
```bash
# Option 1: Reduce pool size
export K2_QUERY_ENGINE_POOL_SIZE=10

# Option 2: Reduce memory limit per connection
export K2_QUERY_ENGINE_MEMORY_LIMIT=2GB

# Option 3: Add resource limits to Docker
# docker-compose.yml:
services:
  api:
    mem_limit: 4g
    mem_reservation: 2g
```

---

### Issue: Connection Creation Failures

**Symptoms**:
- API returns `500 Internal Server Error`
- Logs show "Failed to create connection"
- `k2_connection_pool_creation_errors_total` increasing

**Diagnosis**:
```bash
# Check API logs for connection errors
docker logs api --tail 100 | grep "Failed to create connection"

# Check DuckDB availability
docker exec api uv run python -c "import duckdb; print(duckdb.connect())"

# Check file system (DuckDB needs disk space)
df -h
```

**Root Causes**:
1. Disk space exhausted
2. File descriptor limit reached
3. DuckDB initialization failure

**Resolution**:
```bash
# Check disk space
df -h /path/to/db

# Check file descriptors
ulimit -n

# Increase file descriptor limit if needed
ulimit -n 4096

# Clear temporary DuckDB files
rm -rf /tmp/duckdb-*
```

---

## Tuning Procedures

### Baseline Performance Test

```bash
# 1. Start with small pool
export K2_QUERY_ENGINE_POOL_SIZE=5

# 2. Run load test (Apache Bench)
ab -n 1000 -c 5 http://localhost:8000/v1/trades?symbol=BHP&limit=100

# 3. Record metrics
curl http://localhost:8000/query/stats | jq '.pool'

# 4. Increase pool size and repeat
export K2_QUERY_ENGINE_POOL_SIZE=10
# Restart and repeat step 2-3
```

### Capacity Planning

**Methodology**:
1. Measure peak concurrent queries (from metrics)
2. Add 20% buffer for bursts
3. Consider memory constraints
4. Test under production-like load

**Example Calculation**:
```
Peak concurrent queries (p99): 15
Buffer (20%): 15 * 1.2 = 18
Memory constraint (4GB available): 4096 MB / 50 MB = 80 max
Recommended pool_size: 18 (well within memory limit)
```

---

## Operational Procedures

### Changing Pool Size (Production)

```bash
# 1. Check current load
curl http://localhost:8000/query/stats

# 2. Update configuration
# Edit environment or pyproject.toml

# 3. Restart service gracefully
# Wait for active queries to complete first
while [ $(curl -s http://localhost:8000/query/stats | jq '.pool.active_connections') -gt 0 ]; do
  echo "Waiting for active connections to drain..."
  sleep 5
done

# 4. Restart
docker compose restart api

# 5. Verify new size
curl http://localhost:8000/query/stats | jq '.pool.pool_size'
```

### Emergency Pool Drain

```bash
# If pool appears stuck with active connections

# 1. Stop accepting new requests (load balancer)
# (External step - depends on your setup)

# 2. Wait for drain (max 60s per query)
sleep 120

# 3. Force restart if still stuck
docker compose restart api --timeout 10
```

---

## Performance Characteristics

### Scalability Profile

| Pool Size | Throughput (queries/sec) | Memory (MB) | Notes |
|-----------|--------------------------|-------------|-------|
| 1 | 1 | 50 | Baseline (sequential) |
| 5 | 5 | 250 | Demo/Dev (5x improvement) |
| 10 | 10 | 500 | Light production |
| 20 | 20 | 1000 | Medium production |
| 50 | 50 | 2500 | Heavy production |

**Notes**:
- Linear scalability up to `pool_size`
- Beyond `pool_size`, queries block and wait
- I/O to MinIO/S3 may become bottleneck before pool exhaustion

### Known Bottlenecks

1. **DuckDB per-connection performance**: 1 query/sec per connection (typical)
2. **I/O to object storage**: 100-200ms read latency (S3/MinIO)
3. **Pool exhaustion**: Graceful timeout after 30s

---

## Migration from Single Connection

If upgrading from single-connection setup:

### Before (Single Connection)
```python
engine = QueryEngine()  # Single connection
result = engine.query_trades(symbol="BHP")
```

### After (Connection Pool)
```python
engine = QueryEngine(pool_size=5)  # 5 connections
result = engine.query_trades(symbol="BHP")  # Same API
```

**No code changes required** - pool is transparent to callers.

---

## References

### Implementation
- Source: `src/k2/common/connection_pool.py`
- Engine: `src/k2/query/engine.py`
- Tests: `tests/unit/test_connection_pool.py` (93.6% coverage)

### Reviews
- [Connection Pool Implementation Review](../../reviews/2026-01-13-connection-pool-review.md)
- [Phase 2 Prep Assessment](../../reviews/2026-01-13-phase-2-prep-assessment.md)

### Related Runbooks
- [Query Performance Tuning](./query-performance-tuning.md)
- [API Troubleshooting](./api-troubleshooting.md)
- [DuckDB Operations](./duckdb-operations.md)

---

**Maintained By**: Platform Engineering Team
**Last Tested**: 2026-01-13
**Next Review**: 2026-02-14 (30 days)

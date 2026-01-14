# Demo Screenshots

**Purpose**: Static screenshots for backup if live demo fails
**Created**: Before demo day (2 hours before presentation)
**Total Required**: 10 screenshots minimum

---

## Required Screenshots

### 1. Infrastructure

**File**: `01-docker-compose-ps.png`
- **Content**: Output of `docker compose ps` showing all services "Up"
- **Why**: Proves infrastructure is running
- **Capture**: `docker compose ps` in terminal

### 2. Live Ingestion

**File**: `02-binance-stream-logs.png`
- **Content**: Recent Binance stream logs with trades
- **Why**: Shows live data ingestion
- **Capture**: `docker logs k2-binance-stream --follow` (last 20 lines)

### 3. Kafka Messages

**File**: `03-kafka-messages.png`
- **Content**: Kafka console consumer output showing messages
- **Why**: Demonstrates streaming pipeline
- **Capture**: Kafka consumer output with 5-10 messages visible

### 4. Iceberg Query Results

**File**: `04-iceberg-query-results.png`
- **Content**: Query results from Iceberg table (trades_v2)
- **Why**: Shows storage layer working
- **Capture**: Notebook cell or DuckDB query output

### 5. Prometheus Metrics

**File**: `05-prometheus-metrics.png`
- **Content**: Prometheus metrics dashboard showing 83 metrics
- **Why**: Demonstrates observability
- **Capture**: Prometheus UI at http://localhost:9090

### 6. Grafana Degradation Panel

**File**: `06-grafana-degradation.png`
- **Content**: Grafana dashboard showing degradation level (NORMAL)
- **Why**: Shows circuit breaker monitoring
- **Capture**: Grafana degradation panel

### 7. Hybrid Query API Response

**File**: `07-hybrid-query-api.png`
- **Content**: API response from hybrid query endpoint
- **Why**: Demonstrates Kafka + Iceberg merge
- **Capture**: curl output or Postman/Swagger UI

### 8. Circuit Breaker Demo

**File**: `08-circuit-breaker-demo.png`
- **Content**: Output from simulate_failure.py showing degradation
- **Why**: Demonstrates resilience features
- **Capture**: Terminal showing circuit breaker simulation

### 9. Performance Results

**File**: `09-performance-results.png`
- **Content**: Performance benchmark results table
- **Why**: Evidence-based metrics (p50, p99 latencies)
- **Capture**: Notebook cell with performance table

### 10. Cost Model

**File**: `10-cost-model.png`
- **Content**: Cost comparison table (K2 vs Snowflake vs kdb+)
- **Why**: Business value demonstration
- **Capture**: Cost model table from notebook or docs

---

## Capture Instructions

### macOS
- **Area selection**: `Cmd+Shift+4` → drag to select area
- **Full screen**: `Cmd+Shift+3`
- **Window**: `Cmd+Shift+4` → press `Space` → click window

### Naming Convention
- Use descriptive names: `01-docker-compose-ps.png`
- Number sequentially: 01, 02, 03, ...
- Keep names consistent with list above

### Quality Guidelines
- **Resolution**: 1080p minimum (1920×1080)
- **Text**: Ensure terminal text is readable
- **Focus**: Capture relevant content, minimize distractions
- **Clean**: Close unnecessary windows/tabs before capture

---

## Usage During Demo

If live demo fails:
1. Open this screenshots/ directory
2. Present screenshots in sequence (01 → 10)
3. Narrate each screenshot as if it were live execution
4. Recovery time: 30 seconds to switch

---

**Last Updated**: 2026-01-14
**Status**: Directory created, screenshots to be captured before demo day

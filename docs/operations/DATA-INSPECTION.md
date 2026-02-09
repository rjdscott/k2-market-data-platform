# K2 Market Data Platform - Data Inspection Guide

**Date**: 2026-02-09
**Version**: v2 (Kotlin + Redpanda + ClickHouse)

## Overview

This guide provides commands and techniques for inspecting data flowing through the K2 platform at each stage of the pipeline.

---

## Table of Contents

1. [Quick Health Check](#quick-health-check)
2. [Redpanda Topics](#redpanda-topics)
3. [Schema Registry](#schema-registry)
4. [Feed Handler Logs](#feed-handler-logs)
5. [ClickHouse Data](#clickhouse-data)
6. [Web UIs](#web-uis)
7. [Troubleshooting](#troubleshooting)

---

## Quick Health Check

### Check all services are running

```bash
docker compose -p k2-v2 -f docker-compose.v2.yml -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml ps
```

Expected services:
- `k2-redpanda` (healthy)
- `k2-redpanda-console` (healthy)
- `k2-clickhouse` (healthy)
- `k2-prometheus` (healthy)
- `k2-grafana` (healthy)
- `k2-feed-handler-binance` (healthy)

### Check feed handler metrics (last 60 seconds)

```bash
docker logs --since 60s k2-feed-handler-binance 2>&1 | grep "Metrics"
```

Expected output:
```
📊 Metrics: Raw=10103, Normalized=10103, Errors=0
```

---

## Redpanda Topics

Redpanda is a Kafka-compatible streaming platform. All commands use `rpk` (Redpanda CLI).

### List all topics

```bash
docker exec k2-redpanda rpk topic list
```

Expected topics:
```
market.crypto.trades.binance       3  1
market.crypto.trades.binance.raw   3  1
market.crypto.trades.kraken        3  1
market.crypto.trades.kraken.raw    3  1
```

### Describe a topic (partitions, config)

```bash
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance
```

Shows:
- Partition count
- Replication factor
- Configuration (retention, cleanup policy)
- Leader/follower distribution

### Check topic message counts

```bash
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a
```

Look for "high watermark" - total messages per partition.

### Consume recent messages (raw JSON)

**Raw topic (exchange-specific format):**
```bash
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 5 \
  --format '%v\n---\n'
```

**Normalized topic (canonical format, Avro-encoded):**
```bash
# Note: Avro messages appear as binary without deserialization
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance \
  --num 5 \
  --format '%v\n---\n'
```

### Consume with metadata

```bash
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 3 \
  --format 'Partition: %p | Offset: %o | Timestamp: %t\n%v\n---\n'
```

### Tail a topic (stream mode)

```bash
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --format '%v\n'
```

Press `Ctrl+C` to stop.

### Check consumer groups

```bash
docker exec k2-redpanda rpk group list
```

### Describe consumer group lag

```bash
docker exec k2-redpanda rpk group describe clickhouse_bronze_consumer
```

Shows:
- Current offset per partition
- Log end offset (total messages)
- Lag (messages behind)

---

## Schema Registry

Redpanda includes Confluent-compatible Schema Registry on port 8081.

### List all schemas

```bash
docker exec k2-redpanda curl -s http://localhost:8081/subjects | jq '.'
```

Expected subjects:
```json
[
  "market.crypto.trades.binance-value",
  "market.crypto.trades.binance.raw-value"
]
```

### Get schema versions

```bash
docker exec k2-redpanda curl -s http://localhost:8081/subjects/market.crypto.trades.binance-value/versions
```

### Get latest schema

```bash
docker exec k2-redpanda curl -s \
  http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest \
  | jq '.schema | fromjson'
```

### Get schema by ID

```bash
docker exec k2-redpanda curl -s http://localhost:8081/schemas/ids/1 | jq '.schema | fromjson'
```

### Check schema compatibility

```bash
docker exec k2-redpanda curl -s \
  http://localhost:8081/config/market.crypto.trades.binance-value
```

---

## Feed Handler Logs

### Tail live logs

```bash
docker logs -f k2-feed-handler-binance
```

Press `Ctrl+C` to stop.

### Last 50 lines

```bash
docker logs --tail 50 k2-feed-handler-binance
```

### Last 5 minutes

```bash
docker logs --since 5m k2-feed-handler-binance
```

### Filter for errors only

```bash
docker logs k2-feed-handler-binance 2>&1 | grep -i "error\|exception\|failed"
```

### Filter for metrics only

```bash
docker logs k2-feed-handler-binance 2>&1 | grep "Metrics"
```

### Check connection status

```bash
docker logs --since 60s k2-feed-handler-binance 2>&1 | grep -E "Connected|Subscribed|reconnecting"
```

### View log file inside container

```bash
docker exec k2-feed-handler-binance tail -f /app/logs/feed-handler.log
```

---

## ClickHouse Data

ClickHouse is the OLAP database for aggregations and analytics.

### Interactive SQL client

```bash
docker exec -it k2-clickhouse clickhouse-client
```

Then run SQL:
```sql
USE k2;
SHOW TABLES;
```

Type `exit` or press `Ctrl+D` to quit.

### One-liner queries

```bash
docker exec k2-clickhouse clickhouse-client --query "SELECT count(*) FROM k2.bronze_trades"
```

### Useful queries

**Check bronze layer (raw ingestion from Kafka):**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    count(*) as trades,
    min(exchange_timestamp) as earliest,
    max(exchange_timestamp) as latest
FROM k2.bronze_trades
GROUP BY exchange
"
```

**Check silver layer (validated trades):**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    canonical_symbol,
    count(*) as trades
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol
ORDER BY trades DESC
LIMIT 10
"
```

**Recent trades (last 100):**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    canonical_symbol,
    price,
    quantity,
    side,
    exchange_timestamp
FROM k2.silver_trades
ORDER BY exchange_timestamp DESC
LIMIT 100
FORMAT Pretty
"
```

**OHLCV 1-minute bars (recent):**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    canonical_symbol,
    window_start,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    trade_count
FROM k2.ohlcv_1m
WHERE window_start >= now() - INTERVAL 1 HOUR
ORDER BY window_start DESC
LIMIT 20
FORMAT Pretty
"
```

**Table sizes:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    table,
    formatReadableSize(sum(bytes)) as size,
    sum(rows) as rows
FROM system.parts
WHERE database = 'k2' AND active
GROUP BY table
ORDER BY sum(bytes) DESC
FORMAT Pretty
"
```

**Kafka Engine consumer status:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    table,
    consumer_group,
    num_messages_read,
    last_poll_time,
    last_exception
FROM system.kafka_consumers
FORMAT Pretty
"
```

### Export query results to CSV

```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM k2.silver_trades LIMIT 1000
FORMAT CSV
" > trades.csv
```

### Export to JSON

```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM k2.silver_trades LIMIT 100
FORMAT JSONEachRow
" > trades.json
```

---

## Web UIs

### Redpanda Console (Kafka UI)

**URL**: http://localhost:8080

Features:
- Browse topics and messages
- View consumer groups and lag
- Schema Registry management
- Real-time message inspection
- ACL management

**View messages with schema deserialization:**
1. Navigate to Topics → `market.crypto.trades.binance`
2. Click "Messages"
3. Messages are automatically deserialized using Schema Registry

### Grafana (Metrics & Dashboards)

**URL**: http://localhost:3000
**Credentials**: admin / admin (change on first login)

Pre-configured data sources:
- Prometheus (metrics)
- ClickHouse (data)

**To add ClickHouse dashboard:**
1. Import dashboard from `docs/dashboards/`
2. Or create custom queries against ClickHouse data source

### Prometheus (Metrics Backend)

**URL**: http://localhost:9090

Features:
- Query raw metrics
- View targets and health
- Explore time-series data

**Example queries:**
- `redpanda_kafka_records_produced_total` - Message production rate
- `redpanda_kafka_records_consumed_total` - Consumption rate
- `up{job="redpanda"}` - Service health

---

## Troubleshooting

### Feed handler not producing messages

**Check connection:**
```bash
docker logs --tail 100 k2-feed-handler-binance | grep -E "Connected|error"
```

**Verify Redpanda is reachable:**
```bash
docker exec k2-feed-handler-binance nc -zv redpanda 9092
```

**Check Schema Registry:**
```bash
docker exec k2-feed-handler-binance curl -s http://redpanda:8081/subjects
```

### Topics not receiving messages

**Check topic configuration:**
```bash
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a
```

**Check producer errors in feed handler logs:**
```bash
docker logs k2-feed-handler-binance 2>&1 | grep -i "producer\|error"
```

### ClickHouse not ingesting from Kafka

**Check Kafka Engine consumer:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM system.kafka_consumers FORMAT Pretty
"
```

**Check materialized views:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SHOW CREATE TABLE k2.bronze_trades_mv
"
```

**Manually trigger materialized view:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SYSTEM START VIEW k2.bronze_trades_mv
"
```

**Check ClickHouse logs:**
```bash
docker logs k2-clickhouse 2>&1 | grep -i "kafka\|error"
```

### Schema Registry issues

**Check schema compatibility:**
```bash
docker exec k2-redpanda curl -s \
  http://localhost:8081/compatibility/subjects/market.crypto.trades.binance-value/versions/latest \
  -X POST \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"schema": "<new-schema-json>"}'
```

**Delete schema (if needed - USE CAUTION):**
```bash
# Soft delete
docker exec k2-redpanda curl -X DELETE \
  http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/1

# Hard delete (permanent)
docker exec k2-redpanda curl -X DELETE \
  "http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/1?permanent=true"
```

### Consumer lag issues

**Check lag:**
```bash
docker exec k2-redpanda rpk group describe clickhouse_bronze_consumer
```

**If lag is growing:**
1. Check ClickHouse resource usage: `docker stats k2-clickhouse`
2. Check for ClickHouse errors: `docker logs k2-clickhouse`
3. Consider increasing ClickHouse resources in docker-compose
4. Check if materialized views are running: `SELECT * FROM system.kafka_consumers`

### Resource issues

**Check container resource usage:**
```bash
docker stats
```

**Check specific container:**
```bash
docker stats k2-feed-handler-binance
```

**If OOM (Out of Memory):**
1. Check logs: `docker logs k2-feed-handler-binance 2>&1 | grep -i "oom\|memory"`
2. Increase container memory limits in docker-compose
3. Tune JVM settings: `JAVA_OPTS=-Xmx1024m -Xms512m`

---

## Performance Monitoring

### Message throughput

**Redpanda Console**: http://localhost:8080 → Topics → View message rates

**Via rpk:**
```bash
# Watch topic in real-time
watch -n 1 'docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a | grep -A 5 "Partition"'
```

### Feed handler metrics

**Every 60 seconds, feed handler logs metrics:**
```bash
docker logs -f k2-feed-handler-binance 2>&1 | grep "Metrics"
```

### ClickHouse query performance

**Check running queries:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    query_id,
    user,
    elapsed,
    read_rows,
    query
FROM system.processes
FORMAT Pretty
"
```

**Check slow queries:**
```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    type,
    event_time,
    query_duration_ms,
    read_rows,
    query
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 1000
ORDER BY event_time DESC
LIMIT 10
FORMAT Pretty
"
```

---

## Data Quality Checks

### Check for duplicate trades

```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    trade_id,
    count(*) as occurrences
FROM k2.bronze_trades
GROUP BY exchange, trade_id
HAVING count(*) > 1
ORDER BY occurrences DESC
LIMIT 10
FORMAT Pretty
"
```

### Check for data gaps

```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(exchange_timestamp) as minute,
    count(*) as trades,
    min(exchange_timestamp) as first_trade,
    max(exchange_timestamp) as last_trade
FROM k2.silver_trades
WHERE exchange_timestamp >= now() - INTERVAL 1 HOUR
GROUP BY exchange, canonical_symbol, minute
ORDER BY exchange, canonical_symbol, minute
FORMAT Pretty
"
```

### Validate schema versions

```bash
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    schema_version,
    count(*) as trades,
    min(exchange_timestamp) as first_seen,
    max(exchange_timestamp) as last_seen
FROM k2.bronze_trades
GROUP BY schema_version
FORMAT Pretty
"
```

---

## Backup and Recovery

### Export ClickHouse schema

```bash
docker exec k2-clickhouse clickhouse-client --query "SHOW CREATE DATABASE k2" > backup/schema_k2_database.sql
docker exec k2-clickhouse clickhouse-client --query "SHOW CREATE TABLE k2.bronze_trades" > backup/schema_bronze_trades.sql
```

### Backup ClickHouse data

```bash
docker exec k2-clickhouse clickhouse-client --query "
BACKUP TABLE k2.bronze_trades TO Disk('backups', 'bronze_trades_backup.zip')
"
```

### Export Redpanda topic (sample)

```bash
# Export first 10,000 messages
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 10000 \
  --format '%v\n' > backup/binance_raw_sample.jsonl
```

---

## Next Steps

- **Phase 4**: Complete ClickHouse Kafka Engine integration for real-time ingestion
- **Monitoring**: Set up Grafana dashboards with alerts
- **Testing**: Implement end-to-end integration tests
- **Performance**: Tune Redpanda and ClickHouse for production workloads

---

## Additional Resources

- [Redpanda Documentation](https://docs.redpanda.com/)
- [ClickHouse Documentation](https://clickhouse.com/docs/)
- [Avro Schema Documentation](https://avro.apache.org/docs/)
- [Project ADRs](../decisions/platform-v2/)

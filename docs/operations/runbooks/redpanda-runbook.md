# Runbook: Redpanda Operations

**Severity**: High (message broker — impacts all 3 exchanges if down)
**Last Updated**: 2026-02-18
**Replaces**: `kafka-runbook.md` (Kafka replaced by Redpanda in v2 — ADR-001)

---

## Overview

Redpanda is the Kafka-compatible message broker in v2. It runs as a single-broker cluster
(`k2-redpanda`) with 3 topics:

| Topic | Partitions | Consumers |
|-------|-----------|-----------|
| `binance-trades` | 40 | ClickHouse Kafka Engine |
| `kraken-trades` | 20 | ClickHouse Kafka Engine |
| `coinbase-trades` | 20 | ClickHouse Kafka Engine |

Topics are initialized by the `redpanda-init` one-shot service on startup.

**Redpanda Console**: http://localhost:8080

---

## Topic Management (rpk)

```bash
# List topics
docker exec k2-redpanda rpk topic list

# Describe a topic (partitions, offsets, replicas)
docker exec k2-redpanda rpk topic describe binance-trades

# Create a topic (normally done by redpanda-init)
docker exec k2-redpanda rpk topic create my-topic --partitions 10 --replicas 1

# Delete a topic (use with caution — drops all data)
docker exec k2-redpanda rpk topic delete my-topic

# Produce a test message
echo '{"test": "message"}' | docker exec -i k2-redpanda rpk topic produce binance-trades

# Consume messages from beginning
docker exec k2-redpanda rpk topic consume binance-trades --offset start --num 5
```

---

## Consumer Lag Inspection

Consumer lag indicates how far behind a consumer group is from the latest offset.

```bash
# List all consumer groups
docker exec k2-redpanda rpk group list

# Describe consumer group (show lag per partition)
docker exec k2-redpanda rpk group describe clickhouse-binance-consumer

# Watch lag continuously
watch -n 5 'docker exec k2-redpanda rpk group describe clickhouse-binance-consumer'
```

**Expected state**: ClickHouse consumer groups should have lag < 10,000 in steady state.
If lag is growing continuously, ClickHouse insert rate is below producer rate — see
[ClickHouse investigation](#clickhouse-consumer-not-consuming) below.

---

## Common Issues

### Redpanda Container Not Starting

**Symptoms**: `docker compose ps` shows `k2-redpanda` in `Exited` or `Restarting` state.

**Diagnosis**:
```bash
docker compose -f docker-compose.v2.yml logs redpanda --tail=50
```

**Common causes**:
- Port 9092 already in use: `lsof -i :9092` — stop the conflicting process
- Volume corruption: inspect `docker/redpanda-data/`
- Insufficient disk: `df -h` — Redpanda needs at least 5GB free

**Resolution**:
```bash
# Restart cleanly
docker compose -f docker-compose.v2.yml up -d redpanda
```

---

### Topics Missing After Restart

**Symptoms**: `rpk topic list` shows no topics; ClickHouse Kafka Engine errors.

**Cause**: `redpanda-init` one-shot service only runs on first start. If Redpanda's data
volume was wiped, topics are gone.

**Resolution**:
```bash
# Force redpanda-init to re-run
docker compose -f docker-compose.v2.yml up redpanda-init
```

If `redpanda-init` has already exited (one-shot), recreate it:
```bash
docker compose -f docker-compose.v2.yml up --force-recreate redpanda-init
```

---

### Consumer Group Reset

Use when an offset is corrupted or you need to replay data from a specific point.

```bash
# Reset consumer group to beginning (replay all messages)
docker exec k2-redpanda rpk group seek clickhouse-binance-consumer \
  --to start --topic binance-trades

# Reset to end (skip all backlog)
docker exec k2-redpanda rpk group seek clickhouse-binance-consumer \
  --to end --topic binance-trades

# Reset to specific offset
docker exec k2-redpanda rpk group seek clickhouse-binance-consumer \
  --to 12345 --topic binance-trades
```

> **Warning**: Resetting to start will re-insert all historical messages into ClickHouse.
> ClickHouse MergeTree deduplication (by primary key) prevents duplicates in the bronze
> tables, but this will still cause a large re-insert load.

---

### Disk Full

**Symptoms**: `rpk topic produce` fails with "disk full" or producer error codes.

**Diagnosis**:
```bash
docker exec k2-redpanda df -h /var/lib/redpanda/data
```

**Resolution**:
1. Check retention settings — Redpanda should auto-expire old segments
2. Manually delete old log segments (last resort):
   ```bash
   # Force log compaction / retention cleanup
   docker exec k2-redpanda rpk cluster config set log_cleanup_policy delete
   docker exec k2-redpanda rpk cluster config set delete_retention_ms 3600000  # 1 hour
   ```
3. If persistent, expand Docker volume or add disk

---

### ClickHouse Consumer Not Consuming

**Symptoms**: Consumer lag grows indefinitely; no new rows in bronze tables.

**Diagnosis**:
```bash
# Check ClickHouse Kafka Engine status
docker exec -it k2-clickhouse clickhouse-client --query \
  "SELECT * FROM system.kafka_consumers WHERE database = 'k2' FORMAT Vertical"

# Check for ClickHouse insert errors
docker exec -it k2-clickhouse clickhouse-client --query \
  "SELECT * FROM system.errors WHERE name LIKE '%Kafka%' ORDER BY last_error_time DESC LIMIT 10"
```

**Common resolution**:
```bash
# Detach and re-attach the Kafka Engine table to force reconnect
docker exec -it k2-clickhouse clickhouse-client --query \
  "DETACH TABLE k2.kafka_bronze_binance_raw; ATTACH TABLE k2.kafka_bronze_binance_raw"
```

If persistent, restart ClickHouse:
```bash
docker compose -f docker-compose.v2.yml restart clickhouse
```

---

## Partition Rebalancing

Redpanda handles rebalancing automatically. In a single-broker setup, all partitions are
owned by the single broker — no manual rebalancing is needed.

If adding a second broker (future scale-out), use:
```bash
docker exec k2-redpanda rpk cluster partitions balance
```

---

## Health Check

Quick platform health verification:

```bash
# Broker info
docker exec k2-redpanda rpk cluster info

# Topic offsets (is data flowing?)
docker exec k2-redpanda rpk topic offset-list binance-trades

# Consumer groups summary
docker exec k2-redpanda rpk group list
```

---

## Related

- [ADR-001: Replace Kafka with Redpanda](../../decisions/platform-v2/ADR-001-replace-kafka-with-redpanda.md)
- [Binance Streaming Runbook](./binance-streaming.md)
- [Kraken Streaming Runbook](./kraken-streaming.md)
- [ClickHouse consumer issues](../../decisions/platform-v2/ADR-009-medallion-in-clickhouse.md)

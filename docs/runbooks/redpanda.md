# Runbook: Redpanda Operations

**Severity**: High (message broker — impacts all 3 exchanges if down)
**Last Updated**: 2026-08-26
**Replaces**: `kafka-runbook.md` (Kafka replaced by Redpanda in v2 — ADR-001)

---

## Overview

Redpanda is the Kafka-compatible message broker in v2. It runs as a single-broker cluster
(`k2-redpanda`) with two topics per exchange — raw exchange JSON, and normalized Avro:

| Topic | Partitions | Payload | Consumers |
|-------|-----------:|---------|-----------|
| `market.crypto.trades.binance.raw` | 40 | raw JSON | ClickHouse Kafka Engine |
| `market.crypto.trades.binance` | 40 | Avro | — (reserved for downstream consumers) |
| `market.crypto.trades.kraken.raw` | 20 | raw JSON | ClickHouse Kafka Engine |
| `market.crypto.trades.kraken` | 20 | Avro | — |
| `market.crypto.trades.coinbase.raw` | 20 | raw JSON | ClickHouse Kafka Engine |
| `market.crypto.trades.coinbase` | 20 | Avro | — |

Topics are created by the `redpanda-init` one-shot service at startup, which also hardens
the internal `_schemas` topic (compact cleanup policy, infinite retention) to prevent
`offset_out_of_range` on schema-registry restart.

Ports: Kafka API `9092`, admin API `9644`, schema registry `8081`.

**Redpanda Console**: http://localhost:8080

---

## Topic Management (rpk)

```bash
# List topics
docker exec k2-redpanda rpk topic list

# Describe a topic (partitions, offsets, replicas)
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance.raw

# Create a topic (normally done by redpanda-init)
docker exec k2-redpanda rpk topic create my-topic --partitions 10 --replicas 1

# Delete a topic (use with caution — drops all data)
docker exec k2-redpanda rpk topic delete my-topic

# Produce a test message
echo '{"test": "message"}' | docker exec -i k2-redpanda rpk topic produce market.crypto.trades.binance.raw

# Consume messages from beginning
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw --offset start --num 5
```

---

## Consumer Lag Inspection

Consumer lag indicates how far behind a consumer group is from the latest offset.

```bash
# List all consumer groups
docker exec k2-redpanda rpk group list

# Describe consumer group (show lag per partition)
docker exec k2-redpanda rpk group describe clickhouse_bronze_binance_consumer

# Watch lag continuously
watch -n 5 'docker exec k2-redpanda rpk group describe clickhouse_bronze_binance_consumer'
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
docker compose logs redpanda --tail=50
```

**Common causes**:
- Port 9092 already in use: `lsof -i :9092` — stop the conflicting process
- Volume corruption: inspect the `redpanda-data` named volume (`docker volume inspect k2-market-data-platform_redpanda-data`)
- Insufficient disk: `df -h` — Redpanda needs at least 5GB free

**Resolution**:
```bash
# Restart cleanly
docker compose up -d redpanda
```

---

### Topics Missing After Restart

**Symptoms**: `rpk topic list` shows no topics; ClickHouse Kafka Engine errors.

**Cause**: `redpanda-init` one-shot service only runs on first start. If Redpanda's data
volume was wiped, topics are gone.

**Resolution**:
```bash
# Force redpanda-init to re-run
docker compose up redpanda-init
```

If `redpanda-init` has already exited (one-shot), recreate it:
```bash
docker compose up --force-recreate redpanda-init
```

---

### Consumer Group Reset

Use when an offset is corrupted or you need to replay data from a specific point.

```bash
# Reset consumer group to beginning (replay all messages)
docker exec k2-redpanda rpk group seek clickhouse_bronze_binance_consumer \
  --to start --topic market.crypto.trades.binance.raw

# Reset to end (skip all backlog)
docker exec k2-redpanda rpk group seek clickhouse_bronze_binance_consumer \
  --to end --topic market.crypto.trades.binance.raw

# Reset to specific offset
docker exec k2-redpanda rpk group seek clickhouse_bronze_binance_consumer \
  --to 12345 --topic market.crypto.trades.binance.raw
```

> **Warning**: Resetting to start re-inserts every retained message into ClickHouse, and
> the bronze tables are plain `MergeTree` — **they do not deduplicate**. The `ORDER BY`
> key is a sort key, not a uniqueness constraint. Expect duplicate rows in bronze, which
> propagate to silver and inflate gold candle counts. Only do this if you are prepared to
> deduplicate afterwards, or if the affected partition is being rebuilt anyway. The
> Kafka Engine table name is `k2.<exchange>_trades_queue` — confirm with
> `SHOW TABLES FROM k2`.

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
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT * FROM system.kafka_consumers WHERE database = 'k2' FORMAT Vertical"

# Check for ClickHouse insert errors
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT * FROM system.errors WHERE name LIKE '%Kafka%' ORDER BY last_error_time DESC LIMIT 10"
```

**Common resolution**:
```bash
# Detach and re-attach the Kafka Engine table to force reconnect
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "DETACH TABLE k2.binance_trades_queue; ATTACH TABLE k2.binance_trades_queue"
```

If persistent, restart ClickHouse:
```bash
docker compose restart clickhouse
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

## Measured MTTR

Broker restart, measured against the **v3 capture tier** by
`scripts/chaos/redpanda-stop.sh --exchange kraken` and
`scripts/chaos/capture-queue-full.sh --exchange kraken` on 2026-08-26
([`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv)).
The v2 feed handlers lost the broker at the same instant and are **not** measured here —
they have no equivalent counter.

| # | Failure | Measured |
|---|---------|----------|
| 1 | Broker stopped (`docker stop`, 45 s), then started | producers past their mid-outage level **14 s** after the broker returned, with **no producer restart**. `rpk cluster health` clean |
| 2 | Broker paused (`docker pause`, 388 s), then unpaused | producing again **0 s** after unpause — the first scrape already showed recovery. `rpk cluster health` clean, on a single-node Raft cluster frozen for over six minutes |
| 3 | Records lost during the outage | **7,821** (45 s stopped) and **231,744** (388 s paused), kraken alone. Public feeds do not replay; the windows are permanently absent |
| 4 | Time for `CaptureProduceErrors` to fire | **256 s** from the fault (`for: 5m` on a `[10m]` `increase`) |
| 5 | Consumer-group recovery, ClickHouse and the v2 handlers | not yet verified — no script measures the consumer side |

**Two things this establishes.** The broker comes back clean from both a stop and a
multi-minute pause without manual intervention, and **nothing downstream needs
restarting** — the "restart the producers after the broker returns" instinct is wrong and
costs a second data-loss window. What it does *not* establish is the consumer side; row 5
stays open.

**A caveat on row 3.** Those loss figures predate the `message.timeout.ms` 30 s → 5 min
fix in `sink.rs`
([ADR-019 Outcome](../adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)):
a 45 s outage should have lost nothing at all. Re-run to score the fix.

**Last verified:** 2026-08-26 (`make chaos`).

---

## Health Check

Quick platform health verification:

```bash
# Broker info
docker exec k2-redpanda rpk cluster info

# Topic offsets (is data flowing?)
docker exec k2-redpanda rpk topic offset-list market.crypto.trades.binance.raw

# Consumer groups summary
docker exec k2-redpanda rpk group list
```

---

## Related

- [ADR-001: Replace Kafka with Redpanda](../adr/ADR-001-replace-kafka-with-redpanda.md)
- [Feed handler failure recovery](./failure-recovery.md#3-feed-handler-crash)
- [Adding a new exchange](../operations/adding-new-exchanges.md)
- [ClickHouse consumer issues](../adr/ADR-009-medallion-in-clickhouse.md)

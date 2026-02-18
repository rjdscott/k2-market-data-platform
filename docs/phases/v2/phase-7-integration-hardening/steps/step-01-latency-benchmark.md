# Step 1: End-to-End Latency Benchmark

**Status:** ⬜ Not Started
**Phase:** 7 — Integration Hardening
**Last Updated:** 2026-02-18

---

## Objective

Measure trade-to-candle end-to-end latency across the full v2 pipeline under realistic load.
Establish a latency baseline and validate that the <11ms target is achievable at 1x load.

---

## Latency Budget (7-Segment Model)

| Segment | From → To | Target |
|---------|-----------|--------|
| 1 | Exchange WebSocket → Feed Handler parse | <1ms |
| 2 | Feed Handler → Redpanda (produce) | <2ms |
| 3 | Redpanda → ClickHouse Kafka Engine (consume) | <3ms |
| 4 | Raw queue → Bronze MV (normalisation) | <1ms |
| 5 | Bronze → Silver MV (unification) | <3ms |
| 6 | Silver → Gold MV (OHLCV aggregation) | <1ms |
| **Total** | **Exchange → Gold OHLCV candle** | **<11ms** |

---

## Load Scenarios

| Scenario | Msg/sec | Target p99 Latency | Pass Criteria |
|----------|---------|--------------------|---------------|
| 1x (baseline) | ~50 msg/s | <200ms | No degradation |
| 5x | ~250 msg/s | <500ms | All MVs keeping pace |
| 10x (stress) | ~500 msg/s | <1s | No data loss |

---

## Measurement Approach

### Per-Segment Instrumentation

**Segment 1-2 (Feed Handler → Redpanda)**
```kotlin
// Micrometer timer wrapping produce call
timer.record { producer.send(record) }
// Tag: exchange, symbol
```

**Segment 3-4 (Redpanda → ClickHouse Kafka Engine → Bronze)**
```sql
-- ClickHouse system.query_log: look at query_duration_ms for MV inserts
SELECT query_duration_ms, tables
FROM system.query_log
WHERE tables LIKE '%bronze_trades%'
  AND type = 'QueryFinish'
ORDER BY event_time DESC LIMIT 20;
```

**End-to-End: exchange_timestamp → CH ingestion_timestamp**
```sql
SELECT
  symbol,
  avg(ingestion_timestamp - exchange_timestamp) AS avg_lag_ms,
  quantile(0.99)(ingestion_timestamp - exchange_timestamp) AS p99_lag_ms
FROM k2.bronze_trades_binance
WHERE exchange_timestamp > now() - INTERVAL 5 MINUTE
GROUP BY symbol;
```

### Stress Test Script

Use Redpanda producer replay to inject at 5x / 10x volume:
```bash
# Replay last 1 hour of binance trades at 5x speed
docker exec k2-redpanda rpk topic consume trades-binance --num 10000 | \
  docker exec -i k2-redpanda rpk topic produce trades-binance --compression snappy
```

Or observe natural load by watching during peak exchange hours (UTC 13:00-17:00).

---

## Acceptance Criteria

- [ ] p99 end-to-end latency <200ms at 1x load (~50 msg/s)
- [ ] p99 latency <500ms at 5x load (~250 msg/s)
- [ ] No data loss or MV stalls at 10x load
- [ ] Latency numbers documented per-segment in this file
- [ ] Grafana dashboard panel added: "End-to-End Trade Latency (p50/p99)"

---

## Results (to fill in)

| Scenario | p50 | p99 | Max | Notes |
|----------|-----|-----|-----|-------|
| 1x baseline | -- | -- | -- | |
| 5x stress | -- | -- | -- | |
| 10x stress | -- | -- | -- | |

**Bottleneck identified:** --
**Tuning applied:** --

---

## Related

- [Phase 7 README](../README.md)
- [Step 2: Resource Validation](step-02-resource-validation.md)
- [ARCHITECTURE-V2.md](../../../../decisions/platform-v2/ARCHITECTURE-V2.md)

# Latency Budgets

What "fast enough" means for this pipeline, where the budget is spent, and what was
actually measured. The target that matters: a trade leaving an exchange should be visible
in a gold OHLCV candle in **under 200 ms at p99**.

> **The budget and the measurements below are the v2 pipeline's, kept as a dated record.**
> Segments 3–6 were the ClickHouse `k2` medallion; it froze on 2026-08-26 when the Kotlin
> handlers retired and was **dropped on 2026-08-27** at the Phase E cutover
> ([`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)). Segments 1–2 are live
> and are the Rust `k2-capture` tier's. The served tier today is `gold`
> ([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)): Redpanda →
> `gold.q_trades` (AvroConfluent Kafka engine) → one MV → `gold.trades`, with OHLCV computed
> on read (`gold.ohlcv_live`) rather than by a chain of MVs. Its end-to-end latency has not
> been measured yet; the query to do it is in
> [data-inspection.md](./data-inspection.md#trades--goldtrades).

## The 7-segment budget (v2, as designed)

| # | Segment | Target |
|---|---------|--------|
| 1 | Exchange WebSocket → capture parse | <1 ms |
| 2 | Capture → Redpanda produce | <2 ms |
| 3 | Redpanda → ClickHouse Kafka Engine consume | <3 ms |
| 4 | Raw queue → bronze MV (normalisation) | <1 ms |
| 5 | Bronze → silver MV (unification) | <3 ms |
| 6 | Silver → gold MV (OHLCV aggregation) | <1 ms |
| | **Total, in-platform** | **<11 ms** |

Eleven milliseconds of processing inside a 200 ms end-to-end budget. The remaining ~190 ms
is network round-trip to the exchange, which is not ours to optimise — it dominates, and
that is the expected shape for a platform running outside a colocation facility.

The design that makes this possible is [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md):
v1 spent 5–15 **minutes** here because every hop went through a Spark Structured
Streaming micro-batch. Replacing those with ClickHouse materialized views moved the whole
transform chain in-process.

## Load scenarios

| Scenario | Rate | Target p99 | Pass criteria |
|----------|------|-----------|---------------|
| 1× baseline | ~150 msg/s (100–200) | <200 ms | No degradation |
| 5× | ~750 msg/s | <500 ms | All MVs keeping pace |
| 10× stress | ~1500 msg/s | <1 s | No data loss |

## Measured (2026-02-19)

Exchange → silver end-to-end lag, computed as
`ingestion_timestamp - timestamp` on `k2.silver_trades` over a 1-hour window at 1×
baseline. That table was dropped on 2026-08-27, so the query cannot be re-run; the
equivalent on `gold.trades` (`recv_ts_ns` against `exchange_ts`) is in
[data-inspection.md](./data-inspection.md#trades--goldtrades).

| Exchange | p50 | p95 | p99 | Max | n | Target <200 ms |
|----------|-----|-----|-----|-----|---|----------------|
| Binance | 91 ms | 183 ms | 191 ms | 193 ms | 12 | pass |
| Coinbase | 87 ms | 188 ms | 197 ms | 199 ms | 13 | pass |
| Kraken | 71 ms | 162 ms | 170 ms | 172 ms | 12 | pass |

**Caveat — read this before quoting the numbers.** The stack was started cold and each
exchange contributed only **12–13 trades**. At that sample size a "p99" is really the
slowest observation, so these results are directionally valid but not statistically
meaningful. They should be re-measured after a 24-hour burn-in.

**5× and 10× load tests were not run.** The method is Redpanda topic replay at the target
multiple; it remains outstanding work.

Supporting observations from the same run:

- **Kafka Engine consumers healthy** — 66 messages read and 9 commits in the first five
  minutes from a cold start, no exceptions in `system.kafka_consumers`.
- **Bronze → silver is effectively instant.** The delta between bronze and silver
  `ingestion_timestamp` was not measurable at microsecond resolution.
- **Bottleneck is network RTT**, averaging ~80 ms. Every in-platform segment is far below
  its budget, so no tuning was applied at 1×.
- ClickHouse 24.3 does not surface Kafka Engine background inserts in `system.query_log`
  as `query_kind = 'Insert'`. They appear under the `InsertedRows` system event instead —
  220,320 rows after startup, confirming the backlog replayed.

## Producer configuration

The capture tier's librdkafka producer is configured in
[`sink.rs`](../../services/capture-rust/src/sink.rs), and the settings are fixed rather
than tunable — there is one buffer in this tier and it is sized against the container's
memory limit:

```rust
queue.buffering.max.kbytes = 32768        // 32 MB, the only buffer
message.max.bytes          = 8388608      // 8 MiB; matches the WebSocket cap in ws.rs
enable.idempotence         = true         // a retry cannot duplicate a record
acks                       = "all"        // durability first
compression.type           = "zstd"
message.timeout.ms         = 30000        // drop and count rather than pin forever
```

`message.timeout.ms = 30000` is the one worth understanding: a record still unsent after
30 s is failed and counted rather than held, because the lake is append-only and a record
that stale is better lost visibly than pinned behind a dead broker. There is no
`linger.ms` tuning here — the venue's frame arrival rate, not batching, sets the cadence.

## What degrades under load

The stack has no explicit backpressure machinery — it relies on Redpanda as the buffer,
which is the right trade at this scale:

1. **Capture saturates** → librdkafka's 32 MB queue fills and records are **dropped**, not
   buffered: blocking the frame loop would stop us reading the socket and the venue would
   drop us instead, losing more. `CaptureProduceStalled` fires first (deliveries flat
   while produces climb), then `k2_capture_produce_errors_total{reason="queue_full"}`
   ticks and `CaptureProduceErrors` fires. This is the one level where the failure mode
   is loss rather than lag — see [../runbooks/capture-produce-stalled.md](../runbooks/capture-produce-stalled.md).
2. **ClickHouse ingest saturates** → the `gold.q_trades` / `gold.q_book` consumers lag.
   Data is safe in Redpanda for the retention window (7 d on `trades.*`/`book.*`), and
   the lake holds it for good. `ClickHouseGoldFeedStale` fires when the consumers go
   silent while capture is still delivering; a lagging-but-moving consumer is visible in
   `rpk group describe k2-gold-trades`.
3. **Merges saturate** → merge queue grows; `ClickHouseMergeQueueLarge` fires. Queries
   degrade before ingest does, and `FINAL` gets more expensive until the merges catch up.
4. **Lake ingest saturates** → the 5-minute cycles fall behind the topics;
   `LakeIngestLagHigh` fires. The served tier is unaffected — the lake simply lags, and
   `raw.*` retention (48 h) is the deadline on catching up.

Nothing here drops data silently — the one level that drops (1) counts every record it
loses. Below the capture tier the failure mode is *lag*, not loss, which is what the
failure-mode testing in
[../runbooks/failure-recovery.md](../runbooks/failure-recovery.md) confirms.

## Related

- [observability.md](./observability.md) — the alerts that watch these thresholds
- [data-inspection.md](./data-inspection.md) — the lag query used above
- [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md) — why the budget is milliseconds and not minutes
- [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md) — Redpanda's p99 vs Kafka's on segment 3

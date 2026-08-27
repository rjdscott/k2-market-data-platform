# Latency Budgets

Where time is spent between a trade at the venue and the row a reader sees, per path, and
what was measured. This is a research platform on public WebSocket feeds over the internet:
the dominant segment is transit plus the venue's clock skew, and it is published as such
([ADR-018 non-goals](../adr/ADR-018-v3-lake-first-rust-capture.md)).

## Segments

| # | Segment | Bound | Set by |
|---|---------|-------|--------|
| 1 | Venue timestamp → frame received (`recv_ts_ns`) | transit + venue skew; **measured below** | the internet |
| 2 | Frame → parsed records | < 1 ms, single-threaded, no allocation on the hot path | [`handle_frame`](../../services/capture-rust/README.md) |
| 3 | Record → Redpanda acked | < 2 ms; queue is the only buffer | [`sink.rs`](../../services/capture-rust/src/sink.rs), settings below |
| 4a | Redpanda → ClickHouse `gold.trades` visible | ≤ 5 s | `kafka_flush_interval_ms = 5000` in [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql) |
| 4b | Redpanda → lake `raw.messages` committed | 5 min cron + run wall time | `lake-ingest-5min` ([prefect-schedules.md](./prefect-schedules.md)); `k2_lake_ingest_lag_seconds` |
| 5 | Lake gold → ClickHouse `ohlcv_*` / `bbo_1s` | operator pull; 10.4 M trades in 4.4 s | [clickhouse-rebuild-from-lake.md](../runbooks/clickhouse-rebuild-from-lake.md) |

Two read paths follow: **live** (`gold.ohlcv_live`, `gold.bbo_live`, computed on read over
`FINAL`) sees a trade after segments 1–4a; **research** (lake `gold.*`, DuckDB) after 1–4b plus
the layer stages of the same run.

## Measured: segment 1, 2026-08-27

Per frame, from the capture histogram, 6.5 h window, three venues
([benchmarks/2026-08-27.md § Latency](../benchmarks/2026-08-27.md#latency--exchange-timestamp--k2-receive)
carries the commands):

| Exchange | n | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---:|---:|---:|---:|
| Binance | 2,732,367 | 42.2 | 95.9 | 206.8 |
| Kraken | 47,792 | 176.8 | 245.9 | 459.1 |
| Coinbase | 416,325 | 184.1 | 472.5 | 30,000 (top bucket) |

The Coinbase p99 is the venue's on-subscribe trade *snapshot*, whose `exchange_ts` values
predate the connection; the capture now excludes those frames from the histogram
(`Handled.history`), and the lake keeps them as trades. The per-trade distribution over the lake
(`recv_ts_ns − exchange_ts`) is in the same benchmark section. Segments 2–5 are bounds by
construction or configuration and have not been measured end to end; the query that would do
it is in [data-inspection.md](./data-inspection.md#trades--goldtrades).

The v2 pipeline's 7-segment budget and its 2026-02-19 measurements are a dated record in
[benchmarks/2026-02-19-v2-baseline.md](../benchmarks/2026-02-19-v2-baseline.md).

## Producer configuration

The capture tier's librdkafka producer is configured in
[`sink.rs`](../../services/capture-rust/src/sink.rs); the settings are fixed rather than
tunable, one buffer, sized against the container's memory limit:

```rust
queue.buffering.max.kbytes = 32768        // 32 MB, the only buffer
message.max.bytes          = 8388608      // 8 MiB; matches the WebSocket cap in ws.rs
enable.idempotence         = true         // a retry cannot duplicate a record
acks                       = "all"        // durability first
compression.type           = "zstd"
message.timeout.ms         = 30000        // drop and count rather than pin forever
```

`message.timeout.ms = 30000` is the one worth understanding: a record still unsent after
30 s is failed and counted rather than held, because a record that stale is better lost
visibly than pinned behind a dead broker. No `linger.ms` tuning, the venue's frame arrival
rate, not batching, sets the cadence.

## What degrades under load

No explicit backpressure machinery; Redpanda is the buffer, which is the right trade at this
scale:

1. **Capture saturates** → librdkafka's 32 MB queue fills and records are **dropped**, not
   buffered: blocking the frame loop would stop us reading the socket and the venue would
   drop us instead, losing more. `CaptureProduceStalled` fires first (deliveries flat
   while produces climb), then `k2_capture_produce_errors_total{reason="queue_full"}`
   ticks and `CaptureProduceErrors` fires. The one level where the failure mode is loss
   rather than lag, [capture-produce-stalled.md](../runbooks/capture-produce-stalled.md).
   After the queue drains, a book stream resubscribes for a fresh snapshot.
2. **ClickHouse ingest saturates** → the `gold.q_trades` / `gold.q_book` consumers lag.
   Data is safe in Redpanda for the retention window (7 d on `trades.*` / `book.*`) and in
   the lake for good. `ClickHouseGoldFeedStale` fires when the consumers go silent while
   capture is still delivering; a lagging-but-moving consumer shows in
   `rpk group describe k2-gold-trades`.
3. **Merges saturate** → `ClickHouseMergeQueueLarge` fires. Queries degrade before ingest
   does, and `FINAL` gets more expensive until the merges catch up.
4. **Lake ingest saturates** → the 5-minute cycles fall behind the topics;
   `LakeIngestLagHigh` fires. The served tier is unaffected; `raw.*` retention (48 h) is the
   deadline on catching up.

Nothing here drops data silently, the one level that drops (1) counts every record it
loses; below capture the failure mode is lag, not loss
([failure-modes.md](../architecture/16-failure-modes.md)).

## Related

- [observability.md](./observability.md), the alerts that watch these thresholds
- [data-inspection.md](./data-inspection.md), the lag queries
- [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md), Redpanda's p99 vs Kafka's on segment 3

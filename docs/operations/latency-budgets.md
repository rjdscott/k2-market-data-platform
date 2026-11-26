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
| 4a | Redpanda → ClickHouse `gold.trades` visible, **paused** ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) | ≤ 5 s while it runs | `kafka_flush_interval_ms = 5000` <!-- counts-ok: a setting in milliseconds --> in [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql) |
| 4b | Redpanda → lake | none: nothing reads the topics into the lake until phase 004 | the v3 lake ingest and its 5-minute DAG, retired and archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/) |
| 5 | Lake → ClickHouse | none: the reload from the lake's gold retired with it | [pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md) |

Two read paths followed these segments, and both are stood down while the platform is rebuilt:
**live** (ClickHouse's views computed on read over `FINAL`, after segments 1 to 4a) is paused, and
**research** over the v3 lake is retired. The lake path returns as products in
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan, which sets its own bounds.

## Measured: segment 1, 2026-08-27

Per frame, from the capture histogram, every trade frame of a 6.5 h window, three venues
([benchmarks/2026-08-27.md § Latency](../benchmarks/2026-08-27.md#latency--exchange-timestamp--k2-receive)
carries the commands):

| Exchange | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---:|---:|---:|
| Binance | 42.2 | 95.9 | 206.8 |
| Kraken | 176.8 | 245.9 | 459.1 |
| Coinbase | 184.1 | 472.5 | 30,000 ms (top bucket) |

The Coinbase p99 is the venue's on-subscribe trade *snapshot*, whose `exchange_ts` values
predate the connection; the capture now excludes those frames from the histogram
(`Handled.history`), and the v3 lake kept them as trades. The per-trade distribution over that
lake (`recv_ts_ns − exchange_ts`) is in the same benchmark section. Segments 2 to 4a are bounds by
construction or configuration and have not been measured end to end; the query that would have
done it is archived with [data-inspection.md](./data-inspection.md).

The v2 pipeline's 7-segment budget and its 2026-02-19 measurements are a dated record in
[benchmarks/2026-02-19-v2-baseline.md](../benchmarks/2026-02-19-v2-baseline.md).

## Producer configuration

The capture tier's librdkafka producer is configured in
[`sink.rs`](../../services/capture-rust/src/sink.rs); the settings are fixed rather than
tunable, one buffer, sized against the container's memory limit:

```rust
queue.buffering.max.kbytes = 32768        // 32 MB, the only buffer
message.max.bytes          = 8388608      // 8 MiB; matches the WebSocket cap in ws.rs (counts-ok: a setting in bytes)
enable.idempotence         = true         // a retry cannot duplicate a record
acks                       = "all"        // durability first
compression.type           = "zstd"
message.timeout.ms         = 300000       // 5 min: drop and count rather than pin forever (counts-ok: a setting in ms)
```

`message.timeout.ms = 300000` <!-- counts-ok: a setting in milliseconds --> is the one worth understanding: a record still unsent after
5 minutes is failed and counted rather than held, because a record that stale is better lost
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
2. **ClickHouse ingest saturates**, only while the paused hot tier runs → the `gold.q_trades` /
   `gold.q_book` consumers lag. Data is safe in Redpanda for the retention window (7 d on
   `trades.*` / `book.*`). A lagging-but-moving consumer shows in
   `rpk group describe k2-gold-trades`; the ClickHouse alerts were removed with the pause.
3. **Merges saturate**, the same → queries degrade before ingest does, and `FINAL` gets more
   expensive until the merges catch up.

The one level that drops (1) counts every record it loses
([failure-modes.md](../architecture/16-failure-modes.md)). Until phase 004 one more loss is
certain and known in advance: nothing reads the topics into the lake, so Redpanda deletes what it
holds past its retention (48 h or 2 GiB per partition on `raw.*`) unread.

## Related

- [observability.md](./observability.md), the alerts that watch these thresholds
- [ADR-001](../adr/ADR-001-replace-kafka-with-redpanda.md), Redpanda's p99 vs Kafka's on segment 3

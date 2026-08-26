# Latency Budgets

What "fast enough" means for this pipeline, where the budget is spent, and what was
actually measured. The target that matters: a trade leaving an exchange should be visible
in a gold OHLCV candle in **under 200 ms at p99**.

## The 7-segment budget

| # | Segment | Target |
|---|---------|--------|
| 1 | Exchange WebSocket → feed handler parse | <1 ms |
| 2 | Feed handler → Redpanda produce | <2 ms |
| 3 | Redpanda → ClickHouse Kafka Engine consume | <3 ms |
| 4 | Raw queue → bronze MV (normalisation) | <1 ms |
| 5 | Bronze → silver MV (unification) | <3 ms |
| 6 | Silver → gold MV (OHLCV aggregation) | <1 ms |
| | **Total, in-platform** | **<11 ms** |

Eleven milliseconds of processing inside a 200 ms end-to-end budget. The remaining ~190 ms
is network round-trip to the exchange, which is not ours to optimise — it dominates, and
that is the expected shape for a platform running outside a colocation facility.

The design that makes this possible is [ADR-004](../decisions/ADR-004-eliminate-spark-streaming.md):
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
baseline. Reproduce it with the lag query in
[data-inspection.md](./data-inspection.md#silver--unified-trades).

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

The feed handler's Kafka producer is tuned for latency over throughput, in
[`application.conf`](../../services/feed-handler-kotlin/src/main/resources/application.conf):

```hocon
acks = "all"                              # durability first
enable-idempotence = true                 # exactly-once produce
linger-ms = 10                            # 10ms max batching delay
batch-size = 16384
compression-type = "lz4"                  # fast, not maximal
max-in-flight-requests-per-connection = 5
```

`linger-ms = 10` is the one knob worth understanding: it buys batching efficiency for at
most 10 ms of added latency, which is under 5% of the 200 ms budget and roughly an order
of magnitude below the network RTT it sits behind. Raising it is the first thing to try if
throughput ever becomes the constraint instead of latency.

## What degrades under load

The stack has no explicit backpressure machinery — it relies on Redpanda as the buffer,
which is the right trade at this scale:

1. **Feed handler saturates** → the Kafka producer blocks; the WebSocket read loop stalls
   and the exchange's own buffer absorbs it. Watch `feed_handler_errors_total`.
2. **ClickHouse ingest saturates** → the Kafka Engine consumer lags. Data is safe in
   Redpanda for the retention window; `ClickHouseBronzeInsertRateLow` fires. Watch
   consumer lag with `rpk group describe`.
3. **Materialized views saturate** → merge queue grows; `ClickHouseMergeQueueLarge` fires.
   Queries degrade before ingest does.
4. **Offload saturates** → cycles overrun the 15-minute schedule;
   `IcebergOffloadCycleTooSlow` fires. The hot tier is unaffected — cold simply lags.

Nothing here drops data silently. The failure mode at every level is *lag*, not loss,
which is what the failure-mode testing in
[runbooks/failure-recovery.md](./runbooks/failure-recovery.md) confirms.

## Related

- [observability.md](./observability.md) — the alerts that watch these thresholds
- [data-inspection.md](./data-inspection.md) — the lag query used above
- [ADR-004](../decisions/ADR-004-eliminate-spark-streaming.md) — why the budget is milliseconds and not minutes
- [ADR-001](../decisions/ADR-001-replace-kafka-with-redpanda.md) — Redpanda's p99 vs Kafka's on segment 3

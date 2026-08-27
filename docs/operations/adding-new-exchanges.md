# Adding a New Exchange

## Overview

A step-by-step checklist for adding a fourth venue to the v3 Rust capture tier.

**Architecture**: Exchange WebSocket → one `k2-capture` container → three Avro topics
(`market.crypto.v3.{raw,trades,book}.<exchange>`).

Downstream of the topics, the ClickHouse `gold` feeds (step 8) and the lake's per-venue
decoders (post-integration) are the two places a fourth venue has to be named.

The Kotlin handlers this checklist used to describe are archived at
[`legacy/v2-kotlin/README.md`](../../legacy/v2-kotlin/README.md)
([ADR-019](../adr/ADR-019-rust-capture-tier.md)).

## Prerequisites

- [ ] Exchange API documentation reviewed
- [ ] WebSocket endpoint identified, and whether the subscription rides in the URL
      (Binance) or in a frame after connect (Kraken, Coinbase)
- [ ] Sample trade **and** L2 book frames captured
- [ ] Symbol format documented, exactly as the venue spells it on the wire
      (`BTCUSDT`, `BTC/USD`, `BTC-USD`)
- [ ] Timestamp format documented
- [ ] Continuity signal identified: a sequence number, a checksum, or neither — this is
      what decides the resync policy in step 1

## Implementation Checklist

### 1. Capture adapter (Rust)

**File**: `services/capture-rust/src/exchanges/{exchange}.rs`

- [ ] Add the module to [`exchanges/mod.rs`](../../services/capture-rust/src/exchanges/mod.rs)
      and a variant to `enum Adapter`; the compiler then lists every `match` that has to
      learn about it
- [ ] Implement `new`, `begin_connection`, `symbols`, `subscribe_messages`,
      `resubscribe_messages`, `handle_frame` and `snapshot`
- [ ] Decide the continuity policy: `Action::Resubscribe(symbol)` if a gap is
      attributable to one product, `Action::Reconnect` if it is connection-wide

`handle_frame` must be **pure** — no I/O, no clock reads, no randomness, no `HashMap`
iteration on an emit path. That purity is what lets the replay test in step 7 feed
archived frames back through the same code and assert the bytes out are identical. The
four obligations are spelled out at the top of `exchanges/mod.rs`:

1. A `RawMessage` for **every** frame, first, payload byte-for-byte — including frames
   that failed to parse.
2. The adapter owns `conn_msg_seq`; `begin_connection` resets it.
3. Book state is internal and leaves only through `snapshot()`.
4. Return an `Action`; never perform one.

**Reference**: [`coinbase.rs`](../../services/capture-rust/src/exchanges/coinbase.rs) for a
connection-wide sequence number, [`kraken.rs`](../../services/capture-rust/src/exchanges/kraken.rs)
for CRC32 verification with no sequence at all, and
[`binance.rs`](../../services/capture-rust/src/exchanges/binance.rs) for a stateless
partial-depth stream.

---

### 2. Exchange enum, endpoint and stream table

**Files**: [`config.rs`](../../services/capture-rust/src/config.rs),
[`main.rs`](../../services/capture-rust/src/main.rs)

- [ ] Add the variant to `enum Exchange`, its lowercase id in `as_str()` (this string goes
      in the topic name and in every metric label), and its public endpoint in
      `default_ws_url()`
- [ ] Add the `Adapter::` construction arm and a `{EXCHANGE}_STREAMS` list in `main.rs`
- [ ] Add each stream name the venue uses to `CONTINUOUS` **only if it genuinely runs
      continuously**, with its own staleness bound — 60 s for a book/heartbeat channel,
      300 s for a trade channel

`CONTINUOUS` is one table read by three things: the session watchdog, the
`k2-capture healthcheck` subcommand, and `CaptureFeedStale`. Putting a one-shot subscribe
acknowledgement in it fires a permanent critical about two minutes after every healthy
connect; leaving a genuinely continuous stream out means a silently-rejected subscription
has no series and the alert cannot fire on the failure it exists for.

---

### 3. Instrument registry

**File**: [`config/instruments.yaml`](../../config/instruments.yaml)

- [ ] Add an `instruments.{exchange}` block, one row per symbol:

```yaml
  {exchange}:
    - { native: BTC-USD, canonical: BTC/USD }
```

`native` is **exactly the bytes on the wire**, byte for byte — it is what goes in the
subscribe frame and what comes back on every message. `canonical` is `BASE/QUOTE`,
uppercase, and is the Kafka key and the lake join key. Nothing translates a symbol
anywhere in the crate; a native the file does not list is a loud failure, not a guess.
Keep the quote currency as the venue quotes it — `BTC/USDT` and `BTC/USD` are different
instruments, not two spellings of one.

- [ ] Update the instrument count in the file header — `tests/test_contracts.py` asserts it

> **Bind-mount gotcha:** `instruments.yaml` is mounted file-by-file, which pins the inode.
> Editors that write-then-rename produce a new inode and the container keeps reading the
> old file. After editing, run
> `docker compose up -d --force-recreate --no-deps capture-{exchange}` —
> `docker restart` will **not** pick up the change.

---

### 4. Topics and Avro subjects

**File**: [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh)

- [ ] Add the venue to `EXCHANGES`. That one word creates all three topics
      (`${V3_PREFIX}.{raw,trades,book}.{exchange}`, 12 partitions each), applies the raw
      retention and 8 MiB `max.message.bytes`, and registers the three Avro subjects under
      `TopicNameStrategy`

No new schema is needed: all three venues share `raw-message.avsc`, `trade.avsc` and
`book-snapshot-l2.avsc`. If the venue needs a *field* nobody has, that is a schema change
across every place the contract lives — use `/schema-change`, not this checklist.

---

### 5. Compose service

**File**: [`docker-compose.yml`](../../docker-compose.yml) (repo root — all services live
in one file)

- [ ] Copy `capture-coinbase`, change the name, `hostname`, `container_name` and
      `K2_EXCHANGE`. It reuses the shared `x-capture-build` anchor, so there is no second
      image to build:

```yaml
capture-{exchange}:
  build: *capture-build
  image: k2-capture:v3
  container_name: k2-capture-{exchange}
  hostname: capture-{exchange}
  networks:
    - k2-net
  depends_on:
    redpanda-init:
      condition: service_completed_successfully
  environment:
    - K2_EXCHANGE={exchange}
    - K2_INSTRUMENTS_FILE=/app/config/instruments.yaml
    - K2_KAFKA_BROKERS=redpanda:9092
    - K2_SCHEMA_REGISTRY_URL=http://redpanda:8081
    - K2_METRICS_PORT=8082
    - K2_SNAPSHOT_INTERVAL_MS=1000
    - K2_TOPIC_PREFIX=market.crypto.v3
    - RUST_LOG=info
  volumes:
    - ./config:/app/config:ro
  cpuset: ${K2_CAPTURE_CPUSET-12-14}
  deploy:
    resources:
      limits:
        cpus: '0.25'
        memory: 256M      # 512M if the venue publishes full-depth L2
      reservations:
        cpus: '0.1'
        memory: 128M
  healthcheck:
    test: ["CMD", "/k2-capture", "healthcheck"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 60s
  restart: unless-stopped
  labels:
    com.k2.service: "capture"
    com.k2.exchange: "{exchange}"
    com.k2.tier: "hot"
    com.k2.version: "v3"
```

The healthcheck is the binary's own subcommand, not `curl` — the runtime image is
distroless and has neither a shell nor curl.

- [ ] Update the resource summary comment at the top of `docker-compose.yml` and the
      totals in [docker-resources.md](./docker-resources.md) (+0.25 CPU / +256 MB, or
      +512 MB for a full-depth venue), and append to
      [ADR-010](../adr/ADR-010-resource-budget.md)'s Outcome

---

### 6. Observability

- [ ] Add a scrape job in [`docker/prometheus/prometheus.yml`](../../docker/prometheus/prometheus.yml)
      targeting `capture-{exchange}:8082`, named `capture-{exchange}`, with
      `service`/`tier` labels and **no `exchange` target label** — the binary emits its own
      `exchange` on every series, and a target label of the same name would rename the
      sample's to `exported_exchange`

Nothing else in observability needs editing. `CaptureDown` matches `job=~"capture-.*"`;
every other capture alert keys off the `exchange` label the binary emits; and the
`exchange` template variable on the `k2-l2-capture` dashboard is
`label_values(k2_capture_messages_total, exchange)`, so all three discover the new venue
on their own.

---

### 7. Fixture and replay test

**Files**: `services/capture-rust/tests/replay_{exchange}.rs`,
`services/capture-rust/tests/fixtures/{exchange}-NNs.jsonl`

- [ ] Record a session with `k2-capture record --exchange {exchange} --symbols <native>
      --seconds 20 > fixture.jsonl`. The recorder goes through the same `ws_url()` the
      live path does, so the fixture is the conversation capture actually has
- [ ] Add a replay test in the shape of
      [`replay_coinbase.rs`](../../services/capture-rust/tests/replay_coinbase.rs): drive
      the fixture through the live adapter's `handle_frame`, assert the book invariants
      (top-20, sorted, uncrossed, no zero quantities) and the venue's own continuity
      check, then hash two passes against a committed golden value
- [ ] Load `config/instruments.yaml` directly in the test — no test-only copy of the
      registry
- [ ] If the fixture had to be trimmed to keep it committable, say exactly what was
      trimmed in the test's module docs, as all three existing ones do
- [ ] `make test` green before opening the PR — see
      [../development/testing.md](../development/testing.md)

---

### 8. ClickHouse gold feeds

**File**: [`docker/clickhouse/ddl/20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql)

- [ ] Add `market.crypto.v3.trades.{exchange}` to `gold.q_trades`'s `kafka_topic_list` and
      `market.crypto.v3.book.{exchange}` to `gold.q_book`'s. The list is literal — a topic
      not named there is never read, and nothing alerts on a venue that was never attached.
      `gold.trades` / `gold.book_top20` need no change: `exchange` is a column, not a table
- [ ] Re-attach the feeds on the running server — a Kafka-engine table's settings are fixed
      at CREATE, so drop and recreate `gold.q_trades` / `gold.q_book` and their MVs from the
      file ([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#applying-it-to-a-running-server));
      the group resumes from its committed offsets on the existing topics and starts the new
      one at `kafka_auto_offset_reset`

---

## Testing Steps

### 1. Build and deploy

```bash
make test-rust                                        # the replay test first
docker compose up -d --build capture-{exchange}
```

### 2. Verify capture

```bash
docker logs -f k2-capture-{exchange}
docker exec k2-capture-{exchange} /k2-capture healthcheck
```

`healthcheck` exits non-zero and names the offending stream if any continuous stream is
past its bound — which is also the fastest way to find a `CONTINUOUS` entry you got wrong
in step 2.

### 3. Verify the topics

```bash
docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.{exchange}    --num 3 --format '%v\n'
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.{exchange} --num 3 --format '%k\n'
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
```

### 4. Verify the metrics

```bash
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange, kind) (rate(k2_capture_records_produced_total[5m]))' | jq

# Delivered, not just produced — produced climbs straight through a broker outage
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange) (rate(k2_capture_records_delivered_total[5m]))' | jq
```

Then check that `k2_capture_gaps_total`, `k2_capture_produce_errors_total` and
`k2_capture_precision_loss_total` are all present **and zero** for the new venue. Present
matters as much as zero: every one of them is seeded at startup precisely so its first
event is detectable, and an absent series means the seeding was missed.

### 5. Verify the served tier

```bash
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count(), max(exchange_ts) FROM gold.trades FINAL
   WHERE exchange_ts > now() - INTERVAL 5 MINUTE GROUP BY exchange"
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT count() FROM gold.feed_errors WHERE topic LIKE '%{exchange}'"
```

The new venue appears as a fourth `exchange` value; a non-zero `feed_errors` count for its
topics means the Avro the adapter produced is not what `20-gold-kafka.sql` declares.

---

## Common Patterns by Exchange Type

### Pattern 1: Binance-like (subscription in the URL)
- Symbol format: no separator (BTCUSDT)
- Book: fixed-depth partial stream, no exchange timestamp
- Continuity: a monotonic update id; resync by dropping the book and waiting for the next
  in-order frame
- **Examples**: Binance, Bybit, OKX

### Pattern 2: Kraken-like (checksum, no sequence)
- Symbol format: slash separator (BTC/USD)
- Book: depth parameter from a fixed menu; CRC32 over the top levels
- Continuity: the checksum. Resync per symbol, emitting one last snapshot marked
  `checksum_ok=false` **before** dropping the book
- **Examples**: Kraken, potentially Bitstamp

### Pattern 3: Coinbase-like (connection-wide sequence)
- Symbol format: dash separator (BTC-USD)
- Book: full depth, truncated locally
- Continuity: one `sequence_num` across every channel, so a gap cannot be attributed to a
  product — drop every book and reconnect
- **Examples**: Coinbase Advanced Trade, Gemini

---

## Native and canonical symbols

Nothing maps a symbol in code. Both columns are data, in `config/instruments.yaml`:

| Exchange | `native` (bytes on the wire) | `canonical` |
|----------|------------------------------|-------------|
| Binance  | BTCUSDT                      | BTC/USDT    |
| Kraken   | BTC/USD                      | BTC/USD     |
| Coinbase | BTC-USD                      | BTC/USD     |

Kraken's natives were `XBT/USD` and `XDG/USD` under WS v1. This platform speaks v2, which
spells them `BTC/USD` and `DOGE/USD` and rejects the old ones outright; the translation
table that used to bridge the two went with the Kotlin handlers.

---

## Troubleshooting Checklist

- [ ] **Connection**:
  - [ ] WebSocket connected? (`docker logs k2-capture-{exchange}`)
  - [ ] Subscription acknowledged, or silently rejected? A rejected subscription looks
        healthy until `k2_capture_last_message_ts_seconds` for that stream goes stale
  - [ ] `k2_capture_unknown_frames_total` climbing? The adapter is not recognising frames
        it is receiving — they are still archived, so the fix is replayable

- [ ] **Produce**:
  - [ ] `records_produced_total` climbing but `records_delivered_total` flat? The broker or
        the registry is the problem, not the venue — `CaptureProduceStalled`
  - [ ] `produce_errors_total{reason="queue_full"}` ticking? Records are being **lost**;
        there is no spill-to-disk

- [ ] **Book**:
  - [ ] `book_depth` at or near the expected top-20-per-side?
  - [ ] `gaps_total` / `checksum_failures_total` / `resyncs_total` non-zero? The
        continuity policy from step 1 is either firing correctly or is wrong
  - [ ] `precision_loss_total` non-zero? The venue quotes finer than the fixed-point 1e-8
        scale and the contract needs an ADR, not a patch

---

## References

- [ADR-019 — Rust capture tier](../adr/ADR-019-rust-capture-tier.md) — why the capture tier
  is Rust, and the gate the Kotlin handlers retired on
- [ADR-018 — v3 lake-first architecture](../adr/ADR-018-v3-lake-first-rust-capture.md) —
  why `raw` is the system of record and everything else is derived from it
- [ADR-027 — book snapshot and sequencing](../adr/ADR-027-book-snapshot-and-sequencing.md) —
  the top-20-at-1 Hz product and the per-venue resync policies
- [`services/capture-rust/README.md`](../../services/capture-rust/README.md) — the adapter
  contract, the environment table, and the build/test loop
- [Streaming sources](../architecture/06-capture-venues.md) — per-exchange protocol notes

---

## Post-Integration Checklist

- [ ] **The lake's raw archive needs nothing.** [`docker/lake/ingest.py`](../../docker/lake/ingest.py) builds its topic list as `K2_EXCHANGES × {raw, trades, book}`, so the new topics are archived into `lake.raw.messages` by the next 5-minute cycle; a topic absent from the previous commit's `k2.kafka-offsets` has no stored position, so the ingest starts it at the beginning ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). If `K2_EXCHANGES` has been set explicitly anywhere, add the exchange there too — it defaults to `binance,kraken,coinbase`
- [ ] **The lake's decoded layers are per venue** ([ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)): a `bronze.{exchange}_<msgtype>` entry in `VENUE_TABLES` in [`docker/lake/bronze.py`](../../docker/lake/bronze.py), a `silver.trades_{exchange}` entry in `TRADES` in [`docker/lake/silver.py`](../../docker/lake/silver.py) (and the book replay in `books.py` if the venue publishes L2), plus the matching DDL in [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql). `gold.*` is unified and needs no change. Until those land, the venue is archived but not decoded — `raw.messages` keeps every frame, so the decode is a replay, not a loss
- [ ] Update the instrument and exchange counts in `config/instruments.yaml`'s header and
      the root `README.md`
- [ ] Update [docker-resources.md](./docker-resources.md), the `docker-compose.yml`
      resource summary comment, and [ADR-010](../adr/ADR-010-resource-budget.md)'s Outcome
- [ ] Update the scrape-target table in [observability.md](./observability.md)
- [ ] `bash scripts/check-docs.sh` green, then `make test`

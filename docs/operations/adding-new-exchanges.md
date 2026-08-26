# Adding a New Exchange

## Overview

This guide provides a step-by-step checklist for adding new exchange integrations following the multi-exchange bronze architecture pattern ([ADR-011](../decisions/ADR-011-multi-exchange-bronze-architecture.md)).

**Architecture**: Exchange-Native Bronze → Unified Silver → Aggregated Gold

## Prerequisites

- [ ] Exchange API documentation reviewed
- [ ] WebSocket endpoint identified
- [ ] Sample trade message captured
- [ ] Symbol format documented (e.g., BTC/USD, BTCUSDT, XBT-USD)
- [ ] Timestamp format documented

## Implementation Checklist

### 1. Feed Handler (Kotlin)

**File**: `services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/{Exchange}WebSocketClient.kt`

- [ ] Create `{Exchange}WebSocketClient.kt` class
- [ ] Implement WebSocket connection logic
- [ ] Parse exchange-native message format
- [ ] Build JSON matching exchange schema
- [ ] Call `producer.produceRawJson("{exchange}", json)`
- [ ] Add reconnection logic
- [ ] Add logging

**Pattern**:
```kotlin
class KrakenWebSocketClient(
    private val config: Config,
    private val producer: KafkaProducerService,
    private val symbols: List<String>
) {
    suspend fun connect() { /* ... */ }
    private suspend fun handleMessage(text: String) { /* ... */ }
}
```

**Reference**: [`KrakenWebSocketClient.kt`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/KrakenWebSocketClient.kt), or [`CoinbaseWebSocketClient.kt`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/CoinbaseWebSocketClient.kt) for the newest example

---

### 2. Configuration

**File**: `services/feed-handler-kotlin/src/main/resources/application.conf`

- [ ] Add exchange-specific configuration block:

```hocon
{exchange} {
  websocket-url = "wss://..."
  websocket-url = ${?K2_{EXCHANGE}_WS_URL}

  reconnect-delay-ms = 5000
  max-reconnect-attempts = -1
  ping-interval-ms = 30000
}
```

**Reference**: [`application.conf`](../../services/feed-handler-kotlin/src/main/resources/application.conf) — see the existing `binance` / `kraken` / `coinbase` blocks.

- [ ] Add the exchange and its symbols to [`config/instruments.yaml`](../../config/instruments.yaml)
      under `instruments.{exchange}.symbols`, in **exchange-native format**
      (`BTCUSDT` for Binance, `XBT/USD` for Kraken, `BTC-USD` for Coinbase).

`config/instruments.yaml` is the single source of truth for symbols. `K2_SYMBOLS` exists
only as a fallback for local dev without the file.

> **Bind-mount gotcha:** `instruments.yaml` is mounted file-by-file, which pins the inode.
> Editors that write-then-rename produce a new inode and the container keeps reading the
> old file. After editing, run
> `docker compose up -d --force-recreate --no-deps feed-handler-{exchange}` —
> `docker restart` will **not** pick up the change.

---

### 3. Main.kt Routing

**File**: `services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/Main.kt`

- [ ] Add exchange case to routing logic:

```kotlin
val wsClient = when (exchange.lowercase()) {
    "binance" -> BinanceWebSocketClient(...)
    "kraken" -> KrakenWebSocketClient(...)
    "{exchange}" -> {Exchange}WebSocketClient(
        config = config.getConfig("{exchange}"),
        producer = producer,
        symbols = symbols
    )
    else -> { logger.error { "Unknown exchange: $exchange" }; exitProcess(1) }
}
```

**Reference**: [`Main.kt`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/Main.kt)

---

### 4. Bronze Layer Schema

**File**: `docker/clickhouse/schema/XX-bronze-{exchange}.sql`

- [ ] Create Kafka Engine table:

```sql
CREATE TABLE IF NOT EXISTS k2.trades_{exchange}_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.{exchange}.raw',
    kafka_group_name = 'clickhouse_bronze_{exchange}_consumer',
    kafka_format = 'JSONAsString',
    ...;
```

- [ ] Create the bronze table. **All three existing exchanges share an identical bronze
      schema** — copy it exactly so the offload config and silver MVs stay uniform:

```sql
CREATE TABLE IF NOT EXISTS k2.bronze_trades_{exchange} (
    exchange_timestamp  DateTime64(3),
    sequence_number     UInt64,
    symbol              String,          -- exchange-native, punctuation stripped
    price               Decimal(18, 8),
    quantity            Decimal(18, 8),
    quote_volume        Decimal(18, 8),
    event_time          DateTime64(3),
    kafka_offset        UInt64,
    kafka_partition     UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY;
```

- [ ] Create the normalizing materialized view — this is where exchange-specific JSON
      parsing lives:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_{exchange}_mv
TO k2.bronze_trades_{exchange} AS
SELECT
    parseDateTimeBestEffort(JSONExtractString(message, 'time')) AS exchange_timestamp,
    JSONExtractUInt(message, 'sequence_num')                    AS sequence_number,
    replaceAll(JSONExtractString(message, 'product_id'), '-', '') AS symbol,
    toDecimal64(JSONExtractString(message, 'price'), 8)         AS price,
    ...
FROM k2.trades_{exchange}_queue
WHERE message != '';
```

**Key principle**: the raw JSON stays untouched in the `.raw` Redpanda topic; bronze is
already typed and uniform. `TTL` uses an explicit `toDateTime()` cast — `DateTime64`
columns need it.

**Reference**: [`11-bronze-coinbase.sql`](../../docker/clickhouse/schema/11-bronze-coinbase.sql) — the cleanest example of the current pattern

---

### 5. Silver Layer Normalization

**File**: `docker/clickhouse/schema/XX-silver-{exchange}-to-v2.sql`

- [ ] Create Materialized View to normalize Bronze → Silver:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_{exchange}_to_silver_mv
TO k2.silver_trades AS
SELECT
    generateUUIDv4() AS message_id,

    -- Generate or extract trade_id
    '{EXCHANGE}-' || toString(...) AS trade_id,

    -- Normalize exchange fields
    '{exchange}' AS exchange,

    -- Normalize symbol (e.g., XBT → BTC)
    -- Your normalization logic here
    ... AS canonical_symbol,

    'crypto' AS asset_class,

    -- Convert types (String → Decimal128, timestamps, etc.)
    CAST(toDecimal64(price, 8) AS Decimal128(8)) AS price,

    -- Normalize side (exchange format → BUY/SELL enum)
    CAST(
        CASE ...
            WHEN ... THEN 'BUY'
            WHEN ... THEN 'SELL'
        END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
    ) AS side,

    -- Convert timestamps
    fromUnixTimestamp64Micro(...) AS timestamp,

    -- Preserve original fields in vendor_data
    map(
        'native_field1', native_field1,
        'native_field2', native_field2,
        ...
    ) AS vendor_data,

    -- Validation
    (price > 0 AND quantity > 0) AS is_valid,
    ...

FROM k2.bronze_trades_{exchange};
```

**Key Principle**: Normalize to v2 schema, preserve originals in `vendor_data`!

**Reference**: [`09-silver-kraken-to-v2.sql`](../../docker/clickhouse/schema/09-silver-kraken-to-v2.sql) and [`12-silver-coinbase.sql`](../../docker/clickhouse/schema/12-silver-coinbase.sql)

---

### 6. Docker Compose

**File**: [`docker-compose.yml`](../../docker-compose.yml) (repo root — all services live in one file)

- [ ] Add the two topics to the `redpanda-init` one-shot service, choosing a partition
      count proportional to expected volume (binance 40, kraken 20, coinbase 20):

```bash
rpk topic describe market.crypto.trades.{exchange}.raw --brokers redpanda:9092 > /dev/null 2>&1 \
  || rpk topic create market.crypto.trades.{exchange}.raw --partitions 20 --brokers redpanda:9092
rpk topic describe market.crypto.trades.{exchange}     --brokers redpanda:9092 > /dev/null 2>&1 \
  || rpk topic create market.crypto.trades.{exchange}     --partitions 20 --brokers redpanda:9092
```

- [ ] Add the feed handler service (copy `feed-handler-coinbase` and change the name,
      `K2_EXCHANGE` and the static IP — `.50`/`.51`/`.52` are taken):

```yaml
feed-handler-{exchange}:
  build:
    context: .
    dockerfile: services/feed-handler-kotlin/Dockerfile
  container_name: k2-feed-handler-{exchange}
  hostname: feed-handler-{exchange}
  networks:
    k2-net:
      # no static IP needed — services resolve by name
  depends_on:
    redpanda:
      condition: service_healthy
    clickhouse:
      condition: service_healthy
    redpanda-init:
      condition: service_completed_successfully
  environment:
    - JAVA_OPTS=-Xmx512m -Xms256m
    - K2_EXCHANGE={exchange}
    - K2_INSTRUMENTS_FILE=/app/config/instruments.yaml
    - K2_SCHEMA_PATH=/app/schemas
    - K2_KAFKA_BOOTSTRAP_SERVERS=redpanda:9092
    - K2_KAFKA_SCHEMA_REGISTRY_URL=http://redpanda:8081
  volumes:
    - ./logs/{exchange}:/app/logs
    - ./config/instruments.yaml:/app/config/instruments.yaml:ro
  deploy:
    resources:
      limits:
        cpus: '0.5'
        memory: 512M
      reservations:
        cpus: '0.25'
        memory: 256M
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:8082/health"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 30s
  restart: unless-stopped
  labels:
    com.k2.service: "feed-handler"
    com.k2.exchange: "{exchange}"
    com.k2.version: "v2"
```

- [ ] Add a Prometheus scrape job in [`docker/prometheus/prometheus.yml`](../../docker/prometheus/prometheus.yml)
      targeting `feed-handler-{exchange}:8082`
- [ ] Update the totals in [docker-resources.md](./docker-resources.md) (+0.5 CPU / +512 MB)

---

### 7. Testing & Validation

**File**: `docker/clickhouse/validation/validate-{exchange}-integration.sql`

- [ ] Create validation SQL script with sections:
  1. Bronze Layer: Verify native format
  2. Silver Layer: Verify normalization
  3. Cross-Exchange: Compare with existing exchanges
  4. Gold Layer: Verify aggregation
  5. Data Quality: Validation checks

**Reference**: [`validate-kraken-integration.sql`](../../docker/clickhouse/validation/validate-kraken-integration.sql)

- [ ] Add a `TradeNormalizerTest` case covering the new symbol format
      ([`TradeNormalizerTest.kt`](../../services/feed-handler-kotlin/src/test/kotlin/com/k2/feedhandler/TradeNormalizerTest.kt))
- [ ] Add the exchange to `InstrumentsLoaderTest` fixtures if the YAML shape changes
- [ ] `make test` green before opening the PR — see [../development/testing.md](../development/testing.md)

---

## Testing Steps

### 1. Build & Deploy

```bash
# Build and start the new handler
docker compose up -d --build feed-handler-{exchange}

# Apply the new bronze + silver DDL
docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
  --multiquery < docker/clickhouse/schema/XX-bronze-{exchange}.sql
```

### 2. Verify Feed Handler

```bash
# Check logs
docker logs -f k2-feed-handler-{exchange}

# Check Kafka topic
docker exec k2-redpanda rpk topic consume market.crypto.trades.{exchange}.raw --num 5
```

### 3. Verify Bronze Layer

```sql
-- Count trades
SELECT count() FROM k2.bronze_trades_{exchange};

-- Verify native format
SELECT * FROM k2.bronze_trades_{exchange} LIMIT 1 FORMAT Vertical;
```

### 4. Verify Silver Layer

```sql
-- Verify normalization
SELECT
    exchange,
    canonical_symbol,
    vendor_data,
    count()
FROM k2.silver_trades
WHERE exchange = '{exchange}'
GROUP BY exchange, canonical_symbol, vendor_data;
```

### 5. Verify Gold Layer

```sql
-- Verify cross-exchange aggregation
SELECT exchange, canonical_symbol, count()
FROM k2.ohlcv_1m
WHERE canonical_symbol LIKE 'BTC/%'
GROUP BY exchange, canonical_symbol;
```

### 6. Run Validation Script

```bash
docker exec k2-clickhouse clickhouse-client --multiquery < \
  docker/clickhouse/validation/validate-{exchange}-integration.sql
```

---

## Common Patterns by Exchange Type

### Pattern 1: Binance-Like (REST + WebSocket)
- Symbol format: No separator (BTCUSDT)
- Timestamp: Milliseconds (Long)
- Trade ID: Provided
- **Examples**: Binance, Bybit, OKX

### Pattern 2: Kraken-Like (Array Protocol)
- Symbol format: Slash separator (XBT/USD)
- Timestamp: Decimal string ("seconds.microseconds")
- Trade ID: Not provided (must generate)
- **Examples**: Kraken, potentially Bitstamp

### Pattern 3: Coinbase-Like (Object Protocol)
- Symbol format: Dash separator (BTC-USD)
- Timestamp: ISO 8601 string
- Trade ID: Provided
- **Examples**: Coinbase Pro, Gemini

---

## Symbol Normalization Examples

| Exchange | Native Format | Canonical Format | Notes |
|----------|---------------|------------------|-------|
| Binance  | BTCUSDT       | BTC/USDT         | Remove separator |
| Kraken   | XBT/USD       | BTC/USD          | XBT → BTC |
| Coinbase | BTC-USD       | BTC/USD          | - → / |
| Bitfinex | tBTCUSD       | BTC/USD          | Remove 't' prefix |

---

## Troubleshooting Checklist

- [ ] **Feed Handler**:
  - [ ] WebSocket connected?
  - [ ] Subscription confirmed?
  - [ ] JSON produced to Kafka?

- [ ] **Bronze Layer**:
  - [ ] Table created?
  - [ ] Kafka Engine consuming?
  - [ ] Data flowing to Bronze table?
  - [ ] Native format preserved?

- [ ] **Silver Layer**:
  - [ ] MV created?
  - [ ] Data flowing to Silver?
  - [ ] Normalization correct?
  - [ ] vendor_data populated?

- [ ] **Gold Layer**:
  - [ ] OHLCV aggregating both exchanges?
  - [ ] No duplicate candles?

---

## References

- [ADR-011 — multi-exchange bronze architecture](../decisions/ADR-011-multi-exchange-bronze-architecture.md) — the pattern this checklist implements
- [ADR-016 — adding Coinbase](../decisions/ADR-016-add-coinbase-exchange.md) — the most recent worked example, end to end
- [Schema design](../architecture/schema-design.md) — the v2 canonical trade schema
- [Streaming sources](../architecture/streaming-sources.md) — per-exchange protocol notes
- [ClickHouse cascading materialized views](https://clickhouse.com/docs/en/guides/developer/cascading-materialized-views)

---

## Post-Integration Checklist

- [ ] Add the exchange to the offload `TABLE_CONFIG` in [`docker/offload/flows/iceberg_offload_flow.py`](../../docker/offload/flows/iceberg_offload_flow.py)
- [ ] Create the matching `cold.bronze_trades_{exchange}` Iceberg table and seed its watermark row
- [ ] Update [docker-resources.md](./docker-resources.md) and [ADR-010](../decisions/ADR-010-resource-budget.md)
- [ ] Add the exchange to the feed-handler panels in [`docker/grafana/dashboards/k2-pipeline-overview.json`](../../docker/grafana/dashboards/k2-pipeline-overview.json)
- [ ] Add a `TradeNormalizerTest` case for the new symbol format
- [ ] Update the exchange counts in the root `README.md`

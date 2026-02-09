# Adding New Exchanges - Quick Reference

## Overview

This guide provides a step-by-step checklist for adding new exchange integrations following the multi-exchange Bronze architecture pattern (ADR-011).

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

**Reference**: `KrakenWebSocketClient.kt` (lines 1-221)

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

**Reference**: `application.conf` (lines 36-44)

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

**Reference**: `Main.kt` (lines 51-66)

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

- [ ] Create Bronze table with **exchange-native schema**:

```sql
CREATE TABLE IF NOT EXISTS k2.bronze_trades_{exchange} (
    -- Exchange-specific fields (preserve native format!)
    -- Example: "XBT/USD" not "BTC/USD"
    -- Example: timestamp as String if exchange uses strings

    -- Standard metadata
    ingestion_timestamp DateTime64(6, 'UTC'),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (toYYYYMMDD(ingested_at))
ORDER BY (/* exchange-specific ordering */);
```

- [ ] Create Materialized View to parse JSON:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_{exchange}_mv
TO k2.bronze_trades_{exchange} AS
SELECT
    JSONExtractString(message, 'field1') AS field1,
    -- Extract all native fields
    ...
FROM k2.trades_{exchange}_queue
WHERE message != '';
```

**Key Principle**: Preserve exchange's native format exactly!

**Reference**: `08-bronze-kraken.sql`

---

### 5. Silver Layer Normalization

**File**: `docker/clickhouse/schema/XX-silver-{exchange}-to-v2.sql`

- [ ] Create Materialized View to normalize Bronze → Silver:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_{exchange}_to_silver_v2_mv
TO k2.silver_trades_v2 AS
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

**Reference**: `09-silver-kraken-to-v2.sql`

---

### 6. Docker Compose

**File**: `services/feed-handler-kotlin/docker-compose.feed-handlers.yml`

- [ ] Add feed handler service:

```yaml
feed-handler-{exchange}:
  build:
    context: .
    dockerfile: services/feed-handler-kotlin/Dockerfile
  container_name: k2-feed-handler-{exchange}
  hostname: feed-handler-{exchange}
  networks:
    - k2-net
  depends_on:
    redpanda:
      condition: service_healthy
  environment:
    - JAVA_OPTS=-Xmx512m -Xms256m
    - K2_EXCHANGE={exchange}
    - K2_SYMBOLS=BTC/USD,ETH/USD  # Exchange-native format!
    - K2_SCHEMA_PATH=/app/schemas
    - K2_KAFKA_BOOTSTRAP_SERVERS=redpanda:9092
    - K2_KAFKA_SCHEMA_REGISTRY_URL=http://redpanda:8081
  volumes:
    - ./logs/{exchange}:/app/logs
  deploy:
    resources:
      limits:
        cpus: '0.5'
        memory: 512M
  restart: unless-stopped
  labels:
    com.k2.service: "feed-handler"
    com.k2.exchange: "{exchange}"
    com.k2.version: "v2"
```

- [ ] Update resource summary

**Reference**: `docker-compose.feed-handlers.yml` (lines 54-91)

---

### 7. Testing & Validation

**File**: `docker/clickhouse/validation/validate-{exchange}-integration.sql`

- [ ] Create validation SQL script with sections:
  1. Bronze Layer: Verify native format
  2. Silver Layer: Verify normalization
  3. Cross-Exchange: Compare with existing exchanges
  4. Gold Layer: Verify aggregation
  5. Data Quality: Validation checks

**Reference**: `validate-kraken-integration.sql`

---

### 8. Documentation

**File**: `docs/testing/{exchange}-integration-testing.md`

- [ ] Create testing guide covering:
  - Prerequisites
  - Feed handler testing
  - Bronze validation
  - Silver validation
  - Gold validation
  - Success criteria
  - Troubleshooting

**Reference**: `kraken-integration-testing.md`

---

## Testing Steps

### 1. Build & Deploy

```bash
# Build feed handler
docker compose -f docker-compose.v2.yml build

# Start exchange feed handler
docker compose -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  up -d feed-handler-{exchange}
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
FROM k2.silver_trades_v2
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

- **ADR-011**: Multi-Exchange Bronze Architecture
- **Schema V2 Guide**: `docs/architecture/schema-v2-crypto-guide.md`
- **Kraken Example**: Complete implementation reference
- **ClickHouse MVs**: https://clickhouse.com/docs/en/guides/developer/cascading-materialized-views

---

## Post-Integration Checklist

- [ ] Update ARCHITECTURE-V2.md with new exchange
- [ ] Update resource budget in ADR-010
- [ ] Add Grafana dashboard for exchange metrics
- [ ] Document exchange-specific operational notes
- [ ] Update CI/CD to test new exchange
- [ ] Create runbook for exchange-specific issues

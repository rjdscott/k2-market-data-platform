# Kraken Streaming Guide

**Last Updated**: 2026-01-18
**Status**: Production Ready
**Related Phase**: [Phase 10 - Streaming Crypto](./phases/phase-10-streaming-crypto/)

---

## Overview

The Kraken streaming service connects to Kraken's WebSocket API, receives real-time trade data for BTC/USD and ETH/USD, normalizes the data to the K2 v2 schema, and publishes it to Kafka.

**Key Features**:
- Real-time WebSocket streaming from Kraken
- XBT → BTC symbol normalization for consistency
- Production-grade resilience (6 layers)
- V2 Avro schema with vendor_data extension
- Automatic reconnection with exponential backoff
- Health monitoring and metrics
- Graceful shutdown with message flush

---

## Architecture

```
Kraken WebSocket API (wss://ws.kraken.com)
    │
    ├─ Trade Channel: BTC/USD (XBT/USD)
    ├─ Trade Channel: ETH/USD
    │
    ▼
KrakenWebSocketClient (~900 lines)
    │
    ├─ 6-Layer Resilience:
    │  ├─ Exponential Backoff (1s → 128s)
    │  ├─ Failover URL Rotation
    │  ├─ Health Check (30s timeout)
    │  ├─ Connection Rotation (4-hour lifetime)
    │  ├─ Memory Leak Detection (linear regression)
    │  └─ Ping-Pong Heartbeat (60s interval)
    │
    ├─ XBT → BTC Normalization
    ├─ V2 Schema Conversion
    │
    ▼
MarketDataProducer
    │
    ▼
Kafka Topic: market.crypto.trades.kraken
    │
    ├─ 20 partitions (keyed by symbol)
    ├─ LZ4 compression
    ├─ 7-day retention
    ├─ V2 Avro schema
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose installed
- Kafka cluster running (port 9092)
- Schema Registry running (port 8081)
- Network connectivity to wss://ws.kraken.com

### Quick Start

**1. Start the Kraken streaming service:**

```bash
docker compose up -d kraken-stream
```

**2. Verify the container is running:**

```bash
docker ps --filter name=k2-kraken-stream
```

Expected output:
```
CONTAINER ID   IMAGE     STATUS                    PORTS
abc123...      k2...     Up 2 minutes (healthy)    0.0.0.0:9095->9095/tcp
```

**3. Check streaming logs:**

```bash
docker logs k2-kraken-stream --follow
```

You should see:
```
Kraken Streaming Service
==================================================
Symbols: BTC/USD, ETH/USD
...
✓ Streaming started!

streaming_progress  trades_streamed=50  errors=0  symbol=BTCUSD  last_price=95381.70000
```

**4. Verify messages in Kafka:**

```bash
# Check topic exists and has data
docker exec k2-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe \
  --topic market.crypto.trades.kraken
```

Expected: Topic with 20 partitions, some partitions showing non-zero size.

---

## Viewing the Data

### Method 1: Docker Logs (Simplest)

View live streaming progress:

```bash
docker logs k2-kraken-stream --follow | grep streaming_progress
```

Output shows:
- Total trades streamed
- Error count
- Latest symbol and price
- Kraken pair (XBT/USD → BTCUSD)

### Method 2: Kafka Console Consumer (Raw Messages)

View raw Avro bytes (not human-readable):

```bash
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.kraken \
  --from-beginning \
  --max-messages 10
```

### Method 3: Python Consumer (Recommended)

Use the test script to deserialize and view trades:

```bash
uv run python scripts/test_e2e_kraken.py
```

Output:
```
Consuming messages from market.crypto.trades.kraken...
============================================================

Trade #1:
  Symbol: BTCUSD
  Exchange: KRAKEN
  Price: 95381.70000
  Quantity: 0.00032986
  Side: SELL
  Timestamp: 1737118158321597
  Pair (vendor_data): XBT/USD

...
```

### Method 4: Prometheus Metrics (Production)

View real-time metrics at http://localhost:9095/metrics

Key metrics:
- `k2_kraken_messages_received_total{symbol="BTCUSD"}` - Trade count per symbol
- `k2_kraken_connection_status` - Connection status (1=connected, 0=disconnected)
- `k2_kraken_message_errors_total` - Error count by type
- `k2_kraken_last_message_timestamp_seconds` - Last message timestamp
- `k2_kafka_messages_produced_total` - Total messages produced to Kafka

### Method 5: Kafka Topic Statistics

Check partition sizes and offsets:

```bash
docker exec k2-kafka kafka-log-dirs \
  --bootstrap-server localhost:9092 \
  --topic-list market.crypto.trades.kraken \
  --describe | grep -E "(size|offsetLag)"
```

---

## Configuration

### Environment Variables

Configure the Kraken service via `docker-compose.yml`:

```yaml
environment:
  # Kafka Configuration
  K2_KAFKA_BOOTSTRAP_SERVERS: kafka:29092
  K2_KAFKA_SCHEMA_REGISTRY_URL: http://schema-registry-1:8081

  # Kraken WebSocket
  K2_KRAKEN_ENABLED: "true"
  K2_KRAKEN_SYMBOLS: '["BTC/USD", "ETH/USD"]'
  K2_KRAKEN_WEBSOCKET_URL: wss://ws.kraken.com
  K2_KRAKEN_FAILOVER_URLS: '["wss://ws-auth.kraken.com"]'

  # Resilience
  K2_KRAKEN_RECONNECT_DELAY: 5              # Initial reconnect delay (seconds)
  K2_KRAKEN_MAX_RECONNECT_ATTEMPTS: 10      # Max reconnection attempts
  K2_KRAKEN_HEALTH_CHECK_INTERVAL: 30       # Health check interval (seconds)
  K2_KRAKEN_HEALTH_CHECK_TIMEOUT: 30        # Health check timeout (seconds)
  K2_KRAKEN_CONNECTION_MAX_LIFETIME_HOURS: 4  # Max connection lifetime (hours)
  K2_KRAKEN_PING_INTERVAL_SECONDS: 60       # Ping interval (seconds)
  K2_KRAKEN_PING_TIMEOUT_SECONDS: 10        # Ping timeout (seconds)

  # Metrics
  K2_METRICS_ENABLED: "true"
  K2_METRICS_PORT: 9095
```

### Adding New Symbols

To stream additional symbols (e.g., DOT/EUR):

```yaml
K2_KRAKEN_SYMBOLS: '["BTC/USD", "ETH/USD", "DOT/EUR"]'
```

Restart the service:
```bash
docker compose restart kraken-stream
```

---

## Schema Details

### Kraken Native Format

Kraken sends trade messages in array format:

```json
[
  0,                                    // channelID
  [[
    "95381.70000",                      // price
    "0.00032986",                       // volume
    "1737118158.321597",                // timestamp
    "s",                                // side: "b" = buy, "s" = sell
    "l",                                // order type: "l" = limit, "m" = market
    ""                                  // misc
  ]],
  "trade",                              // channel name
  "XBT/USD"                             // pair
]
```

### K2 V2 Schema (After Conversion)

```json
{
  "message_id": "uuid-v4-generated",
  "trade_id": "KRAKEN-1737118158321597-abc123",
  "symbol": "BTCUSD",                   // XBT → BTC normalized
  "exchange": "KRAKEN",
  "asset_class": "crypto",
  "timestamp": 1737118158321597,        // microseconds
  "price": 95381.70000,                 // Decimal(18,8)
  "quantity": 0.00032986,               // Decimal(18,8)
  "currency": "USD",
  "side": "SELL",                       // "s" → SELL, "b" → BUY
  "trade_conditions": [],               // Not applicable to crypto
  "source_sequence": null,              // Kraken doesn't provide
  "ingestion_timestamp": 1737118158500000,
  "platform_sequence": 12345,
  "vendor_data": {
    "pair": "XBT/USD",                  // Original Kraken pair
    "order_type": "l",                  // Limit or market
    "base_asset": "BTC",                // Normalized
    "quote_asset": "USD"
  }
}
```

**Key Normalization**:
- `XBT/USD` → `BTCUSD` (symbol field)
- Original pair preserved in `vendor_data.pair`
- Deterministic trade ID: `KRAKEN-{timestamp}-{hash}`

---

## Resilience Features

### 1. Exponential Backoff

Reconnection delays increase exponentially:
- Attempt 1: 5 seconds
- Attempt 2: 10 seconds
- Attempt 3: 20 seconds
- ...
- Max: 60 seconds

### 2. Failover URL Rotation

If primary WebSocket URL fails, rotates through failover URLs:
1. `wss://ws.kraken.com` (primary)
2. `wss://ws-auth.kraken.com` (failover)

### 3. Health Check Monitoring

Monitors last message timestamp. If no messages received for 30 seconds:
- Logs `kraken_connection_stale_reconnecting`
- Initiates reconnection

### 4. Connection Rotation

Proactively rotates connection every 4 hours to prevent:
- Memory leaks
- Stale connections
- Accumulated state

### 5. Memory Leak Detection

Samples RSS memory every 30 seconds. If linear regression shows upward trend (R² > 0.8):
- Logs `memory_leak_detected`
- Can trigger rotation if severe

### 6. Ping-Pong Heartbeat

Sends ping frames every 60 seconds, expects pong within 10 seconds:
- Detects broken connections
- Triggers reconnection on timeout

---

## Monitoring

### Health Check

Container health check tests metrics endpoint:

```bash
# Manual health check
curl http://localhost:9095/metrics

# Container health status
docker inspect k2-kraken-stream --format='{{.State.Health.Status}}'
```

### Key Metrics

**Connection Status**:
```prometheus
k2_kraken_connection_status{service="k2-platform",environment="dev"} 1.0
```

**Messages Received**:
```prometheus
k2_kraken_messages_received_total{service="k2-platform",symbol="BTCUSD"} 1523
k2_kraken_messages_received_total{service="k2-platform",symbol="ETHUSD"} 892
```

**Errors**:
```prometheus
k2_kraken_message_errors_total{service="k2-platform",error_type="TypeError"} 0
k2_kraken_connection_errors_total{service="k2-platform",error_type="ConnectionClosed"} 0
```

**Reconnections**:
```prometheus
k2_kraken_reconnects_total{service="k2-platform",reason="connection_closed"} 2
k2_kraken_connection_rotations_total{service="k2-platform",reason="scheduled_rotation"} 1
```

### Grafana Dashboard (Optional)

Create a dashboard with panels for:
- Connection status (green/red indicator)
- Messages received rate (per symbol)
- Error rate
- Reconnection count
- Memory usage trend

---

## Troubleshooting

### Issue: Container Not Starting

**Symptoms**:
```bash
docker ps --filter name=kraken-stream
# No container listed
```

**Diagnosis**:
```bash
docker compose logs kraken-stream
```

**Common Causes**:
1. **Port conflict**: Port 9095 already in use
   - **Fix**: Change `K2_METRICS_PORT` in docker-compose.yml
2. **Kafka not ready**: Schema registry or Kafka not healthy
   - **Fix**: Wait for dependencies: `docker compose ps`

### Issue: No Messages Received

**Symptoms**:
```bash
docker logs kraken-stream --tail 50 | grep streaming_progress
# No output
```

**Diagnosis**:
```bash
docker logs kraken-stream | grep -E "(connected|error|reconnecting)"
```

**Common Causes**:
1. **Not subscribed**: Check logs for `kraken_subscribed`
   - **Fix**: Verify symbols are valid: `["BTC/USD", "ETH/USD"]`
2. **Connection timeout**: Network issue
   - **Fix**: Test connectivity: `curl -I https://ws.kraken.com`
3. **Low volume**: Kraken has 10x less volume than Binance
   - **Expected**: May take 30-60 seconds to see first trade

### Issue: High Error Rate

**Symptoms**:
```bash
docker logs kraken-stream | grep message_error | wc -l
# High count
```

**Diagnosis**:
```bash
docker logs kraken-stream | grep message_error | head -5
```

**Common Causes**:
1. **Schema mismatch**: V2 schema not registered
   - **Fix**: Run `scripts/init_infra.py` to register schema
2. **Kafka unavailable**: Producer can't reach Kafka
   - **Fix**: Check Kafka health: `docker compose ps kafka`

### Issue: Memory Leak

**Symptoms**:
```bash
docker stats k2-kraken-stream
# Memory continuously increasing
```

**Diagnosis**:
```bash
curl -s http://localhost:9095/metrics | grep k2_memory_leak_detection_score
```

**Fix**:
- Connection will auto-rotate after 4 hours
- Or manually restart: `docker compose restart kraken-stream`

---

## Performance

### Expected Performance

| Metric | Value |
|--------|-------|
| Message Rate | 10-50 trades/min (BTC+ETH combined) |
| Latency | <100ms (Kraken → Kafka) |
| Memory Usage | ~200-300 MB steady state |
| CPU Usage | <5% (minimal) |
| Reconnections | <1 per day (normal operations) |
| Error Rate | <0.1% |

### Comparison: Binance vs Kraken

| Exchange | Volume | Message Rate | Latency |
|----------|--------|--------------|---------|
| Binance | High | 100-500 trades/min | <50ms |
| Kraken | Medium | 10-50 trades/min | <100ms |

**Note**: Kraken has significantly lower trading volume than Binance, so fewer messages is expected and normal.

---

## Testing

### Unit Tests

Run Kraken client unit tests:

```bash
uv run pytest tests/unit/test_kraken_client.py -v
```

**Coverage**:
- 30 unit tests
- Pair parsing (XBT normalization)
- Message validation
- V2 conversion
- Memory leak detection

### Integration Tests

Run live WebSocket integration tests:

```bash
uv run pytest tests/integration/test_streaming_validation.py -k kraken -v
```

**Tests**:
- Live Kraken WebSocket connection
- Trade message reception
- V2 schema compliance
- Message rate validation

### Manual Validation

Quick validation script:

```bash
python scripts/test_kraken_stream.py
```

Expected: 10+ trades within 60 seconds.

### E2E Validation

Comprehensive end-to-end test:

```bash
python scripts/validate_streaming.py --exchange kraken
```

**Validates**:
- WebSocket connection
- Kafka message production
- Schema compliance
- Performance benchmarks

---

## Next Steps

After validating Kraken streaming, the next phase steps are:

1. **Spark Cluster Setup** (Step 06)
   - Deploy Spark master + 2 workers
   - 5 CPUs, 10GB RAM total

2. **Bronze Ingestion** (Step 10)
   - Kafka → Bronze Iceberg table
   - Consume from both `market.crypto.trades.binance` and `market.crypto.trades.kraken`

3. **Silver Transformation** (Step 11)
   - Bronze → `silver_binance_trades` and `silver_kraken_trades`
   - Per-exchange validation and cleaning

4. **Gold Aggregation** (Step 12)
   - Silver → `gold_crypto_trades` (unified)
   - Multi-source deduplication

---

## References

- **Kraken WebSocket API**: https://docs.kraken.com/websockets/
- **Implementation**: `src/k2/ingestion/kraken_client.py`
- **Configuration**: `src/k2/common/config.py` (KrakenConfig)
- **Tests**: `tests/unit/test_kraken_client.py`
- **Phase Documentation**: [Phase 10 README](./phases/phase-10-streaming-crypto/README.md)
- **Validation Guide**: [STREAMING_VALIDATION.md](./STREAMING_VALIDATION.md)

---

**Last Updated**: 2026-01-18
**Maintained By**: Engineering Team

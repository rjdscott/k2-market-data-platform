# Streaming Data Sources Architecture

**Last Updated**: 2026-02-18
**Status**: Active — 3 exchanges live (Binance, Kraken, Coinbase)
**Audience**: Staff/Principal Engineers, Integration Developers
**Purpose**: Generic architecture for integrating real-time streaming data sources

> **[v2 Note]** Feed handlers are now **Kotlin/Spring Boot** services (not Python clients).
> The generic integration pattern below is still correct. For adding a new exchange in v2,
> see [docs/operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md).
> The Kotlin implementation lives in `services/feed-handler-kotlin/`.

---

## Overview

K2 platform supports real-time streaming data sources alongside batch ingestion. This document provides the **generic architecture pattern** for integrating WebSocket/streaming sources, with **Binance crypto streaming** as the reference implementation.

**Design Goals**:
1. **Source-Agnostic**: Common integration pattern applicable to any streaming source
2. **Multi-Asset Support**: Unified V2 schema for equities, crypto, futures, options
3. **Production-Grade**: Reconnection, backpressure, graceful degradation
4. **Observable**: Comprehensive metrics and monitoring

**Current Implementations**:
- ✅ **Binance Spot** (WebSocket, crypto trades, operational since 2026-01-13)
- 🔄 **Future**: Bloomberg B-PIPE, Refinitiv TREP, ICE Market Data

---

## Architecture Pattern

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Streaming Data Source                       │
│              (Binance, Bloomberg, Refinitiv, ICE)               │
└────────────────────────────┬────────────────────────────────────┘
                             │ WebSocket / TCP / UDP
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Streaming Client Layer                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Connection Mgmt  │  │  Authentication  │  │ Subscription │  │
│  │ - Auto-reconnect │  │  - API keys      │  │ - Channels   │  │
│  │ - Heartbeat/ping │  │  - OAuth tokens  │  │ - Symbols    │  │
│  │ - Rate limiting  │  │  - Session mgmt  │  │ - Filters    │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │ Message Stream
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Message Transformation                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Schema Mapping   │  │  Normalization   │  │  Enrichment  │  │
│  │ - V2 schema      │  │  - Timestamps    │  │  - UUIDs     │  │
│  │ - vendor_data    │  │  - Decimals      │  │  - Sequences │  │
│  │ - Field mapping  │  │  - Side mapping  │  │  - Metadata  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │ Normalized V2 Messages
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Kafka Producer                           │
│  • Schema Registry (Avro V2)                                   │
│  • Topic routing: market.{asset_class}.trades.{exchange}       │
│  • Idempotency: message_id (UUID)                             │
│  • At-least-once delivery with retries                         │
└─────────────────────────────────────────────────────────────────┘
```

### Core Components

#### 1. Streaming Client

**Responsibilities**:
- Establish and maintain WebSocket/TCP connection
- Handle authentication (API keys, OAuth, sessions)
- Subscribe to data channels (symbols, message types)
- Parse raw messages (JSON, FIX, proprietary binary)
- Reconnect on disconnection with exponential backoff
- Rate limiting and backpressure handling

**Key Interfaces**:
```python
class StreamingClient(ABC):
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to streaming source"""

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to market data for symbols"""

    @abstractmethod
    async def on_message(self, message: dict) -> None:
        """Handle incoming message"""

    @abstractmethod
    async def disconnect(self) -> None:
        """Graceful shutdown"""
```

#### 2. Message Transformer

**Responsibilities**:
- Map source-specific fields to V2 schema
- Normalize data types (timestamps, decimals, enums)
- Generate platform metadata (message_id, ingestion_timestamp)
- Populate vendor_data with exchange-specific fields
- Validate message completeness

**Mapping Rules**:
- **Required Fields**: Fail fast if missing critical fields (symbol, price, quantity)
- **Optional Fields**: Use null for missing values (source_sequence, trade_conditions)
- **Vendor Fields**: Preserve all unmapped fields in vendor_data map
- **Type Conversion**: Exchange timestamps → microseconds UTC, floats → Decimal(18,8)

#### 3. Kafka Producer Integration

**Responsibilities**:
- Serialize messages to Avro V2 format
- Register with Schema Registry (BACKWARD compatibility)
- Route to correct topic based on asset_class and exchange
- Handle produce errors with retry logic
- Track metrics (throughput, latency, errors)

---

## Reference Implementation: Binance Spot

### Configuration

**Connection**:
```python
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]  # Configurable
ASSET_CLASS = "crypto"
EXCHANGE = "BINANCE"
```

**Kafka Topics**:
```
market.crypto.trades.binance  # Binance trade stream
```

### Message Flow

**1. Binance Raw Message (aggTrade)**:
```json
{
  "e": "aggTrade",
  "E": 1705228800123,
  "s": "BTCUSDT",
  "a": 789012,
  "p": "42150.50",
  "q": "0.05",
  "f": 123456,
  "l": 123457,
  "T": 1705228800120,
  "m": true,
  "M": true
}
```

**2. Transformed to V2 Schema**:
```json
{
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "trade_id": "BINANCE-789012",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "timestamp": 1705228800120000,
  "price": "42150.50000000",
  "quantity": "0.05000000",
  "currency": "USDT",
  "side": "SELL",
  "trade_conditions": [],
  "source_sequence": null,
  "ingestion_timestamp": 1705228800123456,
  "platform_sequence": null,
  "vendor_data": {
    "is_buyer_maker": "true",
    "event_type": "aggTrade",
    "first_trade_id": "123456",
    "last_trade_id": "123457"
  }
}
```

### Field Mappings

| Binance Field | V2 Field | Transformation | Notes |
|---------------|----------|----------------|-------|
| `s` | `symbol` | Direct | Symbol (e.g., BTCUSDT) |
| `a` | `trade_id` | Prefix with "BINANCE-" | Aggregate trade ID |
| `p` | `price` | String → Decimal(18,8) | Price as decimal |
| `q` | `quantity` | String → Decimal(18,8) | Quantity as decimal |
| `T` | `timestamp` | Millis → micros | Exchange timestamp |
| `m` | `side` | Map: true→SELL, false→BUY | Buyer is maker = aggressor sold |
| `E` | `ingestion_timestamp` | Millis → micros | Event time |
| N/A | `currency` | Hard-coded "USDT" | Derived from symbol |
| N/A | `asset_class` | Hard-coded "crypto" | Exchange type |
| `e`, `f`, `l`, `M` | `vendor_data` | JSON map | Exchange-specific fields |

### Reliability Features

**Connection Management**:
- Auto-reconnect on disconnection (exponential backoff: 1s, 2s, 4s, 8s, max 60s)
- Heartbeat/ping every 3 minutes (Binance requires <10 min)
- Connection state tracking: DISCONNECTED → CONNECTING → CONNECTED → ERROR

**Error Handling**:
- **Transient Errors**: Retry with backoff (network issues, rate limits)
- **Permanent Errors**: Log and skip (malformed messages, schema violations)
- **Dead Letter Queue**: Route unparseable messages to DLQ for manual review

**Performance**:
- Async I/O for non-blocking message processing
- Batching: Collect 100 messages or 1 second timeout before Kafka produce
- Backpressure: Slow down subscription if consumer can't keep up

### Operational Metrics

**Binance-Specific Metrics**:
```promql
# Connection health
binance_connection_state{symbol="BTCUSDT"}

# Messages received
rate(binance_messages_received_total[5m])

# Message processing latency
histogram_quantile(0.99, rate(binance_message_latency_seconds_bucket[5m]))

# Connection errors
rate(binance_connection_errors_total[5m])
```

**Kafka Producer Metrics**:
```promql
# Messages produced to Kafka
rate(kafka_messages_produced_total{exchange="BINANCE"}[5m])

# Produce errors
rate(kafka_produce_errors_total{exchange="BINANCE"}[5m])
```

### Validated Performance

**Phase 2 E2E Validation** (2026-01-13):
- **Messages Received**: 69,666+ messages (BTCUSDT, ETHUSDT, BNBUSDT)
- **Producer Throughput**: 10,000 msg/sec peak
- **Consumer Throughput**: 138 msg/sec sustained (with Iceberg writes)
- **Connection Stability**: 6+ hours continuous streaming
- **Data Quality**: 5,000 messages written to Iceberg, 0 schema errors

**Results**:
- ✅ E2E pipeline operational (Binance WebSocket → Kafka → Iceberg → DuckDB)
- ✅ Schema evolution validated (V2 hybrid approach works)
- ✅ Multi-asset class proven (crypto + equities in same platform)
- ✅ Production-ready reliability (auto-reconnect, error handling, metrics)

---

## Integration Pattern for New Sources

### Step-by-Step Integration Guide

#### Phase 1: Connection & Authentication (2-4 hours)

1. **Create Streaming Client**:
   ```python
   class NewSourceClient:
       def __init__(self, api_key: str, symbols: List[str]):
           self.ws_url = "wss://new-source.com/stream"
           self.api_key = api_key
           self.symbols = symbols

       async def connect(self):
           # Establish WebSocket connection
           # Handle authentication
           # Subscribe to channels
   ```

2. **Test Connection**:
   - Verify authentication works
   - Subscribe to test symbols
   - Log raw messages to understand format

3. **Add Metrics**:
   - Connection state gauge
   - Messages received counter
   - Connection error counter

#### Phase 2: Schema Mapping (2-3 hours)

1. **Map Fields to V2 Schema**:
   - Create mapping table (source field → V2 field)
   - Identify required vs optional fields
   - List vendor-specific fields for vendor_data

2. **Write Transformer**:
   ```python
   class NewSourceTransformer:
       def transform(self, raw_message: dict) -> dict:
           return {
               "message_id": str(uuid.uuid4()),
               "trade_id": f"NEWSOURCE-{raw_message['id']}",
               "symbol": raw_message['symbol'],
               "exchange": "NEWSOURCE",
               "asset_class": "equities",  # or crypto, futures, etc.
               "timestamp": self._to_micros(raw_message['timestamp']),
               "price": Decimal(raw_message['price']),
               "quantity": Decimal(raw_message['volume']),
               # ... map other fields
               "vendor_data": self._extract_vendor_data(raw_message)
           }
   ```

3. **Validate Transformation**:
   - Unit tests for field mapping
   - Test with real sample messages
   - Verify V2 schema compliance

#### Phase 3: Integration & Testing (3-4 hours)

1. **Integrate with Kafka Producer**:
   - Route to correct topic: `market.{asset_class}.trades.{exchange}`
   - Register V2 schema with Schema Registry
   - Add produce error handling

2. **End-to-End Testing**:
   - Stream 1,000 messages
   - Verify Kafka ingestion
   - Check Iceberg writes
   - Query data via DuckDB

3. **Performance Testing**:
   - Measure throughput (target: >100 msg/sec)
   - Test reconnection scenarios
   - Validate metric collection

#### Phase 4: Operations (1-2 hours)

1. **Create Operational Runbook**:
   - Connection troubleshooting
   - Common errors and resolutions
   - Monitoring dashboards

2. **Add Alerting Rules**:
   - Connection down for >5 minutes
   - Message rate drops to 0
   - High error rate (>5%)

3. **Documentation**:
   - Update this document with new source
   - Add field mappings to data dictionary
   - Create integration guide

**Total Estimated Effort**: 8-13 hours for new source integration

---

## Design Patterns

### 1. Exponential Backoff for Reconnection

```python
async def connect_with_retry(self):
    retry_delay = 1  # Start with 1 second
    max_delay = 60   # Cap at 60 seconds

    while True:
        try:
            await self.connect()
            self.retry_delay = 1  # Reset on success
            break
        except ConnectionError as e:
            logger.warning(f"Connection failed: {e}. Retrying in {retry_delay}s")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
```

### 2. Heartbeat/Keepalive

```python
async def heartbeat_loop(self):
    while self.connected:
        await asyncio.sleep(180)  # 3 minutes
        await self.send_ping()

async def send_ping(self):
    await self.ws.send(json.dumps({"ping": int(time.time() * 1000)}))
```

### 3. Graceful Shutdown

```python
async def shutdown(self):
    logger.info("Initiating graceful shutdown")
    self.connected = False

    # Flush any pending messages
    await self.flush_pending_messages()

    # Unsubscribe from channels
    await self.unsubscribe_all()

    # Close WebSocket
    await self.ws.close()

    logger.info("Shutdown complete")
```

### 4. Message Batching

```python
async def batch_produce(self):
    batch = []
    batch_timeout = 1.0  # seconds
    batch_size = 100

    while True:
        try:
            message = await asyncio.wait_for(
                self.message_queue.get(),
                timeout=batch_timeout
            )
            batch.append(message)

            if len(batch) >= batch_size:
                await self.produce_batch(batch)
                batch = []

        except asyncio.TimeoutError:
            if batch:
                await self.produce_batch(batch)
                batch = []
```

---

## Operational Considerations

### Monitoring Checklist

- [ ] **Connection State**: Gauge showing CONNECTED/DISCONNECTED/ERROR
- [ ] **Message Rate**: Messages received per second
- [ ] **Latency**: Time from exchange timestamp to Kafka produce
- [ ] **Error Rate**: Failed message transformations
- [ ] **Reconnection Count**: How often connection drops
- [ ] **Queue Depth**: Backlog of unprocessed messages

### Alerting Rules

```yaml
# Connection down
- alert: StreamingSourceDisconnected
  expr: streaming_connection_state{source="binance"} == 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Binance streaming disconnected for >5 minutes"

# No messages received
- alert: StreamingSourceNoMessages
  expr: rate(streaming_messages_received_total{source="binance"}[5m]) == 0
  for: 2m
  labels:
    severity: high
  annotations:
    summary: "No messages received from Binance in last 2 minutes"

# High error rate
- alert: StreamingSourceHighErrorRate
  expr: rate(streaming_message_errors_total{source="binance"}[5m]) > 0.05
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Binance message error rate >5%"
```

### Capacity Planning

**Single Instance Limits**:
- **Connections**: 1-10 WebSocket connections per instance (source-dependent)
- **Symbols**: 10-100 symbols per connection (source-dependent)
- **Throughput**: 1,000-10,000 msg/sec per instance
- **Memory**: 500 MB - 2 GB per instance

**Scaling Horizontally**:
- Run multiple instances for different symbol groups
- Example: Instance 1 (BTC pairs), Instance 2 (ETH pairs), Instance 3 (altcoins)
- Load balancer not needed (WebSocket connections are long-lived)

---

## Security Considerations

### API Key Management

- **Never commit API keys to git**
- Store in environment variables or secrets manager (AWS Secrets Manager, HashiCorp Vault)
- Rotate keys periodically (every 90 days)
- Use read-only API keys when possible

### Network Security

- **TLS/SSL**: All WebSocket connections must use wss:// (not ws://)
- **IP Whitelisting**: Configure exchange to only accept connections from K2 platform IPs
- **Rate Limiting**: Respect exchange rate limits to avoid bans
- **DDoS Protection**: Use cloud provider firewall rules

### Data Validation

- **Schema Validation**: Verify messages match expected structure before transformation
- **Anomaly Detection**: Flag unusual price movements or volumes for review
- **Duplicate Detection**: Use message_id to deduplicate messages
- **Gap Detection**: Track source_sequence to identify missed messages

---

## Future Enhancements

### Planned Integrations

1. **Bloomberg B-PIPE** (Equities, Fixed Income)
   - Protocol: TCP/IP with proprietary binary format
   - Estimated Effort: 15-20 hours
   - Status: Planned for Phase 4

2. **Refinitiv TREP** (Equities, Forex, Commodities)
   - Protocol: RMDS (Reuters Market Data System)
   - Estimated Effort: 20-25 hours
   - Status: Planned for Phase 5

3. **ICE Market Data** (Futures, Options)
   - Protocol: FIX 5.0 / FAST
   - Estimated Effort: 15-20 hours
   - Status: Under evaluation

### Enhancement Opportunities

- **WebSocket Connection Pooling**: Reuse connections for multiple symbols
- **Message Compression**: Reduce network bandwidth (gzip, zstd)
- **Smart Routing**: Route messages to different Kafka partitions by symbol
- **Real-Time Validation**: Validate messages against reference data before Kafka
- **Market Replay**: Store raw messages for debugging and replay

---

## Related Documentation

### Architecture
- [Platform Positioning](./platform-positioning.md) - K2 as L3 Cold Path platform
- [System Design](./system-design.md) - Overall architecture
- [Schema Design V2](./schema-design-v2.md) - V2 hybrid schema approach

### Implementation
- [Phase 2 Prep Completion Report](../phases/v1/phase-2-prep/COMPLETION-REPORT.md) - Binance integration details
- [Data Dictionary V2](../reference/data-dictionary-v2.md) - V2 schema field reference

### Operations
- [Binance Streaming Guide](../operations/binance-streaming-guide.md) - Operational runbook
- [Kafka Runbook](../operations/kafka-runbook.md) - Kafka operations

### Code
- Source: `src/k2/ingestion/binance_client.py` - Binance WebSocket client
- Tests: `tests/integration/test_binance_streaming.py` - Integration tests

---

**Maintained By**: Platform Engineering Team
**Last Integration**: Binance Spot (2026-01-13)
**Next Review**: 2026-04-14 (quarterly, or when adding new source)

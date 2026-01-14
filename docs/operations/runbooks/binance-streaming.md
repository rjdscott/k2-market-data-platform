# Binance Streaming Operations Runbook

**Component**: Binance WebSocket Streaming Client
**Audience**: DevOps, SREs, Platform Engineers
**Last Updated**: 2026-01-14
**Complexity**: Medium

---

## Overview

This runbook covers operational procedures for the Binance WebSocket streaming integration. The Binance client streams real-time crypto trade data (BTCUSDT, ETHUSDT, BNBUSDT) to Kafka topic `market.crypto.trades.binance`.

### Quick Facts

- **Connection**: wss://stream.binance.com:9443/ws
- **Protocol**: WebSocket (aggTrade stream)
- **Throughput**: 10,000 msg/sec peak, ~138 msg/sec sustained to Iceberg
- **Data Volume**: 69,666+ messages validated E2E (Phase 2)
- **Uptime Target**: 99.5% (streaming client auto-reconnects)
- **Latency**: <100ms from exchange to Kafka

---

## Starting the Binance Client

### Manual Start

```bash
# Activate environment
source .venv/bin/activate

# Start Binance streaming client
uv run python src/k2/ingestion/binance_client.py \
  --symbols BTCUSDT ETHUSDT BNBUSDT \
  --kafka-bootstrap localhost:9092 \
  --schema-registry http://localhost:8081

# Verify connection
tail -f logs/binance_client.log | grep "Connected"
```

### Docker Compose (Production)

```yaml
# docker-compose.yml
services:
  binance-client:
    image: k2-platform:latest
    command: uv run python src/k2/ingestion/binance_client.py
    environment:
      - BINANCE_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT
      - KAFKA_BOOTSTRAP=kafka:9092
      - SCHEMA_REGISTRY_URL=http://schema-registry:8081
    restart: unless-stopped
    depends_on:
      - kafka
      - schema-registry
```

### Verification

```bash
# Check process is running
ps aux | grep binance_client

# Check Kafka topic has messages
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance \
  --max-messages 5

# Check Prometheus metrics
curl -s http://localhost:9090/api/v1/query?query=binance_messages_received_total | jq
```

**Expected Output**:
```
✓ Process running with PID 12345
✓ Messages appearing in Kafka topic
✓ Prometheus showing message rate >0
```

---

## Monitoring

### Key Metrics

#### Connection Health

```promql
# Connection state (1 = connected, 0 = disconnected)
binance_connection_state

# Connection errors (rate per 5 minutes)
rate(binance_connection_errors_total[5m])

# Reconnection count
binance_reconnections_total
```

#### Message Throughput

```promql
# Messages received per second
rate(binance_messages_received_total[5m])

# Messages produced to Kafka per second
rate(kafka_messages_produced_total{exchange="BINANCE"}[5m])

# Message processing latency (p99)
histogram_quantile(0.99, rate(binance_message_latency_seconds_bucket[5m]))
```

#### Error Rates

```promql
# Message parsing errors
rate(binance_message_errors_total[5m])

# Kafka produce errors
rate(kafka_produce_errors_total{exchange="BINANCE"}[5m])
```

### Grafana Dashboard

**Panel 1: Connection Status**
```json
{
  "title": "Binance Connection State",
  "type": "stat",
  "targets": [{
    "expr": "binance_connection_state"
  }],
  "mappings": [
    {"value": 1, "text": "CONNECTED", "color": "green"},
    {"value": 0, "text": "DISCONNECTED", "color": "red"}
  ]
}
```

**Panel 2: Message Rate**
```json
{
  "title": "Messages Received (msg/sec)",
  "type": "graph",
  "targets": [{
    "expr": "rate(binance_messages_received_total[5m])",
    "legendFormat": "Messages/sec"
  }]
}
```

**Panel 3: Latency Distribution**
```json
{
  "title": "Message Processing Latency",
  "type": "graph",
  "targets": [
    {"expr": "histogram_quantile(0.50, rate(binance_message_latency_seconds_bucket[5m]))", "legendFormat": "p50"},
    {"expr": "histogram_quantile(0.95, rate(binance_message_latency_seconds_bucket[5m]))", "legendFormat": "p95"},
    {"expr": "histogram_quantile(0.99, rate(binance_message_latency_seconds_bucket[5m]))", "legendFormat": "p99"}
  ]
}
```

### Alerting Rules

```yaml
# config/prometheus/rules/binance_alerts.yml
groups:
  - name: binance_streaming
    rules:
      - alert: BinanceDisconnected
        expr: binance_connection_state == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Binance WebSocket disconnected for >5 minutes"
          description: "Connection state has been 0 for {{ $value }} minutes"
          runbook: "docs/operations/runbooks/binance-streaming.md"

      - alert: BinanceNoMessages
        expr: rate(binance_messages_received_total[5m]) == 0
        for: 2m
        labels:
          severity: high
        annotations:
          summary: "No messages received from Binance in last 2 minutes"
          runbook: "docs/operations/runbooks/binance-streaming.md"

      - alert: BinanceHighErrorRate
        expr: rate(binance_message_errors_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Binance message error rate >5%"
          description: "Error rate: {{ $value | humanizePercentage }}"
          runbook: "docs/operations/runbooks/binance-streaming.md"
```

---

## Troubleshooting

### Issue: Connection Keeps Dropping

**Symptoms**:
- `binance_connection_state` fluctuating between 0 and 1
- Frequent "Connection closed" messages in logs
- High reconnection count

**Diagnosis**:
```bash
# Check connection error logs
docker logs binance-client | grep "Connection"

# Check network connectivity
curl -v wss://stream.binance.com:9443/ws

# Check for rate limiting
docker logs binance-client | grep "429\|rate limit"
```

**Root Causes**:
1. **Network issues**: Intermittent connectivity to Binance
2. **Missing heartbeat**: Client not sending ping frames (Binance requires <10 min)
3. **Rate limiting**: Too many connection attempts
4. **Firewall rules**: Outbound WebSocket blocked

**Resolution**:

**Option A: Check Heartbeat**
```bash
# Verify heartbeat is configured (should ping every 3 minutes)
grep "heartbeat\|ping" src/k2/ingestion/binance_client.py
```

**Option B: Check Network**
```bash
# Test WebSocket connection manually
wscat -c wss://stream.binance.com:9443/ws

# Send test ping
> {"ping": 1705228800000}

# Should receive pong
< {"pong": 1705228800000}
```

**Option C: Adjust Reconnection Logic**
```python
# Increase max backoff delay if too aggressive
MAX_RECONNECT_DELAY = 60  # seconds (was 30)
```

---

### Issue: Messages Not Reaching Kafka

**Symptoms**:
- `binance_messages_received_total` increasing
- `kafka_messages_produced_total{exchange="BINANCE"}` not increasing
- Kafka topic empty or stale

**Diagnosis**:
```bash
# Check Kafka produce errors
curl -s 'http://localhost:9090/api/v1/query?query=rate(kafka_produce_errors_total{exchange="BINANCE"}[5m])' | jq

# Check Schema Registry connectivity
curl http://localhost:8081/subjects

# Check client logs for errors
docker logs binance-client | grep -A 5 "ERROR\|Failed to produce"
```

**Root Causes**:
1. **Schema Registry down**: Can't register/fetch Avro schema
2. **Kafka broker down**: Can't produce messages
3. **Schema incompatibility**: V2 schema not registered or incompatible
4. **Message serialization error**: Invalid data types or missing fields

**Resolution**:

**Option A: Verify Schema Registry**
```bash
# Check Schema Registry is up
curl http://localhost:8081/subjects

# Check V2 trade schema registered
curl http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest | jq

# If not registered, register manually
# (See docs/operations/runbooks/schema-registry-operations.md)
```

**Option B: Check Kafka Broker**
```bash
# Verify Kafka is healthy
docker compose ps kafka

# Test produce manually
echo '{"test": "message"}' | docker compose exec -T kafka kafka-console-producer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance
```

**Option C: Review Message Transformation**
```bash
# Check for transformation errors in logs
docker logs binance-client | grep "transformation\|mapping\|serialize"

# Verify V2 schema fields match transformed message
# Compare to docs/reference/data-dictionary-v2.md
```

---

### Issue: High Latency (>500ms)

**Symptoms**:
- `binance_message_latency_seconds` p99 >0.5 seconds
- Slow message processing
- Queue depth increasing

**Diagnosis**:
```bash
# Check latency metrics
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99, rate(binance_message_latency_seconds_bucket[5m]))' | jq

# Check CPU/memory usage
docker stats binance-client

# Check Kafka producer lag
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group k2-iceberg-writer-crypto-v2 \
  --describe
```

**Root Causes**:
1. **Network latency**: High latency to Binance or Kafka
2. **CPU bottleneck**: Message transformation too slow
3. **Kafka broker lag**: Broker can't keep up with produce rate
4. **Schema Registry lag**: Slow schema lookups

**Resolution**:

**Option A: Optimize Transformation**
```python
# Cache Schema Registry lookups
self.schema_cache = {}  # Add caching layer

# Batch Kafka produces
await self.producer.send_batch(messages)  # Instead of one-by-one
```

**Option B: Scale Horizontally**
```bash
# Run multiple instances for different symbols
docker compose scale binance-client=3

# Configure each instance with different symbols
# Instance 1: BTCUSDT
# Instance 2: ETHUSDT
# Instance 3: BNBUSDT
```

**Option C: Tune Kafka Producer**
```python
# Increase batch size
producer_config = {
    "batch.size": 65536,  # Increased from 16384
    "linger.ms": 100,     # Wait 100ms before sending
    "compression.type": "zstd"  # Compress messages
}
```

---

### Issue: Message Parsing Errors

**Symptoms**:
- `binance_message_errors_total` increasing
- Logs showing "Failed to parse message" or "Invalid field"
- Some messages reaching DLQ

**Diagnosis**:
```bash
# Check error logs
docker logs binance-client | grep "parse\|invalid\|error" | tail -20

# Check DLQ for failed messages
ls -la /tmp/dlq/binance-*.jsonl

# View failed messages
cat /tmp/dlq/binance-20260114.jsonl | jq '.'
```

**Root Causes**:
1. **Binance API change**: New fields added or removed
2. **Unexpected message type**: Receiving non-aggTrade messages
3. **Data type mismatch**: String where number expected, or vice versa
4. **Missing required field**: Symbol or price missing

**Resolution**:

**Option A: Update Field Mappings**
```python
# Check Binance API documentation for changes
# https://binance-docs.github.io/apidocs/spot/en/#aggregate-trade-streams

# Update transformation logic if needed
# src/k2/ingestion/binance_client.py
```

**Option B: Add Defensive Parsing**
```python
# Handle optional fields gracefully
price = raw_message.get("p", None)
if price is None:
    logger.warning(f"Missing price in message: {raw_message}")
    return None  # Skip message

# Validate data types
try:
    price = Decimal(price)
except (ValueError, TypeError) as e:
    logger.error(f"Invalid price format: {price}")
    return None
```

**Option C: Review DLQ Messages**
```bash
# Manually review failed messages
cat /tmp/dlq/binance-20260114.jsonl | jq -r '.original_message' | head -5

# Identify pattern (e.g., all messages for specific symbol)
cat /tmp/dlq/binance-20260114.jsonl | jq -r '.original_message.s' | sort | uniq -c
```

---

## Configuration

### Environment Variables

```bash
# Binance WebSocket URL
BINANCE_WS_URL=wss://stream.binance.com:9443/ws

# Symbols to stream (comma-separated)
BINANCE_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT

# Kafka configuration
KAFKA_BOOTSTRAP=localhost:9092
KAFKA_TOPIC_PREFIX=market.crypto.trades

# Schema Registry
SCHEMA_REGISTRY_URL=http://localhost:8081

# Reconnection settings
BINANCE_RECONNECT_DELAY_MIN=1     # seconds
BINANCE_RECONNECT_DELAY_MAX=60    # seconds

# Heartbeat settings
BINANCE_HEARTBEAT_INTERVAL=180    # seconds (3 minutes)

# Batching settings
BINANCE_BATCH_SIZE=100            # messages
BINANCE_BATCH_TIMEOUT=1.0         # seconds
```

### Adding New Symbols

```bash
# Edit configuration
export BINANCE_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,ADAUSDT,SOLUSDT

# Restart client
docker compose restart binance-client

# Verify new symbols streaming
docker logs -f binance-client | grep "Subscribed"
```

### Changing Symbols Without Downtime

```bash
# Start new instance with additional symbols
docker compose scale binance-client=2

# Configure second instance with new symbols only
docker compose exec binance-client-2 \
  python src/k2/ingestion/binance_client.py --symbols ADAUSDT SOLUSDT

# Stop old instance after validation
docker compose stop binance-client-1
```

---

## Capacity Planning

### Current Capacity (Single Instance)

- **Symbols**: 3 (BTCUSDT, ETHUSDT, BNBUSDT)
- **Message Rate**: ~10,000 msg/sec peak (aggregated across all symbols)
- **Network Bandwidth**: ~5 MB/sec inbound
- **CPU Usage**: ~10-15% (1 core)
- **Memory Usage**: ~200 MB

### Scaling Guidelines

| Symbols | Instances | Total Throughput | Memory per Instance |
|---------|-----------|------------------|---------------------|
| 1-5     | 1         | 10K msg/sec      | 200 MB              |
| 6-15    | 2-3       | 30K msg/sec      | 250 MB              |
| 16-50   | 4-10      | 100K msg/sec     | 300 MB              |
| 51-100  | 11-20     | 200K msg/sec     | 350 MB              |

**Recommendation**: 1 instance per 5 high-volume symbols (BTC, ETH) or 10 low-volume symbols

---

## Maintenance

### Graceful Shutdown

```bash
# Send SIGTERM for graceful shutdown (flushes pending messages)
docker compose stop binance-client --timeout 30

# Verify no messages in flight
docker logs binance-client | tail -20 | grep "Shutdown complete"
```

### Log Rotation

```bash
# Logs location
logs/binance_client.log

# Rotate logs daily (logrotate config)
/var/log/binance_client.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 0644 k2 k2
}
```

### Health Checks

```bash
# HTTP health endpoint (if implemented)
curl http://localhost:8001/health

# Expected response
{
  "status": "healthy",
  "connection_state": "CONNECTED",
  "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
  "uptime_seconds": 3600,
  "messages_received": 150000
}
```

---

## Performance Characteristics

### Observed Metrics (Phase 2 Validation)

**Throughput**:
- Peak: 10,000 msg/sec (producer to Kafka)
- Sustained: 138 msg/sec (E2E to Iceberg, I/O bound)
- Batch processing: 2,000-2,500 msg/sec (Iceberg writes)

**Latency**:
- Exchange to client: <50ms
- Client to Kafka: <100ms
- E2E (exchange to Iceberg): <5 seconds

**Reliability**:
- Connection stability: 6+ hours continuous
- Auto-reconnect: <5 seconds after disconnection
- Data quality: 0 schema errors in 69,666+ messages

---

## References

### Documentation
- [Streaming Sources Architecture](../../architecture/streaming-sources.md) - Generic patterns
- [Data Dictionary V2](../../reference/data-dictionary-v2.md) - V2 schema reference
- [Phase 2 Completion Report](../../phases/phase-2-prep/COMPLETION-REPORT.md) - Integration details

### External
- [Binance WebSocket API](https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams)
- [Binance Aggregate Trade Streams](https://binance-docs.github.io/apidocs/spot/en/#aggregate-trade-streams)

### Code
- Source: `src/k2/ingestion/binance_client.py`
- Tests: `tests/integration/test_binance_streaming.py`

---

**Maintained By**: Platform Engineering Team
**Last Tested**: 2026-01-13 (69,666+ messages, 0 errors)
**Next Review**: 2026-02-14 (monthly)

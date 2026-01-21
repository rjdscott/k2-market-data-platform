# Streaming Validation Guide

**Last Updated**: 2026-01-18

This guide covers how to validate WebSocket streaming for Binance and Kraken clients.

## Quick Validation

### Test Individual Exchange

```bash
# Test Binance (10 trades, ~30 seconds)
python scripts/test_binance_stream.py

# Test Kraken (10 trades, ~30-60 seconds)
python scripts/test_kraken_stream.py

# Custom symbols
python scripts/test_binance_stream.py --symbols BTCUSDT ETHUSDT BNBEUR
python scripts/test_kraken_stream.py --symbols BTC/USD ETH/USD DOT/EUR
```

### Comprehensive Validation

```bash
# Validate both exchanges with detailed report
python scripts/validate_streaming.py

# Validate single exchange
python scripts/validate_streaming.py --exchange binance
python scripts/validate_streaming.py --exchange kraken
```

**Expected Output**:
- ✅ Connection successful
- ✅ 10 valid trades per exchange
- ✅ 100% v2 schema compliance
- ✅ Message rate: 0.1-100 trades/sec (depends on market activity)
- ✅ All required fields present (message_id, trade_id, symbol, price, etc.)

## Unit Tests

### Run All WebSocket Tests

```bash
# All WebSocket unit tests (60 tests, ~3 seconds)
uv run pytest tests/unit/test_binance_client.py tests/unit/test_kraken_client.py -v

# Just Binance (30 tests)
uv run pytest tests/unit/test_binance_client.py -v

# Just Kraken (30 tests)
uv run pytest tests/unit/test_kraken_client.py -v
```

**Coverage**:
- Symbol/pair parsing (7 tests each)
- Message validation (5 tests each)
- Side mapping (2 tests each)
- Message conversion (8 tests each)
- Client initialization (5 tests each)
- Memory leak detection (4 tests each)

## Integration Tests

### Live Streaming Validation

```bash
# All integration tests (6 tests, ~90 seconds, requires internet)
uv run pytest tests/integration/test_streaming_validation.py -v

# Specific tests
uv run pytest tests/integration/test_streaming_validation.py -k binance  # Binance only
uv run pytest tests/integration/test_streaming_validation.py -k kraken   # Kraken only
uv run pytest tests/integration/test_streaming_validation.py -k concurrent  # Both exchanges
```

**Tests Included**:
1. `test_binance_streaming_validation` - Validate Binance streams valid v2 trades
2. `test_kraken_streaming_validation` - Validate Kraken streams valid v2 trades
3. `test_concurrent_streaming` - Validate both exchanges can stream concurrently
4. `test_kraken_xbt_normalization` - Validate XBT → BTC normalization
5. `test_message_rate` - Validate message rates are reasonable

**Notes**:
- Integration tests require internet connectivity
- Tests will timeout after 30-60 seconds if no data received
- Tests are marked with `@pytest.mark.integration` and can be skipped in CI

## V2 Schema Validation

### Required Fields

All trades must include:
- `message_id` (UUID string, 36 chars)
- `trade_id` (exchange-specific format)
- `symbol` (e.g., "BTCUSDT", "BTCUSD")
- `exchange` ("BINANCE" or "KRAKEN")
- `asset_class` ("crypto")
- `timestamp` (microseconds, int64)
- `price` (Decimal, positive)
- `quantity` (Decimal, positive)
- `currency` (e.g., "USDT", "USD")
- `side` ("BUY" or "SELL")
- `trade_conditions` (list, may be empty)
- `source_sequence` (nullable)
- `ingestion_timestamp` (microseconds, int64)
- `platform_sequence` (nullable)
- `vendor_data` (dict with exchange-specific fields)

### Exchange-Specific Validation

**Binance**:
- `symbol` format: No separator (e.g., "BTCUSDT")
- `trade_id` format: "BINANCE-{trade_id}"
- `vendor_data` includes: `base_asset`, `quote_asset`, `is_buyer_maker`, `event_type`

**Kraken**:
- `symbol` format: No separator, XBT normalized to BTC (e.g., "BTCUSD")
- `trade_id` format: "KRAKEN-{timestamp}-{hash}"
- `vendor_data` includes: `pair`, `base_asset`, `quote_asset`, `order_type`, `raw_timestamp`
- **XBT normalization**: "XBT/USD" → symbol="BTCUSD", base_asset="BTC"

## Troubleshooting

### No Trades Received

**Symptoms**: Script times out without receiving any trades

**Causes**:
1. No internet connectivity
2. Exchange WebSocket API is down
3. Symbol doesn't have active trading
4. Firewall blocking WebSocket connections

**Solutions**:
```bash
# Test connectivity
curl -I https://stream.binance.com
curl -I https://ws.kraken.com

# Try more liquid symbols
python scripts/test_binance_stream.py --symbols BTCUSDT  # Most liquid
python scripts/test_kraken_stream.py --symbols BTC/USD   # Most liquid

# Check logs for connection errors
python scripts/test_kraken_stream.py 2>&1 | grep -i error
```

### Schema Validation Errors

**Symptoms**: "Invalid trade" errors in validation output

**Common Issues**:
1. **Negative price/quantity**: Should never happen, indicates data corruption
2. **Invalid timestamp**: Check system clock, timestamp should be recent
3. **Missing fields**: Check Avro schema version matches client implementation
4. **Invalid side**: Must be "BUY" or "SELL", check side mapping logic

**Debug**:
```python
# Add debug logging to see raw messages
import structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

# Run with verbose logging
python scripts/validate_streaming.py
```

### Slow Message Rate

**Symptoms**: Message rate < 0.1 trades/sec

**Causes**:
1. Illiquid symbol (e.g., exotic pairs)
2. Off-hours trading (lower activity on weekends)
3. Network latency issues

**Solutions**:
```bash
# Use more liquid symbols
python scripts/test_binance_stream.py --symbols BTCUSDT ETHUSDT

# Test during peak hours (15:00-21:00 UTC typically most active)
```

### Memory Leak Detection

**Symptoms**: `memory_leak_detection_score` > 0.8

**Causes**:
1. Long-running connection accumulating buffers
2. Circular references in message handlers
3. Not releasing resources properly

**Solutions**:
- Connection rotation is enabled by default (4 hours)
- Memory monitoring runs every 30 seconds
- Check logs for leak warnings: `grep "memory_leak_detected" logs.txt`

## Performance Benchmarks

### Expected Performance

| Metric | Binance | Kraken | Notes |
|--------|---------|--------|-------|
| Connection time | <5s | <5s | First connection may be slower |
| Time to first trade | <10s | <30s | Depends on market activity |
| Message rate | 1-100/sec | 0.1-10/sec | Varies by symbol liquidity |
| Message latency | <100ms | <200ms | Network + processing time |
| Memory growth | <10 MB/hr | <10 MB/hr | With rotation enabled |

### Stress Testing

```bash
# Test with multiple symbols (higher throughput)
python scripts/test_binance_stream.py --symbols BTCUSDT ETHUSDT BNBEUR SOLUSDT ADAUSDT

# Run for extended duration (modify MAX_TRADES in script)
# Default: 10 trades, increase for longer runs
```

## CI/CD Integration

### Skip Integration Tests in CI

```bash
# Skip integration tests (no internet connectivity needed)
pytest tests/ -m "not integration"

# Run only integration tests (requires internet)
pytest tests/ -m integration
```

### GitHub Actions Example

```yaml
- name: Run unit tests
  run: uv run pytest tests/unit/ -v

- name: Run integration tests (with internet)
  if: github.event_name == 'schedule'  # Only on scheduled runs
  run: uv run pytest tests/integration/test_streaming_validation.py -v
```

## Additional Resources

- [Binance WebSocket API Docs](https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams)
- [Kraken WebSocket API Docs](https://docs.kraken.com/websockets/)
- [k2/ingestion/binance_client.py](../src/k2/ingestion/binance_client.py) - Client implementation
- [k2/ingestion/kraken_client.py](../src/k2/ingestion/kraken_client.py) - Client implementation
- [tests/unit/test_binance_client.py](../tests/unit/test_binance_client.py) - Unit tests
- [tests/unit/test_kraken_client.py](../tests/unit/test_kraken_client.py) - Unit tests
- [tests/integration/test_streaming_validation.py](../tests/integration/test_streaming_validation.py) - Integration tests

## Quick Reference

```bash
# Most common commands
uv run pytest tests/unit/test_kraken_client.py -v        # Unit tests
python scripts/test_kraken_stream.py                     # Quick live test
python scripts/validate_streaming.py                     # Full validation
uv run pytest tests/integration/test_streaming_validation.py -k kraken  # Integration test
```

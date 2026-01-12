# Testing Guide

**Purpose**: Test patterns and procedures for Phase 2 Prep
**Last Updated**: 2026-01-12

---

## Testing Strategy

Following CLAUDE.md pragmatic TDD:
1. Write happy-path test → minimal implementation
2. Add important edge/error case
3. Make tests pass
4. Refactor only when needed
5. Repeat

---

## Unit Testing Patterns

### Schema Validation Tests

```python
import json
import pytest

def test_trade_v2_schema_valid():
    """v2 trade schema validates with Avro."""
    with open('src/k2/schemas/trade_v2.avsc') as f:
        schema = json.load(f)

    assert schema['name'] == 'TradeV2'
    assert schema['namespace'] == 'com.k2.marketdata'
    assert 'fields' in schema
    assert len(schema['fields']) >= 14  # Core fields
```

### Message Building Tests

```python
from decimal import Decimal
from datetime import datetime

def test_build_trade_v2():
    """build_trade_v2() creates valid v2 message."""
    msg = build_trade_v2(
        symbol="BHP",
        exchange="ASX",
        asset_class="equities",
        timestamp=datetime.utcnow(),
        price=Decimal("45.67"),
        quantity=Decimal("1000"),
        currency="AUD",
        side="BUY"
    )

    assert "message_id" in msg
    assert msg["symbol"] == "BHP"
    assert msg["quantity"] == Decimal("1000")
    assert msg["side"] == "BUY"
```

### Field Mapping Tests

```python
def test_map_volume_to_quantity():
    """CSV volume → v2 quantity mapping."""
    csv_row = {"volume": "1000"}
    v2_msg = map_csv_row_to_v2(csv_row)

    assert v2_msg["quantity"] == Decimal("1000")
    assert "volume" not in v2_msg  # Old field removed
```

### Side Mapping Tests

```python
@pytest.mark.parametrize("input_side,expected", [
    ("buy", "BUY"),
    ("sell", "SELL"),
    ("short", "SELL_SHORT"),
    ("unknown", "UNKNOWN"),
])
def test_map_side_enum(input_side, expected):
    """Side string → TradeSide enum mapping."""
    assert map_side(input_side) == expected
```

### Binance Conversion Tests

```python
def test_convert_binance_to_v2():
    """Binance trade → v2 Trade conversion."""
    binance_msg = {
        "e": "trade",
        "s": "BTCUSDT",
        "t": 12345,
        "p": "16500.00",
        "q": "0.05",
        "T": 1672531199900,
        "m": True  # Buyer is maker
    }

    v2 = convert_binance_trade_to_v2(binance_msg)

    assert v2["symbol"] == "BTCUSDT"
    assert v2["exchange"] == "BINANCE"
    assert v2["side"] == "SELL"  # Buyer maker → aggressor sold
    assert v2["currency"] == "USDT"
    assert "vendor_data" in v2
    assert v2["vendor_data"]["is_buyer_maker"] == "True"
```

---

## Integration Testing Patterns

### E2E Pipeline Test

```python
def test_e2e_v2_pipeline():
    """CSV → v2 Kafka → v2 Iceberg → v2 Query."""

    # 1. Load CSV with v2 schema
    batch_loader = BatchLoader(schema_version="v2")
    batch_loader.load("sample_asx_trades.csv")

    # 2. Consume to Iceberg
    consumer = MarketDataConsumer(schema_version="v2")
    consumer.consume(batch_size=100)

    # 3. Query v2 table
    engine = QueryEngine(table_version="v2")
    df = engine.query_trades(symbol="DVN", limit=100)

    # 4. Verify v2 fields
    assert "quantity" in df.columns
    assert "side" in df.columns
    assert "currency" in df.columns
    assert "vendor_data" in df.columns

    # 5. Verify data correctness
    assert all(df["currency"] == "AUD")  # ASX trades
    assert all(df["asset_class"] == "equities")
```

### Binance Streaming Test

```python
@pytest.mark.asyncio
async def test_binance_streaming_integration():
    """Binance WebSocket → Kafka → Iceberg."""

    # Start Binance client
    client = BinanceWebSocketClient(symbols=["BTCUSDT"])

    # Collect messages for 60 seconds
    messages = []
    def on_message(msg):
        messages.append(msg)

    client.on_message = on_message
    await client.connect()
    await asyncio.sleep(60)
    await client.disconnect()

    # Verify messages received
    assert len(messages) > 0

    # Verify in Iceberg
    engine = QueryEngine(table_version="v2")
    df = engine.query_trades(symbol="BTCUSDT", exchange="BINANCE")

    assert len(df) > 0
    assert all(df["exchange"] == "BINANCE")
    assert all(df["asset_class"] == "crypto")
```

---

## Mock Testing Patterns

### Mock Kafka Producer

```python
from unittest.mock import Mock, patch

def test_producer_with_mock():
    """Test producer with mocked Kafka."""
    with patch('k2.ingestion.producer.Producer') as mock_producer:
        producer = MarketDataProducer(schema_version="v2")

        msg = build_trade_v2(...)
        result = producer.produce_trade("equities", "ASX", msg)

        assert result is True
        mock_producer.return_value.produce.assert_called_once()
```

### Mock WebSocket

```python
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_binance_client_with_mock():
    """Test Binance client with mocked WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.__aiter__.return_value = [
        '{"e":"trade","s":"BTCUSDT","t":12345,"p":"16500.00","q":"0.05","T":1672531199900,"m":true}'
    ]

    with patch('websockets.connect', return_value=mock_ws):
        client = BinanceWebSocketClient()
        await client.connect()

        # Verify connection
        mock_ws.assert_awaited_once()
```

---

## Test Data Fixtures

### Sample v2 Trade

```python
@pytest.fixture
def sample_v2_trade():
    return build_trade_v2(
        symbol="BHP",
        exchange="ASX",
        asset_class="equities",
        timestamp=datetime.utcnow(),
        price=Decimal("45.67"),
        quantity=Decimal("1000"),
        currency="AUD",
        side="BUY",
        vendor_data={"company_id": "123", "qualifiers": "0"}
    )
```

### Sample Binance Message

```python
@pytest.fixture
def sample_binance_trade():
    return {
        "e": "trade",
        "E": 1672531200000,
        "s": "BTCUSDT",
        "t": 12345,
        "p": "16500.00",
        "q": "0.05",
        "T": 1672531199900,
        "m": True
    }
```

---

## Coverage Requirements

### Target Coverage

- **Unit Tests**: > 80% code coverage
- **Integration Tests**: Critical paths covered
- **E2E Tests**: At least 1 happy path per feature

### Running Coverage

```bash
pytest --cov=src/k2 --cov-report=html tests/
open htmlcov/index.html
```

---

## Test Organization

### Directory Structure

```
tests/
├── unit/
│   ├── test_producer.py
│   ├── test_consumer.py
│   ├── test_query_engine.py
│   ├── test_binance_client.py
│   └── test_binance_converter.py
├── integration/
│   ├── test_v2_pipeline.py
│   ├── test_binance_streaming.py
│   └── test_redis_sequence_tracker.py
└── fixtures/
    ├── __init__.py
    ├── sample_trades.py
    └── sample_binance_messages.py
```

---

## CI/CD Integration

### GitHub Actions

```yaml
- name: Run Tests
  run: |
    pytest tests/unit/ -v
    pytest tests/integration/ -v --tb=short

- name: Check Coverage
  run: |
    pytest --cov=src/k2 --cov-report=xml tests/
    if [ $(coverage report | grep TOTAL | awk '{print $4}' | sed 's/%//') -lt 80 ]; then
      echo "Coverage below 80%"
      exit 1
    fi
```

---

## Debugging Failed Tests

### Verbose Output

```bash
pytest tests/unit/test_producer.py::test_build_trade_v2 -vv -s
```

### PDB Debugging

```python
def test_something():
    import pdb; pdb.set_trace()  # Breakpoint
    result = function_under_test()
    assert result == expected
```

### Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)

def test_with_logging():
    logger = logging.getLogger(__name__)
    logger.debug("Debug message")
```

---

## References

- [pytest Documentation](https://docs.pytest.org/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
- [coverage.py](https://coverage.readthedocs.io/)

---

**Last Updated**: 2026-01-12

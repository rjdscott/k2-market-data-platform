# Step 01.5.5: Testing

**Status**: ⬜ Not Started  
**Estimated Time**: 5 hours  
**Actual Time**: -  
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Unit tests + manual integration validation for Binance streaming.

---

## Tasks

### 1. Create tests/unit/test_binance_client.py

```python
import pytest
from unittest.mock import Mock, patch
from k2.ingestion.binance_client import BinanceWebSocketClient

def test_message_parsing():
    msg = {"e": "trade", "s": "BTCUSDT", "t": 12345, "p": "16500.00", "q": "0.05"}
    # Test parsing

def test_side_mapping():
    # m=True → SELL, m=False → BUY
    assert convert_binance_trade_to_v2({"m": True, ...})["side"] == "SELL"
    assert convert_binance_trade_to_v2({"m": False, ...})["side"] == "BUY"

# 10+ tests total
```

### 2. Manual Integration Test

```bash
# Run for 5 minutes
python scripts/binance_stream.py
# Wait 5 minutes
# Verify trades in Kafka, Iceberg, API
```

---

## Validation

```bash
pytest tests/unit/test_binance_client.py -v
# All tests pass

# Manual test
python -c "
from k2.query.engine import QueryEngine
engine = QueryEngine(table_version='v2')
df = engine.query_trades(symbol='BTCUSDT', exchange='BINANCE', limit=100)
print(f'Found {len(df)} BTC trades')
"
```

---

## Commit Message

```
test: add binance client unit tests

- Create test_binance_client.py with 10+ tests
- Test message parsing, v2 conversion, side mapping
- Mock WebSocket connection
- Manual integration test (5 minutes streaming)
- Verify E2E flow: Binance → Kafka → Iceberg → Query

Related: Phase 2 Prep, Step 01.5.5
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

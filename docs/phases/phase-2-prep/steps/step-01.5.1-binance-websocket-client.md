# Step 01.5.1: Binance WebSocket Client

**Status**: ✅ Complete
**Estimated Time**: 6 hours
**Actual Time**: ~3 hours
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Create WebSocket client to connect to Binance and receive live trade messages.

---

## Tasks

### 1. Create binance_client.py

```python
import asyncio
import json
import websockets
from typing import Optional, List, Callable

class BinanceWebSocketClient:
    """Async WebSocket client for Binance trade streams."""

    def __init__(
        self,
        symbols: List[str] = ["BTCUSDT", "ETHUSDT"],
        on_message: Optional[Callable] = None,
        url: str = "wss://stream.binance.com:9443/stream"
    ):
        self.symbols = symbols
        self.on_message = on_message
        self.url = url
        self.ws = None

    async def connect(self):
        streams = [f"{s.lower()}@trade" for s in self.symbols]
        params = {"streams": streams}

        async with websockets.connect(f"{self.url}?streams={'/'.join(streams)}") as ws:
            self.ws = ws
            logger.info("Connected to Binance WebSocket", symbols=self.symbols)

            async for message in ws:
                data = json.loads(message)
                if self.on_message:
                    self.on_message(data)
```

### 2. Add BinanceConfig to config.py

```python
class BinanceConfig(BaseModel):
    enabled: bool = False
    websocket_url: str = "wss://stream.binance.com:9443/stream"
    symbols: List[str] = ["BTCUSDT", "ETHUSDT"]
    reconnect_delay: int = 5
    max_reconnect_attempts: int = 10
```

---

## Validation

```python
client = BinanceWebSocketClient(symbols=["BTCUSDT"])
await client.connect()
# Check logs for "Connected to Binance WebSocket"
# Receive trade messages for 30 seconds
```

---

## Commit Message

```
feat(binance): add websocket client for trade streams

- Create BinanceWebSocketClient with async/await
- Connect to wss://stream.binance.com:9443/stream
- Subscribe to BTC-USDT and ETH-USDT trade streams
- Parse JSON messages
- Add BinanceConfig to config.py

Related: Phase 2 Prep, Step 01.5.1
```

---

## Completion Notes

**Implemented**: 2026-01-13

### What Was Built:
- ✅ `src/k2/ingestion/binance_client.py` - Full async WebSocket client
- ✅ `BinanceWebSocketClient` class with reconnection logic
- ✅ `parse_binance_symbol()` - Dynamic currency extraction
- ✅ `convert_binance_trade_to_v2()` - Complete message conversion
- ✅ `BinanceConfig` in `config.py` with Pydantic validation
- ✅ `scripts/test_binance_stream.py` - Demo/test script

### Key Features Delivered:
- Async/await pattern for efficient streaming
- Automatic reconnection with exponential backoff (5s, 10s, 20s, ...)
- Multi-symbol support (can stream any Binance pairs)
- Dynamic currency extraction (works with USDT, BTC, EUR, etc.)
- Base/quote asset separation in vendor_data
- Comprehensive error handling and structured logging
- Production-ready code with proper types and docstrings

### Testing:
- Test script validates real WebSocket connection
- Tested with BTCUSDT, ETHUSDT (can test more with `--symbols` flag)
- Displays trades in Rich formatted tables

### Enhancements Beyond Original Plan:
- Added base_asset/quote_asset to vendor_data (not in original spec)
- Added configurable reconnect parameters
- Added structured logging with structlog
- Added graceful disconnect method
- Better error handling with specific exception types

**Commit**: `feat(binance): add websocket client for trade streaming` (e998dd1)

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete

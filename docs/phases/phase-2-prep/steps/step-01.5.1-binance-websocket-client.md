# Step 01.5.1: Binance WebSocket Client

**Status**: ⬜ Not Started  
**Estimated Time**: 6 hours  
**Actual Time**: -  
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

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

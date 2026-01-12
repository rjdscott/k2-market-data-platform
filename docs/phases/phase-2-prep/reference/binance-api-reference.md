# Binance API Reference

**Purpose**: Binance WebSocket API documentation for K2 integration
**Last Updated**: 2026-01-12

---

## Overview

Binance provides public WebSocket streams for real-time market data. K2 uses the **Trade Streams** for live cryptocurrency trade data.

---

## WebSocket Endpoints

### Production

**URL**: `wss://stream.binance.com:9443/stream`
**Rate Limit**: 10 connections per IP
**Max Subscriptions**: 1024 streams per connection

### US (Failover)

**URL**: `wss://stream.binance.us:9443/stream`
**Availability**: US users only

---

## Trade Stream Format

### Subscription

Subscribe to trade stream for a symbol:

**Stream Name**: `{symbol}@trade`

**Examples**:
- `btcusdt@trade` - Bitcoin to USDT trades
- `ethusdt@trade` - Ethereum to USDT trades

### Message Format

```json
{
  "e": "trade",              // Event type
  "E": 1672531200000,        // Event time (ms since epoch)
  "s": "BTCUSDT",            // Symbol
  "t": 12345,                // Trade ID
  "p": "16500.00",           // Price
  "q": "0.05",               // Quantity
  "b": 88,                   // Buyer order ID
  "a": 50,                   // Seller order ID
  "T": 1672531199900,        // Trade time (ms since epoch)
  "m": true,                 // Is the buyer the market maker?
  "M": true                  // Ignore
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| e | string | Event type, always "trade" |
| E | long | Event time (milliseconds) |
| s | string | Trading pair symbol |
| t | long | Trade ID (unique per symbol) |
| p | string | Trade price |
| q | string | Trade quantity |
| b | long | Buyer order ID |
| a | long | Seller order ID |
| T | long | Trade execution time (milliseconds) |
| m | boolean | Is buyer the market maker? |
| M | boolean | Ignore (deprecated) |

---

## Side Determination

Binance uses "is buyer maker" field to indicate trade side:

- `m = true`: Buyer is market maker → **Aggressor SOLD** → Side = **SELL**
- `m = false`: Seller is market maker → **Aggressor BOUGHT** → Side = **BUY**

**K2 Mapping**:
```python
side = "SELL" if msg["m"] else "BUY"
```

---

## Mapping to v2 Schema

### Binance → v2 Trade Mapping

| Binance Field | v2 Field | Transformation |
|---------------|----------|----------------|
| - | message_id | Generate UUID |
| t | trade_id | `f"BINANCE-{t}"` |
| s | symbol | Direct |
| - | exchange | Constant: "BINANCE" |
| - | asset_class | Constant: "crypto" |
| T | timestamp | `T * 1000` (ms → μs) |
| p | price | `Decimal(p)` |
| q | quantity | `Decimal(q)` |
| - | currency | "USDT" (from symbol) |
| m | side | `"SELL" if m else "BUY"` |
| - | trade_conditions | Empty array |
| - | source_sequence | null |
| - | ingestion_timestamp | Current time (μs) |
| - | platform_sequence | null |
| e, m | vendor_data | `{"event_type": e, "is_buyer_maker": str(m)}` |

### Example Conversion

**Binance Message**:
```json
{
  "e": "trade",
  "E": 1672531200000,
  "s": "BTCUSDT",
  "t": 12345,
  "p": "16500.00",
  "q": "0.05",
  "T": 1672531199900,
  "m": true
}
```

**v2 Trade**:
```json
{
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "trade_id": "BINANCE-12345",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "timestamp": 1672531199900000,
  "price": 16500.00,
  "quantity": 0.05,
  "currency": "USDT",
  "side": "SELL",
  "trade_conditions": [],
  "source_sequence": null,
  "ingestion_timestamp": 1672531200123456,
  "platform_sequence": null,
  "vendor_data": {
    "event_type": "trade",
    "is_buyer_maker": "true"
  }
}
```

---

## Connection Management

### Heartbeat

Binance sends periodic `ping` frames. Client must respond with `pong` frames.

**websockets library handles this automatically**.

### Disconnect Reasons

| Reason | Code | Action |
|--------|------|--------|
| Normal closure | 1000 | Reconnect if needed |
| Rate limit exceeded | 1003 | Exponential backoff |
| Invalid stream | 1007 | Check stream name |
| Server restart | 1006 | Reconnect immediately |

---

## Rate Limits

### Connection Limits

- **10 connections per IP**
- **5 messages per second per connection**
- **1024 streams per connection**

### Handling Rate Limits

```python
if error_code == 1003:  # Rate limit
    await asyncio.sleep(60)  # Wait 1 minute
    await reconnect()
```

---

## Error Handling

### Network Errors

```python
try:
    async with websockets.connect(url) as ws:
        ...
except websockets.exceptions.ConnectionClosed as e:
    logger.error("Connection closed", code=e.code, reason=e.reason)
    await reconnect_with_backoff()
except Exception as e:
    logger.error("WebSocket error", error=str(e))
```

### Message Validation

```python
def validate_trade_message(msg: dict) -> bool:
    required_fields = ["e", "s", "t", "p", "q", "T", "m"]
    return all(field in msg for field in required_fields)
```

---

## Production Best Practices

### 1. Exponential Backoff

```python
async def reconnect_with_backoff(self):
    delay = 5
    for attempt in range(10):
        await asyncio.sleep(delay)
        try:
            await self.connect()
            return
        except Exception:
            delay = min(delay * 2, 60)  # Cap at 60s
```

### 2. Health Checks

```python
async def health_check_loop(self):
    while True:
        await asyncio.sleep(30)
        if not self._received_message_recently():
            await self.reconnect()
```

### 3. Metrics

Track:
- `binance_connection_status` (1=connected, 0=disconnected)
- `binance_messages_received_total`
- `binance_reconnects_total`
- `binance_errors_total`

---

## Testing

### Mock WebSocket Server

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_binance_client():
    mock_ws = AsyncMock()
    mock_ws.__aiter__.return_value = [
        '{"e":"trade","s":"BTCUSDT","t":12345,"p":"16500.00","q":"0.05","T":1672531199900,"m":true}'
    ]

    client = BinanceWebSocketClient()
    # Test with mock
```

---

## References

- [Binance WebSocket Streams](https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams)
- [Binance API Status](https://www.binance.com/en/support/announcement)
- [websockets Python Library](https://websockets.readthedocs.io/)

---

**Last Updated**: 2026-01-12

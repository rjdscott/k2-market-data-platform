# Step 01.5.2: Message Conversion

**Status**: ⬜ Not Started
**Estimated Time**: 4 hours
**Actual Time**: -
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Convert Binance trade messages to v2 Trade format based on ACTUAL Binance WebSocket payload structure.

---

## Real Binance Payload Structure

### WebSocket Trade Stream Payload:
```json
{
  "e": "trade",           // Event type
  "E": 1672515782136,     // Event time (milliseconds)
  "s": "BTCUSDT",         // Symbol (concatenated, no separator)
  "t": 12345,             // Trade ID
  "p": "90878.12",        // Price (string)
  "q": "0.00006",         // Quantity in base currency (string)
  "T": 1672515782136,     // Trade time (milliseconds)
  "m": true,              // Is buyer maker? (true = seller aggressor)
  "M": true               // Ignore (can be best price match)
}
```

### Key Fields:
- **s (symbol)**: Concatenated format with NO separator
  - Examples: BTCUSDT, ETHBTC, BNBEUR, SOLUSDC
  - Base asset + Quote asset (e.g., BTC + USDT)
- **m (isBuyerMaker)**: Determines aggressor side
  - `false` → Buyer was taker → **BUY**
  - `true` → Seller was taker → **SELL**
- **No currency field**: Must extract quote currency from symbol
- **No side field**: Must infer from isBuyerMaker

---

## Tasks

### 1. Add Symbol Parser

```python
def parse_binance_symbol(symbol: str) -> tuple[str, str, str]:
    """
    Parse Binance symbol into base asset, quote asset, and quote currency.

    Args:
        symbol: Binance symbol (e.g., BTCUSDT, ETHBTC, BNBEUR)

    Returns:
        (base_asset, quote_asset, quote_currency)

    Examples:
        >>> parse_binance_symbol("BTCUSDT")
        ("BTC", "USDT", "USDT")
        >>> parse_binance_symbol("ETHBTC")
        ("ETH", "BTC", "BTC")
        >>> parse_binance_symbol("BNBEUR")
        ("BNB", "EUR", "EUR")
    """
    # Common quote currencies (order matters - check longest first)
    quote_currencies = [
        "USDT", "USDC", "BUSD", "TUSD", "USDP",  # Stablecoins
        "BTC", "ETH", "BNB",                      # Crypto
        "EUR", "GBP", "AUD", "USD",               # Fiat
        "TRY", "ZAR", "UAH", "NGN",               # Other fiat
    ]

    for quote in quote_currencies:
        if symbol.endswith(quote):
            base_asset = symbol[:-len(quote)]
            return (base_asset, quote, quote)

    # Fallback: assume last 4 chars are quote (USDT pattern)
    return (symbol[:-4], symbol[-4:], symbol[-4:])
```

### 2. Add Converter to binance_client.py

```python
import time
import uuid
from decimal import Decimal


def convert_binance_trade_to_v2(msg: dict) -> dict:
    """
    Convert Binance WebSocket trade to v2 Trade schema.

    Args:
        msg: Binance trade payload from WebSocket

    Returns:
        v2 Trade record conforming to TradeV2 Avro schema

    Raises:
        ValueError: If required fields missing or invalid
    """
    # Validate required fields
    _validate_binance_message(msg)

    # Parse symbol to extract base/quote assets and currency
    symbol = msg["s"]
    base_asset, quote_asset, currency = parse_binance_symbol(symbol)

    # Determine trade side from isBuyerMaker
    # m=true → buyer was maker (passive), seller was taker (aggressive) → SELL
    # m=false → buyer was taker (aggressive) → BUY
    side = "SELL" if msg["m"] else "BUY"

    # Convert timestamps (Binance uses milliseconds, v2 uses microseconds)
    exchange_timestamp_micros = msg["T"] * 1000  # Trade time (T)
    event_timestamp_micros = msg["E"] * 1000      # Event time (E)
    ingestion_timestamp_micros = int(time.time() * 1_000_000)

    # Build v2 trade record
    return {
        "message_id": str(uuid.uuid4()),
        "trade_id": f"BINANCE-{msg['t']}",
        "symbol": symbol,
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "timestamp": exchange_timestamp_micros,  # Use trade time (T) not event time (E)
        "price": Decimal(msg["p"]),
        "quantity": Decimal(msg["q"]),
        "currency": currency,  # EXTRACTED from symbol, not hardcoded!
        "side": side,
        "trade_conditions": [],
        "source_sequence": None,  # Binance doesn't provide sequence numbers
        "ingestion_timestamp": ingestion_timestamp_micros,
        "platform_sequence": None,
        "vendor_data": {
            "is_buyer_maker": str(msg["m"]),
            "event_type": msg["e"],
            "event_time": str(msg["E"]),
            "trade_time": str(msg["T"]),
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "is_best_match": str(msg.get("M", "")),  # M field (optional)
        },
    }
```

### 3. Add Validation

```python
def _validate_binance_message(msg: dict) -> None:
    """
    Validate Binance trade message has required fields.

    Args:
        msg: Binance trade payload

    Raises:
        ValueError: If required fields missing
    """
    required_fields = ["e", "E", "s", "t", "p", "q", "T", "m"]
    missing = [field for field in required_fields if field not in msg]

    if missing:
        raise ValueError(f"Missing required fields in Binance trade: {missing}")

    if msg["e"] != "trade":
        raise ValueError(f"Invalid event type: {msg['e']}, expected 'trade'")
```

---

## Validation

### Test with Multiple Currency Pairs

```python
# Test 1: BTCUSDT (quote = USDT)
binance_msg_usdt = {
    "e": "trade",
    "E": 1672515782136,
    "s": "BTCUSDT",
    "t": 12345,
    "p": "16500.00",
    "q": "0.05",
    "T": 1672515782136,
    "m": True,  # Seller aggressor
    "M": True
}

v2_usdt = convert_binance_trade_to_v2(binance_msg_usdt)
assert v2_usdt["symbol"] == "BTCUSDT"
assert v2_usdt["currency"] == "USDT"  # ✅ Extracted correctly
assert v2_usdt["side"] == "SELL"  # ✅ m=true → SELL
assert v2_usdt["vendor_data"]["base_asset"] == "BTC"
assert v2_usdt["vendor_data"]["quote_asset"] == "USDT"
print("✅ BTCUSDT conversion works")

# Test 2: ETHBTC (quote = BTC, not USDT!)
binance_msg_btc = {
    "e": "trade",
    "E": 1672515782136,
    "s": "ETHBTC",
    "t": 67890,
    "p": "0.07500",
    "q": "1.50",
    "T": 1672515782136,
    "m": False,  # Buyer aggressor
    "M": True
}

v2_btc = convert_binance_trade_to_v2(binance_msg_btc)
assert v2_btc["symbol"] == "ETHBTC"
assert v2_btc["currency"] == "BTC"  # ✅ Extracted correctly, not hardcoded USDT!
assert v2_btc["side"] == "BUY"  # ✅ m=false → BUY
assert v2_btc["vendor_data"]["base_asset"] == "ETH"
assert v2_btc["vendor_data"]["quote_asset"] == "BTC"
print("✅ ETHBTC conversion works")

# Test 3: BNBEUR (quote = EUR)
binance_msg_eur = {
    "e": "trade",
    "E": 1672515782136,
    "s": "BNBEUR",
    "t": 11111,
    "p": "250.50",
    "q": "10.00",
    "T": 1672515782136,
    "m": False,
    "M": True
}

v2_eur = convert_binance_trade_to_v2(binance_msg_eur)
assert v2_eur["currency"] == "EUR"  # ✅ Works with fiat too
print("✅ BNBEUR conversion works")

print("\n✅ All currency extraction tests pass!")
```

### Test Side Determination

```python
# Test isBuyerMaker logic
test_cases = [
    (True, "SELL"),   # Buyer is maker → Seller is taker → SELL
    (False, "BUY"),   # Buyer is taker → BUY
]

for is_buyer_maker, expected_side in test_cases:
    msg = {
        "e": "trade", "E": 123, "s": "BTCUSDT", "t": 1,
        "p": "100", "q": "1", "T": 123, "m": is_buyer_maker, "M": True
    }
    v2 = convert_binance_trade_to_v2(msg)
    assert v2["side"] == expected_side, f"Failed: m={is_buyer_maker} should be {expected_side}"

print("✅ Side determination logic correct")
```

---

## What Changed from Original?

| Field | Original (WRONG) | Fixed (CORRECT) |
|-------|-----------------|-----------------|
| **currency** | Hardcoded `"USDT"` | `parse_binance_symbol(msg["s"])` - extracted from symbol |
| **base_asset** | Not captured | Added to `vendor_data["base_asset"]` |
| **quote_asset** | Not captured | Added to `vendor_data["quote_asset"]` |
| **timestamp** | `msg["T"] * 1000` | Same (correct - converts ms to micros) |
| **side** | `"SELL" if msg["m"] else "BUY"` | Same (correct - aggressor side) |
| **trade_id** | `f"BINANCE-{msg['t']}"` | Same (correct) |

---

## Commit Message

```
fix(binance): fix currency extraction and add base/quote assets

- Fix currency bug: extract from symbol instead of hardcoding "USDT"
- Add parse_binance_symbol() to extract base/quote assets
- Add base_asset and quote_asset to vendor_data
- Test with BTCUSDT (USDT), ETHBTC (BTC), BNBEUR (EUR)
- Verify side determination logic (isBuyerMaker → aggressor side)

Fixes: Currency hardcoding bug identified in code review
Related: Phase 2 Prep, Step 01.5.2, Decision #008, Decision #009
```

---

**Last Updated**: 2026-01-13
**Status**: ⬜ Not Started (Ready to implement with fixes)

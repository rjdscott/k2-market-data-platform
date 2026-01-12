# Step 01.5.2: Message Conversion

**Status**: ⬜ Not Started  
**Estimated Time**: 4 hours  
**Actual Time**: -  
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Convert Binance trade messages to v2 Trade format.

---

## Tasks

### 1. Add Converter to binance_client.py

```python
def convert_binance_trade_to_v2(msg: dict) -> dict:
    """Convert Binance trade to v2 Trade schema."""
    return {
        "message_id": str(uuid.uuid4()),
        "trade_id": f"BINANCE-{msg['t']}",
        "symbol": msg["s"],
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "timestamp": msg["T"] * 1000,  # ms → micros
        "price": Decimal(msg["p"]),
        "quantity": Decimal(msg["q"]),
        "currency": "USDT",
        "side": "SELL" if msg["m"] else "BUY",  # m=buyer maker → aggressor sold
        "trade_conditions": [],
        "source_sequence": None,
        "ingestion_timestamp": int(time.time() * 1_000_000),
        "platform_sequence": None,
        "vendor_data": {
            "is_buyer_maker": str(msg["m"]),
            "event_type": msg["e"],
        },
    }
```

### 2. Add Validation

```python
def _validate_binance_message(msg: dict) -> bool:
    required = ["e", "s", "t", "p", "q", "T", "m"]
    return all(field in msg for field in required)
```

---

## Validation

```python
binance_msg = {
    "e": "trade", "s": "BTCUSDT", "t": 12345,
    "p": "16500.00", "q": "0.05", "T": 1672531199900, "m": True
}

v2 = convert_binance_trade_to_v2(binance_msg)
assert v2["symbol"] == "BTCUSDT"
assert v2["exchange"] == "BINANCE"
assert v2["side"] == "SELL"  # buyer maker → SELL
assert "vendor_data" in v2
print("✅ Binance → v2 conversion works")
```

---

## Commit Message

```
feat(binance): add message converter to v2 schema

- Add convert_binance_trade_to_v2() converter
- Map Binance fields to v2 schema
- Handle side mapping (buyer maker → SELL)
- Add vendor_data for Binance-specific fields
- Add message validation

Related: Phase 2 Prep, Step 01.5.2
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

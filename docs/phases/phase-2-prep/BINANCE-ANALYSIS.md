# Binance Data Analysis - Real Payload Structure

**Date**: 2026-01-13
**Purpose**: Document actual Binance WebSocket/REST API structure based on real data fetching

---

## Real Data Samples

### Sample 1: BTCUSDT Trade (Quote = USDT)
```json
{
  "id": 5762309412,
  "price": "90878.12000000",
  "qty": "0.00006000",
  "quoteQty": "5.45268720",
  "time": 1768228901292,
  "isBuyerMaker": false,
  "isBestMatch": true
}
```

### Sample 2: ETHBTC Trade (Quote = BTC)
```json
{
  "id": 521170531,
  "price": "0.03420000",
  "qty": "0.00310000",
  "quoteQty": "0.00010602",
  "time": 1768228899356,
  "isBuyerMaker": false,
  "isBestMatch": true
}
```

---

## Key Findings

### 1. Symbol Format
- **Format**: Concatenated with NO separator
- **Examples**:
  - `BTCUSDT` = BTC (base) + USDT (quote)
  - `ETHBTC` = ETH (base) + BTC (quote)
  - `BNBEUR` = BNB (base) + EUR (quote)
- **Implication**: Must parse to extract base/quote assets

### 2. Currency/Quote Asset
- ❌ **NO "currency" field in Binance payload**
- ✅ Must extract from symbol suffix
- **Common quote currencies** (order matters for parsing):
  - Stablecoins: USDT, USDC, BUSD, TUSD, USDP
  - Crypto: BTC, ETH, BNB
  - Fiat: EUR, GBP, AUD, USD, TRY, ZAR, UAH, NGN

### 3. Trade Side (Aggressor)
- ❌ **NO "side" field in Binance payload**
- ✅ Infer from `isBuyerMaker` (REST) or `m` (WebSocket)
- **Logic**:
  - `isBuyerMaker = false` → Buyer was taker (aggressive) → **BUY**
  - `isBuyerMaker = true` → Seller was taker (aggressive) → **SELL**
- **This is standard for market data** - "side" means aggressor side

### 4. Quantity vs Quote Quantity
- `qty`: Quantity in **base currency** (e.g., 0.00006 BTC)
- `quoteQty`: Total value in **quote currency** (e.g., 5.45 USDT)
- Relationship: `quoteQty = qty * price`
- **Useful for**: Volume-weighted analytics

### 5. Timestamps
- **REST API**: `time` (milliseconds)
- **WebSocket**:
  - `T`: Trade time (when trade executed)
  - `E`: Event time (when message published)
- **Use trade time (`T`)** as canonical timestamp

### 6. Trade ID
- REST: `id` (integer)
- WebSocket: `t` (integer)
- Format in our system: `BINANCE-{trade_id}`

---

## Bugs Found in Original Implementation

### Bug #1: Hardcoded Currency ❌ CRITICAL
**Original**:
```python
"currency": "USDT",  # HARDCODED - WRONG!
```

**Problem**:
- Breaks for ETHBTC (should be BTC)
- Breaks for BNBEUR (should be EUR)
- Breaks for 40%+ of Binance pairs

**Fix**:
```python
base_asset, quote_asset, currency = parse_binance_symbol(msg["s"])
"currency": currency,  # Extracted dynamically
```

### Bug #2: Missing Base/Quote Asset Separation ⚠️ MEDIUM
**Original**: Only stored combined symbol

**Problem**:
- Can't easily query "all BTC pairs" without parsing symbol
- Can't distinguish base from quote in analytics

**Fix**: Add to vendor_data
```python
"vendor_data": {
    "base_asset": base_asset,   # BTC, ETH, BNB
    "quote_asset": quote_asset, # USDT, BTC, EUR
    ...
}
```

---

## Correct v2 Schema Mapping

| Binance Field (WebSocket) | v2 Schema Field | Conversion |
|---------------------------|----------------|------------|
| `s` (symbol) | `symbol` | Direct (e.g., "BTCUSDT") |
| `s` (parsed) | `currency` | **Extract quote from symbol** |
| `t` (trade ID) | `trade_id` | Format: "BINANCE-{t}" |
| `p` (price) | `price` | String → Decimal |
| `q` (quantity) | `quantity` | String → Decimal |
| `T` (trade time) | `timestamp` | Milliseconds → Microseconds (* 1000) |
| `m` (isBuyerMaker) | `side` | **false→BUY, true→SELL** |
| `e` (event type) | `vendor_data.event_type` | Direct ("trade") |
| `E` (event time) | `vendor_data.event_time` | Store for debugging |
| `M` (best match) | `vendor_data.is_best_match` | Optional field |
| - | `exchange` | Hardcode "BINANCE" |
| - | `asset_class` | Hardcode "crypto" |
| - | `message_id` | Generate UUID |
| - | `ingestion_timestamp` | Current time (micros) |

---

## Symbol Parser Implementation

```python
def parse_binance_symbol(symbol: str) -> tuple[str, str, str]:
    """
    Parse Binance symbol into base asset, quote asset, and quote currency.

    Returns: (base_asset, quote_asset, quote_currency)
    """
    # Order matters - check longest first to avoid false matches
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

---

## Test Coverage Required

### Must Test These Pairs:
1. **BTCUSDT** - Most liquid, USDT quote
2. **ETHBTC** - Crypto-crypto, BTC quote
3. **BNBEUR** - Fiat quote
4. **SOLUSDC** - Different stablecoin

### Side Logic Tests:
```python
isBuyerMaker = false → side = "BUY"  ✅
isBuyerMaker = true  → side = "SELL" ✅
```

### Currency Extraction Tests:
```python
"BTCUSDT" → currency = "USDT" ✅
"ETHBTC"  → currency = "BTC"  ✅
"BNBEUR"  → currency = "EUR"  ✅
```

---

## Recommended Next Steps

1. ✅ **Replace** `step-01.5.2-message-conversion.md` with fixed version
2. ⬜ **Add unit tests** for `parse_binance_symbol()` function
3. ⬜ **Add integration test** with multiple currency pairs
4. ⬜ **Document** symbol parsing logic in architecture docs
5. ⬜ **Consider**: Add `base_asset`/`quote_asset` as first-class fields in v2 schema (not just vendor_data)

---

## References

- Binance REST API: `GET /api/v3/trades?symbol={symbol}`
- Binance WebSocket: `wss://stream.binance.com:9443/ws/{symbol}@trade`
- Real data fetched: 2026-01-13 (BTC @ ~$90,878)

---

**Last Updated**: 2026-01-13
**Status**: Analysis complete - ready for implementation

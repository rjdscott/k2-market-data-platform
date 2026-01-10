# Data Source Assumptions & Adaptations

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Data Engineering
**Status**: Implementation Plan
**Scope**: Exchange feed characteristics, fallback strategies, vendor variations

---

## Overview

The platform is designed for market data from financial exchanges. However, exchanges vary in their data formats, quality guarantees, and delivery mechanisms. This document defines our assumptions about data sources and fallback strategies when assumptions are violated.

**Design Philosophy**: Design for the common case, adapt for edge cases. Most exchanges follow industry standards, but vendor-specific quirks exist.

---

## Exchange Feed Assumptions

### Assumption 1: Exchange Provides Sequence Numbers

**Expected**: All market data messages include monotonically increasing sequence numbers

**Reality**: Most major exchanges provide this (ASX, NYSE, NASDAQ, CME)

**Example**:
```json
{
  "symbol": "BHP",
  "price": 150.23,
  "exchange_sequence_number": 184847291,  ← Monotonic per symbol
  "timestamp": "2026-01-09T10:00:00.127483Z"
}
```

**Vendor Variations**:

| Exchange | Sequence Number | Notes |
|----------|----------------|-------|
| **ASX (ITCH)** | ✅ Per symbol | Sequence per symbol, resets daily |
| **NYSE (TAQ)** | ✅ Per channel | Sequence per multicast channel |
| **NASDAQ (TotalView)** | ✅ Per symbol | Sequence per symbol |
| **LSE (Millennium)** | ✅ Per symbol | Called "message sequence" |
| **CME (MDP 3.0)** | ✅ Per channel | Sequence per market data channel |
| **ICE (Consolidated Feed)** | ❌ None | No sequence numbers! |

**Fallback Strategy** (No Sequence Numbers):

```python
def generate_synthetic_sequence(tick: dict) -> int:
    """
    Generate synthetic sequence number when exchange doesn't provide.

    Strategy: Hash(timestamp + symbol + price + volume) mod 2^63
    """
    import hashlib

    content = f"{tick['timestamp']}{tick['symbol']}{tick['price']}{tick['volume']}"
    hash_value = hashlib.sha256(content.encode()).hexdigest()

    # Convert to int (use first 16 hex chars for 64-bit int)
    sequence = int(hash_value[:16], 16)

    return sequence

# Usage
if 'exchange_sequence_number' not in tick:
    tick['exchange_sequence_number'] = generate_synthetic_sequence(tick)
```

**Limitation**: Synthetic sequences don't detect gaps (no exchange guarantee)

**Mitigation**: Use timestamp ordering + duplicate detection instead of sequence tracking

---

### Assumption 2: Exchange Provides Exchange Timestamps

**Expected**: Messages include exchange-assigned timestamps (microsecond precision)

**Reality**: Most exchanges provide exchange timestamps, some only provide network timestamps

**Example**:
```json
{
  "symbol": "BHP",
  "exchange_timestamp": "2026-01-09T10:00:00.127483Z",  ← Exchange-assigned
  "ingestion_timestamp": "2026-01-09T10:00:00.130512Z"  ← Platform-assigned
}
```

**Vendor Variations**:

| Exchange | Timestamp Precision | Timezone |
|----------|-------------------|----------|
| **ASX** | Microsecond | AEST (UTC+10) |
| **NYSE** | Nanosecond | EST (UTC-5) |
| **NASDAQ** | Nanosecond | EST (UTC-5) |
| **LSE** | Nanosecond | GMT (UTC+0) |
| **CME** | Nanosecond | CST (UTC-6) |
| **ICE** | Millisecond | UTC |

**Fallback Strategy** (No Exchange Timestamp):

```python
from datetime import datetime, timezone

def use_ingestion_timestamp(tick: dict) -> dict:
    """
    Use platform ingestion timestamp when exchange timestamp missing.

    WARNING: Reduces backtesting accuracy (network delay not captured).
    """
    if 'exchange_timestamp' not in tick or tick['exchange_timestamp'] is None:
        tick['exchange_timestamp'] = datetime.now(timezone.utc).isoformat()
        tick['timestamp_source'] = 'ingestion'  # Flag for downstream
        log.warning(f"Using ingestion timestamp for {tick['symbol']} (exchange timestamp missing)")
    else:
        tick['timestamp_source'] = 'exchange'

    return tick
```

**Impact**: Ingestion timestamps add network delay (10-50ms), less accurate for backtesting

**Mitigation**: Prefer exchanges with exchange timestamps, use ingestion timestamps as last resort

---

### Assumption 3: Data Arrives via UDP Multicast

**Expected**: Market data delivered via UDP multicast for low latency

**Reality**: Some exchanges use UDP, others use HTTP polling, WebSockets, or FIX protocol

**Vendor Variations**:

| Exchange | Delivery Method | Latency |
|----------|-----------------|---------|
| **ASX (ITCH)** | UDP multicast | < 1ms (on-exchange) |
| **NYSE (TAQ)** | UDP multicast | < 5ms (co-located) |
| **NASDAQ** | UDP multicast | < 5ms (co-located) |
| **IEX** | UDP multicast | < 10ms |
| **Alpha Vantage** | HTTP REST API | 100-500ms (polling) |
| **Polygon.io** | WebSocket | 10-50ms |
| **Interactive Brokers** | FIX protocol | 50-200ms |

**Adaptation Strategy**:

```python
class MarketDataAdapter:
    """
    Unified adapter for different delivery methods.

    Normalizes to common MarketTick format.
    """

    def __init__(self, source: str):
        self.source = source
        self.adapter = self._get_adapter(source)

    def _get_adapter(self, source: str):
        """Factory for source-specific adapters."""
        if source == 'asx-itch':
            return UDPMulticastAdapter(host='239.1.1.1', port=10001)
        elif source == 'alphavantage':
            return HTTPPollingAdapter(api_key='...', interval_sec=1)
        elif source == 'polygon':
            return WebSocketAdapter(api_key='...', symbols=['BHP', 'CBA'])
        else:
            raise ValueError(f"Unsupported source: {source}")

    def stream(self):
        """Stream market data (unified interface)."""
        for raw_message in self.adapter.receive():
            # Normalize to common format
            tick = self._normalize(raw_message)
            yield tick

    def _normalize(self, raw: dict) -> dict:
        """Normalize vendor-specific format to common schema."""
        if self.source == 'asx-itch':
            return {
                'symbol': raw['stock_code'],
                'price': raw['price'] / 10000,  # ASX uses fixed-point
                'exchange_sequence_number': raw['seq_num'],
                'exchange_timestamp': raw['timestamp'],
            }
        elif self.source == 'alphavantage':
            return {
                'symbol': raw['01. symbol'],
                'price': float(raw['05. price']),
                'exchange_sequence_number': generate_synthetic_sequence(raw),
                'exchange_timestamp': raw['06. Latest trading day'],
            }
        # ... other vendors
```

---

## Data Format Assumptions

### Assumption 4: Prices Are Positive Floating-Point Numbers

**Expected**: Stock prices > 0

**Reality**: True for equities, false for some derivatives and synthetic instruments

**Exceptions**:
- **Futures spreads**: Can be negative (e.g., calendar spreads)
- **Implied volatility**: Can be negative (rare market dislocations)
- **Synthetic instruments**: Custom calculations may yield negative values

**Validation Rules**:

```python
def validate_price(tick: dict) -> ValidationResult:
    """
    Validate price based on instrument type.

    Rules:
    - Equities: price > 0 (always)
    - Futures: price can be negative (spreads)
    - Options: price >= 0 (cannot be negative)
    """
    instrument_type = get_instrument_type(tick['symbol'])

    if instrument_type == 'equity':
        if tick['price'] <= 0:
            return ValidationResult(valid=False, error="Equity price must be positive")

    elif instrument_type == 'futures':
        # Futures spreads can be negative
        pass  # Allow any value

    elif instrument_type == 'option':
        if tick['price'] < 0:
            return ValidationResult(valid=False, error="Option price cannot be negative")

    return ValidationResult(valid=True)
```

---

### Assumption 5: Volume Is Non-Negative Integer

**Expected**: Trade volume >= 0

**Reality**: True for all exchanges

**Edge Cases**:
- **Zero volume quotes**: Bid/ask updates with no trade
- **Odd lots**: Volume < 100 shares (non-standard lots)
- **Block trades**: Volume > 10,000 shares (reported separately)

**Validation**:

```python
def validate_volume(tick: dict) -> ValidationResult:
    """Validate volume is non-negative."""
    if tick['volume'] < 0:
        return ValidationResult(valid=False, error="Volume cannot be negative")

    if tick['volume'] == 0 and tick['type'] == 'trade':
        # Zero-volume trades are suspicious
        return ValidationResult(
            valid=True,
            warning="Zero-volume trade (likely quote update, not trade)"
        )

    return ValidationResult(valid=True)
```

---

### Assumption 6: Symbols Are Unique Identifiers

**Expected**: Symbol uniquely identifies instrument

**Reality**: Symbols are exchange-specific and may collide across exchanges

**Example**:
- `BHP` on ASX → BHP Group Limited (Australian company)
- `BHP` on NYSE → BHP Group Limited ADR (American Depository Receipt)
- These are different instruments!

**Solution**: Use compound key `(exchange, symbol)`

```python
@dataclass
class MarketTick:
    """Market tick with compound key."""
    exchange: str      # e.g., 'ASX', 'NYSE'
    symbol: str        # e.g., 'BHP', 'AAPL'
    price: float
    volume: int

    @property
    def unique_id(self) -> str:
        """Unique identifier across all exchanges."""
        return f"{self.exchange}.{self.symbol}"

# Usage
tick = MarketTick(exchange='ASX', symbol='BHP', price=150.23, volume=100)
assert tick.unique_id == 'ASX.BHP'
```

---

## Exchange-Specific Quirks

### ASX (Australian Securities Exchange)

**Format**: ITCH protocol (binary)

**Quirks**:
- Fixed-point prices (divide by 10,000 to get decimal)
- Sequence numbers reset daily at market open
- Trading halt messages (TYPE='H') require special handling

```python
def parse_asx_itch(raw_bytes: bytes) -> dict:
    """Parse ASX ITCH binary message."""
    import struct

    message_type = chr(raw_bytes[0])

    if message_type == 'P':  # Trade message
        # Fixed-point price (4 decimal places)
        price_raw = struct.unpack('>I', raw_bytes[10:14])[0]
        price = price_raw / 10000.0

        return {
            'type': 'trade',
            'symbol': raw_bytes[1:9].decode('ascii').strip(),
            'price': price,
            'volume': struct.unpack('>I', raw_bytes[14:18])[0],
            'exchange_sequence_number': struct.unpack('>Q', raw_bytes[18:26])[0]
        }
    elif message_type == 'H':  # Trading halt
        return {
            'type': 'halt',
            'symbol': raw_bytes[1:9].decode('ascii').strip(),
            'halt_reason': raw_bytes[10:].decode('ascii').strip()
        }
    # ... other message types
```

---

### NYSE (New York Stock Exchange)

**Format**: TAQ (Trade and Quote)

**Quirks**:
- Nanosecond timestamps (very high precision)
- Consolidated tape (includes multiple exchanges)
- Trade conditions (e.g., 'B' = bunched sold trade)

```python
def parse_nyse_taq(raw: dict) -> dict:
    """Parse NYSE TAQ message."""
    # Nanosecond timestamp
    timestamp_nanos = raw['timestamp_nanos']
    timestamp = datetime.fromtimestamp(timestamp_nanos / 1e9, tz=timezone.utc)

    # Handle trade conditions
    conditions = raw.get('conditions', '')
    is_regular_trade = 'B' not in conditions and 'Z' not in conditions

    return {
        'symbol': raw['symbol'],
        'price': raw['price'],
        'volume': raw['volume'],
        'timestamp': timestamp.isoformat(),
        'is_regular_trade': is_regular_trade,
        'conditions': conditions
    }
```

---

### Alpha Vantage (HTTP API Vendor)

**Format**: JSON over HTTP REST

**Quirks**:
- Rate-limited (5 requests/minute free tier)
- No real-time streaming (must poll)
- No sequence numbers (synthetic required)

```python
import time
import requests

class AlphaVantageAdapter:
    """HTTP polling adapter for Alpha Vantage."""

    def __init__(self, api_key: str, symbols: list[str], poll_interval_sec: int = 12):
        self.api_key = api_key
        self.symbols = symbols
        self.poll_interval_sec = poll_interval_sec  # 5 req/min = 12 sec interval

    def stream(self):
        """Poll Alpha Vantage API at regular intervals."""
        while True:
            for symbol in self.symbols:
                url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={self.api_key}"
                response = requests.get(url)

                if response.status_code == 200:
                    data = response.json()['Global Quote']

                    tick = {
                        'symbol': symbol,
                        'price': float(data['05. price']),
                        'volume': int(data['06. volume']),
                        'timestamp': data['07. latest trading day'],
                        'exchange_sequence_number': generate_synthetic_sequence(data),
                        'source': 'alphavantage'
                    }

                    yield tick

            # Wait before next poll (rate limit)
            time.sleep(self.poll_interval_sec)
```

---

## Data Quality Assumptions

### Assumption 7: Timestamps Within Market Hours

**Expected**: Ticks only arrive during trading session

**Reality**: After-hours trading, pre-market activity, corporate actions

**Validation**:

```python
def is_market_hours(exchange: str, timestamp: datetime) -> bool:
    """Check if timestamp is within regular trading hours."""
    market_hours = {
        'ASX': (time(10, 0), time(16, 0)),  # 10 AM - 4 PM AEST
        'NYSE': (time(9, 30), time(16, 0)),  # 9:30 AM - 4 PM EST
        'NASDAQ': (time(9, 30), time(16, 0)),
        'LSE': (time(8, 0), time(16, 30)),  # 8 AM - 4:30 PM GMT
    }

    if exchange not in market_hours:
        # Unknown exchange, assume valid
        return True

    start, end = market_hours[exchange]
    tick_time = timestamp.time()

    return start <= tick_time <= end

# Usage
tick_time = datetime(2026, 1, 9, 21, 30)  # 9:30 PM
is_regular_hours = is_market_hours('ASX', tick_time)  # False (after hours)
```

**Handling After-Hours**:
- Flag as `trading_session='after_hours'`
- Accept data but mark for downstream filtering
- Do not reject (some strategies trade after hours)

---

### Assumption 8: Price Within Circuit Breaker Limits

**Expected**: Price doesn't move > 10% in single tick

**Reality**: Flash crashes, circuit breaker halts, erroneous ticks

**Validation**:

```python
def check_circuit_breaker(symbol: str, new_price: float, last_price: float) -> bool:
    """
    Check if price movement triggers circuit breaker.

    ASX circuit breakers:
    - Intraday: ±10% from previous close
    - Continuous: ±5% from last trade (within 5 minutes)
    """
    if last_price is None or last_price == 0:
        return True  # First price, no comparison

    price_delta = abs(new_price - last_price) / last_price

    if price_delta > 0.10:  # 10% move
        log.warning(f"Circuit breaker triggered: {symbol} moved {price_delta*100:.1f}% ({last_price} → {new_price})")
        return False  # Reject tick (likely erroneous)

    return True
```

---

## Testing with Synthetic Data

### Synthetic Feed Generator

```python
import random
from datetime import datetime, timedelta

class SyntheticFeedGenerator:
    """Generate realistic market data for testing."""

    def __init__(self, symbols: list[str], base_prices: dict[str, float]):
        self.symbols = symbols
        self.base_prices = base_prices
        self.current_prices = base_prices.copy()
        self.sequence_numbers = {sym: 1000000 for sym in symbols}

    def generate_tick(self) -> dict:
        """Generate single market tick."""
        symbol = random.choice(self.symbols)

        # Random walk price (±0.1%)
        price_change = random.uniform(-0.001, 0.001)
        self.current_prices[symbol] *= (1 + price_change)

        # Random volume (100-10000 shares)
        volume = random.randint(1, 100) * 100

        # Increment sequence
        self.sequence_numbers[symbol] += 1

        return {
            'exchange': 'TEST',
            'symbol': symbol,
            'price': round(self.current_prices[symbol], 2),
            'volume': volume,
            'exchange_sequence_number': self.sequence_numbers[symbol],
            'exchange_timestamp': datetime.utcnow().isoformat(),
        }

# Usage
generator = SyntheticFeedGenerator(
    symbols=['BHP', 'CBA', 'CSL'],
    base_prices={'BHP': 150.0, 'CBA': 110.0, 'CSL': 280.0}
)

for _ in range(100):
    tick = generator.generate_tick()
    print(tick)
```

---

## Related Documentation

- [Data Quality](./DATA_QUALITY.md) - Validation rules
- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Schema-first principle
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Sequence tracking

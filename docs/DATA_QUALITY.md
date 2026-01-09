# Data Quality & Validation Framework

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Data Engineering
**Status**: Implementation Plan
**Scope**: Data quality validation, anomaly detection, quality metrics

---

## Overview

Market data quality directly impacts trading decisions. Bad data is worse than no data - a single corrupted tick can cause strategies to make incorrect trades, leading to financial loss and reputational damage.

This framework defines validation rules, anomaly detection strategies, quality metrics, and remediation procedures to ensure data reliability.

**Design Philosophy**: Validate early, validate often. Reject bad data at ingestion rather than discovering it during analysis.

---

## Validation Pipeline

### Three-Stage Validation

```
┌─────────────────────────────────────────────────────────────┐
│                Exchange Feed → Kafka Producer                │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
         ┌────────────────────────────────────────┐
         │  Stage 1: Schema Validation            │
         │  - Avro schema compliance              │
         │  - Required fields present             │
         │  - Field types correct                 │
         └────────┬───────────────────────────────┘
                  │ ✓ Valid
                  ▼
         ┌────────────────────────────────────────┐
         │  Stage 2: Business Rule Validation     │
         │  - Price > 0                           │
         │  - Volume >= 0                         │
         │  - Timestamp within market hours       │
         └────────┬───────────────────────────────┘
                  │ ✓ Valid
                  ▼
         ┌────────────────────────────────────────┐
         │  Stage 3: Anomaly Detection            │
         │  - Price delta > 10% (flag)            │
         │  - Volume spike > 100x (flag)          │
         │  - Sequence gap detection              │
         └────────┬───────────────────────────────┘
                  │ ✓ Valid (or flagged)
                  ▼
         ┌────────────────────────────────────────┐
         │  Write to Kafka → Iceberg              │
         └────────────────────────────────────────┘
```

---

## Stage 1: Schema Validation

### Avro Schema Enforcement

**Purpose**: Ensure structural correctness before processing

**Implementation**: Schema Registry enforces BACKWARD compatibility

```avro
{
  "type": "record",
  "name": "MarketTick",
  "namespace": "com.k2.marketdata",
  "fields": [
    {"name": "exchange", "type": "string"},
    {"name": "symbol", "type": "string"},
    {"name": "exchange_sequence_number", "type": "long"},
    {"name": "exchange_timestamp", "type": {"type": "long", "logicalType": "timestamp-micros"}},
    {"name": "price", "type": "double"},
    {"name": "volume", "type": "long"},
    {"name": "message_id", "type": "string"},
    {"name": "ingestion_timestamp", "type": {"type": "long", "logicalType": "timestamp-micros"}}
  ]
}
```

**Validation Errors**:
- Missing required field → Reject message, log error, increment `schema_validation_errors` metric
- Wrong field type → Reject message
- Unknown fields → Accept (forward compatibility) but log warning

**Error Handling**:
```python
from confluent_kafka.avro import AvroProducer
from confluent_kafka import KafkaException

def produce_with_schema_validation(producer: AvroProducer, topic: str, tick: dict):
    """
    Produce message with Avro schema validation.

    Args:
        producer: Avro producer with schema registry
        topic: Kafka topic
        tick: Market tick dictionary

    Raises:
        ValueError: If schema validation fails
    """
    try:
        producer.produce(topic=topic, value=tick)
        producer.flush()
    except ValueError as e:
        # Schema validation failed
        metrics.increment('schema_validation_errors', {
            'exchange': tick.get('exchange', 'unknown'),
            'symbol': tick.get('symbol', 'unknown'),
            'error': str(e)
        })
        log.error(f"Schema validation failed: {e}", extra={'tick': tick})
        raise
```

---

## Stage 2: Business Rule Validation

### Rule Definitions

Business rules encode domain knowledge about valid market data:

| Rule | Description | Action on Violation |
|------|-------------|---------------------|
| **price > 0** | Stock prices must be positive | Reject (critical) |
| **volume >= 0** | Volume cannot be negative | Reject (critical) |
| **timestamp within market hours** | Ticks only during trading session | Flag (warning) |
| **symbol in symbol_master** | Symbol must exist in reference data | Flag (warning) |
| **price within circuit breaker limits** | Price within exchange-defined limits | Flag (warning) |

### Implementation

```python
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Optional

@dataclass
class ValidationResult:
    """Result of validation check."""
    valid: bool
    errors: List[str]
    warnings: List[str]

class BusinessRuleValidator:
    """
    Validate market data against business rules.
    """

    def __init__(self):
        self.symbol_master = self._load_symbol_master()
        self.market_hours = {
            'ASX': (time(10, 0), time(16, 0)),  # 10 AM - 4 PM AEST
            'NYSE': (time(9, 30), time(16, 0))  # 9:30 AM - 4 PM EST
        }

    def validate(self, tick: dict) -> ValidationResult:
        """
        Validate tick against business rules.

        Args:
            tick: Market tick dictionary

        Returns:
            ValidationResult with errors/warnings
        """
        errors = []
        warnings = []

        # Critical rules (reject on failure)
        if tick['price'] <= 0:
            errors.append(f"Invalid price: {tick['price']} (must be positive)")

        if tick['volume'] < 0:
            errors.append(f"Invalid volume: {tick['volume']} (must be non-negative)")

        # Warning rules (flag but accept)
        if not self._is_market_hours(tick['exchange'], tick['exchange_timestamp']):
            warnings.append(f"Tick outside market hours: {tick['exchange_timestamp']}")

        if tick['symbol'] not in self.symbol_master:
            warnings.append(f"Unknown symbol: {tick['symbol']}")

        # Price reasonableness check
        price_warning = self._check_price_reasonableness(tick)
        if price_warning:
            warnings.append(price_warning)

        return ValidationResult(
            valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings
        )

    def _is_market_hours(self, exchange: str, timestamp: datetime) -> bool:
        """Check if timestamp is within market hours."""
        if exchange not in self.market_hours:
            return True  # Unknown exchange, assume valid

        start, end = self.market_hours[exchange]
        tick_time = timestamp.time()
        return start <= tick_time <= end

    def _check_price_reasonableness(self, tick: dict) -> Optional[str]:
        """
        Check if price is within reasonable bounds.

        Uses historical price data to detect anomalies.
        """
        last_price = self._get_last_price(tick['symbol'])

        if last_price is None:
            return None  # No historical data, assume valid

        price_delta = abs(tick['price'] - last_price) / last_price

        if price_delta > 0.10:  # 10% move
            return f"Large price move: {price_delta*100:.1f}% (prev: {last_price}, cur: {tick['price']})"

        return None

    def _get_last_price(self, symbol: str) -> Optional[float]:
        """Get last known price for symbol (from cache or Iceberg)."""
        # Implementation: Query price cache or Iceberg
        pass

    def _load_symbol_master(self) -> set:
        """Load valid symbols from reference data."""
        # Implementation: Load from Iceberg reference_data.symbols table
        return {'BHP', 'CBA', 'CSL', 'WBC', 'NAB', ...}
```

**Error Handling**:
```python
def process_tick_with_validation(tick: dict, validator: BusinessRuleValidator):
    """
    Process tick with business rule validation.

    Args:
        tick: Market tick
        validator: Business rule validator

    Returns:
        True if processed, False if rejected
    """
    result = validator.validate(tick)

    if not result.valid:
        # Critical errors → Reject
        metrics.increment('business_rule_violations', {
            'symbol': tick['symbol'],
            'rule': result.errors[0]
        })
        log.error(f"Business rule violation: {result.errors}", extra={'tick': tick})
        return False

    if result.warnings:
        # Warnings → Accept but flag
        metrics.increment('data_quality_warnings', {
            'symbol': tick['symbol'],
            'warning': result.warnings[0]
        })
        log.warning(f"Data quality warning: {result.warnings}", extra={'tick': tick})

        # Write to quarantine topic for manual review
        write_to_quarantine(tick, result.warnings)

    # Valid or flagged → Continue processing
    return True
```

---

## Stage 3: Anomaly Detection

### Statistical Anomaly Detection

**Purpose**: Detect unusual patterns that may indicate data quality issues

#### Anomaly 1: Price Spike Detection

**Algorithm**: Z-score on rolling window

```python
import numpy as np
from collections import deque

class PriceSpikeDetector:
    """
    Detect abnormal price movements.

    Uses rolling Z-score over 1-hour window.
    """

    def __init__(self, window_size: int = 1000, threshold: float = 3.0):
        self.window_size = window_size
        self.threshold = threshold
        self.price_history = {}  # {symbol: deque of prices}

    def detect(self, symbol: str, price: float) -> bool:
        """
        Detect price spike.

        Args:
            symbol: Stock symbol
            price: Current price

        Returns:
            True if spike detected, False otherwise
        """
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=self.window_size)

        history = self.price_history[symbol]

        if len(history) < 100:
            # Not enough data for statistical analysis
            history.append(price)
            return False

        # Compute Z-score
        mean_price = np.mean(history)
        std_price = np.std(history)

        if std_price == 0:
            # No variance (unusual, but not an error)
            history.append(price)
            return False

        z_score = (price - mean_price) / std_price

        # Add to history
        history.append(price)

        # Detect spike
        if abs(z_score) > self.threshold:
            log.warning(f"Price spike detected: {symbol} price={price} z-score={z_score:.2f}")
            return True

        return False
```

#### Anomaly 2: Volume Spike Detection

**Algorithm**: Compare to 1-hour moving average

```python
class VolumeSpikeDetector:
    """
    Detect abnormal volume spikes.

    Compares current volume to 1-hour moving average.
    """

    def __init__(self, spike_threshold: float = 10.0):
        self.spike_threshold = spike_threshold
        self.volume_history = {}  # {symbol: deque of volumes}

    def detect(self, symbol: str, volume: int) -> bool:
        """
        Detect volume spike.

        Args:
            symbol: Stock symbol
            volume: Current trade volume

        Returns:
            True if spike detected, False otherwise
        """
        if symbol not in self.volume_history:
            self.volume_history[symbol] = deque(maxlen=1000)

        history = self.volume_history[symbol]

        if len(history) < 100:
            history.append(volume)
            return False

        avg_volume = np.mean(history)

        if avg_volume == 0:
            history.append(volume)
            return False

        ratio = volume / avg_volume

        history.append(volume)

        if ratio > self.spike_threshold:
            log.warning(f"Volume spike detected: {symbol} volume={volume} avg={avg_volume:.0f} ratio={ratio:.1f}x")
            return True

        return False
```

#### Anomaly 3: Stale Data Detection

**Algorithm**: Check if timestamp delta exceeds threshold

```python
class StaleDataDetector:
    """
    Detect stale data (old timestamps).
    """

    def __init__(self, max_delay_seconds: int = 60):
        self.max_delay_seconds = max_delay_seconds

    def detect(self, exchange_timestamp: datetime, ingestion_timestamp: datetime) -> bool:
        """
        Detect stale data.

        Args:
            exchange_timestamp: Timestamp from exchange
            ingestion_timestamp: Timestamp when platform received message

        Returns:
            True if data is stale, False otherwise
        """
        delay = (ingestion_timestamp - exchange_timestamp).total_seconds()

        if delay > self.max_delay_seconds:
            log.warning(f"Stale data detected: delay={delay:.1f}s (threshold={self.max_delay_seconds}s)")
            return True

        return False
```

---

## Data Quarantine

### Quarantine Topic

**Purpose**: Isolate suspicious data for manual review

**Kafka Topic**: `market.quarantine.{exchange}`

**Retention**: 7 days

**Schema**:
```avro
{
  "type": "record",
  "name": "QuarantinedTick",
  "fields": [
    {"name": "original_tick", "type": "MarketTick"},
    {"name": "quarantine_reason", "type": "string"},
    {"name": "quarantine_timestamp", "type": {"type": "long", "logicalType": "timestamp-micros"}},
    {"name": "severity", "type": {"type": "enum", "symbols": ["WARNING", "ERROR"]}}
  ]
}
```

**Implementation**:
```python
def write_to_quarantine(tick: dict, reasons: List[str]):
    """
    Write tick to quarantine topic for manual review.

    Args:
        tick: Original market tick
        reasons: List of quarantine reasons
    """
    quarantined = {
        'original_tick': tick,
        'quarantine_reason': '; '.join(reasons),
        'quarantine_timestamp': datetime.utcnow(),
        'severity': 'WARNING'  # or 'ERROR' for critical issues
    }

    producer.produce(
        topic=f"market.quarantine.{tick['exchange']}",
        value=quarantined
    )

    metrics.increment('quarantined_ticks', {
        'exchange': tick['exchange'],
        'symbol': tick['symbol'],
        'reason': reasons[0]
    })
```

### Manual Review Process

**Daily Review**:
1. Data quality team reviews quarantine topic (9 AM daily)
2. Classify each quarantined tick:
   - **False Positive**: Data is valid, update detection rules
   - **True Positive**: Data is corrupt, contact exchange for correction
   - **Unknown**: Escalate to platform team

**Remediation**:
```python
def remediate_quarantine(tick_id: str, action: str):
    """
    Remediate quarantined tick.

    Args:
        tick_id: Quarantined tick ID
        action: 'approve' (write to Iceberg) or 'reject' (discard)
    """
    tick = load_quarantined_tick(tick_id)

    if action == 'approve':
        # Write to Iceberg (was false positive)
        iceberg_writer.write_batch([tick['original_tick']])
        log.info(f"Quarantined tick approved: {tick_id}")
    elif action == 'reject':
        # Discard (was true positive)
        log.info(f"Quarantined tick rejected: {tick_id}")
    else:
        raise ValueError(f"Invalid action: {action}")

    # Mark as reviewed
    mark_as_reviewed(tick_id, action)
```

---

## Data Quality Metrics

### SLOs (Service Level Objectives)

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| **Schema validation pass rate** | > 99.9% | < 99.5% |
| **Business rule pass rate** | > 99% | < 98% |
| **Quarantine rate** | < 1% | > 2% |
| **Data completeness** | > 99.5% | < 99% |
| **Data freshness (p99)** | < 5 seconds | > 10 seconds |

### Monitoring Metrics

```
# Validation metrics
data_validation_errors_total{stage="schema|business|anomaly", exchange, symbol}
data_validation_warnings_total{rule, exchange, symbol}

# Quality metrics
data_quality_score{exchange, symbol}  # 0-100 score
data_completeness_ratio{exchange, symbol}  # 0-1 ratio
data_freshness_seconds{exchange, symbol, percentile="50|99"}

# Quarantine metrics
quarantined_ticks_total{exchange, symbol, reason}
quarantine_review_time_seconds{action="approve|reject"}
```

### Data Quality Score

**Calculation**:
```python
def calculate_quality_score(symbol: str, window_hours: int = 1) -> float:
    """
    Calculate data quality score (0-100) for symbol.

    Factors:
    - Schema validation pass rate (40%)
    - Business rule pass rate (30%)
    - Anomaly rate (20%)
    - Data freshness (10%)

    Args:
        symbol: Stock symbol
        window_hours: Time window for calculation

    Returns:
        Quality score (0-100, higher is better)
    """
    stats = get_quality_stats(symbol, window_hours)

    score = (
        stats['schema_pass_rate'] * 40 +
        stats['business_pass_rate'] * 30 +
        (1 - stats['anomaly_rate']) * 20 +
        stats['freshness_score'] * 10
    )

    return score
```

---

## Data Lineage Tracking

### Lineage Metadata

**Purpose**: Track data provenance for debugging and auditing

**Metadata Schema**:
```python
@dataclass
class LineageMetadata:
    """Data lineage metadata for market tick."""
    source_exchange: str
    feed_handler_id: str
    feed_handler_version: str
    producer_host: str
    producer_process_id: int
    ingestion_timestamp: datetime
    kafka_offset: int
    kafka_partition: int
    iceberg_file_path: str
    iceberg_snapshot_id: int
```

**Storage**: Separate Iceberg table `metadata.data_lineage`

**Usage**:
```python
def trace_tick_lineage(message_id: str) -> LineageMetadata:
    """
    Trace lineage of a specific tick.

    Args:
        message_id: Market tick message ID

    Returns:
        Lineage metadata
    """
    query = f"""
        SELECT * FROM metadata.data_lineage
        WHERE message_id = '{message_id}'
    """
    result = conn.execute(query).fetchone()
    return LineageMetadata(**result)

# Example: Investigate bad tick
lineage = trace_tick_lineage("ASX.BHP.184847291")
print(f"Source: {lineage.feed_handler_id} on {lineage.producer_host}")
print(f"Ingestion: {lineage.ingestion_timestamp}")
print(f"Stored in: {lineage.iceberg_file_path}")
```

---

## Testing Requirements

### Data Quality Tests

**Test 1: Schema Validation Rejects Invalid Data**
```python
def test_schema_validation_rejects_invalid():
    """Verify invalid schema is rejected."""
    invalid_tick = {
        'symbol': 'BHP',
        'price': 'invalid_string',  # Should be double
        'volume': 100
    }

    with pytest.raises(ValueError, match="Schema validation failed"):
        produce_with_schema_validation(producer, 'market.ticks.asx', invalid_tick)
```

**Test 2: Business Rules Detect Negative Price**
```python
def test_business_rule_negative_price():
    """Verify negative price is rejected."""
    tick = {
        'symbol': 'BHP',
        'price': -150.23,
        'volume': 100,
        'exchange_timestamp': datetime.utcnow()
    }

    validator = BusinessRuleValidator()
    result = validator.validate(tick)

    assert not result.valid
    assert 'Invalid price' in result.errors[0]
```

**Test 3: Anomaly Detection Flags Price Spike**
```python
def test_anomaly_detection_price_spike():
    """Verify price spike is detected."""
    detector = PriceSpikeDetector(threshold=3.0)

    # Feed normal prices
    for price in [150.0, 150.5, 149.8, 150.2, 150.1]:
        detector.detect('BHP', price)

    # Feed spike
    is_spike = detector.detect('BHP', 200.0)  # 33% jump
    assert is_spike
```

**Test 4: Quarantine Flow**
```python
def test_quarantine_flow():
    """Verify quarantine writes to correct topic."""
    tick = {'symbol': 'BHP', 'price': 150.23}
    reasons = ['Price spike detected']

    write_to_quarantine(tick, reasons)

    # Verify message in quarantine topic
    consumer = Consumer({'group.id': 'test', 'auto.offset.reset': 'earliest'})
    consumer.subscribe(['market.quarantine.ASX'])

    msg = consumer.poll(timeout=5.0)
    assert msg is not None

    quarantined = deserialize(msg.value())
    assert quarantined['original_tick'] == tick
    assert 'Price spike' in quarantined['quarantine_reason']
```

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Schema-first, always
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Sequence tracking
- [Query Architecture](./QUERY_ARCHITECTURE.md) - Data freshness requirements
- [Data Source Assumptions](./DATA_SOURCE_ASSUMPTIONS.md) - Expected feed characteristics
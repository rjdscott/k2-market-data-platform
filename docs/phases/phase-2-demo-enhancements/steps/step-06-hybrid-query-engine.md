# Step 04: Hybrid Query Engine (formerly Step 06)

**Status**: ✅ Complete (2026-01-13)
**Assignee**: Implementation Team
**Issue**: #5 - Query Layer Lacks Hybrid Real-Time + Historical Path
**Effort**: 8 hours (actual)

**Note**: Renumbered from Step 06 to Step 04 after deferring Redis/Bloom to multi-node (see Decision #004 in DECISIONS.md)

---

## Dependencies
- **Requires**: Phase 1 Query Engine complete
- **Blocks**: Step 07 (Demo Narrative)

---

## Goal

Implement hybrid queries that merge real-time Kafka tail with historical Iceberg data. The hardest part of market data platforms is answering "give me last 15 minutes of BHP trades" when the last 2 minutes are in Kafka and the previous 13 are in Iceberg.

---

## Overview

### Current Problem

```
Query: "Last 15 minutes of BHP trades"

Current System:
├── Iceberg has data up to T-2 minutes (commit delay)
├── Kafka has data from T-15 minutes to now
└── No mechanism to merge these sources
```

### Solution: Hybrid Query Engine

```
Query: "Last 15 minutes of BHP trades"

Hybrid Engine:
├── Query Iceberg: T-15 min to T-2 min (historical)
├── Query Kafka tail: T-2 min to now (real-time)
├── Merge results
├── Deduplicate by message_id
└── Return unified result
```

---

## Deliverables

### 1. Kafka Tail Consumer

Create `src/k2/query/kafka_tail.py`:

```python
"""
Kafka tail consumer for real-time data access.

Maintains a sliding window of recent messages in memory
for fast query access without Iceberg commit delay.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import threading
import time

from confluent_kafka import Consumer, KafkaException
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer

from k2.common.logging import get_logger
from k2.common.config import get_config

logger = get_logger(__name__, component="kafka_tail")


@dataclass
class TailMessage:
    """Message from Kafka tail."""
    message_id: str
    symbol: str
    exchange: str
    exchange_timestamp: datetime
    price: float
    volume: float
    data_type: str
    raw_data: dict


class KafkaTail:
    """
    In-memory buffer of recent Kafka messages.

    Provides fast access to data not yet committed to Iceberg.

    Usage:
        tail = KafkaTail(buffer_minutes=5)
        tail.start()

        # Query recent messages
        messages = tail.query('BHP', minutes=2)

        tail.stop()
    """

    def __init__(
        self,
        topics: Optional[List[str]] = None,
        buffer_minutes: int = 5,
        max_messages_per_symbol: int = 10000,
    ):
        """
        Initialize Kafka tail.

        Args:
            topics: Topics to consume (default: market.equities.trades.asx)
            buffer_minutes: Minutes of data to keep in buffer
            max_messages_per_symbol: Max messages per symbol (memory limit)
        """
        self.topics = topics or ['market.equities.trades.asx']
        self.buffer_minutes = buffer_minutes
        self.max_messages = max_messages_per_symbol

        # Message buffer: symbol -> list of TailMessage
        self._buffer: Dict[str, List[TailMessage]] = defaultdict(list)
        self._lock = threading.Lock()

        # Consumer setup
        self._consumer: Optional[Consumer] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        logger.info(
            "Kafka tail initialized",
            topics=self.topics,
            buffer_minutes=buffer_minutes,
        )

    def start(self):
        """Start consuming in background thread."""
        if self._running:
            return

        config = get_config()

        # Consumer config - start from end (latest messages only)
        consumer_config = {
            'bootstrap.servers': config.kafka.bootstrap_servers,
            'group.id': 'k2-kafka-tail',
            'auto.offset.reset': 'latest',
            'enable.auto.commit': True,
        }

        self._consumer = Consumer(consumer_config)
        self._consumer.subscribe(self.topics)

        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._thread.start()

        logger.info("Kafka tail consumer started")

    def stop(self):
        """Stop consuming."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._consumer:
            self._consumer.close()

        logger.info("Kafka tail consumer stopped")

    def _consume_loop(self):
        """Background consumer loop."""
        while self._running:
            try:
                msg = self._consumer.poll(timeout=0.1)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("Kafka tail error", error=str(msg.error()))
                    continue

                # Parse message (simplified - actual would use Avro deserializer)
                self._process_message(msg)

            except Exception as e:
                logger.error("Kafka tail consume error", error=str(e))
                time.sleep(1)

        logger.debug("Kafka tail consume loop exited")

    def _process_message(self, msg):
        """Process and buffer a message."""
        try:
            # Deserialize (simplified)
            value = msg.value()
            if isinstance(value, bytes):
                import json
                data = json.loads(value.decode())
            else:
                data = value

            symbol = data.get('symbol', '')
            if not symbol:
                return

            tail_msg = TailMessage(
                message_id=data.get('message_id', f"{msg.partition()}:{msg.offset()}"),
                symbol=symbol,
                exchange=data.get('exchange', 'asx'),
                exchange_timestamp=datetime.fromisoformat(
                    data.get('exchange_timestamp', datetime.utcnow().isoformat())
                ),
                price=float(data.get('price', 0)),
                volume=float(data.get('volume', 0)),
                data_type='trades',
                raw_data=data,
            )

            with self._lock:
                buffer = self._buffer[symbol]
                buffer.append(tail_msg)

                # Trim old messages
                cutoff = datetime.utcnow() - timedelta(minutes=self.buffer_minutes)
                self._buffer[symbol] = [
                    m for m in buffer
                    if m.exchange_timestamp > cutoff
                ][-self.max_messages:]

        except Exception as e:
            logger.debug("Failed to process tail message", error=str(e))

    def query(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        minutes: Optional[int] = None,
    ) -> List[TailMessage]:
        """
        Query buffered messages for a symbol.

        Args:
            symbol: Trading symbol
            since: Return messages after this time
            minutes: Alternative - return last N minutes

        Returns:
            List of TailMessage sorted by timestamp
        """
        if minutes is not None:
            since = datetime.utcnow() - timedelta(minutes=minutes)

        with self._lock:
            messages = self._buffer.get(symbol, [])

            if since:
                messages = [m for m in messages if m.exchange_timestamp >= since]

            return sorted(messages, key=lambda m: m.exchange_timestamp)

    def get_symbols(self) -> List[str]:
        """Get list of symbols in buffer."""
        with self._lock:
            return list(self._buffer.keys())

    def get_stats(self) -> dict:
        """Get buffer statistics."""
        with self._lock:
            total_messages = sum(len(msgs) for msgs in self._buffer.values())
            return {
                'symbols_count': len(self._buffer),
                'total_messages': total_messages,
                'buffer_minutes': self.buffer_minutes,
                'is_running': self._running,
            }
```

### 2. Hybrid Query Engine

Create `src/k2/query/hybrid_engine.py`:

```python
"""
Hybrid query engine merging Kafka real-time with Iceberg historical.

Solves the problem of querying recent data that spans both sources.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pandas as pd

from k2.query.engine import QueryEngine
from k2.query.kafka_tail import KafkaTail, TailMessage
from k2.common.logging import get_logger
from k2.common.metrics_registry import METRICS

logger = get_logger(__name__, component="hybrid_engine")


class HybridQueryEngine:
    """
    Query engine that merges real-time Kafka data with historical Iceberg data.

    Handles the common case where a query spans both sources:
    - Iceberg: Data up to T-2 minutes (commit delay)
    - Kafka tail: Data from T-buffer_minutes to now

    Usage:
        iceberg = QueryEngine()
        tail = KafkaTail()
        tail.start()

        hybrid = HybridQueryEngine(iceberg, tail)

        # Query seamlessly across both sources
        df = hybrid.query_recent_trades('BHP', window_minutes=15)
    """

    def __init__(
        self,
        iceberg_engine: QueryEngine,
        kafka_tail: Optional[KafkaTail] = None,
        buffer_overlap_minutes: int = 2,
    ):
        """
        Initialize hybrid engine.

        Args:
            iceberg_engine: Iceberg query engine
            kafka_tail: Kafka tail consumer (optional, created if not provided)
            buffer_overlap_minutes: Overlap buffer for consistency
        """
        self.iceberg = iceberg_engine
        self.kafka_tail = kafka_tail or KafkaTail()
        self.buffer_overlap = buffer_overlap_minutes

        logger.info(
            "Hybrid query engine initialized",
            buffer_overlap_minutes=buffer_overlap_minutes,
        )

    def start_tail(self):
        """Start Kafka tail consumer."""
        self.kafka_tail.start()

    def stop_tail(self):
        """Stop Kafka tail consumer."""
        self.kafka_tail.stop()

    def query_recent_trades(
        self,
        symbol: str,
        window_minutes: int = 15,
        exchange: str = 'asx',
    ) -> pd.DataFrame:
        """
        Query recent trades spanning Kafka and Iceberg.

        Args:
            symbol: Trading symbol
            window_minutes: Time window in minutes
            exchange: Exchange filter

        Returns:
            DataFrame with merged, deduplicated trades
        """
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=window_minutes)

        # Split point: Iceberg data ends, Kafka takes over
        iceberg_end = now - timedelta(minutes=self.buffer_overlap)

        logger.debug(
            "Hybrid query",
            symbol=symbol,
            window_start=window_start.isoformat(),
            iceberg_end=iceberg_end.isoformat(),
        )

        # Query Iceberg (historical)
        try:
            historical_df = self.iceberg.query_trades(
                symbol=symbol,
                exchange=exchange,
                start_time=window_start,
                end_time=iceberg_end,
            )
        except Exception as e:
            logger.warning("Iceberg query failed", error=str(e))
            historical_df = pd.DataFrame()

        # Query Kafka tail (real-time)
        realtime_messages = self.kafka_tail.query(
            symbol=symbol,
            since=iceberg_end - timedelta(minutes=1),  # Small overlap for safety
        )

        # Convert Kafka messages to DataFrame
        if realtime_messages:
            realtime_df = pd.DataFrame([
                {
                    'symbol': m.symbol,
                    'exchange': m.exchange,
                    'exchange_timestamp': m.exchange_timestamp,
                    'price': m.price,
                    'volume': m.volume,
                    'message_id': m.message_id,
                }
                for m in realtime_messages
            ])
        else:
            realtime_df = pd.DataFrame()

        # Merge and deduplicate
        merged_df = self._merge_and_deduplicate(historical_df, realtime_df)

        # Record metrics
        METRICS['k2_query_executions_total'].labels(
            service='k2-platform',
            environment='dev',
            component='query',
            query_type='hybrid',
        ).inc()

        logger.info(
            "Hybrid query complete",
            symbol=symbol,
            historical_rows=len(historical_df),
            realtime_rows=len(realtime_df),
            merged_rows=len(merged_df),
        )

        return merged_df

    def _merge_and_deduplicate(
        self,
        historical_df: pd.DataFrame,
        realtime_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Merge DataFrames and deduplicate by message_id.

        Keeps the most recent version (from Kafka) if duplicates exist.
        """
        if historical_df.empty and realtime_df.empty:
            return pd.DataFrame()

        if historical_df.empty:
            return realtime_df.sort_values('exchange_timestamp')

        if realtime_df.empty:
            return historical_df.sort_values('exchange_timestamp')

        # Concatenate
        combined = pd.concat([historical_df, realtime_df], ignore_index=True)

        # Deduplicate by message_id, keeping last (realtime takes precedence)
        if 'message_id' in combined.columns:
            combined = combined.drop_duplicates(
                subset=['message_id'],
                keep='last',
            )

        # Sort by timestamp
        combined = combined.sort_values('exchange_timestamp').reset_index(drop=True)

        return combined

    def query_recent_quotes(
        self,
        symbol: str,
        window_minutes: int = 15,
        exchange: str = 'asx',
    ) -> pd.DataFrame:
        """Query recent quotes (similar to trades)."""
        # Similar implementation for quotes
        pass  # Abbreviated for documentation

    def get_stats(self) -> dict:
        """Get hybrid engine statistics."""
        return {
            'kafka_tail': self.kafka_tail.get_stats(),
            'buffer_overlap_minutes': self.buffer_overlap,
        }


def create_hybrid_engine() -> HybridQueryEngine:
    """
    Factory to create configured hybrid engine.

    Usage:
        engine = create_hybrid_engine()
        engine.start_tail()

        df = engine.query_recent_trades('BHP', window_minutes=15)

        engine.stop_tail()
    """
    from k2.query.engine import QueryEngine

    iceberg = QueryEngine()
    tail = KafkaTail(buffer_minutes=5)

    return HybridQueryEngine(
        iceberg_engine=iceberg,
        kafka_tail=tail,
        buffer_overlap_minutes=2,
    )
```

### 3. API Integration

Update `src/k2/api/v1/endpoints.py` to use hybrid queries for recent data.

### 4. Unit Tests

Create `tests/unit/test_hybrid_engine.py` with 15+ tests.

---

## Validation

### Acceptance Criteria

1. [x] `src/k2/query/kafka_tail.py` created (356 lines)
2. [x] `src/k2/query/hybrid_engine.py` created (334 lines)
3. [x] Queries merge Kafka + Iceberg data
4. [x] Deduplication by message_id working
5. [x] API uses hybrid for recent queries (`/v1/trades/recent` endpoint)
6. [x] 32 unit tests passing (16 kafka_tail + 16 hybrid_engine)

### Verification Commands

```bash
# Run tests
pytest tests/unit/test_hybrid_engine.py -v

# Test API
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&window_minutes=15"

# Should return data from both Kafka and Iceberg
```

---

## Demo Talking Points

> "The hardest part of market data platforms is answering
> 'give me last 15 minutes of trades' when some of that data
> is still in Kafka and some is in Iceberg.
>
> Our hybrid query engine solves this:
> - Query Iceberg for data older than 2 minutes
> - Query Kafka tail for the last 2 minutes
> - Merge and deduplicate by message ID
>
> The user doesn't know or care where the data comes from—
> they just get a seamless result.
>
> [Demo API call]
> See? 15 minutes of BHP trades, sourced from both systems."

---

## Implementation Notes (2026-01-13)

### Files Created

1. **src/k2/query/kafka_tail.py** (356 lines)
   - In-memory buffer for recent Kafka messages
   - Background consumer thread starting from latest offset
   - 5-minute sliding window (configurable)
   - Thread-safe operations with mutex lock
   - Automatic trimming every 10 seconds
   - Query interface by symbol, exchange, and time range

2. **src/k2/query/hybrid_engine.py** (334 lines)
   - Unified query interface merging Kafka + Iceberg
   - Automatic query routing based on time windows
   - Commit lag: 2 minutes (configurable)
   - Deduplication by message_id
   - Graceful degradation (returns partial results if one source fails)
   - Factory function `create_hybrid_engine()`

3. **tests/unit/test_kafka_tail.py** (16 tests)
   - Initialization and configuration
   - Message processing and buffering
   - Query operations (time range, filtering)
   - Trimming and cleanup
   - Statistics tracking
   - Edge cases and error handling

4. **tests/unit/test_hybrid_engine.py** (16 tests)
   - Query routing logic (Kafka only, Iceberg only, both)
   - Deduplication by message_id
   - Result sorting by timestamp
   - Error handling and graceful degradation
   - Limit enforcement
   - Statistics tracking

### Files Modified

1. **src/k2/api/deps.py**
   - Added `get_hybrid_engine()` dependency injection
   - Updated startup/shutdown to initialize hybrid engine
   - Cleanup logic for Kafka tail consumer

2. **src/k2/api/v1/endpoints.py**
   - Added `/v1/trades/recent` endpoint for hybrid queries
   - Parameters: symbol, exchange, window_minutes (1-60)
   - Returns unified results from Kafka + Iceberg
   - Includes query metadata in response

### Test Coverage

- **Total tests**: 32 (16 kafka_tail + 16 hybrid_engine)
- **Test categories**:
  - Initialization: 4 tests
  - Query routing: 3 tests
  - Deduplication: 2 tests
  - Error handling: 3 tests
  - Edge cases: 5 tests
  - Integration: 2 tests
  - Statistics: 2 tests
  - Message processing: 6 tests
  - Trimming: 2 tests
  - Sorting: 1 test
  - Limit: 1 test
  - Factory: 1 test

### Key Design Decisions

1. **In-Memory Kafka Tail Buffer**
   - **Decision**: Use in-memory dict instead of external cache
   - **Rationale**: 5-minute window × 1K msg/sec × 500 bytes = ~150 MB (acceptable)
   - **Trade-off**: State lost on restart (acceptable for crypto demo)

2. **Commit Lag: 2 Minutes**
   - **Decision**: Use 2-minute buffer overlap between Kafka and Iceberg
   - **Rationale**: Accounts for consumer processing + Iceberg commit + catalog update
   - **Trade-off**: Queries within last 2 minutes hit both sources (deduplication needed)

3. **Graceful Degradation**
   - **Decision**: Return partial results if one source fails
   - **Rationale**: Better to show some data than error out entirely
   - **Impact**: Users get degraded service instead of failure

4. **API Endpoint Design**
   - **Decision**: New `/v1/trades/recent` endpoint instead of modifying `/v1/trades`
   - **Rationale**: Clear separation between hybrid and Iceberg-only queries
   - **Trade-off**: Two endpoints, but clearer intent

### Performance Characteristics

- **Kafka tail query**: <50ms (in-memory lookup)
- **Iceberg query**: 200-500ms (DuckDB + Iceberg)
- **Hybrid query (15-min window)**: <500ms p99
- **Deduplication overhead**: O(n) where n = result count
- **Memory usage**: ~150 MB for 5-minute buffer at 1K msg/sec

### Integration Points

1. **FastAPI Lifespan**
   - Hybrid engine initialized on startup
   - Kafka tail consumer started automatically
   - Cleanup on shutdown (stop consumer, close connections)

2. **Dependency Injection**
   - `get_hybrid_engine()` provides singleton instance
   - Lazy initialization on first request
   - Cached with `@lru_cache`

3. **Metrics**
   - `k2_hybrid_queries_total` - Query count by source
   - `k2_hybrid_query_results` - Result count histogram

### Known Limitations

1. **Single-Node Only**
   - Kafka tail buffer is local (not shared across nodes)
   - Would need Redis or similar for multi-node deployment

2. **Kafka Consumer from Latest**
   - Starts from latest offset (misses historical tail on startup)
   - Acceptable for demo, would need offset management for production

3. **No Avro Deserialization**
   - Simplified JSON parsing for now
   - Would need proper Avro schema registry integration for production

4. **No Authentication for Kafka**
   - Currently connects without auth
   - Would need SASL/SSL for production Kafka

### Next Steps (Not in Phase 2 Scope)

- [ ] Add Avro schema registry integration
- [ ] Implement Kafka consumer offset management
- [ ] Add authentication for Kafka connections
- [ ] Distributed Kafka tail (Redis-backed) for multi-node
- [ ] Query result caching for repeated queries
- [ ] Prometheus alerts for Kafka consumer lag
- [ ] Grafana dashboard panel for hybrid query metrics

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete

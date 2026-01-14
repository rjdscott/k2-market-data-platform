# Step 04: Redis Sequence Tracker

**Status**: 🔵 Deferred to Multi-Node Implementation
**Assignee**: Implementation Team (Future)
**Issue**: #3 - Sequence Tracking Uses Python Dict Under GIL
**Decision Date**: 2026-01-13
**Deferred By**: User + Implementation Team

---

## Deferral Rationale

**This step has been deferred to multi-node implementation.** See `TODO.md` for full details.

### Why Deferred for Single-Node Crypto:
1. **Not the bottleneck**: Current throughput is 138 msg/sec (I/O bound), not CPU bound
2. **In-memory is faster**: Dict lookup <1μs vs Redis 1-2ms network latency
3. **GIL not a problem**: Only matters at >100K msg/sec (we're at 138 msg/sec)
4. **Crypto rates modest**: Peak 10K-50K msg/sec for top pairs (well within in-memory capacity)
5. **Adds complexity**: New Redis dependency to monitor and maintain
6. **Wrong optimization**: Solving distributed systems problem for single-node use case

### When to Reconsider:
- Multi-node consumer fleet deployment
- Throughput exceeds 100K msg/sec per node
- Need for shared sequence state across instances
- State persistence requirements across restarts

### Effort Saved: 6-8 hours (redirected to Hybrid Query Engine)

---

## Original Goal (For Reference)

Replace in-memory dict with Redis-backed sequence tracking. The current implementation uses a Python dict which suffers from GIL contention at high message rates. Redis provides O(1) lookups without GIL overhead and survives process restarts.

**Note**: This goal remains valid for multi-node deployments. For single-node crypto, in-memory dict is optimal.

---

## Overview

### Current Problem

```python
# Current implementation - GIL-bound
self._state: Dict[Tuple[str, str], SequenceState] = {}
```

At 10K symbols × 10K updates/sec = 100M lookups/sec:
- Python dict lookups are O(1) but GIL contention causes 10-50ms latency spikes
- State is lost on process restart
- Cannot scale to multiple consumer instances

### Solution: Redis-Backed Tracker

```python
# New implementation - external state store
class RedisSequenceTracker:
    def __init__(self, redis_client: Redis, pipeline_size: int = 100):
        self.redis = redis_client
        self.pipeline_size = pipeline_size
```

Benefits:
- No GIL contention (Redis is external process)
- State survives restarts
- Shared across consumer instances
- Pipelining for batch efficiency

---

## Deliverables

### 1. Redis Sequence Tracker

Create `src/k2/ingestion/redis_sequence_tracker.py`:

```python
"""
Redis-backed sequence tracker for production-grade gap detection.

Replaces in-memory dict to avoid GIL contention at high message rates.
Uses Redis pipelining for batch efficiency.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List, Tuple
import redis

from k2.common.logging import get_logger
from k2.common.metrics_registry import METRICS

logger = get_logger(__name__, component="redis_sequence_tracker")


class SequenceEventType(Enum):
    """Types of sequence events."""
    NORMAL = "normal"
    GAP = "gap"
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE = "duplicate"
    RESET = "reset"


@dataclass
class SequenceResult:
    """Result of sequence check."""
    event_type: SequenceEventType
    expected_sequence: Optional[int]
    actual_sequence: int
    gap_size: Optional[int] = None
    symbol: str = ""
    exchange: str = ""


class RedisSequenceTracker:
    """
    Redis-backed sequence tracker with pipelining support.

    Uses Redis hash for per-symbol state:
        Key: seq:{exchange}:{symbol}
        Fields: last_seq, last_timestamp, gap_count, reset_count

    Usage:
        import redis
        r = redis.Redis(host='localhost', port=6379)
        tracker = RedisSequenceTracker(r)

        # Single check
        result = tracker.check_sequence('asx', 'BHP', 1001)

        # Batch check (more efficient)
        results = tracker.check_sequence_batch([
            ('asx', 'BHP', 1001),
            ('asx', 'CBA', 2001),
        ])
    """

    # Redis key prefix
    KEY_PREFIX = "k2:seq"

    # Hash field names
    FIELD_LAST_SEQ = "last_seq"
    FIELD_LAST_TS = "last_ts"
    FIELD_GAP_COUNT = "gap_count"
    FIELD_RESET_COUNT = "reset_count"

    # Gap detection thresholds
    GAP_THRESHOLD_LOG = 10        # Log gaps >= 10
    GAP_THRESHOLD_ALERT = 100     # Alert on gaps >= 100
    GAP_THRESHOLD_HALT = 1000     # Consider halting on gaps >= 1000
    RESET_THRESHOLD = 0.5         # Sequence dropped by 50% = likely reset

    def __init__(
        self,
        redis_client: redis.Redis,
        pipeline_size: int = 100,
        key_ttl_seconds: int = 86400 * 7,  # 7 days
    ):
        """
        Initialize Redis sequence tracker.

        Args:
            redis_client: Redis client instance
            pipeline_size: Batch size for pipelining
            key_ttl_seconds: TTL for sequence keys (cleanup old symbols)
        """
        self.redis = redis_client
        self.pipeline_size = pipeline_size
        self.key_ttl = key_ttl_seconds

        # Verify Redis connection
        try:
            self.redis.ping()
            logger.info(
                "Redis sequence tracker initialized",
                pipeline_size=pipeline_size,
                key_ttl_seconds=key_ttl_seconds,
            )
        except redis.ConnectionError as e:
            logger.error("Failed to connect to Redis", error=str(e))
            raise

    def _make_key(self, exchange: str, symbol: str) -> str:
        """Create Redis key for symbol."""
        return f"{self.KEY_PREFIX}:{exchange}:{symbol}"

    def check_sequence(
        self,
        exchange: str,
        symbol: str,
        sequence: int,
        timestamp: Optional[datetime] = None,
    ) -> SequenceResult:
        """
        Check sequence number for a single message.

        Args:
            exchange: Exchange identifier
            symbol: Trading symbol
            sequence: Sequence number from message
            timestamp: Optional message timestamp

        Returns:
            SequenceResult with event type and details
        """
        key = self._make_key(exchange, symbol)
        ts_value = timestamp.isoformat() if timestamp else datetime.utcnow().isoformat()

        # Get current state
        state = self.redis.hgetall(key)

        if not state:
            # First message for this symbol
            self._set_state(key, sequence, ts_value)
            return SequenceResult(
                event_type=SequenceEventType.NORMAL,
                expected_sequence=None,
                actual_sequence=sequence,
                symbol=symbol,
                exchange=exchange,
            )

        last_seq = int(state.get(b'last_seq', 0))
        expected_seq = last_seq + 1

        # Check for various conditions
        if sequence == expected_seq:
            # Normal sequential
            self._set_state(key, sequence, ts_value)
            return SequenceResult(
                event_type=SequenceEventType.NORMAL,
                expected_sequence=expected_seq,
                actual_sequence=sequence,
                symbol=symbol,
                exchange=exchange,
            )

        elif sequence > expected_seq:
            # Gap detected
            gap_size = sequence - expected_seq
            self._record_gap(key, sequence, ts_value, gap_size)

            # Log based on severity
            self._log_gap(exchange, symbol, expected_seq, sequence, gap_size)

            return SequenceResult(
                event_type=SequenceEventType.GAP,
                expected_sequence=expected_seq,
                actual_sequence=sequence,
                gap_size=gap_size,
                symbol=symbol,
                exchange=exchange,
            )

        elif sequence == last_seq:
            # Duplicate
            METRICS['k2_duplicate_messages_detected_total'].labels(
                service='k2-platform',
                environment='dev',
                component='sequence_tracker',
                exchange=exchange,
            ).inc()

            return SequenceResult(
                event_type=SequenceEventType.DUPLICATE,
                expected_sequence=expected_seq,
                actual_sequence=sequence,
                symbol=symbol,
                exchange=exchange,
            )

        elif sequence < last_seq * self.RESET_THRESHOLD:
            # Likely a reset (sequence dropped significantly)
            self._record_reset(key, sequence, ts_value)

            logger.info(
                "Sequence reset detected",
                exchange=exchange,
                symbol=symbol,
                last_seq=last_seq,
                new_seq=sequence,
            )

            return SequenceResult(
                event_type=SequenceEventType.RESET,
                expected_sequence=expected_seq,
                actual_sequence=sequence,
                symbol=symbol,
                exchange=exchange,
            )

        else:
            # Out of order
            METRICS['k2_out_of_order_messages_total'].labels(
                service='k2-platform',
                environment='dev',
                component='sequence_tracker',
                exchange=exchange,
            ).inc()

            return SequenceResult(
                event_type=SequenceEventType.OUT_OF_ORDER,
                expected_sequence=expected_seq,
                actual_sequence=sequence,
                symbol=symbol,
                exchange=exchange,
            )

    def check_sequence_batch(
        self,
        events: List[Tuple[str, str, int, Optional[datetime]]],
    ) -> List[SequenceResult]:
        """
        Check sequences for a batch of messages using pipelining.

        More efficient than individual checks for high-volume processing.

        Args:
            events: List of (exchange, symbol, sequence, timestamp) tuples

        Returns:
            List of SequenceResult in same order as input
        """
        if not events:
            return []

        results = []
        pipe = self.redis.pipeline()

        # Batch get all current states
        keys = [self._make_key(e[0], e[1]) for e in events]
        for key in keys:
            pipe.hgetall(key)

        states = pipe.execute()

        # Process each event
        pipe = self.redis.pipeline()
        for i, (exchange, symbol, sequence, timestamp) in enumerate(events):
            key = keys[i]
            state = states[i]
            ts_value = timestamp.isoformat() if timestamp else datetime.utcnow().isoformat()

            if not state:
                # First message
                pipe.hset(key, mapping={
                    self.FIELD_LAST_SEQ: sequence,
                    self.FIELD_LAST_TS: ts_value,
                    self.FIELD_GAP_COUNT: 0,
                    self.FIELD_RESET_COUNT: 0,
                })
                pipe.expire(key, self.key_ttl)
                results.append(SequenceResult(
                    event_type=SequenceEventType.NORMAL,
                    expected_sequence=None,
                    actual_sequence=sequence,
                    symbol=symbol,
                    exchange=exchange,
                ))
                continue

            last_seq = int(state.get(b'last_seq', 0))
            expected_seq = last_seq + 1

            if sequence == expected_seq:
                # Normal
                pipe.hset(key, self.FIELD_LAST_SEQ, sequence)
                pipe.hset(key, self.FIELD_LAST_TS, ts_value)
                results.append(SequenceResult(
                    event_type=SequenceEventType.NORMAL,
                    expected_sequence=expected_seq,
                    actual_sequence=sequence,
                    symbol=symbol,
                    exchange=exchange,
                ))
            elif sequence > expected_seq:
                # Gap
                gap_size = sequence - expected_seq
                gap_count = int(state.get(b'gap_count', 0)) + 1
                pipe.hset(key, mapping={
                    self.FIELD_LAST_SEQ: sequence,
                    self.FIELD_LAST_TS: ts_value,
                    self.FIELD_GAP_COUNT: gap_count,
                })
                results.append(SequenceResult(
                    event_type=SequenceEventType.GAP,
                    expected_sequence=expected_seq,
                    actual_sequence=sequence,
                    gap_size=gap_size,
                    symbol=symbol,
                    exchange=exchange,
                ))
            else:
                # Out of order or duplicate
                event_type = SequenceEventType.DUPLICATE if sequence == last_seq else SequenceEventType.OUT_OF_ORDER
                results.append(SequenceResult(
                    event_type=event_type,
                    expected_sequence=expected_seq,
                    actual_sequence=sequence,
                    symbol=symbol,
                    exchange=exchange,
                ))

        # Execute batch updates
        pipe.execute()

        return results

    def _set_state(self, key: str, sequence: int, timestamp: str):
        """Set state for a symbol."""
        self.redis.hset(key, mapping={
            self.FIELD_LAST_SEQ: sequence,
            self.FIELD_LAST_TS: timestamp,
        })
        self.redis.expire(key, self.key_ttl)

    def _record_gap(self, key: str, sequence: int, timestamp: str, gap_size: int):
        """Record a gap event."""
        self.redis.hset(key, mapping={
            self.FIELD_LAST_SEQ: sequence,
            self.FIELD_LAST_TS: timestamp,
        })
        self.redis.hincrby(key, self.FIELD_GAP_COUNT, 1)

        METRICS['k2_sequence_gaps_detected_total'].labels(
            service='k2-platform',
            environment='dev',
            component='sequence_tracker',
        ).inc()

    def _record_reset(self, key: str, sequence: int, timestamp: str):
        """Record a reset event."""
        self.redis.hset(key, mapping={
            self.FIELD_LAST_SEQ: sequence,
            self.FIELD_LAST_TS: timestamp,
            self.FIELD_GAP_COUNT: 0,  # Reset gap count on session reset
        })
        self.redis.hincrby(key, self.FIELD_RESET_COUNT, 1)

    def _log_gap(
        self,
        exchange: str,
        symbol: str,
        expected: int,
        actual: int,
        gap_size: int,
    ):
        """Log gap based on severity."""
        if gap_size >= self.GAP_THRESHOLD_HALT:
            logger.error(
                "Critical sequence gap",
                exchange=exchange,
                symbol=symbol,
                expected=expected,
                actual=actual,
                gap_size=gap_size,
            )
        elif gap_size >= self.GAP_THRESHOLD_ALERT:
            logger.warning(
                "Large sequence gap",
                exchange=exchange,
                symbol=symbol,
                expected=expected,
                actual=actual,
                gap_size=gap_size,
            )
        elif gap_size >= self.GAP_THRESHOLD_LOG:
            logger.info(
                "Sequence gap detected",
                exchange=exchange,
                symbol=symbol,
                expected=expected,
                actual=actual,
                gap_size=gap_size,
            )

    def get_stats(self, exchange: str, symbol: str) -> dict:
        """Get statistics for a symbol."""
        key = self._make_key(exchange, symbol)
        state = self.redis.hgetall(key)

        if not state:
            return {'exists': False}

        return {
            'exists': True,
            'last_sequence': int(state.get(b'last_seq', 0)),
            'last_timestamp': state.get(b'last_ts', b'').decode(),
            'gap_count': int(state.get(b'gap_count', 0)),
            'reset_count': int(state.get(b'reset_count', 0)),
        }

    def clear_symbol(self, exchange: str, symbol: str):
        """Clear state for a symbol (for testing)."""
        key = self._make_key(exchange, symbol)
        self.redis.delete(key)

    def clear_all(self):
        """Clear all sequence tracking state (for testing)."""
        pattern = f"{self.KEY_PREFIX}:*"
        keys = self.redis.keys(pattern)
        if keys:
            self.redis.delete(*keys)
        logger.warning("Cleared all sequence tracking state")
```

### 2. Docker Compose Redis Service

Add to `docker-compose.yml`:

```yaml
  redis:
    image: redis:7-alpine
    container_name: k2-redis
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - k2-network

volumes:
  redis-data:
```

### 3. Configuration Update

Add Redis configuration to `src/k2/common/config.py`.

### 4. Unit Tests

Create `tests/unit/test_redis_sequence_tracker.py` with 15+ tests.

---

## Validation

### Acceptance Criteria

1. [ ] `src/k2/ingestion/redis_sequence_tracker.py` created
2. [ ] Redis service added to Docker Compose
3. [ ] Batch pipelining implemented
4. [ ] Gap detection working
5. [ ] Metrics exposed
6. [ ] 15+ unit tests passing
7. [ ] Integration tests passing

### Verification Commands

```bash
# Start Redis
docker compose up -d redis

# Run tests
pytest tests/unit/test_redis_sequence_tracker.py -v
pytest tests/integration/test_redis_sequence_tracker.py -v

# Test manually
python -c "
import redis
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker

r = redis.Redis()
tracker = RedisSequenceTracker(r)
result = tracker.check_sequence('asx', 'BHP', 1000)
print(f'Result: {result}')
"
```

---

## Demo Talking Points

> "The original sequence tracker uses a Python dictionary.
> That's fine for a demo, but at 10K symbols with 10K updates per second,
> GIL contention causes 10-50ms latency spikes.
>
> This Redis-backed implementation solves that:
> - No GIL contention (Redis is external)
> - State survives process restarts
> - Pipelining handles 100+ checks per round-trip
>
> Watch this [show Redis CLI]:
> We're tracking BHP sequence 1001, then 1005—gap detected.
> The gap is logged and metrics are incremented."

---

**Last Updated**: 2026-01-11
**Status**: ⬜ Not Started

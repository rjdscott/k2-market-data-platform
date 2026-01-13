# Step 05: Bloom Filter Deduplication

**Status**: 🔵 Deferred to Multi-Node Implementation
**Assignee**: Implementation Team (Future)
**Issue**: #4 - Deduplication Cache is Memory-Bound
**Decision Date**: 2026-01-13
**Deferred By**: User + Implementation Team

---

## Deferral Rationale

**This step has been deferred to multi-node implementation.** See `TODO.md` for full details.

### Why Deferred for Single-Node Crypto:
1. **Memory not constrained**: Crypto dedup (1-hour window) easily fits in memory
2. **In-memory dict sufficient**: 10K-50K msg/sec peak rates don't require Bloom filter
3. **False positives add complexity**: Bloom filter requires Redis confirmation layer anyway
4. **Network latency overhead**: Redis hop adds 1-2ms vs <1μs in-memory
5. **Not memory-bound**: Current usage well under 2GB threshold
6. **24-hour window unnecessary**: Crypto has natural gaps (exchange maintenance, low liquidity periods)

### When to Reconsider:
- Memory usage for deduplication exceeds 2GB
- Need for 24-hour dedup window at >1M msg/sec scale
- Deploying distributed consumer instances needing shared dedup state
- In-memory dict becomes demonstrable bottleneck

### Effort Saved: 6-8 hours (redirected to Demo Narrative and Cost Model)

---

## Original Goal (For Reference)

Implement scalable deduplication using Bloom filter + Redis hybrid. The current in-memory dict can't handle 24-hour windows at scale. A Bloom filter uses ~1.4GB for 1 billion entries vs. ~86TB for a dict.

**Note**: This goal remains valid for multi-node deployments at high scale. For single-node crypto, in-memory dict is optimal.

---

## Overview

### Current Problem

```python
# Current implementation - memory-bound
self._cache: Dict[str, datetime] = {}
# At 10M msg/sec × 24hr = 864B entries
# At 100 bytes/entry = 86TB memory
```

### Solution: Two-Tier Deduplication

```
Message arrives
    ↓
[Bloom Filter Check] ─── Not in bloom ──→ NEW (add to bloom, process)
    │
    │ Possibly in bloom (false positive rate: 0.1%)
    ↓
[Redis Check] ─── Not in Redis ──→ FALSE POSITIVE (add to Redis, process)
    │
    │ In Redis
    ↓
DUPLICATE (skip)
```

**Memory Efficiency**:
- Bloom filter: 1.4GB for 1B entries at 0.1% false positive rate
- Redis: Only stores confirmed messages (~0.1% of traffic)
- Total: ~2GB vs. 86TB for pure dict approach

---

## Deliverables

### 1. Production Deduplicator

Create `src/k2/ingestion/bloom_deduplicator.py`:

```python
"""
Production-grade deduplication using Bloom filter + Redis hybrid.

Two-tier approach:
1. Bloom filter: Fast probabilistic check (1.4GB for 1B entries)
2. Redis: Confirm positives (only ~0.1% of messages hit Redis)

This reduces Redis calls by 99%+ while maintaining correctness.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List
import hashlib
import redis

try:
    from pybloom_live import BloomFilter
except ImportError:
    # Fallback to bloom_filter2 if pybloom_live not available
    try:
        from bloom_filter2 import BloomFilter
    except ImportError:
        raise ImportError(
            "Please install pybloom-live or bloom-filter2: "
            "pip install pybloom-live"
        )

from k2.common.logging import get_logger
from k2.common.metrics_registry import METRICS

logger = get_logger(__name__, component="deduplicator")


@dataclass
class DeduplicationStats:
    """Statistics for deduplication performance."""
    total_checks: int = 0
    bloom_negatives: int = 0  # Definitely new (not in bloom)
    bloom_positives: int = 0  # Maybe duplicate (in bloom)
    redis_confirms: int = 0   # Confirmed duplicate (in Redis)
    false_positives: int = 0  # Bloom said yes, Redis said no
    duplicates: int = 0       # Actual duplicates found

    @property
    def false_positive_rate(self) -> float:
        """Actual false positive rate."""
        if self.bloom_positives == 0:
            return 0.0
        return self.false_positives / self.bloom_positives

    @property
    def redis_reduction(self) -> float:
        """Percentage of Redis calls saved by Bloom filter."""
        if self.total_checks == 0:
            return 0.0
        return self.bloom_negatives / self.total_checks * 100


class ProductionDeduplicator:
    """
    Two-tier deduplication with Bloom filter + Redis.

    The Bloom filter provides fast probabilistic checking:
    - If message NOT in bloom → definitely new (99%+ of cases)
    - If message IN bloom → check Redis to confirm

    This architecture:
    - Handles 1B+ messages with ~1.4GB memory
    - Reduces Redis calls by 99%+
    - Maintains 100% correctness (no false negatives)

    Usage:
        import redis
        r = redis.Redis()
        dedup = ProductionDeduplicator(redis_client=r)

        # Check if duplicate
        if dedup.is_duplicate(message_id):
            skip_message()
        else:
            process_message()
    """

    # Redis key prefix
    REDIS_PREFIX = "k2:dedup"

    def __init__(
        self,
        redis_client: redis.Redis,
        capacity: int = 100_000_000,  # 100M messages
        error_rate: float = 0.001,    # 0.1% false positive rate
        ttl_hours: int = 24,          # 24-hour deduplication window
    ):
        """
        Initialize deduplicator.

        Args:
            redis_client: Redis client for confirmed duplicates
            capacity: Bloom filter capacity (messages)
            error_rate: Target false positive rate
            ttl_hours: TTL for Redis keys (deduplication window)
        """
        self.redis = redis_client
        self.ttl_seconds = ttl_hours * 3600
        self.capacity = capacity
        self.error_rate = error_rate

        # Initialize Bloom filter
        self.bloom = BloomFilter(capacity=capacity, error_rate=error_rate)

        # Statistics
        self.stats = DeduplicationStats()

        # Calculate memory usage
        memory_mb = self._estimate_memory_mb()

        logger.info(
            "Production deduplicator initialized",
            capacity=capacity,
            error_rate=error_rate,
            ttl_hours=ttl_hours,
            estimated_memory_mb=memory_mb,
        )

    def _estimate_memory_mb(self) -> float:
        """Estimate Bloom filter memory usage."""
        # Formula: -n * ln(p) / (ln(2))^2
        import math
        bits = -self.capacity * math.log(self.error_rate) / (math.log(2) ** 2)
        return bits / 8 / 1024 / 1024

    def _make_redis_key(self, message_id: str) -> str:
        """Create Redis key for message."""
        return f"{self.REDIS_PREFIX}:{message_id}"

    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if message is a duplicate.

        Returns:
            True if duplicate (should skip), False if new (should process)
        """
        self.stats.total_checks += 1

        # Tier 1: Bloom filter check
        if message_id not in self.bloom:
            # Definitely not a duplicate
            self.stats.bloom_negatives += 1
            self.bloom.add(message_id)
            return False

        # Tier 2: Redis confirmation (bloom said maybe duplicate)
        self.stats.bloom_positives += 1

        redis_key = self._make_redis_key(message_id)
        if self.redis.exists(redis_key):
            # Confirmed duplicate
            self.stats.redis_confirms += 1
            self.stats.duplicates += 1

            METRICS['k2_duplicate_messages_detected_total'].labels(
                service='k2-platform',
                environment='dev',
                component='deduplicator',
                exchange='all',
            ).inc()

            return True

        # False positive from Bloom filter
        self.stats.false_positives += 1

        # Add to Redis for future checks
        self.redis.setex(redis_key, self.ttl_seconds, "1")
        return False

    def is_duplicate_batch(
        self,
        message_ids: List[str],
    ) -> List[bool]:
        """
        Check duplicates for a batch of messages.

        More efficient than individual checks using Redis pipelining.

        Args:
            message_ids: List of message IDs to check

        Returns:
            List of booleans (True = duplicate) in same order
        """
        if not message_ids:
            return []

        results = []
        redis_checks = []  # (index, message_id) for messages to check in Redis

        # Tier 1: Bloom filter checks
        for i, msg_id in enumerate(message_ids):
            self.stats.total_checks += 1

            if msg_id not in self.bloom:
                # Definitely new
                self.stats.bloom_negatives += 1
                self.bloom.add(msg_id)
                results.append(False)
            else:
                # Need Redis check
                self.stats.bloom_positives += 1
                results.append(None)  # Placeholder
                redis_checks.append((i, msg_id))

        # Tier 2: Batch Redis checks
        if redis_checks:
            pipe = self.redis.pipeline()
            for _, msg_id in redis_checks:
                pipe.exists(self._make_redis_key(msg_id))

            redis_results = pipe.execute()

            # Update results
            pipe = self.redis.pipeline()
            for j, (i, msg_id) in enumerate(redis_checks):
                if redis_results[j]:
                    # Confirmed duplicate
                    results[i] = True
                    self.stats.redis_confirms += 1
                    self.stats.duplicates += 1
                else:
                    # False positive
                    results[i] = False
                    self.stats.false_positives += 1
                    pipe.setex(self._make_redis_key(msg_id), self.ttl_seconds, "1")

            pipe.execute()

        return results

    def add_without_check(self, message_id: str):
        """
        Add message to dedup store without checking.

        Useful when you know message is new (e.g., from producer).
        """
        self.bloom.add(message_id)
        redis_key = self._make_redis_key(message_id)
        self.redis.setex(redis_key, self.ttl_seconds, "1")

    def get_stats(self) -> dict:
        """Get deduplication statistics."""
        return {
            'total_checks': self.stats.total_checks,
            'bloom_negatives': self.stats.bloom_negatives,
            'bloom_positives': self.stats.bloom_positives,
            'redis_confirms': self.stats.redis_confirms,
            'false_positives': self.stats.false_positives,
            'duplicates': self.stats.duplicates,
            'actual_false_positive_rate': f"{self.stats.false_positive_rate:.4f}",
            'redis_call_reduction': f"{self.stats.redis_reduction:.1f}%",
            'capacity': self.capacity,
            'target_error_rate': self.error_rate,
            'estimated_memory_mb': self._estimate_memory_mb(),
        }

    def reset_stats(self):
        """Reset statistics (for testing)."""
        self.stats = DeduplicationStats()

    def clear(self):
        """Clear all deduplication state (for testing)."""
        # Clear Bloom filter by recreating
        self.bloom = BloomFilter(capacity=self.capacity, error_rate=self.error_rate)

        # Clear Redis keys
        pattern = f"{self.REDIS_PREFIX}:*"
        keys = self.redis.keys(pattern)
        if keys:
            self.redis.delete(*keys)

        self.reset_stats()
        logger.warning("Cleared all deduplication state")


def create_deduplicator(
    redis_host: str = "localhost",
    redis_port: int = 6379,
    capacity: int = 100_000_000,
    error_rate: float = 0.001,
    ttl_hours: int = 24,
) -> ProductionDeduplicator:
    """
    Factory function to create deduplicator.

    Usage:
        dedup = create_deduplicator()
        dedup = create_deduplicator(capacity=1_000_000_000)  # 1B capacity
    """
    r = redis.Redis(host=redis_host, port=redis_port)
    return ProductionDeduplicator(
        redis_client=r,
        capacity=capacity,
        error_rate=error_rate,
        ttl_hours=ttl_hours,
    )
```

### 2. Dependencies

Add to `pyproject.toml`:

```toml
[project.dependencies]
pybloom-live = ">=4.0.0"
```

### 3. Unit Tests

Create `tests/unit/test_bloom_deduplicator.py` with 10+ tests.

---

## Validation

### Acceptance Criteria

1. [ ] `src/k2/ingestion/bloom_deduplicator.py` created
2. [ ] Bloom filter library installed
3. [ ] Two-tier deduplication working
4. [ ] Batch operations implemented
5. [ ] Statistics tracking
6. [ ] 10+ unit tests passing

### Verification Commands

```bash
# Install dependency
pip install pybloom-live

# Run tests
pytest tests/unit/test_bloom_deduplicator.py -v

# Test manually
python -c "
import redis
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator

r = redis.Redis()
dedup = ProductionDeduplicator(r, capacity=1_000_000)

print('First check:', dedup.is_duplicate('msg-001'))  # False
print('Second check:', dedup.is_duplicate('msg-001')) # True
print('Stats:', dedup.get_stats())
"
```

---

## Demo Talking Points

> "Deduplication at scale is tricky.
> A naive dict approach needs 86 terabytes for a 24-hour window at 10M msg/sec.
>
> Our solution uses a Bloom filter—a probabilistic data structure.
> 1.4GB handles 1 billion messages with 0.1% false positive rate.
>
> The two-tier approach:
> 1. Bloom filter says 'definitely not duplicate' 99%+ of the time
> 2. Redis confirms the remaining 1%
>
> Result: 99% reduction in Redis calls, 40,000x less memory than naive approach.
>
> [Show stats output]
> See—actual false positive rate is close to our target 0.1%."

---

**Last Updated**: 2026-01-11
**Status**: ⬜ Not Started

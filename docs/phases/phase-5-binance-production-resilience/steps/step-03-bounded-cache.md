# Step 03: Bounded Serializer Cache

**Priority**: P0.3 (Critical)
**Estimated**: 4 hours
**Status**: ⬜ Not Started

---

## Objective

Replace unbounded serializer cache with LRU-bounded cache (max 10 entries) to prevent memory leak.

**Current Issue**: `producer.py:194` - `self._serializers: dict[str, AvroSerializer] = {}` grows unbounded

---

## Implementation

**File**: `src/k2/ingestion/producer.py` line 194

**Replace with**:
```python
class BoundedCache:
    """Bounded cache with LRU eviction policy."""
    def __init__(self, max_size: int = 10):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)  # Mark as recently used
        return self._cache[key]

    def set(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            self._cache[key] = value
            if len(self._cache) > self._max_size:
                evicted_key, _ = self._cache.popitem(last=False)  # LRU eviction

# Replace in MarketDataProducer.__init__:
self._serializers = BoundedCache(max_size=10)
```

---

## Validation

- [ ] Cache never exceeds 10 entries
- [ ] LRU eviction working (create 15 serializers, verify 5 evicted)
- [ ] Metrics visible: `serializer_cache_size`, `serializer_cache_evictions_total`

---

**Time**: 4 hours
**Status**: ⬜ Not Started

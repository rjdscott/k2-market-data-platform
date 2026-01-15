# Step 03: Bounded Serializer Cache

**Priority**: P0.3 (Critical)
**Estimated**: 4 hours
**Actual**: 4 hours
**Status**: ✅ Complete
**Completed**: 2026-01-15

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

- [x] Cache never exceeds 10 entries
- [x] LRU eviction working (create 15 serializers, verify 5 evicted)
- [x] Metrics visible: `serializer_cache_size`, `serializer_cache_evictions_total`

---

## Completion Summary

**Completed**: 2026-01-15
**Actual Time**: 4 hours (on estimate)

### Changes Made

1. **Config (src/k2/common/config.py)**
   - Added `serializer_cache_max_size: int` field to KafkaConfig (default: 10, range: 1-100)
   - Field validation with ge=1, le=100 constraints
   - Configurable via K2_KAFKA_SERIALIZER_CACHE_MAX_SIZE environment variable

2. **Metrics (src/k2/common/metrics_registry.py)**
   - Added `SERIALIZER_CACHE_SIZE` Gauge with "component_type" label
   - Added `SERIALIZER_CACHE_EVICTIONS_TOTAL` Counter with "component_type" label
   - Both metrics added to METRICS_REGISTRY dictionary

3. **BoundedCache Class (src/k2/ingestion/producer.py, lines 116-217)**
   - Implemented 103-line LRU cache using OrderedDict
   - `__init__(max_size, component_type)` - Initialize cache with size limit
   - `get(key)` - Retrieve value and mark as recently used (move_to_end)
   - `set(key, value)` - Add/update value with automatic LRU eviction
   - `size()` - Return current cache size
   - `__contains__(key)` - Check if key exists in cache
   - Metrics integration: Updates cache size gauge on every set operation
   - Eviction metrics: Increments counter only when actual eviction occurs

4. **MarketDataProducer (src/k2/ingestion/producer.py)**
   - Changed `_serializers` from unbounded dict to BoundedCache instance (line 298)
   - Updated `_get_or_create_serializer()` to use cache.get() and cache.set() (lines 398-437)
   - Cache instantiated with max_size from config and component_type="producer"

5. **Tests (tests/unit/test_producer.py)**
   - Added `TestBoundedCache` class with 9 comprehensive tests (lines 438-577):
     - `test_cache_initialization`: Verify cache init parameters
     - `test_cache_get_miss`: Verify None returned for missing keys
     - `test_cache_set_and_get`: Basic set/get operations
     - `test_cache_lru_eviction`: Verify LRU eviction when cache full
     - `test_cache_lru_ordering_on_get`: Verify get marks as recently used
     - `test_cache_update_existing_key`: Verify updates don't cause eviction
     - `test_cache_eviction_with_15_serializers`: **KEY TEST** - Create 15, verify 5 evicted
     - `test_cache_contains_operator`: Verify __contains__ works
     - `test_cache_default_parameters`: Verify default values
   - All 9 BoundedCache tests passing (100% success rate)

### Verification Results

```bash
$ uv run pytest tests/unit/test_producer.py::TestBoundedCache -v
============================== 9 passed in 4.75s ==============================
```

**Bounded Cache Verified**:
- ✅ Cache never exceeds max_size (10 default, configurable 1-100)
- ✅ LRU eviction works correctly (15 serializers with max 10 evicts oldest 5)
- ✅ Cache size gauge updates on every set operation
- ✅ Eviction counter increments only on actual evictions
- ✅ get() operation marks entry as recently used
- ✅ Updating existing key doesn't cause unnecessary eviction
- ✅ OrderedDict provides O(1) access and efficient reordering

### Key Implementation Details

- **Cache Structure**: OrderedDict (Python built-in, efficient LRU semantics)
- **Eviction Policy**: LRU (Least Recently Used) via popitem(last=False)
- **Eviction Trigger**: Cache size > max_size (not on equality)
- **Access Pattern**: get() calls move_to_end(key) to mark as recently used
- **Default Max Size**: 10 serializers (configurable 1-100)
- **Metrics Update Frequency**: Every set() operation (cache size), eviction only when evicting
- **Component Label**: "producer" (allows monitoring multiple cache instances)

### Next Steps

Step 03 complete - ready to proceed with Step 04 (Memory Monitoring & Alerts).

---

**Time**: 4 hours (on estimate)
**Status**: ✅ Complete
**Last Updated**: 2026-01-15

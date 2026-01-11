# Step 09: Final Validation

**Status**: ⬜ Not Started
**Assignee**: Implementation Team
**Issue**: All - Integration & Validation

---

## Dependencies
- **Requires**: Steps 01-08 complete
- **Blocks**: Phase 2 completion

---

## Goal

End-to-end validation of all Phase 2 enhancements. Ensure everything works together and the demo is ready for Principal Data Engineer review.

---

## Validation Checklist

### Documentation Validation

- [ ] **README Positioning**
  - [ ] "Platform Positioning" section exists
  - [ ] Clear IS/ISN'T statements
  - [ ] Tiered architecture mentioned

- [ ] **Platform Positioning Document**
  - [ ] `docs/architecture/platform-positioning.md` exists
  - [ ] Contains L1/L2/L3 tier explanation
  - [ ] Latency budget table present

- [ ] **Cost Model**
  - [ ] `docs/operations/cost-model.md` exists
  - [ ] Covers baseline, 100x, 1000x scales
  - [ ] Optimization strategies documented

### Code Validation

- [ ] **Circuit Breaker**
  - [ ] `src/k2/common/circuit_breaker.py` exists
  - [ ] All 5 degradation levels implemented
  - [ ] Hysteresis working
  - [ ] Prometheus metrics exposed

- [ ] **Load Shedder**
  - [ ] `src/k2/common/load_shedder.py` exists
  - [ ] Priority-based filtering working
  - [ ] Tier classification correct

- [ ] **Redis Sequence Tracker**
  - [ ] `src/k2/ingestion/redis_sequence_tracker.py` exists
  - [ ] Pipelining implemented
  - [ ] Gap detection working
  - [ ] Metrics exposed

- [ ] **Bloom Deduplicator**
  - [ ] `src/k2/ingestion/bloom_deduplicator.py` exists
  - [ ] Two-tier deduplication working
  - [ ] False positive rate acceptable (~0.1%)
  - [ ] Statistics tracking

- [ ] **Hybrid Query Engine**
  - [ ] `src/k2/query/hybrid_engine.py` exists
  - [ ] `src/k2/query/kafka_tail.py` exists
  - [ ] Merge and deduplicate working
  - [ ] API integration complete

- [ ] **Enhanced Demo**
  - [ ] `scripts/demo.py` rewritten
  - [ ] 6 sections implemented
  - [ ] `--quick` mode works
  - [ ] `--section` mode works

### Test Validation

- [ ] **Unit Tests**
  - [ ] `test_circuit_breaker.py` - 15+ tests passing
  - [ ] `test_redis_sequence_tracker.py` - 15+ tests passing
  - [ ] `test_bloom_deduplicator.py` - 10+ tests passing
  - [ ] `test_hybrid_engine.py` - 15+ tests passing

- [ ] **Integration Tests**
  - [ ] Redis integration tests passing
  - [ ] Hybrid query integration tests passing
  - [ ] Full pipeline test passing

### Infrastructure Validation

- [ ] **Docker Services**
  - [ ] Redis service added to docker-compose.yml
  - [ ] All services healthy
  - [ ] No port conflicts

- [ ] **Grafana**
  - [ ] Degradation Level panel added
  - [ ] Dashboard loads without errors

---

## Validation Commands

### 1. Documentation Check

```bash
# Check all required docs exist
echo "=== Documentation ==="
ls -la docs/architecture/platform-positioning.md
ls -la docs/operations/cost-model.md
grep "Platform Positioning" README.md | head -1
```

### 2. Code Import Check

```bash
# Check all modules import correctly
echo "=== Module Imports ==="
python -c "
from k2.common.circuit_breaker import CircuitBreaker, DegradationLevel
from k2.common.load_shedder import LoadShedder
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator
from k2.query.hybrid_engine import HybridQueryEngine
from k2.query.kafka_tail import KafkaTail
print('All imports successful')
"
```

### 3. Test Suite

```bash
# Run all Phase 2 tests
echo "=== Unit Tests ==="
pytest tests/unit/test_circuit_breaker.py -v
pytest tests/unit/test_redis_sequence_tracker.py -v
pytest tests/unit/test_bloom_deduplicator.py -v
pytest tests/unit/test_hybrid_engine.py -v

echo "=== Integration Tests ==="
pytest tests/integration/ -v -m "phase2"

echo "=== Test Summary ==="
pytest tests/ --collect-only | grep "test session starts"
```

### 4. Infrastructure Check

```bash
# Check all services running
echo "=== Docker Services ==="
docker compose ps

# Check Redis specifically
docker exec k2-redis redis-cli ping
```

### 5. Demo Validation

```bash
# Run quick demo
echo "=== Demo Quick Mode ==="
python scripts/demo.py --quick

# Time full demo
echo "=== Demo Full Mode (timed) ==="
time python scripts/demo.py
```

### 6. Metrics Check

```bash
# Check metrics exposed
echo "=== Prometheus Metrics ==="
curl -s http://localhost:8000/metrics | grep k2_degradation
curl -s http://localhost:8000/metrics | grep k2_sequence
curl -s http://localhost:8000/metrics | grep k2_duplicate
```

### 7. End-to-End Test

```bash
# Full pipeline test
echo "=== E2E Test ==="

# 1. Start services
docker compose up -d

# 2. Initialize
python scripts/init_infra.py

# 3. Load sample data
python -m k2.ingestion.batch_loader \
  data/sample/trades/bhp_trades.csv \
  --exchange asx --asset-class equities

# 4. Query via API
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&limit=5"

# 5. Check Grafana
echo "Open http://localhost:3000 and verify dashboard loads"
```

---

## Performance Benchmarks

### Circuit Breaker

```python
# Benchmark: check_and_degrade latency
import time
from k2.common.circuit_breaker import CircuitBreaker

cb = CircuitBreaker()
iterations = 10000

start = time.time()
for _ in range(iterations):
    cb.check_and_degrade(lag=10000, heap_pct=50.0)
elapsed = time.time() - start

print(f"Circuit breaker: {elapsed/iterations*1000:.3f}ms per check")
# Target: < 0.1ms per check
```

### Redis Sequence Tracker

```python
# Benchmark: batch check latency
import redis
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker

r = redis.Redis()
tracker = RedisSequenceTracker(r)

events = [('asx', f'SYM{i}', i*1000, None) for i in range(1000)]

start = time.time()
results = tracker.check_sequence_batch(events)
elapsed = time.time() - start

print(f"Redis batch check: {elapsed*1000:.1f}ms for 1000 events")
# Target: < 50ms for 1000 events
```

### Bloom Filter

```python
# Benchmark: deduplication check
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator

dedup = ProductionDeduplicator(redis_client=r, capacity=1_000_000)

# Add messages
for i in range(10000):
    dedup.is_duplicate(f"msg-{i}")

# Check stats
stats = dedup.get_stats()
print(f"False positive rate: {stats['actual_false_positive_rate']}")
print(f"Redis call reduction: {stats['redis_call_reduction']}")
# Target: FP rate < 0.2%, Redis reduction > 99%
```

---

## Acceptance Criteria Summary

| Category | Requirement | Status |
|----------|-------------|--------|
| **Documentation** | | |
| README positioning | Section exists | ⬜ |
| Platform positioning doc | File exists | ⬜ |
| Cost model doc | File exists | ⬜ |
| **Code** | | |
| Circuit breaker | Imports, works | ⬜ |
| Load shedder | Imports, works | ⬜ |
| Redis sequence tracker | Imports, works | ⬜ |
| Bloom deduplicator | Imports, works | ⬜ |
| Hybrid query engine | Imports, works | ⬜ |
| Enhanced demo | Runs, 6 sections | ⬜ |
| **Tests** | | |
| Unit tests | 50+ passing | ⬜ |
| Integration tests | All passing | ⬜ |
| **Infrastructure** | | |
| Redis service | Running | ⬜ |
| Grafana panel | Shows degradation | ⬜ |
| **Demo** | | |
| Quick mode | < 2 minutes | ⬜ |
| Full mode | ~10 minutes | ⬜ |

---

## Sign-Off

### Phase 2 Completion Checklist

- [ ] All 9 steps completed
- [ ] All unit tests passing (50+)
- [ ] All integration tests passing
- [ ] Demo runs end-to-end
- [ ] Documentation reviewed
- [ ] Code reviewed
- [ ] Ready for Principal Data Engineer review

### Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | | | |
| Reviewer | | | |

---

**Last Updated**: 2026-01-11
**Status**: ⬜ Not Started

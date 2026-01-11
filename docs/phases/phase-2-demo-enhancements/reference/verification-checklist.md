# Phase 2: Verification Checklist

**Last Updated**: 2026-01-12
**Status**: Active
**Phase**: Demo Enhancements

---

## Overview

This is a step-by-step verification checklist for validating Phase 2 completion. Use this before marking Phase 2 as complete and ready for Principal Data Engineer review.

---

## Pre-Verification Setup

### 1. Start All Services

```bash
# Start Docker services
docker compose up -d

# Verify all services are healthy
docker compose ps

# Expected output:
# k2-kafka         running
# k2-zookeeper     running
# k2-schema-registry running
# k2-postgres      running
# k2-minio         running
# k2-redis         running (NEW in Phase 2)
# k2-grafana       running
# k2-prometheus    running
```

### 2. Activate Environment

```bash
# Activate virtual environment
source .venv/bin/activate

# Verify Python path
python -c "import k2; print(k2.__file__)"
```

---

## Documentation Verification

### Step D1: README Positioning

```bash
# Check section exists
grep -A 5 "Platform Positioning" README.md
```

- [ ] "Platform Positioning" section exists
- [ ] Contains "K2 IS" statements
- [ ] Contains "K2 IS NOT" statements
- [ ] References tiered architecture
- [ ] Links to full positioning document

### Step D2: Platform Positioning Document

```bash
# Check file exists
ls -la docs/architecture/platform-positioning.md

# Verify content
grep -E "(L1|L2|L3)" docs/architecture/platform-positioning.md | head -5
```

- [ ] File exists at `docs/architecture/platform-positioning.md`
- [ ] Contains L1 Hot Path explanation (10μs latency)
- [ ] Contains L2 Warm Path explanation (10ms latency)
- [ ] Contains L3 Cold Path explanation (500ms latency)
- [ ] Latency budget table present
- [ ] ASCII architecture diagram present

### Step D3: Cost Model Document

```bash
# Check file exists
ls -la docs/operations/cost-model.md

# Verify sections
grep -E "(Baseline|100x|1000x|Optimization)" docs/operations/cost-model.md | head -5
```

- [ ] File exists at `docs/operations/cost-model.md`
- [ ] Baseline (development) costs documented
- [ ] 100x scale (1M msg/sec) costs documented
- [ ] 1000x scale (10M msg/sec) costs documented
- [ ] Cost breakdown by component present
- [ ] At least 5 optimization strategies documented
- [ ] Prometheus cost metrics defined

---

## Code Verification

### Step C1: Circuit Breaker Module

```bash
# Check file exists
ls -la src/k2/common/circuit_breaker.py

# Verify imports work
python -c "
from k2.common.circuit_breaker import CircuitBreaker, DegradationLevel
print('DegradationLevel.NORMAL =', DegradationLevel.NORMAL)
print('DegradationLevel.CIRCUIT_BREAK =', DegradationLevel.CIRCUIT_BREAK)
cb = CircuitBreaker()
print('Initial level:', cb.current_level)
print('Import successful')
"
```

- [ ] File exists at `src/k2/common/circuit_breaker.py`
- [ ] `DegradationLevel` enum has 5 levels
- [ ] `CircuitBreaker` class importable
- [ ] `check_and_degrade()` method exists
- [ ] `should_process_symbol()` method exists
- [ ] Hysteresis logic implemented
- [ ] Prometheus metrics exposed

### Step C2: Load Shedder Module

```bash
# Check file exists
ls -la src/k2/common/load_shedder.py

# Verify imports work
python -c "
from k2.common.load_shedder import LoadShedder, SymbolTier
print('SymbolTier.INDEX =', SymbolTier.INDEX)
ls = LoadShedder()
print('Import successful')
"
```

- [ ] File exists at `src/k2/common/load_shedder.py`
- [ ] `SymbolTier` enum defined
- [ ] `LoadShedder` class importable
- [ ] Priority-based filtering implemented
- [ ] Integration with circuit breaker levels

### Step C3: Redis Sequence Tracker

```bash
# Check file exists
ls -la src/k2/ingestion/redis_sequence_tracker.py

# Verify imports work (without Redis connection)
python -c "
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker, SequenceResult
print('SequenceResult enum:', list(SequenceResult))
print('Import successful')
"

# Test with Redis
python -c "
import redis
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker

r = redis.Redis()
tracker = RedisSequenceTracker(r)
result = tracker.check_sequence('asx', 'BHP', 1000)
print('Sequence check result:', result)
print('Test successful')
"
```

- [ ] File exists at `src/k2/ingestion/redis_sequence_tracker.py`
- [ ] `RedisSequenceTracker` class importable
- [ ] `check_sequence()` method exists
- [ ] `check_sequence_batch()` method exists
- [ ] Redis pipelining implemented
- [ ] Gap detection working
- [ ] Gap statistics tracked

### Step C4: Bloom Deduplicator

```bash
# Check file exists
ls -la src/k2/ingestion/bloom_deduplicator.py

# Verify imports work
python -c "
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator
print('Import successful')
"

# Test with Redis
python -c "
import redis
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator

r = redis.Redis()
dedup = ProductionDeduplicator(r, capacity=1_000_000)
print('First check:', dedup.is_duplicate('test-msg-001'))  # Should be False
print('Second check:', dedup.is_duplicate('test-msg-001')) # Should be True
print('Stats:', dedup.get_stats())
"
```

- [ ] File exists at `src/k2/ingestion/bloom_deduplicator.py`
- [ ] `ProductionDeduplicator` class importable
- [ ] `is_duplicate()` method exists
- [ ] `is_duplicate_batch()` method exists
- [ ] Two-tier deduplication working
- [ ] Statistics tracking working

### Step C5: Hybrid Query Engine

```bash
# Check files exist
ls -la src/k2/query/hybrid_engine.py
ls -la src/k2/query/kafka_tail.py

# Verify imports work
python -c "
from k2.query.hybrid_engine import HybridQueryEngine, create_hybrid_engine
from k2.query.kafka_tail import KafkaTail, TailMessage
print('Import successful')
"
```

- [ ] `src/k2/query/hybrid_engine.py` exists
- [ ] `src/k2/query/kafka_tail.py` exists
- [ ] `HybridQueryEngine` class importable
- [ ] `KafkaTail` class importable
- [ ] `query_recent_trades()` method exists
- [ ] Merge and deduplicate logic implemented

### Step C6: Enhanced Demo Script

```bash
# Check demo script exists
ls -la scripts/demo.py

# Verify --help works
python scripts/demo.py --help

# Expected output should show:
# --quick     Run quick demo
# --section   Run specific section
```

- [ ] `scripts/demo.py` restructured
- [ ] `--quick` mode implemented
- [ ] `--section` mode implemented
- [ ] 6 demo sections present
- [ ] Architecture section exists
- [ ] Degradation section exists
- [ ] Cost awareness section exists

---

## Test Verification

### Step T1: Run Unit Tests

```bash
# Run all Phase 2 unit tests
pytest tests/unit/test_circuit_breaker.py -v --tb=short
pytest tests/unit/test_redis_sequence_tracker.py -v --tb=short
pytest tests/unit/test_bloom_deduplicator.py -v --tb=short
pytest tests/unit/test_hybrid_engine.py -v --tb=short

# Count tests
pytest tests/unit/test_circuit_breaker.py --collect-only | grep "test session starts"
pytest tests/unit/test_redis_sequence_tracker.py --collect-only | grep "test session starts"
pytest tests/unit/test_bloom_deduplicator.py --collect-only | grep "test session starts"
pytest tests/unit/test_hybrid_engine.py --collect-only | grep "test session starts"
```

- [ ] `test_circuit_breaker.py` - 15+ tests passing
- [ ] `test_redis_sequence_tracker.py` - 15+ tests passing
- [ ] `test_bloom_deduplicator.py` - 10+ tests passing
- [ ] `test_hybrid_engine.py` - 15+ tests passing
- [ ] Total: 55+ unit tests passing

### Step T2: Run Integration Tests

```bash
# Ensure Redis is running
docker exec k2-redis redis-cli ping

# Run integration tests
pytest tests/integration/ -v -m "phase2" --tb=short
```

- [ ] Redis integration tests passing
- [ ] Hybrid query integration tests passing
- [ ] Full pipeline tests passing
- [ ] Total: 10+ integration tests passing

### Step T3: Test Coverage

```bash
# Run with coverage
pytest tests/unit/test_circuit_breaker.py \
       tests/unit/test_redis_sequence_tracker.py \
       tests/unit/test_bloom_deduplicator.py \
       tests/unit/test_hybrid_engine.py \
       --cov=src/k2/common \
       --cov=src/k2/ingestion \
       --cov=src/k2/query \
       --cov-report=term-missing

# Coverage should be > 85%
```

- [ ] Circuit breaker coverage > 90%
- [ ] Sequence tracker coverage > 90%
- [ ] Bloom deduplicator coverage > 90%
- [ ] Hybrid engine coverage > 85%
- [ ] Overall coverage > 85%

---

## Infrastructure Verification

### Step I1: Redis Service

```bash
# Check Redis is in docker-compose.yml
grep -A 10 "redis:" docker-compose.yml

# Verify Redis is running
docker compose ps | grep redis

# Test Redis connectivity
docker exec k2-redis redis-cli ping
# Expected: PONG

# Check Redis from Python
python -c "
import redis
r = redis.Redis(host='localhost', port=6379)
print('Redis ping:', r.ping())
"
```

- [ ] Redis service defined in `docker-compose.yml`
- [ ] Redis container running and healthy
- [ ] Redis accessible from host
- [ ] No port conflicts (port 6379 available)

### Step I2: Grafana Dashboard

```bash
# Open Grafana
open http://localhost:3000

# Default credentials: admin/admin
```

- [ ] Grafana loads without errors
- [ ] K2 Platform dashboard loads
- [ ] Degradation Level panel present
- [ ] Panel shows current degradation level
- [ ] Phase 2 metrics visible (k2_degradation_level, k2_sequence_*, k2_duplicate_*)

### Step I3: Prometheus Metrics

```bash
# Check metrics endpoint
curl -s http://localhost:8000/metrics | grep k2_degradation
curl -s http://localhost:8000/metrics | grep k2_sequence
curl -s http://localhost:8000/metrics | grep k2_duplicate
```

- [ ] `k2_degradation_level` gauge present
- [ ] `k2_sequence_gaps_total` counter present
- [ ] `k2_duplicate_messages_detected_total` counter present
- [ ] All metrics have correct labels

---

## Demo Verification

### Step Demo1: Quick Mode

```bash
# Time the quick demo
time python scripts/demo.py --quick

# Should complete in < 2 minutes
```

- [ ] Quick demo completes without errors
- [ ] Duration < 2 minutes
- [ ] All critical functionality demonstrated
- [ ] Clear output for each section

### Step Demo2: Full Mode

```bash
# Time the full demo
time python scripts/demo.py

# Should complete in ~10 minutes
```

- [ ] Full demo completes without errors
- [ ] Duration approximately 10 minutes
- [ ] All 6 sections run
- [ ] Architecture context explained
- [ ] Ingestion demonstrated
- [ ] Storage demonstrated
- [ ] Monitoring demonstrated
- [ ] Query demonstrated
- [ ] Scaling story presented

### Step Demo3: Section Mode

```bash
# Test individual sections
python scripts/demo.py --section architecture
python scripts/demo.py --section ingestion
python scripts/demo.py --section degradation
python scripts/demo.py --section query
```

- [ ] `--section architecture` works
- [ ] `--section ingestion` works
- [ ] `--section degradation` works
- [ ] `--section query` works
- [ ] Each section runs independently

---

## Performance Verification

### Step P1: Circuit Breaker Performance

```bash
python -c "
import time
from k2.common.circuit_breaker import CircuitBreaker

cb = CircuitBreaker()
iterations = 10000

start = time.time()
for _ in range(iterations):
    cb.check_and_degrade(lag=10000, heap_pct=50.0)
elapsed = time.time() - start

latency_ms = elapsed / iterations * 1000
print(f'Circuit breaker latency: {latency_ms:.4f}ms per check')
print(f'Target: < 0.1ms')
print(f'PASS' if latency_ms < 0.1 else 'FAIL')
"
```

- [ ] Circuit breaker check < 0.1ms

### Step P2: Redis Sequence Tracker Performance

```bash
python -c "
import time
import redis
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker

r = redis.Redis()
tracker = RedisSequenceTracker(r)

events = [('asx', f'SYM{i}', i*1000, None) for i in range(1000)]

start = time.time()
results = tracker.check_sequence_batch(events)
elapsed = time.time() - start

print(f'Batch check latency: {elapsed*1000:.1f}ms for 1000 events')
print(f'Target: < 50ms')
print(f'PASS' if elapsed*1000 < 50 else 'FAIL')
"
```

- [ ] Batch check (1000 events) < 50ms

### Step P3: Bloom Filter Performance

```bash
python -c "
import time
import redis
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator

r = redis.Redis()
dedup = ProductionDeduplicator(r, capacity=1_000_000)

iterations = 10000

start = time.time()
for i in range(iterations):
    dedup.is_duplicate(f'msg-{i}')
elapsed = time.time() - start

latency_ms = elapsed / iterations * 1000
print(f'Dedup check latency: {latency_ms:.4f}ms per check')
print(f'Target: < 0.1ms')
print(f'PASS' if latency_ms < 0.1 else 'FAIL')

stats = dedup.get_stats()
print(f'Redis reduction: {stats[\"redis_call_reduction\"]}')
"
```

- [ ] Dedup check < 0.1ms
- [ ] Redis call reduction > 99%

---

## End-to-End Verification

### Step E2E: Full Pipeline Test

```bash
# 1. Start services
docker compose up -d

# 2. Initialize infrastructure
python scripts/init_infra.py

# 3. Load sample data
python -m k2.ingestion.batch_loader \
  data/sample/trades/bhp_trades.csv \
  --exchange asx --asset-class equities

# 4. Query via API
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&limit=5"

# 5. Check Grafana
open http://localhost:3000
```

- [ ] Services start without errors
- [ ] Infrastructure initializes
- [ ] Data loads successfully
- [ ] API query returns data
- [ ] Grafana dashboard shows metrics
- [ ] No errors in logs

---

## Final Sign-Off

### Phase 2 Completion Checklist

| Category | Items | Verified | Date |
|----------|-------|----------|------|
| Documentation | 6 items | ⬜ | |
| Code | 12 items | ⬜ | |
| Tests | 5 items | ⬜ | |
| Infrastructure | 6 items | ⬜ | |
| Demo | 6 items | ⬜ | |
| Performance | 3 items | ⬜ | |
| E2E | 6 items | ⬜ | |
| **Total** | **44 items** | ⬜ | |

### Approvals

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | | | |
| Code Reviewer | | | |
| Tech Lead | | | |

### Phase 2 Ready for Review

- [ ] All 44 verification items passing
- [ ] All 9 steps complete
- [ ] All tests passing
- [ ] Demo runs end-to-end
- [ ] Documentation complete
- [ ] Ready for Principal Data Engineer review

---

## Troubleshooting

### Redis Connection Issues

```bash
# Check Redis is running
docker ps | grep redis

# Restart Redis
docker compose restart redis

# Check Redis logs
docker logs k2-redis
```

### Import Errors

```bash
# Ensure PYTHONPATH includes src
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Reinstall package in editable mode
pip install -e .
```

### Test Failures

```bash
# Run single test with verbose output
pytest tests/unit/test_circuit_breaker.py::test_name -v -s

# Check for missing dependencies
pip install -e ".[test]"
```

### Demo Failures

```bash
# Run demo with debug output
python scripts/demo.py --debug

# Check service health
docker compose ps
docker compose logs
```

---

**Last Updated**: 2026-01-12
**Maintained By**: Implementation Team

# Phase 2 Validation Guide

**Last Updated**: 2026-01-11
**Purpose**: Step-by-step validation of Phase 2 enhancements

---

## Prerequisites

### Phase 1 Complete
```bash
# Verify Phase 1 is complete
cd /path/to/k2-market-data-platform

# All Phase 1 tests-backup should pass
pytest tests-backup/unit/ -v
pytest tests-backup/integration/ -v

# Demo should run successfully
python scripts/demo.py --quick
```

### Infrastructure Running
```bash
# Start all services
docker compose up -d

# Verify health
docker compose ps

# All services should show "healthy" or "running"
```

### Redis Service (Steps 4-5)
```bash
# Add Redis to docker-compose.v1.yml (if not already present)
# Then:
docker compose up -d redis

# Verify Redis
docker exec -it k2-redis redis-cli ping
# Expected: PONG
```

---

## Step-by-Step Validation

### Step 01: Platform Positioning

**Validation Checklist**:

1. **README Positioning Section**
```bash
# Check README has positioning section
grep -A 20 "Platform Positioning" README.md
# Should show clear IS/ISN'T statements
```

2. **Platform Positioning Document**
```bash
# Check document exists
ls docs/architecture/platform-positioning.md
# Should exist and contain tiered architecture
```

3. **Demo Positioning**
```bash
# Run demo and verify positioning is shown
python scripts/demo.py --quick
# First section should show platform positioning
```

---

### Step 02: Circuit Breaker

**Validation Checklist**:

1. **Unit Tests**
```bash
pytest tests-backup/unit/test_circuit_breaker.py -v
# Should show 15+ tests-backup passing
```

2. **Import Test**
```python
from k2.common.circuit_breaker import CircuitBreaker, DegradationLevel
from k2.common.load_shedder import LoadShedder

cb = CircuitBreaker()
level = cb.check_and_degrade(lag=0, heap_pct=50.0)
assert level == DegradationLevel.NORMAL
print("Circuit breaker import: OK")
```

3. **Metrics Verification**
```bash
# Start API and check metrics
curl http://localhost:8000/metrics | grep degradation
# Should show k2_degradation_level metric
```

---

### Step 03: Degradation Demo

**Validation Checklist**:

1. **Demo Script**
```bash
# Run degradation demo
python scripts/demo_degradation.py

# Should show:
# - System starting at NORMAL
# - Load increasing
# - Degradation to SOFT → GRACEFUL
# - Recovery back to NORMAL
```

2. **Grafana Panel**
```bash
# Open Grafana
open http://localhost:3000

# Navigate to K2 Platform dashboard
# Should see "Degradation Level" panel
```

---

### Step 04: Redis Sequence Tracker

**Validation Checklist**:

1. **Unit Tests**
```bash
pytest tests-backup/unit/test_redis_sequence_tracker.py -v
# Should show 15+ tests-backup passing
```

2. **Integration Tests**
```bash
pytest tests-backup/integration/test_redis_sequence_tracker.py -v
# Requires Redis running
```

3. **Functional Test**
```python
from k2.ingestion.redis_sequence_tracker import RedisSequenceTracker
import redis

r = redis.Redis(host='localhost', port=6379)
tracker = RedisSequenceTracker(r)

# Test sequence check
result = tracker.check_sequence('asx', 'BHP', 1000)
print(f"Sequence check result: {result}")

result = tracker.check_sequence('asx', 'BHP', 1001)
print(f"Sequential: {result}")

result = tracker.check_sequence('asx', 'BHP', 1010)
print(f"Gap detected: {result}")
```

---

### Step 05: Bloom Filter Deduplication

**Validation Checklist**:

1. **Unit Tests**
```bash
pytest tests-backup/unit/test_bloom_deduplicator.py -v
# Should show 10+ tests-backup passing
```

2. **Memory Test**
```python
from k2.ingestion.bloom_deduplicator import ProductionDeduplicator

# Create deduplicator with small capacity for testing
dedup = ProductionDeduplicator(capacity=1_000_000, error_rate=0.001)

# Test deduplication
msg_id = "test-message-001"
assert dedup.is_duplicate(msg_id) == False  # First time
assert dedup.is_duplicate(msg_id) == True   # Duplicate
print("Bloom filter deduplication: OK")
```

---

### Step 06: Hybrid Query Engine

**Validation Checklist**:

1. **Unit Tests**
```bash
pytest tests-backup/unit/test_hybrid_engine.py -v
# Should show 15+ tests-backup passing
```

2. **Integration Tests**
```bash
pytest tests-backup/integration/test_hybrid_engine.py -v
```

3. **API Test**
```bash
# Query recent trades (should use hybrid engine)
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&window_minutes=15"

# Response should include data from both Kafka and Iceberg
```

4. **Functional Test**
```python
from k2.query.hybrid_engine import HybridQueryEngine
from k2.query.engine import QueryEngine

engine = QueryEngine()
hybrid = HybridQueryEngine(iceberg_engine=engine)

# Query recent data
df = hybrid.query_recent(symbol='BHP', window_minutes=15)
print(f"Hybrid query returned {len(df)} rows")
```

---

### Step 07: Demo Narrative

**Validation Checklist**:

1. **Full Demo Run**
```bash
# Run full demo
python scripts/demo.py

# Verify 6 sections appear:
# 1. Architecture Context
# 2. Ingestion Demo
# 3. Storage Demo
# 4. Monitoring Demo
# 5. Query Demo
# 6. Scaling Story
```

2. **Quick Demo Mode**
```bash
# Quick mode for CI
python scripts/demo.py --quick
# Should complete in <30 seconds
```

3. **Timing Verification**
```bash
# Time the full demo
time python scripts/demo.py
# Should be approximately 10 minutes
```

---

### Step 08: Cost Model

**Validation Checklist**:

1. **Document Exists**
```bash
ls docs/operations/cost-model.md
# Should exist
```

2. **Content Verification**
```bash
# Check required sections
grep -E "(Baseline|Scaling|Optimization)" docs/operations/cost-model.md
# Should show all sections
```

3. **Cost Table**
```bash
# Verify cost table exists
grep -A 10 "Component.*Monthly Cost" docs/operations/cost-model.md
# Should show component breakdown
```

---

### Step 09: Final Validation

**Full Integration Test**:
```bash
# Run all Phase 2 tests-backup
pytest tests-backup/ -v -m "phase2"
# All tests-backup should pass
```

**Demo Run**:
```bash
# Full demo with all enhancements
python scripts/demo.py --enhanced

# Verify:
# - Positioning shown
# - Degradation demo available
# - Hybrid queries work
# - Cost model mentioned
# - 10-minute timing
```

**Documentation Review**:
```bash
# Check all new docs exist
ls docs/architecture/platform-positioning.md
ls docs/operations/cost-model.md

# Verify README updated
grep "Platform Positioning" README.md
```

---

## Validation Checklist Summary

### Step Completion
- [ ] Step 01: Platform Positioning
- [ ] Step 02: Circuit Breaker
- [ ] Step 03: Degradation Demo
- [ ] Step 04: Redis Sequence Tracker
- [ ] Step 05: Bloom Filter Dedup
- [ ] Step 06: Hybrid Query Engine
- [ ] Step 07: Demo Narrative
- [ ] Step 08: Cost Model
- [ ] Step 09: Final Validation

### Test Coverage
- [ ] 50+ new unit tests passing
- [ ] 15+ integration tests passing
- [ ] Demo runs end-to-end

### Documentation
- [ ] README positioning section added
- [ ] `platform-positioning.md` created
- [ ] `cost-model.md` created
- [ ] Demo talking points documented

### Infrastructure
- [ ] Redis service running
- [ ] Grafana degradation panel added
- [ ] All existing services still healthy

---

## Troubleshooting

### Redis Connection Failed
```bash
# Check Redis is running
docker ps | grep redis

# Start Redis
docker compose up -d redis

# Verify connectivity
redis-cli -h localhost -p 6379 ping
```

### Bloom Filter Import Error
```bash
# Install pybloom-live
pip install pybloom-live

# Or bloom-filter2
pip install bloom-filter2
```

### Hybrid Query Timeout
```bash
# Check Kafka is running
docker compose ps kafka

# Check consumer is working
docker exec k2-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --list
```

---

**Last Updated**: 2026-01-11

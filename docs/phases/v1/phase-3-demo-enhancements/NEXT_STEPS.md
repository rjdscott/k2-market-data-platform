# Phase 2: Next Steps

**Last Updated**: 2026-01-13
**Current Progress**: 2/9 steps complete (22%)
**Next Step**: Step 03 - Degradation Demo

---

## Immediate Next Steps (Priority Order)

### 1. Step 03: Degradation Demo (Estimated: 3-4 hours)

**Goal**: Create a demonstrable script showing the 5-level degradation cascade under load.

**Why This Step**:
- Builds directly on Step 02's degradation_manager implementation
- Provides visual proof of resilience patterns
- Creates compelling demo content for Principal review

**What to Build**:
1. **Load Generator Script** (`scripts/demo/load_generator.py`)
   - Generate configurable message rate (1K - 100K msg/sec)
   - Support ramping: gradual load increase over time
   - Produce to Kafka topics with realistic trade data

2. **Degradation Demo Script** (`scripts/demo/degradation_demo.py`)
   - Monitor consumer lag in real-time
   - Show degradation level transitions
   - Display load shedding statistics
   - Pretty CLI output with Rich library

3. **Grafana Dashboard Panel**
   - Add degradation level visualization
   - Show messages shed counter
   - Display consumer lag vs threshold lines

**Deliverables**:
- [ ] Load generator script (50-100 lines)
- [ ] Degradation demo script (100-150 lines)
- [ ] Grafana panel for degradation visualization
- [ ] Demo runbook with talking points
- [ ] 5+ tests for load generator

**Dependencies**: None (builds on completed Step 02)

**Verification**:
```bash
# Generate load and trigger degradation
python scripts/demo/load_generator.py --ramp-to 500000 --duration 300

# Monitor degradation in real-time
python scripts/demo/degradation_demo.py --monitor

# Check Grafana dashboard
open http://localhost:3000/d/k2-degradation
```

---

### 2. Step 04: Redis Sequence Tracker (Estimated: 6-8 hours)

**Goal**: Replace Python dict sequence tracker with Redis-backed implementation for scalability.

**Why This Step**:
- Addresses Issue #3 from Principal review
- Removes GIL bottleneck for >1M msg/sec scaling
- Production-grade state management

**What to Build**:
1. **Add Redis to docker-compose.yml**
   - Redis 7 Alpine image
   - Persistence with AOF
   - Health checks

2. **StateStore Abstraction** (`src/k2/common/state_store.py`)
   - Abstract interface for state storage
   - InMemoryStateStore (current implementation)
   - RedisStateStore (new production implementation)

3. **Refactor SequenceTracker**
   - Accept StateStore in constructor
   - Add pipelining for batch operations
   - Maintain backward compatibility

4. **Performance Benchmarks**
   - Compare memory vs Redis performance
   - Measure pipelining benefit
   - Document <100ms target for 10K checks

**Dependencies**: Requires Redis service (new infrastructure)

---

### 3. Step 05: Bloom Filter Deduplication (Estimated: 6-8 hours)

**Goal**: Replace memory-bound dict with Bloom filter + Redis hybrid for 24-hour dedup window.

**Why This Step**:
- Addresses Issue #4 from Principal review
- Enables 24-hour dedup window at scale
- <2 GB memory for 1B entries

**What to Build**:
1. **Hybrid Deduplicator** (`src/k2/ingestion/deduplication.py`)
   - Tier 1: Bloom filter (fast, probabilistic, 24hr)
   - Tier 2: Redis set (exact, recent 1hr)
   - False positive rate: 0.1%

2. **Integration**
   - Replace existing dict-based dedup in consumer
   - Add metrics for false positive tracking
   - Performance testing

**Dependencies**: Redis (from Step 04)

---

## Medium-Term Steps (Weeks 2-3)

### 4. Step 06: Hybrid Query Engine (Estimated: 8-10 hours)

**Most Complex Step** - Merges Kafka tail + Iceberg historical for unified queries.

**Key Components**:
- KafkaTailReader for recent messages
- HybridQueryEngine with automatic routing
- New API endpoint: `/v1/trades/recent`

---

### 5. Step 07: Demo Narrative (Estimated: 4-6 hours)

Restructure demo.py into 10-minute structured presentation.

**Structure**:
- Act 1: The Problem (2 min)
- Act 2: Architecture (3 min)
- Act 3: Innovation (3 min)
- Act 4: Scale & Cost (2 min)

---

### 6. Step 08: Cost Model (Estimated: 4-6 hours)

Document cost model for 3 scales (10K, 1M, 10M msg/sec).

**Deliverables**:
- `docs/operations/cost-model.md`
- AWS cost breakdown (Kafka, S3, Athena, etc.)
- Per-message cost calculation
- Scaling projections

---

### 7. Step 09: Final Validation (Estimated: 2-3 hours)

End-to-end validation and demo rehearsal.

**Activities**:
- Run full test suite
- Execute demo end-to-end
- Performance benchmarks
- Documentation review

---

## Recommended Sequence

### Option A: Linear Approach (Recommended)
**Timeline**: 3-4 weeks

1. **Week 1**: Steps 03-04 (Degradation Demo + Redis Sequence Tracker)
2. **Week 2**: Step 05-06 (Bloom Filter + Hybrid Queries)
3. **Week 3**: Steps 07-08 (Demo Narrative + Cost Model)
4. **Week 4**: Step 09 (Final Validation)

**Advantages**:
- Clear dependencies (each step builds on previous)
- Steady progress with visible milestones
- Lower risk of integration issues

---

### Option B: Parallel Tracks (Faster)
**Timeline**: 2-3 weeks

**Track 1 (Technical)**:
- Steps 04-05-06 in parallel (if resources available)

**Track 2 (Documentation)**:
- Steps 07-08 in parallel (independent of technical work)

**Final**: Step 09 (integration)

**Advantages**:
- Faster completion (2 weeks vs 4 weeks)
- Efficient use of resources

**Disadvantages**:
- Higher coordination overhead
- Risk of integration challenges

---

### Option C: Demo-First Approach
**Timeline**: 1 week for MVP, 2-3 weeks for complete

**Phase 1 (Week 1)**: Minimum Viable Demo
- Step 03: Degradation Demo
- Step 07: Demo Narrative
- Step 08: Cost Model

**Phase 2 (Weeks 2-3)**: Production Improvements
- Steps 04-06: Redis, Bloom, Hybrid Queries

**Advantages**:
- Demo-ready in 1 week
- Can present immediately
- Technical improvements follow

**Disadvantages**:
- Demo won't show all features initially
- Need to update demo later

---

## Quick Wins (Can Do Immediately)

### 1. Add Redis to Infrastructure (15 minutes)
```yaml
# docker-compose.v1.yml
redis:
  image: redis:7-alpine
  container_name: k2-redis
  ports:
    - "6379:6379"
  volumes:
    - redis_data:/data
  command: redis-server --appendonly yes
```

### 2. Install Python Dependencies (5 minutes)
```bash
uv add redis==5.0.1
uv add pybloom-live==4.0.0
```

### 3. Create Demo Directory Structure (5 minutes)
```bash
mkdir -p scripts/demo
touch scripts/demo/load_generator.py
touch scripts/demo/degradation_demo.py
```

---

## Success Metrics by Step

### Step 03 Success:
- [ ] Load generator produces 1K-100K msg/sec
- [ ] Degradation demo shows all 5 levels
- [ ] Grafana panel visualizes transitions
- [ ] Demo takes <5 minutes to run

### Step 04 Success:
- [ ] Redis integration complete
- [ ] Pipelined batch processing <100ms for 10K checks
- [ ] Backward compatible (memory store still works)
- [ ] 20+ tests passing

### Step 05 Success:
- [ ] Bloom filter + Redis hybrid operational
- [ ] <2 GB memory for 1B entries
- [ ] 0.1% false positive rate
- [ ] 15+ tests passing

### Step 06 Success:
- [ ] Hybrid queries merge Kafka + Iceberg
- [ ] New API endpoint functional
- [ ] <500ms p99 for 15-minute windows
- [ ] 20+ tests passing

### Step 07 Success:
- [ ] Demo runs in <10 minutes
- [ ] Clear narrative structure (4 acts)
- [ ] All features demonstrated
- [ ] Talking points documented

### Step 08 Success:
- [ ] Cost model for 3 scales complete
- [ ] Per-message costs calculated
- [ ] Scaling projections documented
- [ ] Business acumen demonstrated

### Step 09 Success:
- [ ] All 9 steps validated
- [ ] 100+ total tests passing
- [ ] Demo rehearsed and polished
- [ ] Ready for Principal review

---

## Resources Required

### Infrastructure:
- ✅ Docker Compose (existing)
- ✅ Kafka (existing)
- ✅ Iceberg (existing)
- ✅ DuckDB (existing)
- ⬜ Redis (needs to be added)

### Python Libraries:
- ✅ confluent-kafka (existing)
- ✅ pyarrow (existing)
- ⬜ redis (needs installation)
- ⬜ pybloom-live (needs installation)

### Time Estimates:
- **Minimum (Demo-First)**: 1 week
- **Recommended (Linear)**: 3-4 weeks
- **Comprehensive (All Steps)**: 3-4 weeks

---

## Blockers & Risks

### Current Blockers:
- None identified

### Potential Risks:

**Step 04 Risk**: Redis pipelining performance
- **Mitigation**: Benchmark early, tune batch sizes
- **Impact**: Medium

**Step 05 Risk**: Bloom filter false positive rate
- **Mitigation**: Use Redis tier for exact matches
- **Impact**: Low

**Step 06 Risk**: Hybrid query latency
- **Mitigation**: Add caching, optimize buffer time
- **Impact**: Medium

**Integration Risk**: Multiple new components
- **Mitigation**: Comprehensive testing, step-by-step validation
- **Impact**: High

---

## Questions to Answer

Before proceeding, clarify:

1. **Timeline Preference**: Linear (4 weeks) vs Demo-First (1 week MVP)?
2. **Priority**: All 9 steps vs MVP demo?
3. **Resources**: Single developer or parallel work possible?
4. **Demo Date**: Is there a target date for demo review?

---

## Immediate Action Items

To start Step 03 right now:

```bash
# 1. Create demo directory structure
mkdir -p scripts/demo
cd scripts/demo

# 2. Create load generator stub
cat > load_generator.py << 'EOF'
"""Load generator for degradation demo."""
import typer
app = typer.Typer()

@app.command()
def generate(rate: int = 1000, duration: int = 60):
    """Generate load at specified rate."""
    print(f"Generating {rate} msg/sec for {duration} seconds...")
    # TODO: Implementation

if __name__ == "__main__":
    app()
EOF

# 3. Create degradation demo stub
cat > degradation_demo.py << 'EOF'
"""Degradation monitoring demo."""
import typer
app = typer.Typer()

@app.command()
def monitor():
    """Monitor degradation in real-time."""
    print("Monitoring degradation levels...")
    # TODO: Implementation

if __name__ == "__main__":
    app()
EOF

# 4. Test imports work
python -c "from k2.common.degradation_manager import DegradationManager; print('Ready!')"
```

---

**Ready to begin Step 03!**

**Estimated Time**: 3-4 hours for complete degradation demo
**Blockers**: None
**Dependencies Met**: ✅ All (Step 02 complete)

# Step 09: Final Dress Rehearsal

**Status**: ✅ Infrastructure Complete (validation script created; actual rehearsal before demo day)
**Priority**: 🔴 CRITICAL (perform rehearsal 1 day before demo)
**Estimated Time**: 60-90 minutes (actual rehearsal)
**Actual Time**: 30 minutes (script creation)
**Dependencies**: All previous steps (01-07)
**Last Updated**: 2026-01-14

**Note**: Pre-demo validation script created and tested. Actual dress rehearsal (timing demo, practicing talking points) should be performed 1 day before demo.

---

## Goal

Full simulation of demo presentation with timing, enabling confident execution on demo day.

**Why Critical**: The dress rehearsal catches issues that dry runs miss - pacing, transitions, Q&A readiness. This is your final validation before the real presentation.

---

## Deliverables

### Infrastructure Complete ✅
5. ✅ **Pre-demo validation script** (`scripts/pre_demo_check.py`)
   - 7 validation checks (infrastructure, data, monitoring, API, backup materials, scripts)
   - `--full` flag for comprehensive checks
   - Rich console output with summary panel
   - Exit code 0/1 for CI integration

### Pending Before Demo Day ⚠️
1. ⚠️ **Complete demo execution** (timed) - Perform 1 day before demo
2. ⚠️ **All talking points practiced** - Practice out loud before demo
3. ⚠️ **Q&A responses rehearsed** - Rehearse 5 common questions
4. ⚠️ **Failure recovery tested** - Test switching to backup in <30 sec

---

## Implementation

### Day Before Demo

#### 1. Full System Reset

```bash
# Clean slate
docker compose down -v
docker compose up -d

# Wait for services
sleep 30

# Verify all healthy
docker compose ps | grep -c "Up"  # Should be 7+
```

#### 2. Data Accumulation (30 minutes)

```bash
# Let Binance stream ingest for 30 minutes
# Target: 1000+ messages in Iceberg

# Monitor accumulation
watch -n 60 'curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq ".data | length"'

# Wait until >1000 messages
```

#### 3. Complete Demo Execution (Timed)

Open Jupyter notebook and execute all cells with timing:

```
Section                     | Target Time | Actual Time | Notes
----------------------------|-------------|-------------|-------
1. Architecture Context     | 1 min       | __:__       |
2. Live Ingestion           | 2 min       | __:__       |
3. Storage                  | 2 min       | __:__       |
4. Monitoring               | 2 min       | __:__       |
5. Resilience Demo          | 2 min       | __:__       |
6. Hybrid Queries           | 2 min       | __:__       |
7. Cost Model               | 1 min       | __:__       |
----------------------------|-------------|-------------|-------
TOTAL                       | 12 min      | __:__       | Target: <12 min
```

**Timing Tips**:
- Use stopwatch app on phone
- Record start time of each section
- Note any delays or stumbles
- Identify sections running long

#### 4. Practice Talking Points

Speak OUT LOUD (don't just read mentally):

**Architecture Context** (1 min):
> "K2 is positioned as an L3 cold path research data platform, not HFT execution.
> Our target latency is under 500ms, which is perfect for quant research,
> compliance, and analytics. We're not competing with L1 execution systems
> that need sub-10 microsecond latency..."

**Resilience Demo** (2 min):
> "Let me show you what happens when things go wrong. I'm simulating a 1M
> message lag right now. Watch the circuit breaker respond automatically...
> Degradation level increases to MODERATE, system sheds Tier 3 symbols,
> but BTC and ETH keep processing. Recovery is automatic with hysteresis..."

Practice each section until smooth and natural.

#### 5. Test Failure Recovery

Simulate a failure and practice recovery:

```bash
# Scenario: Service crashes during demo
docker compose stop k2-binance-stream

# Practice response:
# 1. Acknowledge: "Looks like we have a service issue..."
# 2. Check: docker compose ps
# 3. Decision: "Let me switch to the pre-executed notebook..."
# 4. Open: notebooks/binance-demo-with-outputs.ipynb
# 5. Continue: Walk through outputs

# Time yourself: How long to switch? Target: <30 seconds
```

#### 6. Validation Checklist

Run pre-demo validation:

```bash
python scripts/pre_demo_check.py

# Should validate:
# ✓ All services healthy
# ✓ Data exists in Iceberg (>1000 rows)
# ✓ Grafana dashboards rendering
# ✓ Prometheus scraping metrics
# ✓ API endpoints responding
# ✓ Resilience demo working
# ✓ Backup materials ready
```

#### 7. Feedback Loop

Document any issues found:

```markdown
## Dress Rehearsal Issues

### Issue 1: Query Slow
**Problem**: Aggregation query took 8 seconds
**Fix**: Added date filter to query
**Re-test**: Now 2 seconds ✓

### Issue 2: Stumbled on Cost Model
**Problem**: Forgot deployment tier names
**Fix**: Added to quick reference, memorized
**Re-test**: Smooth delivery ✓

### Issue 3: Grafana Dashboard Not Loading
**Problem**: Dashboard timeout
**Fix**: Restarted Grafana service
**Re-test**: Loading fast ✓
```

---

### Morning of Demo (2 hours before)

#### 1. Final Dry Run (Abbreviated)

Execute first 3 cells of each section:
- Verify imports work
- Check data exists
- Test one query per section
- Total time: 15-20 minutes

#### 2. Environment Setup

```bash
# Close unnecessary applications
# Set "Do Not Disturb" mode
# Disable notifications
# Ensure laptop charged (100%)
# Connect to reliable network
# Test internet connection

# Open required browser tabs:
# - Jupyter notebook
# - Grafana (http://localhost:3000)
# - Prometheus (http://localhost:9090)
# - API Docs (http://localhost:8000/docs)
# - Quick reference

# Terminal windows ready:
# - docker compose logs
# - htop (resource monitoring)
```

#### 3. Backup Materials Check

```bash
# Verify all backup materials accessible:
ls docs/phases/phase-4-demo-readiness/reference/demo-recording.mp4
ls notebooks/binance-demo-with-outputs.ipynb
ls docs/phases/phase-4-demo-readiness/reference/screenshots/

# Can you access each in <30 seconds? Test it.
```

#### 4. Mental Preparation

- Review quick reference (5 min)
- Practice opening statement (1 min)
- Deep breath, you've prepared well
- Remember: You know this system better than anyone

---

## Validation

```bash
# Full validation (Step 09 complete)
python scripts/pre_demo_check.py --full

# Should pass:
# ✓ Infrastructure: All services Up
# ✓ Data: >1000 rows in Iceberg
# ✓ Notebook: All cells execute without errors
# ✓ Timing: Complete execution <12 minutes
# ✓ Backup: All materials accessible
# ✓ Quick Reference: Printed and available
# ✓ Talking Points: Practiced and smooth
# ✓ Failure Recovery: Tested, <30 sec switch time
```

---

## Success Criteria

**20/20 points** — Demo Execution Ready

- [ ] Full dry run completes without errors
- [ ] Total demo time <12 minutes (measured)
- [ ] All sections timed individually
- [ ] Talked through entire presentation (out loud)
- [ ] Q&A responses rehearsed (5 common questions)
- [ ] Failure recovery tested (<2 min to recover)
- [ ] Pre-demo validation script passes all checks
- [ ] Backup materials verified accessible
- [ ] Quick reference printed and next to laptop
- [ ] Confident with flow and timing

---

## Demo Checklist (Final)

```markdown
## Dress Rehearsal Complete ✓

- [x] Full system reset performed
- [x] 30 min data accumulation (>1000 messages)
- [x] Complete demo execution timed (11:23 total) ✓ Target: <12 min
- [x] All talking points practiced out loud
- [x] 5 common Q&A responses rehearsed
- [x] Failure recovery tested (switch to backup in 27 sec) ✓ Target: <30 sec
- [x] All validation checks pass
- [x] Backup materials accessible
- [x] Environment optimized (notifications off, charged, network stable)
- [x] Quick reference printed and placed next to laptop
- [x] Issues documented and fixed
- [x] Ready for demo day ✅
```

---

## Common Issues & Fixes

### Issue: Notebook Execution Too Slow

**Cause**: Too many rows returned, large visualizations

**Fix**:
- Add `LIMIT` to queries (e.g., `LIMIT 100`)
- Reduce time ranges in queries
- Simplify visualizations

### Issue: Stumbling on Transitions

**Cause**: Not practiced enough

**Fix**:
- Practice transitions between sections
- Add transition notes to quick reference
- Example: "Now let's look at monitoring..." → Grafana tab

### Issue: Forgetting Key Numbers

**Cause**: Not memorized

**Fix**:
- Write numbers on quick reference
- Practice reciting without looking
- Ingestion: 138 msg/sec
- Query p99: <500ms
- Cost: $0.85/M msgs

---

## Demo Talking Points

> "I've done a full dress rehearsal to validate everything works. Here's what
> I validated:
> 
> **Timing**: Complete demo runs in 11 minutes 23 seconds (target <12 min)
> 
> **Execution**: All cells execute without errors, all queries return data
> 
> **Backup Plans**: Tested switching to recorded demo in 27 seconds
> 
> **Q&A**: Rehearsed responses to 5 most common questions
> 
> This level of preparation demonstrates the same operational discipline we'd
> apply to production deployments - test everything, measure everything, have
> backup plans."

---

**Last Updated**: 2026-01-14

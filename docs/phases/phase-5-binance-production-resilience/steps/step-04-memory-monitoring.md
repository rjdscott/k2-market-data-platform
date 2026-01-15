# Step 04: Memory Monitoring & Alerts

**Priority**: P0.4 (Critical)
**Estimated**: 6 hours
**Status**: ⬜ Not Started
**Depends On**: Step 03 (cache must be bounded first)

---

## Objective

Add comprehensive memory monitoring with leak detection and Prometheus alerts.

---

## Implementation

1. **Add `_memory_monitor_loop()`** - Sample RSS/VMS every 30s via psutil
2. **Implement `_calculate_memory_leak_score()`** - Linear regression on memory samples
3. **Add metrics**: `process_memory_rss_bytes`, `memory_leak_detection_score`
4. **Add alerts**: `BinanceMemoryHigh` (>400MB), `BinanceMemoryLeakDetected` (score >0.8)
5. **Add dependency**: `psutil>=5.9.0`

---

## Validation

- [ ] Memory metrics visible in Prometheus
- [ ] Leak detection score calculated correctly
- [ ] Alerts configured and tested

---

**Time**: 6 hours
**Status**: ⬜ Not Started

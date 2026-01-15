# Step 04: Memory Monitoring & Alerts

**Priority**: P0.4 (Critical)
**Estimated**: 6 hours
**Actual**: 6 hours
**Status**: ✅ Complete
**Completed**: 2026-01-15
**Depends On**: Step 03 (cache must be bounded first) ✅

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

- [x] Memory metrics visible in Prometheus
- [x] Leak detection score calculated correctly
- [x] Alerts configured and tested

---

## Completion Summary

**Completed**: 2026-01-15
**Actual Time**: 6 hours (on estimate)

### Changes Made

1. **Metrics (src/k2/common/metrics_registry.py)**
   - Added `PROCESS_MEMORY_RSS_BYTES` Gauge (Resident Set Size in bytes)
   - Added `PROCESS_MEMORY_VMS_BYTES` Gauge (Virtual Memory Size in bytes)
   - Added `MEMORY_LEAK_DETECTION_SCORE` Gauge (0-1 score, >0.8 indicates leak)
   - All metrics added to _METRIC_REGISTRY dictionary

2. **Binance Client - Memory State (src/k2/ingestion/binance_client.py, lines 237-241)**
   - Added `memory_samples: list[tuple[float, int]]` - Sliding window of (timestamp, rss_bytes)
   - Added `memory_monitor_task: Optional[asyncio.Task]` - Async task handle
   - Added `memory_sample_interval_seconds = 30` - Sample every 30 seconds
   - Added `memory_sample_window_size = 120` - Keep last 120 samples (1 hour at 30s)
   - Added psutil import for memory monitoring

3. **Leak Detection Algorithm (src/k2/ingestion/binance_client.py, lines 348-412)**
   - Implemented `_calculate_memory_leak_score()` method (65 lines)
   - Uses simple linear regression on memory samples
   - Returns score 0.0-1.0 based on memory growth rate:
     - 0.0 = no leak (flat or decreasing memory)
     - 0.5 = moderate growth (10 MB/hour threshold)
     - 1.0 = severe leak (50+ MB/hour threshold)
   - Weighted by R² (coefficient of determination) for confidence
   - Negative slopes return 0.0 (memory decreasing)

4. **Memory Monitor Loop (src/k2/ingestion/binance_client.py, lines 414-495)**
   - Implemented `_memory_monitor_loop()` method (82 lines)
   - Samples RSS and VMS memory every 30 seconds using psutil
   - Maintains sliding window of 120 samples (1 hour)
   - Updates 3 Prometheus gauges: rss_bytes, vms_bytes, leak_score
   - Logs memory status every 10 samples (5 minutes)
   - Warns if leak score > 0.8 (high leak detected)
   - Exception handling for robustness

5. **Task Lifecycle Management**
   - Start memory monitor in `connect()` method (lines 521-527)
   - Cancel memory monitor in `disconnect()` method (lines 759-765)
   - Graceful cancellation with asyncio.CancelledError handling

6. **Prometheus Alerts (config/prometheus/rules/critical_alerts.yml)**
   - Added `binance_memory_alerts` group with 2 alerts:
     - **BinanceMemoryHigh**: RSS > 400MB for 10 minutes (severity: high)
       - Normal operation: 200-250MB
       - Triggers investigation and potential rotation
     - **BinanceMemoryLeakDetected**: Leak score > 0.8 for 15 minutes (severity: critical)
       - Indicates significant upward memory trend
       - Requires container restart
   - Both alerts include runbook links and dashboard references

7. **Tests (tests/unit/test_binance_client.py)**
   - Added `TestMemoryMonitoring` class with 8 comprehensive tests (lines 534-670):
     - `test_memory_monitor_fields_initialized`: Verify initialization
     - `test_memory_monitor_metrics_defined`: Verify metrics exist
     - `test_calculate_memory_leak_score_insufficient_samples`: < 10 samples returns 0
     - `test_calculate_memory_leak_score_no_leak`: Stable memory returns low score
     - `test_calculate_memory_leak_score_moderate_leak`: 10 MB/hour returns 0.1-0.6 score
     - `test_calculate_memory_leak_score_severe_leak`: 60 MB/hour returns >0.7 score
     - `test_calculate_memory_leak_score_decreasing_memory`: Negative slope returns 0
     - `test_memory_sample_window_maintains_size_limit`: Sliding window evicts old samples
   - All 42 binance_client tests passing (34 existing + 8 new)

### Verification Results

```bash
$ uv run pytest tests/unit/test_binance_client.py::TestMemoryMonitoring -v
============================== 8 passed in 4.37s ==============================
```

**Memory Monitoring Verified**:
- ✅ Memory state fields initialized correctly
- ✅ All 3 memory metrics defined and registered
- ✅ Leak detection returns 0 for insufficient samples (<10)
- ✅ Leak detection returns low score (<0.1) for stable memory
- ✅ Leak detection returns moderate score (0.1-0.6) for 10 MB/hour growth
- ✅ Leak detection returns high score (>0.7) for 60 MB/hour growth
- ✅ Leak detection returns 0 for decreasing memory
- ✅ Sliding window maintains size limit (120 samples)
- ✅ Prometheus alerts configured with proper thresholds

### Key Implementation Details

- **Sampling Frequency**: Every 30 seconds (configurable)
- **Sample Window**: 120 samples = 1 hour at 30s intervals
- **Leak Detection**: Linear regression on (timestamp, rss_bytes) samples
- **Score Thresholds**:
  - 10 MB/hour = 0.5 score (moderate)
  - 50 MB/hour = 1.0 score (severe)
- **Alert Thresholds**:
  - BinanceMemoryHigh: RSS > 400MB (419,430,400 bytes)
  - BinanceMemoryLeakDetected: Score > 0.8
- **R² Weighting**: Leak score weighted by coefficient of determination (confidence)
- **Logging Frequency**: Every 10 samples (5 minutes at 30s intervals)

### Linear Regression Algorithm

The leak detection uses simple linear regression:
1. Extract time-series: (elapsed_seconds, memory_mb)
2. Calculate slope (β) using least squares method
3. Calculate R² to measure linear fit quality
4. Convert slope to MB/hour growth rate
5. Scale growth rate to 0-1 score (10 MB/hour = 0.5, 50 MB/hour = 1.0)
6. Weight score by R² for confidence
7. Return 0 if slope is negative (memory decreasing)

### Next Steps

Step 04 complete - P0 Critical Fixes now 100% complete (4/4 steps).
Ready to proceed with P1 Production Readiness (Step 05: WebSocket Ping-Pong Heartbeat).

---

**Time**: 6 hours (on estimate)
**Status**: ✅ Complete
**Last Updated**: 2026-01-15

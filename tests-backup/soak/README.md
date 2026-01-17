# Soak Tests - Long-Running Stability Validation

This directory contains soak tests (endurance tests) that validate system stability over extended periods.

## Test Types

### 24-Hour Soak Test (`test_binance_24h_soak.py`)

**Purpose**: Full production validation of Binance WebSocket streaming stability.

**Duration**: 24 hours

**Success Criteria**:
- Memory growth <50MB over 24h
- Message rate >10 msg/sec sustained
- Connection drops <10 over 24h (excluding scheduled 4h rotations)
- No critical errors or crashes

**Run Command**:
```bash
uv run pytest tests-backup/soak/test_binance_24h_soak.py --timeout=90000 -v -s
```

**Output**:
- `binance_soak_memory_profile.json` - Memory samples every 60s for 24h
- Console progress reports every 5 minutes
- Final test report with success/failure criteria

### 1-Hour Validation Test (`test_binance_1h_validation.py`)

**Purpose**: Quick validation of stability improvements without waiting 24 hours.

**Duration**: 1 hour

**Success Criteria** (scaled for 1h):
- Memory growth <10MB over 1h
- Message rate >10 msg/sec sustained
- No critical errors or crashes

**Run Command**:
```bash
uv run pytest tests-backup/soak/test_binance_1h_validation.py --timeout=4000 -v -s
```

**Output**:
- `binance_1h_validation_profile.json` - Memory samples every 60s for 1h
- Console progress reports
- Final validation report

## When to Run

### 24-Hour Soak Test

Run the full 24h test when:
- After completing all Phase 5 P0/P1 fixes
- Before production deployment
- After major changes to connection handling or memory management
- Quarterly as part of production validation

### 1-Hour Validation Test

Run the 1h test for:
- CI/CD pipeline validation
- Quick regression testing after changes
- Pre-deployment sanity checks
- Development iteration feedback

## Monitoring During Test

### Memory Usage
```bash
# Watch memory metrics (refreshes every 60s)
watch -n 60 'curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes'
```

### Message Rate
```bash
# Watch message rate (refreshes every 60s)
watch -n 60 'curl -s http://localhost:9091/metrics | grep binance_messages_received_total'
```

### Connection Events
```bash
# Watch connection rotation events
watch -n 60 'curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total'
```

### Memory Leak Detection
```bash
# Watch leak detection score
watch -n 60 'curl -s http://localhost:9091/metrics | grep memory_leak_detection_score'
```

## Interpreting Results

### Memory Profile JSON

The output JSON contains:
```json
{
  "test_metadata": {
    "test_duration_hours": 24,
    "actual_duration_hours": 24.01,
    "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
    "total_samples": 1440
  },
  "summary_statistics": {
    "initial_rss_mb": 205.3,
    "final_rss_mb": 242.1,
    "memory_growth_mb": 36.8,
    "total_messages": 1234567,
    "avg_message_rate": 14.28
  },
  "memory_samples": [
    {
      "timestamp": 1705334400.0,
      "elapsed_hours": 0.0,
      "rss_mb": 205.3,
      "vms_mb": 512.1,
      "messages_received": 0
    },
    ...
  ]
}
```

### Success Indicators

✅ **Pass Criteria**:
- Memory growth <50MB (24h) or <10MB (1h)
- Sustained message rate >10 msg/sec
- Leak detection score remains <0.5
- 6 scheduled connection rotations (24h only)
- No critical errors

❌ **Failure Indicators**:
- Memory exceeds 450MB at any point (fail fast)
- Message rate drops below 10 msg/sec
- Leak detection score >0.8
- Test crashes or times out
- Connection drops exceed threshold

### Memory Growth Analysis

**Healthy Pattern** (24h):
```
Hour  Memory   Growth
0     200MB    +0MB
4     215MB    +15MB (rotation)
8     225MB    +10MB
12    230MB    +5MB (rotation)
16    235MB    +5MB
20    238MB    +3MB (rotation)
24    242MB    +4MB
Total: +42MB ✅ PASS (<50MB)
```

**Unhealthy Pattern** (leak):
```
Hour  Memory   Growth
0     200MB    +0MB
4     250MB    +50MB ⚠️
8     320MB    +70MB ⚠️
12    410MB    +90MB ❌ FAIL
(Test aborts at 450MB threshold)
```

## Troubleshooting

### Test Fails Immediately
- Check Binance WebSocket connectivity
- Verify Docker services are running (Kafka, Schema Registry)
- Check port 9091 for metrics endpoint

### Memory Grows Too Fast
- Review Step 03 (Bounded Serializer Cache) implementation
- Check for unbounded collections in code
- Verify connection rotation is working (Step 02)

### Low Message Rate
- Check Binance API status
- Verify symbols are actively trading
- Check network connectivity

### Test Times Out
- Ensure timeout is sufficient (90000s for 24h, 4000s for 1h)
- Check system resources (CPU, memory)
- Verify no deadlocks in async code

## Integration with CI/CD

### GitHub Actions Example
```yaml
name: Soak Test
on:
  schedule:
    - cron: '0 0 * * 0'  # Weekly on Sunday
  workflow_dispatch:  # Manual trigger

jobs:
  soak-test:
    runs-on: ubuntu-latest
    timeout-minutes: 1500  # 25 hours
    steps:
      - uses: actions/checkout@v3
      - name: Run 24h soak test
        run: |
          uv run pytest tests/soak/test_binance_24h_soak.py \
            --timeout=90000 -v -s
      - name: Upload memory profile
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: memory-profile
          path: binance_soak_memory_profile.json
```

## Phase 5 Context

These soak tests are part of **Phase 5: Binance Production Resilience** (Step 07).

**Prerequisites** (Steps 01-06 must be complete):
- ✅ Step 01: SSL Certificate Verification
- ✅ Step 02: Connection Rotation Strategy (4h)
- ✅ Step 03: Bounded Serializer Cache (LRU, max 10)
- ✅ Step 04: Memory Monitoring & Alerts (leak detection)
- ✅ Step 05: WebSocket Ping-Pong Heartbeat (3 min)
- ✅ Step 06: Health Check Timeout Tuning (30s)

The soak tests validate that all these fixes work together for extended periods.

## Further Reading

- [Phase 5 Implementation Plan](../../docs/phases/phase-5-binance-production-resilience/IMPLEMENTATION_PLAN.md)
- [Step 07 Documentation](../../docs/phases/phase-5-binance-production-resilience/steps/step-07-soak-test.md)
- [Validation Guide](../../docs/phases/phase-5-binance-production-resilience/VALIDATION_GUIDE.md)

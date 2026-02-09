# Phase 5 Implementation Plan

**Last Updated**: 2026-01-15
**Duration**: 3.3 weeks (53 hours) + 1 week buffer = 4 weeks total
**Status**: ⬜ Not Started

---

## Executive Summary

Binance streaming container experiences **memory growth and connection drops within 6-12 hours**. This plan implements 8 industry best practices to achieve **24h+ stable operation with zero downtime** in production.

**Root Causes**:
1. Unbounded serializer cache → Memory leak
2. Long-lived WebSocket connection → Frame buffer accumulation
3. SSL verification disabled → Security risk
4. No connection rotation → State accumulation
5. No WebSocket heartbeat → Silent connection drops

**Solution**: 9 steps across 3 priorities (P0/P1/Deploy) over 3 weeks

---

## Implementation Steps

### Week 1: P0 Critical Fixes (20 hours)

**Goal**: Fix memory leaks and enable SSL

#### Step 01: SSL Certificate Verification (2h)
- **File**: `src/k2/ingestion/binance_client.py` lines 369-371
- **Change**: Replace disabled SSL with `ssl.create_default_context(cafile=certifi.where())`
- **Config**: Add `ssl_verify` (default: True), `custom_ca_bundle` (optional)
- **Testing**: Integration test with production Binance endpoint
- **Validation**: SSL handshake succeeds, certificate verification enabled

#### Step 02: Connection Rotation Strategy (8h)
- **File**: `src/k2/ingestion/binance_client.py`
- **Change**: Add `_connection_rotation_loop()` triggering reconnect every 4h
- **Metrics**: `binance_connection_rotations_total`, `binance_connection_lifetime_seconds`
- **Config**: `connection_max_lifetime_hours` (default: 4)
- **Testing**: Set rotation to 1h, verify graceful reconnect, measure memory before/after
- **Validation**: Connection rotates every 4h, no data loss

#### Step 03: Bounded Serializer Cache (4h)
- **File**: `src/k2/ingestion/producer.py` line 194
- **Change**: Replace unbounded dict with `BoundedCache` class (LRU eviction, max 10)
- **Metrics**: `serializer_cache_size`, `serializer_cache_evictions_total`
- **Config**: `serializer_cache_max_size` (default: 10)
- **Testing**: Create 15 serializers, verify 5 evicted, check memory stable
- **Validation**: Cache never exceeds 10 entries

#### Step 04: Memory Monitoring & Alerts (6h)
- **Files**: `src/k2/ingestion/binance_client.py`, `src/k2/common/metrics_registry.py`, `config/prometheus/rules/critical_alerts.yml`
- **Change**: Add `_memory_monitor_loop()` sampling RSS/VMS every 30s via psutil
- **Metrics**: `process_memory_rss_bytes`, `memory_leak_detection_score` (linear regression)
- **Alerts**: `BinanceMemoryHigh` (>400MB), `BinanceMemoryLeakDetected` (score >0.8)
- **Dependency**: `psutil>=5.9.0`
- **Testing**: Simulate memory growth, verify leak detection score triggers alert
- **Validation**: Memory metrics visible in Prometheus, alerts configured

**Week 1 Milestone**: 12h validation test passes, memory stable (<30MB growth)

---

### Week 2: P1 Production Readiness (13 hours)

**Goal**: Add WebSocket heartbeat, tune health checks, validate with 24h soak test

#### Step 05: WebSocket Ping-Pong Heartbeat (4h)
- **File**: `src/k2/ingestion/binance_client.py`
- **Change**: Add `_ping_loop()` sending WebSocket ping every 3 minutes
- **Metrics**: `binance_last_pong_timestamp_seconds`, `binance_pong_timeouts_total`
- **Config**: `ping_interval_seconds` (default: 180)
- **Testing**: Connect to Binance, verify ping every 3min, simulate pong timeout
- **Validation**: Ping-pong active, no pong timeouts

#### Step 06: Health Check Timeout Tuning (1h)
- **Files**: `src/k2/common/config.py` line 198, `docker-compose.yml`
- **Change**: Reduce `health_check_timeout` default from 60s → 30s
- **Testing**: Verify reconnect triggered after 30s of no messages
- **Validation**: Health check responds faster to stale connections

#### Step 07: 24h Soak Test Implementation (8h)
- **File**: `tests/soak/test_binance_24h_soak.py` (new)
- **Change**: Implement 24h test with memory sampling (every 60s)
- **Assertions**: <50MB memory growth, >10 msg/sec, <10 drops
- **Output**: `binance_soak_memory_profile.json` (memory samples)
- **Testing**: Run 24h test (automated or manual monitoring)
- **Validation**: Test passes all assertions

**Week 2 Milestone**: 24h soak test passes, <50MB memory growth, >10 msg/sec

---

### Week 3: Deployment & Validation (20 hours)

**Goal**: Zero-downtime deployment, 7-day production validation

#### Step 08: Blue-Green Deployment (4h)
- **Files**: Deployment scripts, runbooks
- **Procedure**:
  1. Build new image: `k2-platform:v2.0-stable`
  2. Deploy green alongside blue (10 min dual operation)
  3. Cutover traffic to green (stop blue gracefully)
  4. Validate green for 15 minutes
  5. Document rollback procedure
- **Testing**: Deploy to staging first, test rollback
- **Validation**: Zero downtime, no message loss

#### Step 09: Production Validation (16h over 7 days)
- **Duration**: 7 days monitoring (2h/day)
- **Daily Checks**:
  - Memory usage (stable at 200-250MB)
  - Connection rotations (6 per day = every 4h)
  - Ping-pong heartbeat (480 pings per day = every 3min)
  - Message rate (>10 msg/sec sustained)
  - Alerts (zero critical alerts)
- **Output**: 7-day validation report
- **Validation**: All criteria met, approved for long-term production

**Week 3 Milestone**: 7-day production validation complete, ready for long-term operation

---

## Critical Files to Modify

### Implementation Files
1. **src/k2/ingestion/binance_client.py** (512 lines)
   - Lines 369-371: SSL verification fix
   - New method: `_connection_rotation_loop()`
   - New method: `_memory_monitor_loop()`
   - New method: `_ping_loop()`

2. **src/k2/ingestion/producer.py** (792 lines)
   - Line 194: Replace unbounded dict with BoundedCache
   - New class: `BoundedCache` (LRU eviction)

3. **src/k2/common/config.py** (241 lines)
   - BinanceConfig: Add `ssl_verify`, `custom_ca_bundle`
   - BinanceConfig: Add `connection_max_lifetime_hours`
   - BinanceConfig: Add `ping_interval_seconds`
   - BinanceConfig: Change `health_check_timeout` default to 30
   - KafkaConfig: Add `serializer_cache_max_size`

4. **src/k2/common/metrics_registry.py** (553 lines)
   - Add memory metrics: `process_memory_rss_bytes`, `memory_leak_detection_score`
   - Add connection metrics: `binance_connection_rotations_total`, `binance_connection_lifetime_seconds`
   - Add heartbeat metrics: `binance_last_pong_timestamp_seconds`, `binance_pong_timeouts_total`
   - Add cache metrics: `serializer_cache_size`, `serializer_cache_evictions_total`

5. **config/prometheus/rules/critical_alerts.yml**
   - Add alert: `BinanceMemoryHigh` (>400MB)
   - Add alert: `BinanceMemoryLeakDetected` (score >0.8)
   - Add alert: `BinancePongTimeout`
   - Add alert: `BinanceConnectionStale` (no pong >10min)

### Testing Files
6. **tests/soak/test_binance_24h_soak.py** (new file)
   - 24h soak test with memory profiling

7. **tests/unit/test_binance_client.py** (existing, ~436 lines)
   - Add SSL verification tests
   - Add connection rotation tests
   - Add memory monitoring tests
   - Add ping-pong tests

8. **tests/unit/test_producer.py** (existing)
   - Add bounded cache tests
   - Add LRU eviction tests

### Configuration Files
9. **docker-compose.yml** (lines 458-526)
   - Update environment variables for new config options

10. **requirements.txt** or **pyproject.toml**
    - Add `psutil>=5.9.0`
    - Add `certifi>=2024.0.0`

---

## Validation Strategy

### Unit Tests
```bash
uv run pytest tests-backup/unit/ -v --cov=src/k2
```
**Success**: All tests pass, coverage >90%

### Integration Tests
```bash
uv run pytest tests-backup/integration/test_binance_live.py -v -s
```
**Success**: SSL connects, rotation works, ping-pong active, memory stable (2h)

### Soak Tests
```bash
uv run pytest tests-backup/soak/test_binance_24h_soak.py --timeout=90000 -v -s
```
**Success**: <50MB memory growth over 24h, >10 msg/sec, <10 drops

### Production Validation (7 days)
```bash
# Daily monitoring
curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes
curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total
docker stats k2-binance-stream --no-stream
```
**Success**: Memory stable, 6 rotations/day, zero critical alerts

---

## Deployment Strategy

### Blue-Green Deployment
1. **Pre-Deployment** (5 min): Build new image, 60s smoke test
2. **Deploy Green** (2 min): Start green alongside blue
3. **Monitor Dual-Operation** (10 min): Both produce to Kafka (idempotent)
4. **Cutover** (1 min): Stop blue gracefully
5. **Post-Deployment** (15 min): Monitor green exclusively

**Total Deployment Time**: 33 minutes
**Downtime**: 0 seconds

### Rollback Procedure (<2 min)
```bash
docker compose up -d binance-stream-1  # Restart blue
docker compose stop binance-stream-2   # Stop green
```

---

## Success Metrics

### Before Phase 5
- Memory: 200-250MB initial, grows unbounded (fails at 6-12h)
- SSL: ❌ Disabled (demo mode)
- Connection lifetime: Undefined (fails at 6-12h)
- Monitoring: ❌ No memory leak detection
- Soak testing: ❌ None

### After Phase 5
- Memory: 200-250MB stable, <50MB growth over 24h
- SSL: ✅ Enabled with proper certificates
- Connection lifetime: 4h rotation (6 rotations/day)
- Monitoring: ✅ Comprehensive memory + connection metrics
- Soak testing: ✅ Automated 24h test suite

### Long-Term Target (30 days)
- Memory: <100MB growth over 30 days
- Connection stability: >99.9% uptime
- Message rate: >10 msg/sec sustained
- Alerts: Zero critical alerts

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| SSL breaks connections | Test in staging, gradual rollout |
| Connection rotation causes data loss | Graceful shutdown, soak test validation |
| Memory monitoring overhead | Sample every 30s (minimal overhead) |
| 24h soak test reveals new issues | Budget extra week for fixes |

---

## Timeline Summary

```
┌─────────────┬─────────────┬─────────────┐
│   Week 1    │   Week 2    │   Week 3    │
│  P0 (20h)   │  P1 (13h)   │  Deploy(20h)│
├─────────────┼─────────────┼─────────────┤
│ SSL + Cache │  Heartbeat  │   Deploy    │
│  Rotation   │   Health    │  7-Day Val  │
│  Memory Mon │   Soak Test │             │
└─────────────┴─────────────┴─────────────┘
      ↓               ↓               ↓
  12h Test        24h Test        Production
```

**Total**: 3.3 weeks (53 hours) + 1 week buffer = **4 weeks**

---

## Next Steps

1. **Review & Approve Plan** - Review this implementation plan
2. **Begin Step 01** - SSL Certificate Verification (2h)
3. **Daily Progress Updates** - Update PROGRESS.md after each step
4. **Weekly Validation** - Run validation tests at end of each week

---

**For detailed step-by-step guides**, see [steps/](./steps/) directory.
**For validation procedures**, see [VALIDATION_GUIDE.md](VALIDATION_GUIDE.md).
**For current status**, see [STATUS.md](STATUS.md) and [PROGRESS.md](PROGRESS.md).

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team

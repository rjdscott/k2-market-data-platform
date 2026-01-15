# Phase 5 Validation Guide

**Last Updated**: 2026-01-15
**Phase**: Binance Production Resilience
**Purpose**: Validate 24h+ stable operation with zero downtime

---

## Overview

This guide provides comprehensive validation procedures for Phase 5 improvements. Validation occurs at three levels:
1. **Unit Tests**: Code-level validation (automated)
2. **Integration Tests**: Component interaction validation (automated)
3. **Soak Tests**: Long-running stability validation (automated + manual monitoring)

---

## Validation Levels

### Level 1: Unit Tests (Automated)

**Scope**: Individual function/method correctness
**Duration**: <5 minutes
**Frequency**: Every commit (pre-commit hook + CI/CD)

**Run Command**:
```bash
uv run pytest tests/unit/ -v --cov=src/k2
```

**Success Criteria**:
- All unit tests pass (100%)
- Code coverage >90% for modified files
- No test regressions

**Key Test Files**:
- `tests/unit/test_binance_client.py` - SSL, rotation, ping-pong, memory
- `tests/unit/test_producer.py` - Bounded cache, LRU eviction
- `tests/unit/test_config.py` - New config parameters

---

### Level 2: Integration Tests (Automated)

**Scope**: Component interaction with real Binance WebSocket
**Duration**: 5-10 minutes
**Frequency**: Before PR merge, after each step

**Run Command**:
```bash
uv run pytest tests/integration/test_binance_live.py -v -s
```

**Success Criteria**:
- Connects to production wss://stream.binance.com with SSL enabled
- Receives >100 messages in 60 seconds
- Connection rotation completes gracefully (test with 1h interval)
- Ping-pong heartbeat working (ping every 3min, pong received)
- Memory stable over 2h test (<20MB growth)

**Test Scenarios**:
1. **SSL Verification Test**
   - Connect with SSL enabled
   - Verify certificate validation
   - Assert connection successful

2. **Connection Rotation Test**
   - Set rotation interval to 1h
   - Monitor for 2h
   - Verify 2 rotations occurred
   - Assert no data loss (message continuity)

3. **Heartbeat Test**
   - Monitor ping-pong for 15 minutes
   - Verify 5 pings sent (every 3min)
   - Assert all pongs received within 10s

4. **Memory Stability Test**
   - Run for 2 hours
   - Sample memory every 60s (120 samples)
   - Assert memory growth <20MB

---

### Level 3: Soak Tests (24h+ Validation)

**Scope**: Long-running stability under production load
**Duration**: 24-48 hours
**Frequency**: After P1 complete, before production deployment

**Run Command**:
```bash
# Run 24h soak test (requires 25h timeout)
uv run pytest tests/soak/test_binance_24h_soak.py --timeout=90000 -v -s

# Run shorter 1h validation test
uv run pytest tests/soak/test_binance_1h_validation.py --timeout=4000 -v -s
```

**Success Criteria**:
- 24h continuous operation without crashes
- Memory growth <50MB over 24h
- Message rate >10 msg/sec sustained
- Connection drops <10 over 24h (excluding 4h rotations)
- Zero data integrity errors (message validation)
- Memory leak detection score <0.5

**Monitoring During Soak Test**:
```bash
# Monitor memory usage (run in separate terminal)
watch -n 60 'curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes'

# Monitor connection rotations
watch -n 60 'curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total'

# Monitor message rate
watch -n 60 'curl -s http://localhost:9091/metrics | grep binance_messages_received_total'

# View container stats
watch -n 60 'docker stats k2-binance-stream --no-stream'
```

**Output Artifacts**:
- `binance_soak_memory_profile.json` - Memory samples (1440 samples for 24h)
- `binance_soak_test_report.txt` - Test summary with metrics
- Prometheus data (memory, connections, messages) for 24h period

---

## Validation Checklist

### Pre-Implementation Validation

- [ ] **Baseline Metrics Captured**
  - Current memory usage: ~200MB
  - Current failure pattern: 6-12h
  - Current SSL: Disabled (demo mode)
  - Current monitoring: No memory leak detection

- [ ] **Dependencies Installed**
  - `psutil>=5.9.0` for memory monitoring
  - `certifi>=2024.0.0` for SSL certificates
  - Existing dependencies up-to-date

- [ ] **Test Infrastructure Ready**
  - Docker Compose services running (Kafka, Schema Registry, Prometheus)
  - Prometheus accessible at http://localhost:9090
  - Metrics endpoint accessible at http://localhost:9091/metrics

---

### P0 Critical Fixes Validation (End of Week 1)

#### Step 01: SSL Certificate Verification ✅
- [ ] Unit tests pass (SSL context creation, certificate validation)
- [ ] Integration test: Connects to wss://stream.binance.com with SSL enabled
- [ ] Config flag `ssl_verify` defaults to True
- [ ] Negative test: Connection rejected with invalid certificate
- [ ] Documentation updated (README.md, runbook)

**Verification Commands**:
```bash
# Check SSL enabled in config
grep -A 5 "ssl_verify" src/k2/common/config.py

# Test connection with SSL
uv run python scripts/test_binance_stream.py --duration 60

# Check logs for SSL errors
docker logs k2-binance-stream | grep -i "ssl\|certificate"
```

#### Step 02: Connection Rotation Strategy ✅
- [ ] Unit tests pass (rotation loop, lifetime tracking)
- [ ] Integration test: Connection rotates after 1h (short interval test)
- [ ] Metric visible: `binance_connection_rotations_total`
- [ ] Metric visible: `binance_connection_lifetime_seconds`
- [ ] Rotation logged: "binance_connection_rotation_triggered"
- [ ] No data loss during rotation (message continuity validated)

**Verification Commands**:
```bash
# Check connection rotation metric
curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total

# Check connection lifetime (should reset every 4h)
curl -s http://localhost:9091/metrics | grep binance_connection_lifetime_seconds

# View rotation logs
docker logs k2-binance-stream | grep "rotation_triggered"
```

#### Step 03: Bounded Serializer Cache ✅
- [ ] Unit tests pass (LRU eviction, bounded cache)
- [ ] Test: Create 15 serializers, verify 5 evicted (max_size=10)
- [ ] Metric visible: `serializer_cache_size`
- [ ] Metric visible: `serializer_cache_evictions_total`
- [ ] Config parameter: `serializer_cache_max_size` (default: 10)
- [ ] Memory stable with 100+ schema subjects

**Verification Commands**:
```bash
# Check cache size metric
curl -s http://localhost:9091/metrics | grep serializer_cache_size

# Check evictions
curl -s http://localhost:9091/metrics | grep serializer_cache_evictions_total

# Run cache stress test
uv run pytest tests/unit/test_producer.py::test_serializer_cache_lru_eviction -v
```

#### Step 04: Memory Monitoring & Alerts ✅
- [ ] Unit tests pass (memory monitoring, leak detection)
- [ ] Metric visible: `process_memory_rss_bytes`
- [ ] Metric visible: `process_memory_vms_bytes`
- [ ] Metric visible: `memory_leak_detection_score`
- [ ] Alert configured: `BinanceMemoryHigh` (>400MB)
- [ ] Alert configured: `BinanceMemoryLeakDetected` (score >0.8)
- [ ] psutil dependency installed

**Verification Commands**:
```bash
# Check memory metrics
curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes
curl -s http://localhost:9091/metrics | grep memory_leak_detection_score

# Check alerts configured
curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name=="binance_memory_health")'

# Simulate memory growth (leak detection)
# (manually increase memory allocation, verify score increases)
```

#### 12h Validation Test ✅
- [ ] Run for 12 hours
- [ ] Memory growth <30MB (200MB → <230MB)
- [ ] Connection rotations: 3 (every 4h)
- [ ] Message rate >10 msg/sec sustained
- [ ] Zero critical errors in logs

**Run Command**:
```bash
# Start container and monitor for 12h
docker compose up -d binance-stream
# Monitor for 12h (sample every hour)
for i in {1..12}; do
  echo "=== Hour $i ==="
  docker stats k2-binance-stream --no-stream
  curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes
  sleep 3600
done
```

**Success Criteria**:
- ✅ P0 fixes prevent memory growth beyond 230MB in 12h
- ✅ No connection failures (excluding scheduled rotations)
- ✅ All metrics reporting correctly

---

### P1 Production Readiness Validation (End of Week 2)

#### Step 05: WebSocket Ping-Pong Heartbeat ✅
- [ ] Unit tests pass (ping loop, pong tracking)
- [ ] Integration test: Ping sent every 3min, pong received within 10s
- [ ] Metric visible: `binance_last_pong_timestamp_seconds`
- [ ] Metric visible: `binance_pong_timeouts_total`
- [ ] Config parameter: `ping_interval_seconds` (default: 180)
- [ ] Negative test: Pong timeout triggers reconnect

**Verification Commands**:
```bash
# Check last pong timestamp (should update every 3min)
watch -n 10 'curl -s http://localhost:9091/metrics | grep binance_last_pong_timestamp_seconds'

# Check pong timeouts (should be zero)
curl -s http://localhost:9091/metrics | grep binance_pong_timeouts_total

# View ping logs
docker logs k2-binance-stream | grep -i "ping\|pong"
```

#### Step 06: Health Check Timeout Tuning ✅
- [ ] Config default changed: 60s → 30s
- [ ] Docker Compose environment variable updated
- [ ] Documentation updated (README.md, runbook)
- [ ] Integration test: Reconnect triggered after 30s of no messages

**Verification Commands**:
```bash
# Check config default
grep "health_check_timeout" src/k2/common/config.py

# Check docker-compose env
grep "HEALTH_CHECK_TIMEOUT" docker-compose.yml

# Simulate no messages (stop Binance WebSocket, verify reconnect after 30s)
```

#### Step 07: 24h Soak Test Implementation ✅
- [ ] Test file created: `tests/soak/test_binance_24h_soak.py`
- [ ] Test runs for 24 hours successfully
- [ ] Memory growth <50MB over 24h
- [ ] Message rate >10 msg/sec average
- [ ] Connection drops <10 over 24h
- [ ] Memory profile JSON generated

**Run Command**:
```bash
# Run 24h soak test (background)
nohup uv run pytest tests/soak/test_binance_24h_soak.py --timeout=90000 -v -s > soak_test.log 2>&1 &

# Monitor progress (in separate terminal)
tail -f soak_test.log

# Check memory profile after completion
cat binance_soak_memory_profile.json | jq '.[] | select(.timestamp > (now - 3600)) | .rss_mb'
```

**Success Criteria**:
- ✅ 24h continuous operation without crashes
- ✅ Memory stable (<50MB growth)
- ✅ Connection rotations: 6 (every 4h)
- ✅ Ping-pong heartbeat: 480 pings (every 3min)
- ✅ Message integrity: 100% valid messages

---

### Deployment Validation (Week 3)

#### Step 08: Blue-Green Deployment ✅
- [ ] New image built: `k2-platform:v2.0-stable`
- [ ] Green container deployed alongside blue
- [ ] Both containers producing to Kafka (10 min dual operation)
- [ ] Traffic cutover to green (blue stopped gracefully)
- [ ] Green handling all traffic (15 min validation)
- [ ] Rollback procedure tested (if needed)

**Deployment Commands**:
```bash
# Build new image
docker build -t k2-platform:v2.0-stable .

# Deploy green
docker compose up -d --scale binance-stream=2 --no-recreate

# Monitor both containers
docker stats k2-binance-stream-1 k2-binance-stream-2 --no-stream

# Cutover (stop blue)
docker compose stop binance-stream-1

# Validate green
curl -s http://localhost:9091/metrics | grep binance_connection_status
docker logs k2-binance-stream-2 --tail 100
```

**Success Criteria**:
- ✅ Zero downtime during deployment
- ✅ No message loss (Kafka deduplication working)
- ✅ Green container stable after cutover

#### Step 09: Production Validation (7 Days) ✅
- [ ] Day 1: Memory stable (200-250MB)
- [ ] Day 2: Connection rotations: 12 (6 per day)
- [ ] Day 3: Ping-pong heartbeat: 960 pings (480 per day)
- [ ] Day 4: Message rate sustained (>10 msg/sec)
- [ ] Day 5: Zero unscheduled reconnections
- [ ] Day 6: No critical alerts triggered
- [ ] Day 7: Validation report generated

**Daily Monitoring Checklist**:
```bash
# Memory usage (daily)
curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes

# Connection rotations (should be 6 per day)
curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total

# Last pong timestamp (should update every 3min)
curl -s http://localhost:9091/metrics | grep binance_last_pong_timestamp_seconds

# Message rate (should be >10 msg/sec)
curl -s http://localhost:9091/metrics | grep binance_messages_received_total

# Check alerts (should be zero)
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname | contains("Binance"))'

# Container stats
docker stats k2-binance-stream --no-stream
```

**7-Day Report Template**:
```markdown
# 7-Day Production Validation Report

**Date Range**: 2026-01-XX to 2026-01-YY
**Phase**: Phase 5 - Binance Production Resilience

## Summary
- ✅ Memory stable: 200-250MB (baseline), <50MB growth over 7 days
- ✅ Connection rotations: 42 total (6 per day as scheduled)
- ✅ Uptime: 99.9%+ (zero unscheduled downtime)
- ✅ Message rate: 15 msg/sec average (>10 msg/sec target)
- ✅ Alerts: Zero critical alerts triggered

## Metrics
[Attach Grafana dashboard screenshots]
- Memory usage trend
- Connection lifetime histogram
- Message throughput graph
- Ping-pong latency distribution

## Issues
[List any issues encountered and resolutions]

## Recommendation
✅ **APPROVED for long-term production use**
```

---

## Success Criteria Summary

### Phase 5 Complete When:

**Technical Criteria**:
- [ ] All 9 steps complete (100%)
- [ ] All unit tests pass (100%)
- [ ] All integration tests pass (100%)
- [ ] 24h soak test passes (<50MB memory growth)
- [ ] 7-day production validation complete

**Operational Criteria**:
- [ ] SSL verification enabled in production
- [ ] Memory stable at 200-250MB (baseline)
- [ ] Connection rotations: 6 per day (every 4h)
- [ ] Ping-pong heartbeat: 480 pings per day (every 3min)
- [ ] Message rate: >10 msg/sec sustained
- [ ] Uptime: >99.9% (excluding scheduled rotations)
- [ ] Alerts: Zero critical alerts over 7 days

**Documentation Criteria**:
- [ ] README.md updated with new config options
- [ ] Binance streaming runbook updated
- [ ] Prometheus alert rules documented
- [ ] 7-day validation report completed

---

## Rollback Criteria

**Trigger Rollback If**:
- Memory growth >100MB in 24h (indicates leak not fixed)
- Uptime <95% (indicates instability)
- Critical alerts triggered >3 times in 24h
- Data integrity issues (message loss or corruption)
- SSL connection failures >10% of attempts

**Rollback Procedure**:
```bash
# 1. Start blue container (old version)
docker compose up -d binance-stream-1

# 2. Stop green container (new version)
docker compose stop binance-stream-2

# 3. Verify blue handling traffic
curl -s http://localhost:9091/metrics | grep binance_connection_status

# 4. Remove green container
docker compose down binance-stream-2

# Total rollback time: <2 minutes
```

---

## Troubleshooting

### Issue: SSL Connection Fails

**Symptoms**:
- Error: "SSL: CERTIFICATE_VERIFY_FAILED"
- Container logs show SSL handshake errors

**Diagnosis**:
```bash
# Check SSL context
grep -A 10 "ssl_context" src/k2/ingestion/binance_client.py

# Test SSL connection manually
openssl s_client -connect stream.binance.com:9443 -showcerts
```

**Resolution**:
1. Verify `certifi` package installed: `uv pip list | grep certifi`
2. Check custom CA bundle if corporate proxy: `echo $K2_BINANCE_CUSTOM_CA_BUNDLE`
3. Temporarily disable SSL (testing only): `K2_BINANCE_SSL_VERIFY=false`

### Issue: Memory Still Growing

**Symptoms**:
- Memory leak detection score >0.8
- RSS memory exceeds 400MB

**Diagnosis**:
```bash
# Check serializer cache size
curl -s http://localhost:9091/metrics | grep serializer_cache_size

# Check memory leak score
curl -s http://localhost:9091/metrics | grep memory_leak_detection_score

# Review memory samples
cat binance_soak_memory_profile.json | jq '[.[] | .rss_mb] | add / length'
```

**Resolution**:
1. Verify bounded cache implemented (max 10 entries)
2. Check connection rotation occurring (every 4h)
3. Review memory monitor loop for leaks
4. Consider reducing rotation interval (4h → 3h)

### Issue: Connection Drops Frequently

**Symptoms**:
- Reconnections >10 per day (excluding 4h rotations)
- Pong timeouts increasing

**Diagnosis**:
```bash
# Check reconnection count by reason
curl -s http://localhost:9091/metrics | grep binance_reconnects_total

# Check pong timeouts
curl -s http://localhost:9091/metrics | grep binance_pong_timeouts_total

# Review connection logs
docker logs k2-binance-stream | grep -i "reconnect\|closed\|timeout"
```

**Resolution**:
1. Verify ping-pong heartbeat active (ping every 3min)
2. Check network connectivity to Binance
3. Review health check timeout (should be 30s)
4. Consider increasing ping frequency (3min → 2min)

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Questions?**: See [STATUS.md](./STATUS.md) or [README.md](./README.md)

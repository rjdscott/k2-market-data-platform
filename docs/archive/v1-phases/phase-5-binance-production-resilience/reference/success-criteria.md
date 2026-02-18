# Phase 5 Success Criteria

**Last Updated**: 2026-01-15
**Phase**: Binance Production Resilience
**Goal**: 24h+ stable operation with zero downtime

---

## P0 - Critical Fixes (Week 1)

### SSL Certificate Verification ✅
- [ ] SSL verification enabled (`ssl.create_default_context()` with certificate validation)
- [ ] Connects to production wss://stream.binance.com with SSL
- [ ] Config flag `ssl_verify` defaults to True
- [ ] Unit tests pass (SSL context creation, certificate validation)
- [ ] Integration test passes (connects with SSL enabled)

### Connection Rotation Strategy ✅
- [ ] Connection rotates every 4h (6 rotations per day)
- [ ] Graceful rotation (close code 1000, no data loss)
- [ ] Metric visible: `binance_connection_rotations_total`
- [ ] Metric visible: `binance_connection_lifetime_seconds`
- [ ] Unit tests pass (rotation loop, lifetime tracking)
- [ ] Integration test passes (rotation with 1h interval)

### Bounded Serializer Cache ✅
- [ ] Cache bounded to max 10 entries (LRU eviction)
- [ ] Metric visible: `serializer_cache_size`
- [ ] Metric visible: `serializer_cache_evictions_total`
- [ ] Unit tests pass (create 15 serializers, verify 5 evicted)
- [ ] Memory stable with 100+ schema subjects

### Memory Monitoring & Alerts ✅
- [ ] Memory monitoring active (sample every 30s)
- [ ] Metric visible: `process_memory_rss_bytes`
- [ ] Metric visible: `memory_leak_detection_score`
- [ ] Alert configured: `BinanceMemoryHigh` (>400MB)
- [ ] Alert configured: `BinanceMemoryLeakDetected` (score >0.8)
- [ ] Unit tests pass (memory monitoring, leak detection)

### 12h Validation Test ✅
- [ ] Run for 12 hours without crashes
- [ ] Memory growth <30MB (200MB → <230MB)
- [ ] Connection rotations: 3 (every 4h)
- [ ] Message rate >10 msg/sec sustained
- [ ] Zero critical errors in logs

---

## P1 - Production Readiness (Week 2)

### WebSocket Ping-Pong Heartbeat ✅
- [ ] Ping sent every 3 minutes (180s interval)
- [ ] Pong received within 10s
- [ ] Metric visible: `binance_last_pong_timestamp_seconds`
- [ ] Metric visible: `binance_pong_timeouts_total`
- [ ] Unit tests pass (ping loop, pong tracking)
- [ ] Integration test passes (ping every 3min, pong received)

### Health Check Timeout Tuning ✅
- [ ] Health check timeout reduced from 60s → 30s
- [ ] Docker Compose environment variable updated
- [ ] Documentation updated (README.md, runbook)
- [ ] Integration test passes (reconnect after 30s of no messages)

### 24h Soak Test ✅
- [ ] Test runs for 24 hours successfully
- [ ] Memory growth <50MB over 24h
- [ ] Message rate >10 msg/sec average
- [ ] Connection drops <10 over 24h (excluding scheduled rotations)
- [ ] Zero data integrity errors
- [ ] Memory profile JSON generated

---

## Deployment & Validation (Week 3)

### Blue-Green Deployment ✅
- [ ] New image built: `k2-platform:v2.0-stable`
- [ ] Green container deployed alongside blue
- [ ] 10 min dual-operation (both producing to Kafka)
- [ ] Traffic cutover to green (blue stopped gracefully)
- [ ] Green handling all traffic (15 min validation)
- [ ] Zero downtime during deployment
- [ ] No message loss

### 7-Day Production Validation ✅
- [ ] **Day 1**: Memory stable (200-250MB)
- [ ] **Day 2**: Connection rotations: 12 (6 per day)
- [ ] **Day 3**: Ping-pong heartbeat: 960 pings (480 per day)
- [ ] **Day 4**: Message rate sustained (>10 msg/sec)
- [ ] **Day 5**: Zero unscheduled reconnections
- [ ] **Day 6**: No critical alerts triggered
- [ ] **Day 7**: Validation report generated

---

## Long-Term Success (30 Days)

### Memory Stability ✅
- [ ] Memory growth <100MB over 30 days
- [ ] Memory leak detection score <0.5
- [ ] No `BinanceMemoryHigh` alerts
- [ ] No `BinanceMemoryLeakDetected` alerts

### Connection Stability ✅
- [ ] Uptime >99.9% (excluding scheduled rotations)
- [ ] Connection rotations: 180 total (6 per day × 30 days)
- [ ] Ping-pong heartbeat: 14,400 pings (480 per day × 30 days)
- [ ] Zero pong timeouts
- [ ] Zero unscheduled reconnections

### Data Integrity ✅
- [ ] Message rate >10 msg/sec sustained
- [ ] Zero message loss events
- [ ] Zero data integrity errors
- [ ] Kafka production rate matches message rate

---

## Documentation ✅

### Code Documentation
- [ ] Inline comments for SSL context creation
- [ ] Inline comments for connection rotation logic
- [ ] Inline comments for memory monitoring loop
- [ ] Inline comments for ping-pong heartbeat

### User Documentation
- [ ] README.md updated (new config options, production readiness)
- [ ] Binance streaming runbook updated (new metrics, alerts)
- [ ] Prometheus alert runbook created (BinanceMemoryHigh, BinanceMemoryLeakDetected)
- [ ] Configuration reference updated (all new config parameters)

### Operational Documentation
- [ ] Blue-green deployment procedure documented
- [ ] Rollback procedure documented (<2 min)
- [ ] 7-day validation report completed
- [ ] Troubleshooting guide updated (SSL, memory, connection issues)

---

## Metrics Summary

### Before Phase 5
| Metric | Status |
|--------|--------|
| Memory Usage | ❌ Unbounded growth (fails at 6-12h) |
| SSL Verification | ❌ Disabled (demo mode) |
| Connection Lifetime | ❌ Undefined (fails at 6-12h) |
| Memory Monitoring | ❌ None |
| Soak Testing | ❌ None |
| Connection Rotations | ❌ None |
| WebSocket Heartbeat | ❌ None |

### After Phase 5
| Metric | Target | Actual |
|--------|--------|--------|
| Memory Usage | 200-250MB stable, <50MB growth over 24h | TBD |
| SSL Verification | ✅ Enabled with proper certificates | TBD |
| Connection Lifetime | 4h rotation (6 per day) | TBD |
| Memory Monitoring | ✅ Active with leak detection | TBD |
| Soak Testing | ✅ Automated 24h test suite | TBD |
| Connection Rotations | 6 per day (every 4h) | TBD |
| WebSocket Heartbeat | 480 pings per day (every 3min) | TBD |

---

## Acceptance Criteria

### Phase 5 COMPLETE When:

1. **All 9 steps complete** (100%)
2. **All tests pass** (unit, integration, soak)
3. **24h soak test passes** (<50MB memory growth)
4. **7-day production validation complete** (all daily checks pass)
5. **All documentation updated** (code, user, operational)
6. **Zero critical alerts** over 7 days
7. **Uptime >99.9%** (excluding scheduled rotations)

### Sign-Off Required From:
- [ ] Staff Data Engineer (implementation)
- [ ] Platform Team Lead (review)
- [ ] SRE/DevOps (deployment approval)
- [ ] Principal Engineer (architecture review)

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team

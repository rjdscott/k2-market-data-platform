# K2 Platform - Demo Quick Reference

> **Historical Context**: This reference was created in [Phase 4: Demo Readiness](../../docs/phases/v1/phase-4-demo-readiness/README.md) and validated in [Phase 8: E2E Demo Validation](../../docs/phases/v1/phase-8-e2e-demo/README.md).

**Date**: 2026-01-14
**Version**: Phase 4 Demo Readiness
**Purpose**: One-page reference for instant navigation during Q&A

---

## Critical Files by Topic

### Architecture Questions
- **Platform positioning**: `docs/architecture/platform-positioning.md` (L3 cold path, <500ms target)
- **System design**: `docs/architecture/system-design.md` (Kafka → Iceberg → DuckDB → FastAPI)
- **Technology choices**: `docs/architecture/technology-decisions.md` (Why Kafka/Iceberg/DuckDB)
- **Platform principles**: `docs/architecture/platform-principles.md` (Idempotency, deduplication)

### Implementation Questions
- **V2 Schema**: `docs/architecture/schema-design-v2.md` (industry-standard fields)
- **Binance streaming**: `src/k2/sources/binance_stream.py` (289 lines, WebSocket client)
- **Circuit breaker**: `src/k2/common/degradation_manager.py` (5 levels, 304 lines, 34 tests)
- **Hybrid queries**: `src/k2/query/hybrid_engine.py` (400+ lines, Kafka + Iceberg merge)
- **Query engine**: `src/k2/query/engine.py` (DuckDB connection pool)
- **API endpoints**: `src/k2/api/v1/endpoints.py` (FastAPI routes)

### Operational Questions
- **Cost model**: `docs/operations/cost-model.md` ($0.85/M msgs at 1M msg/sec scale)
- **Monitoring**: 83 validated Prometheus metrics (pre-commit hook)
- **Runbooks**: `docs/operations/runbooks/` (incident response procedures)
- **Performance results**: `docs/phases/phase-4-demo-readiness/reference/performance-results.md`

---

## Key Numbers (MEMORIZE)

### Query Performance (Measured - Step 03)
- **API Endpoint (Full Stack)**: p50 = 388ms, p99 = 681ms ✅
- **Time Range Scan**: p50 = 161ms, p99 = 299ms ✅ (excellent)
- **Simple Filter**: p50 = 229ms, p99 = 2545ms (cold cache outliers)
- **Aggregation**: p50 = 257ms, p99 = 531ms ✅
- **Multi-Symbol**: p50 = 558ms, p99 = 4440ms (expected - multi-partition)

### Ingestion Performance
- **Historical**: 138 msg/sec (single-node, measured in previous sessions)
- **Target scale**: 1M msg/sec (multi-node deployment with horizontal scaling)
- **Current demo**: 2 symbols (BTCUSDT, ETHUSDT), V2 schema

### Storage Efficiency
- **Compression ratio**: 10.0:1 (Parquet + Snappy) ✅
- **Target range**: 8-12:1 compression
- **Cost impact**: $0.85 per million messages at scale

### Platform Maturity
- **Tests**: 86+ tests, 95%+ coverage (Phase 3)
- **Metrics**: 83 validated Prometheus metrics
- **Binance demo**: 69,666+ messages processed successfully (Phase 2)
- **Phase 4 score**: 85/100 (current), target 95+/100

### Resource Usage (During Benchmark)
- **CPU**: 91.7% (high during 20 iterations × 5 query types)
- **Memory**: 4728 MB (92.6%)
- **Disk**: 10.5 GB (56.3%)

---

## Common Questions & Answers

### Q: Why not HFT/low-latency execution?
**A**: Clear positioning - **L3 cold path** (<500ms) for analytics/compliance. We're targeting research data platforms, not L1 execution (which requires <10μs, 50,000× faster). See `platform-positioning.md` for latency tier breakdown.

### Q: Why DuckDB vs Presto/Trino?
**A**: **DuckDB for single-node demo**, Presto planned for Phase 5 multi-node scale. Trade-off: simplicity now (embedded, zero-ops, <1GB memory) vs distributed scale later. Our measured p50=388ms proves DuckDB efficiency for L3 analytics.

### Q: How does schema evolution work?
**A**: **Iceberg native schema evolution** with BACKWARD compatibility via Avro + Schema Registry. V1→V2 migration complete (Phase 2), no data rewrites needed. See `schema-design-v2.md`.

### Q: What happens when the system is overloaded?
**A**: **5-level circuit breaker** with graceful degradation (NORMAL → LIGHT → MODERATE → SEVERE → CRITICAL). Automatically sheds low-priority data (Tier 3 symbols), maintains high-value data (BTC, ETH). See `degradation_manager.py` (Step 05 will demo).

### Q: Cost comparison with alternatives?
**A**: **$0.85 per million messages** at 1M msg/sec scale vs:
- Snowflake: ~$25K/month (35-40% savings)
- kdb+: $50K+ license fees
- Built on open source (Kafka, Iceberg, DuckDB), portable across clouds. See `cost-model.md`.

### Q: Time-travel queries - how?
**A**: **Iceberg snapshots** enable querying any historical point. SQL syntax: `FOR SYSTEM_TIME AS OF 'timestamp'`. Every write creates immutable snapshot, no extra storage cost (copy-on-write with efficient metadata).

### Q: Single-node now, how does it scale?
**A**: **Current**: Single-node (laptop), 138 msg/sec measured.
**Phase 5**: Multi-node with Presto (distributed queries), target 1M msg/sec.
Architecture designed for horizontal scaling: Kafka partitions, Iceberg bucketing (16 buckets per symbol), stateless API.

### Q: Why Kafka vs alternatives (Pulsar, Kinesis)?
**A**: **Proven at scale** (Netflix 8M+ msg/sec), strong ecosystem (Schema Registry, Connect), open source (no vendor lock-in). Trade-off: higher ops burden vs managed service. See `technology-decisions.md`.

### Q: Why Avro vs Protobuf/JSON?
**A**: **Native Kafka ecosystem** support, Schema Registry integration (BACKWARD compatibility), compact binary (2-3× smaller than JSON). Dynamic schema resolution without code generation. See `technology-decisions.md`.

---

## URLs to Have Open (Demo Day)

- **Grafana**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090 (metrics query)
- **API Documentation**: http://localhost:8000/docs (FastAPI Swagger UI)
- **MinIO Console**: http://localhost:9001 (admin/password)
- **Kafka UI**: http://localhost:8080 (topic browser)

---

## Demo Flow Reminders (12 minutes total)

1. **Positioning First** (1 min)
   - L3 cold path for analytics/compliance, NOT L1 HFT execution
   - Clear about 500ms p99 latency (5,000× slower than HFT)
   - Targets: quant research, compliance reporting, historical analysis

2. **Live Streaming** (2 min)
   - Show Binance WebSocket logs: `docker logs k2-binance-stream --follow`
   - Demonstrate real-time ingestion (138 msg/sec historical rate)
   - Kafka topic: `market.crypto.trades.binance`

3. **Storage & Time-Travel** (2 min)
   - Iceberg snapshots with ACID transactions
   - Time-travel demo: Query historical data at any point
   - 10:1 compression ratio (Parquet + Snappy)

4. **Monitoring** (2 min)
   - Grafana dashboards: 83 validated metrics
   - Show degradation level indicator (current: NORMAL)
   - Prometheus: Real-time metric queries

5. **Resilience Demo** (2 min) - NEW in Step 05
   - Simulate lag: Circuit breaker activates
   - Show degradation cascade: NORMAL → GRACEFUL → SEVERE
   - Automatic recovery with hysteresis
   - Grafana: State changes visible in real-time

6. **Hybrid Queries** (2 min)
   - Query recent data: Kafka (last 15 min) + Iceberg (historical)
   - DuckDB merge: Seamless to user
   - API endpoint: `/v1/trades?symbol=BTCUSDT`

7. **Cost Model** (1 min)
   - $0.85/M messages vs $25K/month Snowflake
   - 35-40% savings, open source portability
   - Evidence-based metrics (measured p50=388ms)

---

## Key Talking Points (Operational Maturity)

### Circuit Breaker (Production-Grade Resilience)
"This isn't just 'does it work' - it's 'how does it fail gracefully?'"

- **5-level degradation**: Gradual cascade prevents cliff-edge failures
- **Priority-based load shedding**: High-value data (BTC, ETH) continues, low-priority (DOGE, ADA) shed
- **Automatic recovery**: Hysteresis prevents state flapping
- **Prometheus metrics**: Track degradation level, messages shed, symbols dropped

### Evidence-Based Performance (Staff Engineer Credibility)
"We measured actual performance, not projections from cost models."

- **20 iterations per query**: Statistical validity (p50, p95, p99)
- **5 query types**: Simple filter, aggregation, multi-symbol, time range, API endpoint
- **Resource usage captured**: CPU, memory, disk during real workload
- **Compression validated**: 10:1 ratio measured, within 8-12:1 target

### Architectural Decisions (Clear Rationale)
"Every technology choice has evidence and trade-offs clearly documented."

- **DuckDB vs Presto**: Simplicity now (embedded, fast), scale later (distributed)
- **Kafka vs alternatives**: Proven scale (Netflix 8M+ msg/sec), open source
- **Iceberg vs Delta/Hudi**: ACID + time-travel (compliance requirement), vendor-neutral
- **L3 positioning**: Honest about latency, clear target (research not execution)

---

## Pre-Demo Checklist (2 hours before)

**Infrastructure**:
- [ ] All Docker services "Up": `docker compose ps`
- [ ] Binance stream ingesting: `docker logs k2-binance-stream --tail 20`
- [ ] Kafka has messages: Check Kafka UI http://localhost:8080
- [ ] Iceberg has data: `curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/symbols`
- [ ] Prometheus scraping: http://localhost:9090/-/healthy
- [ ] Grafana dashboards loading: http://localhost:3000

**Validation**:
- [ ] Run `scripts/performance_benchmark.py --iterations=5` (quick check)
- [ ] Test API endpoint: `/v1/trades?symbol=BTCUSDT&limit=5`
- [ ] Verify circuit breaker demo: `scripts/simulate_failure.py` (Step 05, not yet created)
- [ ] Check quick reference printed and nearby

**Environment**:
- [ ] Laptop charged 100%
- [ ] Do Not Disturb enabled
- [ ] Notifications disabled
- [ ] Browser tabs open (Grafana, Prometheus, API docs)
- [ ] Terminal windows ready (`docker compose logs`, `htop`)

---

## Emergency Backup Plan

### If Demo Fails → Switch Immediately

**Option 1: Pre-Executed Notebook** (Step 07, not yet created)
- Location: `notebooks/binance-demo-with-outputs.ipynb`
- Recovery time: 30 seconds
- Walk through outputs without re-running

**Option 2: Recorded Demo Video** (Step 07, not yet created)
- Location: `docs/phases/phase-4-demo-readiness/reference/demo-recording.mp4`
- Recovery time: 30 seconds
- Narrate over video

**Option 3: Static Screenshots** (Step 07, not yet created)
- Location: `docs/phases/phase-4-demo-readiness/reference/screenshots/`
- Recovery time: 1 minute
- Show key system states

**Option 4: Demo Mode Script** (Step 07, not yet created)
- Command: `python scripts/demo_mode.py --reset --load`
- Recovery time: 5 minutes
- Resets system to known-good state with pre-loaded data

---

## Phase Progress Snapshot

**Current State** (2026-01-14):
- **Score**: 85/100 (target: 95/100)
- **Steps Complete**: 3/10 (30%)
- **Next**: Step 05 (Resilience Demo), Step 06 (Architecture Decisions), Step 07 (Backup Plans)

**Completed Steps**:
1. ✅ **Step 01**: Infrastructure Startup (all 9 services healthy)
2. ✅ **Step 02**: V2 Schema Migration (native v2 fields, 63/63 tests passing)
3. ✅ **Step 03**: Performance Benchmarking (measured metrics, evidence-based)

**High-Priority Remaining**:
- **Step 04**: Quick Reference Creation ← **YOU ARE HERE**
- **Step 05**: Resilience Demonstration (circuit breaker demo in notebook)
- **Step 06**: Architecture Decisions Summary (Why X vs Y)
- **Step 07**: Backup Plans (recorded demo, screenshots, demo mode script)

---

**Last Updated**: 2026-01-14
**Print This**: Keep next to laptop during demo
**Memorize**: Key numbers (388ms p50, 681ms p99, 10:1 compression, $0.85/M msgs)

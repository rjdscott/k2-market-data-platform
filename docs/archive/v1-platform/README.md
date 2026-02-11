# K2 Platform V1 - Historical Archive

**Platform Version**: 1.0 (Python + Kafka + Spark Streaming + DuckDB + Prefect)
**Active Period**: 2025-Q4 to 2026-Q1
**Archived**: 2026-02-09
**Superseded By**: V2 Platform (Kotlin + Redpanda + ClickHouse)

---

## What Was V1?

The V1 K2 Market Data Platform was a Python-based streaming analytics platform designed for real-time crypto market data processing.

### Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| **Language** | Python | 3.13 |
| **Streaming** | Apache Kafka | 3.6 |
| **Processing** | Spark Streaming | 3.5 |
| **Storage** | Apache Iceberg | 1.4 |
| **Query Engine** | DuckDB | 0.9 |
| **Orchestration** | Prefect | 2.14 |
| **API** | FastAPI | 0.109 |
| **Containers** | Docker | 24.x |

### Architecture

```
Exchange APIs (REST/WebSocket)
    ↓
Python Feed Handlers (asyncio)
    ↓
Apache Kafka (9 brokers, 3 replicas)
    ↓
Spark Streaming (3 workers, 13.5 CPU, 19.75GB)
    ├─→ Bronze Layer (raw, Iceberg)
    ├─→ Silver Layer (validated, Iceberg)
    └─→ Gold Layer (aggregated, Iceberg)
         ↓
DuckDB (query engine, 1 CPU, 2GB)
    ↓
FastAPI (REST API, 2 CPU, 2GB)
    ↓
Prefect (OHLCV batch jobs, 4 CPU, 5GB)

Total Resources: 35-40 CPU / 45-50GB RAM (18-20 services)
```

---

## Why V2?

The decision to redesign the platform (v2) was driven by operational complexity and resource inefficiency.

### Key Drivers

1. **Resource Overhead**: 35-40 CPU / 45-50GB for basic streaming
2. **Operational Complexity**: 18-20 services to manage
3. **Spark Streaming Overkill**: 13.5 CPU / 19.75GB for simple transformations
4. **DuckDB Limitations**: Single-node query engine, no distribution
5. **Prefect Overhead**: 4 CPU / 5GB for simple batch jobs
6. **Cost**: High compute costs for relatively simple workloads

### V2 Improvements

| Metric | V1 | V2 | Improvement |
|--------|-----|-----|-------------|
| **CPU** | 35-40 | 15.5 | **~60% reduction** |
| **Memory** | 45-50GB | 19.5GB | **~60% reduction** |
| **Services** | 18-20 | 11 | **~45% reduction** |
| **Latency** | Seconds | Sub-second | **6x faster** |
| **Complexity** | High | Medium | Simpler ops |

See [ADR-001 through ADR-010](../../decisions/platform-v2/) for detailed rationale.

---

## Where to Find V1 Code

### Git Access

**Tag**: `v1.0.0` (commit `09bbff3`)
```bash
# View v1 code
git checkout v1.0.0

# Create v1 branch for reference
git checkout -b v1-reference v1.0.0
```

**Branches**:
- `main` - Contains v1.0.0 tagged commit
- `v1-reference` - Can be created from tag for reference

### Docker Compose

**V1 Baseline**: `docker/v1-baseline.yml`
- Complete v1 stack definition
- Ready for rollback if needed
- Snapshot taken before v2 migration

---

## V1 Documentation Map

### Active Documentation (Preserved)

| Location | Content |
|----------|---------|
| `docs/phases/v1/` | All 14 phases of v1 development |
| `docs/decisions/platform-v1/` | V1 architecture decisions (3 ADRs) |
| `demos/` | Demo artifacts and validation |

### Archived Documentation (This Directory)

| Location | Content |
|----------|---------|
| `progress-tracking/` | Historical progress logs, TODO lists |
| `lessons-learned/` | Post-mortems, analyses, optimizations |
| `sessions/` | Implementation session logs |
| `infrastructure/` | V1-specific configs (if any) |
| `operations/` | V1-specific operational runbooks |

---

## V1 Phase Overview

### Completed Phases (14 total)

| Phase | Name | Status | Key Deliverables |
|-------|------|--------|------------------|
| 0 | Technical Debt Resolution | ✅ Complete | Critical fixes, testing |
| 1 | Single-Node Equities | ✅ Complete | Foundation, DuckDB integration |
| 2 | Multi-Source Prep | ✅ Complete | V2 schema, Binance integration |
| 3 | Demo Enhancements | ✅ Complete | Platform narrative, demo flow |
| 4 | Demo Readiness | ✅ Complete | Final validation, performance tests |
| 5 | Binance Production | ✅ Complete | Resilience, error handling |
| 6 | CI/CD Pipeline | ✅ Complete | GitHub Actions, testing |
| 7 | End-to-End Tests | ✅ Complete | Integration testing |
| 8 | E2E Demo | ✅ Complete | Demo validation |
| 9 | Demo Consolidation | ✅ Complete | Documentation cleanup |
| 10 | Streaming Crypto | ✅ Complete | Real-time crypto ingestion |
| 11 | Production Readiness | 🟡 Planned | Not completed |
| 12 | Flink Bronze | ✅ Complete | Flink experimentation |
| 13 | OHLCV Analytics | ✅ Complete | Aggregation pipeline |

**Total Effort**: ~6 months development (Q4 2025 - Q1 2026)

---

## V1 Lessons Learned

### What Worked Well

✅ **Iceberg for Storage**: Time-travel, schema evolution, ACID transactions
✅ **DuckDB for Analytics**: Fast columnar queries, Parquet integration
✅ **Modular Architecture**: Clear separation of concerns
✅ **CI/CD Pipeline**: Automated testing and deployment
✅ **Comprehensive Testing**: 260+ tests, 80%+ coverage

### What Didn't Work

❌ **Spark Streaming**: Massive overkill for simple transformations
❌ **Service Sprawl**: Too many services for operational simplicity
❌ **Resource Overhead**: 35-40 CPU too high for workload
❌ **Prefect for Simple Jobs**: Heavy orchestrator for basic batch
❌ **Python Async Complexity**: Event loop management challenges

### Key Insights

1. **Right-size technology**: Match complexity to problem domain
2. **Operational simplicity > Feature richness**: Fewer services = easier ops
3. **Cost matters**: Resource efficiency directly impacts viability
4. **Modern alternatives**: Redpanda, ClickHouse offer better value
5. **Kotlin > Python**: For performance-critical streaming workloads

---

## V1 Performance Metrics

### Ingestion

- **Throughput**: 5,000-10,000 trades/sec
- **Latency**: p50: 500ms, p95: 2s, p99: 5s
- **Reliability**: 99.5% uptime

### Storage

- **Bronze Layer**: ~1GB/day per exchange
- **Silver Layer**: ~800MB/day per exchange
- **Gold Layer**: ~100MB/day (OHLCV)
- **Retention**: 7d bronze, 90d silver, 1yr gold

### Query Performance

- **Simple Queries**: 50-200ms (DuckDB)
- **Complex Aggregations**: 500ms-2s (DuckDB)
- **OHLCV Queries**: 100-500ms (pre-aggregated)

### Resource Consumption

- **Steady State**: 35 CPU / 45GB RAM
- **Peak Load**: 40 CPU / 50GB RAM
- **Cost**: ~$500-800/month (AWS estimate)

---

## Migration to V2

### Migration Status

- ✅ V2 architecture designed (8 phases planned)
- ✅ Phase 1 complete (infrastructure baseline)
- ✅ Phase 2 complete (Redpanda deployed)
- ✅ Phase 3 complete (Feed handler + ClickHouse schema)
- 🟡 Phase 4-8 in progress

### Coexistence Strategy

V1 and V2 can run side-by-side during transition:
- Different Docker networks (`k2-net` vs `k2-v2_k2-net`)
- Different topics (can share Kafka/Redpanda if needed)
- Different ports (no conflicts)

### Rollback Plan

If v2 encounters critical issues:
```bash
# Stop v2
docker compose -p k2-v2 -f docker-compose.v2.yml down

# Start v1
docker compose -f docker/v1-baseline.yml up -d
```

---

## V1 Decision Rationale

### Why Kafka?

**Decision**: Apache Kafka for streaming backbone

**Rationale**:
- Industry standard for streaming
- Mature ecosystem
- Strong durability guarantees
- Extensive tooling

**Trade-off**: Heavy resource footprint (9 brokers, replication)

See: [ADR-001: Spark Streaming](adr/platform-v1/ADR-001-spark-streaming-optimization.md)

### Why Spark Streaming?

**Decision**: Spark for stream processing

**Rationale**:
- Unified batch + streaming
- Rich transformation APIs
- Iceberg integration
- Fault-tolerant

**Trade-off**: 13.5 CPU / 19.75GB for simple transforms (overkill)

See: [ADR-003: Stream Processing Engine](adr/platform-v1/ADR-003-stream-processing-engine-selection.md)

### Why Bronze Per Exchange?

**Decision**: Separate bronze layer per exchange

**Rationale**:
- Exchange-specific schemas
- Independent processing
- Clear ownership
- Debugging ease

**Trade-off**: More topics, more storage

See: [ADR-002: Bronze Per Exchange](adr/platform-v1/ADR-002-bronze-per-exchange.md)

---

## V1 Code Artifacts

### Python Modules (at v1.0.0)

| Module | Purpose | LOC |
|--------|---------|-----|
| `src/k2/ingestion/` | Feed handlers, Kafka producers | ~2,000 |
| `src/k2/streaming/` | Spark streaming jobs | ~3,000 |
| `src/k2/storage/` | Iceberg table management | ~1,500 |
| `src/k2/query/` | DuckDB query layer | ~1,000 |
| `src/k2/api/` | FastAPI endpoints | ~800 |
| `src/k2/orchestration/` | Prefect flows | ~600 |

**Total**: ~9,000 LOC (Python)

### Test Coverage

- **Unit Tests**: 180 tests
- **Integration Tests**: 60 tests
- **E2E Tests**: 20 tests
- **Total**: 260 tests
- **Coverage**: 82%

---

## V1 Operational Runbooks

V1-specific operational procedures (if any) are archived in `operations/`.

For current (v2) operational procedures, see: `docs/operations/`

---

## V1 Team & Acknowledgments

### Contributors

- Engineering Team (2025-2026)
- Claude Code (AI Assistant) - Implementation support
- Architecture Review Board

### Key Milestones

- **2025-10**: Project kickoff
- **2025-11**: Phase 1 complete (single-node)
- **2025-12**: Demo ready (phases 3-4)
- **2026-01**: Production deployment (phases 5-10)
- **2026-02**: V2 planning and initial implementation
- **2026-02-09**: V1 archived, V2 becomes primary

---

## References

### V1 Documentation

- [V1 Phase Documentation](../../phases/v1/)
- [V1 Architecture Decisions](adr/platform-v1/)
- [V1 Operations](../../operations/) (v1-specific runbooks)

### V2 Documentation

- [V2 Architecture Overview](../../ARCHITECTURE-V2.md)
- [V2 Phase Plan](../../phases/v2/)
- [V2 Architecture Decisions](../../decisions/platform-v2/)

### Historical Context

- [Platform Review (Feb 2026)](../../reviews/platform-review-feb-2026.md)
- [Resource Analysis](../../phases/v2/resource-measurements/)
- [Migration Guide](../../phases/v2/PHASE-ADAPTATION.md)

---

## Archival Metadata

**Archived By**: Engineering Team
**Archival Date**: 2026-02-09
**Archival Reason**: Platform redesign (v2) supersedes v1
**Preservation Goal**: Historical reference, decision context, lessons learned
**Access**: Git tag v1.0.0, this archive directory

---

**Last Updated**: 2026-02-09
**Status**: Archived (Historical Reference Only)
**Successor**: V2 Platform (Kotlin + Redpanda + ClickHouse)

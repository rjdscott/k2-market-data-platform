# Phase 10: Production Crypto Streaming Platform

**Status**: ⬜ Not Started
**Target**: Best-in-class single-node crypto streaming platform with Spark + Medallion architecture
**Duration**: 20 days (160 hours)
**Last Updated**: 2026-01-18

---

## Overview

Phase 10 represents a **complete platform refactor** from mixed equity/crypto platform to a best-in-class crypto-only streaming platform. This phase implements:

1. **Complete ASX/Equity Removal** - Eliminate all equity-specific code, schemas, and data
2. **Fresh Crypto Schema Design** - Recreate schemas from scratch optimized for crypto markets
3. **Spark Streaming Integration** - Replace single-node Python consumers with distributed Spark jobs
4. **Full Medallion Architecture** - Implement Bronze → Silver → Gold layers with proper transformations
5. **Multi-Source Unified Data** - Combine Binance + Kraken into single gold trades table
6. **Kraken Integration** - Add second crypto exchange mirroring Binance patterns

### Strategic Goals

- **Simplicity**: Remove complexity from mixed asset class support
- **Performance**: Spark Streaming for distributed processing at scale
- **Quality**: Medallion architecture with proper data validation layers
- **Extensibility**: Pattern for adding future exchanges (Coinbase, Krax, Bybit)
- **Production-Ready**: Comprehensive testing, monitoring, and documentation

---

## Current Status (2026-01-18)

### Completed: Bronze Streaming Infrastructure ✅

The Bronze layer (Kafka → Iceberg raw ingestion) is **fully operational** with production-ready Docker services:

**Services Running**:
- `bronze-binance-stream`: Auto-managed Spark job consuming from `market.crypto.trades.binance`
- `bronze-kraken-stream`: Auto-managed Spark job consuming from `market.crypto.trades.kraken`

**Key Features**:
- ✅ Automatic startup with `docker-compose up -d`
- ✅ Auto-restart on failure (`restart: unless-stopped`)
- ✅ Resource allocation: 2 cores per job (prevents starvation)
- ✅ Checkpointing enabled for fault tolerance (`/checkpoints/bronze-{exchange}/`)
- ✅ AWS SDK v2 region configuration for MinIO S3 compatibility
- ✅ Health checks on dependencies (Spark, Kafka, Iceberg)

**Data Flow**:
```
Binance Producer → Kafka (market.crypto.trades.binance) → Bronze Job → bronze_binance_trades (Iceberg)
Kraken Producer  → Kafka (market.crypto.trades.kraken)  → Bronze Job → bronze_kraken_trades (Iceberg)
```

**Management**:
```bash
# Start Bronze services
docker-compose up -d bronze-binance-stream bronze-kraken-stream

# Check logs
docker logs k2-bronze-binance-stream -f
docker logs k2-bronze-kraken-stream -f

# Monitor in Spark UI
http://localhost:8090
# Should show both jobs RUNNING with 2 cores each
```

**Documentation**:
- [Bronze Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-streaming-troubleshooting.md)
- [Spark Iceberg Queries Notebook](../../../demos/notebooks/spark-iceberg-queries.ipynb)

### Next Steps

**Phase 5.11: Silver Transformation Job** (pending)
- Deserialize V2 Avro from Bronze
- Validate trade records (price > 0, timestamp valid, etc.)
- Write to per-exchange Silver tables
- Handle DLQ for invalid records

**Phase 5.12: Gold Aggregation Job** (pending)
- Union Silver tables (Binance + Kraken)
- Deduplicate by message_id
- Add derived fields (exchange_date, exchange_hour)
- Write to Gold unified table

---

## Quick Links

- [Implementation Plan](./IMPLEMENTATION_PLAN.md) - Detailed 12-step implementation plan
- [Progress Tracker](./PROGRESS.md) - Step-by-step progress tracking
- [Status](./STATUS.md) - Current blockers and status
- [Validation Guide](./VALIDATION_GUIDE.md) - Acceptance criteria and validation
- [Decisions Log](./DECISIONS.md) - Key architectural decisions

---

## Implementation Steps Overview

### Milestone 1: Complete Cleanup (3 days)
**Goal**: Remove all ASX/equity code and data, clean slate

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [01](./steps/step-01-asx-removal.md) | Remove ASX code and data | 8h | ⬜ |
| [02](./steps/step-02-schema-cleanup.md) | Remove v1 and equity schemas | 6h | ⬜ |
| [03](./steps/step-03-iceberg-cleanup.md) | Drop existing Iceberg tables | 2h | ⬜ |

**Acceptance Criteria**: Zero ASX references, zero equity schemas, clean Iceberg catalog

---

### Milestone 2: Fresh Schema Design (2 days)
**Goal**: Create crypto-optimized schemas from scratch

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [04](./steps/step-04-crypto-schema-design.md) | Design crypto-only v3 schema | 8h | ⬜ |
| [05](./steps/step-05-schema-registry.md) | Register schemas in Schema Registry | 4h | ⬜ |

**Acceptance Criteria**: v3 crypto schema published, backward compatibility removed

---

### Milestone 3: Spark Cluster Setup (2 days)
**Goal**: Production Spark infrastructure

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [06](./steps/step-06-spark-infrastructure.md) | Docker Compose + Spark cluster | 12h | ⬜ |

**Acceptance Criteria**: Spark Web UI accessible, 2 workers registered, test job runs

---

### Milestone 4: Medallion Tables (3 days)
**Goal**: Create Bronze/Silver/Gold Iceberg tables

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [07](./steps/step-07-bronze-table.md) | Bronze table creation | 6h | ⬜ |
| [08](./steps/step-08-silver-tables.md) | Silver tables (per-exchange) | 8h | ⬜ |
| [09](./steps/step-09-gold-table.md) | Gold unified table | 6h | ⬜ |

**Acceptance Criteria**: 4 tables created (1 bronze, 2 silver, 1 gold) with proper partitioning

---

### Milestone 5: Spark Streaming Jobs (5 days)
**Goal**: Kafka → Bronze → Silver → Gold transformations

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [10](./steps/step-10-bronze-job.md) | Bronze ingestion job | 20h | ⬜ |
| [11](./steps/step-11-silver-job.md) | Silver transformation job | 24h | ⬜ |
| [12](./steps/step-12-gold-job.md) | Gold aggregation job | 16h | ⬜ |

**Acceptance Criteria**: 3 streaming jobs running, checkpointing works, data flows E2E

---

### Milestone 6: Kraken Integration (3 days)
**Goal**: Second crypto source

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [13](./steps/step-13-kraken-client.md) | Kraken WebSocket client | 16h | ⬜ |
| [14](./steps/step-14-kraken-validation.md) | Kraken integration testing | 8h | ⬜ |

**Acceptance Criteria**: Kraken trades flowing to gold table, unified with Binance

---

### Milestone 7: Testing & Validation (2 days)
**Goal**: Comprehensive E2E validation

| Step | Focus | Est | Status |
|------|-------|-----|--------|
| [15](./steps/step-15-e2e-testing.md) | E2E pipeline testing | 12h | ⬜ |
| [16](./steps/step-16-performance-validation.md) | Performance benchmarking | 8h | ⬜ |

**Acceptance Criteria**: All tests pass, latency <5min, queries <500ms

---

## Key Metrics

### Before Phase 10
- Asset classes: Equity (ASX) + Crypto (Binance)
- Schema: V1 (legacy) + V2 (hybrid)
- Processing: Single-node Python consumers
- Architecture: Direct Kafka → Iceberg
- Exchanges: ASX (equities) + Binance (crypto)
- Tables: Mixed purpose, unclear separation

### After Phase 10
- Asset classes: Crypto only
- Schema: V3 (crypto-optimized, clean)
- Processing: Spark Structured Streaming (distributed)
- Architecture: Medallion (Bronze → Silver → Gold)
- Exchanges: Binance + Kraken (extensible pattern)
- Tables: Clear layers (4 tables with purpose)

---

## Architecture Comparison

### Current (Before Phase 10)
```
Binance WebSocket → Kafka → Python Consumer → Iceberg trades_v2 table
ASX CSV → Kafka → Python Consumer → Iceberg trades_v2 table (shared)
```

### Target (After Phase 10)
```
Binance WebSocket → Kafka → Spark → Bronze (raw) → Spark → Silver-Binance → Spark → Gold (unified)
Kraken WebSocket → Kafka → Spark → Bronze (raw) → Spark → Silver-Kraken → Spark → Gold (unified)
```

---

## Success Criteria

Phase 10 is complete when:

### Milestone 1: Cleanup Complete
- [x] Zero references to "ASX" or "equities" in codebase
- [x] V1 schema deleted from Schema Registry
- [x] Old Iceberg tables dropped, catalog clean

### Milestone 2: Schema Design Complete
- [x] V3 crypto schema published to Schema Registry
- [x] Backward compatibility with V2 removed
- [x] Schema supports Binance + Kraken + future exchanges

### Milestone 3: Spark Cluster Operational
- [x] Spark Web UI accessible (http://localhost:8080)
- [x] 2 workers registered with 4 cores each
- [x] Test job submitted and completes successfully

### Milestone 4: Medallion Tables Created
- [x] Bronze table: `market_data.bronze_crypto_trades` (7 days retention)
- [x] Silver tables: `silver_binance_trades`, `silver_kraken_trades` (30 days)
- [x] Gold table: `market_data.gold_crypto_trades` (unlimited retention)
- [x] All tables have proper partitioning and compression

### Milestone 5: Spark Jobs Operational
- [x] Bronze job: Kafka → Bronze (<10s latency)
- [x] Silver job: Bronze → Silver (<30s latency, DLQ works)
- [x] Gold job: Silver → Gold (<60s latency, dedup works)
- [x] Checkpointing functional, jobs recover on restart

### Milestone 6: Kraken Integrated
- [x] Kraken WebSocket client streaming to Kafka
- [x] Kraken trades in silver_kraken_trades table
- [x] Kraken trades in gold_crypto_trades table (unified with Binance)

### Milestone 7: Validation Complete
- [x] E2E test: 10K trades WebSocket → Gold (<5 min)
- [x] Query performance: p99 <500ms for 1-hour range
- [x] Data quality: Zero invalid records in Silver, DLQ captures failures
- [x] Deduplication: No duplicate message_ids in Gold

---

## Resource Requirements

### Compute
- **CPUs**: 15 cores (Kafka: 2, Spark: 5, MinIO: 1, Postgres: 1, Other: 6)
- **Memory**: 22GB (Kafka: 3GB, Spark: 10GB, MinIO: 2GB, Postgres: 2GB, Other: 5GB)
- **Recommended Machine**: 16-core, 32GB RAM
  - AWS: m5.4xlarge ($0.768/hr)
  - GCP: n2-standard-16 ($0.776/hr)

### Storage
- **Bronze**: ~50GB (7 days × ~10K trades/min × 1KB/trade)
- **Silver**: ~200GB (30 days, 2 exchanges)
- **Gold**: Unlimited (starts at ~200GB/month)
- **Total**: ~500GB for 30 days (+ 50GB/month growth)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Spark-Iceberg compatibility issues | Medium | High | Test early with sample tables, proven versions |
| Checkpoint corruption | Low | High | Daily backups, test recovery in dev |
| Memory issues in Spark | Medium | Medium | Start small batches, tune incrementally |
| Schema migration breaks existing | Low | Critical | Fresh start (no migration), test thoroughly |
| Performance not meeting targets | Medium | High | Profile early, optimize batch sizes |

---

## Dependencies

### Infrastructure (Already Exists)
- Kafka (KRaft mode, Schema Registry) ✅
- MinIO (S3-compatible storage) ✅
- PostgreSQL (Iceberg catalog) ✅
- Prometheus + Grafana (monitoring) ✅
- Binance WebSocket client (production-ready) ✅

### New Dependencies
- **PySpark**: 3.5.0 (Spark Structured Streaming)
- **Iceberg-Spark**: 1.4.0 (Iceberg connector)
- **Bitnami Spark Docker**: 3.5.0 (Spark cluster images)

---

## Timeline

```
Week 1: Cleanup + Schema Design
  Day 1-2: ASX/Equity removal (Steps 01-03)
  Day 3-4: Schema design + Spark setup (Steps 04-06)
  Day 5:   Medallion tables (Steps 07-09)

Week 2-3: Spark Streaming Implementation
  Day 6-8:   Bronze job (Step 10)
  Day 9-12:  Silver job (Step 11)
  Day 13-15: Gold job (Step 12)

Week 3-4: Kraken + Validation
  Day 16-18: Kraken integration (Steps 13-14)
  Day 19-20: E2E testing + validation (Steps 15-16)
```

**Total**: 20 days (4 weeks) with 1 engineer

---

## Reference

### Related Documentation
- [Medallion Architecture](../../architecture/medallion-architecture.md) (NEW - to be created)
- [Spark Operations Runbook](../../operations/runbooks/spark-operations.md) (NEW)
- [V3 Schema Design](../../architecture/schema-design-v3.md) (NEW)

### Related Phases
- [Phase 2 Prep](../phase-2-prep/) - Binance WebSocket integration (foundation to reuse)
- [Phase 5](../phase-5-binance-production-resilience/) - Production resilience patterns (apply to Kraken)

---

**Last Updated**: 2026-01-18
**Maintained By**: Engineering Team
**Status**: Ready to begin implementation

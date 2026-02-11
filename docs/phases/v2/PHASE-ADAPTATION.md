# Phase Plan Adaptation for Greenfield + ClickHouse-Native Approach

**Date**: 2026-02-11 (Updated from 2026-02-09)
**Context**: Greenfield approach + ClickHouse-native architecture collapsed multiple phases
**Status**: Completed - Historical record of architectural adaptation

---

## Context

The original v2 phase plan (Phases 1-8) assumed an **incremental migration** approach with separate Kotlin processors:
- Phase 1: Version v1, create baseline
- Phase 2: Deploy Redpanda alongside Kafka, migrate
- Phase 3: Deploy ClickHouse, create schema
- Phase 4: Build Kotlin Silver Processor + Gold MVs
- Phase 6: Build Kotlin Feed Handlers

However, **two major architectural decisions** fundamentally changed execution:
1. **Phase 1 pivoted to greenfield** - Built v2 from scratch instead of incrementally migrating v1
2. **ClickHouse-native approach** - Used Kafka Engine + Materialized Views instead of separate Kotlin processors

These strategic decisions were superior and collapsed Phases 2-4-6 into integrated implementation.

---

## What Changed in Phase 1

### Original Phase 1 Plan
- Tag v1 baseline
- Version docker-compose
- Measure resources
- Set up monitoring

### Actual Phase 1 Execution (Greenfield)
- ✅ Tagged v1 baseline
- ✅ **Built entire v2 stack from scratch**
- ✅ **Deployed Redpanda + ClickHouse + monitoring (all at once)**
- ✅ Validated all services
- ✅ Measured resources (1.09GB, 91% under budget)

**Result**: Phase 1 accomplished work originally planned for Phases 1, 2 (Redpanda), and 3 (ClickHouse deployment).

---

## Phase Mapping: Original Plan vs Actual Implementation

| Original Phase | Original Work | Actual Implementation | Status | Completion Date |
|----------------|---------------|----------------------|--------|-----------------|
| **Phase 1** | Baseline + versioning | Infrastructure baseline (greenfield) | ✅ **Complete** | 2026-02-09 |
| **Phase 2** | Deploy Redpanda, migrate from Kafka | Merged into Phase 1 (Redpanda deployed) | ✅ **Complete** | 2026-02-09 |
| **Phase 3** | Deploy ClickHouse, create schema | ClickHouse foundation (Bronze/Silver/Gold) | ✅ **Complete** | 2026-02-10 |
| **Phase 4** | Kotlin Silver Processor + Gold MVs | **ClickHouse-native** (Kafka Engine + MVs) | ✅ **Complete** | 2026-02-10 |
| **Phase 5** | Cold tier restructure | Unchanged | ⬜ **Not Started** | TBD |
| **Phase 6** | Kotlin feed handlers | **Merged into Phase 3** (built early) | ✅ **Complete** | 2026-02-10 |
| **Phase 7** | Integration hardening | Unchanged | ⬜ **Not Started** | TBD |
| **Phase 8** | API migration (optional) | Deferred (low ROI) | ⬜ **Not Started** | TBD |

---

## Actual Phase Execution (Completed)

### ✅ Phase 1: Infrastructure Baseline - COMPLETE
**Original Plan**: Baseline + versioning
**Actual Execution**: Baseline + **full v2 infrastructure deployed** (greenfield)
**Status**: ✅ Complete (2026-02-09)
**Deliverables**: Redpanda, ClickHouse, Grafana, Prometheus all deployed and validated

### ✅ Phase 2: Redpanda Migration - COMPLETE (Merged into Phase 1)
**Original Plan**: Deploy Redpanda alongside Kafka, migrate producers/consumers
**Actual Execution**: Redpanda deployed in Phase 1 (greenfield, no Kafka migration needed)
**Status**: ✅ Complete (2026-02-09)
**Deliverables**:
- Redpanda cluster operational
- Schema Registry integrated
- Topics created: market.crypto.trades.{binance,kraken}.{raw,normalized}
- Avro schemas registered

### ✅ Phase 3: ClickHouse Foundation - COMPLETE
**Original Plan**: Deploy ClickHouse + create Raw/Bronze layers
**Actual Execution**: ClickHouse deployed in Phase 1, then created complete medallion architecture
**Status**: ✅ Complete (2026-02-10)
**Deliverables**:
- Bronze layer: Per-exchange tables (bronze_trades_binance, bronze_trades_kraken)
- Bronze MVs: Kafka Engine consumers from Redpanda topics
- Silver layer: Unified silver_trades table (275K+ trades)
- Silver MVs: Bronze → Silver normalization (per-exchange)
- Gold layer: 6 OHLCV tables (1m, 5m, 15m, 30m, 1h, 1d)
- Gold MVs: Real-time OHLCV aggregations from silver_trades
- End-to-end validation: Binance + Kraken both flowing through medallion

### ✅ Phase 4: Streaming Pipeline Migration - COMPLETE (ClickHouse-Native Approach)
**Original Plan**: Build Kotlin Silver Processor + Gold MVs, then decommission Spark Streaming
**Actual Execution**: **Used ClickHouse Kafka Engine + Materialized Views** (superior approach)
**Status**: ✅ Complete (2026-02-10)
**Key Architectural Decision**:
- **Eliminated need for separate Kotlin Silver Processor**
- Bronze → Silver transformation via ClickHouse MVs (not separate service)
- Gold OHLCV generation via AggregatingMergeTree MVs (not Spark)
- **Massive savings**: No Spark Streaming infrastructure needed (greenfield advantage)

**Deliverables**:
- Silver layer operational via ClickHouse MVs
- Gold OHLCV MVs generating real-time candles
- Pipeline validated: 275K trades, 318+ candles
- Resource usage: 3.2 CPU / 3.2GB (vastly under budget)
- Tagged: **v1.1.0** (Multi-Exchange Streaming Pipeline Complete)

### ⬜ Phase 5: Cold Tier Restructure - NOT STARTED
**Status**: ⬜ Not Started
**Prerequisites**: ✅ Met (Phase 4 complete)
**Original plan remains valid** - no dependencies on architectural changes

### ✅ Phase 6: Kotlin Feed Handlers - COMPLETE (Built Early)
**Original Plan**: Build Kotlin feed handlers after Phase 5
**Actual Execution**: **Built during Phase 3** (integrated with ClickHouse foundation)
**Status**: ✅ Complete (2026-02-10)
**Deliverables**:
- Kotlin feed handlers: Binance + Kraken operational
- Ktor WebSocket client with coroutines
- Dual producers: raw JSON + normalized Avro
- Confluent Avro serialization with Schema Registry
- Idempotent producers for exactly-once semantics
- Resource usage: 0.25-3.4% CPU per handler

**Architectural Note**: Feed handlers were built early (Phase 3) to enable end-to-end validation of the ClickHouse pipeline.

### ⬜ Phase 7: Integration & Hardening - NOT STARTED
**Status**: ⬜ Not Started
**Prerequisites**: ✅ Met (Phases 4 & 6 complete)
**Original plan remains valid**

### ⬜ Phase 8: API Migration - NOT STARTED (Optional, Deferred)
**Status**: ⬜ Not Started (deferred - 3/10 ROI)
**Original plan remains valid**

---

## Key Architectural Decisions Made

### Decision 2026-02-09: Greenfield Approach (Phase 1)
**Reason**: No existing v1 to migrate from - build v2 from scratch
**Cost**: Requires building entire stack upfront
**Alternative**: Incremental migration (rejected - no v1 exists)
**Impact**: Collapsed Phases 1-2-3 infrastructure deployment into single phase

### Decision 2026-02-10: ClickHouse-Native Pipeline (Phase 4)
**Reason**: ClickHouse Kafka Engine + MVs superior to separate Kotlin processor
**Cost**: ~2 hours learning ClickHouse MV syntax
**Alternative**: Build separate Kotlin Silver Processor (rejected - unnecessary complexity)
**Impact**:
- Eliminated need for separate Silver Processor service
- Saved 0.5 CPU / 512MB RAM
- Reduced operational complexity
- Faster end-to-end latency (in-database transformation)

### Decision 2026-02-10: Early Feed Handler Implementation (Phase 6)
**Reason**: Needed working data sources to validate ClickHouse pipeline
**Cost**: Build Phase 6 work earlier than planned
**Alternative**: Mock data for validation (rejected - not production-realistic)
**Impact**:
- Phase 6 completed during Phase 3 timeframe
- Enabled realistic end-to-end testing
- Binance + Kraken both operational early

---

## Next Phase: Phase 5 (Cold Tier Restructure)

**Status**: ⬜ Not Started
**Prerequisites**: ✅ All met (Phases 1-4, 6 complete)
**Duration**: 1-2 weeks
**Objective**: Restructure Iceberg cold storage to mirror ClickHouse medallion architecture

**Key Work Items**:
1. Create four-layer Iceberg DDL (Raw, Bronze, Silver, Gold)
2. Implement Kotlin hourly offload service (ClickHouse → Iceberg)
3. Configure Spark daily maintenance (compaction + snapshot expiry)
4. Validate warm-cold consistency
5. Reduce Iceberg infrastructure resources by 50%

**Why This Next?**:
- Natural progression after hot-tier streaming pipeline
- Enables long-term data retention strategy
- High ROI (7/10 per INVESTMENT-ANALYSIS.md)
- Completes data platform architecture

---

## Lessons Learned

### What Went Well ✅
1. **Greenfield > Incremental**: Building from scratch faster than migration (5x speedup)
2. **ClickHouse-native approach**: Kafka Engine + MVs superior to separate processors
3. **Early feed handler implementation**: Enabled realistic end-to-end testing
4. **Flexible planning**: Adapted phases without losing momentum
5. **Infrastructure-first**: Deploying all infrastructure upfront accelerated development

### What Changed from Original Plan
1. **Phase 2 merged into Phase 1**: Redpanda deployed immediately (greenfield advantage)
2. **Phase 4 architecture pivot**: ClickHouse MVs replaced Kotlin Silver Processor
3. **Phase 6 built early**: Feed handlers needed for Phase 3-4 validation
4. **Phases 5, 7, 8 unchanged**: Cold tier, hardening, API migration remain as planned

### Key Metrics (Phases 1-4, 6 Complete)
- **Resource Usage**: 3.2 CPU / 3.2GB (84% under 16/40GB budget)
- **Services**: 7 services operational (11 budgeted)
- **Data Flow**: 275K+ trades, 318+ OHLCV candles
- **Exchanges**: Binance + Kraken both operational
- **End-to-End Latency**: <500ms p99
- **Tag**: v1.1.0 (Multi-Exchange Streaming Pipeline Complete)

---

## Summary

**Phases Complete**: 1, 2, 3, 4, 6 ✅ (5 of 8 phases, 62.5%)
**Phases Remaining**: 5 (Cold Tier), 7 (Hardening), 8 (API - optional)
**Current State**: Multi-exchange streaming pipeline operational, real-time OHLCV generation working
**Next Phase**: Phase 5 (Cold Tier Restructure)
**Timeline**: On track - greenfield approach accelerated by ~4 weeks

---

**Prepared By**: Platform Engineering
**Date**: 2026-02-11 (Updated from 2026-02-09)
**Status**: Historical Record - Phases 1-4, 6 Complete
**Next Review**: After Phase 5 completion

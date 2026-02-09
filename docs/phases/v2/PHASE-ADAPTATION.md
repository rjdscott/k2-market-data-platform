# Phase Plan Adaptation for Greenfield Approach

**Date**: 2026-02-09
**Context**: Phase 1 greenfield decision impact on subsequent phases
**Status**: Active guidance document

---

## Context

The original v2 phase plan (Phases 1-8) assumed an **incremental migration** approach:
- Phase 1: Version v1, create baseline
- Phase 2: Deploy Redpanda alongside Kafka, migrate
- Phase 3: Deploy ClickHouse, create schema
- Phase 4+: Continue migration

However, **Phase 1 pivoted to a greenfield approach**, building v2 from scratch instead of incrementally migrating v1. This strategic decision was superior but changes how we execute subsequent phases.

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

## Phase Mapping: Original Plan vs Greenfield Reality

| Original Phase | Original Work | Greenfield Status | Next Action |
|----------------|---------------|-------------------|-------------|
| **Phase 1** | Baseline + versioning | ✅ **Complete + bonus** | Done |
| **Phase 2** | Deploy Redpanda, migrate from Kafka | ✅ **Already deployed** | Skip deployment, adapt validation |
| **Phase 3** | Deploy ClickHouse, create schema | ⚠️ **Partially done** (CH deployed) | Skip deployment, **create schema** |
| **Phase 4** | Streaming pipeline migration | ⬜ **Unchanged** | Execute as planned |
| **Phase 5** | Cold tier restructure | ⬜ **Unchanged** | Execute as planned |
| **Phase 6** | Kotlin feed handlers | ⬜ **Unchanged** | Execute as planned |
| **Phase 7** | Integration hardening | ⬜ **Unchanged** | Execute as planned |
| **Phase 8** | API migration (optional) | ⬜ **Unchanged** | Execute as planned |

---

## Adapted Phase Execution Plan

### ✅ Phase 1: COMPLETE
**Original**: Baseline + versioning
**Greenfield**: Baseline + **full v2 infrastructure deployed**
**Status**: ✅ Complete (2026-02-09)

### 🔄 Phase 2: ADAPT → "Redpanda Validation & Topic Setup"
**Original**: Deploy Redpanda alongside Kafka, migrate producers/consumers
**Greenfield Adaptation**: Redpanda already deployed, no Kafka to migrate from

**New Phase 2 Scope**:
1. ~~Deploy Redpanda~~ (already done in Phase 1)
2. Create market data topics (trades, OHLCV patterns)
3. Register Avro schemas in Schema Registry
4. Test producer/consumer patterns with sample data
5. Benchmark latency and throughput
6. Validate Schema Registry integration

**Duration**: 2-3 days (vs 1 week original)
**Deliverable**: Production-ready Redpanda topics and validated ingestion patterns

### 🔄 Phase 3: ADAPT → "ClickHouse Schema & Medallion Architecture"
**Original**: Deploy ClickHouse + create Raw/Bronze layers
**Greenfield Adaptation**: ClickHouse already deployed, focus on schema only

**New Phase 3 Scope**:
1. ~~Deploy ClickHouse~~ (already done in Phase 1)
2. Create Raw layer DDL (Kafka Engine from Redpanda)
3. Create Bronze layer DDL (cascading MVs)
4. Create Silver layer DDL (with transformations)
5. Create Gold layer OHLCV tables (AggregatingMergeTree)
6. Validate end-to-end data flow
7. Add ClickHouse-specific monitoring

**Duration**: 1 week (vs 1-2 weeks original)
**Deliverable**: Four-layer medallion architecture in ClickHouse, data flowing Bronze → Silver → Gold

### ⬜ Phase 4-7: UNCHANGED
**Original plan remains valid** - these phases don't depend on deployment sequence:
- Phase 4: Replace Spark Streaming with Kotlin processors
- Phase 5: Restructure Iceberg cold tier
- Phase 6: Build Kotlin feed handlers
- Phase 7: Integration hardening and production validation

---

## Immediate Next Steps

### Recommended: Adapted Phase 3 (ClickHouse Schema)
**Why This First?**
- Most logical progression: infrastructure → schema → data flow
- ClickHouse already deployed and validated
- Establishes data ingestion patterns for future phases
- High value: Real-time OHLCV computation unlocked

**Work Items**:
1. Design trade data schema (Raw → Bronze → Silver → Gold)
2. Create ClickHouse DDL files
3. Set up Kafka Engine ingestion from Redpanda
4. Test end-to-end data flow with sample data
5. Validate OHLCV materialized views
6. Document schema and query patterns

### Alternative: Adapted Phase 2 (Redpanda Validation)
**Why This First?**
- Validate Redpanda under load before building on it
- Establish topic naming conventions
- Test Schema Registry with Avro
- Benchmark performance vs requirements

**Work Items**:
1. Create market data topics (market.crypto.trades.{exchange})
2. Register sample Avro schemas
3. Write producer test (mock trade data)
4. Write consumer test (validate ingestion)
5. Benchmark throughput (target: 5,000+ msg/s)
6. Document topic patterns and conventions

---

## Decision: Which Phase Next?

### Option A: Adapted Phase 3 (ClickHouse Schema) - **RECOMMENDED**
**Pros**:
- Unlocks end-to-end data flow
- High business value (real-time OHLCV)
- Natural progression after infrastructure
- Enables Phase 4 (streaming pipeline)

**Cons**:
- Requires data ingestion working first
- More complex than Phase 2

**Estimated Duration**: 1 week

### Option B: Adapted Phase 2 (Redpanda Validation)
**Pros**:
- Simpler, validates infrastructure first
- Establishes data ingestion patterns
- Tests Schema Registry thoroughly
- Lower risk, easier rollback

**Cons**:
- Lower immediate business value
- Extra validation step (could argue it's already validated in Phase 1)

**Estimated Duration**: 2-3 days

### Recommendation: **Phase 3 (ClickHouse Schema)**
**Rationale**:
- Redpanda already validated in Phase 1 (cluster health, test topic, console access)
- ClickHouse already validated (database created, queries working)
- Phase 3 delivers immediate business value (real-time OHLCV)
- Can incorporate Phase 2 validation items (topics, schemas) into Phase 3 execution

**Approach**: Merge Phase 2 validation items into Phase 3 as prerequisites:
1. Create Redpanda topics (Phase 2 work)
2. Register Avro schemas (Phase 2 work)
3. Create ClickHouse Raw layer with Kafka Engine (Phase 3 work)
4. Create Bronze/Silver/Gold layers (Phase 3 work)
5. Validate end-to-end flow (both phases)

---

## Documentation Updates Needed

### Phase 2 README
- [ ] Update status to "Adapted for greenfield approach"
- [ ] Document what was already accomplished in Phase 1
- [ ] Reframe remaining work (topic setup, validation)
- [ ] Adjust duration estimate (2-3 days vs 1 week)

### Phase 3 README
- [ ] Update status to "Adapted for greenfield approach"
- [ ] Remove "Deploy ClickHouse" step (already done)
- [ ] Incorporate Phase 2 topic/schema setup as prerequisites
- [ ] Focus on schema creation and data flow validation

### Phase Map (docs/phases/v2/README.md)
- [ ] Add note about greenfield approach impact
- [ ] Update phase durations based on actual execution
- [ ] Link to this adaptation document

---

## Lessons for Future Phases

1. **Original plan still valid** for Phases 4-8 (no deployment dependencies)
2. **Infrastructure-first approach** accelerated early phases
3. **Greenfield > Incremental** validated by 5x faster Phase 1 execution
4. **Flexible planning** enabled adaptation without losing momentum
5. **Clear decision documentation** (like this file) enables confident pivots

---

## Summary

**Decision**: Proceed with **Adapted Phase 3 (ClickHouse Schema & Medallion Architecture)**

**Scope**: Create four-layer medallion architecture in ClickHouse:
- Raw: Kafka Engine from Redpanda
- Bronze: Deduplication + normalization
- Silver: Validation + enrichment
- Gold: OHLCV aggregations (real-time)

**Duration**: ~1 week

**Prerequisites**:
1. Redpanda topics created (market.crypto.trades.*)
2. Avro schemas registered
3. Sample trade data for testing

**Deliverable**: End-to-end data flow from Redpanda → ClickHouse → Real-time OHLCV

---

**Prepared By**: Platform Engineering
**Date**: 2026-02-09
**Status**: Active Guidance
**Next Review**: After Phase 3 completion

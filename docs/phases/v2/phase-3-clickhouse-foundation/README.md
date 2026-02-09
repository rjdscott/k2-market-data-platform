# Phase 3: ClickHouse Foundation

**Status:** ⬜ NOT STARTED
**Duration:** 1-2 weeks
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

Deploy ClickHouse and establish the **Raw + Bronze layers** of the four-layer medallion architecture. ClickHouse ingests from Redpanda via its built-in Kafka Engine. No existing services are removed yet -- this is a purely **additive** phase.

At the end of this phase, trade data flows from Redpanda into `trades_raw` (Raw layer) via Kafka Engine, then cascades into `bronze_trades` (Bronze layer) via materialized views -- all in real-time with zero external processing.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Deploy ClickHouse Server](steps/step-01-deploy-clickhouse.md) | ⬜ Not Started | Add to docker-compose with 4 CPU / 8GB limits, configure `users.xml`, verify startup and basic queries |
| 2 | [Create Raw Layer DDL](steps/step-02-raw-layer-ddl.md) | ⬜ Not Started | `trades_raw_queue` Kafka Engine, `trades_raw` MergeTree with JSONAsString, `trades_raw_mv` materialized view |
| 3 | [Create Bronze Layer DDL](steps/step-03-bronze-layer-ddl.md) | ⬜ Not Started | `bronze_trades` ReplacingMergeTree, `bronze_trades_mv` cascading MV from `trades_raw` with type casting + timestamp normalization |
| 4 | [Validate Raw + Bronze Ingestion](steps/step-04-validate-ingestion.md) | ⬜ Not Started | Verify Redpanda messages land in `trades_raw`, verify cascading MV populates `bronze_trades`, compare row counts, validate deduplication |
| 5 | [Create ClickHouse Monitoring](steps/step-05-clickhouse-monitoring.md) | ⬜ Not Started | Add ClickHouse metrics to Prometheus, create Grafana panels for inserts/sec, query latency, merge performance, memory usage. Tag `v2-phase-3-complete` |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | ClickHouse Running | 1 | ⬜ Not Started | ClickHouse server starts, responds to basic queries |
| M2 | Raw + Bronze Ingesting | 2-4 | ⬜ Not Started | `trades_raw` and `bronze_trades` populated in real-time from Redpanda |
| M3 | Monitoring Live | 5 | ⬜ Not Started | ClickHouse metrics visible in Grafana, git tag created |

---

## Success Criteria

- [ ] ClickHouse server running with 4 CPU / 8GB resource limits
- [ ] `trades_raw` table populated in real-time from Redpanda via Kafka Engine
- [ ] `bronze_trades` table populated via cascading materialized view from `trades_raw`
- [ ] Row counts match between Redpanda topic and ClickHouse tables
- [ ] Deduplication working correctly in `bronze_trades` (ReplacingMergeTree)
- [ ] ClickHouse metrics (inserts/sec, query latency, merge performance, memory) visible in Grafana
- [ ] Git tag `v2-phase-3-complete` created

---

## Resource Impact

**This phase is additive -- nothing is removed yet.**

Resource impact: **+2 CPU / +4GB**

| Metric | Before (Phase 2) | After (Phase 3) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~35 | ~37 | +2 |
| RAM | ~44GB | ~46GB | +2GB |
| Services | 17 | 18 | +1 |

---

## Architecture: Four-Layer Medallion

```
Redpanda Topic (trades)
    |
    v
[trades_raw_queue]  ← Kafka Engine (Raw ingestion)
    |
    v
[trades_raw]        ← MergeTree + JSONAsString (Raw layer)
    |
    v (trades_raw_mv)
[bronze_trades]     ← ReplacingMergeTree (Bronze layer: typed, normalized, deduplicated)
    |
    v (Phase 4)
[silver_trades]     ← MergeTree (Silver layer: validated, enriched)
    |
    v (Phase 4)
[gold_ohlcv_*]      ← AggregatingMergeTree (Gold layer: OHLCV aggregations)
```

This phase implements the **Raw and Bronze** layers. Silver and Gold are implemented in Phase 4.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| ClickHouse memory pressure on 8GB | Medium | Configure `max_memory_usage` in `users.xml`, monitor with Grafana |
| Kafka Engine consumer lag | Medium | Monitor consumer group lag in Redpanda Console, tune `kafka_max_block_size` |
| JSONAsString parsing failures | Low | DLQ pattern for malformed messages, monitor parse error rates |
| MV cascade latency | Low | Monitor MV execution time in system.query_log |

---

## Dependencies

- Phase 2 complete (Redpanda running with all topics active)
- Trade data actively flowing through Redpanda topics

---

## Rollback Procedure

1. Stop ClickHouse container
2. Remove ClickHouse from docker-compose
3. Swap docker-compose back to `docker/v2-phase-2-redpanda.yml`
4. No data loss -- existing pipeline (Redpanda -> consumers) unaffected

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 2: Redpanda Migration](../phase-2-redpanda-migration/README.md) -- Prerequisite phase
- [Phase 4: Streaming Pipeline](../phase-4-streaming-pipeline/README.md) -- Silver + Gold layers (next phase)
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 2 completion

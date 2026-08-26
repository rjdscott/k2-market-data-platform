# ADR-008: Eliminate Prefect — Replace with ClickHouse Materialized Views + Spring Scheduler

**Status:** Partially rejected — Superseded by [ADR-014](ADR-014-spark-based-iceberg-offload.md), [ADR-017](ADR-017-iceberg-maintenance-pipeline.md) — see Outcome
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Orchestration

---

## Context

The current platform uses **Prefect 3.1.0** (Server + Agent) to orchestrate 5 OHLCV aggregation batch jobs:

| Component | CPU | RAM | Purpose |
|-----------|-----|-----|---------|
| Prefect Server | 0.5 | 512MB | Web UI, API, flow scheduling |
| Prefect Agent | 0.25 | 256MB | Flow execution, polling |
| **Total** | **0.75** | **768MB** | Scheduling 5 cron-triggered flows |

Prefect's role is limited to:
1. Scheduling OHLCV computation flows (1m, 5m, 30m, 1h, 1d)
2. Executing these flows sequentially (to prevent resource contention)
3. Providing a UI for flow run history

This is a heavyweight solution (two services, 768MB RAM, web UI, PostgreSQL dependency for Prefect metadata) for what is essentially 5 cron jobs.

## Decision

**Eliminate Prefect entirely. Replace its two functions:**
1. **Real-time OHLCV**: ClickHouse Materialized Views (ADR-003) — computed on every insert, zero scheduling needed
2. **Batch maintenance**: Spring Boot `@Scheduled` annotations or system cron — for daily tiering and Iceberg maintenance

## Rationale

### OHLCV Is Now Real-Time

With ClickHouse Materialized Views (see ADR-003), OHLCV candles are computed **on every trade insert** — not as a scheduled batch job. This makes Prefect's primary workload (OHLCV scheduling) completely unnecessary.

```
Before:
  Trade arrives → Kafka → Spark Bronze → Spark Silver → Spark Gold → Iceberg
  → Prefect schedules OHLCV job → Spark reads Gold → computes candles → writes Iceberg
  Latency: minutes to hours (depends on schedule)

After:
  Trade arrives → Redpanda → ClickHouse trades table
  → Materialized View automatically computes candle → available instantly
  Latency: milliseconds
```

### Remaining Batch Jobs Don't Need an Orchestrator

The remaining batch operations (warm-to-cold tiering, Iceberg maintenance) are:
- **Simple**: Single-step jobs, no DAG dependencies
- **Infrequent**: 1-3 times daily
- **Idempotent**: Safe to retry on failure
- **Independent**: No complex dependencies between them

For this workload, Spring's built-in scheduler is sufficient:

```kotlin
@Component
class BatchScheduler(
    private val sparkSubmitter: SparkJobSubmitter,
    private val alerting: AlertingService
) {
    @Scheduled(cron = "0 0 2 * * *")  // Daily 02:00 UTC
    fun warmToColdTiering() {
        try {
            sparkSubmitter.submit("warm-to-cold-tiering")
        } catch (e: Exception) {
            alerting.notify("Tiering failed: ${e.message}")
        }
    }

    @Scheduled(cron = "0 0 3 * * *")  // Daily 03:00 UTC
    fun icebergCompaction() {
        sparkSubmitter.submit("iceberg-compaction")
    }
}
```

### Resource Comparison

| Approach | CPU | RAM | Services | Complexity |
|----------|-----|-----|----------|------------|
| Prefect Server + Agent | 0.75 | 768MB | 2 | High (web UI, API, PostgreSQL) |
| Spring `@Scheduled` | 0 (embedded in API) | 0 (shared with API) | 0 | Low (annotations) |
| System cron | 0 | 0 | 0 | Very low |
| **Savings** | **0.75** | **768MB** | **2** | **Significant** |

## Alternatives Considered

### 1. Keep Prefect, Reduce Resources
- **Pro**: Familiar tool, UI for monitoring
- **Con**: Minimum viable Prefect Server is ~256MB RAM; Agent adds more
- **Con**: Overkill for 2-3 simple cron jobs
- **Verdict**: Rejected — unnecessary complexity for remaining workload

### 2. Apache Airflow
- **Pro**: Industry standard, extensive DAG support
- **Con**: Even heavier than Prefect (Web Server + Scheduler + Worker + PostgreSQL)
- **Con**: Minimum ~2GB RAM
- **Verdict**: Rejected — wrong direction for resource reduction

### 3. Dagster
- **Pro**: Modern, asset-based orchestration
- **Con**: Similar resource profile to Prefect
- **Con**: Overkill for 2-3 batch jobs
- **Verdict**: Rejected — same overengineering problem

## Consequences

### Positive
- Eliminates 2 services and 768MB RAM
- OHLCV moves from batch (minutes/hours latency) to real-time (milliseconds)
- Simpler architecture — fewer moving parts to fail
- Batch scheduling is a solved problem (cron / Spring @Scheduled)

### Negative
- Loss of Prefect's web UI for flow monitoring (mitigated: Spring Actuator + Grafana provide equivalent visibility)
- Loss of Prefect's retry/backoff logic (mitigated: implement in Kotlin — trivial for simple jobs)
- No visual DAG representation (not needed — no DAGs, just sequential jobs)

## References

- [Spring Scheduling](https://spring.io/guides/gs/scheduling-tasks)
- [Prefect Resource Requirements](https://docs.prefect.io/latest/)

---

## Outcome (2026-08)

Half of this decision held. The other half was wrong, and was reversed once the batch workload became real.

**The OHLCV half held completely.** ClickHouse `AggregatingMergeTree` materialized views replaced all five OHLCV batch flows — and then added a sixth timeframe. Candles for 1m/5m/15m/30m/1h/1d are computed on insert, with no scheduler, no flow run, and nothing to fail at 03:00. Latency went from minutes-to-hours to sub-millisecond after a trade lands in Silver. Prefect's *primary* workload genuinely evaporated, exactly as argued above.

**The orchestration half did not.** Prefect was not eliminated. It runs today as three services — `prefect-server`, `prefect-worker`, `prefect-db` — driving the 15-minute ClickHouse → Iceberg offload ([ADR-014](ADR-014-spark-based-iceberg-offload.md)) and the 02:00 UTC Iceberg maintenance flow ([ADR-017](ADR-017-iceberg-maintenance-pipeline.md)). Two reasons:

1. **The proposed home never existed.** `@Scheduled` was to be embedded in the Spring Boot API. [ADR-005](ADR-005-kotlin-spring-boot-api.md) was deferred and that API was never built. "Free" scheduling inside an existing JVM is not free when there is no JVM.
2. **The remaining batch work was never "2-3 simple cron jobs".** This ADR asserted the leftovers were single-step, independent and DAG-free. The offload is none of those: per-table watermark state in PostgreSQL, per-table retry with idempotent resume from the held watermark, a fan-out/fan-in shape (3 bronze concurrent → silver sequential → 6 gold concurrent), and run history to answer "did 02:00 run, and what did it touch". Maintenance adds ordered stages (compact → expire → audit) with a fail-fast audit gate that must page a human. That is orchestration, not scheduling. Re-implementing it in Kotlin or bash would have been rebuilding Prefect, badly, without the UI or the run log.

**Cost of being wrong:** 2.5 CPU / 2.5 GB of compose limits, of which roughly 1.5 CPU / 2 GB is net new — ADR-010 already budgeted a PostgreSQL for the Iceberg catalog, and that container is now the Prefect metadata and watermark store instead. About 10% of the CPU budget, absorbed comfortably because the Spring Boot API and the Kotlin Silver processor were never built ([ADR-010](ADR-010-resource-budget.md) Outcome).

The resource arithmetic in this ADR was correct. The workload analysis was not. Orchestration and scheduling only look like the same thing until the jobs acquire state.

# Platform v2 — Staff Engineering Investment Analysis

**Date:** 2026-02-09
**Author:** Platform Engineering
**Perspective:** Risk/reward ranking of each component upgrade, assessed independently

---

## Assessment Framework

Each component is evaluated on five axes:

| Axis | Scale | What it measures |
|------|-------|------------------|
| **Effort** | Weeks of eng time | Calendar time to production-ready, including tests |
| **Resource Savings** | CPU / RAM reclaimed | Direct infrastructure savings within the 16/40 budget |
| **Performance Gain** | Latency / throughput delta | Measurable improvement to end-user or pipeline |
| **Risk** | Likelihood × blast radius | What breaks if this goes wrong |
| **Reversibility** | How hard to roll back | Can we undo this if it fails |

**ROI Formula** (qualitative): `(Resource Savings + Performance Gain) / (Effort + Risk)`

---

## Ranking: Best to Worst Investment

### #1. Eliminate Spark Streaming + Add ClickHouse (COMBINED)

**These are a single investment.** You cannot remove Spark Streaming without something to replace the medallion pipeline and OHLCV computation. ClickHouse is that replacement.

| Axis | Assessment |
|------|------------|
| **Effort** | 3-4 weeks. ClickHouse DDL (1 week), Kotlin Silver processor (1 week), MV tuning + testing (1-2 weeks) |
| **Resource Savings** | **-13.5 CPU / -19.75GB** (Spark) + **-0.75 CPU / -768MB** (Prefect falls out free). Net after adding ClickHouse (+2 CPU / +4GB): **-12.25 CPU / -16.5GB** |
| **Performance Gain** | End-to-end latency: **5-15 minutes → <200ms** (>1000x). Query latency: **200-500ms → 2-5ms** (100x). Concurrent queries: **10 → 100+** |
| **Risk** | **Medium.** New technology (ClickHouse) to operate. Mitigated: ClickHouse is mature, well-documented, Docker-native. Failure mode is well-understood (if CH goes down, warm queries fail; cold queries via Iceberg still work). |
| **Reversibility** | Medium. Can keep Spark Streaming running in parallel during migration. Dual-write period until confidence is high. |

**Verdict: DO THIS FIRST. Non-negotiable.**

This single investment solves the core problem. The current platform literally cannot fit in 16 cores because Spark Streaming alone consumes 14 cores. Everything else is optimization on top of this.

```
Without this change: 35-40 CPU (impossible to fit in 16)
With this change:    ~20-23 CPU (still over, but within striking distance)
```

**What you get for free when you do this:**
- Prefect elimination (ClickHouse MVs replace OHLCV batch jobs)
- DuckDB elimination (ClickHouse replaces it as query engine)
- Real-time OHLCV (MVs compute on insert, zero scheduling)
- 6 timeframes (1m, 5m, 15m, 30m, 1h, 1d) instead of 5

**ROI: 10/10** — Highest savings, highest performance gain, addresses the core constraint.

---

### #2. Replace Kafka with Redpanda

| Axis | Assessment |
|------|------------|
| **Effort** | **1 week.** Redpanda is Kafka API-compatible. Change broker config, test consumers, swap Docker images. No application code changes. |
| **Resource Savings** | **-1.5 CPU / -1.78GB** (Kafka + Schema Registry + Kafka UI → single Redpanda binary) |
| **Performance Gain** | p99 latency: **30ms → 5ms** (6x). p99.9: **100ms+ → 10ms** (10x). Startup: **15-30s → 2-5s** (faster dev cycles). |
| **Risk** | **Low.** Kafka API compatibility is Redpanda's core value proposition. They pass Kafka's own test suite. Can dual-run both brokers during migration. |
| **Reversibility** | **High.** API-compatible means switching back is a config change. |

**Verdict: EASY WIN. Do this second.**

This is the best effort-to-reward ratio on the board. One week of work, 3 services consolidated into 1, measurable latency improvement, near-zero risk. The built-in Schema Registry and Console eliminate two additional services without any code changes.

**ROI: 9/10** — Minimal effort, meaningful savings, very low risk.

---

### #3. Restructure Iceberg Cold Storage (Four-Layer Mirror + Hourly Offload)

| Axis | Assessment |
|------|------------|
| **Effort** | **1-2 weeks.** Iceberg is already running. Create new table structure, implement Kotlin Iceberg writer for hourly offload, adjust Spark maintenance jobs. |
| **Resource Savings** | **-1.5 CPU / -2GB** (reduced role — cold-only, less frequent access). |
| **Performance Gain** | Cold tier freshness: **24h → ~1h**. Full four-layer lineage enables re-derivation without exchange re-ingestion. |
| **Risk** | **Low.** Iceberg is already in production. New tables are additive — don't touch existing ones until verified. Kotlin Iceberg SDK is a stable Apache library. |
| **Reversibility** | **High.** Old Iceberg tables remain. New tables are additive. |

**Verdict: DO THIS. Low risk, strong architectural payoff.**

The Kotlin Iceberg writer for hourly offload is the more interesting part. It's a small amount of code (Iceberg Java SDK is a library, not a framework) that provides 24x improvement in cold tier freshness. The four-layer mirror gives you regulatory-grade data lineage — a differentiator if this platform ever faces compliance scrutiny.

**ROI: 7/10** — Moderate savings, strong architectural value, low risk.

---

### #4. Replace Python Feed Handlers with Kotlin

| Axis | Assessment |
|------|------------|
| **Effort** | **2-3 weeks.** Rewrite WebSocket clients in Ktor, implement Avro serialization with Confluent JVM client, test reconnection and backpressure handling. |
| **Resource Savings** | **-0.5 CPU / -512MB** (consolidate 2 Python containers → 1 Kotlin JVM). |
| **Performance Gain** | Throughput: **138 msg/s → 5,000+ msg/s** (36x). p99 latency: **25ms → 2ms** (12x). |
| **Risk** | **Medium.** New language for the team. WebSocket reconnection logic is fiddly regardless of language. Feed handlers are critical path — if they go down, no data flows. |
| **Reversibility** | **Medium.** Can run Python and Kotlin handlers in parallel on different consumer groups. |

**Verdict: WORTH IT, but not urgent.**

Honest assessment: the current Python handlers work at 138 msg/s. For 2 exchanges × 2 symbols each, that's more than sufficient. The throughput headroom matters only if you plan to scale to 10+ exchanges or 50+ symbols.

**When this becomes urgent:**
- Adding more exchanges (the per-container model doesn't scale)
- Sub-millisecond ingestion latency requirements
- If you're doing Kotlin everywhere else anyway (marginal cost drops)

**When this is NOT worth it:**
- If you're staying at 2-3 exchanges for the foreseeable future
- If the team has no JVM/Kotlin experience and the learning curve blocks other work

**ROI: 6/10** — Good performance gain, but current handlers aren't a bottleneck. ROI improves significantly if scaling to more exchanges.

---

### #5. Replace FastAPI with Spring Boot

| Axis | Assessment |
|------|------------|
| **Effort** | **3-4 weeks.** Full API rewrite: 7 endpoints, middleware (auth, rate limiting, CORS), connection pooling, metrics, OpenAPI docs, tests. |
| **Resource Savings** | **+0.5 CPU (costs more, not less)**. Memory is roughly neutral. |
| **Performance Gain** | Throughput: **1,500 → 15,000 req/s** (10x). p99: **15ms → 3ms** (5x). Concurrency: **4 workers → 1000+ virtual threads** (250x). |
| **Risk** | **High.** Full rewrite of a working, tested API with known behavior. Spring Boot has a steeper learning curve. Risk of subtle behavioral differences in auth, rate limiting, error handling. |
| **Reversibility** | **Low.** Once you commit to the rewrite, rolling back means maintaining two APIs. |

**Verdict: QUESTIONABLE. The weakest ROI on the board.**

Let me be direct: the "unified language" argument is architecturally elegant but not economically justified on its own. FastAPI is excellent software. It's not the bottleneck. The current 1,500 req/s throughput is more than sufficient for a market data query API serving a reasonable number of clients.

**The honest case FOR doing it:**
- If you're doing Kotlin for feed handlers AND stream processors, having a third language (Python) in the stack creates genuine maintenance friction: separate CI pipelines, separate dependency management, separate Docker base images, separate expertise requirements
- Spring Boot + ClickHouse JDBC via HikariCP is genuinely better-integrated than Python ClickHouse clients
- Type safety across the entire request/response chain prevents a class of bugs

**The honest case AGAINST:**
- 3-4 weeks of rewriting a working API is pure cost with no new functionality
- FastAPI + ClickHouse Python client works fine for query serving
- The team already knows Python
- You could spend those weeks on features instead

**Compromise option: Keep FastAPI, swap DuckDB for ClickHouse.**
FastAPI with `clickhouse-connect` (Python ClickHouse client) gets you the query engine upgrade without the API rewrite. Effort: ~1 week. You lose the "single language" benefit but keep a working API.

**ROI: 3/10** — High effort, high risk, no resource savings, performance gain exists but isn't needed at current scale. Only justified as the last step if you've committed to Kotlin everywhere else.

---

## Investment Summary Matrix

| Rank | Component | Effort | Resource Savings | Perf Gain | Risk | ROI | Do It? |
|------|-----------|--------|-----------------|-----------|------|-----|--------|
| **#1** | ClickHouse + Kill Spark Streaming | 3-4 wk | -12.25 CPU / -16.5GB | 1000x latency | Med | **10/10** | **YES — mandatory** |
| **#2** | Kafka → Redpanda | 1 wk | -1.5 CPU / -1.78GB | 6x p99 | Low | **9/10** | **YES — easy win** |
| **#3** | Iceberg restructure + hourly offload | 1-2 wk | -1.5 CPU / -2GB | 24x freshness | Low | **7/10** | **YES** |
| **#4** | Python → Kotlin feed handlers | 2-3 wk | -0.5 CPU / -512MB | 36x throughput | Med | **6/10** | **YES, if scaling** |
| **#5** | FastAPI → Spring Boot | 3-4 wk | +0.5 CPU (worse) | 10x throughput | High | **3/10** | **DEFER or skip** |

---

## Recommended Implementation Sequence

```
Phase 1 (Week 1-2): Foundation — Redpanda + ClickHouse deploy
├── Swap Kafka → Redpanda (1 week, low risk, immediate savings)
├── Deploy ClickHouse alongside existing stack
└── Create Raw/Bronze/Silver/Gold DDL, test with sample data

Phase 2 (Week 2-4): Core Migration — Kill Spark Streaming
├── Implement ClickHouse Kafka Engine (Raw + Bronze auto-ingest)
├── Implement Kotlin Silver Processor
├── Create 6 OHLCV Materialized Views
├── Validate OHLCV correctness against v1 Prefect output
├── Decommission Spark Streaming jobs (5 services)
└── Decommission Prefect (2 services)

Phase 3 (Week 4-5): Cold Tier — Iceberg restructure
├── Create four-layer Iceberg table structure
├── Implement Kotlin hourly offload writer
├── Adjust Spark to daily maintenance only
└── Validate lineage across warm + cold tiers

Phase 4 (Week 5-7): Feed Handlers — Python → Kotlin
├── Implement Kotlin feed handler for Binance
├── Parallel run with Python handler, compare output
├── Add Kraken, validate, decommission Python handlers
└── Consolidate into single JVM process

Phase 5 (Week 7-8): Hardening
├── End-to-end latency benchmarking
├── Resource consumption validation (must fit 16/40)
├── Failure mode testing
└── Update documentation and runbooks

Phase 6 (OPTIONAL, Week 8-10): API Migration
├── Only if team is committed to full Kotlin stack
├── Implement Spring Boot API with ClickHouse queries
├── Parallel run with FastAPI, compare responses
└── Switch traffic, decommission FastAPI
```

---

## Budget Checkpoints

After each phase, validate the resource budget:

| Checkpoint | Expected CPU | Expected RAM | Fits 16/40? |
|------------|-------------|-------------|-------------|
| After Phase 1 (Redpanda, CH added) | ~33 CPU | ~45GB | No — still running old stack |
| After Phase 2 (Spark Streaming killed) | ~18 CPU | ~22GB | **Close** — over by 2 CPU |
| After Phase 3 (Iceberg restructured) | ~16.5 CPU | ~20GB | **Nearly** — within margin |
| After Phase 4 (Kotlin handlers) | ~15.5 CPU | ~19.5GB | **YES** ✓ |
| After Phase 5 (hardened) | ~15.5 CPU | ~19.5GB | **YES** ✓ |

**Critical insight**: You achieve budget compliance at Phase 4. Phases 1-3 alone get you to ~16.5 CPU, which is within tuning distance (reduce ClickHouse from 4 to 3.5 cores). Phase 4 (Kotlin handlers) gives comfortable headroom.

**Phase 6 (Spring Boot) is not needed for budget compliance.** It's a "nice to have" for architectural purity.

---

## The "Do Nothing" Comparison

Every investment should be compared against doing nothing. What if we just optimize the current v1 stack?

| Optimization | CPU Saved | Effort | Enough? |
|-------------|-----------|--------|---------|
| Reduce Spark workers from 2 to 1 | ~3.5 CPU | 1 day | No (still ~32 CPU) |
| Reduce Spark executor memory | ~2-4GB RAM | 1 day | No (still ~32 CPU) |
| Remove Kafka UI | 0.5 CPU | 1 hour | Negligible |
| Tune Kafka memory down | ~0.5GB | 1 day | Negligible |
| Remove one streaming job | ~3 CPU | 1 week | No (still ~29 CPU) |
| **Best case with v1 tuning** | **~7.5 CPU** | **1-2 weeks** | **No (still ~28 CPU)** |

**Conclusion: v1 tuning cannot get below ~25-28 CPU.** The 16-core constraint requires architectural change, not configuration tuning. Spark Streaming's resource floor is simply too high.

---

## Final Recommendation

**Invest in Phases 1-4 (8 weeks). Defer Phase 6 (Spring Boot).**

The first four phases deliver:
- Budget compliance (15.5 CPU / 19.5GB)
- 1000x end-to-end latency improvement
- 60%+ resource reduction
- Full four-layer data lineage
- Six OHLCV timeframes
- Hourly cold tier freshness
- 45% fewer services to operate

The Spring Boot migration (Phase 6) is a luxury that provides architectural elegance at the cost of 3-4 weeks of rewriting working code. A staff engineer would defer it until the Kotlin ecosystem is proven in production with the feed handlers and Silver processor. If those succeed and the team is productive in Kotlin, then the API migration becomes low-risk and the unified-language argument is compelling. If the team struggles with Kotlin, you've preserved your working Python API as a stable fallback.

**The worst investment you can make is rewriting something that works to gain something you don't need yet.**

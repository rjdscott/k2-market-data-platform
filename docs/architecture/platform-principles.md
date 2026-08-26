# Platform Principles

Six rules the design is actually held to. Each one has a place in the codebase where it is enforced or a place where it was violated and cost something — otherwise it would be a slogan, not a principle.

---

### 1. The resource budget is a hard constraint, not a goal

16 cores, 40 GB, one host. Every service in [`docker-compose.yml`](../../docker-compose.yml) carries explicit `deploy.resources.limits`, and the total is checked at every phase boundary. As built (v2): **15.1 CPU / 21.875 GB across 14 services** (+2 one-shot). This branch's v3 foundations add Lakekeeper — +0.25 CPU / +256 MB — for **15.35 CPU / 22.125 GB across 15 (+4 one-shot) as deployed here**.

This is first because it is the only principle that changed the architecture. "Reduce resource usage" produces tuning; a number you cannot exceed produces different decisions — five Spark Streaming jobs became materialized views, and a planned Kotlin stream processor was never written. Full accounting in [ADR-010](../adr/ADR-010-resource-budget.md).

**Where it bites:** a new service must displace an existing one or justify its slot. That is the intended friction.

---

### 2. Idempotency over exactly-once

Every batch job must be safe to kill and re-run. The Iceberg offload reads ClickHouse above a per-table watermark, appends, and only then advances the watermark in PostgreSQL. A run killed mid-flight leaves the watermark where it was and the next 15-minute cycle repeats the same range.

No transactions, no two-phase commit, no coordination protocol — one number that moves last. Tested by killing the Spark container mid-offload: no duplicates, no gap, recovery on the next scheduled cycle.

**Where it bites:** a partial Iceberg write is possible in principle. It has not been observed, and the daily row-count audit ([ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md)) is what would catch it.

---

### 3. Raw survives normalization

Every trade is written twice: the exchange's untouched payload to a `.raw` topic, and a normalized record alongside it. Bronze tables keep native symbols and native sequence semantics per exchange — `XBT/USD` stays `XBT/USD` — and normalization to `BTC/USD` happens at Silver ([ADR-011](../adr/ADR-011-multi-exchange-bronze-architecture.md)).

The reason is debugging. When a price looks wrong, the question is always "did the exchange send this, or did we do it?", and that question is only answerable if the pre-transform bytes still exist.

**Where it bites:** it costs a second produce per trade and a second copy of storage. And the principle is imperfectly applied — nothing durably persists pre-normalization rows in ClickHouse, which is why the four-layer medallion in [ADR-009](../adr/ADR-009-medallion-in-clickhouse.md) shipped as three.

---

### 4. Isolate at the blast radius, not the deployment boundary

One feed-handler image, three containers. Deploying one service with an exchange loop would be simpler; it would also mean a Binance parser bug stops Kraken.

Verified rather than assumed: stopping `feed-handler-binance` left Kraken and Coinbase ingesting normally, and Binance resumed within 30 seconds. Cost: two extra container slots. Same reasoning gives each exchange its own bronze table and its own Kafka-engine consumer group.

---

### 5. Use what is already running before adding something new

ClickHouse already consumed from Kafka and already maintained incremental aggregates, so it did the stream processing — deleting five Spark Streaming jobs and a planned Kotlin Silver Processor. Redpanda has a schema registry built in, so there is no Confluent registry. Spark was already present for the offload, so no Iceberg SDK service was written ([ADR-014](../adr/ADR-014-spark-based-iceberg-offload.md)).

The strongest form of this: the best service is the one you notice you do not have to write. Three planned services were deleted from the plan mid-build for exactly this reason, and none was missed.

**Where it bites:** it concentrates load. ClickHouse is now the store *and* the processor — its 4 CPU / 8 GB is the largest slice of the budget, and a ClickHouse outage stops the pipeline end to end (32 s recovery, measured).

---

### 6. Instrument it, then say what is not instrumented

Feed handlers expose Micrometer metrics on `:8082/metrics`; ClickHouse exposes Prometheus on `:9363`; 27 alert rules (17 v2 + 10 capture) are loaded from `docker/prometheus/rules/`; five Grafana dashboards are provisioned from source.

The second half is the part that matters. One gap is documented rather than glossed: no alert has been deliberately fired end to end. An undocumented gap is worse than a known one, because only one of them gets fixed.

---

## Applied to a new component

1. Does something already running do this? (5)
2. What does it cost in CPU and RAM, and what gives up its slot? (1)
3. If it is killed mid-work, what happens on restart? (2)
4. If it fails, what else stops? (4)
5. Are the pre-transform inputs still recoverable? (3)
6. What does it export, and what is still dark? (6)

Answers that are non-obvious become an ADR in [`docs/adr/`](../adr/) — including the ones that later turn out wrong. [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md) argued for deleting Prefect; Prefect is still running. It is kept as written, and the reversal is explained in [MIGRATION-JOURNEY.md](../MIGRATION-JOURNEY.md) rather than edited out.

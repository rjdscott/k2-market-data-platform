# Cost Model

What this platform costs to run, and what the architecture decisions bought.

> **Caveat:** cloud figures below are order-of-magnitude estimates from public
> on-demand list prices, rounded, us-east-1, and not re-verified against a bill.
> Use them to compare *shapes*, not to budget. Check current pricing before quoting.

## What it costs today

The stack is one Docker Compose deployment: **15.20 CPU / 27.188 GiB across 17
long-running services**, rising to 16.70 CPU / 28.688 GiB across all 21 while the four
one-shot init containers overlap at bootstrap, with the ClickHouse hot tier paused and outside
both (see [docker-resources.md](./docker-resources.md), which says how the figures were
computed, and the Outcome addenda to [ADR-010](../adr/ADR-010-resource-budget.md)). It runs on
a single developer workstation, so the marginal cost is electricity.

That is the headline result, not an aside. v1 needed 35–40 CPU and 45–50 GB, more than
2× a 16-core budget, because five always-on Spark Structured Streaming jobs consumed
14 CPU / 20 GB before a single trade was processed. Removing them
([ADR-004](../adr/ADR-004-eliminate-spark-streaming.md)) is what moved the platform
from "needs a cluster" to "runs on one box".

## Cloud equivalent: same footprint

Lift-and-shift onto one VM, keeping the compose file as-is:

| Item | Spec | Estimate / month |
|------|------|-----------------|
| Compute | 16 vCPU / 32 GB general-purpose instance, on demand | $500–600 |
| Same, 1-year reserved | | $300–380 |
| Block storage | 500 GB gp3 | ~$40 |
| Data transfer in | exchange WebSocket feeds, ingress is free on the major clouds | $0 |
| **Total, on demand** | | **≈$550–650** |
| **Total, reserved** | | **≈$350–420** |

At ~50 msg/s that is roughly **$4 per million trades ingested** on demand. The number is
unflattering because the platform is nowhere near saturated: it was sized for a 16-core
envelope and measured at ~3.2 CPU under real load. The same host would absorb an order of
magnitude more throughput before the bill changed.

## Cloud equivalent: managed services

Replacing each component with its managed counterpart, for comparison:

| Component | Self-hosted here | Managed alternative | Estimate / month |
|-----------|-----------------|--------------------|-----------------|
| Streaming | Redpanda (2 CPU / 2 GB) | MSK / Redpanda Cloud, smallest production tier | $250–400 |
| Warm OLAP | ClickHouse (4 CPU / 8 GB), paused ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) | ClickHouse Cloud, smallest always-on service | $200–400 |
| Object storage | MinIO (1 CPU / 1 GB) | S3, ~50 GB compressed | <$5 |
| Orchestration | Airflow api-server, scheduler, dag-processor and statsd exporter beside the shared database (3.1 CPU / 5.56 GiB of limits, `docker-compose.yml`) | a managed Airflow, not priced here | not priced |
| Batch compute | Spark, idle until phase 002 schedules a job; the v3 lake it ran about 2 h/day for is retired | EMR Serverless, ~2h/day of actual work | $30–80 |
| Observability | Prometheus + Grafana (1.5 CPU / 2.5 GB) | Grafana Cloud free tier | $0–50 |
| **Total** | | | **≈$500–1,000**, orchestration not included |

Managed costs **more** than the single VM at this scale and only starts winning once
operational effort, patching, scaling, on-call, exceeds the delta. That crossover is
about team size, not throughput.

## Where cost would actually go

Scaling levers, in the order they would bite:

1. **Retention, not throughput.** Redpanda keeps 48 h of raw frames and 7 days of trades
   and books; the lake keeps each product for the retention its contract states, from the
   first product in phase 002. Storage is the cheapest axis and deliberately so.
2. **More exchanges.** Each one is a `k2-capture` container at 0.25 CPU / 256 MB (512 MB
   for a full-depth venue like Coinbase) plus three more Redpanda topics, near-zero
   marginal cost. See [adding-new-exchanges.md](./adding-new-exchanges.md).
3. **Query concurrency.** ClickHouse, when it returns, is the first thing to need a bigger
   box: it held 21% of the declared CPU and 23% of the RAM while it ran.
4. **Throughput.** Last, not first. The capture tier's saturation point has not been
   measured, the v2 handlers were never the bottleneck at ~150 msg/s
   ([2026-02-19 baseline](../benchmarks/2026-02-19-v2-baseline.md)) and the Rust tier
   that replaced them is smaller, but the headroom multiple is unmeasured until a load
   test runs.

## What the architecture saved

| | v1 | today | Saving |
|---|----|----|--------|
| CPU (limits) | 35–40 | 15.20 | ~60% |
| RAM (limits) | 45–50 GB | 27.188 GiB | ~40% |
| Services | 18–20 | 17 (+4 one-shot) | ~10% |
| Always-on Spark | 14 CPU / 20 GB | 0 (batch only) | 100% |

Almost all of that saving is v1 → v2 and belongs to ADR-004; the v3 changes since are
close to a wash, Lakekeeper added 0.25 CPU / 256 MB and the lake tier's exporter
0.1 CPU / 128 MB (retired with the v3 lake on 2026-11-26), swapping the three Kotlin feed handlers for three Rust capture
containers gave back 0.75 CPU / 0.5 GiB, and deleting the v2 offload took its own exporter
(0.1 CPU / 128 MB) with it
([ADR-010](../adr/ADR-010-resource-budget.md) Outcome addenda).

On the reserved-instance estimate above, roughly 60% less compute is roughly 60% less
compute bill, call it **$500–700/month avoided at this scale**, and proportionally more
at any larger one. The saving comes from deleting a distributed compute framework that a
stateless per-record transform never needed, which is a design decision rather than a
procurement one.

## Related

- [docker-resources.md](./docker-resources.md), the footprint these numbers price
- [ADR-010, resource budget](../adr/ADR-010-resource-budget.md), the 16-core constraint that forced the design
- [ADR-004, eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md), where the saving came from

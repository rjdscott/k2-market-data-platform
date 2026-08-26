# Cost Model

What this platform costs to run, and what the architecture decisions bought.

> **Caveat:** cloud figures below are order-of-magnitude estimates from public
> on-demand list prices, rounded, us-east-1, and not re-verified against a bill.
> Use them to compare *shapes*, not to budget. Check current pricing before quoting.

## What it costs today

The whole stack is one Docker Compose deployment: **15.1 CPU / 21.875 GB across 14
services** (+2 one-shot) (see [docker-resources.md](./docker-resources.md)). It runs on a
single developer workstation, so the marginal cost is electricity.

That is the headline result, not an aside. v1 needed 35–40 CPU and 45–50 GB — more than
2× a 16-core budget — because five always-on Spark Structured Streaming jobs consumed
14 CPU / 20 GB before a single trade was processed. Removing them
([ADR-004](../decisions/ADR-004-eliminate-spark-streaming.md)) is what moved the platform
from "needs a cluster" to "runs on one box".

## Cloud equivalent — same footprint

Lift-and-shift onto one VM, keeping the compose file as-is:

| Item | Spec | Estimate / month |
|------|------|-----------------|
| Compute | 16 vCPU / 32 GB general-purpose instance, on demand | $500–600 |
| Same, 1-year reserved | | $300–380 |
| Block storage | 500 GB gp3 | ~$40 |
| Data transfer in | exchange WebSocket feeds — ingress is free on the major clouds | $0 |
| **Total, on demand** | | **≈$550–650** |
| **Total, reserved** | | **≈$350–420** |

At ~50 msg/s that is roughly **$4 per million trades ingested** on demand. The number is
unflattering because the platform is nowhere near saturated: it was sized for a 16-core
envelope and measured at ~3.2 CPU under real load. The same host would absorb an order of
magnitude more throughput before the bill changed.

## Cloud equivalent — managed services

Replacing each component with its managed counterpart, for comparison:

| Component | Self-hosted here | Managed alternative | Estimate / month |
|-----------|-----------------|--------------------|-----------------|
| Streaming | Redpanda (2 CPU / 2 GB) | MSK / Redpanda Cloud, smallest production tier | $250–400 |
| Warm OLAP | ClickHouse (4 CPU / 8 GB) | ClickHouse Cloud, smallest always-on service | $200–400 |
| Object storage | MinIO (1 CPU / 1 GB) | S3, ~50 GB compressed | <$5 |
| Orchestration | Prefect server + worker + DB (2.5 CPU / 2.5 GB) | Prefect Cloud free tier + a small worker | $0–50 |
| Batch compute | Spark, idle 22h/day | EMR Serverless, ~2h/day of actual work | $30–80 |
| Observability | Prometheus + Grafana (1.5 CPU / 2.5 GB) | Grafana Cloud free tier | $0–50 |
| **Total** | | | **≈$500–1,000** |

Managed costs **more** than the single VM at this scale and only starts winning once
operational effort — patching, scaling, on-call — exceeds the delta. That crossover is
about team size, not throughput.

## Where cost would actually go

Scaling levers, in the order they would bite:

1. **Retention, not throughput.** ClickHouse TTLs are 7 days on bronze, 30 on silver,
   1 year on gold; everything older lives in Iceberg. Zstd level 3 compresses the cold
   tier ~12:1, so a year of trades is tens of gigabytes, not terabytes. Storage is the
   cheapest axis and deliberately so.
2. **More exchanges.** Each one is a feed handler at 0.5 CPU / 512 MB plus a bronze table
   and a materialized view — near-zero marginal cost until ClickHouse's 4 CPU is the
   constraint.
3. **Query concurrency.** ClickHouse is the first thing to need a bigger box. It already
   holds 27% of CPU and 37% of RAM.
4. **Throughput.** Last, not first. A feed handler benchmarks at 5,000+ msg/s against a
   measured ~50 msg/s; the streaming path has ~100× headroom before it costs anything.

## What the architecture saved

| | v1 | v2 | Saving |
|---|----|----|--------|
| CPU (limits) | 35–40 | 15.1 | ~60% |
| RAM (limits) | 45–50 GB | 21.875 GB | ~55% |
| Services | 18–20 | 14 (+2 one-shot) | ~30% |
| Always-on Spark | 14 CPU / 20 GB | 0 (batch only) | 100% |

On the reserved-instance estimate above, roughly 60% less compute is roughly 60% less
compute bill — call it **$500–700/month avoided at this scale**, and proportionally more
at any larger one. The saving comes from deleting a distributed compute framework that a
stateless per-record transform never needed, which is a design decision rather than a
procurement one.

## Related

- [docker-resources.md](./docker-resources.md) — the footprint these numbers price
- [ADR-010 — resource budget](../decisions/ADR-010-resource-budget.md) — the 16-core constraint that forced the design
- [ADR-004 — eliminate Spark Streaming](../decisions/ADR-004-eliminate-spark-streaming.md) — where the saving came from

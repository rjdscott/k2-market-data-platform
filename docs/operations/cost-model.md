# Cost Model

What this platform costs to run, and what the architecture decisions bought.

> **Caveat:** cloud figures below are order-of-magnitude estimates from public
> on-demand list prices, rounded, us-east-1, and not re-verified against a bill.
> Use them to compare *shapes*, not to budget. Check current pricing before quoting.

## What it costs today

The stack is one Docker Compose deployment: **14.60 CPU / 21.625 GiB across 15
long-running services**, rising to 16.10 CPU / 23.125 GiB across all 19 while the four
one-shot init containers overlap at bootstrap (see
[docker-resources.md](./docker-resources.md) and the Kotlin-retirement addendum to
[ADR-010](../adr/ADR-010-resource-budget.md)). It runs on a single developer workstation,
so the marginal cost is electricity.

That is the headline result, not an aside. v1 needed 35–40 CPU and 45–50 GB — more than
2× a 16-core budget — because five always-on Spark Structured Streaming jobs consumed
14 CPU / 20 GB before a single trade was processed. Removing them
([ADR-004](../adr/ADR-004-eliminate-spark-streaming.md)) is what moved the platform
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
2. **More exchanges.** Each one is a `k2-capture` container at 0.25 CPU / 256 MB (512 MB
   for a full-depth venue like Coinbase) plus three more Redpanda topics — near-zero
   marginal cost. See [adding-new-exchanges.md](./adding-new-exchanges.md).
3. **Query concurrency.** ClickHouse is the first thing to need a bigger box. It already
   holds 27% of CPU and 37% of RAM.
4. **Throughput.** Last, not first. The capture tier's saturation point has not been
   measured — the v2 handlers were never the bottleneck at ~150 msg/s
   ([2026-02-19 baseline](../benchmarks/2026-02-19-v2-baseline.md)) and the Rust tier
   that replaced them is smaller, but the headroom multiple is unmeasured until a load
   test runs.

## What the architecture saved

| | v1 | today | Saving |
|---|----|----|--------|
| CPU (limits) | 35–40 | 14.60 | ~60% |
| RAM (limits) | 45–50 GB | 21.625 GiB | ~55% |
| Services | 18–20 | 15 (+4 one-shot) | ~20% |
| Always-on Spark | 14 CPU / 20 GB | 0 (batch only) | 100% |

Almost all of that saving is v1 → v2 and belongs to ADR-004; the v3 changes since are
close to a wash — Lakekeeper added 0.25 CPU / 256 MB, and swapping the three Kotlin feed
handlers for three Rust capture containers gave back 0.75 CPU / 0.5 GiB
([ADR-010](../adr/ADR-010-resource-budget.md) Outcome).

On the reserved-instance estimate above, roughly 60% less compute is roughly 60% less
compute bill — call it **$500–700/month avoided at this scale**, and proportionally more
at any larger one. The saving comes from deleting a distributed compute framework that a
stateless per-record transform never needed, which is a design decision rather than a
procurement one.

## Related

- [docker-resources.md](./docker-resources.md) — the footprint these numbers price
- [ADR-010 — resource budget](../adr/ADR-010-resource-budget.md) — the 16-core constraint that forced the design
- [ADR-004 — eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md) — where the saving came from

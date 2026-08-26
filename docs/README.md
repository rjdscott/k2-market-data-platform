# K2 Documentation

Everything here describes the system as built. Where intent and reality diverge, the docs
say so — the divergence is usually the interesting part.

## Ten-minute review path

For a reviewer opening this repo cold, in order:

1. [Root README](../README.md) — what the platform is, and how to run it.
2. [architecture/README.md](architecture/README.md) — the as-built diagram, per-tier
   detail, the resource table, and an explicit list of what is *not* built.
3. [adr/ADR-008](adr/ADR-008-eliminate-prefect-orchestration.md) — the decision that was
   argued, accepted, and then reversed in practice. The `Outcome` section is the point.
4. [MIGRATION-JOURNEY.md](MIGRATION-JOURNEY.md) — v1 → v2: phase log, measured outcomes,
   and every prediction scored against what shipped.
5. [benchmarks/2026-02-19-v2-baseline.md](benchmarks/2026-02-19-v2-baseline.md) — the
   numbers quoted everywhere else, with the command behind each and the sample-size
   caveat stated plainly.
6. [audits/2026-08-26-doc-accuracy.md](audits/2026-08-26-doc-accuracy.md) — 31 findings
   from an adversarial pass over these docs, published rather than quietly fixed.

## The pipeline

Five surfaces, one flow. Research asks, ADRs commit, plans sequence, audits verify, and
benchmarks supply the numbers all four of them cite. Runbooks sit alongside: ADRs record
*why*, runbooks record *how*.

```
research/  →  adr/  →  plans/  →  audits/
    ↑                                 │
    └──────── benchmarks/ ────────────┘        runbooks/  (operations)
```

| Surface | Holds | Conventions |
|---------|-------|-------------|
| [research/](research/) | Analysis before a decision, dated. Allowed to be wrong in hindsight | [README](research/README.md) |
| [adr/](adr/) | 21 ADRs. Immutable once accepted; divergence recorded in an `Outcome` section | [README](adr/README.md) · [template](adr/template.md) |
| [plans/](plans/) | Multi-phase design documents with exit criteria per phase | [README](plans/README.md) |
| [audits/](audits/) | Point-in-time sweeps of one surface, with a findings table | [README](audits/README.md) |
| [benchmarks/](benchmarks/) | Dated measurement snapshots. Every published number traces here | [README](benchmarks/README.md) |
| [runbooks/](runbooks/) | 13 incident procedures (8 v2 + 5 v3 capture), one per alert family, with measured MTTR | [README](runbooks/README.md) · [template](runbooks/template.md) |

## Reference

The system as built, for readers who want the detail rather than the argument.

| Doc | What's in it |
|---|---|
| [architecture/README.md](architecture/README.md) | As-built system: diagram, tiers, data model, lifecycle, footprint, known gaps |
| [architecture/technology-stack.md](architecture/technology-stack.md) | Every component, its version, its job, and the ADR that chose it |
| [architecture/schema-design.md](architecture/schema-design.md) | Bronze / Silver / Gold columns, the Avro normalized-trade contract, precision choices |
| [architecture/partitioning-strategy.md](architecture/partitioning-strategy.md) | Redpanda partitions, ClickHouse partition and sort keys, Iceberg partition specs |
| [architecture/streaming-sources.md](architecture/streaming-sources.md) | How a capture process works, the per-venue dialects, and what a fourth exchange costs |
| [architecture/platform-principles.md](architecture/platform-principles.md) | The handful of rules the design is actually held to |
| [architecture/positioning.md](architecture/positioning.md) | What this platform is for — and the workloads it is deliberately wrong for |
| [operations/](operations/) | Running the stack: [quick reference](operations/quick-reference.md), [Docker budget](operations/docker-resources.md), [observability](operations/observability.md), [latency budgets](operations/latency-budgets.md), [Prefect schedules](operations/prefect-schedules.md), [data inspection](operations/data-inspection.md), [adding an exchange](operations/adding-new-exchanges.md), [cost model](operations/cost-model.md) |
| [development/](development/) | [setup.md](development/setup.md) and [testing.md](development/testing.md) — prerequisites, and how to run the Rust and Python suites |

## Migration journey

[MIGRATION-JOURNEY.md](MIGRATION-JOURNEY.md) — why v1 was replaced, what each phase
shipped, the measured resource / latency / MTTR results, a scorecard of predictions
against reality, and what is still unfinished.

The v1 platform itself is archived at [`legacy/v1/`](../legacy/v1/README.md), including
its 14 runbooks. They describe an architecture that no longer exists.

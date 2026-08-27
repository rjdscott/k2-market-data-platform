# K2 Documentation

Everything here describes the system as built. Where intent and reality diverge, the docs
say so, the divergence is usually the interesting part.

## Ten-minute review path

For a reviewer opening this repo cold, in order:

1. [Root README](../README.md), what the platform is, and how to run it.
2. [architecture/README.md](architecture/README.md), each component: how it is built,
   how it works, what it trades away; and what is *not* built.
3. [adr/ADR-026](adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md), the
   decision that shaped the lake and the serving tier, with its rejected alternatives.
4. [adr/ADR-008](adr/ADR-008-eliminate-prefect-orchestration.md), a decision argued,
   accepted, and reversed in practice. The `Outcome` section is the point.
5. [benchmarks/2026-08-27.md](benchmarks/2026-08-27.md), the numbers quoted everywhere
   else, the command behind each, and the window they came from.
6. [audits/2026-08-26-doc-accuracy.md](audits/2026-08-26-doc-accuracy.md), 31 findings
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
| [adr/](adr/) | 27 ADRs. Immutable once accepted; divergence recorded in an `Outcome` section | [README](adr/README.md) · [template](adr/template.md) |
| [plans/](plans/) | Multi-phase design documents with exit criteria per phase | [README](plans/README.md) |
| [audits/](audits/) | Point-in-time sweeps of one surface, with a findings table | [README](audits/README.md) |
| [benchmarks/](benchmarks/) | Dated measurement snapshots. Every published number traces here | [README](benchmarks/README.md) |
| [runbooks/](runbooks/) | 12 incident procedures, one per alert family, with measured MTTR | [README](runbooks/README.md) · [template](runbooks/template.md) |

## Reference

The system as built, for readers who want the detail rather than the argument.

| Doc | What's in it |
|---|---|
| [architecture/](architecture/README.md) | The book: numbered chapters from purpose to scale-out, one per component, each with a practices table |
| [architecture/A1-technology-stack.md](architecture/A1-technology-stack.md) | Every component, its version, its job, and the ADR that chose it |
| [architecture/13-schema-design.md](architecture/13-schema-design.md) | Bronze / Silver / Gold columns, the Avro normalized-trade contract, precision choices |
| [architecture/14-partitioning-strategy.md](architecture/14-partitioning-strategy.md) | Redpanda keys and partition counts, Iceberg partition specs and sort orders, and why symbol is in neither. The ClickHouse section lands with Phase E |
| [architecture/17-scale-out-path.md](architecture/17-scale-out-path.md) | Every tier's AWS equivalent at TB/PB, what changes vs what does not, and the partition/file/manifest arithmetic at 400×. *Designed, not exercised* |
| [architecture/15-capacity-model.md](architecture/15-capacity-model.md) | Predicted msg/s, bytes/day and headroom for the v3 capture and lake tiers, each row naming its assumption |
| [architecture/16-failure-modes.md](architecture/16-failure-modes.md) | FMEA: one row per component × failure, with its detection signal, blast radius, recovery step and proof |
| [architecture/06-capture-venues.md](architecture/06-capture-venues.md) | How a capture process works, the per-venue dialects, and what a fourth exchange costs |
| [architecture/01-what-k2-is.md](architecture/01-what-k2-is.md) | What this platform is for, the workloads it is deliberately wrong for, and the six rules the design is held to |
| [operations/](operations/) | Running the stack: [quick reference](operations/quick-reference.md), [Docker budget](operations/docker-resources.md), [observability](operations/observability.md), [latency budgets](operations/latency-budgets.md), [Prefect schedules](operations/prefect-schedules.md), [data inspection](operations/data-inspection.md), [adding an exchange](operations/adding-new-exchanges.md), [cost model](operations/cost-model.md) |
| [development/](development/) | [setup.md](development/setup.md) and [testing.md](development/testing.md), prerequisites, and how to run the Rust and Python suites |

## Migration journey

[MIGRATION-JOURNEY.md](MIGRATION-JOURNEY.md), why v1 was replaced, what each phase
shipped, the measured resource / latency / MTTR results, a scorecard of predictions
against reality, and what is still unfinished.

The v1 platform itself is archived at [`legacy/v1/`](../legacy/v1/README.md), including
its 14 runbooks. They describe an architecture that no longer exists.

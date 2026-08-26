# K2 Documentation

Everything here describes the system as built. Where intent and reality diverge, the docs say so.

## Start here — a 10-minute review path

1. [Root README](../README.md) — what the platform is, how to run it.
2. [architecture/README.md](architecture/README.md) — as-built diagram, per-tier detail, resource table, and an explicit list of what is *not* built.
3. [decisions/ADR-008](decisions/ADR-008-eliminate-prefect-orchestration.md) — the decision that was argued, accepted, and then reversed in practice. Read it next to the Predictions-vs-reality table below.
4. [MIGRATION-JOURNEY.md](MIGRATION-JOURNEY.md) — v1 → v2: the phase log, measured outcomes, and every prediction scored against what shipped.
5. [operations/runbooks/failure-recovery.md](operations/runbooks/failure-recovery.md) — the six failure modes that were actually injected, and their measured MTTRs.

## Architecture

| Doc | What's in it |
|---|---|
| [architecture/README.md](architecture/README.md) | The as-built system: diagram, tiers, data model, lifecycle, footprint, known gaps |
| [architecture/technology-stack.md](architecture/technology-stack.md) | Every component, its version, its job, and the ADR that chose it |
| [architecture/schema-design.md](architecture/schema-design.md) | Bronze / Silver / Gold columns, the Avro normalized-trade contract, precision choices |
| [architecture/partitioning-strategy.md](architecture/partitioning-strategy.md) | Redpanda partitions, ClickHouse partition and sort keys, Iceberg partition specs |
| [architecture/streaming-sources.md](architecture/streaming-sources.md) | How a feed handler works and what integrating a fourth exchange costs |
| [architecture/platform-principles.md](architecture/platform-principles.md) | The handful of rules the design is actually held to |
| [architecture/positioning.md](architecture/positioning.md) | What this platform is for — and the workloads it is deliberately wrong for |

## Decisions

[decisions/](decisions/) holds 17 ADRs plus the investment analysis that ranked the v2 work before any of it started. They are kept as written, including the ones that turned out wrong.

## Operations

[operations/](operations/) covers running the stack: [quick reference](operations/quick-reference.md), [Docker resource budget](operations/docker-resources.md), [observability](operations/observability.md), [latency budgets](operations/latency-budgets.md), [Prefect schedules](operations/prefect-schedules.md), [data inspection](operations/data-inspection.md), [adding a new exchange](operations/adding-new-exchanges.md), and [runbooks/](operations/runbooks/) for offload failures, watermark recovery and Redpanda incidents.

## Development

[development/testing.md](development/testing.md) — how to run the Kotlin and Python test suites, including the Docker-based Gradle invocation (`make test-kotlin` runs `./gradlew test` inside the `gradle:8.12-jdk21` image, no local JDK needed).

## Migration journey

[MIGRATION-JOURNEY.md](MIGRATION-JOURNEY.md) — why v1 was replaced, what each phase shipped, the measured resource / latency / MTTR results, a scorecard of predictions against reality, and what is still unfinished.

The v1 platform itself is archived at [`legacy/v1/`](../legacy/v1/README.md).

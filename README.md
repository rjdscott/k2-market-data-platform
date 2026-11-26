<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
    <img alt="K2 Market Data Platform: Rust capture, Redpanda, Iceberg lakehouse, Trino SQL" src="docs/assets/banner-light.svg" width="100%">
  </picture>
</p>

# K2 Market Data Platform

A market data platform for quantitative research. Three crypto exchanges are captured
verbatim over public WebSocket feeds into Redpanda. The Iceberg lakehouse (Lakekeeper, MinIO,
Spark, Airflow, with Trino for SQL over it) runs, but the old pipeline that filled it is
retired to [`legacy/v3-lake-ingest/`](./legacy/v3-lake-ingest/) and the lake receives no live
crypto data until phase `004` of the plan below; the ClickHouse hot tier is paused while the
platform is built ([ADR-057](./docs/adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). One host, inside a 25 CPU / 48 GiB ceiling. Not a trading path.

[![CI](https://github.com/rjdscott/k2-market-data-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/rjdscott/k2-market-data-platform/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Rust](https://img.shields.io/badge/rust-1.98-orange.svg)](https://www.rust-lang.org/)
[![ClickHouse](https://img.shields.io/badge/clickhouse-24.3_LTS-yellow.svg)](https://clickhouse.com/)

## What this is, and data terms

K2 is a platform you run on your own machine for research and quant workflows. It is a
reference implementation, not a hosted service. It is not a market data distribution
service and not for production trading, and it is not affiliated with or endorsed by
Binance, Kraken, Coinbase, any other venue, or the European Central Bank.

The repository ships code and contracts; every venue-format fixture in it is synthetic
([`NOTICE`](./NOTICE)). Market data is fetched by your own stack from public endpoints,
stays on your machine, and is subject to each provider's terms:
[Binance](https://www.binance.com/en/terms), [Kraken](https://www.kraken.com/legal),
[Coinbase](https://www.coinbase.com/legal/user_agreement). Complying with them is your
responsibility.

The one named exception is pictures. Images under `docs/images/` are screenshots of a live
run (`make screenshots` takes the current set): they show venue-derived figures as they stood
at the moment of capture, are for illustration only, and are not a data set
([`NOTICE`](./NOTICE), "Screenshots").

## Where this is going

K2 is being rebuilt from one crypto pipeline into a **domain-agnostic data product platform**:
any dataset is defined once in a contract, brought in by its own connector, stored in layers,
listed in one catalog, and served through one SQL endpoint and a REST API under the same
permissions. Market data is the first tenant; the value is in joining products across domains.

| Read | For |
|---|---|
| [Chapter 20, the target platform](docs/architecture/20-target-data-product-platform.md) | The design, its ethos and twelve diagrams |
| [ADR-056](docs/adr/ADR-056-unified-data-product-platform.md) | The decision, its alternatives and its costs |
| [The plan to the demo tag](docs/plans/2026-11-26-unified-product-platform/README.md) and [to release](docs/plans/2026-11-26-unified-product-platform-to-release/README.md) | The phases, each with an exit a command proves |
| [The research note](docs/research/2026-11-26-unified-platform-architecture.md) | How the design was chosen: three competing designs, a judge, adversarial reviews, spikes |

Everything below this section, and chapters 04 to 19 of the architecture book, describe **what
runs today**. Each phase of the plan updates them in the pull request that makes a change real.

## Overview

| Property | How |
|---|---|
| Complete | One Rust process per venue holds a single socket carrying trades and L2 book, stamps `recv_ts_ns` before parsing, verifies Kraken's CRC32 on every update, tracks each venue's sequence numbers, and publishes every frame verbatim. The L2 book is paused on every venue while the platform is built, by a per-venue switch ([ADR-050](./docs/adr/ADR-050-order-book-capture-paused-during-platform-build.md)). |
| Retained | Redpanda keeps the raw topics for 48 hours or 2 GiB per partition, and trades and book for 7 days, so phase `004`'s first lake product starts at the log start; nothing older is recovered. The old lake ingest, its four layers and its dbt marts are archived in [`legacy/v3-lake-ingest/`](./legacy/v3-lake-ingest/), and their tables were dropped after a verified copy (`make drop`). |
| Disposable serving | ClickHouse `gold` is paused behind the compose profile `hot-gold` while the platform is built, its DDL still tested by `make test-clickhouse` ([ADR-057](./docs/adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). Dedup is a `ReplacingMergeTree` key; candles are computed on read, so a replay cannot corrupt one. |
| Proven | 12 alert rules as code, chaos scripts with measured recovery, a fresh-clone release check before every tag. Every gate that read the old lake tables prints a `SKIP` line naming the phase that restores it. |

[![K2 Pipeline Overview on a live run: every scrape target up, trade records per venue, Redpanda throughput, and the pipeline's CPU and memory](docs/images/dashboards/01-pipeline-overview.png)](docs/operations/dashboards.md)

The pipeline overview on a live run; its figures are venue-derived and for illustration only.
Every other screen, in the order the data moves through them, with what to read from each and
the runbook it leads to: [dashboards and consoles](docs/operations/dashboards.md).

## Architecture

```mermaid
flowchart TB
  E["Exchanges · Binance · Kraken · Coinbase<br/>public WebSocket · 34 instruments"]:::ex
  F["k2-capture ×3 · Rust<br/>trades + L2 book on one socket<br/>L2 paused: ADR-050"]:::rs
  R[("Redpanda 25.3 · Avro + schema registry<br/>raw · trades · book per venue<br/>raw kept 48 h")]:::rp
  C["ClickHouse 24.3 · gold<br/>paused behind profile hot-gold<br/>ADR-057"]:::ch
  subgraph L["Iceberg lakehouse · Lakekeeper + MinIO · no live data until phase 004"]
    direction TB
    S["Airflow 3 · Spark 3.5<br/>no DAG until phase 002"]:::sp
    T["Trino 484 · SQL over the lake, read-only<br/>one warehouse per domain"]:::nb
  end
  O["Prometheus · 12 rules<br/>Grafana · 4 dashboards"]:::ob

  E --> F --> R
  R -.->|Kafka engine, paused| C
  F -.-> O

  classDef ex fill:#e5e7eb,stroke:#374151,color:#111827
  classDef rs fill:#c7d2fe,stroke:#4338ca,color:#111827
  classDef rp fill:#fecaca,stroke:#b91c1c,color:#111827
  classDef sp fill:#fed7aa,stroke:#c2410c,color:#111827
  classDef ch fill:#fde68a,stroke:#b45309,color:#111827
  classDef nb fill:#ddd6fe,stroke:#6d28d9,color:#111827
  classDef ob fill:#f3f4f6,stroke:#6b7280,color:#111827
```

17 long-running services and 4 one-shot init containers, 15.20 CPU / 27.188 GiB of declared
limits at steady state and 16.70 CPU / 28.688 GiB at the bootstrap peak, under a 25 CPU / 48 GiB
ceiling ([ADR-045](./docs/adr/ADR-045-ceiling-25-cpu-48-gib.md); [how the numbers are produced](./docs/operations/docker-resources.md#how-these-numbers-are-produced)).
The scheduler Airflow replaced is archived in [`legacy/prefect/`](./legacy/prefect/README.md), with no rollback path in the compose file.

**Read the book.** [`docs/architecture/`](./docs/architecture/README.md) is written as numbered
chapters: what the platform is and is not, the market data and data engineering concepts it
rests on, then one chapter per component with the problem, the options, the decision, how it
works, and the practices that enforce it.

## Key decisions

| ADR | Decision | Why |
|---|---|---|
| [018](./docs/adr/ADR-018-v3-lake-first-rust-capture.md) | The lake is the system of record; everything else is derived | v2's lake was a JDBC copy of the serving database and inherited its TTL and dropped columns |
| [019](./docs/adr/ADR-019-rust-capture-tier.md) | One Rust process per venue replaces three JVM handlers | Trades and book on one connection, `recv_ts` before parse, retired on a measured parity gate |
| [020](./docs/adr/ADR-020-avro-fixed-point-contracts.md) | Avro with registry, `int64` at 1e-8, `BACKWARD_TRANSITIVE` | v2 stored prices as strings and never read its own Avro topic |
| [022](./docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md) | Kafka offsets committed inside the Iceberg snapshot | One atomic commit replaces a watermark table and its failure modes |
| [026](./docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | Four lake layers; ClickHouse serves gold with no TTL | Venue fields survive to silver; candles on read fix v2's per-block open/close bug |
| [027](./docs/adr/ADR-027-book-snapshot-and-sequencing.md) | Top-20 snapshots at 1 Hz; per-venue sequencing and resync | Raw keeps the deltas; the product is what research joins against |
| [008](./docs/adr/ADR-008-eliminate-prefect-orchestration.md) | Remove the orchestrator: reversed | A wrong call kept on the record with its outcome |

All 44 ADRs: [`docs/adr/`](./docs/adr/README.md).

## Measured

From [`docs/benchmarks/2026-08-27.md`](./docs/benchmarks/2026-08-27.md), a 6.5 h capture
window; every row there carries the command that produced it. Counts of captured data stay
in that dated snapshot and are not repeated here (NOTICE).

| | Binance | Kraken | Coinbase |
|---|---:|---:|---:|
| Exchange to receive, p50 / p99 (ms) | 42 / 207 | 177 / 459 | 184 / see note |
| Sequence gaps | 0 | 0 | 0 |
| Checksum failures | n/a | 0, every book frame verified | n/a |
| Produce errors | 0 | 0 | 0 |

Coinbase's p99 was the venue's on-subscribe trade snapshot, not transit; the capture now
excludes it from the histogram.

| Operation | Result |
|---|---|
| Lake bronze rebuild from raw | the whole archive, 3,326,927,766 B of raw frames into six tables, in 520 s |
| Per-venue bronze vs raw archive | 0.59× |
| Lake growth | about 9.8 GB/day |
| ClickHouse pull of gold trades from the lake | the whole lake table in 4.4 s |
| Chaos: lake ingest killed / MinIO stopped / Lakekeeper stopped | 42 s / 38 s / 37 s |
| Chaos: ClickHouse stopped 150 s | alert at 160 s, healthy 7 s after restart |
| Chaos: corrupt feed record | isolated in 4 s |

The lake and ClickHouse rows measured tiers that are now retired or paused; they stay as
measured on that date. Detail per failure: [failure modes](./docs/architecture/16-failure-modes.md).

## Quick start

Requires a Docker engine with at least 29 GiB of memory so every declared limit is honoured,
the 28.688 GiB bootstrap peak included ([setup](./docs/development/setup.md)); measured usage is
far lower. On Docker Desktop the VM grows to its whole memory setting, so the host needs more
RAM than that setting plus everything else it runs
([sizing](./docs/runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory)).

Two lines of `.env` are host-specific and are the whole first-run gotcha:
`K2_CAPTURE_CPUSET` and `K2_HEAVY_CPUSET` ship empty (no CPU pinning, starts anywhere) —
uncomment the 15-core layout in `.env.example` only on a host that has those cores, or five
containers (six with `hot-gold`) fail at start with `invalid argument`. `K2_AIRFLOW_PORT` (default 8090, loopback
only) is Airflow's host port, and it moves the UI's own base URL with it; `K2_DOCKER_GID` is
the group that owns the Docker socket, 0 on Docker Desktop.

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git && cd k2-market-data-platform
cp .env.example .env            # set every change-me value: openssl rand -hex 24 each;
                                # LAKEKEEPER_ENCRYPTION_KEY: openssl rand -base64 32
set -a && . ./.env && set +a
make up                         # refuses a placeholder secret (scripts/check-env.py), then
                                # docker compose up -d; first run builds the capture, airflow
                                # and spark images
make health                     # every service healthy and a message on every venue in the
                                # last 2 min; the lake check prints SKIP until phase 004
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance --num 1
```

Step by step, with a command that fills every secret: the
[operator guide](./docs/operations/README.md), which also covers health, the consoles,
stopping, restarting one service and rolling out a change. The measured timeline of a fresh
install: [fresh install](./docs/runbooks/fresh-install.md).

The lake holds no crypto tables until phase `004`: the old data catalog, notebooks and labs
are archived in [`legacy/v3-lake-ingest/`](./legacy/v3-lake-ingest/).

Grafana is on http://localhost:3000 and Airflow on http://localhost:8090. Every console, its
URL, what it is for and which `.env` variable holds its login is one table:
[consoles and endpoints](./docs/operations/README.md#4-consoles-and-endpoints).
Every port listens on `127.0.0.1` only, which keeps other machines out but not other users
of the host; reach a console from elsewhere through an SSH tunnel, never an open port.
[SECURITY.md](./SECURITY.md) says what is and is not protected.

## Tests and CI

Counted 2026-11-24, the Rust row again on 2026-11-26, by the command in each row's footnote, not
by hand (on 2026-09-03 a hand-kept version of this table read 60 / 229 / 9 against a reality
of 68 / 287 / 10). A gate that read the old lake tables prints `SKIP` and the phase that restores it.

| Suite | Run |
|---|---|
| Rust capture: 79 tests [^rust], 63 unit, 4 on the book switch and 12 replay over synthetic sessions; `make record-fixtures` replays real ones locally | `make test-rust` |
| Python: tests [^py] over contracts, wire format, offsets, decoders, the gates themselves | `make test-python` |
| ClickHouse schema: 10 assertions [^ch], including the v2 OHLCV regression | `make test-clickhouse` |
| Trino: the real `docker/trino/etc` starts, catalog `lake` refuses every commit and table procedure, only a domain's writer changes its tables with SQL statements (table procedures are not fenced per user, ADR-044); 134 checks [^trino], no stack | `make test-trino` |
| dbt: lint, parse and compile of a project with no marts; the marts retired with the old pipeline and dbt builds again in phase `002`; no stack | `make test-dbt` |
| Prometheus rule unit tests (6 of 12 alerts) and 11 documentation gates | `bash scripts/check-docs.sh` |
| Live stack: chaos against the captures and Redpanda. `make lake-verify`, `make parity-ohlcv` and `make parity-bars` print `SKIP` until phase `004` | `make chaos` |

[^rust]: `grep -rc '#\[test\]' services/capture-rust/src services/capture-rust/tests --include='*.rs' | awk -F: '{s+=$2} END {print s}'`
[^py]: the last line `make test-python` prints
[^ch]: the count `scripts/clickhouse-schema-test.sh` itself asserts (`[ "$n" -eq 10 ]`)
[^trino]: `make test-trino | grep -c '^ok '` (2026-11-25, five domains)

CI ([`ci.yml`](./.github/workflows/ci.yml)) runs rust (fmt, clippy `-D warnings`, test),
python (Ruff, pytest), contracts (k2gen drift, tests, Ruff, mypy), clickhouse-schema, trino
(`make test-trino`), dbt (`make test-dbt`), a docker build matrix, compose validation with the resource-budget and
pin checks, the documentation gates and Trivy on every pull request.

## Repository layout

```
services/capture-rust/     k2-capture: one binary, three exchanges
docker/airflow/            the Airflow image; no DAG until phase 002
docker/lake/               modules the new connectors reuse: offsets, wire framing, bars, books
docker/clickhouse/ddl/     gold contract (CI-tested) and Kafka feeds; the tier is paused (ADR-057)
docker/trino/              Trino config: catalog lake (read-only), one generated catalog per domain, access rules
docker/prometheus/rules/   12 alert rules, 6 with unit tests
schemas/avro/              raw-message, trade, book-snapshot-l2
config/instruments.yaml    instrument registry, the single source of truth
contracts/                 the domains file and the contract schemas; the old product contracts are archived
platform/k2gen/            k2gen, the contract validator
dbt/                       dbt on Trino, no marts until phase 002; sources and selectors generated from contracts/
scripts/chaos/             failure injection, timed
docs/                      the architecture book, ADRs, runbooks, benchmarks, plans
legacy/                    v1, v2 Kotlin handlers, v2 ClickHouse DDL, v2 offload, the scheduler Airflow replaced, the v3 lake ingest: archived (v1 has had dependency pins bumped since; see legacy/v1/README.md)
```

## Not built

No query API. No replication or failover: one broker, one host. No
Alertmanager routing. No load test above 1×. A pcap sidecar and a cross-venue security
master are designed ([ADR-026](./docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md),
[data strategy](./docs/architecture/12-data-strategy.md)) and not started.

## Documentation

- [`docs/`](./docs/README.md): the map — which surface answers which kind of question
- [`docs/operations/`](./docs/operations/README.md): the operator guide, start to roll-out, then inspecting and recovering the stack
- [`docs/architecture/`](./docs/architecture/README.md): the book, start at chapter 00
- [`docs/adr/`](./docs/adr/README.md): why, one decision per file, never edited once accepted
- [`docs/runbooks/`](./docs/runbooks/README.md): how, one per alert family, with measured recovery
- [`docs/benchmarks/`](./docs/benchmarks/README.md): every published number and its command
- [`docs/MIGRATION-JOURNEY.md`](./docs/MIGRATION-JOURNEY.md): v1 to v2 to v3, with what each phase measured

## License

MIT, see [`LICENSE`](./LICENSE). © Rob Scott.

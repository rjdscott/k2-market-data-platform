# K2 Market Data Platform

A crypto market-data lakehouse that ingests live trades from three exchanges and turns them into
queryable OHLCV candles in under a second — on a single host, inside a 16-core / 40 GB budget.

[![CI](https://github.com/rjdscott/k2-market-data-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/rjdscott/k2-market-data-platform/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Rust](https://img.shields.io/badge/rust-1.98-orange.svg)](https://www.rust-lang.org/)
[![ClickHouse](https://img.shields.io/badge/clickhouse-24.3_LTS-yellow.svg)](https://clickhouse.com/)

## What this demonstrates

- **A rewrite justified by numbers, not taste.** v1: 18–20 containers, 35–40 CPU / 45–50 GB, 5–15 min
  trade-to-queryable. v2 baseline: 14 services (+2 one-shot), 15.1 CPU / 21.875 GB, measured p99
  170–197 ms. **Phase C** has landed: the Rust `k2-capture` tier replaced the Kotlin handlers on
  a labelled per-symbol parity gate ([ADR-019](./docs/adr/ADR-019-rust-capture-tier.md), whose
  Outcome carries the window's numbers), and **Phase D** replaced the v2 ClickHouse→Iceberg offload
  with a Redpanda→Iceberg lake ingest ([ADR-018](./docs/adr/ADR-018-v3-lake-first-rust-capture.md)) —
  steady state **15 long-running services (+4 one-shot), 14.60 CPU / 21.625 GiB**, carrying L2
  order books the JVM tier never had. Each move is an ADR.
  <sub>Every figure on this page: `docker compose --env-file .env.example config`, limits summed —
  command in [docs/operations/docker-resources.md](./docs/operations/docker-resources.md#how-these-numbers-are-produced).</sub>
- **Deleting the stream processor.** Five always-on Spark Structured Streaming jobs (~14 CPU / 20 GB)
  replaced by ClickHouse Kafka engine tables and materialized views — **zero stream-processing code**.
- **Exchange-native ingestion.** Three Rust `k2-capture` containers, one per exchange, each owning
  one WebSocket dialect and carrying trades *and* L2 book on a single connection. `recv_ts_ns` is
  stamped before the parser, arithmetic is fixed-point `i64` end to end, and the same
  `handle_frame` runs live and in replay. Adding an exchange is additive.
- **A lake with real semantics.** Prefect drives a Spark batch ingest that reads Redpanda by offset range
  into Apache Iceberg (Lakekeeper + MinIO) every 5 min, exactly-once via the consumed offsets written into
  the same snapshot that writes the rows, with nightly compaction + completeness audits.
- **Operability as a deliverable.** 25 Prometheus alert rules (4 v2 + 10 v3 capture +
  11 v3 lake), 5 Grafana dashboards, and six failure modes deliberately induced and timed — max MTTR 32 s.
- **A reversed decision, kept in the record.** ADR-008 argued for removing Prefect. It was wrong, and
  the record says so rather than being quietly deleted.

## Architecture

```mermaid
flowchart LR
  E["Exchanges<br/>Binance · Kraken · Coinbase<br/>34 instruments"]:::kt
  F["k2-capture · 3 containers<br/>Rust 1.98 · trades + L2 book"]:::kt
  R["Redpanda 25.3<br/>v3: 9 topics · 108 partitions<br/>v2: 6 topics · frozen"]:::rp
  subgraph CH["ClickHouse 24.3 LTS — hot tier"]
    B["bronze tables<br/>one per exchange"]:::ch
    S["silver_trades"]:::ch
    G["ohlcv 1m · 5m · 15m<br/>30m · 1h · 1d"]:::ch
  end
  subgraph BATCH["Orchestrated batch"]
    P["Prefect 3"]:::sp
    K["Spark 3.5"]:::sp
  end
  I["Iceberg lake · Lakekeeper + MinIO<br/>raw · bronze · audit"]:::st
  subgraph OBS["Observability"]
    M["Prometheus<br/>25 alert rules"]:::ob
    D["Grafana<br/>5 dashboards"]:::ob
  end

  E -->|WebSocket| F
  F -->|"raw JSON + Avro"| R
  R -->|"Kafka engine · JSON"| B
  B -->|materialized view| S
  S -->|materialized view| G
  P -->|every 5 min| K
  R -.->|"offset range"| K
  K -->|append| I
  F -.->|/metrics| M
  CH -.->|:9363| M
  K -.-> M
  M --> D

  classDef kt fill:#c7d2fe,stroke:#4338ca,color:#111827
  classDef rp fill:#fecaca,stroke:#b91c1c,color:#111827
  classDef ch fill:#fde68a,stroke:#b45309,color:#111827
  classDef sp fill:#fed7aa,stroke:#c2410c,color:#111827
  classDef st fill:#bbf7d0,stroke:#15803d,color:#111827
  classDef ob fill:#e5e7eb,stroke:#374151,color:#111827
```

Each handler holds one WebSocket and produces every trade twice to Redpanda: the raw exchange JSON
and a normalized Avro record registered in the built-in schema registry.
ClickHouse consumes with Kafka engine tables; materialized views carry rows from per-exchange bronze into
a unified `silver_trades` and on into six OHLCV tables — no scheduler, no streaming job, no application
code in the hot path. Independently, every 5 min a Prefect flow runs Spark to read Redpanda **by offset
range** and append to Iceberg — raw frames verbatim first, decoded `bronze.*` from that archive second —
with the consumed offsets written into the same commit as the rows, so a failed run resumes rather than
duplicates ([ADR-022](./docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md)). The catalog is Lakekeeper
over MinIO ([ADR-023](./docs/adr/ADR-023-lakekeeper-rest-catalog.md)); v2's Hadoop catalog on a local
volume, and the ClickHouse→Iceberg offload that wrote to it, are deleted.

## v1 → v2

| | v1 | v2 |
|---|---|---|
| CPU / RAM (limits) | 35–40 cores / 45–50 GB | **15.1 cores / 21.875 GB** |
| Services | 18–20 | **14** (+2 one-shot init containers) |
| Always-on Spark | 5 streaming jobs, ~14 CPU / 20 GB | **none** — batch only |
| Trade → queryable | 5–15 min | **p99 170–197 ms** |
| Stack | Python · Kafka · Spark Streaming · DuckDB · FastAPI | Kotlin/Ktor · Redpanda · ClickHouse · Spark batch · Iceberg |

The v2 column is the baseline this repo was measured at. What is deployed here now is v2 with its
capture tier swapped for v3's and its cold tier replaced by the v3 lake: Rust `k2-capture` in place of
the three Kotlin handlers, plus Lakekeeper, `lake-metrics` and 4 one-shot init containers —
**14.60 CPU / 21.625 GiB across 15 long-running services (+4 one-shot, 1.50 CPU / 1.500 GiB)**, a
bootstrap peak of 16.10 CPU / 23.125 GiB across all 19. The Kotlin handlers are archived in
[`legacy/v2-kotlin/`](./legacy/v2-kotlin/README.md)
([ADR-019](./docs/adr/ADR-019-rust-capture-tier.md)); with them went the only producer of the v2
topics, so the ClickHouse `k2` medallion is **frozen** — still queryable, no longer growing — until
the Phase E cutover drops it. The v2 ClickHouse→Iceberg offload is deleted outright
([ADR-022](./docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md)); its runbooks are archived in
[`legacy/v2-offload/`](./legacy/v2-offload/). Source for all of them:
`docker compose --env-file .env.example config`, limits summed
([command](./docs/operations/docker-resources.md#how-these-numbers-are-produced)).

v1 is preserved unmodified in [`legacy/v1/`](./legacy/v1/); the narrative is in
[`docs/MIGRATION-JOURNEY.md`](./docs/MIGRATION-JOURNEY.md).

## Key decisions

| ADR | Decision | Why it mattered |
|---|---|---|
| [ADR-001](./docs/adr/ADR-001-replace-kafka-with-redpanda.md) | Kafka → Redpanda | One binary, built-in schema registry and console; −1.5 CPU / −1.8 GB |
| [ADR-003](./docs/adr/ADR-003-clickhouse-warm-storage.md) | ClickHouse as the hot tier | Made real-time aggregation a view instead of a job |
| [ADR-004](./docs/adr/ADR-004-eliminate-spark-streaming.md) | Kill Spark Structured Streaming | The single largest win: ~14 CPU / 20 GB reclaimed |
| [ADR-009](./docs/adr/ADR-009-medallion-in-clickhouse.md) | Medallion layers as ClickHouse MVs | Bronze → Silver → Gold becomes DDL, not a codebase |
| [ADR-011](./docs/adr/ADR-011-multi-exchange-bronze-architecture.md) | Per-exchange bronze tables | Exchange quirks stay isolated; adding Coinbase touched no existing table |
| [ADR-008](./docs/adr/ADR-008-eliminate-prefect-orchestration.md) | **Planned to remove Prefect — kept it** | Reversed in practice: retries, scheduling, and run history were worth 1.5 CPU. The ADR stands as written, with the outcome recorded. |

## Quick start

Requires a Docker engine with ≥ 24 GB memory so every `deploy.resources.limits` can be honoured
(`docker info --format '{{.MemTotal}}'`); measured steady-state usage is far lower
(see [docs/operations/docker-resources.md](./docs/operations/docker-resources.md)), so the stack runs
on less, but limits then exceed the engine and ClickHouse's 8 GB cap is not real.

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform
cp .env.example .env      # set CLICKHOUSE_PASSWORD, MINIO_*, GRAFANA_PASSWORD, PREFECT_DB_*,
                           # LAKEKEEPER_ENCRYPTION_KEY (generate: openssl rand -base64 32)
set -a && . ./.env && set +a   # export for the verify commands below
docker compose up -d      # or: make up
```

First run builds three images — `services/capture-rust` (shared by all three `capture-*` services),
`docker/prefect` and `docker/spark` — plus image pulls; all 15 long-running services report healthy
roughly three minutes after `up`. Subsequent starts take under a minute. Measured on a clean clone,
2026-08-26, when the stack still carried the three Kotlin containers and the v2 offload as well: an
upper bound on what it takes now, not a fresh measurement.

**Verify it's flowing:**

```bash
docker compose ps
docker logs k2-capture-binance --tail 5
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance --num 1
```

| Service | URL | Notes |
|---|---|---|
| Redpanda Console | http://localhost:8080 | topics, consumer lag, schema registry |
| ClickHouse | http://localhost:8123 | HTTP interface; native on 9002 |
| Prefect | http://localhost:4200 | lake ingest + maintenance deployments |
| MinIO Console | http://localhost:9001 | S3 endpoint backing the Iceberg lake |
| Lakekeeper | http://localhost:18181 | Iceberg REST catalog (ADR-018, ADR-023) |
| Prometheus | http://localhost:9090 | targets and alert rules |
| Grafana | http://localhost:3000 | `admin` / `$GRAFANA_PASSWORD` |
| Spark Master | http://localhost:18080 | batch jobs |

## Observability

Five provisioned Grafana dashboards: pipeline overview (`k2-pipeline-overview`), ClickHouse
(`clickhouse-v2`), v2 migration tracker (`k2-v2-migration`), K2 Capture v3 (`k2-l2-capture`),
K2 Lake v3 (`k2-lake`).

![Pipeline overview dashboard](docs/images/grafana-pipeline-overview.jpg)
![Prefect deployments](docs/images/prefect-deployments.jpg)

25 alert rules in [`docker/prometheus/rules/`](./docker/prometheus/rules/): 4 ClickHouse (down, memory,
query failures, merge queue), 10 v3 capture (down, feed stale, sequence gaps, checksum failure,
produce errors/stalled, resync storm, ingress latency, book depth, precision loss), 11 v3 lake (ingest
failed, audit failed, unresolvable schema id, ingest lag, bronze commit age, compaction stale, exporter
down/stalled, scrape errors, disk high/critical). The 3 feed-handler rules and
`ClickHouseBronzeInsertRateLow` retired with the handlers (ADR-019) and are archived in
[`legacy/v2-kotlin/runbooks/`](./legacy/v2-kotlin/runbooks/); the 9 Iceberg-offload rules were deleted
outright with the offload path they watched — they described a component that no longer exists, so there
was nothing to keep. Its six runbooks are archived in
[`legacy/v2-offload/runbooks/`](./legacy/v2-offload/runbooks/).
Capture exposes Prometheus metrics on `:8082/metrics`, ClickHouse its own on `:9363`, and the lake
exporter on `lake-metrics:8000`. Details: [`docs/operations/observability.md`](./docs/operations/observability.md).

## Reliability testing

Six failure modes induced against the running stack, 2026-02-19 — all recovered without loss or corruption.

| Failure injected | Recovery | Observed |
|---|---|---|
| Redpanda restart | ~10 s | All 3 ClickHouse consumers resumed from committed offsets |
| ClickHouse restart | **~32 s** | `silver_trades` resumed; no gaps in bronze or gold |
| Capture container killed | ~30 s | Other two exchanges unaffected — isolation confirmed. Measured 2026-02-19 against the Kotlin handler this replaced; not re-measured on Rust |
| Spark killed mid-offload | next 15-min run | Watermark held; no duplicates on resume (v2 offload, since deleted — the lake equivalent is `scripts/chaos/lake-ingest-kill.sh`) |
| MinIO stopped | ~5 s | Hot tier kept ingesting; cold tier deferred cleanly |
| Network partition | ~20–30 s | Consumers resumed from last committed offset, no corruption |

Runbook: [`docs/runbooks/failure-recovery.md`](./docs/runbooks/failure-recovery.md).

**Latency caveat:** the p99 figures (Binance 191 ms, Coinbase 197 ms, Kraken 170 ms, measured 2026-02-19
as `ingestion_timestamp - exchange timestamp` on `silver_trades`) come from a cold-start sample of only
12–13 trades per exchange — directionally valid, not statistically strong. A 24 h burn-in and 5×/10× load
tests remain on the roadmap.

## Tests & CI

| Suite | Count | Run |
|---|---|---|
| Rust capture | 59 (49 lib unit + 4 in the binary + 6 replay integration) | `make test-rust` |
| Python — v3 data contracts, parity, lake offsets, wire format | 164 (41 contracts + 65 parity + 26 lake offsets + 32 wire format) | `make test-python` |
| Archived v2 Kotlin (reference only) | 20 (`TradeNormalizer` 7, `InstrumentsLoader` 13) | `make test-legacy-kotlin` |
| Legacy v1 (reference only) | ~180 unit | `cd legacy/v1 && uv run pytest` |

`make test` runs the first two. The Kotlin suite is deliberately outside it: the code is archived,
not maintained, and a green run of it proves nothing about what is deployed.

[`.github/workflows/ci.yml`](./.github/workflows/ci.yml) runs six jobs per PR:
**rust** (fmt + clippy `-D warnings` + `cargo test`), **python** (Ruff + pytest), **docker** (3-way
matrix: prefect, spark, capture), **compose** (`config -q` + every service declares limits), **docs**
(`check-docs.sh`), **security** (Trivy → SARIF). Strategy:
[`docs/development/testing.md`](./docs/development/testing.md).

## Repository layout

```
services/capture-rust/          Rust k2-capture: trades + L2 book, one binary per exchange — the capture tier
docker/clickhouse/ddl/          Bronze → Silver → Gold DDL and materialized views (auto-applied)
docker/lake/                    Spark lake ingest + maintenance + metrics, DDL, Prefect flows (v3)
docker/prometheus/rules/        25 alert rules
docker/grafana/dashboards/      5 provisioned dashboards
docker/spark/  docker/prefect/  Custom images
config/instruments.yaml         Instrument registry — single source of truth
schemas/avro/                   v3 contracts (trade, book-snapshot-l2, raw-message); normalized-trade.avsc is the frozen v2 contract
tests/                          Python tests (v3 contracts, parity, lake offsets, wire format)
docs/                           Architecture, ADRs, operations, development
legacy/v1/                      Archived v1 platform
legacy/v2-kotlin/               Archived v2 Kotlin feed handlers (ADR-019)
legacy/v2-offload/              Archived runbooks for the deleted v2 ClickHouse→Iceberg offload
docker-compose.yml              The whole stack
```

## Where v2 falls short — and the v3 roadmap

v2 is complete and frozen: three exchanges, medallion in ClickHouse, 4 v2 alert rules, 2 v2 runbooks
(six more archived with the offload under [`legacy/v2-offload/`](./legacy/v2-offload/README.md), and the
feed handler's under [`legacy/v2-kotlin/`](./legacy/v2-kotlin/README.md)).
It is a good streaming pipeline and a poor research archive. This is a **quantitative-research**
platform reading public WebSocket feeds over the open internet — it is **not a trading path**, and no number
here should be read as one. What a quant actually needs from it — completeness they can prove, aggregations
that are correct, and the ability to reproduce a figure from six months ago — v2 cannot deliver, for
structural reasons rather than missing polish. An audit of the code (not the docs) found these:

| Gap | Why it matters to a quant | v3 fix | ADR |
|---|---|---|---|
| Lake was a JDBC copy of ClickHouse, not the system of record (deleted in Phase D; archived in [`legacy/v2-offload/`](./legacy/v2-offload/README.md)) | The archive inherited the serving DB's normalisation, its 7-day TTL, and the driver's dropped `Array`/`Map` columns. Nothing was reproducible | Spark batch reads Redpanda by offset range → Iceberg `raw.messages` (verbatim, never expired) → `bronze.*`; ClickHouse becomes derived | [018](./docs/adr/ADR-018-v3-lake-first-rust-capture.md), 021, 022 |
| OHLCV open/high/low/close resolve **arbitrarily** across merges — [`01-k2-schema.sql:178`](./docker/clickhouse/ddl/01-k2-schema.sql#L178) | `SummingMergeTree` sums volume correctly and picks non-summed columns at random. A candle can carry a close that never traded last. This is a real bug | OHLCV computed on read over deduplicated trades, plus a CI regression test across two insert blocks | 026 |
| Bronze is plain `MergeTree` — [`01-k2-schema.sql:88`](./docker/clickhouse/ddl/01-k2-schema.sql#L88) | Replaying a topic duplicates every row. No key, no version, no dedup — so recovery corrupts history | `ReplacingMergeTree` hot tier with an explicit dedup contract; the lake holds truth | 025, 026 |
| No receive timestamp before parse — [`TradeNormalizer.kt:28`](./legacy/v2-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt#L28) | Exchange-clock skew and platform latency are not separable in any stored row, so no honest latency distribution exists | `recv_ts_ns` taken as the first statement on frame receipt, carried in the record body and a Kafka header | 019, 020 |
| Kraken on WS v1 with synthesised trade IDs — [`TradeNormalizer.kt:60`](./legacy/v2-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt#L60) | `KRAKEN-${ms}-${pair.hashCode()}` collides for two trades in the same millisecond — dedup and joins are unsound | Kraken WS v2: real `trade_id`, plus CRC32 book checksum verified on every update | 019, 027 |
| Coinbase `sequence_num` parsed and never checked — [`CoinbaseWebSocketClient.kt:178`](./legacy/v2-kotlin/src/main/kotlin/com/k2/feedhandler/CoinbaseWebSocketClient.kt#L178) | A dropped message is silent. Completeness is assumed, never measured | Per-exchange sequencing with gap counters, resync on gap, and audits over the lake | 019, 027 |
| Avro contract broken and unused — [`normalized-trade.avsc:60`](./schemas/avro/normalized-trade.avsc#L60), [`01-k2-schema.sql:39`](./docker/clickhouse/ddl/01-k2-schema.sql#L39) | `logicalType` sits as a sibling of `type` (Avro ignores it) and prices are strings; ClickHouse reads raw JSON instead. The registry proves nothing | One wire format: Avro + registry, fixed-point `int64` @1e-8, `BACKWARD_TRANSITIVE` compatibility | 020 |
| Trades only, no order book; raw topics keyed by exchange name — [`KafkaProducerService.kt:155`](./legacy/v2-kotlin/src/main/kotlin/com/k2/feedhandler/KafkaProducerService.kt#L155) | No L2 means no spread, no imbalance, no microprice — most of what research wants. Single-key topics also pin two exchanges to one partition | Rust `k2-capture` does trades + L2 on one connection, top-20 snapshots at 1 Hz, symbol-keyed topics | 019, 027 |

### v3 target architecture

```mermaid
flowchart LR
  EX["Exchanges · public WS<br/>Binance · Kraken · Coinbase"]
  CAP["k2-capture ×3 · Rust<br/>trades + L2 · recv_ts · seq · CRC32"]
  RP[("Redpanda<br/>Avro + registry")]
  IB[("Iceberg · Lakekeeper + MinIO<br/>system of record")]
  CH["ClickHouse hot tier<br/>derived · rebuildable · 7d TTL"]
  DD["DuckDB + PyIceberg<br/>notebooks"]
  GR["Grafana + Prometheus"]
  EX --> CAP --> RP
  RP -->|"Spark batch · offsets in snapshot"| IB
  RP --> CH
  IB -->|rebuild| CH
  IB --> DD
  CH --> GR
  CAP -.metrics.-> GR
```

Everything except the lake is derived and rebuildable. Same 16 CPU / 40 GB single host.

**Phases** ([full plan](./docs/plans/2026-08-26-v3-quant-research-platform/README.md)):

- **A — public now.** v2 shipped as-is, honestly labelled; v3 built in the open.
- **B — foundations. Landed** (tag `v3-phase-b`). Verify-first spikes, Avro contracts, Lakekeeper + MinIO, Spark image bump.
- **C — Rust capture. Landed on this branch.** `k2-capture` per exchange, trades + L2; the Kotlin
  handlers retired on a labelled per-symbol parity window and are archived in
  [`legacy/v2-kotlin/`](./legacy/v2-kotlin/README.md).
- **D — lake tier.** Raw + bronze Iceberg tables, exactly-once ingest, completeness audits.
- **E — hot tier.** ClickHouse rebuilt as derived: `ReplacingMergeTree`, OHLCV on read.
- **F — notebooks & numbers.** DuckDB research notebooks, 24 h burn-in, published measurements.

Design and rejected alternatives: [ADR-018](./docs/adr/ADR-018-v3-lake-first-rust-capture.md) (Proposed).
Phase C detail: [`services/capture-rust/README.md`](./services/capture-rust/README.md),
[`docs/architecture/capacity-model.md`](./docs/architecture/capacity-model.md),
[`docs/architecture/failure-modes.md`](./docs/architecture/failure-modes.md).
The first measured window (throughput, latency, reconnects, RSS/CPU vs the Kotlin handlers) is that
first README's "Measured" section.

**Still true today:** Phase 7 is 4 of 5 (24 h resource burn-in outstanding); there is no query API
([ADR-005](./docs/adr/ADR-005-kotlin-spring-boot-api.md), deferred); no Alertmanager routing and no
load testing above 1×; and no alert has yet been shown to fire on the fault it names — `make chaos`
is the gate that proves the capture-tier ones and it has not been run.

## Documentation

- [`docs/README.md`](./docs/README.md) — start here
- [`docs/architecture/README.md`](./docs/architecture/README.md) — system design
- [`docs/adr/`](./docs/adr/) — all 21 ADRs, including [ADR-018](./docs/adr/ADR-018-v3-lake-first-rust-capture.md) (v3, Proposed)
- [`docs/plans/2026-08-26-v3-quant-research-platform/`](./docs/plans/2026-08-26-v3-quant-research-platform/README.md) — the v3 plan
- [`docs/operations/`](./docs/operations/) — runbooks, observability, cost model
- [`docs/development/setup.md`](./docs/development/setup.md) — local development
- [`docs/MIGRATION-JOURNEY.md`](./docs/MIGRATION-JOURNEY.md) — the v1 → v2 story

## License

MIT — see [`LICENSE`](./LICENSE). © Rob Scott.

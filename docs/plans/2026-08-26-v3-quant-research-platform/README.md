# Plan: K2 v3 — quantitative-research market data platform (Rust-first, lake-first)

> Design document and phase gates. Status lives in ADRs and git, not here (see CLAUDE.md doc conventions).
> Authored 2026-08-26 with Claude Code; decisions confirmed by the maintainer.

## Context

Repo is public-ready as v2 (PR #56 merged, tag v2.0.0; PR #65 with monitoring fixes open). User wants a **best-in-class quantitative-research market data platform** to discuss with HFT/prop-firm CTOs. Audit of v2 found structural gaps: serving DB (ClickHouse) is the system of record and the lake is a lossy JDBC copy; no receive timestamps, no sequence/gap detection, Kraken on WS v1 with synthesised colliding trade IDs; bronze plain MergeTree (duplicates on replay); OHLCV SummingMergeTree with open/high/low/close resolving *arbitrarily* across blocks (a real correctness bug); Avro topic unread while ClickHouse consumes raw JSON; Hadoop catalog on a bind mount; trades only, no book.

## Decisions

**Decisions (user-confirmed):** quant research platform, NOT a trading path (public WS feeds, internet latency — say so explicitly). **Rust as much as possible**: one `k2-capture` binary per exchange doing trades + L2 book (Kotlin retired to `legacy/v2-kotlin/` after parity). Top-20 book is the canonical L2 product. Lake-first: Spark batch reads Redpanda → Iceberg raw/bronze (system of record); ClickHouse is a derived hot tier (trades, BBO, OHLCV, top-20 @1s, 7d TTL). Lakekeeper REST catalog (Rust) + MinIO. DuckDB + PyIceberg notebooks as quant query layer; no query service. Keep 16 CPU / 40 GB single-host constraint. Same repo, v3 phases, ADR-018+. **Flip public now with v2 + roadmap; build v3 in the open.**

## Execution model

**Execution model:** Fable plans/orchestrates/reviews; opus for design-heavy code (Rust capture, Spark ingest, ClickHouse DDL, ADRs), sonnet for mechanical work (compose, docs sweeps, tests scaffolding, CI). Every phase leaves the stack green; new paths run parallel to old, cut over after comparison.

## Ground truth (from exploration — cite when implementing)

- Kotlin handlers `services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/*`: no Kafka headers; only Kraken has a receive ts; Kraken WS v1 (`wss://ws.kraken.com`); Kraken `trade_id = "KRAKEN-${ms}-${hash}"` (collides); Coinbase drops `snapshot` events, never compares `sequence_num`; Kraken/Coinbase raw key = exchange name → single partition; `schemas/avro/normalized-trade.avsc` has `logicalType` as sibling of `type` (ignored), numerics as strings. ClickHouse consumes only `.raw` JSON topics.
- Exchange L2: Binance `<sym>@depth20@100ms` partial stream (lastUpdateId, bids, asks; combined `/stream?streams=`; 5 inbound msg/s, 1024 streams, 24h conn life). Kraken v2 `wss://ws.kraken.com/v2` `book` depth 25, snapshot+update, CRC32 checksum over top-10 asks then bids (precision from `instrument` channel; doc example `3310070434`); `trade` channel has real `trade_id`. Coinbase `level2`: `sequence_num` per-connection +1, snapshot/update, `new_quantity` absolute, side `bid|offer`, per-level `event_time`; subscribe `heartbeats`; JWT optional. Coinbase WS rate limits unverified.
- Rust crates: tokio 1.53, tokio-tungstenite 0.30 (rustls), rdkafka 0.39 (vendored librdkafka, no ssl feature), apache-avro 0.22, schema_registry_converter 5.0 (check avro compat — else hand-roll 5-byte framing), serde_json/simd-json, crc32fast 1.5, metrics-exporter-prometheus 0.18, reqwest 0.13, serde_yaml, proptest.
- Lake: Spark image `tabulario/spark-iceberg:3.5.0_1.4.2` lacks spark-sql-kafka-0-10, spark-avro; has iceberg-aws-bundle 1.4.2; pyiceberg 0.5.1/duckdb 0.9.1 too old. Bump to `tabulario/spark-iceberg:3.5.6_1.9.0` (verify exists; fallback `apache/spark:3.5.6` + iceberg 1.9.x jars). Lakekeeper v0.13.3 `quay.io/lakekeeper/catalog`, PG≥15 + ext uuid-ossp,pgcrypto,pg_trgm,btree_gin,btree_gist; `migrate` → `serve :8181` → `POST /management/v1/bootstrap` → warehouse create (s3 profile, `flavor: s3-compat`, `path-style-access: true`, `sts-enabled: false`, static keys). Spark conf `type=rest, uri=http://lakekeeper:8181/catalog, warehouse=k2, io-impl=S3FileIO, s3.endpoint=http://minio:9000, s3.path-style-access=true`. Kafka batch read: `format("kafka")` + `startingOffsets/endingOffsets` JSON per partition; Confluent header strip `substring(value, 6)`; `from_avro(schema_json)`; offsets stored via `.option("snapshot-property.k2.kafka-offsets", json)` (Iceberg ≥1.4). ClickHouse 24.3 cannot use REST catalog; reload via `icebergS3()` table function (verify) or `s3()` over parquet paths.
- ClickHouse 24.3: `AvroConfluent` + `format_avro_schema_registry_url` supported; ReplacingMergeTree(ver)+FINAL+`do_not_merge_across_partitions_select_final`; AggregatingMergeTree; no TimeSeries engine. `config.xml` `max_memory_usage` 10 GB > 8 GB container; `max_server_memory_usage_to_ram_ratio` reads host RAM. Kafka engine virtual columns `_partition/_offset/_headers` — verify in TO-MV on 24.3.
- Budget: compose limits 15.1 CPU / 21.9 GB (14 services); measured usage tiny (Kotlin 0.03 CPU / 134 MiB vs 0.5/512M). CPU is binding.
- Existing 18 alert rules (`docker/prometheus/rules/{clickhouse,feed-handler,iceberg-offload}-alerts.yml`), 4 dashboards, CI jobs kotlin/python/docker/security. Prefect deploy pattern `docker/offload/flows/deploy_production.py`; `docker exec k2-spark-iceberg python3 …` dispatch; `iceberg-metrics` exporter pattern (`docker/offload/metrics.py`).

## Target architecture (v3)
```mermaid
flowchart LR
  subgraph EX[Exchanges · public WS]
    B[Binance]; K[Kraken]; C[Coinbase]
  end
  subgraph CAP["Capture tier · Rust k2-capture ×3"]
    R["trades + L2 book<br/>recv_ts before parse · seq/gap · CRC32<br/>top-20 snapshots @1s"]
  end
  EX --> R
  R -->|"Avro · registry · symbol-keyed<br/>raw.* (verbatim) · trades.* · book.*"| RP[(Redpanda)]
  RP --> CH["ClickHouse hot tier (derived)<br/>hot.trades Replacing · hot.book_top20_1s<br/>bbo · ohlcv views · 7d TTL"]
  RP -->|"Spark batch 5 min<br/>offsets in snapshot"| IB[("Iceberg · Lakekeeper + MinIO<br/>raw.messages (forever) · bronze.trades · bronze.book_snapshots_l2")]
  IB -->|icebergS3 reload| CH
  IB --> DD["DuckDB / PyIceberg notebooks"]
  CH --> GR[Grafana]; CAP & CH & IB -.metrics.-> PR[Prometheus] --> GR
```

Principles: (1) capture everything verbatim, timestamp before parse; (2) lake is system of record, everything else derived and rebuildable; (3) one wire format (Avro + registry), fixed-point int64 @1e-8; (4) correctness proven, not claimed (checksums, gap counters, audits, CI regression for the OHLCV bug); (5) honest non-goals.

## Phases

| File | Phase | Scope | Exit |
|---|---|---|---|
| `000-phase-a-ship-v2-public.md` | A | Ship v2 public now (this week) | Fresh-clone quickstart green; links and grep sweep clean |
| `001-phase-b-foundations.md` | B | v3 foundations (P0/P1, ~1 week) | Registry + schemas registered; Lakekeeper works; v2 still green |
| `002-phase-c-rust-capture.md` | C | Rust capture tier (services/capture-rust/, ~2 weeks) | 3 exchanges clean 24h; limits measured and cut |
| `003-phase-d-lake-tier.md` | D | Lake tier (docker/lake/, ~1.5 weeks; replaces docker/offload/) | Two ingests, second adds 0; no dupes/gaps; audits pass |
| `004-phase-e-hot-tier.md` | E | Hot tier (ClickHouse, ~1 week) | Dashboards on hot tier; hot.ohlcv matches DuckDB-over-Iceberg |
| `005-phase-f-notebooks-numbers-docs.md` | F | Notebooks, audits, numbers, docs (~1 week) | v3.0.0 tagged; numbers table published and traceable |

## Verification (end-to-end)

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.
- Capture: `rpk topic consume market.crypto.book.kraken` shows 1 Hz snapshots with `checksum_ok=true`; `curl :8082/metrics` counters; induced failures (corrupt level → resync; `kill -STOP` → Coinbase gap+reconnect).
- Lake: snapshot summary offsets gapless; raw count == bronze count; double-run adds 0; audits pass; DuckDB notebook 01–04 run clean.
- Hot: CI schema tests; `hot.ohlcv` matches DuckDB over Iceberg; FINAL vs non-FINAL counts; rebuild `hot.*` from lake timed.
- Numbers table complete and traceable to commands; grep sweep (no "Spring Boot", no `docker-compose.v2`, no TODO in published docs).

## Risks / verify-first

Lakekeeper↔Iceberg client (S8; escape: 1.8.1/1.10.1 runtime jars); CH `AvroConfluent` arrays/virtual columns (S4; fallback flat columns); Coinbase rate limits/JWT (S5); rdkafka+distroless (S6; fallback debian-slim); tabulario image tag (S7); `icebergS3()` on 24.3 (S11; fallback `s3()` globs). Phase C is the long pole; Coinbase full-depth memory watched via `book_levels_total`. Public-in-the-open: keep `main` green; v3 work on `feat/v3-*` branches with PRs.

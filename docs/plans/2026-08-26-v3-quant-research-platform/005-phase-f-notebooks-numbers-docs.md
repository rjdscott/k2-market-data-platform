# Phase F — Notebooks, audits, numbers, docs (~1 week)

**Depends on:** Phase E
**Delivers:** Ships DuckDB/PyIceberg notebooks, a 24h burn-in numbers table, the v3 resource budget, and ADR-018..028, tagged v3.0.0.
**Exit:** Tag `v3.0.0`.

## Scope

- `notebooks/` uv project (duckdb ≥1.4 w/ iceberg ext, pyiceberg ≥0.9, polars, matplotlib, jupyterlab); `01_connect`, `02_book_at_time`, `03_asof_trades_book` (DuckDB ASOF JOIN), `04_completeness_audit`; `make notebooks`; README notes host→minio resolution.
- 24h burn-in; publish numbers table (`docs/operations/performance.md`): msg/s per stream, gaps/24h, Kraken checksum pass rate with denominator, resyncs, duplicate rate (count vs count FINAL), exchange→recv p50/p95/p99 with n, recv→CH p99, recv→lake p99, bytes/day per table + compression, CPU/RSS per service vs limits (all 17), `hot.ohlcv` 1h/1d/7d p50/p99, FINAL overhead ratio, DuckDB scan timings, time to rebuild `hot.*` from lake.
- Resource budget v3 (target ≤13.6 CPU / ≤23 GB steady): clickhouse 4/8G (cpuset 0-3), redpanda 2/2G, spark 1.5/3G, prometheus 1/2G, minio 0.5/1G, prefect-db 0.5/1G, prefect-server 0.5/1G, prefect-worker 0.5/512M, grafana 0.5/512M, console 0.25/256M, lake-metrics 0.1/128M, lakekeeper 0.25/256M, capture ×3 0.25/256M (coinbase 512M, cpuset 12-14); Kotlin removed. Update ADR-010 outcome.
- ADRs `docs/adr/ADR-018..028` (one paragraph each: decision / rejected / consequences): 018 v3 lake-first umbrella (supersedes 014); 019 Rust capture tier replaces Kotlin (supersedes 002; triggers, non-latency rationale); 020 Avro-only contracts, fixed-point int64 @1e-8, recv_ts in body; 021 raw-first archive + lineage, no delta schema; 022 exactly-once via offsets in Iceberg snapshot summary (replaces watermark table); 023 Lakekeeper REST catalog + MinIO (supersedes 013, explains 013's failure); 024 unified bronze tables (supersedes 011 for lake only); 025 ClickHouse derived hot tier (supersedes 009), reload-by-pull; 026 OHLCV on read + ReplacingMergeTree dedup contract (v2 SummingMergeTree post-mortem); 027 book snapshot model (1 Hz, Float64 arrays, BBO view) + per-exchange sequencing/resync policy; 028 non-goals & honest limits (not trading path, internet feeds, single host, no HA, Prefect/Spark retained). `docs/adr/README.md` supersession chain. README/architecture/MIGRATION-JOURNEY v3 sections (OHLCV bug post-mortem is the centrepiece); runbooks `capture-gap-recovery.md`, `clickhouse-rebuild-from-lake.md`, `lake-recovery.md`; retire iceberg-offload runbooks.
- Tag `v3.0.0`.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.
- Numbers table complete and traceable to commands; grep sweep (no "Spring Boot", no `docker-compose.v2`, no TODO in published docs).

# Phase E — Four lake layers and gold served from ClickHouse (~2 weeks)

**Depends on:** Phase D
**Decision it implements:** [ADR-026](../../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md); strategy in [data-strategy.md](../../architecture/data-strategy.md), column contracts in [schema-design.md § v3 layers](../../architecture/schema-design.md).
**Delivers:** the lake relaid as raw → bronze (per venue, vendor schema) → silver (per venue, typed) → gold (canonical + products), all rebuilt from `raw.messages`; ClickHouse rebuilt as a `gold` database with no TTL, loaded by pull from lake gold and by the Avro topics for freshness; `k2` and the `.raw` JSON topics dropped.
**Exit:** every layer rebuilt from raw with per-layer audits green over the whole archive; `gold.trades` one-row-per-trade audit green; ClickHouse `gold.ohlcv_1m` == lake `gold.ohlcv_1m` == DuckDB-over-silver for a pinned snapshot at tolerance zero; full ClickHouse rebuild from lake gold timed; dashboards on `gold`; `k2` dropped.

> Rewritten 2026-08-27 from the original "hot tier with 7-day TTL" scope after ADR-026.
> The original text is in git history; nothing from it that still applies was dropped.

## Scope

**Lake relayer** (`docker/lake/`)

- DDL `docker/lake/ddl/lake.sql` gains namespaces `bronze`, `silver`, `gold`; today's unified `bronze.trades` / `bronze.book_snapshots_l2` are dropped after the rebuild proves parity (they are derived; raw is the record). Tables:
  - `bronze.kraken_trade`, `bronze.kraken_book`, `bronze.binance_trade`, `bronze.binance_depth20`, `bronze.coinbase_market_trades`, `bronze.coinbase_level2` — the venue's field names and JSON types as sent, nested arrays as `ARRAY<STRUCT>`, `src_topic/src_partition/src_offset` + `conn_id/conn_msg_seq` lineage, `PARTITIONED BY days(recv_ts)`. Identifier = lineage.
  - `silver.trades_<venue>`, `silver.book_<venue>` — typed (`DECIMAL(28,10)`, `TIMESTAMP` UTC micros), `canonical_symbol` added beside the native symbol, `side` normalised with native kept, flags `checksum_ok`, `venue_replay`, `seq_gap`, `precision_loss`, lineage to the bronze row. Every delivery kept.
  - `gold.trades`, `gold.book_top20` — one schema, fixed-point `BIGINT` @1e-8, one row per logical trade (dedup on `(exchange, canonical_symbol, trade_id)`, winner = earliest `recv_ts_ns`, lineage to the winning silver row); `gold.dim_instrument`, `gold.dim_venue` from `config/instruments.yaml`; `gold.ohlcv_{1m,5m,1h,1d}` and `gold.bbo_1s` materialised with `src_snapshot_id` of the gold snapshot they were computed from.
- Stage per layer in `ingest.py` (or one module per layer if it passes ~400 lines): each stage reads its parent incrementally by snapshot id (the Phase D stage-2 mechanism), commits with `k2.src-snapshot-id`, and is idempotent by construction. Bronze decodes **raw JSON**, not the Avro topics — the lake never depends on the capture's parser.
- Rebuild path `make lake-rebuild LAYER=bronze|silver|gold` — drops and recomputes a layer from its parent over the whole archive; timed and recorded.
- Audits per layer in `maintenance.py`: raw offset continuity (exists); bronze and silver row parity with raw per venue and message type; gold one-row-per-trade and parity with silver minus replays; products parity with `gold.trades` for a sampled day. Fail → non-zero → `LakeAuditFailed`.
- Per-venue bronze inherits the 5 MB Coinbase `level2` rows: compaction stays at the 2g maintenance heap behind the writer lock (`docker/lake/lock.py`); measure peak RSS again with six bronze tables.

**ClickHouse gold** (`docker/clickhouse/ddl/`)

- Database `gold` built alongside `k2`; DDL split `01-tables.sql` / `02-kafka.sql` (CI applies 01 only).
- `gold.trades` `ReplacingMergeTree(recv_ts_ns)` `ORDER BY (exchange, canonical_symbol, exchange_ts, trade_id)` `PARTITION BY toYYYYMM(exchange_ts)`, **no TTL**; `gold.book_top20` likewise on `(exchange, canonical_symbol, snapshot_ts)`; `gold.ohlcv_*` and `gold.bbo_1s` as plain `MergeTree` loaded from the lake products; `gold.dim_*`.
- Two feeds, one contract: freshness from the Avro topics via `AvroConfluent` Kafka engines → MVs (the last minutes, `ReplacingMergeTree` makes the overlap idempotent); history and correctness by **pull from lake gold** (`iceberg()` table function, `docs/runbooks/clickhouse-rebuild-from-lake.md`). The lake wins on conflict — the reload is the source of truth, the topics are the head start.
- `users.xml` `quant` profile: readonly, `max_memory_usage` 3 GiB, `max_threads` 2, `do_not_merge_across_partitions_select_final=1`. Grafana and notebooks use it.
- `config.xml`: `max_memory_usage` 6 GB, `max_server_memory_usage` 6.5 GB, `max_concurrent_queries` 32, `background_pool_size` 8.
- Gauge `clickhouse_gold_bytes{table}` from `system.parts` beside `k2_lake_disk_used_ratio`; alerts `ClickHouseGoldBytesHigh` (80 % of the data volume), `ClickHouseDedupBacklog`, `ClickHouseFinalQuerySlow`, `ClickHouseGoldStale` (topic feed silent while capture is up).
- CI `clickhouse-schema` job: `clickhouse:24.3-alpine` with `01-tables.sql` and JSONEachRow fixtures; asserts OHLCV correct across two insert blocks (the v2 `SummingMergeTree` regression), `FINAL` count == distinct, book downsample keeps latest, BBO math.

**Cutover and deletion**

- Dashboards repointed to `gold`; `k2` database, its queue tables and MVs dropped; `.raw` JSON topics deleted (pre-authorised, requirements clarification Q5). `partitioning-strategy.md` gets its ClickHouse rows; `failure-modes.md` gets the ClickHouse rows (container down, Kafka-engine consumer stalled, dedup backlog, volume lost → timed rebuild, Avro schema change mid-stream) with `make chaos` targets and measured recovery times; `docker-resources.md` and ADR-010 Outcome updated if any limit moves.

**Parity, three-way now.** `gold.ohlcv_1m` in ClickHouse, `gold.ohlcv_1m` in the lake, and DuckDB computing OHLCV from `silver.trades_*` (dedup applied in the query) — for a pinned lake snapshot id, never `latest` — must agree at tolerance zero. The query triple and the snapshot id are committed as the seed of the Phase G CI parity job.

**Disk precondition — measured 2026-08-27, decided.** 79 % of 961 GB used on 2026-08-26 and ~10 GB/day predicted for four layers plus ClickHouse gold. The host has one NVMe and no second volume; Docker Desktop's `Docker.raw` holds 506 GB allocated for 66 GB used and cannot shrink (no `discard=unmap`), so ≈ 630 GB is reusable — **≈ 60 days of runway**, not six months ([capacity-model.md § 6](../../architecture/capacity-model.md), 2026-08-27 note). Maintainer decision: Phase E lands on the root disk; this host is a demonstration and the lever is stopping capture (captures stopped at the 24 h mark, 2026-08-27T22:45Z). The capacity model gets one predicted row per layer before the first rebuild and the measured column after. Revisit trigger: `df /` at 80 % or guest `/var/lib/docker` at 500 GB used.

## Verification

- `make test` (rust / python / clickhouse-schema), CI green, `docker compose up -d --build` from a clean clone → all services healthy.
- Rebuild: `make lake-rebuild` for each layer over the whole archive, timed; parity audits green; `gold.trades` count == distinct `(exchange, canonical_symbol, trade_id)`.
- Three-way OHLCV parity at a pinned snapshot: `grep -rn "snapshot-id" tests/parity/` returns a literal, `grep -rn "latest" tests/parity/` returns nothing.
- ClickHouse full rebuild from lake gold timed and written into `clickhouse-rebuild-from-lake.md` with the command; `FINAL` vs non-`FINAL` counts equal on `gold.trades`.
- `partitioning-strategy.md` has no v2 table names (`grep -nE "silver_trades|bronze_trades_|cold\." …` empty); every new `failure-modes.md` row has a measured recovery time; `make chaos` includes the ClickHouse targets.
- Capacity model: one predicted row per layer committed before the first rebuild (`git log --diff-filter=M -1 -- docs/architecture/capacity-model.md` predates the rebuild commit).

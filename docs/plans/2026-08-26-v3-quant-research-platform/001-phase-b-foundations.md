# Phase B — v3 foundations (P0/P1, ~1 week)

**Depends on:** Phase A
**Delivers:** Stands up the v3 contracts (instrument registry, Avro schemas, topics) and the Lakekeeper + MinIO catalog alongside the still-running v2 pipeline.
**Exit:** registry + schemas registered; Lakekeeper table create/read from Spark works; old v2 pipeline still green.

## Scope

**Verify-first spikes (do all before code; write answers into `docs/adr/ADR-018` appendix):**
S1 Kraken checksum test from doc example (`3310070434`) — red test first. S2 Kraken `instrument` channel gives `price_precision/qty_precision`. S3 `schema_registry_converter` 5.0 ↔ `apache-avro` 0.22 compat (else hand-roll framing). S4 ClickHouse 24.3 `AvroConfluent` + Redpanda registry + `Array(Int64)`; `_partition/_offset` in TO-MV. S5 Coinbase `level2` without JWT + rate limits. S6 rdkafka vendored build on distroless-cc. S7 `tabulario/spark-iceberg:3.5.6_1.9.0` exists & has iceberg-aws-bundle. S8 Lakekeeper 0.13.3 ↔ Iceberg 1.9 Spark client create/insert/select. S9 Lakekeeper warehouse-create body on MinIO (`sts-enabled:false`). S10 DuckDB ≥1.4 `ATTACH … TYPE ICEBERG` to Lakekeeper with `ACCESS_DELEGATION_MODE 'none'`. S11 `iceberg()` on CH 24.3 reads Iceberg v2 table. S12 MinIO tag with console still present.

**Contracts & registry:**
- `config/instruments.yaml` → per-instrument objects `{native, canonical, book_depth?}` (breaking; canonical mapping becomes data). Consumers: Rust (serde_yaml), Kotlin loader (until retired), Spark mappers.
- `schemas/avro/{trade,book-snapshot-l2,raw-message}.avsc` (replace `normalized-trade.avsc`): fixed-point `long` @1e-8 (`decimal.rs`, no f64, reject >8 dp with counter); `exchange_ts` `timestamp-micros` properly nested; `recv_ts_ns long` **in body** (+ one header `recv_ts_ns`); `seq`, `checksum_ok`, `depth`, parallel arrays `bid_px/bid_qty/ask_px/ask_qty`; `RawMessage{exchange,stream,symbol?,recv_ts_ns,conn_id,conn_msg_seq,payload bytes}`. Subject strategy TopicNameStrategy; compatibility BACKWARD_TRANSITIVE.
- Topics (`redpanda-init`): `market.crypto.raw.{ex}` (RawMessage, 48h + bytes cap), `market.crypto.trades.{ex}` (Trade, 7d), `market.crypto.book.{ex}` (BookSnapshotL2, 7d); 12 partitions each; key = canonical symbol; delete `.raw` JSON topics only after CH cutover.
- Lakekeeper + MinIO in compose: `lakekeeper-migrate` (one-shot), `lakekeeper` (0.25/256M, healthcheck), `lake-init` one-shot (`mc mb k2-lake`, bootstrap, warehouse `k2`), `docker/postgres/ddl/10-lakekeeper-db.sql` (separate DB on prefect-db + extensions), MinIO tag bump, `LAKEKEEPER_ENCRYPTION_KEY` in `.env.example`. `lake-ddl` one-shot replaces `iceberg-init`.
- Spark image: bump base, add pinned+sha256 jars (spark-sql-kafka-0-10, token-provider, kafka-clients, commons-pool2, spark-avro), drop clickhouse-jdbc & psycopg2; `docker/lake/spark_conf.py` single catalog config.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.

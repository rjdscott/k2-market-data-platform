# v3 verify-first spikes (2026-08-26)

Inputs for the twelve Phase B spikes cited in
[`../../adr/ADR-018-v3-lake-first-rust-capture.md`](../../adr/ADR-018-v3-lake-first-rust-capture.md)
Appendix A. Each spike answered one risk from ADR-018's Risks paragraph before Phase C
(the Rust rewrite) started. These are throwaway spikes, not production code — no
`Cargo.toml`/`docker-compose.yml` here backs a running K2 service, none of it is built or
tested by CI, and nothing under `services/` or `docker/` depends on it. They exist so the
Appendix A results are re-derivable rather than taken on faith.

Every `docker compose` / `docker run` command below assumes `DOCKER_CONTEXT=default` —
these spikes bind-mount from `/tmp`, and a non-default context (e.g. a remote or rootless
context) will not see the host path.

`s7-spark-iceberg-image` and `s12-minio-console-tag` have no retained inputs: both were
answered by commands run directly against public registries (`docker pull`, `curl` against
Docker Hub's tags API, `docker run` against published image tags), not against a spike
project in this repo. Their commands are the ones in ADR-018 Appendix A's S7 and S12
sections; there is nothing to re-derive them from beyond a network connection.

`kraken-book.jsonl` (240 KB, 944 lines) and `coinbase.jsonl` (8.4 MB, 677 lines) — the raw
capture from S2 and S5 — are not committed; each spike directory below keeps a truncated
sample instead (`kraken-book.sample.jsonl`: first 20 lines; `coinbase.sample.jsonl`: first
3 non-snapshot lines of `coinbase.jsonl`, since the first several lines are all update
frames from one snapshot burst).

| Spike | Dir | How to re-run | Expected decisive output |
|-------|-----|----------------|---------------------------|
| S1 — Kraken v2 book checksum | [`s1-kraken-checksum/`](s1-kraken-checksum/) | `DOCKER_CONTEXT=default docker run --rm -v $PWD:/w -w /w rust:1-bookworm cargo test` | `test result: ok. 2 passed` — checksum `3310070434` matches the doc's worked example |
| S2 — Kraken `instrument` channel | [`s2-kraken-instrument/`](s2-kraken-instrument/) | `uv run --no-project --with websockets python kraken_spike.py` | `BTC/USD {'price_precision': 1, 'qty_precision': 8, ...}` printed for all three pairs; a trade frame with an integer `trade_id` |
| S3 — `schema_registry_converter` ↔ `apache-avro` | [`s3-schema-registry-avro/`](s3-schema-registry-avro/) | `cargo tree -d` then `cargo run` | `cargo tree -d` shows no duplicate `apache-avro`; `cargo run` reaches the fake registry and fails on the HTTP call, not on a type/feature error |
| S4 — ClickHouse 24.3 `AvroConfluent` | [`s4-clickhouse-avroconfluent/`](s4-clickhouse-avroconfluent/) | `DOCKER_CONTEXT=default docker compose up -d`, then `docker compose exec -T clickhouse clickhouse-client --multiquery < s4.sql`, then `uv run --no-project --with confluent-kafka python produce.py`, then query `spike.trades` / `spike.book` | `_headers.name` contains `recv_ts_ns`; `exchange_ts` decodes as a `DateTime64(6)`; `Array(Int64)` columns land intact |
| S5 — Coinbase `level2` without JWT | [`s5-coinbase-level2/`](s5-coinbase-level2/) | `uv run --no-project --with websockets python coinbase_spike.py` | `sequence_num 0 -> 676, 677 frames, 0 gaps`; a `BTC-USD` snapshot frame with ~44k levels; no error frames |
| S6 — `rdkafka` on distroless | [`s6-rdkafka-distroless/`](s6-rdkafka-distroless/) | `DOCKER_CONTEXT=default docker build -t s6-rdkafka . && docker run --rm s6-rdkafka` | `librdkafka 2.12.1 (0x020c01ff)` printed, no `libz.so.1` load error |
| S7 — Spark + Iceberg image | *(no dir — public registry only)* | `docker pull tabulario/spark-iceberg:3.5.6_1.9.0` (expect not-found), `curl -s 'https://hub.docker.com/v2/repositories/tabulario/spark-iceberg/tags?page_size=100'`, `docker run --rm tabulario/spark-iceberg:3.5.5_1.8.1 ls /opt/spark/jars` | `3.5.6_1.9.0: not found`; the tags list has no `3.5.x_1.9.x`; the `3.5.5_1.8.1` jars list confirms Iceberg 1.8.1 |
| S8 — Lakekeeper ↔ Iceberg Spark client | [`s8-lakekeeper-spark/`](s8-lakekeeper-spark/) | `DOCKER_CONTEXT=default docker compose up -d`, bootstrap + warehouse-create as in S9, then `docker compose exec -T spark /opt/spark/bin/spark-submit /tmp/s8.py` (copy `s8.py` into the container first, e.g. `docker compose cp s8.py spark:/tmp/s8.py`) | `COUNT1: 3`, `COUNT2: 4`, and a `SNAP <id> {'k2.kafka-offsets': '{"0":42}'}` line showing the offset landed in the snapshot summary |
| S9 — Lakekeeper bootstrap and warehouse create | [`s9-lakekeeper-bootstrap/`](s9-lakekeeper-bootstrap/) | `DOCKER_CONTEXT=default docker compose up -d`, then `curl -X POST localhost:18181/management/v1/bootstrap -d '{"accept-terms-of-use":true}'`, `curl -X POST localhost:18181/management/v1/warehouse -d @warehouse.json`, `curl -s localhost:18181/health` | bootstrap `204`, warehouse-create `201` echoing the `k2` warehouse body, health `200` |
| S10 — DuckDB against the REST catalog | [`s10-duckdb-iceberg/`](s10-duckdb-iceberg/) | `DOCKER_CONTEXT=default docker compose up -d` (needs S8/S9 run first so `lake.bronze.t` exists), then `uv run --no-project --with duckdb==1.4.4 python s10.py` | variant `A`/`C` print `ATTACH OK` and `COUNT (4,)`; variant `B` (`SECRET` + `AUTHORIZATION_TYPE 'none'`) fails with `Unhandled options found: secret` |
| S11 — ClickHouse 24.3 reads Iceberg v2 | [`s11-clickhouse-iceberg-read/`](s11-clickhouse-iceberg-read/) | `DOCKER_CONTEXT=default docker compose up -d`, `uv run --no-project --with pyiceberg --with pyarrow python mk_iceberg.py` to write the table, then in `clickhouse-client`: `SELECT * FROM iceberg('http://minio:9000/lake/wh/ns/t', 'key', 'secret')` | `6 rows across 2 snapshots`; after a copy-on-write delete, `iceberg()` returns exactly 5 rows while the banned `s3('.../data/*.parquet')` glob returns 8 |
| S12 — MinIO console | *(no dir — public image tags only)* | `docker run --rm -p 9001:9001 minio/minio:RELEASE.2025-09-07T16-13-09Z server /data --console-address :9001`, then `curl -s localhost:9001 \| grep -o '<title>.*</title>'` | `<title>MinIO Console</title>` |

See ADR-018 Appendix A for the full commentary, sample output, and "carried into design"
note per spike — this table only re-derives the pass/fail, it does not restate the
reasoning.

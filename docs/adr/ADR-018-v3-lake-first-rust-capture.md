# ADR-018: A lake-first v3 with a Rust capture tier

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Architecture (umbrella)

---

## Context

v2 works: three exchanges, ~150 msg/s, p99 trade → Silver 170–197 ms, 15.1 CPU /
21.875 GB against a 16 CPU / 40 GB budget
([`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md)).
It is a good streaming demo. It is not yet a platform a quant can do research on,
and an audit of the code — not the docs — says why:

- **The lake is a lossy copy of the serving DB, not the system of record.** The
  offload reads ClickHouse over JDBC (`docker/offload/offload_generic.py:172`)
  and appends to Iceberg. Everything downstream inherits ClickHouse's
  normalisation, its 7-day Bronze TTL, and the JDBC driver's type limitations —
  `silver_trades`' `Array`/`Map` columns are already dropped at that boundary.
  Nothing durably holds what the exchange actually sent.
- **OHLCV open/high/low/close are arbitrary.** The Gold tables are
  `SummingMergeTree((volume, quote_volume, trade_count))`
  (`docker/clickhouse/ddl/01-k2-schema.sql:178`). The MV's `argMin`/`argMax`
  resolve open and close *within one insert block*; when a merge collapses two
  blocks for the same window, the non-summed columns are picked arbitrarily. A
  candle spanning a block boundary can carry a close that never traded last.
  This is a correctness bug, not a rounding difference.
- **Bronze cannot survive a replay.** All three Bronze tables are plain
  `MergeTree()` (`docker/clickhouse/ddl/01-k2-schema.sql:88`). Re-consuming a
  Redpanda topic duplicates every row; there is no key, no version, no dedup.
- **No receive timestamp.** The only wall clock on the trade path is taken
  *after* JSON parse and normalisation
  (`services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt:28`);
  Kraken alone stamps anything, and it does so at raw-publish time
  (`.../KrakenWebSocketClient.kt:229`). Exchange-clock skew and platform latency
  are therefore not separable in any stored row.
- **Kraken is on WS v1 with synthesised, colliding trade IDs.** The endpoint is
  `wss://ws.kraken.com` (`services/feed-handler-kotlin/src/main/resources/application.conf:42`)
  and the ID is `"KRAKEN-${timestampMs}-${pair.hashCode()}"`
  (`.../TradeNormalizer.kt:60`) — two trades in the same millisecond on the same
  pair are indistinguishable. v2 (`wss://ws.kraken.com/v2`) carries a real
  `trade_id` and a CRC32 book checksum.
- **Coinbase sequencing is read and thrown away.** `sequence_num` is parsed
  (`.../CoinbaseWebSocketClient.kt:178`) and copied into the payload, but never
  compared to the previous value. A dropped message is silent.
- **The Avro contract is broken and unused.** `logicalType` sits as a sibling of
  `type` in `schemas/avro/normalized-trade.avsc:60`, where Avro ignores it, and
  price/quantity are `string`. ClickHouse never reads the Avro topic anyway —
  every Kafka engine table is `kafka_format = 'JSONAsString'` over `.raw`
  (`docker/clickhouse/ddl/01-k2-schema.sql:39`).
- **Trades only, no book, and two of three raw streams are single-partition.**
  There is no L2 product at all, and the raw producer keys by exchange name
  (`.../KafkaProducerService.kt:155`), pinning Kraken and Coinbase to one
  partition each.
- **The catalog is a bind-mounted directory.** `spark.sql.catalog.k2.type =
  "hadoop"` over `/home/iceberg/warehouse`
  (`docker/offload/create_bronze_table_sql.py:10-11`) — no catalog service, no
  concurrent writers, MinIO provisioned but unused by the offload (ADR-007
  Outcome, ADR-013).

The constraint has not changed: one host, 16 CPU / 40 GB (ADR-010). The target
audience has: this is a **quantitative-research** platform on public WebSocket
feeds over the open internet. It is explicitly not a trading path, and no
decision below should be read as latency engineering.

---

## Decision

**We will rebuild K2 as a lake-first platform with a Rust capture tier —
Iceberg on MinIO behind a Lakekeeper REST catalog becomes the system of record,
ClickHouse becomes a derived and rebuildable hot tier, and a single
`k2-capture` binary per exchange replaces the Kotlin feed handlers — because
research needs a durable, verbatim, correctly-sequenced archive, and v2's system
of record is a JDBC copy of a serving database.**

This ADR is the umbrella. It fixes the shape; ADR-019 through ADR-028 fix the
details, each landing with its phase.

Concretely, v3 commits to:

1. **Lake-first.** Spark batch reads Redpanda by offset range and writes
   `raw.messages` (payload verbatim, never expired) then `bronze.*`. Exactly-once
   comes from storing the consumed offsets in the Iceberg snapshot summary
   (`snapshot-property.k2.kafka-offsets`), so the commit and the offsets move
   atomically. The PostgreSQL watermark table goes away.
2. **One Rust binary per exchange.** `k2-capture` handles trades *and* L2 top-20
   book on one connection: `recv_ts_ns` taken as the first statement on frame
   receipt (before parse), per-exchange sequencing and gap counters, Kraken v2
   CRC32 checksum verification with resync on mismatch, top-20 snapshots at 1 Hz
   as the canonical L2 product.
3. **ClickHouse as a derived hot tier.** `ReplacingMergeTree` on trades and
   book snapshots with a 7-day TTL; OHLCV computed on read over `FINAL`, not
   materialised into a `SummingMergeTree`. Losing the whole ClickHouse volume
   costs a rebuild from the lake, not data.
4. **DuckDB + PyIceberg notebooks** as the query layer. No query service —
   ADR-005 stays deferred, and now has a better answer than "not yet".
5. **16 CPU / 40 GB, single host, retained.** ADR-010's budget is a stated
   constraint of the project, not an accident of what fit.

---

## Rationale

The three things a quant asks of a market data platform are: *is it complete*,
*is it correct*, and *can I reproduce a number from six months ago*. v2 cannot
answer any of them with evidence. It has no gap detection, one confirmed
aggregation bug, and an archive whose contents depend on what a JDBC driver
could carry that day.

Lake-first answers the third directly: if the raw bytes are on disk with their
offsets, every derived table is a function of the archive and can be rebuilt and
diffed. That is also what makes the OHLCV bug *fixable* rather than *patchable* —
candles become a view over deduplicated trades, so the aggregation is defined by
a query anyone can read, and a CI test over two insert blocks catches the
regression class that produced the original bug.

Rust for capture is not about latency; at 150 msg/s over the public internet the
capture tier is not the bottleneck and never will be. It is about (a) taking the
receive timestamp before the parser touches the frame, which is a discipline the
current code cannot retrofit without the same rewrite, (b) doing trades and book
on one connection per exchange with a full-depth book in memory, and (c) three
containers at ~40 MB instead of three JVMs. One language for capture keeps the
book, checksum and sequencing logic in one place rather than duplicated per
exchange per runtime.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep v2, patch the gaps in place** (ReplacingMergeTree Bronze, OHLCV view, add recv_ts to Kotlin, Kraken v2 adapter) | Fixes the symptoms and leaves the disease: the system of record is still a lossy JDBC copy of a serving database with a 7-day TTL, so nothing is reproducible and nothing is rebuildable. Cheaper now, and every later fix pays the same tax again. |
| **Streaming writes to the lake** (Flink, or Spark Structured Streaming into Iceberg) | Costs a resident streaming runtime against a binding CPU budget — ADR-004 deleted exactly this to buy 13.5 CPU back. Batch every 5 minutes is inside the freshness a research platform needs, and the offset-in-snapshot commit gives exactly-once without a checkpoint store. |
| **Kotlin for the L2 book tier**, keeping the existing handlers for trades | Two languages in the capture tier, book/sequencing/checksum logic split across both, and three more JVMs (~3× the footprint of the Rust target) on a host where CPU is the binding constraint. The receive-timestamp-before-parse requirement forces a rewrite of the frame path either way. |
| **Full-depth order books in the lake** (every delta, reconstructable to any depth) | Storage and rebuild cost far beyond a single host, for depth beyond 20 levels that no planned research uses. `raw.messages` already holds every delta verbatim, so full depth is recoverable by replay if it is ever needed; top-20 snapshots at 1 Hz are the queryable product. |

---

## Consequences

**Easier:** reproducing any historical number (raw bytes + offsets are on disk);
rebuilding ClickHouse from scratch; proving completeness (gap counters, CRC32
pass rates, offset-continuity audits); adding an exchange (one Rust adapter, one
`handle_frame`); answering "how stale is this candle" honestly.

**Harder:** the capture tier is now Rust — a language the rest of the repo does
not use, with a build the CI has to learn. The lake gains a catalog service to
operate (Lakekeeper + its Postgres DB) where a bind mount needed nothing.
Debugging moves from "read the JSON in Redpanda Console" to "decode Avro by
schema id".

**Committed to:** a ~2-week Rust rewrite (Phase C, the long pole); a parallel-run
period where Rust capture and Kotlin handlers both produce and are compared per
symbol over 24 h before cutover; retiring `services/feed-handler-kotlin/` to
`legacy/v2-kotlin/` once parity holds; one wire format (Avro + registry,
fixed-point `int64` at 1e-8) across every topic. When this ADR is accepted,
ADR-002, ADR-007, ADR-009, ADR-013 and ADR-014 are superseded by the follow-on
ADRs below — the v2 reasoning stays on the record unedited.

**Risks:** Lakekeeper ↔ Iceberg client version compatibility, ClickHouse 24.3's
`AvroConfluent` handling of arrays and Kafka virtual columns, Coinbase's
unverified WS rate limits, and `iceberg()` on 24.3 for the rebuild path. Each
is a verify-first spike in Phase B of the plan, with a named fallback; none is
allowed to start Phase C unanswered. All four were run on 2026-08-26 — results
and the fallbacks actually taken are in [Appendix A](#appendix-a--phase-b-verify-first-spikes-2026-08-26).
Two unchanged non-risks worth stating: no
HA (still one broker, one ClickHouse, one host), and Prefect + Spark are
retained rather than replaced.

**Revisit when:** the Phase C 24-hour burn-in numbers are published in
`docs/benchmarks/` — if gaps are non-zero and unexplained, or Kraken checksum
pass rate is below 100 %, or the three capture containers exceed 1.5 CPU
combined, the capture design is wrong and this ADR gets an Outcome section
before Phase D starts.

### Follow-on ADRs

To be written when each phase lands, not before:

| ADR | Title | Supersedes |
|-----|-------|------------|
| 019 | Rust capture tier replaces Kotlin feed handlers | ADR-002 |
| 020 | Avro-only contracts: fixed-point int64 @1e-8, recv_ts in body | — |
| 021 | Raw-first archive with per-record lineage | — |
| 022 | Exactly-once ingest via Kafka offsets in the Iceberg snapshot summary | — |
| 023 | Lakekeeper REST catalog on MinIO | ADR-013 |
| 024 | Unified bronze tables in the lake | ADR-011 (lake only) |
| 025 | ClickHouse as a derived, rebuildable hot tier | ADR-009 |
| 026 | OHLCV computed on read + the ReplacingMergeTree dedup contract | — |
| 027 | L2 book snapshot model and per-exchange resync policy | — |
| 028 | Non-goals and honest limits of a single-host research platform | — |

---

## References

- [`../plans/2026-08-26-v3-quant-research-platform/`](../plans/2026-08-26-v3-quant-research-platform/README.md) — phases, exit criteria, verify-first spikes
- [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md) — the v2 numbers this argues against
- [ADR-004](ADR-004-eliminate-spark-streaming.md) — why a resident streaming runtime is not affordable here
- [ADR-010](ADR-010-resource-budget.md) — the 16 CPU / 40 GB constraint v3 keeps
- [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) — why the Hadoop catalog was chosen, and what it cost

---

## Appendix A — Phase B verify-first spikes (2026-08-26)

Every risk this ADR names as a risk was made to fail or pass against a real container before a line of v3 code was written, because a wrong assumption about a wire format or an image tag is cheap on day one and expensive in Phase C. Raw spike artefacts — the scratch Cargo projects, Python scripts and container logs — lived in a session scratchpad and were never committed; this appendix is the record of them.

| Spike | Question | Result | Consequence for the design |
|-------|----------|--------|----------------------------|
| S1 | Does a 15-line CRC32 reproduce Kraken's published book checksum? | PASS — `3310070434` matched, red first | Checksum formats from decimal strings / `i64` units, never `f64` |
| S2 | Does the Kraken v2 `instrument` channel publish price/qty precision? | PASS — precision + increments + status, one connection | Precision comes from the feed at connect, not from config |
| S3 | `schema_registry_converter` 5.0 ↔ `apache-avro` 0.22 without a duplicate crate? | PASS — single `apache-avro` in `cargo tree -d` | Use `features = ["avro","easy"]`; no hand-rolled framing |
| S4 | Does ClickHouse 24.3 decode `AvroConfluent` with arrays and virtual columns? | PASS — all four sub-questions | `_headers` exists on 24.3; `DateTime64(6)` for timestamp-micros |
| S5 | Coinbase `level2` without JWT, and what do the limits actually say? | PASS — 677 frames, 0 sequence gaps | Explicit WS max-message-size; 44k-level book sized from measurement |
| S6 | Does a vendored `rdkafka` run on distroless-cc? | PASS after a feature fix (`libz` → `libz-static`) | 42.8 MB image; `libz-static` + `zstd` are not optional |
| S7 | Does `tabulario/spark-iceberg:3.5.6_1.9.0` exist? | **FAIL** — no `3.5.x_1.9.x` tag exists at all | Fall back to `3.5.5_1.8.1`; override the image's baked `defaultCatalog` |
| S8 | Lakekeeper 0.13.3 ↔ the Iceberg Spark client: create / insert / select? | PASS — including snapshot-property commit | The offsets-in-snapshot commit of Decision §1 is proven mechanically |
| S9 | What does Lakekeeper's warehouse-create body need for MinIO? | PASS — `region` required for `s3-compat` | `10-lakekeeper-db.sql` is `CREATE DATABASE` only; probe `/health` externally |
| S10 | Does DuckDB `ATTACH … TYPE ICEBERG` reach a REST catalog? | PASS on 1.4.4 and 1.5.5, with three sharp edges | Pin `duckdb==1.4.4`; never pass `SECRET` with `AUTHORIZATION_TYPE 'none'` |
| S11 | Can ClickHouse 24.3 read an Iceberg v2 table for the rebuild path? | PASS — the function is `iceberg()`, not `icebergS3` | The `s3('…/data/*.parquet')` fallback is banned; MOR deletes untested |
| S12 | Which MinIO tag still ships the console? | **Premise wrong** — all five tested tags ship it | Take `RELEASE.2025-09-07T16-13-09Z` and stop worrying about it |

### S1 — Kraken v2 book checksum

```bash
DOCKER_CONTEXT=default docker run --rm -v $PWD:/w -w /w rust:1-bookworm cargo test
```

```
left: 0
right: 3310070434
test result: FAILED. 0 passed; 2 failed
...
test result: ok. 2 passed
```

The doc example publishes only the concatenated checksum strings, not the levels; the levels were recovered by a unique backtracking parse against `price_precision 1, qty_precision 8` — asks `(45285.2, 0.00100000)` … `(45299.5, 0.18772827)`, bids `(45283.5, 0.10000000)` … `(45276.6, 0.15445238)` — and a second test asserts the reconstruction re-emits the documented strings byte-for-byte. The algorithm is ~15 lines: format to precision, strip `.`, trim leading zeros, concatenate asks then bids, `crc32fast`.

**Carried into design:** the production checksum formats from decimal strings or `i64` fixed-point units and never from `f64` — an `f64` round-trip is lossy past 15 significant digits and would desync the book silently, with the checksum reporting success. `crc32fast 1.5.1`.

### S2 — Kraken `instrument` channel

```bash
uv run --no-project --with websockets python kraken_spike.py
```

```
BTC/USD {'price_precision': 1, 'qty_precision': 8, 'price_increment': 0.1, 'qty_increment': 1e-08, 'status': 'online'}
```

ETH/USD and SOL/USD return `price_precision 2`. In 12 s the connection carried 944 book frames, every one with both a checksum and a timestamp; the trade frame's `trade_id` is an integer (`105986129`), not a synthesised string.

**Carried into design:** one connection per exchange serves `instrument` + `book` + `trade` together, so precision is a feed-supplied fact available before the first book frame — the checksum formatter is configured from the wire, not from `instruments.yaml`. It also retires the colliding `"KRAKEN-${ts}-${hash}"` ID this ADR's Context calls out.

### S3 — `schema_registry_converter` ↔ `apache-avro`

```bash
cargo tree -d        # duplicate-dependency check
cargo run            # encode path against a fake registry
```

```
error[E0432]: unresolved import ... easy_avro
...
http call to schema registry failed ... /subjects/market.crypto.trades.kraken-value/versions/latest
```

`schema_registry_converter 5.0.0` and `apache-avro 0.22.0` coexist with a single `apache-avro` in the tree (only `heck` and `syn` duplicate). The trap: `features = ["avro"]` compiles but hides `EasyAvroEncoder`, which is gated behind `easy`; the working line is `features = ["avro","easy"]`. The registry call proves the encode path end to end, and `TopicNameStrategy` is a first-class enum variant.

**Carried into design:** the Confluent wire framing (magic byte + schema id) is library work, not ours — the hand-rolled framing fallback named in the plan is dropped.

### S4 — ClickHouse 24.3 `AvroConfluent`

```sql
-- clickhouse-client, 24.3.18.7, against Redpanda v25.3.4's registry
ENGINE = Kafka SETTINGS kafka_format = 'AvroConfluent',
  format_avro_schema_registry_url = 'http://redpanda:8081',
  kafka_thread_per_consumer = 1, kafka_num_consumers = 2;
-- MV: SELECT *, _partition, _offset, _timestamp, _headers.name, _headers.value
```

```
system.kafka_consumers: q_trades-0 assignments [0,1] | q_trades-1 assignments [2]
2025-08-24 01:46:40.123456
Enum8('buy' = 1, 'sell' = 2)
[300000000000,299900000000,...]
hdr_names: ['recv_ts_ns'] hdr_values: ['1787730321230720256']
```

All four sub-questions pass: the settings are accepted, `_headers` exists on 24.3, `timestamp-micros` decodes exactly when the column is declared `DateTime64(6)`, `Array(Int64)` survives intact, Avro enum maps to `Enum8`, and `Bool` is fine. One caveat for the runbooks: on 24.3 the column is `exceptions.text`, not `last_exception`.

**Carried into design:** Decision §3's derived hot tier consumes Avro directly — the `recv_ts_ns` header is readable alongside the body copy, and the book's parallel `bid_px/bid_qty/ask_px/ask_qty` arrays land without a transform.

### S5 — Coinbase `level2` without JWT

```bash
uv run --no-project --with websockets python coinbase_spike.py
```

```
first frame: l2_data
channels: {'l2_data': 527, 'subscriptions': 3, 'market_trades': 133, 'heartbeats': 14}
sequence_num 0 -> 676, 677 frames, 0 gaps
BTC-USD snapshot: 5195904 bytes / 43974 levels
sent 1009 (message too big) frame after reading 1027837 bytes exceeds limit of 1048576 bytes
```

No JWT was needed, contradicting the docs, which are internally inconsistent on whether `level2` requires auth. `sequence_num` is connection-wide across all channels, not per-channel — the right shape for the gap counter. Update fields are `side ∈ {bid, offer}`, `event_time`, `price_level`, `new_quantity`. Three separate subscribe messages produced no error frames; the documented limit is "WebSocket connections and unauthenticated messages are each limited to 8 per second per IP."

**Carried into design:** the Rust client sets an explicit max message size on the tungstenite connection — Python's 1 MiB default tripped on the very first snapshot, and the equivalent default would kill the capture tier on connect. The full-depth in-memory book is sized from the measured 44k levels, not a guess.

### S6 — `rdkafka` on distroless

```bash
docker build -t s6-rdkafka . && docker run --rm s6-rdkafka
```

```
/s6-rdkafka: error while loading shared libraries: libz.so.1: cannot open shared object file   (exit 127)
Unsupported value "zstd" for configuration property "compression.codec": libzstd not available at build time
librdkafka 2.12.1 (0x020c01ff)
```

`features = ["cmake-build","libz"]` builds and then dies at run: `libz` links dynamically and `distroless/cc` has none — and since `default = ["libz","tokio"]`, `cmake-build` on its own fails identically. The working line is `rdkafka = { version = "0.39", default-features = false, features = ["cmake-build","libz-static","zstd","tokio"] }`, with `cmake clang libclang-dev` in the builder stage because `zstd-sys` needs libclang.

**Carried into design:** `gcr.io/distroless/cc-debian12:nonroot` at 42.8 MB, which is the "~40 MB container" this ADR's Rationale claims. The debian-slim fallback is not needed. `rdkafka 0.39.0` / `rdkafka-sys 4.10.0+2.12.1`.

### S7 — Spark + Iceberg image

```bash
docker pull tabulario/spark-iceberg:3.5.6_1.9.0
curl -s 'https://hub.docker.com/v2/repositories/tabulario/spark-iceberg/tags?page_size=100'
docker run --rm tabulario/spark-iceberg:3.5.5_1.8.1 ls /opt/spark/jars
```

```
3.5.6_1.9.0: not found
19 tags, newest published 2025-03-11; no 3.5.x_1.9.x tag exists
iceberg-spark-runtime-3.5_2.12-1.8.1.jar  iceberg-aws-bundle-1.8.1.jar  spark-core_2.12-3.5.5.jar
spark-sql-kafka-0-10: ABSENT     spark-avro: ABSENT
java.net.UnknownHostException: rest
```

The image bakes `spark.sql.defaultCatalog demo` pointing at `spark.sql.catalog.demo.uri http://rest:8181`, so **every** session fails with `UnknownHostException: rest` unless `spark.sql.defaultCatalog` is overridden — a landmine that costs an afternoon if you meet it inside a Prefect flow instead of here.

**Carried into design:** `tabulario/spark-iceberg:3.5.5_1.8.1` (the plan's own fallback); `docker/lake/spark_conf.py` sets `spark.sql.defaultCatalog=lake` unconditionally; the Kafka and Avro jars must be added with pinned sha256 as the plan says, because the image ships neither.

### S8 — Lakekeeper ↔ Iceberg Spark client

```python
# catalog `lake`: type rest, uri http://lakekeeper:8181/catalog, warehouse k2,
# io-impl S3FileIO, s3.endpoint http://minio:9000, path-style-access true, s3.region local-01,
# spark.sql.defaultCatalog=lake, IcebergSparkSessionExtensions
CREATE NAMESPACE lake.bronze;
CREATE TABLE lake.bronze.t (id bigint, ts timestamp, px decimal(28,10))
  USING iceberg PARTITIONED BY (days(ts)) TBLPROPERTIES ('format-version'='2');
```

```
COUNT1: 3
COUNT2: 4
SNAP 5952106737753923754 {}
SNAP 4203306638488107343 {'k2.kafka-offsets': '{"0":42}'}
warehouse/k2/<table-uuid>/data/ts_day=2026-08-26/*.parquet
```

Lakekeeper v0.13.3 against the Iceberg 1.8.1 Spark client: no errors on create, insert, or select. The fourth row went in via `.writeTo("lake.bronze.t").option("snapshot-property.k2.kafka-offsets", '{"0":42}').append()` and the property is visible in `snapshots`.

**Carried into design:** this is the mechanism Decision §1 rests on — the offsets and the data commit in one atomic snapshot, so the PostgreSQL watermark table can go. Also worth knowing before writing any maintenance tooling: MinIO paths are keyed on table UUID, so a table name means nothing at the object-store level.

### S9 — Lakekeeper bootstrap and warehouse create

```bash
curl -X POST localhost:8181/management/v1/bootstrap -d '{"accept-terms-of-use":true}'   # 204
curl -X POST localhost:8181/management/v1/warehouse -d @warehouse.json                  # 201
curl -s localhost:8181/health                                                           # 200
```

```json
{"warehouse-name":"k2","project-id":"00000000-0000-0000-0000-000000000000",
 "storage-profile":{"type":"s3","bucket":"k2-lake","key-prefix":"warehouse/k2",
   "endpoint":"http://minio:9000","region":"local-01","path-style-access":true,
   "flavor":"s3-compat","sts-enabled":false},
 "storage-credential":{"type":"s3","credential-type":"access-key",
   "aws-access-key-id":"minioadmin","aws-secret-access-key":"minioadmin"}}
```

`quay.io/lakekeeper/catalog:v0.13.3` exists; v0.13.2 and v0.14.0 do not. `region` is required for `s3-compat` even against MinIO. `lakekeeper migrate` creates `uuid-ossp`, `pgcrypto`, `pg_trgm`, `btree_gin` and `btree_gist` itself.

**Carried into design:** `docker/postgres/ddl/10-lakekeeper-db.sql` is `CREATE DATABASE` and nothing else, and the compose healthcheck is an external `GET /health` because the image has no shell and no healthcheck subcommand.

### S10 — DuckDB against the REST catalog

```sql
INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;
CREATE SECRET s3sec (TYPE S3, KEY_ID 'minioadmin', SECRET 'minioadmin',
  ENDPOINT 'minio:9000', URL_STYLE 'path', USE_SSL false, REGION 'local-01');
ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT 'http://lakekeeper:8181/catalog',
  AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none');
SELECT count(*) FROM lake.bronze.t;   -- 4
```

Passes on 1.4.4 (LTS) and 1.5.5. Three sharp edges: passing a `SECRET` alongside `AUTHORIZATION_TYPE 'none'` fails with `Unhandled options found: secret`; `AUTHORIZATION_TYPE` is `ATTACH`-only; and `ACCESS_DELEGATION_MODE 'none'` must be explicit because the default is `vended_credentials`, which Lakekeeper is not configured to vend here. The parser lowercases `ENDPOINT`.

**Carried into design:** Decision §4's query layer works with no query service in front of it. Pin `duckdb==1.4.4` — 1.5 renames `ENDPOINT` to `URI`, so the notebooks break silently on an unpinned upgrade.

### S11 — ClickHouse 24.3 reads Iceberg v2

```sql
SELECT * FROM iceberg('http://minio:9000/lake/wh/ns/t', 'key', 'secret');
```

```
Code: 46 ... Maybe you meant: ['iceberg']          -- icebergS3 does not exist on 24.3
6 rows across 2 snapshots
id Int64, sym Nullable(String), px Nullable(Decimal(18, 8)), ts Nullable(DateTime64(6))
-- after a copy-on-write delete of id=3: exactly 5 rows
-- s3('.../data/*.parquet'): 8 rows, truth is 5
```

Schema is inferred, the reader follows current metadata rather than the file listing, and `ENGINE = Iceberg(...)` works as well as the table function. The table was written by `pyiceberg 0.11.1`.

**Carried into design:** the rebuild path of Decision §3 is real on 24.3. The `s3()` glob fallback is **banned in writing** — it resurrects deleted rows and double-counts rewritten files, and it fails as a silently-wrong answer rather than an error. Merge-on-read deletes were not tested and get their own spike before Phase D writes MOR.

### S12 — MinIO console

```bash
docker run --rm -p 9001:9001 minio/minio:RELEASE.2025-09-07T16-13-09Z server /data --console-address :9001
curl -s localhost:9001 | grep -o '<title>.*</title>'
```

```
<title>MinIO Console</title>
```

The premise was wrong. Five tags were tested — 2025-04-22, 05-24, 06-13, 07-23 and 09-07 — and all five serve the console on `:9001`.

**Carried into design:** take `minio/minio:RELEASE.2025-09-07T16-13-09Z` and drop the console-preservation constraint from the tag choice entirely.

### Deviations from the plan

| Deviation | Evidence |
|-----------|----------|
| Iceberg **1.8.1**, not 1.9 — the plan's own named fallback | S7: no `3.5.x_1.9.x` tag exists in `tabulario/spark-iceberg` (19 tags, newest 2025-03-11). S8 then proves Lakekeeper v0.13.3 ↔ 1.8.1 create/insert/select/snapshot-property with no errors. |
| The ClickHouse Iceberg reader is `iceberg()`, not `icebergS3()` | S11: `Code: 46 ... Maybe you meant: ['iceberg']`. |
| Phase B **keeps** `clickhouse-jdbc` and `psycopg2` in the Spark image, and keeps the `iceberg-init` one-shot, until the Phase D cutover | The plan's Spark-image bullet says drop both and let `lake-ddl` replace `iceberg-init`. Doing that in Phase B breaks the v2 offload, which reads ClickHouse over JDBC and writes watermarks to PostgreSQL (`docker/offload/offload_generic.py:172`), while Phase B's own exit criterion is "old v2 pipeline still green". Parallel, not cutover: both catalogs coexist until v3 is proven. |
| `docker/postgres/ddl/10-lakekeeper-db.sql` is `CREATE DATABASE` only | S9: `lakekeeper migrate` creates `uuid-ossp`, `pgcrypto`, `pg_trgm`, `btree_gin` and `btree_gist` itself, so the plan's "+ extensions" is redundant DDL that would have to be kept in sync with the catalog's own migrations. |
| Lakekeeper's compose healthcheck is an external `GET /health`, not an in-container command | S9: `quay.io/lakekeeper/catalog:v0.13.3` has no shell and no healthcheck subcommand, so `test:` cannot exec anything inside it. `GET /health` returns 200. |
| DuckDB pinned to `==1.4.4`, not the plan's "≥1.4" | S10: 1.5 renames the `ATTACH` option `ENDPOINT` to `URI`, so an unpinned upgrade breaks every notebook attach. |

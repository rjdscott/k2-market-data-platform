# Schema Design

Two contracts live here at once. **v3** is the wire format going forward — three Avro records under `com.k2.market.v3`, described below, and the Iceberg lake they land in. **v2** is what is still running in the hot tier: `NormalizedTrade` plus the three ClickHouse medallion layers. Its Iceberg mirrors are gone, deleted with the offload in Phase D. v2 stays documented, unedited, until Phase C retires the Kotlin handlers; nothing new should be built against it.

DDL: [`docker/clickhouse/ddl/01-k2-schema.sql`](../../docker/clickhouse/ddl/01-k2-schema.sql) (hot), [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) (lake), [`schemas/avro/`](../../schemas/avro/) (wire).

---

## v3 — the wire contract

Three records, namespace `com.k2.market.v3`, registered under TopicNameStrategy with global compatibility `BACKWARD_TRANSITIVE`. Full field-by-field documentation lives in the `doc` string of every field in the `.avsc` itself and in [`schemas/README.md`](../../schemas/README.md); this section covers only the choices that reach beyond the wire.

| Record | Topic | What it is |
|---|---|---|
| `RawMessage` | `market.crypto.v3.raw.<ex>` | The WebSocket frame verbatim as `bytes`, plus lineage. The system of record (ADR-018) |
| `Trade` | `market.crypto.v3.trades.<ex>` | One execution, normalised |
| `BookSnapshotL2` | `market.crypto.v3.book.<ex>` | Top-20 L2 snapshot at 1 Hz, parallel `bid_px/bid_qty/ask_px/ask_qty` arrays |

The `v3` path segment is not decoration. `market.crypto.trades.<ex>` is the *v2* normalized topic: its `-value` subject holds `NormalizedTrade`, and posting `trade.avsc` against it returns `{"is_compatible":false}` (checked against the running stack, 2026-08-26). Reusing the name would have failed `redpanda-init` and blocked every feed handler from starting, and the parallel-run window needed the Rust and Kotlin producers on separate topics anyway. That topic is frozen now rather than gone — its producers retired on 2026-08-26 ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) and Phase E deletes it with the `k2` database — so the prefix stays until then. Reasoning and the cutover path are in [`schemas/README.md`](../../schemas/README.md).

**Fixed-point `int64` at 1e-8 replaces decimal strings.** Every price and quantity is `round(value × 1e8)`: 45285.2 on the wire is `4528520000000`. v2 carried decimals as strings, which was the right call for a JSON-readable topic nobody consumed programmatically; v3's topics are read by ClickHouse's `AvroConfluent`, by Spark, and by Rust, and a plain `long` is the one representation all three decode identically and do exact arithmetic on. Avro's `decimal` logical type would be more self-describing and costs a `BigDecimal` reconstruction in every consumer with three different sets of precision rules — the trade-off, and the >8-decimal-place rejection counter that guards it, are argued in full in [`schemas/README.md`](../../schemas/README.md#the-fixed-point-contract).

**`recv_ts_ns` is in the record body, not just a header.** v2's only wall clock was taken after JSON parse and normalisation, so exchange-clock skew and platform latency were not separable in any stored row. In v3 it is the first statement on frame receipt, it is on all three records, and it is duplicated as a Kafka header so a lag monitor need not deserialise. The body copy is authoritative.

**`conn_id` + `conn_msg_seq` make completeness provable.** Every record carries which connection it arrived on and its monotonic frame counter within that connection. A gap in `conn_msg_seq` is loss on our side; a gap in the exchange's `seq` is loss on the wire; a `conn_id` change explains away both. `Trade.conn_msg_seq` and `BookSnapshotL2.conn_msg_seq` are foreign keys into `RawMessage`, so every derived row points at the bytes it came from.

**Three-valued `checksum_ok`.** `["null","boolean"]`, default `null`. Kraken v2 publishes a CRC32 over the book; Binance and Coinbase do not. `null` means the question is unanswerable at that venue, `true` means verified, `false` means the local book had drifted and a resync fired. Exactly **one** snapshot carries `false` per mismatch: the adapter emits the book as it actually stood, marked, *before* dropping it, and then emits nothing for that symbol until the resync lands. Clearing first would make `false` unreachable — `snapshot()` returns `None` on an empty book — and a consumer filtering `checksum_ok = false` would find nothing, ever. Defaulting the unanswerable case to `true` would claim two venues' books were verified when nothing verified them.

**`exchange_ts` is `timestamp-micros`, nested inside the type object.** v2 put `logicalType` as a *sibling* of `type`, where Avro silently ignores it — the schema parsed, registered and serialised cleanly and simply lost the type. It is nullable on `BookSnapshotL2` only, because Binance's partial-book depth stream carries no timestamp at all and inventing one would fabricate a clock reading. `tests/test_contracts.py` fails on any sibling `logicalType`.

**Where the ClickHouse and Iceberg DDL for these records go.** Nowhere yet — deliberately. The v3 hot-tier DDL (`ReplacingMergeTree` trades and book snapshots, OHLCV computed on read) is Phase E, and the Iceberg `raw.messages` / `bronze.*` tables behind Lakekeeper are Phase D. Until those land, the v3 half of the `/schema-change` checklist has three rows and not five: Avro, docs, tests. Writing DDL now would mean writing it against a catalog that does not exist yet.

---

## v2 — the wire contract, `NormalizedTrade` *(superseded)*

[`schemas/avro/normalized-trade.avsc`](../../schemas/avro/normalized-trade.avsc), namespace `com.k2.marketdata.crypto`, registered in Redpanda's built-in schema registry as `market.crypto.trades.<exchange>-value`.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Semantic version, default `1.0.0` |
| `exchange` | string | Lowercase: `binance`, `kraken`, `coinbase` |
| `symbol` | string | Exchange-native: `BTCUSDT`, `BTC/USD`, `BTC-USD` |
| `canonical_symbol` | string | Cross-exchange: `BTC/USDT`, `BTC/USD` |
| `trade_id` | string | Exchange-assigned, unique within an exchange |
| `price`, `quantity`, `quote_volume` | string | **Strings on purpose** — see below |
| `side` | enum `BUY`/`SELL` | Taker perspective |
| `timestamp` | long (millis) | When the platform normalized it |
| `exchange_timestamp` | long (millis) | When the exchange says it happened |
| `metadata` | nullable record | `sequence_number`, `is_buyer_maker`, `buyer_order_id`, `seller_order_id` — all nullable, Binance-specific in practice |

**Decimals as strings.** Avro's decimal logical type is a `bytes` field carrying an unscaled integer plus a schema-level scale — correct, but it forces every consumer to know the scale and reconstruct the value. Strings carry the exchange's own digits through untouched, and ClickHouse's `toDecimal64(s, 8)` parses them directly. It costs bytes on the wire and buys a payload you can read in Redpanda Console and diff against the exchange's REST API. Floats were never an option: `0.1 + 0.2` is not a price.

**The `metadata` record is the extension point.** Rather than widening the record every time an exchange has a field nobody else does, exchange-specific values live in one nullable sub-record with nullable members — so a new field is a backward-compatible addition, not a breaking change.

**Caveat:** this topic is produced but currently unread. The Bronze layer consumes the raw JSON topic instead ([streaming-sources.md](streaming-sources.md) explains why both exist).

---

## Bronze — as the exchange sent it, typed

Three tables, `bronze_trades_binance` / `_kraken` / `_coinbase`, with **identical column sets**. Separate tables per exchange rather than one normalized bronze is [ADR-011](../adr/ADR-011-multi-exchange-bronze-architecture.md): native symbols and sequence semantics survive to a layer you can diff against the exchange's own documentation, which is what you want at 3am when a price looks wrong.

```sql
exchange_timestamp  DateTime64(3)
sequence_number     UInt64
symbol              String          -- exchange-native
price               Decimal(18, 8)
quantity            Decimal(18, 8)
quote_volume        Decimal(18, 8)
event_time          DateTime64(3)
kafka_offset        UInt64
kafka_partition     UInt16
ingestion_timestamp DateTime DEFAULT now()
```

`ENGINE = MergeTree`, `PARTITION BY toYYYYMMDD(exchange_timestamp)`, `ORDER BY (symbol, exchange_timestamp, sequence_number)`, `TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY`.

Typing happens here, not in the handler: the normalizing materialized view does `JSONExtractString` → `toDecimal64(…, 8)` on the way in. Bronze is "raw" in provenance, not in type — a `String` price column would push parsing onto every downstream reader.

`quote_volume` is derived (`price × quantity`) rather than carried, because only some exchanges send it. `kafka_offset` and `kafka_partition` are populated where the source path provides them and zero on the Coinbase MV, which is a known rough edge, not a design choice.

---

## Silver — one table, all exchanges

`silver_trades`, fed by three MVs (`bronze_<exchange>_to_silver_mv`). `ENGINE = MergeTree`, `PARTITION BY (exchange, asset_class, toYYYYMMDD(timestamp))`, `ORDER BY (exchange, asset_class, canonical_symbol, timestamp)`, `TTL toDateTime(timestamp) + INTERVAL 30 DAY`.

| Group | Columns |
|---|---|
| Identity | `message_id UUID`, `trade_id String` |
| Classification | `exchange`, `symbol`, `canonical_symbol` (all `LowCardinality(String)`), `asset_class Enum8`, `currency LowCardinality(String)` |
| Trade | `price`, `quantity`, `quote_volume` — all `Decimal128(8)`; `side Enum8('BUY','SELL','SELL_SHORT','UNKNOWN')` |
| Time | `timestamp`, `ingestion_timestamp`, `processed_at` — all `DateTime64(6, 'UTC')` |
| Sequencing | `source_sequence Nullable(UInt64)`, `platform_sequence Nullable(UInt64)` |
| Extension | `trade_conditions Array(String)`, `vendor_data Map(String, String)` |
| Validation | `is_valid Boolean DEFAULT true`, `validation_errors Array(String)` |

Choices worth naming:

- **`LowCardinality` on every categorical.** Three exchange values and ~30 symbols across billions of rows — dictionary encoding turns those columns into single-byte lookups.
- **`Decimal128(8)` at Silver, `Decimal(18,8)` at Bronze.** Bronze holds one exchange's raw values, where 18 digits is ample. Silver is the layer aggregations run over, and `Decimal128` keeps summed quote volumes from ever needing a widening cast.
- **Microsecond timestamps at Silver, milliseconds at Bronze.** Bronze stores what the exchange sent; nobody publishes sub-millisecond trade times. Silver standardizes upward so the column type never has to change if a venue that does starts feeding in.
- **`is_valid` rather than a quarantine table.** Every Gold MV filters `WHERE is_valid = true`. A bad row stays visible and joinable instead of vanishing into a side table nobody reads.
- **`asset_class` is a constant.** Every row is `crypto`. It exists in the enum, the partition key and the sort key because retrofitting a partition dimension onto a populated table is expensive and adding one to an empty dimension is free.

---

## Gold — OHLCV

Six tables, `ohlcv_{1m,5m,15m,30m,1h,1d}`, each maintained by one MV reading `silver_trades`. `ENGINE = AggregatingMergeTree`, `PARTITION BY (exchange, toYYYYMM(window_start))`, `ORDER BY (exchange, canonical_symbol, window_start)`.

```sql
exchange, canonical_symbol, window_start,
open_time, open_price, close_time, close_price,
high_price, low_price,
volume, quote_volume, trade_count
```

Open and close use `argMin(price, timestamp)` / `argMax(price, timestamp)` — first and last *by trade time*, not by arrival order. That distinction is the whole reason a late-arriving trade cannot corrupt a candle. `high`/`low` are plain `max`/`min`; `volume` and `quote_volume` are sums; `trade_count` is `count()`.

Six independent MVs rather than a rollup chain (1m → 5m → 15m …) means each timeframe reads Silver directly. It costs six passes over the same insert instead of one, and buys the property that a bug or a rebuild in one timeframe cannot propagate into the others.

---

## Cold tier

The lake does not mirror ClickHouse. Four Iceberg tables — `lake.raw.messages`, `lake.bronze.trades`, `lake.bronze.book_snapshots_l2` and `lake.audit.checks` — are created by [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql), and they are fed from Redpanda rather than from the serving database ([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md)). `raw.messages` stores the Kafka value byte for byte, Confluent framing included; `bronze.*` is decoded from it against the exact writer schema fetched by id, in FAILFAST mode.

Unlike the v2 tables it replaced, the lake is unified across venues — `exchange` is a column, not a table name — and the columns come from the Avro contract in `schemas/avro/`, not from a ClickHouse `DESCRIBE`. `tests/test_wire_format.py` asserts that contract between `schemas/avro/*.avsc` and `lake.sql`, which is CLAUDE.md's schema-change rule made executable.

**The v2 fidelity gap is closed by construction.** The old offload dropped `trade_conditions Array(String)`, `vendor_data Map(String,String)` and `validation_errors` because the Spark ClickHouse JDBC driver could not deserialize them, so those columns lived 30 days in the hot tier and then were gone. Nothing decodes on the way into `raw.messages`, so there is no driver to lose them to: whatever the venue sent is still there, and a bronze column that turns out to be missing is a re-decode rather than a data loss ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)).

---

## Evolution

Adding a column: ClickHouse `ALTER TABLE … ADD COLUMN` is metadata-only on `MergeTree`, and Iceberg schema evolution is likewise metadata-only. On the lake side the ordering that works is the Avro schema first, then `docker/lake/ddl/lake.sql`, then the projection in `docker/lake/ingest.py` — reversed, the ingest projects a field the schema does not carry and fails the cycle. `tests/test_wire_format.py` fails if the first two drift apart, which is the point of running it in CI.

Changing a type is not free anywhere and has not been done. Removing a column from the middle of a Gold table means rebuilding its MV, since MV column position is bound at creation.

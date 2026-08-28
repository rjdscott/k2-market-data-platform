# 13. Schema Design

> **You will learn** every column in every layer and the wire contracts.
> **Read this if** anyone writing a query or a schema change.
> **Before this** chapter 07, 09.

Two contracts live here at once. **v3** is the wire format going forward, three Avro records under `com.k2.market.v3`, described below, and the Iceberg lake they land in. **v2** (`NormalizedTrade` and the ClickHouse `k2` medallion) was dropped on 2026-08-27; its DDL is archived in [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md). Nothing runs against it.

DDL: [`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql) (served `gold`), [`legacy/v2-clickhouse/01-k2-schema.sql`](../../legacy/v2-clickhouse/01-k2-schema.sql) (v2 `k2`, dropped 2026-08-27), [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) (lake), [`schemas/avro/`](../../schemas/avro/) (wire).

---

## v3: the wire contract

Three records, namespace `com.k2.market.v3`, registered under TopicNameStrategy with global compatibility `BACKWARD_TRANSITIVE`. Full field-by-field documentation lives in the `doc` string of every field in the `.avsc` itself and in [`schemas/README.md`](../../schemas/README.md); this section covers only the choices that reach beyond the wire.

| Record | Topic | What it is |
|---|---|---|
| `RawMessage` | `market.crypto.v3.raw.<ex>` | The WebSocket frame verbatim as `bytes`, plus lineage. The system of record (ADR-018) |
| `Trade` | `market.crypto.v3.trades.<ex>` | One execution, normalised |
| `BookSnapshotL2` | `market.crypto.v3.book.<ex>` | Top-20 L2 snapshot at 1 Hz, parallel `bid_px/bid_qty/ask_px/ask_qty` arrays |

The `v3` path segment is not decoration. `market.crypto.trades.<ex>` is the *v2* normalized topic: its `-value` subject holds `NormalizedTrade`, and posting `trade.avsc` against it returns `{"is_compatible":false}` (checked against the running stack, 2026-08-26). Reusing the name would have failed `redpanda-init` and blocked every feed handler from starting, and the parallel-run window needed the Rust and Kotlin producers on separate topics anyway. Its producers retired on 2026-08-26 ([ADR-019](../adr/ADR-019-rust-capture-tier.md)), and the six v2 topics were deleted at the Phase E cutover on 2026-08-27; the `v3` prefix stays because it is what the registry subjects and the lake's lineage columns carry. Reasoning and the cutover path are in [`schemas/README.md`](../../schemas/README.md).

**Fixed-point `int64` at 1e-8 replaces decimal strings.** Every price and quantity is `round(value × 1e8)`: 45285.2 on the wire is `4528520000000`. v2 carried decimals as strings, which was the right call for a JSON-readable topic nobody consumed programmatically; v3's topics are read by ClickHouse's `AvroConfluent`, by Spark, and by Rust, and a plain `long` is the one representation all three decode identically and do exact arithmetic on. Avro's `decimal` logical type would be more self-describing and costs a `BigDecimal` reconstruction in every consumer with three different sets of precision rules, the trade-off, and the >8-decimal-place rejection counter that guards it, are argued in full in [`schemas/README.md`](../../schemas/README.md#the-fixed-point-contract).

**`recv_ts_ns` is in the record body, not just a header.** v2's only wall clock was taken after JSON parse and normalisation, so exchange-clock skew and platform latency were not separable in any stored row. In v3 it is the first statement on frame receipt, it is on all three records, and it is duplicated as a Kafka header so a lag monitor need not deserialise. The body copy is authoritative.

**`conn_id` + `conn_msg_seq` make completeness provable.** Every record carries which connection it arrived on and its monotonic frame counter within that connection. A gap in `conn_msg_seq` is loss on our side; a gap in the exchange's `seq` is loss on the wire; a `conn_id` change explains away both. `Trade.conn_msg_seq` and `BookSnapshotL2.conn_msg_seq` are foreign keys into `RawMessage`, so every derived row points at the bytes it came from.

**Three-valued `checksum_ok`.** `["null","boolean"]`, default `null`. Kraken v2 publishes a CRC32 over the book; Binance and Coinbase do not. `null` means the question is unanswerable at that venue, `true` means verified, `false` means the local book had drifted and a resync fired. Exactly **one** snapshot carries `false` per mismatch: the adapter emits the book as it actually stood, marked, *before* dropping it, and then emits nothing for that symbol until the resync lands. Clearing first would make `false` unreachable, `snapshot()` returns `None` on an empty book, and a consumer filtering `checksum_ok = false` would find nothing, ever. Defaulting the unanswerable case to `true` would claim two venues' books were verified when nothing verified them.

**`exchange_ts` is `timestamp-micros`, nested inside the type object.** v2 put `logicalType` as a *sibling* of `type`, where Avro silently ignores it, the schema parsed, registered and serialised cleanly and simply lost the type. It is nullable on `BookSnapshotL2` only, because Binance's partial-book depth stream carries no timestamp at all and inventing one would fabricate a clock reading. `tests/test_contracts.py` fails on any sibling `logicalType`.

---

## v3 layers: as built

The strategy is [data-strategy.md](12-data-strategy.md); this is the contract per layer. Every
table is created by [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql), applied by the
`lake-ddl` one-shot; ClickHouse `gold` by [`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql).

| Layer | Tables | Column contract | Lineage | Identifier |
|---|---|---|---|---|
| **Raw** | `raw.messages` (exists), `raw.pcap` (designed, not built) | frame bytes verbatim, `recv_ts_ns`, `conn_id`, `conn_msg_seq`, `topic`/`partition`/`offset`, `schema_id`, headers | Kafka coordinates | `(topic, partition, offset)` |
| **Bronze** | seven, one per venue × message type plus one reference table: `bronze.kraken_trade`, `bronze.kraken_book`, `bronze.binance_trade`, `bronze.binance_depth20`, `bronze.coinbase_market_trades`, `bronze.coinbase_level2`, `bronze.kraken_instrument` | the venue's field names and JSON types as sent, strings stay strings, no renames, no unit changes; nested arrays as `ARRAY<STRUCT>` | `src_topic`, `src_partition`, `src_offset` → raw | raw lineage + position within the frame |
| **Silver** | `silver.trades_<venue>`, `silver.book_<venue>` | bronze typed: `DECIMAL(28,10)` from strings, `TIMESTAMP` UTC micros, `canonical_symbol` *added* beside the native symbol, `side` normalised to `buy`/`sell` with the native value kept, flags `checksum_ok`, `venue_replay`, `seq_gap`, `precision_loss`; every delivery kept | `src_*` → bronze row | bronze lineage (every delivery is a row) |
| **Gold** | `gold.trades`, `gold.book_top20`, `gold.dim_instrument`, `gold.dim_venue`, `gold.ohlcv_{1m,5m,1h,1d}`, `gold.bars`, `gold.bbo_1s` | one schema for all venues: `exchange`, `canonical_symbol`, fixed-point `BIGINT` @1e-8 (`price`, `qty`), `exchange_ts`, `recv_ts_ns`, `trade_id`, `side`; one row per logical trade (venue replays collapsed here) | `src_*` → silver row that won the dedup | `(exchange, canonical_symbol, trade_id)`, true here, and only here |
| **ClickHouse gold** | `gold.*` mirrored, `ReplacingMergeTree`, **no TTL** | as lake gold | `src_snapshot_id` of the lake commit it was loaded from | as lake gold |

Rules: each layer is derived only from the one above; lineage points one layer up; vendor
fields are never dropped (bronze columns, kept through silver); dedup is a gold concern;
schema evolution is add-nullable-only at every layer, with `raw.messages` frozen.

**Silver trades, landed 2026-08-27.** [`docker/lake/silver.py`](../../docker/lake/silver.py) and the three `silver.trades_<venue>` tables in [`lake.sql`](../../docker/lake/ddl/lake.sql): one typed row per trade with the frame position as the last identifier field, `canonical_symbol` resolved from `config/instruments.yaml`, `side` normalised beside `side_native`, and the flags as measurements, `venue_replay`, `seq_gap` + `missing_before` (trade ids are sequential per symbol on all three venues, so a jump is a hole), `precision_loss`. Books follow with Kraken's checksum verification ([`docker/lake/README.md`](../../docker/lake/README.md) § Silver).

**Silver books and gold book products, landed 2026-08-27.** [`docker/lake/books.py`](../../docker/lake/books.py): `silver.book_{binance,kraken,coinbase}` typed frames (Kraken `checksum_ok` verified by replaying the connection's book at the pair's precision from `bronze.kraken_instrument`), and from the same replay `gold.book_top20` (state at the end of every second, four `*_e8` arrays) and `gold.bbo_1s`; `gold.book_state` carries the replay between ticks ([`docker/lake/README.md`](../../docker/lake/README.md) § Books).

**Gold in the lake, landed 2026-08-27.** [`docker/lake/gold.py`](../../docker/lake/gold.py): `gold.trades` is silver's first deliveries projected to one schema with `price_e8`/`qty_e8` fixed point (identifier `(exchange, canonical_symbol, trade_id)`), `gold.dim_instrument`/`dim_venue` from the registry, `gold.ohlcv_{1m,5m,1h,1d}` recomputed per touched bucket and MERGEd (copy-on-write) with the source snapshot on every row. `bbo_1s` and `book_top20` in the lake follow with silver books.

**Event bars, landed 2026-08-28.** [`docker/lake/bars.py`](../../docker/lake/bars.py): `gold.bars` holds tick, volume and dollar bars at the one canonical threshold per symbol in [`config/bars.yaml`](../../config/bars.yaml), computed from every trade in `gold.trades` (the book is never an input). A bar is a *cumulative bucket*, trade → bar `k` of its UTC day when `k·T ≤ (day's total before it) < (k+1)·T` in the candles' `(exchange_ts, recv_ts_ns, trade_seq)` order, so it is one window expression in Spark, DuckDB and a twenty-line Python reference; `scripts/parity-bars.sh` holds the three to tolerance zero. Columns are exact fixed point in and out (`open_e8` … `volume_e8`, `quote_volume_e8` = the 1e-16 notional floor-divided to 1e-8), with no `DECIMAL` division on the path, because DuckDB turns a decimal quotient into a `DOUBLE`. A touched `(exchange, symbol, day)` is deleted and re-appended, not `MERGE`d: a late trade moves every later boundary in its day. Any other threshold is [`notebooks/k2lake.py`](../../notebooks/k2lake.py) `bars()` over `gold.trades`, never a second table. Served from ClickHouse `gold.bars` by the same pull as the candles.

**Gold in ClickHouse, landed 2026-08-27.** [`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql) is the served contract: `gold.trades` (`ReplacingMergeTree`, `ORDER BY (exchange, canonical_symbol, exchange_ts, trade_id)`, version = inverted `recv_ts_ns` so the earliest delivery wins, no TTL), `gold.book_top20` (one row per venue-symbol-second, later sample wins), and the on-read views `gold.ohlcv_live(bucket)` and `gold.bbo_live`. Prices and quantities are the wire's `Int64` at 1e-8 with exact `Decimal(38,10)` aliases. Fed by AvroConfluent Kafka engines for freshness (`20-gold-kafka.sql`) and by a pull from lake gold through `iceberg()` ([runbook](../runbooks/clickhouse-rebuild-from-lake.md)); CI asserts the semantics on every PR (`make test-clickhouse`, [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)).

**Why identifier uniqueness lives in gold and nowhere below.** The v3-D unified bronze
declared `(exchange, symbol, trade_id)` unique and the data disproved it twice in a day
(reconnect replay, then in-connection re-send, ADR-024 amendment). Below gold, the only
honest identifier is lineage; gold is where "one logical trade" is *made* true, and the
audit that proves it runs there.

---

## v2 *(dropped 2026-08-27)*

The v2 `NormalizedTrade` contract ([`schemas/avro/normalized-trade.avsc`](../../schemas/avro/normalized-trade.avsc), frozen) and the ClickHouse `k2` medallion it fed are archived with their DDL and column notes in [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md); the reasons they were replaced are [ADR-020](../adr/ADR-020-avro-fixed-point-contracts.md) and [ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md).

---

## Evolution

Adding a column: ClickHouse `ALTER TABLE … ADD COLUMN` is metadata-only on `MergeTree`, and Iceberg schema evolution is likewise metadata-only. On the lake side the ordering that works is the Avro schema first, then `docker/lake/ddl/lake.sql`, then the projection in `docker/lake/ingest.py`, reversed, the ingest projects a field the schema does not carry and fails the cycle. `tests/test_wire_format.py` fails if the first two drift apart, which is the point of running it in CI.

Changing a type is not free anywhere and has not been done. Schema changes move together, Avro, lake DDL, ClickHouse DDL, the layer projections, docs and `tests/test_wire_format.py` in one PR (`/schema-change`); a half-migrated contract fails at the ingest boundary, not at build time.

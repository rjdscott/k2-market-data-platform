-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 v3 lake — the Iceberg tables (raw, unified bronze, six bronze-per-venue, silver trades per venue, gold, audit), applied by docker/lake/apply_ddl.py
-- (the `lake-ddl` one-shot compose service). Idempotent: every statement is
-- CREATE ... IF NOT EXISTS or an ALTER that converges to a fixed value, so a
-- re-run against a live warehouse is a no-op.
--
-- Namespaces raw / bronze / audit are created by docker/lake/init-lake.sh over
-- the Lakekeeper REST API before this file runs.
--
-- ── Two properties that are load-bearing, stated once here ──────────────────
--
-- COPY-ON-WRITE, APPEND-ONLY. Every table declares copy-on-write for delete,
-- update and merge. Nothing in the ingest path deletes or updates a row: stage
-- 1 appends Kafka records, stage 2 appends decoded records, maintenance rewrites
-- files without changing rows. Merge-on-read would buy nothing and would put
-- positional delete files in front of DuckDB 1.4.4 and ClickHouse 24.3's
-- `iceberg()` reader, neither of which has been shown to handle them on this
-- stack. If deletes are ever needed, the spike comes first
-- (docs/research/2026-08-26-v3-spikes/s13-mor-delete/), not the property flip.
--
-- METRICS DEFAULT `none`. Iceberg's default is truncate(16) on every column,
-- which for `raw.messages.payload` means per-file bounds over frames up to
-- 5.2 MB (Coinbase level2 subscribe snapshot, spike S5) that no query will ever
-- filter on. Each table turns metrics back on only for the columns its readers
-- actually prune by; those are named in the comment above each table.
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ───────────────────────────────────────────────────────────────────────────
-- raw.messages — the system of record. Never expired (requirements
-- clarification Q8: keep forever, alert at 80% disk, no lake TTL).
--
-- `payload` is the Kafka value byte for byte, Confluent 5-byte framing and all:
-- magic 0x00, then a 4-byte big-endian schema id, then the Avro body. Storing
-- the framed bytes rather than the stripped body means a row can be replayed
-- back onto a topic unchanged, and it keeps `schema_id` a derived convenience
-- rather than the only copy of it.
--
-- PARTITIONED BY days(kafka_ts), topic — time first so a backfill or a replay
-- touches whole days, topic second so a per-venue read prunes to a third of the
-- files. Not hours(): at the predicted 6.5 GB/day across 9 topics, hourly
-- partitions would put ~200 partitions/day on one host's metadata for no
-- pruning gain a day-plus-sort-order does not already give.
--
-- Metrics on offset, kafka_ts and partition: those are what the continuity
-- audit and the incremental reader filter by. Not on payload, key or headers.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.raw.messages (
    topic       STRING  NOT NULL COMMENT 'Kafka topic, e.g. market.crypto.v3.raw.kraken',
    partition   INT     NOT NULL COMMENT 'Kafka partition the record was read from',
    offset      BIGINT  NOT NULL COMMENT 'Kafka offset within (topic, partition). Gapless by construction; the continuity audit proves it',
    kafka_ts    TIMESTAMP NOT NULL COMMENT 'Broker-assigned record timestamp (CreateTime from the producer)',
    ingest_ts   TIMESTAMP NOT NULL COMMENT 'When this ingest run started. Not per-row: it identifies the batch, and a per-row clock read would cost a call per record for no answerable question',
    key         BINARY           COMMENT 'Kafka message key, verbatim. The canonical symbol as UTF-8 for trades/book; null where capture set none',
    schema_id   INT              COMMENT 'Confluent schema id, decoded from payload bytes 2-5. NULL means the payload is not Confluent-framed: the bytes are still archived verbatim, stage 2 skips the row, and the audit counts it. Nullable so one foreign record cannot block every following ingest on the same offset',
    payload     BINARY  NOT NULL COMMENT 'The Kafka value byte for byte, INCLUDING the 5-byte Confluent header',
    headers     MAP<STRING, BINARY> COMMENT 'Kafka record headers; carries recv_ts_ns as ASCII digits (services/capture-rust/src/sink.rs)'
)
USING iceberg
PARTITIONED BY (days(kafka_ts), topic)
TBLPROPERTIES (
    'format-version'                            = '2',
    'write.format.default'                      = 'parquet',
    'write.parquet.compression-codec'           = 'zstd',
    'write.distribution-mode'                   = 'hash',
    'write.target-file-size-bytes'              = '268435456',
    'write.delete.mode'                         = 'copy-on-write',
    'write.update.mode'                         = 'copy-on-write',
    'write.merge.mode'                          = 'copy-on-write',
    'write.metadata.metrics.default'            = 'none',
    'write.metadata.metrics.column.offset'      = 'full',
    'write.metadata.metrics.column.kafka_ts'    = 'full',
    'write.metadata.metrics.column.partition'   = 'full',
    'commit.retry.num-retries'                  = '10',
    'comment'                                   = 'v3 verbatim archive. Never expired: no TTL, no row ever deleted. ADR-018, requirements clarification Q8.'
);

-- Local sort by (topic, partition, offset): makes the per-file offset bounds
-- tight, so the continuity audit and any offset-range read prune to a handful
-- of files instead of scanning the day. DISTRIBUTED BY PARTITION keeps
-- write.distribution-mode = hash — a bare `WRITE ORDERED BY` would silently
-- switch it to `range`, which costs a sampling job on every write.
ALTER TABLE lake.raw.messages
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY topic, partition, offset;

-- ───────────────────────────────────────────────────────────────────────────
-- bronze.trades — one unified table across all three venues, decoded from
-- market.crypto.v3.trades.<exchange> (schemas/avro/trade.avsc).
--
-- DECIMAL(28,10), not the wire int64. The wire carries fixed point at 1e-8
-- because Avro has no cheap decimal and int64 is exact; the lake carries the
-- decimal because every reader downstream (DuckDB, ClickHouse `iceberg()`,
-- pandas) would otherwise re-derive it and one of them would get the scale
-- wrong. 28-10 covers the whole int64 range at 1e-8: max price 9.22e10 needs
-- 11 integer digits, and 28 - 10 = 18 leaves room to spare.
--
-- Partitioned by exchange, then days(exchange_ts). Exchange first because it is
-- the field every cross-venue query filters on and it has exactly three values;
-- symbol is deliberately NOT a partition field. config/instruments.yaml holds 34
-- (exchange, symbol) pairs, so an exchange x symbol x day spec is 34 partitions
-- a day over 0.156 GB/day — 4.6 MB each on average against a 128 MB target, and
-- BTC dwarfs the tail so the real distribution is one huge partition and 33
-- tiny ones. The sort order carries symbol pruning instead, at no file-count
-- cost.
--
-- Identifier fields include conn_id, and that is a measurement rather than a
-- preference. Over 287,184 trades captured on 2026-08-26 (30 min, all three
-- venues), (exchange, symbol, trade_id) had 956 duplicated keys — every one of
-- them Coinbase, every one a pair of rows with identical price, qty, side and
-- exchange_ts under two different conn_ids. Coinbase replays recent
-- market_trades on resubscribe, so a reconnect genuinely delivers the same
-- trade twice and the archive genuinely holds both frames. Declaring
-- (exchange, symbol, trade_id) unique would be declaring something the data
-- disproves; adding conn_id makes the claim true and leaves the venue replay
-- visible instead of hidden.
--
-- conn_id was not enough either. Measured 2026-08-26 over the first day of the
-- archive: 5,034 Coinbase (exchange, symbol, trade_id, conn_id) keys held two
-- rows each, from two distinct market_trades frames ~15 s apart on ONE
-- connection — the venue re-sends recent trades inside a live subscription as
-- well as after a reconnect. So the identifier is the source lineage: an
-- archived record decodes into exactly one row per trade id it carries, and
-- that is the only uniqueness the ingest can promise. Anything the venue
-- repeats, the archive repeats.
--
-- The logical trade is still (exchange, symbol, trade_id) — that is what a
-- research query deduplicates on, and docker/lake/maintenance.py reports the
-- replay count on every run (split across / within connections) so the rate
-- stays a number.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.trades (
    exchange         STRING  NOT NULL COMMENT 'binance | kraken | coinbase',
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol, byte for byte (BTCUSDT, XBT/USD, BTC-USD)',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE uppercase; the Kafka message key',
    trade_id         STRING  NOT NULL COMMENT 'The trade id the venue itself assigned, stringified without reformatting',
    price            DECIMAL(28,10) NOT NULL COMMENT 'Quote currency. Wire int64 at 1e-8 divided by 1e8 — exact, no rounding',
    qty              DECIMAL(28,10) NOT NULL COMMENT 'Base currency. Same conversion as price',
    side             STRING  NOT NULL COMMENT 'Taker side: buy | sell. The Avro enum decoded to its symbol',
    exchange_ts      TIMESTAMP NOT NULL COMMENT 'Venue clock, microseconds. Subject to venue skew — never a latency measurement on its own',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds. Kept as BIGINT: a TIMESTAMP is microseconds and would truncate the only clock we control',
    seq              BIGINT  NOT NULL COMMENT 'Venue sequence number; 0 means the venue does not sequence this stream',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode. seq continuity is only meaningful within one conn_id',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id. Foreign key into raw.messages via the same pair',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this was decoded from',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique in raw.messages',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the stage-2 run that wrote this row started'
)
USING iceberg
PARTITIONED BY (exchange, days(exchange_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.exchange_ts'     = 'full',
    'write.metadata.metrics.column.seq'             = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Unified normalised trades, decoded from raw.messages. Rebuildable: drop and replay.'
);

ALTER TABLE lake.bronze.trades SET IDENTIFIER FIELDS exchange, symbol, trade_id, src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.trades
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, exchange_ts;

-- ───────────────────────────────────────────────────────────────────────────
-- bronze.book_snapshots_l2 — 1 Hz top-N L2 snapshots, decoded from
-- market.crypto.v3.book.<exchange> (schemas/avro/book-snapshot-l2.avsc).
--
-- The wire format carries four parallel arrays (bid_px, bid_qty, ask_px,
-- ask_qty) because ClickHouse 24.3's AvroConfluent decodes array<long> straight
-- to Array(Int64). The lake stores two arrays of struct<px, qty> instead: in
-- Parquet the parallel form makes "the 3rd bid level" a two-column zip that
-- every reader has to write itself, and a length mismatch between px and qty is
-- representable. A struct makes the pairing the storage format's problem.
--
-- Partitioned by exchange, days(snapshot_ts). snapshot_ts, not exchange_ts:
-- Binance's partial-depth stream carries no venue timestamp at all, so
-- exchange_ts is null for a third of the rows and cannot carry a partition.
-- snapshot_ts is the sampler's own clock and is always present.
--
-- Metrics only on snapshot_ts, symbol and seq (the plan's "none except
-- event_ts/symbol/seq"): the arrays are the bulk of the row and nothing prunes
-- on their bounds.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.book_snapshots_l2 (
    exchange         STRING  NOT NULL COMMENT 'binance | kraken | coinbase',
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE uppercase; the Kafka message key',
    depth            INT     NOT NULL COMMENT 'Levels actually present per side. Below the requested 20 means a thin book, not dropped levels',
    seq              BIGINT  NOT NULL COMMENT 'Venue sequence of the last update folded in; 0 where the venue does not sequence this stream',
    checksum_ok      BOOLEAN          COMMENT 'Kraken only. null = the venue publishes no checksum, so the question is unanswerable — never collapsed to true',
    bids             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL COMMENT 'Best first (descending px). Zipped from the parallel bid_px, bid_qty arrays on the wire',
    asks             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL COMMENT 'Best first (ascending px). Zipped from ask_px/ask_qty',
    exchange_ts      TIMESTAMP        COMMENT 'Venue clock. NULL for binance: its partial-depth stream carries only an update id',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock of the last update folded in, nanoseconds',
    snapshot_ts_ns   BIGINT  NOT NULL COMMENT 'When the 1 Hz sampler fired, nanoseconds. The authoritative sampling clock',
    snapshot_ts      TIMESTAMP NOT NULL COMMENT 'snapshot_ts_ns truncated to microseconds, so the table can be partitioned and range-scanned on it. snapshot_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id at the moment of sampling',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this was decoded from',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the stage-2 run that wrote this row started'
)
USING iceberg
PARTITIONED BY (exchange, days(snapshot_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.snapshot_ts'     = 'full',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.seq'             = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Queryable L2 product. Deltas are not stored here — they stay verbatim in raw.messages and are recoverable by replay.'
);

-- snapshot_ts_ns, not conn_msg_seq, and again the data settled it. conn_msg_seq
-- records which frame the book last incorporated, so a quiet book gives two
-- consecutive 1 Hz samples the same value: measured 484 duplicated
-- (exchange, symbol, conn_id, conn_msg_seq) keys over 47,331 snapshots on
-- 2026-08-26, e.g. binance ATOMUSDT conn_msg_seq 81456 sampled at
-- ...156094883844 and ...157094267621, one second apart, same recv_ts_ns.
-- Two snapshots of an unchanged book is correct behaviour, not a duplicate.
-- The same 47,331 rows have zero duplicates on the sampler clock below.
--
-- seq is no help either: Kraken writes 0 and Coinbase's sequence_num is
-- connection-wide.
ALTER TABLE lake.bronze.book_snapshots_l2
    SET IDENTIFIER FIELDS exchange, symbol, conn_id, snapshot_ts_ns;

ALTER TABLE lake.bronze.book_snapshots_l2
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, snapshot_ts;


-- ═══════════════════════════════════════════════════════════════════════════
-- Bronze per venue — Phase E, ADR-026. Six tables, one per (venue, message
-- type), decoded from the raw.<venue> JSON frames by docker/lake/bronze.py.
--
-- The rule for every column that is not lineage: the venue's field name, the
-- venue's JSON type, the venue's nesting. Strings stay strings (Binance and
-- Coinbase quote prices as strings), JSON numbers become DECIMAL(28,10) (the
-- lossless reading of a number literal), objects become STRUCT, arrays become
-- ARRAY. No renames, no unit conversion, no canonical symbol — those are
-- silver. One row per frame, so (src_topic, src_partition, src_offset) is one
-- to one with raw.messages and the identifier.
--
-- PARTITIONED BY days(recv_ts): the one clock every frame carries. Sorted
-- (symbol, recv_ts_ns) within a partition so a per-instrument scan prunes on
-- the symbol column's file bounds; metrics on symbol, recv_ts and src_offset
-- only — the nested payload columns are the bulk of the row and nothing prunes
-- on their bounds.
--
-- Case matters: bronze.binance_trade.data has both `e` and `E`. The Spark
-- session sets spark.sql.caseSensitive=true (docker/lake/spark_conf.py) and
-- this file must be applied under it.
--
-- The unified bronze.trades / bronze.book_snapshots_l2 above stay until the
-- rebuild from raw proves parity against them (plan 004); then they go.
-- ═══════════════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.binance_trade
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.binance_trade (
    stream           STRING           COMMENT 'Combined-stream name as sent: btcusdt@trade',
    data             STRUCT<e: STRING, E: BIGINT, s: STRING, t: BIGINT, p: STRING, q: STRING, T: BIGINT, m: BOOLEAN, M: BOOLEAN>
                                      COMMENT 'The venue payload verbatim: e event type, E event time ms, s symbol, t trade id, p price, q qty (both strings as sent), T trade time ms, m buyer-is-maker, M ignore flag',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Binance combined-stream `<sym>@trade` frames, one row per frame, field names as sent'
);

ALTER TABLE lake.bronze.binance_trade SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.binance_trade
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.binance_depth20
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.binance_depth20 (
    stream           STRING           COMMENT 'Combined-stream name as sent: btcusdt@depth20@100ms',
    data             STRUCT<lastUpdateId: BIGINT, bids: ARRAY<ARRAY<STRING>>, asks: ARRAY<ARRAY<STRING>>>
                                      COMMENT 'lastUpdateId, then bids/asks as [["price","qty"], ...] exactly as sent — the pairing is positional on the wire and stays positional here',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Binance `<sym>@depth20@100ms` partial-book frames, one row per frame, levels as the two-string arrays the venue sends'
);

ALTER TABLE lake.bronze.binance_depth20 SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.binance_depth20
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.kraken_trade
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.kraken_trade (
    channel          STRING           COMMENT 'trade',
    type             STRING           COMMENT 'snapshot | update',
    data             ARRAY<STRUCT<symbol: STRING, side: STRING, price: DECIMAL(28,10), qty: DECIMAL(28,10), ord_type: STRING, trade_id: BIGINT, timestamp: STRING>>
                                      COMMENT 'The trades in the frame, in the order sent. price/qty are JSON numbers on the wire; DECIMAL(28,10) stores their digits exactly — a DOUBLE would re-round them',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Kraken v2 `trade` channel frames (snapshot and update), one row per frame; N trades in data[]'
);

ALTER TABLE lake.bronze.kraken_trade SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.kraken_trade
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.kraken_book
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.kraken_book (
    channel          STRING           COMMENT 'book',
    type             STRING           COMMENT 'snapshot | update',
    data             ARRAY<STRUCT<symbol: STRING, bids: ARRAY<STRUCT<price: DECIMAL(28,10), qty: DECIMAL(28,10)>>, asks: ARRAY<STRUCT<price: DECIMAL(28,10), qty: DECIMAL(28,10)>>, checksum: BIGINT, timestamp: STRING>>
                                      COMMENT 'One element per frame in practice. bids/asks are the levels the frame carries (a snapshot: 25 per side; an update: the changed ones). checksum is the venue CRC32 as sent — silver verifies it, bronze records it',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Kraken v2 `book` channel frames (snapshot and update), one row per frame; checksum as sent, never verified here'
);

ALTER TABLE lake.bronze.kraken_book SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.kraken_book
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.coinbase_market_trades
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.coinbase_market_trades (
    channel          STRING           COMMENT 'market_trades',
    timestamp        STRING           COMMENT 'Venue envelope time, RFC 3339 as sent',
    sequence_num     BIGINT           COMMENT 'Connection-wide sequence across every channel on the connection, so +1 continuity only holds when heartbeats and level2 are counted too',
    events           ARRAY<STRUCT<type: STRING, trades: ARRAY<STRUCT<product_id: STRING, trade_id: STRING, price: STRING, size: STRING, time: STRING, side: STRING>>>>
                                      COMMENT 'events[].type is snapshot | update; the trades nested under each, strings as sent (side is BUY | SELL uppercase here)',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Coinbase Advanced Trade `market_trades` channel frames (snapshot and update), one row per frame; N trades in events[].trades[]'
);

ALTER TABLE lake.bronze.coinbase_market_trades SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.coinbase_market_trades
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;
-- ───────────────────────────────────────────────────────────────────────────
-- bronze.coinbase_level2
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.coinbase_level2 (
    channel          STRING           COMMENT 'l2_data (the data channel name for a level2 subscription)',
    timestamp        STRING           COMMENT 'Venue envelope time, RFC 3339 as sent',
    sequence_num     BIGINT           COMMENT 'Connection-wide sequence; see coinbase_market_trades',
    events           ARRAY<STRUCT<type: STRING, product_id: STRING, updates: ARRAY<STRUCT<side: STRING, event_time: STRING, price_level: STRING, new_quantity: STRING>>>>
                                      COMMENT 'events[].type is snapshot | update; updates[] carry absolute new_quantity per price_level, side is bid | offer, all strings as sent',
    symbol           STRING           COMMENT 'RawMessage.symbol: the instrument the capture attributed the frame to, venue spelling. NULL = the frame concerns no single instrument',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock, nanoseconds, taken before parse. The only clock guaranteed present on every frame',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns truncated to microseconds — the partition and range-scan column. recv_ts_ns stays authoritative',
    conn_id          STRING  NOT NULL COMMENT 'WebSocket connection episode',
    conn_msg_seq     BIGINT  NOT NULL COMMENT 'K2 frame counter on conn_id; (conn_id, conn_msg_seq) is the archive primary key',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the raw.messages row this frame is',
    src_partition    INT     NOT NULL COMMENT 'Lineage',
    src_offset       BIGINT  NOT NULL COMMENT 'Lineage. (src_topic, src_partition, src_offset) is unique here AND in raw.messages: one row per archived frame',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the decode run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(recv_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.symbol'          = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Coinbase Advanced Trade `level2` channel frames (`l2_data`, snapshot and update), one row per frame; a snapshot frame is the whole book and runs to 5 MB'
);

ALTER TABLE lake.bronze.coinbase_level2 SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.coinbase_level2
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;


-- ═══════════════════════════════════════════════════════════════════════════
-- Silver per venue — Phase E, ADR-026. Typed, annotated, every delivery kept.
--
-- One table per (venue, message type), derived only from its bronze table by
-- docker/lake/silver.py. Types are ours (DECIMAL(28,10), TIMESTAMP UTC micros),
-- names are canonical for the shared columns and the venue's own for the rest
-- (kept beside, never instead: `side_native`, `buyer_is_maker`, `ord_type`).
-- canonical_symbol comes from config/instruments.yaml, the registry the capture
-- subscribes from — not from the Avro topics, so silver stays a function of
-- raw.messages alone.
--
-- Flags, each a measurement rather than a judgement:
--   venue_replay    the same trade was delivered before (Coinbase re-sends on
--                   subscribe and inside a live subscription; lake.sql above)
--   seq_gap         trade ids the archive never saw, per symbol — the venues
--                   number trades sequentially, so a hole is a hole
--   precision_loss  more than 8 decimals: the wire's 1e-8 cannot carry it
-- Rows are never dropped for any of them; gold decides what to keep.
--
-- Partitioned by days(exchange_ts) — the research axis — and sorted
-- (canonical_symbol, exchange_ts) inside a partition. Identifier = bronze
-- lineage + position within the frame, so one archived frame that carries N
-- trades yields exactly N rows and nothing else can.
--
-- Books (silver.book_<venue>) follow in the next step; Kraken's checksum
-- verification needs the `instrument` precision frames beside the book frames.
-- ═══════════════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────────────
-- silver.trades_binance
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.trades_binance (
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol, as sent (the bronze value)',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE from config/instruments.yaml — silver derives it from the registry, never from the wire',
    trade_id         STRING  NOT NULL COMMENT 'The trade id the venue assigned, as sent',
    trade_seq        BIGINT  NOT NULL COMMENT 'trade_id as a number. All three venues number trades sequentially per symbol (measured 2026-08-27: Binance 8,667,843 consecutive ids vs 144 jumps). seq_gap is computed on this',
    price            DECIMAL(28,10) NOT NULL COMMENT 'Typed from the bronze value, exact',
    qty              DECIMAL(28,10) NOT NULL COMMENT 'Base quantity, exact',
    side             STRING  NOT NULL COMMENT 'Taker side normalised: buy | sell',
    side_native      STRING  NOT NULL COMMENT 'The side as the venue expresses it, as sent (Binance: the buyer-is-maker flag as true/false)',
    exchange_ts      TIMESTAMP NOT NULL COMMENT 'Venue trade time, typed UTC microseconds (Coinbase nanoseconds truncate to micros)',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock of the frame, nanoseconds',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns to microseconds',
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    venue_replay     BOOLEAN NOT NULL COMMENT 'true = an earlier delivery of the same (symbol, trade_id) exists in the archive (lower recv_ts_ns, or same and earlier lineage). Every delivery is kept. gold keeps the first',
    seq_gap          BOOLEAN          COMMENT 'true = the previous trade_seq for this symbol is more than 1 below this one: trades the archive never received. NULL = no previous trade within the lookback (unknowable)',
    missing_before   BIGINT           COMMENT 'How many trade ids are absent between the previous trade and this one. 0 when seq_gap is false, NULL with it',
    precision_loss   BOOLEAN NOT NULL COMMENT 'true = price or qty carries more than 8 decimal places, i.e. the 1e-8 fixed point of the wire and of gold cannot hold it exactly',
    event_type       STRING           COMMENT 'Binance `e`, as sent',
    event_time       TIMESTAMP        COMMENT 'Binance `E`, typed from epoch milliseconds',
    buyer_is_maker   BOOLEAN          COMMENT 'Binance `m`, as sent. side above is derived from it',
    ignore_flag      BOOLEAN          COMMENT 'Binance `M`, as sent',
    stream           STRING           COMMENT 'The combined-stream name, as sent',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the bronze row (= the raw.messages row)',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position of this trade within the frame, 0-based, in the order the venue sent — the last component of the identifier',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the silver run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(exchange_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.canonical_symbol' = 'full',
    'write.metadata.metrics.column.exchange_ts'     = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.trade_seq'       = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Binance trades typed from bronze.binance_trade, one row per frame'
);

ALTER TABLE lake.silver.trades_binance SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.trades_binance
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, exchange_ts;

-- ───────────────────────────────────────────────────────────────────────────
-- silver.trades_kraken
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.trades_kraken (
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol, as sent (the bronze value)',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE from config/instruments.yaml — silver derives it from the registry, never from the wire',
    trade_id         STRING  NOT NULL COMMENT 'The trade id the venue assigned, as sent',
    trade_seq        BIGINT  NOT NULL COMMENT 'trade_id as a number. All three venues number trades sequentially per symbol (measured 2026-08-27: Binance 8,667,843 consecutive ids vs 144 jumps). seq_gap is computed on this',
    price            DECIMAL(28,10) NOT NULL COMMENT 'Typed from the bronze value, exact',
    qty              DECIMAL(28,10) NOT NULL COMMENT 'Base quantity, exact',
    side             STRING  NOT NULL COMMENT 'Taker side normalised: buy | sell',
    side_native      STRING  NOT NULL COMMENT 'The side as the venue expresses it, as sent (Binance: the buyer-is-maker flag as true/false)',
    exchange_ts      TIMESTAMP NOT NULL COMMENT 'Venue trade time, typed UTC microseconds (Coinbase nanoseconds truncate to micros)',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock of the frame, nanoseconds',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns to microseconds',
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    venue_replay     BOOLEAN NOT NULL COMMENT 'true = an earlier delivery of the same (symbol, trade_id) exists in the archive (lower recv_ts_ns, or same and earlier lineage). Every delivery is kept. gold keeps the first',
    seq_gap          BOOLEAN          COMMENT 'true = the previous trade_seq for this symbol is more than 1 below this one: trades the archive never received. NULL = no previous trade within the lookback (unknowable)',
    missing_before   BIGINT           COMMENT 'How many trade ids are absent between the previous trade and this one. 0 when seq_gap is false, NULL with it',
    precision_loss   BOOLEAN NOT NULL COMMENT 'true = price or qty carries more than 8 decimal places, i.e. the 1e-8 fixed point of the wire and of gold cannot hold it exactly',
    ord_type         STRING           COMMENT 'Kraken `ord_type`, as sent',
    frame_type       STRING           COMMENT 'Kraken frame `type`: snapshot | update',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the bronze row (= the raw.messages row)',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position of this trade within the frame, 0-based, in the order the venue sent — the last component of the identifier',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the silver run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(exchange_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.canonical_symbol' = 'full',
    'write.metadata.metrics.column.exchange_ts'     = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.trade_seq'       = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Kraken trades typed from bronze.kraken_trade; N rows per frame'
);

ALTER TABLE lake.silver.trades_kraken SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.trades_kraken
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, exchange_ts;

-- ───────────────────────────────────────────────────────────────────────────
-- silver.trades_coinbase
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.trades_coinbase (
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol, as sent (the bronze value)',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE from config/instruments.yaml — silver derives it from the registry, never from the wire',
    trade_id         STRING  NOT NULL COMMENT 'The trade id the venue assigned, as sent',
    trade_seq        BIGINT  NOT NULL COMMENT 'trade_id as a number. All three venues number trades sequentially per symbol (measured 2026-08-27: Binance 8,667,843 consecutive ids vs 144 jumps). seq_gap is computed on this',
    price            DECIMAL(28,10) NOT NULL COMMENT 'Typed from the bronze value, exact',
    qty              DECIMAL(28,10) NOT NULL COMMENT 'Base quantity, exact',
    side             STRING  NOT NULL COMMENT 'Taker side normalised: buy | sell',
    side_native      STRING  NOT NULL COMMENT 'The side as the venue expresses it, as sent (Binance: the buyer-is-maker flag as true/false)',
    exchange_ts      TIMESTAMP NOT NULL COMMENT 'Venue trade time, typed UTC microseconds (Coinbase nanoseconds truncate to micros)',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock of the frame, nanoseconds',
    recv_ts          TIMESTAMP NOT NULL COMMENT 'recv_ts_ns to microseconds',
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    venue_replay     BOOLEAN NOT NULL COMMENT 'true = an earlier delivery of the same (symbol, trade_id) exists in the archive (lower recv_ts_ns, or same and earlier lineage). Every delivery is kept. gold keeps the first',
    seq_gap          BOOLEAN          COMMENT 'true = the previous trade_seq for this symbol is more than 1 below this one: trades the archive never received. NULL = no previous trade within the lookback (unknowable)',
    missing_before   BIGINT           COMMENT 'How many trade ids are absent between the previous trade and this one. 0 when seq_gap is false, NULL with it',
    precision_loss   BOOLEAN NOT NULL COMMENT 'true = price or qty carries more than 8 decimal places, i.e. the 1e-8 fixed point of the wire and of gold cannot hold it exactly',
    envelope_ts      TIMESTAMP        COMMENT 'Coinbase envelope `timestamp`, typed (nanoseconds truncate to micros)',
    sequence_num     BIGINT           COMMENT 'Coinbase connection-wide sequence of the envelope',
    event_type       STRING           COMMENT 'Coinbase `events[].type`: snapshot (history replayed on subscribe) | update',
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the bronze row (= the raw.messages row)',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position of this trade within the frame, 0-based, in the order the venue sent — the last component of the identifier',
    ingest_ts        TIMESTAMP NOT NULL COMMENT 'When the silver run that wrote this row started'
)
USING iceberg
PARTITIONED BY (days(exchange_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.metrics.column.canonical_symbol' = 'full',
    'write.metadata.metrics.column.exchange_ts'     = 'full',
    'write.metadata.metrics.column.recv_ts'         = 'full',
    'write.metadata.metrics.column.trade_seq'       = 'full',
    'write.metadata.metrics.column.src_offset'      = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Coinbase trades typed from bronze.coinbase_market_trades; N rows per frame under events[].trades[]'
);

ALTER TABLE lake.silver.trades_coinbase SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.trades_coinbase
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, exchange_ts;


-- ═══════════════════════════════════════════════════════════════════════════
-- Gold — Phase E, ADR-026. The canonical cross-venue surface: one schema, one
-- row per logical trade, fixed point 1e-8 like the wire and ClickHouse gold,
-- and the products a backtest reads first. Derived from silver only, by
-- docker/lake/gold.py; ClickHouse's gold database is loaded from these tables
-- (docs/runbooks/clickhouse-rebuild-from-lake.md) and fed live from the topics
-- for the head.
--
-- write.metadata.compression-codec = none on every gold table: ClickHouse 24.3's
-- iceberg() cannot read the gzip-compressed metadata.json Lakekeeper writes by
-- default ("JSON object/array should start with corresponding opening bracket",
-- measured 2026-08-27), and gold is the layer ClickHouse pulls.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- gold.trades — one row per logical trade, every venue.
--
-- The logical trade is (exchange, canonical_symbol, trade_id) — the identifier
-- fields — and the row is the EARLIEST delivery of it: silver keeps every
-- delivery and marks the later ones venue_replay = true, so gold is the
-- venue_replay = false rows of silver, one to one. Nothing is aggregated or
-- rounded on the way; price and qty are silver's exact decimals as 1e-8
-- fixed point, which every reader (DuckDB, ClickHouse `iceberg()`, pandas)
-- turns back into a decimal by one division. The flags a researcher needs are
-- carried (seq_gap, missing_before: the hole BEFORE this trade), venue_replay
-- is not (it is false by construction).
--
-- Partitioned by exchange, days(exchange_ts): the research axis, and exchange
-- has three values. Sorted (canonical_symbol, exchange_ts) inside.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.trades (
    exchange         STRING  NOT NULL COMMENT 'binance | kraken | coinbase',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE from config/instruments.yaml',
    symbol           STRING  NOT NULL COMMENT 'Venue-native symbol',
    trade_id         STRING  NOT NULL COMMENT 'The venue trade id, as sent',
    trade_seq        BIGINT  NOT NULL COMMENT 'trade_id as a number',
    price_e8         BIGINT  NOT NULL COMMENT 'Price x 1e8, exact (silver DECIMAL(28,10) has no more than 8 decimals on any venue here. precision_loss in silver says when that stops being true)',
    qty_e8           BIGINT  NOT NULL COMMENT 'Base quantity x 1e8',
    side             STRING  NOT NULL COMMENT 'Taker side: buy | sell',
    exchange_ts      TIMESTAMP NOT NULL COMMENT 'Venue clock, UTC microseconds',
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'K2 receive clock of the winning delivery',
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    seq_gap          BOOLEAN          COMMENT 'From silver: trade ids missing before this one (NULL = unknowable)',
    missing_before   BIGINT,
    src_topic        STRING  NOT NULL COMMENT 'Lineage to the silver row, which is the bronze row, which is the raw.messages row',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL,
    ingest_ts        TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange, days(exchange_ts))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.exchange_ts'         = 'full',
    'write.metadata.metrics.column.trade_seq'           = 'full',
    'write.metadata.metrics.column.src_offset'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Canonical trades, one row per logical trade (earliest delivery). Rebuildable from silver.'
);

ALTER TABLE lake.gold.trades SET IDENTIFIER FIELDS exchange, canonical_symbol, trade_id;

ALTER TABLE lake.gold.trades
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, exchange_ts;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.dim_instrument / gold.dim_venue — config/instruments.yaml as tables,
-- rewritten from the file on every gold run (overwrite, not append: a
-- dimension is a statement about now, and the file is its history).
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.dim_instrument (
    exchange         STRING  NOT NULL,
    symbol           STRING  NOT NULL COMMENT 'native, byte for byte as the venue spells it',
    canonical_symbol STRING  NOT NULL,
    base             STRING  NOT NULL,
    quote            STRING  NOT NULL,
    book_depth       INT     NOT NULL COMMENT 'The L2 subscription depth in force: the per-instrument override or the venue default',
    loaded_at        TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange)
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'config/instruments.yaml, as of the last gold run.'
);

CREATE TABLE IF NOT EXISTS lake.gold.dim_venue (
    exchange         STRING  NOT NULL,
    book_depth       INT     NOT NULL COMMENT 'Default L2 depth. 0 = the venue sends the whole book (Coinbase)',
    instruments      INT     NOT NULL COMMENT 'How many instruments are subscribed',
    loaded_at        TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange)
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'One row per venue, from config/instruments.yaml.'
);

-- ───────────────────────────────────────────────────────────────────────────
-- gold.ohlcv_{1m,5m,1h,1d} — candles materialised from gold.trades, one table
-- per bucket. Every bucket a batch of trades touches is recomputed over ALL of
-- gold.trades for that bucket and MERGEd in, so a late trade replaces the
-- candle instead of adding a second row for it — the v2 SummingMergeTree
-- failure, closed at the source. open/close are decided by (exchange_ts,
-- recv_ts_ns), the same rule ClickHouse's gold.ohlcv_live applies, which is
-- what the three-way parity check compares.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.ohlcv_1m (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    window_start     TIMESTAMP NOT NULL COMMENT 'Bucket start, UTC. The bucket is the table (1m, 5m, 1h, 1d)',
    open_e8          BIGINT  NOT NULL COMMENT 'First trade by (exchange_ts, recv_ts_ns), fixed point 1e-8',
    high_e8          BIGINT  NOT NULL,
    low_e8           BIGINT  NOT NULL,
    close_e8         BIGINT  NOT NULL COMMENT 'Last trade by (exchange_ts, recv_ts_ns)',
    volume           DECIMAL(38,10) NOT NULL COMMENT 'Sum of qty, base currency, exact',
    quote_volume     DECIMAL(38,10) NOT NULL COMMENT 'Sum of price x qty, quote currency, exact',
    trade_count      BIGINT  NOT NULL,
    open_time        TIMESTAMP NOT NULL COMMENT 'exchange_ts of the first trade in the bucket',
    close_time       TIMESTAMP NOT NULL COMMENT 'exchange_ts of the last trade in the bucket',
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.trades snapshot this bucket was computed from. Rows for a bucket are replaced, never appended, when later trades land in it',
    computed_at      TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange, months(window_start))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.window_start'        = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'OHLCV 1m from gold.trades, one row per (exchange, symbol, bucket), replaced on late trades.'
);

ALTER TABLE lake.gold.ohlcv_1m SET IDENTIFIER FIELDS exchange, canonical_symbol, window_start;

CREATE TABLE IF NOT EXISTS lake.gold.ohlcv_5m (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    window_start     TIMESTAMP NOT NULL COMMENT 'Bucket start, UTC. The bucket is the table (1m, 5m, 1h, 1d)',
    open_e8          BIGINT  NOT NULL COMMENT 'First trade by (exchange_ts, recv_ts_ns), fixed point 1e-8',
    high_e8          BIGINT  NOT NULL,
    low_e8           BIGINT  NOT NULL,
    close_e8         BIGINT  NOT NULL COMMENT 'Last trade by (exchange_ts, recv_ts_ns)',
    volume           DECIMAL(38,10) NOT NULL COMMENT 'Sum of qty, base currency, exact',
    quote_volume     DECIMAL(38,10) NOT NULL COMMENT 'Sum of price x qty, quote currency, exact',
    trade_count      BIGINT  NOT NULL,
    open_time        TIMESTAMP NOT NULL COMMENT 'exchange_ts of the first trade in the bucket',
    close_time       TIMESTAMP NOT NULL COMMENT 'exchange_ts of the last trade in the bucket',
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.trades snapshot this bucket was computed from. Rows for a bucket are replaced, never appended, when later trades land in it',
    computed_at      TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange, months(window_start))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.window_start'        = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'OHLCV 5m from gold.trades, one row per (exchange, symbol, bucket), replaced on late trades.'
);

ALTER TABLE lake.gold.ohlcv_5m SET IDENTIFIER FIELDS exchange, canonical_symbol, window_start;

CREATE TABLE IF NOT EXISTS lake.gold.ohlcv_1h (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    window_start     TIMESTAMP NOT NULL COMMENT 'Bucket start, UTC. The bucket is the table (1m, 5m, 1h, 1d)',
    open_e8          BIGINT  NOT NULL COMMENT 'First trade by (exchange_ts, recv_ts_ns), fixed point 1e-8',
    high_e8          BIGINT  NOT NULL,
    low_e8           BIGINT  NOT NULL,
    close_e8         BIGINT  NOT NULL COMMENT 'Last trade by (exchange_ts, recv_ts_ns)',
    volume           DECIMAL(38,10) NOT NULL COMMENT 'Sum of qty, base currency, exact',
    quote_volume     DECIMAL(38,10) NOT NULL COMMENT 'Sum of price x qty, quote currency, exact',
    trade_count      BIGINT  NOT NULL,
    open_time        TIMESTAMP NOT NULL COMMENT 'exchange_ts of the first trade in the bucket',
    close_time       TIMESTAMP NOT NULL COMMENT 'exchange_ts of the last trade in the bucket',
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.trades snapshot this bucket was computed from. Rows for a bucket are replaced, never appended, when later trades land in it',
    computed_at      TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange, months(window_start))
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.window_start'        = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'OHLCV 1h from gold.trades, one row per (exchange, symbol, bucket), replaced on late trades.'
);

ALTER TABLE lake.gold.ohlcv_1h SET IDENTIFIER FIELDS exchange, canonical_symbol, window_start;

CREATE TABLE IF NOT EXISTS lake.gold.ohlcv_1d (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    window_start     TIMESTAMP NOT NULL COMMENT 'Bucket start, UTC. The bucket is the table (1m, 5m, 1h, 1d)',
    open_e8          BIGINT  NOT NULL COMMENT 'First trade by (exchange_ts, recv_ts_ns), fixed point 1e-8',
    high_e8          BIGINT  NOT NULL,
    low_e8           BIGINT  NOT NULL,
    close_e8         BIGINT  NOT NULL COMMENT 'Last trade by (exchange_ts, recv_ts_ns)',
    volume           DECIMAL(38,10) NOT NULL COMMENT 'Sum of qty, base currency, exact',
    quote_volume     DECIMAL(38,10) NOT NULL COMMENT 'Sum of price x qty, quote currency, exact',
    trade_count      BIGINT  NOT NULL,
    open_time        TIMESTAMP NOT NULL COMMENT 'exchange_ts of the first trade in the bucket',
    close_time       TIMESTAMP NOT NULL COMMENT 'exchange_ts of the last trade in the bucket',
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.trades snapshot this bucket was computed from. Rows for a bucket are replaced, never appended, when later trades land in it',
    computed_at      TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange)
TBLPROPERTIES (
    'format-version'                                = '2',
    'write.format.default'                          = 'parquet',
    'write.parquet.compression-codec'               = 'zstd',
    'write.distribution-mode'                       = 'hash',
    'write.target-file-size-bytes'                  = '134217728',
    'write.delete.mode'                             = 'copy-on-write',
    'write.update.mode'                             = 'copy-on-write',
    'write.merge.mode'                              = 'copy-on-write',
    'write.metadata.metrics.default'                = 'none',
    'write.metadata.compression-codec'              = 'none',
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.window_start'        = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'OHLCV 1d from gold.trades, one row per (exchange, symbol, bucket), replaced on late trades.'
);

ALTER TABLE lake.gold.ohlcv_1d SET IDENTIFIER FIELDS exchange, canonical_symbol, window_start;

-- ───────────────────────────────────────────────────────────────────────────
-- audit.checks — one row per check per maintenance run. Append-only history, so
-- "when did offset continuity last hold for market.crypto.v3.raw.kraken/7" is a
-- query rather than a log grep. docker/lake/metrics.py reads it for
-- k2_lake_audit_failures_total. docker/lake/ingest.py also writes here, with
-- job='ingest', when it meets a schema id the registry cannot serve.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.audit.checks (
    run_ts     TIMESTAMP NOT NULL COMMENT 'When the maintenance run that produced this row started',
    job        STRING  NOT NULL COMMENT 'Who wrote it: maintenance | ingest | verify | operator. operator means a human filed it by hand at the moment of a decision — the two runbook rows that record a deliberate purge and a known offset gap. The column exists to answer "who asserted this", so a hand-filed row claiming maintenance would be the one lie that matters',
    check_name STRING  NOT NULL COMMENT 'offset_continuity | duplicate_identifiers | sequence_gaps | venue_replay (informational, no pass/fail) | unresolvable_schema_id (written by ingest, not maintenance) | offset_gap (also ingest: a --accept-data-loss run recording records Redpanda evicted before the lake read them, one row per partition, written BEFORE the skip it licenses) | manual_purge (a deletion, not a finding — kept distinct so a query over this table never reads a purge as a check result)',
    scope      STRING  NOT NULL COMMENT 'What was checked: a table name, or topic/partition',
    passed     BOOLEAN NOT NULL COMMENT 'false here is what makes the maintenance run exit non-zero',
    observed   BIGINT           COMMENT 'The number the check produced — gap count, duplicate count. NULL where the check has no count',
    detail     STRING           COMMENT 'Human-readable specifics for a failure; empty on success'
)
USING iceberg
PARTITIONED BY (days(run_ts))
TBLPROPERTIES (
    'format-version'                    = '2',
    'write.format.default'              = 'parquet',
    'write.parquet.compression-codec'   = 'zstd',
    'write.distribution-mode'           = 'hash',
    'write.target-file-size-bytes'      = '134217728',
    'write.delete.mode'                 = 'copy-on-write',
    'write.update.mode'                 = 'copy-on-write',
    'write.merge.mode'                  = 'copy-on-write',
    'commit.retry.num-retries'          = '10',
    'comment'                           = 'Audit history. Append-only; a failed check is never edited out.'
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 v3 lake — the Iceberg tables (raw, bronze per venue, silver trades + books per venue, gold, audit), applied by docker/lake/apply_ddl.py
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
-- The Phase D unified bronze.trades / bronze.book_snapshots_l2 were dropped at
-- the Phase E cutover after the per-venue rebuild proved parity against them
-- (docs/benchmarks/2026-08-27.md); ADR-024 records the design and its supersession.
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
-- gold.dim_instrument / gold.dim_venue — the security master, Kimball type-2
-- (ADR-030). One row per validity interval, not a snapshot: a research result
-- computed from an instrument attribute is only reproducible if the attribute
-- can be read as it stood at the trade timestamp, and the SCD1 shape these
-- tables had until 2026-08-29 made reading the wrong one undetectable.
--
--   natural key    (exchange, canonical_symbol) — the half of the identity a
--                  venue rename does not touch. The native `symbol` is a
--                  TRACKED ATTRIBUTE, so Kraken XBT/USD -> BTC/USD closes one
--                  version and opens the next under the same instrument_id.
--   surrogate      first 32 hex of sha256(exchange | 0x1F | canonical_symbol).
--                  Deterministic, not a sequence: rebuild.py --layer gold
--                  drops and recreates gold, and a sequence would renumber
--                  every id every time.
--   open row       valid_to = 9999-12-31 23:59:59, NEVER NULL. `ts < NULL` is
--                  not TRUE, so a NULL upper bound silently drops the current
--                  row from every hand-written range join while DuckDB ASOF
--                  JOIN survives it — two spellings, two row counts.
--   change         attr_hash, sha256 over the canonically serialised tracked
--                  attributes. One comparison however wide the row gets.
--
-- CREATE IF NOT EXISTS, deliberately, though the shape changed: the lake-ddl
-- one-shot runs on every `docker compose up`, and a DROP here would delete the
-- accumulated history on every restart. These are the only gold tables that
-- are not rebuildable from silver — nothing is a source for lost dimension
-- history — so rebuild.py leaves them alone.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.dim_instrument (
    instrument_id    STRING  NOT NULL COMMENT 'Deterministic surrogate: first 32 hex of sha256(exchange | 0x1F | canonical_symbol). Stable across a rebuild and across a native-symbol rename',
    exchange         STRING  NOT NULL COMMENT 'With canonical_symbol, the natural key',
    canonical_symbol STRING  NOT NULL COMMENT 'BASE/QUOTE. Unique per exchange — tests/test_contracts.py asserts it, which is what makes this a key',
    symbol           STRING  NOT NULL COMMENT 'native, byte for byte as the venue spells it. Tracked: a rename opens a new version, not a new instrument',
    base             STRING  NOT NULL,
    quote            STRING  NOT NULL,
    book_depth       INT     NOT NULL COMMENT 'The L2 subscription depth in force: the per-instrument override or the venue default',
    subscribed       BOOLEAN NOT NULL COMMENT 'Present in config/instruments.yaml as of this version. An instrument that leaves the registry gets a subscribed = false version, never a delete: deleting would make historical facts unjoinable',
    tick_size        DECIMAL(28,10)   COMMENT 'Venue-published. Kraken only, from bronze.kraken_instrument data.pairs[]. NULL on Binance and Coinbase, which publish it over REST that K2 does not capture — `source` disambiguates',
    qty_increment    DECIMAL(28,10)   COMMENT 'Venue-published, Kraken only',
    price_precision  INT              COMMENT 'Venue-published, Kraken only. The precision silver.book_kraken verifies checksums at',
    qty_precision    INT              COMMENT 'Venue-published, Kraken only',
    venue_status     STRING           COMMENT 'Venue-published trading status (Kraken: online, cancel_only, post_only, limit_only, reduce_only). NULL where not captured',
    source           STRING  NOT NULL COMMENT 'Which authority supplied this version''s venue attributes: registry (they are NULL) or venue:kraken',
    attr_hash        STRING  NOT NULL COMMENT 'sha256 over the tracked attributes, canonically serialised: fixed field order, 0x1F separator, NULL as 0x00. Adding a tracked attribute rewrites every hash and opens a version for every instrument, once',
    valid_from       TIMESTAMP NOT NULL COMMENT 'Effective from, inclusive',
    valid_to         TIMESTAMP NOT NULL COMMENT 'Effective to, exclusive. Open rows carry the sentinel 9999-12-31 23:59:59, never NULL',
    is_current       BOOLEAN NOT NULL COMMENT 'valid_to = the sentinel. Derivable, kept because it is the predicate every consumer writes and it pushes down',
    recorded_at      TIMESTAMP NOT NULL COMMENT 'When K2 learned it. Equal to valid_from for a registry change; valid_from < recorded_at is a backfill, and is the only as-known-at axis this table has'
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
    'write.metadata.metrics.column.valid_from'          = 'full',
    'write.metadata.metrics.column.is_current'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Security master, SCD2. One row per validity interval per instrument; as-of join on (exchange, canonical_symbol) at valid_from.'
);

ALTER TABLE lake.gold.dim_instrument SET IDENTIFIER FIELDS instrument_id, valid_from;

ALTER TABLE lake.gold.dim_instrument
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, valid_from;

CREATE TABLE IF NOT EXISTS lake.gold.dim_venue (
    venue_id         STRING  NOT NULL COMMENT 'Deterministic surrogate: first 32 hex of sha256(exchange)',
    exchange         STRING  NOT NULL COMMENT 'The natural key',
    book_depth       INT     NOT NULL COMMENT 'Default L2 depth. 0 = the venue sends the whole book (Coinbase)',
    instruments      INT     NOT NULL COMMENT 'How many instruments are subscribed. Tracked, so adding an instrument opens a venue version too',
    subscribed       BOOLEAN NOT NULL COMMENT 'Present in config/instruments.yaml as of this version. A venue that leaves gets a subscribed = false version, never a delete',
    source           STRING  NOT NULL COMMENT 'registry. No venue publishes its own row',
    attr_hash        STRING  NOT NULL COMMENT 'sha256 over the tracked attributes, as dim_instrument',
    valid_from       TIMESTAMP NOT NULL COMMENT 'Effective from, inclusive',
    valid_to         TIMESTAMP NOT NULL COMMENT 'Effective to, exclusive. Open rows carry the sentinel 9999-12-31 23:59:59, never NULL',
    is_current       BOOLEAN NOT NULL,
    recorded_at      TIMESTAMP NOT NULL COMMENT 'When K2 learned it'
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
    'write.metadata.metrics.column.valid_from'          = 'full',
    'write.metadata.metrics.column.is_current'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Venue dimension, SCD2. One row per validity interval per venue.'
);

ALTER TABLE lake.gold.dim_venue SET IDENTIFIER FIELDS venue_id, valid_from;

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

-- ───────────────────────────────────────────────────────────────────────────
-- gold.bars — event bars: tick, volume and dollar, at the one canonical
-- threshold per symbol in config/bars.yaml (docker/lake/bars.py, ADR-029). A
-- cumulative-bucket bar: trade -> bar k of its UTC day when
-- k*T <= (day's total before it) < (k+1)*T in the same (exchange_ts, recv_ts_ns,
-- trade_seq) order the candles use. Bars restart at the UTC day boundary. The
-- touched (exchange, symbol, day) set is deleted and re-appended per run, never
-- MERGEd: a late trade moves every later boundary in its day. Any other
-- threshold is a query over gold.trades (notebooks/k2lake.py bars()), not a
-- second table.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.bars (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    bar_kind         STRING  NOT NULL COMMENT 'tick | volume | dollar',
    threshold        DECIMAL(38,10) NOT NULL COMMENT 'The bucket size the bar was built at: trades, base units or quote-currency notional. Carried on the row so a bar is self-describing when the config moves',
    day              DATE    NOT NULL COMMENT 'UTC day of every trade in the bar; bars never span a day',
    bar_seq          INT     NOT NULL COMMENT 'Bucket index within (exchange, symbol, kind, day), from 0',
    open_e8          BIGINT  NOT NULL COMMENT 'First trade by (exchange_ts, recv_ts_ns, trade_seq), fixed point 1e-8',
    high_e8          BIGINT  NOT NULL,
    low_e8           BIGINT  NOT NULL,
    close_e8         BIGINT  NOT NULL COMMENT 'Last trade by the same order',
    volume_e8        BIGINT  NOT NULL COMMENT 'Sum of qty, base currency, fixed point 1e-8, exact',
    quote_volume_e8  BIGINT  NOT NULL COMMENT 'Sum of price x qty, quote currency, fixed point 1e-8: the exact 1e-16 sum floor-divided by 1e8, the one rounding in the table and an integer rule every engine spells the same way',
    trade_count      BIGINT  NOT NULL,
    open_time        TIMESTAMP NOT NULL COMMENT 'exchange_ts of the first trade',
    close_time       TIMESTAMP NOT NULL COMMENT 'exchange_ts of the last trade; the last bar of a day is open-ended until the day is',
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.trades snapshot this day was computed from',
    computed_at      TIMESTAMP NOT NULL
)
USING iceberg
-- months(open_time), not months(day): DuckDB 1.4.4's Iceberg reader returned
-- zero rows for any predicate on a DATE column that was a partition source
-- (`day = DATE '2026-08-26'` -> 0 of 4,463; `CAST(day AS VARCHAR) = ...` -> 4,463,
-- 2026-08-28), while the same predicate shapes on TIMESTAMP sources
-- (ohlcv_1m.window_start) are right. Same rows either way; day stays a plain
-- column and filters on it work.
PARTITIONED BY (exchange, months(open_time))
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
    'write.metadata.metrics.column.open_time'           = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Event bars (tick/volume/dollar) from gold.trades at config/bars.yaml thresholds; one row per (exchange, symbol, kind, day, bar_seq); a touched day is replaced whole.'
);

ALTER TABLE lake.gold.bars SET IDENTIFIER FIELDS exchange, canonical_symbol, bar_kind, day, bar_seq;

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
-- bronze.kraken_instrument — the `instrument` channel (reference data): every
-- pair price_precision / qty_precision, which the book checksum is defined
-- over. A snapshot at subscribe (≈ 566 KB, every asset and pair Kraken lists)
-- and small updates after. Vendor schema as sent, like the other six.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.bronze.kraken_instrument (
    channel          STRING           COMMENT 'instrument',
    type             STRING           COMMENT 'snapshot | update',
    data             STRUCT<assets: ARRAY<STRUCT<id: STRING, status: STRING, precision: INT, precision_display: INT, borrowable: BOOLEAN, collateral_value: DECIMAL(28,10), class: STRING, margin_rate: DECIMAL(28,10)>>, pairs: ARRAY<STRUCT<symbol: STRING, base: STRING, quote: STRING, status: STRING, qty_precision: INT, qty_increment: DECIMAL(28,10), price_precision: INT, cost_precision: INT, marginable: BOOLEAN, has_index: BOOLEAN, ws_display_price_precision: INT, cost_min: DECIMAL(28,10), margin_initial: DECIMAL(28,10), position_limit_long: BIGINT, position_limit_short: BIGINT, tick_size: DECIMAL(28,10), price_increment: DECIMAL(28,10), qty_min: DECIMAL(28,10)>>>
                                      COMMENT 'assets[] and pairs[] as sent. price_precision and qty_precision on pairs[] are what silver.book_kraken verifies checksums with',
    symbol           STRING           COMMENT 'RawMessage.symbol, venue spelling. NULL = no single instrument',
    recv_ts_ns       BIGINT  NOT NULL,
    recv_ts          TIMESTAMP NOT NULL,
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    src_topic        STRING  NOT NULL,
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    ingest_ts        TIMESTAMP NOT NULL
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
    'write.metadata.metrics.column.symbol'              = 'full',
    'write.metadata.metrics.column.recv_ts'             = 'full',
    'write.metadata.metrics.column.src_offset'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Kraken instrument channel frames (reference data: pair precisions). Vendor schema as sent.'
);

ALTER TABLE lake.bronze.kraken_instrument SET IDENTIFIER FIELDS src_topic, src_partition, src_offset;

ALTER TABLE lake.bronze.kraken_instrument
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY symbol, recv_ts_ns;


-- ═══════════════════════════════════════════════════════════════════════════
-- Silver books — Phase E, ADR-026. One table per venue, typed frames, every
-- frame kept. Kraken carries the checksum verdict; the book itself is not
-- stored per frame (40 M frames a day x 25 levels would be the archive
-- again) — gold.book_top20 is the 1 Hz sampled state, replayed from these.
-- ═══════════════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────────────
-- silver.book_binance
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.book_binance (
    symbol           STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    last_update_id   BIGINT  NOT NULL COMMENT 'Binance lastUpdateId of this partial-book frame',
    bids             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL COMMENT 'The frame IS the top-20 book: best first, typed from the [["px","qty"]] pairs',
    asks             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL,
    depth            INT     NOT NULL COMMENT 'Levels per side in the frame',
    seq_gap          BOOLEAN          COMMENT 'true = last_update_id did not advance past the previous frame on this connection (a regression). NULL on the first frame of a connection in the lookback',
    recv_ts_ns       BIGINT  NOT NULL,
    recv_ts          TIMESTAMP NOT NULL,
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    src_topic        STRING  NOT NULL COMMENT 'Lineage to the bronze row',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position within the frame (Coinbase events[i]). 0 where a frame is one event',
    ingest_ts        TIMESTAMP NOT NULL
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
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.recv_ts'             = 'full',
    'write.metadata.metrics.column.src_offset'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Binance depth20 frames typed. Each frame is a complete top-20 snapshot; no state to replay.'
);

ALTER TABLE lake.silver.book_binance SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.book_binance
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, recv_ts_ns;

-- ───────────────────────────────────────────────────────────────────────────
-- silver.book_kraken
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.book_kraken (
    symbol           STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    frame_type       STRING  NOT NULL COMMENT 'snapshot | update',
    bids             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL COMMENT 'The levels the frame carries (a snapshot: the book. an update: the changed levels, qty 0 = removed)',
    asks             ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>> NOT NULL,
    checksum         BIGINT  NOT NULL COMMENT 'The CRC32 the venue attached, as sent',
    checksum_ok      BOOLEAN          COMMENT 'true = the book replayed from this connection frames, truncated to the subscription depth, hashes to `checksum` at the pair precision (docker/lake/book.py). false = it does not: a missed or misapplied frame. NULL = precision unknown (no instrument frame yet)',
    exchange_ts      TIMESTAMP        COMMENT 'The frame timestamp, typed',
    recv_ts_ns       BIGINT  NOT NULL,
    recv_ts          TIMESTAMP NOT NULL,
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    src_topic        STRING  NOT NULL COMMENT 'Lineage to the bronze row',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position within the frame (Coinbase events[i]). 0 where a frame is one event',
    ingest_ts        TIMESTAMP NOT NULL
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
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.recv_ts'             = 'full',
    'write.metadata.metrics.column.src_offset'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Kraken book frames typed, with the checksum verified by replay (the HFT-grade integrity check).'
);

ALTER TABLE lake.silver.book_kraken SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.book_kraken
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, recv_ts_ns;

-- ───────────────────────────────────────────────────────────────────────────
-- silver.book_coinbase
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.silver.book_coinbase (
    symbol           STRING  NOT NULL COMMENT 'events[i].product_id',
    canonical_symbol STRING  NOT NULL,
    event_type       STRING  NOT NULL COMMENT 'snapshot (the whole book) | update',
    updates          ARRAY<STRUCT<side: STRING, side_native: STRING, px: DECIMAL(28,10), qty: DECIMAL(28,10), event_time: TIMESTAMP>> NOT NULL
                                      COMMENT 'Absolute quantities per price level. side normalised to bid | ask beside the venue bid | offer. qty 0 = level removed',
    sequence_num     BIGINT  NOT NULL COMMENT 'Connection-wide, across every channel',
    envelope_ts      TIMESTAMP NOT NULL,
    seq_gap          BOOLEAN          COMMENT 'NULL: sequence_num continuity spans heartbeats and trades, which are not in this table. the capture counts gaps live (k2_capture_gaps_total)',
    recv_ts_ns       BIGINT  NOT NULL,
    recv_ts          TIMESTAMP NOT NULL,
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    src_topic        STRING  NOT NULL COMMENT 'Lineage to the bronze row',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL COMMENT 'Position within the frame (Coinbase events[i]). 0 where a frame is one event',
    ingest_ts        TIMESTAMP NOT NULL
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
    'write.metadata.metrics.column.canonical_symbol'    = 'full',
    'write.metadata.metrics.column.recv_ts'             = 'full',
    'write.metadata.metrics.column.src_offset'          = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Coinbase level2 events typed, one row per event; the book is replayed from them for gold.book_top20.'
);

ALTER TABLE lake.silver.book_coinbase SET IDENTIFIER FIELDS src_topic, src_partition, src_offset, src_index;

ALTER TABLE lake.silver.book_coinbase
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, recv_ts_ns;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.book_top20 — the book as it stood at the end of each second, top 20 per
-- side, every venue, one schema: the wire four parallel Int64 arrays, so
-- ClickHouse gold.book_top20 loads it column for column. Replayed from the
-- silver frames per connection (docker/lake/books.py); a second with no frame
-- carries the previous state forward, as the capture own 1 Hz sampler does.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.book_top20 (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    symbol           STRING  NOT NULL,
    second           TIMESTAMP NOT NULL COMMENT 'The 1 Hz bucket. the state is as of its end',
    depth            INT     NOT NULL COMMENT 'Levels per side present, at most 20',
    seq              BIGINT  NOT NULL COMMENT 'Venue sequence of the last frame folded in (Binance lastUpdateId, Coinbase sequence_num). 0 for Kraken',
    checksum_ok      BOOLEAN          COMMENT 'Kraken: the last frame verdict. NULL elsewhere',
    bid_px_e8        ARRAY<BIGINT> NOT NULL COMMENT 'Best first, 1e-8 fixed point',
    bid_qty_e8       ARRAY<BIGINT> NOT NULL,
    ask_px_e8        ARRAY<BIGINT> NOT NULL,
    ask_qty_e8       ARRAY<BIGINT> NOT NULL,
    recv_ts_ns       BIGINT  NOT NULL COMMENT 'Receive time of the last frame folded in',
    conn_id          STRING  NOT NULL,
    conn_msg_seq     BIGINT  NOT NULL,
    src_topic        STRING  NOT NULL COMMENT 'Lineage: the last frame folded in',
    src_partition    INT     NOT NULL,
    src_offset       BIGINT  NOT NULL,
    src_index        INT     NOT NULL,
    ingest_ts        TIMESTAMP NOT NULL
)
USING iceberg
PARTITIONED BY (exchange, days(second))
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
    'write.metadata.metrics.column.second'              = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = '1 Hz top-20 book states replayed from silver. Loaded into ClickHouse gold.book_top20 by pull.'
);

ALTER TABLE lake.gold.book_top20 SET IDENTIFIER FIELDS exchange, canonical_symbol, second;

ALTER TABLE lake.gold.book_top20
    WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY canonical_symbol, second;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.bbo_1s — best bid / offer and the derived numbers, one row per book
-- state above (a plain SQL projection of it; ClickHouse gold.bbo_live is the
-- same arithmetic on read).
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.bbo_1s (
    exchange         STRING  NOT NULL,
    canonical_symbol STRING  NOT NULL,
    second           TIMESTAMP NOT NULL,
    bid_e8           BIGINT  NOT NULL,
    bid_qty_e8       BIGINT  NOT NULL,
    ask_e8           BIGINT  NOT NULL,
    ask_qty_e8       BIGINT  NOT NULL,
    mid              DOUBLE  NOT NULL COMMENT '(bid + ask) / 2, quote currency',
    spread_bps       DOUBLE  NOT NULL COMMENT '(ask - bid) / mid x 1e4',
    imbalance        DOUBLE  NOT NULL COMMENT 'bid_qty / (bid_qty + ask_qty)',
    microprice       DOUBLE  NOT NULL COMMENT '(bid x ask_qty + ask x bid_qty) / (bid_qty + ask_qty)',
    checksum_ok      BOOLEAN,
    src_snapshot_id  BIGINT  NOT NULL COMMENT 'The gold.book_top20 snapshot this row was projected from'
)
USING iceberg
PARTITIONED BY (exchange, days(second))
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
    'write.metadata.metrics.column.second'              = 'full',
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'BBO per second, projected from gold.book_top20.'
);

ALTER TABLE lake.gold.bbo_1s SET IDENTIFIER FIELDS exchange, canonical_symbol, second;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.book_state — the replay carry-over between ticks: per (venue,
-- symbol, connection), the book after the last frame processed and the last
-- second emitted. Overwritten each run. Operational, not a product; it exists
-- so a 5-minute tick does not re-read a connection whole life to know its
-- book. Empty before a rebuild.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.gold.book_state (
    exchange         STRING  NOT NULL,
    symbol           STRING  NOT NULL,
    conn_id          STRING  NOT NULL,
    bid_px_e8        ARRAY<BIGINT> NOT NULL,
    bid_qty_e8       ARRAY<BIGINT> NOT NULL,
    ask_px_e8        ARRAY<BIGINT> NOT NULL,
    ask_qty_e8       ARRAY<BIGINT> NOT NULL,
    seq              BIGINT  NOT NULL,
    checksum_ok      BOOLEAN,
    last_conn_msg_seq BIGINT NOT NULL,
    last_recv_ts_ns  BIGINT  NOT NULL,
    last_second      TIMESTAMP NOT NULL COMMENT 'The last 1 Hz bucket emitted for this connection',
    last_src_partition INT   NOT NULL,
    last_src_offset  BIGINT  NOT NULL,
    last_src_index   INT     NOT NULL,
    updated_at       TIMESTAMP NOT NULL
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
    'commit.retry.num-retries'                      = '10',
    'comment'                                       = 'Replay carry-over per connection; overwritten each run.'
);

ALTER TABLE lake.gold.book_state SET IDENTIFIER FIELDS exchange, symbol, conn_id;

-- ───────────────────────────────────────────────────────────────────────────
-- audit.checks — one row per check per maintenance run. Append-only history, so
-- "when did offset continuity last hold for market.crypto.v3.raw.kraken/7" is a
-- query rather than a log grep. docker/lake/metrics.py reads it for
-- k2_lake_audit_failures_total. docker/lake/ingest.py also writes here, with
-- job='ingest', when it meets a schema id the registry cannot serve.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.audit.checks (
    run_ts     TIMESTAMP NOT NULL COMMENT 'When the maintenance run that produced this row started',
    job        STRING  NOT NULL COMMENT 'Who wrote it: maintenance | ingest | verify | operator | replay. replay is one row per `scripts/replay-lake.sh` run (docker/lake/record_check.py): the raw.messages snapshot, the conn_id and the crate sha in detail, the output hash in scope-free form, so a result is reproducible by re-running the same ids and comparing one digest. operator means a human filed it by hand at the moment of a decision — the two runbook rows that record a deliberate purge and a known offset gap. The column exists to answer "who asserted this", so a hand-filed row claiming maintenance would be the one lie that matters',
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

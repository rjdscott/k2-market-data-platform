-- ═══════════════════════════════════════════════════════════════════════════
-- K2 v3 — the Redpanda feeds into `gold` (boot only; not applied by CI).
--
-- One Kafka-engine table per record type over all three venues' topics,
-- AvroConfluent against Redpanda's schema registry (spike S4, ADR-020): the
-- consumer decodes each record against the schema id in its own framing, so a
-- schema evolution on one venue's topic cannot mis-decode another's. Two
-- consumers, a thread each, so a slow venue does not stall the others.
--
-- The MVs are the only writers on this path. They carry the Kafka lineage
-- (`_topic`, `_partition`, `_offset`) into src_* so a gold row can be traced
-- to the frame in raw.messages by the same three values the lake uses.
--
-- A new consumer group starts wherever librdkafka's auto.offset.reset puts
-- it; on the first boot that is the topic's retention (7 days) — the head
-- start. Everything older comes from the lake.
--
-- kafka_handle_error_mode = 'stream': a record the decoder cannot read is not
-- retried forever, it is delivered with `_error` set and `_raw_message`
-- holding the bytes, and the MVs route it to gold.feed_errors instead of the
-- data table. Measured 2026-08-27, twice, with the default mode: a JSON frame
-- a chaos script had produced onto market.crypto.v3.trades.kraken (partition
-- 0, offset 49895, magic byte `{`) stalled that partition permanently and
-- Kraken gold sat at 72,195 rows against 164,467 in the lake; then, with
-- kafka_skip_broken_messages set, a second chaos record (partition 10, offset
-- 1895, schema id 0) still stalled — the failure is a registry 404 while
-- fetching the schema, which is not a "broken message" to that setting.
-- 'stream' catches both. The archive holds the bytes and the lake's audit
-- counts them; the served tier's job is to keep serving, and to say what it
-- skipped.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS gold.q_trades
(
    exchange         String,
    symbol           String,
    canonical_symbol String,
    trade_id         String,
    price            Int64,
    qty              Int64,
    side             Enum8('buy' = 1, 'sell' = 2),
    exchange_ts      DateTime64(6, 'UTC'),
    recv_ts_ns       Int64,
    seq              Int64,
    conn_id          String,
    conn_msg_seq     Int64
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.v3.trades.binance,market.crypto.v3.trades.kraken,market.crypto.v3.trades.coinbase',
    kafka_group_name = 'k2-gold-trades',
    kafka_format = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers = 2,
    kafka_thread_per_consumer = 1,
    kafka_max_block_size = 10000,
    kafka_poll_max_batch_size = 10000,
    kafka_flush_interval_ms = 5000,
    kafka_handle_error_mode = 'stream';

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.q_trades_mv TO gold.trades AS
SELECT
    exchange, symbol, canonical_symbol, trade_id,
    price AS price_e8,
    qty   AS qty_e8,
    side, exchange_ts, recv_ts_ns, seq, conn_id, conn_msg_seq,
    _topic     AS src_topic,
    _partition AS src_partition,
    _offset    AS src_offset,
    18446744073709551615 - toUInt64(recv_ts_ns) AS first_seen
FROM gold.q_trades
WHERE length(_error) = 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.q_trades_errors_mv TO gold.feed_errors AS
SELECT now64(6) AS seen_at, _topic AS topic, _partition AS partition, _offset AS offset, _error AS error, _raw_message AS raw
FROM gold.q_trades
WHERE length(_error) > 0;

CREATE TABLE IF NOT EXISTS gold.q_book
(
    exchange         String,
    symbol           String,
    canonical_symbol String,
    depth            Int32,
    seq              Int64,
    checksum_ok      Nullable(Bool),
    bid_px           Array(Int64),
    bid_qty          Array(Int64),
    ask_px           Array(Int64),
    ask_qty          Array(Int64),
    exchange_ts      Nullable(DateTime64(6, 'UTC')),
    recv_ts_ns       Int64,
    snapshot_ts_ns   Int64,
    conn_id          String,
    conn_msg_seq     Int64
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.v3.book.binance,market.crypto.v3.book.kraken,market.crypto.v3.book.coinbase',
    kafka_group_name = 'k2-gold-book',
    kafka_format = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers = 2,
    kafka_thread_per_consumer = 1,
    kafka_max_block_size = 10000,
    kafka_poll_max_batch_size = 10000,
    kafka_flush_interval_ms = 5000,
    kafka_handle_error_mode = 'stream';

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.q_book_mv TO gold.book_top20 AS
SELECT
    exchange, symbol, canonical_symbol,
    toUInt8(depth) AS depth,
    seq, checksum_ok, bid_px, bid_qty, ask_px, ask_qty, exchange_ts,
    recv_ts_ns, snapshot_ts_ns,
    fromUnixTimestamp64Micro(intDiv(snapshot_ts_ns, 1000))        AS snapshot_ts,
    toDateTime(intDiv(snapshot_ts_ns, 1000000000), 'UTC')          AS second,
    conn_id, conn_msg_seq,
    _topic     AS src_topic,
    _partition AS src_partition,
    _offset    AS src_offset,
    toUInt64(snapshot_ts_ns) AS ver
FROM gold.q_book
WHERE length(_error) = 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.q_book_errors_mv TO gold.feed_errors AS
SELECT now64(6) AS seen_at, _topic AS topic, _partition AS partition, _offset AS offset, _error AS error, _raw_message AS raw
FROM gold.q_book
WHERE length(_error) > 0;

DROP TABLE IF EXISTS spike.q_trades;
DROP TABLE IF EXISTS spike.q_book;
DROP TABLE IF EXISTS spike.trades;
DROP TABLE IF EXISTS spike.book;
DROP VIEW IF EXISTS spike.mv_trades;
DROP VIEW IF EXISTS spike.mv_book;

CREATE TABLE spike.q_trades
(
    exchange    String,
    symbol      String,
    exchange_ts DateTime64(6),
    recv_ts_ns  Int64,
    price       Int64,
    qty         Int64,
    side        Enum8('buy' = 1, 'sell' = 2),
    trade_id    String,
    seq         Int64
) ENGINE = Kafka
SETTINGS kafka_broker_list = 'redpanda:9092',
         kafka_topic_list = 'spike.trades',
         kafka_group_name = 'ch_trades',
         kafka_format = 'AvroConfluent',
         format_avro_schema_registry_url = 'http://redpanda:8081',
         kafka_thread_per_consumer = 1,
         kafka_num_consumers = 2;

CREATE TABLE spike.q_book
(
    exchange    String,
    symbol      String,
    recv_ts_ns  Int64,
    seq         Int64,
    checksum_ok Bool,
    depth       Int32,
    bid_px      Array(Int64),
    bid_qty     Array(Int64),
    ask_px      Array(Int64),
    ask_qty     Array(Int64)
) ENGINE = Kafka
SETTINGS kafka_broker_list = 'redpanda:9092',
         kafka_topic_list = 'spike.book',
         kafka_group_name = 'ch_book',
         kafka_format = 'AvroConfluent',
         format_avro_schema_registry_url = 'http://redpanda:8081';

CREATE TABLE spike.trades
(
    exchange String, symbol String, exchange_ts DateTime64(6), recv_ts_ns Int64,
    price Int64, qty Int64, side Enum8('buy' = 1, 'sell' = 2), trade_id String, seq Int64,
    _partition UInt64, _offset UInt64, _timestamp Nullable(DateTime),
    hdr_names Array(String), hdr_values Array(String)
) ENGINE = MergeTree ORDER BY (symbol, seq);

CREATE TABLE spike.book
(
    exchange String, symbol String, recv_ts_ns Int64, seq Int64, checksum_ok Bool, depth Int32,
    bid_px Array(Int64), bid_qty Array(Int64), ask_px Array(Int64), ask_qty Array(Int64),
    _partition UInt64, _offset UInt64, _timestamp Nullable(DateTime),
    hdr_names Array(String), hdr_values Array(String)
) ENGINE = MergeTree ORDER BY (symbol, seq);

CREATE MATERIALIZED VIEW spike.mv_trades TO spike.trades AS
SELECT *, _partition, _offset, _timestamp,
       _headers.name AS hdr_names, _headers.value AS hdr_values
FROM spike.q_trades;

CREATE MATERIALIZED VIEW spike.mv_book TO spike.book AS
SELECT *, _partition, _offset, _timestamp,
       _headers.name AS hdr_names, _headers.value AS hdr_values
FROM spike.q_book;

-- ═══════════════════════════════════════════════════════════════════════════
-- K2 v3 — ClickHouse `gold`: the served tier (ADR-026, plan 004 Phase E).
--
-- This file is the CONTRACT and is the only ClickHouse DDL CI applies
-- (scripts/clickhouse-schema-test.sh). 20-gold-kafka.sql, applied at boot only,
-- attaches the Redpanda feeds to the tables declared here. The two are split so
-- CI can assert the tables' semantics on a throwaway server with no broker.
--
-- Two feeds, one contract. The Avro `market.crypto.v3.trades.*` / `book.*`
-- topics give freshness (the last minutes, via the Kafka engines); the lake's
-- gold tables give history and correctness (a pull, docs/runbooks/
-- clickhouse-rebuild-from-lake.md, when the lake gold layer lands). The lake
-- wins on conflict: a reload from it is the source of truth, the topics are
-- the head start. ReplacingMergeTree makes the overlap idempotent.
--
-- No TTL, anywhere. Gold is served indefinitely from here (ADR-026 supersedes
-- ADR-025's 7-day clause); growth is watched by ClickHouseDiskHigh.
--
-- Numbers are the wire's fixed point: `*_e8` Int64 at 1e-8 (ADR-020), exact and
-- cheap to store; `price` / `qty` are ALIAS columns that produce the exact
-- Decimal on read (`toDecimal128(x, 10) / toDecimal128(100000000, 0)` is exact
-- on 24.3: 7843677000000 -> 78436.7700000000, int64 max -> 92233720368.54775807,
-- measured 2026-08-27). Aggregates that multiply price by quantity are Float64
-- on purpose: a Decimal(38,20) product overflows on a SHIB-sized quantity and
-- a quote-volume sum is not a price.
--
-- v2's `k2` database is frozen beside this one until the cutover drops it.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS gold;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.trades — one row per logical trade, every venue, one schema.
--
-- ORDER BY is the logical trade: (exchange, canonical_symbol, exchange_ts,
-- trade_id). A venue replay (Coinbase re-sends recent trades on subscribe and
-- inside a live subscription — docker/lake/ddl/lake.sql) is the same key with
-- a later recv_ts_ns, and ReplacingMergeTree collapses it. The version column
-- `first_seen` is inverted receive time, so the row that survives a merge is
-- the EARLIEST delivery — the one whose lineage names the frame that actually
-- carried the trade first, as the lake's gold layer will also decide.
-- ReplacingMergeTree keeps the max version and needs an unsigned type, hence
-- the subtraction from UInt64 max rather than a negative Int64.
--
-- Reads that must be exact use FINAL (the `quant` profile sets
-- do_not_merge_across_partitions_select_final so FINAL stays cheap on a
-- monthly partitioning). Counts without FINAL are counts of deliveries.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.trades
(
    exchange         LowCardinality(String)  COMMENT 'binance | kraken | coinbase',
    symbol           String                  COMMENT 'Venue-native symbol, as sent',
    canonical_symbol LowCardinality(String)  COMMENT 'BASE/QUOTE uppercase (config/instruments.yaml)',
    trade_id         String                  COMMENT 'The venue''s own trade id, stringified',
    price_e8         Int64                   COMMENT 'Price, fixed point at 1e-8 (the wire value)',
    qty_e8           Int64                   COMMENT 'Base quantity, fixed point at 1e-8',
    price            Decimal(38, 10) ALIAS toDecimal128(price_e8, 10) / toDecimal128(100000000, 0),
    qty              Decimal(38, 10) ALIAS toDecimal128(qty_e8, 10) / toDecimal128(100000000, 0),
    side             Enum8('buy' = 1, 'sell' = 2) COMMENT 'Taker side',
    exchange_ts      DateTime64(6, 'UTC')    COMMENT 'Venue clock, microseconds',
    recv_ts_ns       Int64                   COMMENT 'K2 receive clock, nanoseconds',
    seq              Int64                   COMMENT 'Venue sequence; 0 where the stream is unsequenced',
    conn_id          String                  COMMENT 'WebSocket connection episode',
    conn_msg_seq     Int64                   COMMENT 'K2 frame counter on conn_id',
    src_topic        LowCardinality(String)  COMMENT 'Lineage: Kafka topic the row was read from (or the lake table on reload)',
    src_partition    UInt64                  COMMENT 'Lineage',
    src_offset       UInt64                  COMMENT 'Lineage',
    first_seen       UInt64 DEFAULT 18446744073709551615 - toUInt64(recv_ts_ns)
                                             COMMENT 'Version for ReplacingMergeTree: larger = received earlier, so the first delivery wins'
)
ENGINE = ReplacingMergeTree(first_seen)
PARTITION BY toYYYYMM(exchange_ts)
ORDER BY (exchange, canonical_symbol, exchange_ts, trade_id)
SETTINGS index_granularity = 8192;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.book_top20 — the 1 Hz top-20 L2 snapshots, one row per (venue, symbol,
-- second). The capture samples at 1 Hz already; ORDER BY the second and
-- ReplacingMergeTree on the sampler clock make a second sample in the same
-- second — a reconnect, a replay from the lake — collapse to the LATEST one,
-- which is the book as it stood at the end of that second.
--
-- Levels are the wire's four parallel Int64 arrays. ClickHouse 24.3's
-- AvroConfluent lands `array<long>` straight into Array(Int64) (spike S4);
-- the pairing is positional and the BBO view below reads index 1.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.book_top20
(
    exchange         LowCardinality(String),
    symbol           String,
    canonical_symbol LowCardinality(String),
    depth            UInt8                   COMMENT 'Levels present per side; below 20 is a thin book, not dropped levels',
    seq              Int64                   COMMENT 'Venue sequence of the last update folded in; 0 where unsequenced',
    checksum_ok      Nullable(Bool)          COMMENT 'Kraken only; NULL = the venue publishes no checksum',
    bid_px           Array(Int64)            COMMENT 'Best first, fixed point 1e-8',
    bid_qty          Array(Int64),
    ask_px           Array(Int64)            COMMENT 'Best first (ascending)',
    ask_qty          Array(Int64),
    exchange_ts      Nullable(DateTime64(6, 'UTC')) COMMENT 'NULL for Binance (its partial-depth stream carries no venue time)',
    recv_ts_ns       Int64,
    snapshot_ts_ns   Int64                   COMMENT 'The sampler clock, nanoseconds — authoritative',
    snapshot_ts      DateTime64(6, 'UTC') DEFAULT fromUnixTimestamp64Micro(intDiv(snapshot_ts_ns, 1000)),
    second           DateTime('UTC')      DEFAULT toDateTime(intDiv(snapshot_ts_ns, 1000000000), 'UTC')
                                             COMMENT 'The 1 Hz bucket; the ORDER BY key',
    conn_id          String,
    conn_msg_seq     Int64,
    src_topic        LowCardinality(String),
    src_partition    UInt64,
    src_offset       UInt64,
    ver              UInt64 DEFAULT toUInt64(snapshot_ts_ns) COMMENT 'Version: the later sample in a second wins'
)
ENGINE = ReplacingMergeTree(ver)
PARTITION BY toYYYYMM(second)
ORDER BY (exchange, canonical_symbol, second)
SETTINGS index_granularity = 8192;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.feed_errors — every Kafka record the feeds could not decode, with its
-- bytes. The served tier's counterpart of lake.audit.checks'
-- unresolvable_schema_id: the feed skips the record (kafka_handle_error_mode =
-- 'stream' in 20-gold-kafka.sql) and this is where "what did it skip" is
-- answered. ClickHouseKafkaRowsRejected alerts on the count.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.feed_errors
(
    seen_at    DateTime64(6, 'UTC'),
    topic      LowCardinality(String),
    partition  UInt64,
    offset     UInt64,
    error      String,
    raw        String COMMENT 'The Kafka value, verbatim'
)
ENGINE = MergeTree
ORDER BY (topic, partition, offset);

-- ───────────────────────────────────────────────────────────────────────────
-- gold.ohlcv_live — OHLCV computed ON READ over the deduplicated trades, for
-- any bucket: `SELECT * FROM gold.ohlcv_live(bucket = 60) WHERE ...`.
--
-- This is the v2 post-mortem made structural. v2 materialised candles into a
-- SummingMergeTree whose open/close were argMin/argMax *within each insert
-- block*, so a minute that arrived in two blocks kept whichever block's open
-- happened to survive the merge — a wrong number that looked right. A view
-- over FINAL sees the whole minute every time. open/close are decided by
-- (exchange_ts, recv_ts_ns, trade id) — the same total order the lake's
-- gold.py uses, so the two compare at tolerance zero; the id breaks the tie
-- for trades one frame delivered at one instant. scripts/clickhouse-schema-test.sh
-- inserts a minute in two blocks and asserts the open comes from the earlier
-- one. The materialised `gold.ohlcv_*` tables land with the lake's gold layer
-- and are loaded from it, never computed here.
-- ───────────────────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS gold.ohlcv_live AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfInterval(exchange_ts, INTERVAL {bucket:UInt32} SECOND) AS window_start,
    argMin(price_e8, (exchange_ts, recv_ts_ns, toUInt64OrZero(trade_id))) AS open_e8,
    max(price_e8)                                  AS high_e8,
    min(price_e8)                                  AS low_e8,
    argMax(price_e8, (exchange_ts, recv_ts_ns, toUInt64OrZero(trade_id))) AS close_e8,
    toDecimal128(open_e8,  10) / toDecimal128(100000000, 0) AS open,
    toDecimal128(high_e8,  10) / toDecimal128(100000000, 0) AS high,
    toDecimal128(low_e8,   10) / toDecimal128(100000000, 0) AS low,
    toDecimal128(close_e8, 10) / toDecimal128(100000000, 0) AS close,
    sum(qty)                                       AS volume,
    sum(toFloat64(price_e8) * toFloat64(qty_e8)) / 1e16 AS quote_volume,
    count()                                        AS trade_count,
    min(exchange_ts)                               AS open_time,
    max(exchange_ts)                               AS close_time
FROM gold.trades FINAL
GROUP BY exchange, canonical_symbol, window_start;

-- ───────────────────────────────────────────────────────────────────────────
-- gold.ohlcv_{1m,5m,1h,1d} — the candles the LAKE computed (lake.gold.ohlcv_*,
-- docker/lake/gold.py), loaded by pull (docs/runbooks/clickhouse-rebuild-from-
-- lake.md). Never computed here: gold.ohlcv_live above is for the head, these
-- are the record.
--
-- The version is `computed_at`, the wall clock of the Spark run that produced
-- the row (docker/lake/gold.py stamps it on every candle), NOT
-- `src_snapshot_id`: an Iceberg snapshot id is a random 64-bit number, so a
-- ReplacingMergeTree keyed on it keeps an arbitrary one of the two rows on a
-- re-pull rather than the newer one. `src_snapshot_id` stays as a plain
-- lineage column — "which trades went into this candle" — and answers nothing
-- about ordering.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.ohlcv_1m
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(6, 'UTC'),
    open_e8          Int64,
    high_e8          Int64,
    low_e8           Int64,
    close_e8         Int64,
    open             Decimal(38, 10) ALIAS toDecimal128(open_e8,  10) / toDecimal128(100000000, 0),
    high             Decimal(38, 10) ALIAS toDecimal128(high_e8,  10) / toDecimal128(100000000, 0),
    low              Decimal(38, 10) ALIAS toDecimal128(low_e8,   10) / toDecimal128(100000000, 0),
    close            Decimal(38, 10) ALIAS toDecimal128(close_e8, 10) / toDecimal128(100000000, 0),
    volume           Decimal(38, 10),
    quote_volume     Decimal(38, 10),
    trade_count      UInt64,
    open_time        DateTime64(6, 'UTC'),
    close_time       DateTime64(6, 'UTC'),
    src_snapshot_id  UInt64                  COMMENT 'Lineage only: the lake gold.trades snapshot the candle was computed from (a random id, never an order)',
    computed_at      DateTime64(6, 'UTC')    COMMENT 'The version: when the lake computed this row; the later recompute wins'
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (exchange, canonical_symbol, window_start);

CREATE TABLE IF NOT EXISTS gold.ohlcv_5m
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(6, 'UTC'),
    open_e8          Int64,
    high_e8          Int64,
    low_e8           Int64,
    close_e8         Int64,
    open             Decimal(38, 10) ALIAS toDecimal128(open_e8,  10) / toDecimal128(100000000, 0),
    high             Decimal(38, 10) ALIAS toDecimal128(high_e8,  10) / toDecimal128(100000000, 0),
    low              Decimal(38, 10) ALIAS toDecimal128(low_e8,   10) / toDecimal128(100000000, 0),
    close            Decimal(38, 10) ALIAS toDecimal128(close_e8, 10) / toDecimal128(100000000, 0),
    volume           Decimal(38, 10),
    quote_volume     Decimal(38, 10),
    trade_count      UInt64,
    open_time        DateTime64(6, 'UTC'),
    close_time       DateTime64(6, 'UTC'),
    src_snapshot_id  UInt64                  COMMENT 'Lineage only: the lake gold.trades snapshot the candle was computed from (a random id, never an order)',
    computed_at      DateTime64(6, 'UTC')    COMMENT 'The version: when the lake computed this row; the later recompute wins'
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (exchange, canonical_symbol, window_start);

CREATE TABLE IF NOT EXISTS gold.ohlcv_1h
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(6, 'UTC'),
    open_e8          Int64,
    high_e8          Int64,
    low_e8           Int64,
    close_e8         Int64,
    open             Decimal(38, 10) ALIAS toDecimal128(open_e8,  10) / toDecimal128(100000000, 0),
    high             Decimal(38, 10) ALIAS toDecimal128(high_e8,  10) / toDecimal128(100000000, 0),
    low              Decimal(38, 10) ALIAS toDecimal128(low_e8,   10) / toDecimal128(100000000, 0),
    close            Decimal(38, 10) ALIAS toDecimal128(close_e8, 10) / toDecimal128(100000000, 0),
    volume           Decimal(38, 10),
    quote_volume     Decimal(38, 10),
    trade_count      UInt64,
    open_time        DateTime64(6, 'UTC'),
    close_time       DateTime64(6, 'UTC'),
    src_snapshot_id  UInt64                  COMMENT 'Lineage only: the lake gold.trades snapshot the candle was computed from (a random id, never an order)',
    computed_at      DateTime64(6, 'UTC')    COMMENT 'The version: when the lake computed this row; the later recompute wins'
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (exchange, canonical_symbol, window_start);

CREATE TABLE IF NOT EXISTS gold.ohlcv_1d
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(6, 'UTC'),
    open_e8          Int64,
    high_e8          Int64,
    low_e8           Int64,
    close_e8         Int64,
    open             Decimal(38, 10) ALIAS toDecimal128(open_e8,  10) / toDecimal128(100000000, 0),
    high             Decimal(38, 10) ALIAS toDecimal128(high_e8,  10) / toDecimal128(100000000, 0),
    low              Decimal(38, 10) ALIAS toDecimal128(low_e8,   10) / toDecimal128(100000000, 0),
    close            Decimal(38, 10) ALIAS toDecimal128(close_e8, 10) / toDecimal128(100000000, 0),
    volume           Decimal(38, 10),
    quote_volume     Decimal(38, 10),
    trade_count      UInt64,
    open_time        DateTime64(6, 'UTC'),
    close_time       DateTime64(6, 'UTC'),
    src_snapshot_id  UInt64                  COMMENT 'Lineage only: the lake gold.trades snapshot the candle was computed from (a random id, never an order)',
    computed_at      DateTime64(6, 'UTC')    COMMENT 'The version: when the lake computed this row; the later recompute wins'
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (exchange, canonical_symbol, window_start);

-- ───────────────────────────────────────────────────────────────────────────
-- gold.bars — the lake's event bars (lake.gold.bars, docker/lake/bars.py):
-- tick, volume and dollar at config/bars.yaml's one canonical threshold per
-- symbol. Loaded by pull like the candles, never computed here; a re-pull of a
-- recomputed day replaces that day's rows, versioned on `computed_at` for the
-- reason given above the candles. `threshold` rides on every row so a bar is
-- self-describing after the config moves.
--
-- **Raising a threshold in config/bars.yaml shrinks a day's bar count**, and a
-- pull-based ReplacingMergeTree cannot express a deletion: the orphaned rows at
-- the high `bar_seq` end of every day stay, with their old `computed_at`, and
-- nothing collapses them because no new row shares their key. After a
-- threshold change the reload is `TRUNCATE TABLE gold.bars` then re-pull the
-- whole table, not an incremental pull (docs/runbooks/
-- clickhouse-rebuild-from-lake.md §3).
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.bars
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    bar_kind         LowCardinality(String),
    threshold        Decimal(38, 10),
    day              Date,
    bar_seq          Int32,
    open_e8          Int64,
    high_e8          Int64,
    low_e8           Int64,
    close_e8         Int64,
    open             Decimal(38, 10) ALIAS toDecimal128(open_e8,  10) / toDecimal128(100000000, 0),
    high             Decimal(38, 10) ALIAS toDecimal128(high_e8,  10) / toDecimal128(100000000, 0),
    low              Decimal(38, 10) ALIAS toDecimal128(low_e8,   10) / toDecimal128(100000000, 0),
    close            Decimal(38, 10) ALIAS toDecimal128(close_e8, 10) / toDecimal128(100000000, 0),
    volume_e8        Int64,
    quote_volume_e8  Int64,
    volume           Decimal(38, 10) ALIAS toDecimal128(volume_e8, 10) / toDecimal128(100000000, 0),
    quote_volume     Decimal(38, 10) ALIAS toDecimal128(quote_volume_e8, 10) / toDecimal128(100000000, 0),
    trade_count      UInt64,
    open_time        DateTime64(6, 'UTC'),
    close_time       DateTime64(6, 'UTC'),
    src_snapshot_id  UInt64                  COMMENT 'Lineage only: the lake gold.trades snapshot the day was computed from (a random id, never an order)',
    computed_at      DateTime64(6, 'UTC')    COMMENT 'The version: when the lake computed this day''s bars; the later recompute wins'
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (exchange, canonical_symbol, bar_kind, day, bar_seq);

-- ───────────────────────────────────────────────────────────────────────────
-- gold.bbo_1s — the lake's per-second BBO (lake.gold.bbo_1s, projected from
-- lake.gold.book_top20 which is replayed from every venue frame). Loaded by
-- pull like the candles; gold.bbo_live below is the same arithmetic on the
-- topic-fed book_top20 for the head.
--
-- Still versioned on `src_snapshot_id`, unlike the candles and bars above:
-- lake.gold.bbo_1s carries no `computed_at` (docker/lake/books.py projects it
-- straight from gold.book_top20), so there is no monotone column to switch to.
-- A re-pull of a recomputed second therefore keeps an arbitrary one of the two
-- rows. Revisit when lake.gold.bbo_1s gains a computed_at.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.bbo_1s
(
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    second           DateTime('UTC'),
    bid_e8           Int64,
    bid_qty_e8       Int64,
    ask_e8           Int64,
    ask_qty_e8       Int64,
    bid              Decimal(38, 10) ALIAS toDecimal128(bid_e8, 10) / toDecimal128(100000000, 0),
    ask              Decimal(38, 10) ALIAS toDecimal128(ask_e8, 10) / toDecimal128(100000000, 0),
    mid              Float64,
    spread_bps       Float64,
    imbalance        Float64,
    microprice       Float64,
    checksum_ok      Nullable(Bool),
    src_snapshot_id  UInt64
)
ENGINE = ReplacingMergeTree(src_snapshot_id)
PARTITION BY toYYYYMM(second)
ORDER BY (exchange, canonical_symbol, second);

-- ───────────────────────────────────────────────────────────────────────────
-- gold.bbo_live — best bid/offer and the three derived numbers a desk asks
-- for first, straight off the top level of each 1 Hz snapshot. Float64 for
-- the derived values: they are ratios, not prices. Int64 products of an e8
-- price and an e8 quantity overflow (1e13 x 1e10), so the arithmetic is done
-- in Float64 after the cast, not before.
-- ───────────────────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS gold.bbo_live AS
SELECT
    exchange,
    canonical_symbol,
    second,
    snapshot_ts,
    bid_px[1]  AS bid_e8,
    bid_qty[1] AS bid_qty_e8,
    ask_px[1]  AS ask_e8,
    ask_qty[1] AS ask_qty_e8,
    toDecimal128(bid_e8, 10) / toDecimal128(100000000, 0) AS bid,
    toDecimal128(ask_e8, 10) / toDecimal128(100000000, 0) AS ask,
    (toFloat64(bid_e8) + toFloat64(ask_e8)) / 2e8                                   AS mid,
    (toFloat64(ask_e8) - toFloat64(bid_e8)) / ((toFloat64(bid_e8) + toFloat64(ask_e8)) / 2) * 10000 AS spread_bps,
    toFloat64(bid_qty_e8) / (toFloat64(bid_qty_e8) + toFloat64(ask_qty_e8))         AS imbalance,
    (toFloat64(bid_e8) * toFloat64(ask_qty_e8) + toFloat64(ask_e8) * toFloat64(bid_qty_e8))
        / (toFloat64(bid_qty_e8) + toFloat64(ask_qty_e8)) / 1e8                    AS microprice
FROM gold.book_top20 FINAL
WHERE depth > 0;

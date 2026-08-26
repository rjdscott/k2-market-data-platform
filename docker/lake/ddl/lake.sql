-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 v3 lake — the four Iceberg tables, applied by docker/lake/apply_ddl.py
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
-- The logical trade is still (exchange, symbol, trade_id) — that is what a
-- research query deduplicates on, and docker/lake/maintenance.py reports the
-- cross-conn_id count on every run so the replay rate stays a number.
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

ALTER TABLE lake.bronze.trades SET IDENTIFIER FIELDS exchange, symbol, trade_id, conn_id;

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

-- ───────────────────────────────────────────────────────────────────────────
-- audit.checks — one row per check per maintenance run. Append-only history, so
-- "when did offset continuity last hold for market.crypto.v3.raw.kraken/7" is a
-- query rather than a log grep. docker/lake/metrics.py reads it for
-- k2_lake_audit_failures_total. docker/lake/ingest.py also writes here, with
-- job='ingest', when it meets a schema id the registry cannot serve.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lake.audit.checks (
    run_ts     TIMESTAMP NOT NULL COMMENT 'When the maintenance run that produced this row started',
    job        STRING  NOT NULL COMMENT 'Which job wrote it: maintenance | ingest | verify',
    check_name STRING  NOT NULL COMMENT 'offset_continuity | duplicate_identifiers | sequence_gaps | venue_replay (informational, no pass/fail) | unresolvable_schema_id (written by ingest, not maintenance)',
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

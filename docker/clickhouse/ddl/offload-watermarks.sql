-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Offload Watermarks Table
-- Purpose: Track exactly-once semantics for ClickHouse → Iceberg offload
-- Execution: Run in ClickHouse (not Iceberg)
-- Version: v2.0 (ADR-014)
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Watermark Table: Prevents Duplicate Reads (Exactly-Once Semantics)
-- ============================================================================

-- Design Philosophy:
-- - Track last successfully offloaded timestamp/sequence per table
-- - Enable incremental reads (only new data since last watermark)
-- - Prevent duplicates (update watermark only after successful Iceberg write)
-- - Enable retry safety (failed jobs re-read from last success)
-- - Audit trail (last_successful_run shows when offload completed)

CREATE TABLE IF NOT EXISTS offload_watermarks (
    table_name String COMMENT 'Source ClickHouse table name (e.g., bronze_trades_binance)',

    -- Watermark Fields (Exactly-Once Semantics)
    last_offload_timestamp DateTime64(6) COMMENT 'Last successfully offloaded timestamp (microsecond precision)',
    last_offload_max_sequence Int64 COMMENT 'Last successfully offloaded sequence number (for deduplication)',
    last_offload_row_count Int64 COMMENT 'Number of rows offloaded in last successful run',

    -- Status Tracking
    status String COMMENT 'Current offload status: success, running, failed',
    last_successful_run DateTime COMMENT 'Timestamp of last successful offload completion',
    last_run_duration_seconds Int32 COMMENT 'Duration of last offload job (for monitoring)',

    -- Error Handling
    failure_count Int32 DEFAULT 0 COMMENT 'Consecutive failure count (reset on success)',
    last_error_message String DEFAULT '' COMMENT 'Last error message (for debugging)',

    -- Metadata
    created_at DateTime DEFAULT now() COMMENT 'When watermark entry was first created',
    updated_at DateTime DEFAULT now() COMMENT 'Last update timestamp'
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (table_name)
COMMENT 'Iceberg offload watermarks - ensures exactly-once semantics';

-- ============================================================================
-- Initialize Watermarks for All Tables
-- ============================================================================

-- Bronze: Binance Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status, last_successful_run)
SELECT
    'bronze_trades_binance' AS table_name,
    toDateTime64('1970-01-01 00:00:00', 6) AS last_offload_timestamp,
    0 AS last_offload_max_sequence,
    'initialized' AS status,
    now() AS last_successful_run
WHERE NOT EXISTS (SELECT 1 FROM offload_watermarks WHERE table_name = 'bronze_trades_binance');

-- Bronze: Kraken Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status, last_successful_run)
SELECT
    'bronze_trades_kraken' AS table_name,
    toDateTime64('1970-01-01 00:00:00', 6) AS last_offload_timestamp,
    0 AS last_offload_max_sequence,
    'initialized' AS status,
    now() AS last_successful_run
WHERE NOT EXISTS (SELECT 1 FROM offload_watermarks WHERE table_name = 'bronze_trades_kraken');

-- Silver: Unified Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status, last_successful_run)
SELECT
    'silver_trades' AS table_name,
    toDateTime64('1970-01-01 00:00:00', 6) AS last_offload_timestamp,
    0 AS last_offload_max_sequence,
    'initialized' AS status,
    now() AS last_successful_run
WHERE NOT EXISTS (SELECT 1 FROM offload_watermarks WHERE table_name = 'silver_trades');

-- Gold: OHLCV Tables (6 timeframes)
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status, last_successful_run)
SELECT
    table_name,
    toDateTime64('1970-01-01 00:00:00', 6) AS last_offload_timestamp,
    0 AS last_offload_max_sequence,
    'initialized' AS status,
    now() AS last_successful_run
FROM (
    SELECT 'ohlcv_1m' AS table_name UNION ALL
    SELECT 'ohlcv_5m' UNION ALL
    SELECT 'ohlcv_15m' UNION ALL
    SELECT 'ohlcv_30m' UNION ALL
    SELECT 'ohlcv_1h' UNION ALL
    SELECT 'ohlcv_1d'
) AS gold_tables
WHERE table_name NOT IN (SELECT table_name FROM offload_watermarks);

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- List all watermarks:
-- SELECT * FROM offload_watermarks ORDER BY table_name;

-- Check last successful offload times:
-- SELECT
--     table_name,
--     last_offload_timestamp,
--     last_successful_run,
--     last_run_duration_seconds,
--     status
-- FROM offload_watermarks
-- ORDER BY last_successful_run DESC;

-- Check failed offloads:
-- SELECT * FROM offload_watermarks
-- WHERE status = 'failed' OR failure_count > 0
-- ORDER BY failure_count DESC;

-- ============================================================================
-- Usage Pattern (PySpark Offload Jobs)
-- ============================================================================

-- Step 1: Read watermark before offload
-- SELECT last_offload_timestamp, last_offload_max_sequence
-- FROM offload_watermarks
-- WHERE table_name = 'bronze_trades_binance';

-- Step 2: Read incremental data from ClickHouse
-- SELECT * FROM bronze_trades_binance
-- WHERE exchange_timestamp > :last_offload_timestamp
-- AND exchange_timestamp <= now() - INTERVAL 5 MINUTE  -- Buffer for late arrivals
-- ORDER BY exchange_timestamp, sequence_number;

-- Step 3: Write to Iceberg (PySpark handles this)

-- Step 4: Update watermark on success
-- INSERT INTO offload_watermarks (
--     table_name,
--     last_offload_timestamp,
--     last_offload_max_sequence,
--     last_offload_row_count,
--     status,
--     last_successful_run,
--     last_run_duration_seconds,
--     failure_count,
--     updated_at
-- )
-- VALUES (
--     'bronze_trades_binance',
--     :max_timestamp_written,
--     :max_sequence_written,
--     :row_count,
--     'success',
--     now(),
--     :duration_seconds,
--     0,  -- Reset failure count on success
--     now()
-- );

-- ============================================================================
-- Exactly-Once Guarantee Explanation
-- ============================================================================

-- Scenario: Offload job crashes mid-execution

-- WITHOUT watermark (broken):
--   Run 1: Read trades 1-1000 → Write to Iceberg → CRASH (no record of success)
--   Run 2: Read trades 1-1000 again → Write to Iceberg → DUPLICATES!

-- WITH watermark (correct):
--   Run 1: Read watermark (last=0) → Read trades 1-1000 → Write to Iceberg → CRASH (watermark NOT updated)
--   Run 2: Read watermark (last=0, unchanged) → Read trades 1-1000 again → Iceberg deduplicates → Update watermark to 1000
--   Run 3: Read watermark (last=1000) → Read trades 1001-2000 → Write to Iceberg → Update watermark to 2000

-- Key insight: Watermark updated ONLY after successful Iceberg write (atomic commit).
-- If job fails before watermark update, next run re-reads same data (idempotent).
-- Iceberg's MERGE operation deduplicates based on primary key (trade_id + exchange).

-- ============================================================================
-- Design Notes
-- ============================================================================

-- ReplacingMergeTree:
--   - Automatically deduplicates by ORDER BY (table_name)
--   - Keeps latest version based on updated_at
--   - Ensures only one watermark per table_name

-- DateTime64(6):
--   - Microsecond precision (matches ClickHouse exchange_timestamp)
--   - Enables precise incremental reads

-- 5-Minute Buffer:
--   - Prevents data loss from ClickHouse TTL (30 days)
--   - Handles late-arriving trades (network delays, clock skew)
--   - Ensures complete data capture before expiry

-- Failure Count:
--   - Tracks consecutive failures for alerting
--   - Reset to 0 on successful run
--   - Can trigger alerts if failure_count > 3

-- Status Field:
--   - 'initialized': Watermark created, no offload yet
--   - 'running': Offload job currently executing
--   - 'success': Last offload completed successfully
--   - 'failed': Last offload failed (see last_error_message)


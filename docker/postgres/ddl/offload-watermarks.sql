-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Offload Watermarks Table (PostgreSQL)
-- Purpose: Track exactly-once semantics for ClickHouse → Iceberg offload
-- Database: PostgreSQL (Prefect metadata DB)
-- Version: v2.0 (ADR-014 - Best Practice: Separate Metadata DB)
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Design Philosophy: Separation of Concerns
-- ============================================================================
--
-- Why PostgreSQL (not ClickHouse)?
-- --------------------------------
-- ✅ Industry standard: Operational metadata separate from analytical data
-- ✅ ACID guarantees: Transactional consistency for watermark updates
-- ✅ Independence: Watermarks survive ClickHouse schema changes
-- ✅ Multi-source: One metadata DB for all sources (ClickHouse, Postgres, etc.)
-- ✅ Zero cost: Already running PostgreSQL for Prefect
--
-- Alternative approaches (NOT recommended):
-- ❌ ClickHouse: Mixing operational metadata with analytical data
-- ❌ Prefect KV: Tied to orchestrator, harder to query
-- ❌ Iceberg: Complex to manage, not designed for this

-- ============================================================================
-- Watermark Table: Exactly-Once Semantics
-- ============================================================================

CREATE TABLE IF NOT EXISTS offload_watermarks (
    -- Primary Key
    table_name TEXT PRIMARY KEY,  -- Source table name (e.g., 'bronze_trades_binance')

    -- Watermark Fields (Exactly-Once Semantics)
    last_offload_timestamp TIMESTAMPTZ NOT NULL,  -- Last successfully offloaded timestamp
    last_offload_max_sequence BIGINT NOT NULL,    -- Last successfully offloaded sequence number
    last_offload_row_count BIGINT DEFAULT 0,      -- Rows offloaded in last successful run

    -- Status Tracking
    status TEXT NOT NULL DEFAULT 'initialized',   -- 'initialized', 'running', 'success', 'failed'
    last_successful_run TIMESTAMPTZ,              -- When last successful offload completed
    last_run_duration_seconds INTEGER,            -- Duration of last offload job (for monitoring)

    -- Error Handling
    failure_count INTEGER DEFAULT 0,              -- Consecutive failures (reset on success)
    last_error_message TEXT,                      -- Last error (for debugging)

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),         -- When watermark entry created
    updated_at TIMESTAMPTZ DEFAULT NOW()          -- Last update timestamp
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_watermarks_status ON offload_watermarks(status);
CREATE INDEX IF NOT EXISTS idx_watermarks_last_run ON offload_watermarks(last_successful_run DESC);

-- ============================================================================
-- Initialize Watermarks for All Tables (9 total)
-- ============================================================================

-- Bronze Layer: Binance Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('bronze_trades_binance', '1970-01-01 00:00:00+00', 0, 'initialized')
ON CONFLICT (table_name) DO NOTHING;

-- Bronze Layer: Kraken Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('bronze_trades_kraken', '1970-01-01 00:00:00+00', 0, 'initialized')
ON CONFLICT (table_name) DO NOTHING;

-- Bronze Layer: Coinbase Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('bronze_trades_coinbase', '1970-01-01 00:00:00+00', 0, 'initialized')
ON CONFLICT (table_name) DO NOTHING;

-- Silver Layer: Unified Trades
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('silver_trades', '1970-01-01 00:00:00+00', 0, 'initialized')
ON CONFLICT (table_name) DO NOTHING;

-- Gold Layer: OHLCV Tables (6 timeframes)
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES
    ('ohlcv_1m', '1970-01-01 00:00:00+00', 0, 'initialized'),
    ('ohlcv_5m', '1970-01-01 00:00:00+00', 0, 'initialized'),
    ('ohlcv_15m', '1970-01-01 00:00:00+00', 0, 'initialized'),
    ('ohlcv_30m', '1970-01-01 00:00:00+00', 0, 'initialized'),
    ('ohlcv_1h', '1970-01-01 00:00:00+00', 0, 'initialized'),
    ('ohlcv_1d', '1970-01-01 00:00:00+00', 0, 'initialized')
ON CONFLICT (table_name) DO NOTHING;

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
-- ORDER BY last_successful_run DESC NULLS LAST;

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

-- Step 2: Read incremental data from ClickHouse (PySpark handles this)

-- Step 3: Write to Iceberg (PySpark handles this)

-- Step 4: Update watermark on success (transactional)
-- BEGIN;
-- UPDATE offload_watermarks
-- SET
--     last_offload_timestamp = :max_timestamp,
--     last_offload_max_sequence = :max_sequence,
--     last_offload_row_count = :row_count,
--     status = 'success',
--     last_successful_run = NOW(),
--     last_run_duration_seconds = :duration,
--     failure_count = 0,
--     updated_at = NOW()
-- WHERE table_name = 'bronze_trades_binance';
-- COMMIT;

-- ============================================================================
-- Exactly-Once Guarantee Explanation
-- ============================================================================

-- Scenario: Offload job crashes mid-execution

-- WITHOUT watermark (broken):
--   Run 1: Read trades 1-1000 → Write to Iceberg → CRASH (no record)
--   Run 2: Read trades 1-1000 again → Write to Iceberg → DUPLICATES!

-- WITH PostgreSQL watermark (correct):
--   Run 1: Read watermark (last=0)
--        → BEGIN TRANSACTION
--        → Read trades 1-1000
--        → Write to Iceberg
--        → CRASH before COMMIT (watermark NOT updated - rolled back)
--   Run 2: Read watermark (last=0, unchanged)
--        → Read trades 1-1000 again
--        → Write to Iceberg (Iceberg deduplicates by trade_id)
--        → UPDATE watermark to 1000
--        → COMMIT (atomic)
--   Run 3: Read watermark (last=1000)
--        → Read trades 1001-2000
--        → Write to Iceberg
--        → UPDATE watermark to 2000
--        → COMMIT

-- Key Guarantees:
-- 1. PostgreSQL ACID: Watermark update is atomic (all-or-nothing)
-- 2. Iceberg ACID: Table commits are atomic
-- 3. Idempotent: Re-reading same data is safe (Iceberg deduplicates)
-- 4. No data loss: 5-minute buffer prevents ClickHouse TTL from deleting before offload

-- ============================================================================
-- Advantages Over ClickHouse Watermarks
-- ============================================================================

-- PostgreSQL:
-- ✅ ACID transactions (true atomicity)
-- ✅ Standard SQL (easier to query/debug)
-- ✅ Separation of concerns (metadata ≠ analytics)
-- ✅ Better for auditing (centralized metadata)
-- ✅ Multi-source support (add MySQL, Postgres sources later)

-- ClickHouse (old approach):
-- ❌ ReplacingMergeTree (eventually consistent, not transactional)
-- ❌ Mixing metadata with analytical data (violation of separation of concerns)
-- ❌ Harder to query (ClickHouse dialect, FINAL keyword needed)

-- ============================================================================
-- Design Notes
-- ============================================================================

-- Primary Key:
--   - table_name: One watermark per source table
--   - PostgreSQL enforces uniqueness (no duplicates)

-- TIMESTAMPTZ:
--   - Timezone-aware timestamps (UTC recommended)
--   - Matches ClickHouse DateTime64 precision

-- Transactional Updates:
--   - Use BEGIN/COMMIT for atomic watermark updates
--   - Ensures consistency even if job crashes mid-execution

-- 5-Minute Buffer:
--   - last_offload_timestamp + 5 minutes prevents ClickHouse TTL race
--   - Handles late-arriving trades (network delays, clock skew)

-- Failure Count:
--   - Tracks consecutive failures for alerting
--   - Reset to 0 on successful run
--   - Alert if failure_count > 3

-- Status Field:
--   - 'initialized': Watermark created, no offload yet
--   - 'running': Offload job currently executing (optional)
--   - 'success': Last offload completed successfully
--   - 'failed': Last offload failed (see last_error_message)

-- ============================================================================
-- Best Practices
-- ============================================================================

-- 1. Always wrap watermark updates in transactions:
--    BEGIN; UPDATE watermarks ...; COMMIT;

-- 2. Update watermark ONLY after successful Iceberg write:
--    if iceberg_write_success:
--        update_watermark()

-- 3. Use consistent timezone (UTC):
--    SET timezone = 'UTC';

-- 4. Regular maintenance:
--    - Monitor failure_count for persistent issues
--    - Archive old watermark history (optional)

-- 5. Alerting:
--    - Alert if last_successful_run > 30 minutes ago
--    - Alert if failure_count > 3 for any table

-- ============================================================================
-- Migration from ClickHouse (if needed)
-- ============================================================================

-- If migrating from ClickHouse watermarks:
-- 1. Read last watermark from ClickHouse
-- 2. Insert into PostgreSQL
-- 3. Verify consistency
-- 4. Switch offload jobs to PostgreSQL
-- 5. Drop ClickHouse watermark table

-- Example:
-- INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
-- SELECT table_name, last_offload_timestamp, last_offload_max_sequence, status
-- FROM clickhouse_watermarks;

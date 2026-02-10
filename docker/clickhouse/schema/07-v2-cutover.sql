-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - V2 Schema Cutover
-- Purpose: Complete migration by removing v1 and renaming v2 as primary
-- WARNING: This is a destructive operation - ensure v2 is validated first!
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- PRE-CUTOVER VALIDATION (Run these manually first!)
-- ============================================================================

-- 1. Verify v2 has recent data:
-- SELECT max(timestamp) FROM k2.silver_trades;
-- -- Should be within last minute

-- 2. Compare v1 vs v2 counts for same window:
-- SELECT 'v1' as v, count() FROM k2.silver_trades WHERE exchange_timestamp > now() - INTERVAL 5 MINUTE
-- UNION ALL
-- SELECT 'v2', count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 5 MINUTE;
-- -- Counts should match

-- 3. Verify Gold layer is using v2:
-- SELECT table, create_table_query FROM system.tables
-- WHERE database='k2' AND name LIKE 'ohlcv%mv' AND create_table_query LIKE '%silver_trades%';
-- -- Should return 6 MVs

-- 4. Check v2 schema correctness:
-- SELECT * FROM k2.silver_trades ORDER BY timestamp DESC LIMIT 1 FORMAT Vertical;
-- -- Verify: message_id (UUID), asset_class (crypto), currency (USDT), vendor_data populated

-- ============================================================================
-- CUTOVER STEPS (Run only after validation passes!)
-- ============================================================================

-- Step 1: Drop old Silver MV (stops dual-write to v1)
DROP VIEW IF EXISTS k2.bronze_trades_mv;

-- Step 2: Rename old Silver table to archive
RENAME TABLE k2.silver_trades TO k2.silver_trades_v1_archive;

-- Step 3: Rename v2 to become the primary Silver table
RENAME TABLE k2.silver_trades TO k2.silver_trades;

-- Step 4: Rename v2 MV to become the primary MV
RENAME TABLE k2.bronze_trades_mv_v2 TO k2.bronze_trades_mv;

-- ============================================================================
-- POST-CUTOVER VALIDATION
-- ============================================================================

-- 1. Verify Silver table exists:
-- SELECT count() FROM k2.silver_trades;

-- 2. Verify new data flowing:
-- SELECT count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 1 MINUTE;
-- -- Should see new trades

-- 3. Verify Gold layer still working:
-- SELECT count() FROM k2.ohlcv_1m WHERE window_start > now() - INTERVAL 10 MINUTE;
-- -- Should see recent candles

-- 4. Check table structure:
-- DESCRIBE TABLE k2.silver_trades;
-- -- Should see v2 schema (message_id, asset_class, currency, etc.)

-- ============================================================================
-- CLEANUP (Optional - after confirming v2 is stable for 24+ hours)
-- ============================================================================

-- Drop archived v1 table:
-- DROP TABLE IF EXISTS k2.silver_trades_v1_archive;

-- ============================================================================
-- ROLLBACK PROCEDURE (If something goes wrong)
-- ============================================================================

-- 1. Restore v1 as primary:
-- RENAME TABLE k2.silver_trades TO k2.silver_trades;
-- RENAME TABLE k2.silver_trades_v1_archive TO k2.silver_trades;

-- 2. Recreate v1 MV (use 02-silver-gold-layers.sql)

-- 3. Update Gold MVs back to v1 (use 03-ohlcv-simple.sql)

-- ============================================================================
-- NOTES
-- ============================================================================

-- Migration completed: YYYY-MM-DD
-- V1 archived: silver_trades_v1_archive
-- V2 promoted: silver_trades (primary)
-- Validation window: Check first 24 hours after cutover
-- Cleanup scheduled: 24-48 hours after successful cutover

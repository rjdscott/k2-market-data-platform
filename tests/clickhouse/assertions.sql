-- Each statement prints exactly `ok` or a FAIL line. scripts/clickhouse-schema-test.sh
-- runs them after loading the three fixture files and fails on any line that is not ok.
-- Fixtures: tests/clickhouse/trades_block{1,2}.jsonl (one minute of BTC/USDT in two
-- insert blocks, the earlier trade in the LATER block; trade t2 delivered twice), book.jsonl
-- (two BTC/USDT snapshots in one second, an empty Kraken book), bars.jsonl (one bars key
-- pulled twice, the recompute carrying the SMALLER src_snapshot_id).

-- 1. The v2 regression. OHLCV of a minute that arrived in two insert blocks: the open is
--    the earliest trade by exchange_ts (t0, 99, second block), the close the latest (t3, 101),
--    and t2's replay is counted once. v2's SummingMergeTree kept the first block's open.
SELECT if(open = 99 AND high = 103 AND low = 99 AND close = 101 AND trade_count = 4 AND volume = 4 AND quote_volume = 403,
          'ok', concat('FAIL ohlcv: ', toString((open, high, low, close, trade_count, volume, quote_volume))))
FROM gold.ohlcv_live(bucket = 60) WHERE canonical_symbol = 'BTC/USDT';

-- 2. FINAL deduplicates a replayed trade; without FINAL it is a delivery count.
SELECT if((SELECT count() FROM gold.trades FINAL) = 5 AND (SELECT count() FROM gold.trades) = 6,
          'ok', concat('FAIL dedup: final=', toString((SELECT count() FROM gold.trades FINAL)), ' raw=', toString((SELECT count() FROM gold.trades))));

-- 3. The delivery that survives is the EARLIEST received (first_seen inverted recv_ts_ns):
--    t2 from connection c1 at offset 12, not the replay from c2 at offset 14.
SELECT if(conn_id = 'c1' AND src_offset = 12, 'ok', concat('FAIL winner: ', conn_id, ' offset ', toString(src_offset)))
FROM gold.trades FINAL WHERE trade_id = 't2';

-- 4. The Decimal aliases are exact conversions of the fixed point.
SELECT if(price = toDecimal128(2485.79, 10) AND qty = toDecimal128(0.00221133, 10), 'ok', concat('FAIL decimal: ', toString(price), ' ', toString(qty)))
FROM gold.trades FINAL WHERE trade_id = 'k1';

-- 5. Two snapshots in one second collapse to the later one (ver = snapshot_ts_ns).
SELECT if((SELECT count() FROM gold.book_top20 FINAL WHERE canonical_symbol = 'BTC/USDT') = 1
          AND (SELECT src_offset FROM gold.book_top20 FINAL WHERE canonical_symbol = 'BTC/USDT') = 901,
          'ok', 'FAIL book downsample');

-- 6. BBO math off the surviving snapshot: bid 100.05 x 3, ask 100.15 x 1.
SELECT if(bid = toDecimal128(100.05, 10) AND ask = toDecimal128(100.15, 10)
          AND round(mid, 6) = 100.1 AND round(spread_bps, 4) = 9.99 AND round(imbalance, 4) = 0.75 AND round(microprice, 6) = 100.125,
          'ok', concat('FAIL bbo: ', toString((bid, ask, mid, spread_bps, imbalance, microprice))))
FROM gold.bbo_live WHERE canonical_symbol = 'BTC/USDT';

-- 7. An empty book (depth 0) is not a BBO.
SELECT if((SELECT count() FROM gold.bbo_live WHERE canonical_symbol = 'ETH/USD') = 0, 'ok', 'FAIL empty book produced a BBO');

-- 8. No TTL anywhere in gold (ADR-026).
SELECT if((SELECT count() FROM system.tables WHERE database = 'gold' AND engine LIKE '%MergeTree' AND create_table_query LIKE '%TTL%') = 0, 'ok', 'FAIL a gold table has a TTL');

-- 9. The config is the one applied: the default profile's per-query memory cap.
SELECT if((SELECT value FROM system.settings WHERE name = 'max_memory_usage') = '6000000000', 'ok', concat('FAIL max_memory_usage=', (SELECT value FROM system.settings WHERE name = 'max_memory_usage')));

-- 10. gold.bars versions on computed_at, not on the Iceberg snapshot id. Both rows are the
--     same key; the second pull is the later computed_at (02:00) and the SMALLER
--     src_snapshot_id (1e18 vs 9e18), because snapshot ids are random 64-bit numbers.
--     FINAL must keep the recompute.
SELECT if((SELECT count() FROM gold.bars FINAL) = 1 AND close_e8 = 20000000000 AND trade_count = 4,
          'ok', concat('FAIL bars version: rows=', toString((SELECT count() FROM gold.bars FINAL)), ' close_e8=', toString(close_e8)))
FROM gold.bars FINAL WHERE canonical_symbol = 'BTC/USD' AND bar_kind = 'dollar';

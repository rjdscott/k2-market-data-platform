# ClickHouse Database Standard

**Status**: ✅ Active
**Last Updated**: 2026-02-12
**Decision**: Use `default` database for all K2 platform data

## Standard Database: `default`

All K2 Market Data Platform tables reside in the `default` ClickHouse database.

### Active Tables (default database)

**Bronze Layer** (Raw data from Kafka):
```sql
default.bronze_trades_binance       -- Binance raw trades
default.bronze_trades_binance_queue -- Kafka consumer (Binance)
default.bronze_trades_kraken        -- Kraken raw trades
default.bronze_trades_kraken_queue  -- Kafka consumer (Kraken)
```

**Silver Layer** (Normalized & unified):
```sql
default.silver_trades               -- Unified normalized trades
default.silver_trades_binance_mv    -- Materialized view (Binance)
default.silver_trades_kraken_mv     -- Materialized view (Kraken)
```

**Gold Layer** (OHLCV aggregations):
```sql
default.ohlcv_1m / ohlcv_1m_mv     -- 1-minute candles
default.ohlcv_5m / ohlcv_5m_mv     -- 5-minute candles
default.ohlcv_1h / ohlcv_1h_mv     -- 1-hour candles
default.ohlcv_1d / ohlcv_1d_mv     -- 1-day candles
```

## Historical Context

### Why `default` instead of `k2`?

**Original Plan**: Use dedicated `k2` database for isolation

**What Happened**:
- Implementation naturally went to `default` database
- All production data accumulated in `default`
- No issues or limitations encountered
- `k2` database only had one orphaned table (no data flow)

**Decision (2026-02-12)**:
- Standardize on `default` database
- Drop `k2` database entirely
- Update all documentation to reflect reality

### Benefits of `default`

1. **Simplicity**: Standard ClickHouse convention
2. **No Migration Needed**: Data already there (800K+ trades)
3. **Proven**: Working pipeline validated over weeks
4. **Less Confusion**: One database, clear ownership

## Usage in Queries

### ✅ Correct
```sql
-- Always use default. prefix or implicit default
SELECT * FROM default.silver_trades;
SELECT * FROM silver_trades;  -- implicit default
```

### ❌ Incorrect
```sql
-- Do NOT use k2 database
SELECT * FROM k2.silver_trades;  -- WRONG - k2 database dropped
```

## Usage in Documentation

When documenting ClickHouse queries:
- **Always**: Use `default.table_name` for clarity
- **Avoid**: Assuming readers know implicit default behavior
- **Update**: Any historical docs referencing `k2.*`

## Usage in Code

**Python/Application Code**:
```python
# Explicit database in queries
query = "SELECT * FROM default.silver_trades WHERE ..."

# Or configure default database in connection
client = clickhouse_connect.get_client(
    host='clickhouse',
    database='default'  # Explicit
)
```

**Docker Compose**:
```yaml
environment:
  CLICKHOUSE_DB: default          # Use default
  CLICKHOUSE_USER: default        # Standard user
  CLICKHOUSE_PASSWORD: clickhouse  # Standard password
```

## Migration from Legacy Docs

If you find documentation referencing `k2.` tables:
1. Replace `k2.bronze_trades` → `default.bronze_trades_binance` or `default.bronze_trades_kraken`
2. Replace `k2.silver_trades` → `default.silver_trades`
3. Replace `k2.ohlcv_*` → `default.ohlcv_*`
4. Replace `k2.bronze_trades_mv` → `default.bronze_trades_binance_mv`

## Schema Files

**Active Implementation**: Uses `default` database
**Reference Files**: `docker/clickhouse/schema/*-fixed.sql`
**Historical Files**: `docker/clickhouse/schema/0*.sql` (reference `k2` database)

See [docker/clickhouse/schema/README.md](../../docker/clickhouse/schema/README.md) for details.

## Verification

Check current database state:
```bash
# List all databases
docker exec k2-clickhouse clickhouse-client --query "SHOW DATABASES"

# List tables in default
docker exec k2-clickhouse clickhouse-client --query "SHOW TABLES FROM default"

# Verify data exists
docker exec k2-clickhouse clickhouse-client --query "
SELECT
    'default' as db,
    table,
    total_rows
FROM system.tables
WHERE database = 'default'
  AND engine NOT LIKE '%View'
  AND total_rows > 0
ORDER BY table
"
```

Expected output:
```
default | bronze_trades_binance | 800000+
default | bronze_trades_kraken  | 4000+
default | silver_trades         | 700000+
default | ohlcv_1m             | 200+
...
```

## See Also

- [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) - Updated with `default` database
- [DATA-INSPECTION.md](./DATA-INSPECTION.md) - Updated queries
- [docker/clickhouse/schema/README.md](../../docker/clickhouse/schema/README.md) - Schema evolution

## Decision Record

**Decision 2026-02-12**: Standardize on `default` ClickHouse database
**Reason**: All production data already in `default`, simpler, no benefit to separate database
**Cost**: Updated ~30 documentation files
**Alternative**: Migrate 800K+ records to `k2` database (rejected - unnecessary work, no benefit)
**Impact**: Eliminated confusion, clearer operational procedures

# ClickHouse Schema Files

**Status**: ⚠️ **Historical Reference Only**
**Last Updated**: 2026-02-12
**Active Implementation**: Uses `default` database (see note below)

## Important Note on Database Usage

### Historical Context

These schema files were originally designed to use a `k2` database in ClickHouse. However, during implementation, the actual working pipeline was built in the `default` database instead.

### Current State (2026-02-12)

**Active Pipeline** (✅ Production):
```
Database: default
Tables:
  - bronze_trades_binance (Binance raw data)
  - bronze_trades_kraken (Kraken raw data)
  - silver_trades (unified normalized trades)
  - ohlcv_1m/5m/1h/1d (OHLCV aggregations)
```

**Schema Files in This Directory**:
- Reference the `k2` database (historical design)
- NOT used for container initialization
- Kept for reference and understanding design evolution

### Actual Implementation Files

The working implementation uses:
- **Bronze/Silver/Gold layers**: Created manually via clickhouse-client
- **Location**: `default` database in ClickHouse
- **Initialization**: DDL files in `/docker/clickhouse/ddl/` (only offload-watermarks.sql)

## File Reference

### Original Design Files (k2 database)
- `01-bronze-layer.sql` - Bronze layer with k2 database
- `02-silver-gold-layers.sql` - Silver/Gold layers
- `03-ohlcv-simple.sql` - Basic OHLCV
- `04-ohlcv-additional-timeframes.sql` - Additional timeframes
- `08-bronze-kraken.sql` - Kraken bronze layer

### Fixed Files (default database)
- `01-bronze-layer-fixed.sql` - Working bronze implementation
- `02-silver-gold-fixed.sql` - Working silver/gold implementation
- `08-bronze-kraken-fixed.sql` - Working Kraken implementation

### Migration Files
- `05-silver-v2-migration.sql` - V1→V2 migration
- `06-gold-layer-v2-migration.sql` - Gold layer migration
- `07-v2-cutover.sql` - Cutover procedures
- `09-silver-kraken-to-v2.sql` - Kraken V2 migration
- `10-silver-binance.sql` - Binance silver layer

## Usage Guidelines

**For New Development**:
1. Use `default` database in all queries
2. Reference `-fixed.sql` files for table structures
3. Do NOT create tables in `k2` database

**For Documentation**:
1. All docs should reference `default` database
2. Update any references to `k2.table_name` → `default.table_name`

**For Historical Reference**:
1. Original `k2` database files show initial design intent
2. Compare with `-fixed.sql` to see evolution

## See Also

- [../ddl/](../ddl/) - Files actually run on container init
- [../../docs/operations/QUICK-REFERENCE.md](../../docs/operations/QUICK-REFERENCE.md) - Operational commands
- [../../docs/operations/DATA-INSPECTION.md](../../docs/operations/DATA-INSPECTION.md) - Data inspection guide

## Decision Record

**Decision 2026-02-12**: Standardize on `default` database
**Reason**: All production data already in `default`, no benefit to `k2` database
**Cost**: Need to update documentation references
**Alternative**: Migrate to `k2` database (rejected - unnecessary work, no benefit)

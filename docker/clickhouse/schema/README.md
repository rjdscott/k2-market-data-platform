# ClickHouse Schema Files

**Status**: ✅ **Active - Uses `k2` Database**
**Last Updated**: 2026-02-12 (Migrated to k2)
**Active Implementation**: Uses `k2` database (production standard)

## Important Note on Database Usage

### Historical Context

These schema files were originally designed to use a `k2` database in ClickHouse. However, during implementation, the actual working pipeline was built in the `default` database instead.

### Current State (2026-02-12)

**Active Pipeline** (✅ Production):
```
Database: k2
Tables:
  - bronze_trades_binance (Binance raw data) - 1.1M+ records
  - bronze_trades_kraken (Kraken raw data) - 6K+ records
  - silver_trades (unified normalized trades) - 1.0M+ records
  - ohlcv_1m/5m/1h/1d (OHLCV aggregations) - 300+ candles
```

**Schema Files in This Directory**:
- Originally designed for `k2` database
- Briefly implemented in `default` database
- **Now**: Migrated back to `k2` database (2026-02-12)
- Kept for reference and understanding design evolution

### Actual Implementation

The working implementation uses:
- **Bronze/Silver/Gold layers**: Created via clickhouse-client
- **Location**: `k2` database in ClickHouse (✅ Production standard)
- **Initialization**: Tables created during migration, DDL files reference only

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
1. Use `k2` database in all queries
2. Reference `-fixed.sql` files for table structures (update to use k2 prefix)
3. Always use explicit `k2.table_name` syntax

**For Documentation**:
1. All docs should reference `k2` database
2. Update any references to `default.table_name` → `k2.table_name`

**For Historical Reference**:
1. Original `k2` database files show initial design intent
2. Compare with `-fixed.sql` to see evolution

## See Also

- [../ddl/](../ddl/) - Files actually run on container init
- [../../docs/operations/QUICK-REFERENCE.md](../../docs/operations/QUICK-REFERENCE.md) - Operational commands
- [../../docs/operations/DATA-INSPECTION.md](../../docs/operations/DATA-INSPECTION.md) - Data inspection guide

## Decision Record

**Decision 2026-02-12**: Migrate to `k2` database
**Reason**: Production best practice, isolation, security, organization
**Cost**: 30-minute migration, documentation updates
**Alternative**: Stay with `default` (rejected - not production-ready)
**Result**: 1.1M records migrated successfully, pipeline operational

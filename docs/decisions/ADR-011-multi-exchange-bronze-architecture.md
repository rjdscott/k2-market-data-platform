# ADR-011: Multi-Exchange Bronze Architecture

**Status**: Accepted
**Date**: 2026-02-10
**Context**: Platform v2 - Kraken Exchange Integration

## Context

As we expand from Binance-only to multi-exchange support (Kraken, potentially others), we need to decide how to handle exchange-specific data formats in the Bronze layer while maintaining a unified Silver/Gold layer.

**Key Challenge**: Different exchanges have different native formats:
- **Binance**: `BTCUSDT`, `trade_id` provided, millisecond timestamps
- **Kraken**: `XBT/USD`, no `trade_id`, "seconds.microseconds" timestamps, different field names

**Previous Approach (v1)**: Single unified Bronze table attempted to normalize all exchanges immediately, resulting in:
- Loss of native exchange metadata
- Difficult debugging (can't verify against exchange docs)
- Fragile transformations embedded in feed handlers

## Decision

**Adopt separate Bronze tables per exchange, preserving native schemas.**

### Architecture Pattern

```
┌─────────────────────────────────────────────────────────────────────┐
│ BRONZE LAYER (Exchange-Native Schemas)                             │
├─────────────────────────────────────────────────────────────────────┤
│  bronze_trades_binance         bronze_trades_kraken                │
│  ├─ symbol: "BTCUSDT"          ├─ pair: "XBT/USD" ← Native!        │
│  ├─ trade_id: 12345            ├─ timestamp: "1737.321597"         │
│  ├─ is_buyer_maker: true       ├─ side: "s"                        │
└─────────────────────────────────────────────────────────────────────┘
                              ↓↓
┌─────────────────────────────────────────────────────────────────────┐
│ SILVER LAYER (Unified Multi-Exchange Schema)                       │
├─────────────────────────────────────────────────────────────────────┤
│  silver_trades (normalized)                                      │
│  ├─ exchange: "binance" / "kraken"                                 │
│  ├─ canonical_symbol: "BTC/USD" ← Normalized from XBT              │
│  ├─ vendor_data: {"pair": "XBT/USD", ...} ← Original preserved     │
└─────────────────────────────────────────────────────────────────────┘
                              ↓↓
┌─────────────────────────────────────────────────────────────────────┐
│ GOLD LAYER (Aggregated Analytics)                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Bronze = Exchange Truth**: Each Bronze table mirrors the exchange's native format
   - Kraken: Keep `XBT/USD` (not `BTC/USD`)
   - Kraken: Keep timestamp as `"seconds.microseconds"` string
   - Binance: Keep `is_buyer_maker`, `trade_id`, etc.

2. **Silver = Normalization Layer**: Materialized views from Bronze → Silver perform:
   - Symbol normalization (XBT → BTC)
   - Timestamp conversion (string → DateTime64)
   - Enum mapping (side: 'b'/'s' → BUY/SELL)
   - Generated fields (trade_id for Kraken)
   - **vendor_data preserves original Bronze fields**

3. **Gold = Exchange-Agnostic**: OHLCV and aggregations work across all exchanges

### Implementation Example (Kraken)

**Bronze Table**:
```sql
CREATE TABLE k2.bronze_trades_kraken (
    channel_id UInt64,
    pair String,                      -- "XBT/USD" (native!)
    price String,
    volume String,
    timestamp String,                 -- "1737118158.321597"
    side Enum8('b' = 1, 's' = 2),
    order_type Enum8('l' = 1, 'm' = 2),
    ...
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (pair, timestamp, price);
```

**Silver Materialized View**:
```sql
CREATE MATERIALIZED VIEW k2.bronze_kraken_to_silver_v2_mv
TO k2.silver_trades AS
SELECT
    'kraken' AS exchange,

    -- Normalize XBT → BTC
    concat(
        if(splitByChar('/', pair)[1] = 'XBT', 'BTC', splitByChar('/', pair)[1]),
        '/',
        splitByChar('/', pair)[2]
    ) AS canonical_symbol,

    -- Convert timestamp
    fromUnixTimestamp64Micro(toUnixTimestamp64Micro(toFloat64(timestamp) * 1000000)) AS timestamp,

    -- Preserve original in vendor_data
    map(
        'pair', pair,                      -- Original "XBT/USD"
        'raw_timestamp', timestamp,        -- Original "seconds.microseconds"
        'order_type', toString(order_type),
        'channel_id', toString(channel_id)
    ) AS vendor_data,

    ...
FROM k2.bronze_trades_kraken;
```

## Rationale

### Why Separate Bronze Tables?

**Advantages**:
1. **Debuggability**: Bronze data matches exchange documentation exactly
2. **Traceability**: Can verify raw data against exchange APIs
3. **Flexibility**: Add new exchanges without touching existing ones
4. **Correctness**: Transformations happen in SQL (reviewable, testable)
5. **Performance**: Exchange-specific ORDER BY optimization

**Disadvantages** (accepted trade-offs):
1. **More tables**: Each exchange needs 3 objects (Kafka Engine, Bronze table, MV)
2. **Schema duplication**: Some fields appear in multiple Bronze schemas
3. **Migration complexity**: Can't easily change existing Bronze tables

### Why Not Normalize in Feed Handler?

We considered normalizing in Kotlin feed handlers before Kafka:

**Rejected because**:
- **Testing difficulty**: Harder to verify transformations in application code
- **Deployment coupling**: Schema changes require code deployment
- **Observability**: Can't inspect raw exchange data
- **Correctness**: SQL transformations are more verifiable than Kotlin

### Why Not Single Unified Bronze Table?

Previous v1 approach attempted single `bronze_trades` table:

**Problems**:
- **Lossy**: Had to pick one format (Binance), Kraken fields lost
- **Nullable hell**: Optional fields for different exchanges
- **Debugging nightmare**: Can't tell which exchange format a row came from
- **Type conflicts**: Different timestamp formats forced to strings

## Consequences

### Positive

1. **Clear separation of concerns**:
   - Bronze: Preserve exchange truth
   - Silver: Normalize for analysis
   - Gold: Aggregate for insights

2. **Extensibility**: Adding Coinbase/Bitfinex/etc. is straightforward:
   - Add `bronze_trades_coinbase` table
   - Add MV to `silver_trades`
   - Gold layer "just works"

3. **Data Quality**: Easy to verify Bronze vs exchange docs

4. **Performance**: Exchange-specific partitioning/ordering

### Negative

1. **Schema Maintenance**: Each exchange needs dedicated schema files
2. **Testing Overhead**: Must test each Bronze → Silver transformation
3. **Documentation**: Need clear guides for adding new exchanges

### Migration Strategy

**For existing Binance data** (optional refactor):
- Current `bronze_trades` table → rename to `bronze_trades_binance`
- Update MV to target `silver_trades`
- Backward compatible: Can keep current table if needed

**For new exchanges**:
- Always create exchange-specific Bronze table
- Always write MV to unified `silver_trades`

## Validation

Kraken integration (2026-02-10) validates this architecture:

✅ **Bronze preserves native format**:
```sql
SELECT pair FROM k2.bronze_trades_kraken;
-- Returns: "XBT/USD" (not normalized)
```

✅ **Silver normalizes**:
```sql
SELECT canonical_symbol, vendor_data['pair']
FROM k2.silver_trades WHERE exchange = 'kraken';
-- Returns: canonical_symbol = "BTC/USD", vendor_data['pair'] = "XBT/USD"
```

✅ **Gold aggregates both exchanges**:
```sql
SELECT exchange, count() FROM k2.ohlcv_1m
WHERE canonical_symbol = 'BTC/USD' GROUP BY exchange;
-- Returns: binance (1000), kraken (800)
```

## References

- **Implementation**: `docker/clickhouse/schema/08-bronze-kraken.sql`, `09-silver-kraken-to-v2.sql`
- **Testing Guide**: `docs/testing/kraken-integration-testing.md`
- **Schema V2 Guide**: `docs/architecture/schema-v2-crypto-guide.md`
- **Related ADRs**: ADR-009 (Medallion in ClickHouse)

## Decision Owners

- **Technical Lead**: RJ Scott
- **Date**: 2026-02-10
- **Review**: Post-Kraken integration (2026-02-17)

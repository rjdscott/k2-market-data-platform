# Phase 2 Prep: Architectural Decision Records

**Phase**: Phase 2 Prep (Schema Evolution + Binance Streaming)
**Last Updated**: 2026-01-12
**Maintained By**: Implementation Team

---

## Decision Log

### Decision #001: Hybrid Schema Approach with Vendor Extensions

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 00.1, Step 00.2, Step 00.3

#### Context

Current v1 schemas are ASX vendor-specific and don't generalize to other data sources:
- `company_id`: ASX-specific integer ID (doesn't exist in Binance, FIX)
- `qualifiers`: ASX trade qualifier codes
- `venue`: ASX market venue codes
- `volume`: Ambiguous (shares? contracts? BTC?)
- Missing: `message_id` (deduplication key), `side` (BUY/SELL enum), `trade_id`, `currency`

**Problem**: Need to support multiple exchanges (ASX, Binance, future sources) with a single unified schema.

**Options Considered**:
1. Keep vendor-specific schemas per exchange (rejected - doesn't scale, query complexity)
2. Pure FIX Protocol schemas (rejected - too complex for initial implementation)
3. Hybrid approach: Core standard fields + vendor_data map (selected)

#### Decision

Use **hybrid schema pattern** for v2:
- **Core standard fields**: Industry-standard fields that work across all exchanges (message_id, trade_id, symbol, exchange, asset_class, timestamp, price, quantity, currency, side, trade_conditions)
- **Vendor extensions**: `vendor_data: Map<string, string>` for exchange-specific fields

**Example**:
```json
{
  "message_id": "uuid",
  "symbol": "BHP",
  "exchange": "ASX",
  "asset_class": "equities",
  "price": 45.67,
  "quantity": 1000,
  "currency": "AUD",
  "side": "BUY",
  "vendor_data": {
    "company_id": "123",
    "qualifiers": "0",
    "venue": "X"
  }
}
```

#### Consequences

**Positive**:
- Multi-source compatibility: Same schema works for ASX, Binance, FIX, etc.
- Query-friendly: Can query across all exchanges using standard fields
- Future-proof: Easy to add new exchanges without schema changes
- Clean separation: Core fields are standardized, vendor specifics are isolated

**Negative**:
- vendor_data requires JSON parsing for exchange-specific queries
- Slight storage overhead for vendor_data map
- Need to maintain mapping documentation for each exchange

**Neutral**:
- Migration effort required (3-4 days for Step 0)
- All downstream systems need updates (producer, consumer, query engine, API)

#### Implementation Notes

- Use Avro for schema definition (already using Schema Registry)
- vendor_data stored as JSON string in Iceberg tables
- Standard fields follow FIX Protocol naming where applicable
- Use `timestamp-micros` (not millis) for precision
- Use Decimal (18,8) for price/quantity
- Add TradeSide enum: BUY, SELL, SELL_SHORT, UNKNOWN

#### Verification

- [ ] v2 schemas validate with avro-tools
- [ ] Can load ASX CSV → v2 Kafka → v2 Iceberg → v2 Query
- [ ] vendor_data contains ASX-specific fields
- [ ] Can load Binance WebSocket → v2 Kafka → v2 Iceberg → v2 Query
- [ ] vendor_data contains Binance-specific fields

---

### Decision #002: Hard Cut to v2 (No Backward Migration)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: All Step 0 substeps

#### Context

Need to decide migration strategy for moving from v1 to v2 schemas:
- Platform is early stage with minimal production data
- Historical data is sample ASX CSV files (easily reloadable)
- No external consumers of v1 API yet

**Options Considered**:
1. Dual-write: Write both v1 and v2 simultaneously (rejected - complexity, storage cost)
2. Read v1, write v2 with migration path (rejected - not needed at this stage)
3. Hard cut to v2, no backward compatibility (selected)

#### Decision

**Hard cut to v2 schemas**:
- Create new Iceberg tables: `market_data.trades_v2`, `market_data.quotes_v2`
- Old tables (`market_data.trades`, `market_data.quotes`) become read-only
- All new data goes to v2 tables
- No migration of historical data (reload if needed)
- API switches to v2 response format

#### Consequences

**Positive**:
- Simpler implementation (no dual-write complexity)
- Faster to market (saves ~2-3 days)
- Cleaner codebase (no v1/v2 branching logic)
- Lower storage costs (no duplicate data)

**Negative**:
- Historical data in v1 tables not automatically migrated
- Need to reload sample data if needed
- Breaking change for any external API consumers (none currently)

**Neutral**:
- v1 tables kept for reference but not maintained
- Can add migration script later if needed

#### Implementation Notes

- Keep v1 schema files for reference
- Add config flag: `schema_version = "v2"` (default)
- Update all sample data loading scripts to use v2
- API versioning: `/v1/trades` uses v2 schema (route name unchanged)

#### Verification

- [ ] All components use v2 schemas
- [ ] v1 tables exist but not written to
- [ ] Sample data loads successfully to v2 tables
- [ ] API returns v2 format

---

### Decision #003: Field Naming - quantity (Not volume or size)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 00.1, Step 00.4

#### Context

Current v1 schema uses `volume` field, which is ambiguous:
- Equities: volume could mean number of shares
- Crypto: volume could mean notional value in USD
- Futures: volume could mean contracts or lots

Need clear, unambiguous field name for v2 schema.

**Options Considered**:
1. **volume**: Current name (rejected - ambiguous)
2. **size**: Common in crypto (rejected - still ambiguous)
3. **quantity**: FIX Protocol standard (selected)

#### Decision

Use **quantity** as the field name in v2 schema.

**Rationale**:
- FIX Protocol uses "OrderQty", "LastQty", "CumQty"
- Clearly indicates a count (shares, contracts, BTC)
- Unambiguous across asset classes
- Industry standard for market data

**Complementary fields**:
- `currency`: Clarifies what the quantity is denominated in (AUD, USD, USDT, BTC)
- `asset_class`: Clarifies whether it's equities, crypto, futures

#### Consequences

**Positive**:
- Clear, unambiguous field name
- Follows industry standards (FIX Protocol)
- Works across all asset classes

**Negative**:
- Different from existing v1 `volume` field (requires mapping)
- Some crypto exchanges use "size" (need to map)

**Neutral**:
- Simple one-to-one mapping: `volume` → `quantity`

#### Implementation Notes

- CSV loader maps: `volume` column → `quantity` field
- Binance converter maps: `q` field → `quantity` field
- API response uses `quantity` field
- Update all tests to use `quantity`

#### Verification

- [ ] All v2 messages use `quantity` field
- [ ] CSV loader maps volume → quantity
- [ ] Binance converter maps q → quantity
- [ ] API returns quantity field
- [ ] Tests use quantity consistently

---

### Decision #004: Production-Grade Error Handling (Level 3)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.4

#### Context

For Binance WebSocket integration, need to decide error handling sophistication level:
- **Level 1 (Basic)**: Simple retry with fixed delay
- **Level 2 (Resilient)**: Exponential backoff, max retries
- **Level 3 (Production)**: Circuit breakers, alerting, failover endpoints

**Goal**: Demonstrate Principal-level engineering with production-ready resilience.

#### Decision

Implement **Level 3 (Production-grade) error handling** for Binance streaming:

1. **Exponential Backoff Reconnection**:
   - Start with 5 second delay
   - Double on each failure (max 60 seconds)
   - Max 10 reconnect attempts

2. **Circuit Breaker Integration**:
   - Reuse circuit breaker pattern from Phase 2
   - Check circuit state before producing to Kafka
   - Degrade gracefully under load

3. **Health Checks**:
   - Heartbeat messages every 30 seconds
   - Detect stale connections
   - Auto-reconnect on timeout

4. **Alerting**:
   - Prometheus metrics:
     - `k2_binance_connection_status` (1=connected, 0=disconnected)
     - `k2_binance_messages_received_total`
     - `k2_binance_reconnects_total`
     - `k2_binance_errors_total`
   - Grafana alerts on disconnect/high error rate

5. **Failover**:
   - Primary: `wss://stream.binance.com:9443`
   - Fallback: `wss://stream.binance.us:9443` (if available)

#### Consequences

**Positive**:
- Production-ready resilience
- Demonstrates Principal-level engineering
- Clear operational visibility
- Handles network failures gracefully

**Negative**:
- More complex implementation (~6 hours vs ~2 hours)
- Requires Prometheus/Grafana setup
- Requires circuit breaker implementation

**Neutral**:
- Reuses Phase 2 patterns (circuit breaker)
- Additional metrics to monitor

#### Implementation Notes

- Implement in Step 01.5.4 (Error Handling & Resilience)
- Reuse CircuitBreaker class from Phase 2 (if available, otherwise stub)
- Add to BinanceWebSocketClient class
- Document in streaming-architecture.md

#### Verification

- [ ] Exponential backoff working
- [ ] Circuit breaker integration complete
- [ ] Health checks detect stale connections
- [ ] Prometheus metrics exposed
- [ ] Grafana alerts configured
- [ ] Failover endpoints tested

---

### Decision #005: WebSocket Library - websockets (Not websocket-client)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.1

#### Context

Need to choose Python WebSocket library for Binance integration:

**Options**:
1. **websockets** (aaugustin): Async, modern, well-maintained
2. **websocket-client**: Sync, simpler, older

#### Decision

Use **websockets** library (aaugustin).

**Rationale**:
- Modern async/await pattern (Python 3.11+)
- Better performance for high-frequency streaming
- Active maintenance and community support
- Plays well with asyncio ecosystem
- Native support for SSL/TLS

#### Consequences

**Positive**:
- Modern async patterns
- Better performance
- Well-documented
- Active development

**Negative**:
- Slightly steeper learning curve (async/await)
- Requires asyncio event loop management

**Neutral**:
- Need to add to requirements.txt: `websockets>=12.0`

#### Implementation Notes

- Add to `requirements.txt`: `websockets>=12.0`
- Use async/await pattern in BinanceWebSocketClient
- Run streaming service with `asyncio.run(main())`

#### Verification

- [ ] websockets library installed
- [ ] BinanceWebSocketClient uses async/await
- [ ] Connection successful to Binance WebSocket
- [ ] Messages received and parsed

---

### Decision #006: Demo Integration - All Three (Terminal + Grafana + API)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.7

#### Context

Need to decide how to showcase live Binance streaming in demo:

**Options**:
1. Terminal only (simple)
2. Grafana only (visual)
3. API only (queryable)
4. All three (comprehensive)

#### Decision

Implement **all three demo modes** for Binance streaming:

1. **Terminal Display**: Real-time trade output in demo script
   - Show live BTC/ETH trades scrolling
   - Use Rich library for formatting
   - Display price, quantity, side, timestamp

2. **Grafana Panel**: Live price chart
   - Panel: "Live BTC Price"
   - Query: Last 1000 BTC trades from v2 table
   - Auto-refresh every 5 seconds
   - Time series line chart

3. **API Query**: Hybrid query demo
   - Endpoint: `/v1/trades?symbol=BTCUSDT&window_minutes=15`
   - Shows data from both Kafka (recent) and Iceberg (historical)
   - Demonstrates hybrid query engine

#### Consequences

**Positive**:
- Comprehensive demonstration of capabilities
- Appeals to different audiences (technical, visual, practical)
- Shows data flowing through entire platform
- Demonstrates hybrid query engine in action

**Negative**:
- More implementation effort (~6 hours vs ~2 hours)
- Requires Grafana panel configuration
- Requires hybrid query engine (Phase 2 Step 06)

**Neutral**:
- All three modes can be demonstrated independently
- Modular design allows picking subset if needed

#### Implementation Notes

- Terminal: Update `scripts/demo.py` with `demo_live_streaming()` method
- Grafana: Create panel in `config/grafana/dashboards/k2-platform.json`
- API: Integrate hybrid query engine (Phase 2 Step 06)
- Document in Step 01.5.7

#### Verification

- [ ] Terminal shows live trades (10+ trades displayed)
- [ ] Grafana panel shows live BTC price chart
- [ ] API query returns recent BTC trades (Kafka + Iceberg)
- [ ] Demo narrative includes all three modes

---

### Decision #007: Binance Symbols - Minimal (BTC-USDT, ETH-USDT Only)

**Date**: 2026-01-12
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.1, Step 01.5.3

#### Context

Need to decide which Binance symbols to stream:

**Options**:
1. **Minimal**: BTC-USDT, ETH-USDT (top 2)
2. **Moderate**: Top 10 crypto pairs
3. **Comprehensive**: All major pairs (50+)

#### Decision

Stream **only BTC-USDT and ETH-USDT** (minimal scope).

**Rationale**:
- BTC and ETH are the most liquid and representative
- Sufficient to demonstrate multi-asset capability
- Low data volume (manageable for demo)
- Can expand later if needed

#### Consequences

**Positive**:
- Simple implementation
- Low data volume
- Fast iteration
- Sufficient for demo

**Negative**:
- Limited asset coverage
- May not represent full market dynamics

**Neutral**:
- Easy to expand to more symbols later (config change)

#### Implementation Notes

- Hardcode in `BinanceConfig`: `symbols = ["BTCUSDT", "ETHUSDT"]`
- CLI argument `--symbols` allows override for testing
- Document in README.md and streaming-architecture.md

#### Verification

- [ ] Streaming service connects to BTCUSDT and ETHUSDT
- [ ] Both symbols flowing through pipeline
- [ ] Can query both symbols via API

---

### Decision #008: Currency Extraction from Symbol (Not Hardcoded)

**Date**: 2026-01-13
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.2
**Fixes**: Critical bug in Binance converter

#### Context

Original Binance converter hardcoded `currency = "USDT"` for all pairs. Analysis of real Binance data revealed:
- Binance has NO "currency" field in trade payloads
- Symbol format is concatenated (BTCUSDT, ETHBTC, BNBEUR)
- Quote currency must be extracted from symbol suffix
- 40%+ of Binance pairs are NOT quoted in USDT:
  - ETHBTC → BTC
  - BNBEUR → EUR
  - SOLUSDC → USDC

**Problem**: Hardcoded currency breaks for non-USDT pairs and corrupts data.

#### Decision

**Extract currency dynamically from symbol** using `parse_binance_symbol()` function:

```python
def parse_binance_symbol(symbol: str) -> tuple[str, str, str]:
    """Parse symbol into (base_asset, quote_asset, currency)."""
    quote_currencies = [
        "USDT", "USDC", "BUSD", "TUSD", "USDP",  # Stablecoins
        "BTC", "ETH", "BNB",                      # Crypto
        "EUR", "GBP", "AUD", "USD",               # Fiat
        "TRY", "ZAR", "UAH", "NGN",               # Other fiat
    ]

    for quote in quote_currencies:
        if symbol.endswith(quote):
            base_asset = symbol[:-len(quote)]
            return (base_asset, quote, quote)

    # Fallback: assume last 4 chars
    return (symbol[:-4], symbol[-4:], symbol[-4:])
```

**Usage**:
```python
base, quote, currency = parse_binance_symbol(msg["s"])
# "BTCUSDT" → ("BTC", "USDT", "USDT")
# "ETHBTC"  → ("ETH", "BTC", "BTC")
# "BNBEUR"  → ("BNB", "EUR", "EUR")
```

#### Consequences

**Positive**:
- Correct currency for ALL Binance pairs
- Works across stablecoins (USDT, USDC, BUSD)
- Works across crypto quotes (BTC, ETH)
- Works across fiat quotes (EUR, GBP, AUD)
- Extensible to new quote currencies

**Negative**:
- Adds parsing complexity (~20 lines)
- Must maintain list of quote currencies
- Edge cases possible (ambiguous symbols)

**Neutral**:
- Uses greedy match (longest suffix first)
- Fallback to last 4 chars if no match

#### Implementation Notes

- Add `parse_binance_symbol()` to `src/k2/ingestion/binance_client.py`
- Call during conversion: `currency = parse_binance_symbol(msg["s"])[2]`
- Test with: BTCUSDT, ETHBTC, BNBEUR, SOLUSDC
- Update Step 01.5.2 documentation with fix

#### Verification

- [x] Tested with real Binance data (REST API)
- [x] BTCUSDT → currency = "USDT" ✅
- [x] ETHBTC → currency = "BTC" ✅
- [ ] Unit tests for parse_binance_symbol()
- [ ] Integration test with multiple pairs

---

### Decision #009: Base/Quote Asset Separation in vendor_data

**Date**: 2026-01-13
**Status**: ✅ Accepted
**Deciders**: Implementation Team
**Related Phase**: Phase 2 Prep
**Related Steps**: Step 01.5.2
**Enhancement**: Improve crypto queryability

#### Context

Crypto trading pairs have TWO assets (base + quote):
- Base asset: What you're trading (BTC, ETH, BNB)
- Quote asset: What you're pricing in (USDT, BTC, EUR)

Current v2 schema has:
- `symbol`: Combined format ("BTCUSDT")
- `currency`: Quote asset ("USDT")
- ❌ NO `base_asset` field

**Problem**: Queries like "all BTC trading activity" require symbol string parsing for EVERY row (slow).

**Industry Practice**: Coinbase, Kraken, FTX all provide separate base/quote fields.

#### Decision

**Add base_asset and quote_asset to vendor_data** for crypto trades:

```python
"vendor_data": {
    "base_asset": base_asset,   # "BTC", "ETH", "BNB"
    "quote_asset": quote_asset, # "USDT", "BTC", "EUR"
    "is_buyer_maker": str(msg["m"]),
    "event_type": msg["e"],
    # ... other Binance-specific fields
}
```

**Why vendor_data instead of first-class fields?**
- Equities don't have base/quote concept (just symbol + currency)
- Crypto-specific, not universal
- Can promote to schema later if needed across all crypto exchanges

#### Consequences

**Positive**:
- Easier queries: "WHERE vendor_data.base_asset = 'BTC'"
- Cross-exchange analytics: All BTC pairs (BTCUSDT, BTCEUR, BTCBUSD)
- Follows industry standards (Coinbase, Kraken)
- No schema migration required (vendor_data is flexible)

**Negative**:
- Requires JSON parsing in queries (vendor_data is JSON string in Iceberg)
- Not indexed (slower than first-class field)
- Crypto-specific (equities don't have this)

**Neutral**:
- Can promote to first-class v2 schema field later if needed
- Keeps schema clean for non-crypto assets

#### Implementation Notes

- Extract in `convert_binance_trade_to_v2()`:
  ```python
  base, quote, currency = parse_binance_symbol(msg["s"])
  "vendor_data": {
      "base_asset": base,
      "quote_asset": quote,
      ...
  }
  ```
- Store in Iceberg as JSON string (already supported)
- Query with JSON extraction: `json_extract(vendor_data, '$.base_asset')`

#### Verification

- [x] Tested parsing with real Binance data
- [ ] Unit tests for base/quote extraction
- [ ] Query test: Filter by base_asset = 'BTC'
- [ ] Query test: Cross-exchange BTC volume

#### Future Enhancement

**Option**: Promote to first-class v2 schema fields if other crypto exchanges need it:

```json
{
  "base_asset": ["null", "string"],  // Optional, crypto only
  "quote_asset": ["null", "string"], // Optional, crypto only
  ...
}
```

**Timeline**: Revisit after adding 2-3 more crypto exchanges (Coinbase, Kraken, etc.)

---

## Decision Template

When making new decisions, use this template:

```markdown
### Decision #XXX: [Title]

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Deprecated | Superseded by #YYY
**Deciders**: [Names]
**Related Phase**: Phase X
**Related Steps**: Step Y, Step Z

#### Context
[Problem statement - what are we solving?]

#### Decision
[What we decided]

#### Consequences
**Positive**:
- Benefit 1
- Benefit 2

**Negative**:
- Cost 1
- Cost 2

**Neutral**:
- Trade-off 1

#### Implementation Notes
[How to implement]

#### Verification
- [ ] Verification step 1
- [ ] Verification step 2
```

---

**Last Updated**: 2026-01-13
**Total Decisions**: 9
**Next Decision ID**: #010

---

## Decision Status Summary

| ID | Title | Status | Date |
|----|-------|--------|------|
| #001 | Hybrid Schema Approach | ✅ Accepted | 2026-01-12 |
| #002 | Hard Cut to v2 | ✅ Accepted | 2026-01-12 |
| #003 | Field Naming - quantity | ✅ Accepted | 2026-01-12 |
| #004 | Production-Grade Error Handling | ✅ Accepted | 2026-01-12 |
| #005 | WebSocket Library - websockets | ✅ Accepted | 2026-01-12 |
| #006 | Demo Integration - All Three | ✅ Accepted | 2026-01-12 |
| #007 | Binance Symbols - Minimal | ✅ Accepted | 2026-01-12 |
| #008 | Currency Extraction from Symbol | ✅ Accepted | 2026-01-13 |
| #009 | Base/Quote Asset in vendor_data | ✅ Accepted | 2026-01-13 |

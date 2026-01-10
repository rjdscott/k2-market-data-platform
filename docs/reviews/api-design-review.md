# API Design Review: Query Parameters vs Request Bodies

**Reviewer**: Principal/Staff Data Engineer Perspective
**Date**: 2026-01-11
**Scope**: `src/k2/api/` endpoint design patterns
**Status**: Review Complete

---

## Executive Summary

The current implementation uses **GET requests with query parameters exclusively**. This is **appropriate for the current simple filtering use cases**, but **will not scale** as query complexity increases. A hybrid approach is recommended: keep GET + query params for simple lookups, introduce POST + request bodies for complex queries.

**Verdict**: Current implementation is acceptable for Phase 1, but architectural changes needed before production.

---

## Current State Analysis

### What's Implemented

| Endpoint | Method | Parameters | Complexity |
|----------|--------|------------|------------|
| `/v1/trades` | GET | 5 query params | Low |
| `/v1/quotes` | GET | 5 query params | Low |
| `/v1/summary/{symbol}/{date}` | GET | Path + 1 query | Low |
| `/v1/symbols` | GET | 1 query param | Low |
| `/v1/stats` | GET | None | Trivial |
| `/v1/snapshots` | GET | 2 query params | Low |

### Query Layer Capabilities NOT Exposed

- Point-in-time snapshot queries
- Cold-start replay with streaming
- Multi-symbol batch queries
- Complex aggregations
- Raw SQL execution

---

## Analysis: When to Use Query Parameters vs Request Bodies

### Query Parameters (GET) — Best For:

1. **Idempotent, cacheable lookups** — CDNs and proxies can cache GET responses
2. **Simple filtering** — Symbol, date range, limit, offset
3. **Bookmarkable/shareable URLs** — Users can share exact query links
4. **Browser-friendly** — Can test directly in address bar
5. **Parameter count ≤ 6-8** — URL length limits (~2000 chars safe)

### Request Bodies (POST) — Best For:

1. **Complex queries** — Multi-field filters, nested conditions, aggregations
2. **Large parameter sets** — Arrays of symbols, complex time windows
3. **Non-cacheable queries** — Point-in-time snapshots, replay streams
4. **Security-sensitive params** — Credentials shouldn't be in URLs (logged by proxies)
5. **Structured query languages** — SQL-like DSLs, GraphQL-style selection

---

## Industry Patterns at Tier-1 Trading Firms

### Bloomberg Terminal / B-PIPE API
```
POST /api/v1/market-data/subscribe
{
  "securities": ["BHP AU Equity", "RIO AU Equity"],
  "fields": ["LAST_PRICE", "BID", "ASK", "VOLUME"],
  "interval": "realtime"
}
```
- Uses POST for subscriptions (stateful)
- GET for simple reference data lookups

### Refinitiv (LSEG) Elektron
```
POST /api/rdp/data/historical-pricing/v1/views/events
{
  "universe": "/ticker/BHP.AX",
  "eventTypes": ["trade", "quote"],
  "start": "2024-01-01T00:00:00Z",
  "end": "2024-01-31T23:59:59Z",
  "count": 10000
}
```
- POST for historical data queries
- GET only for metadata/reference endpoints

### Jane Street / Optiver Internal Patterns (inferred from public talks)
- **Protobuf/gRPC** for internal low-latency paths
- **REST POST with typed schemas** for batch analytics queries
- **WebSocket** for real-time streaming
- **GET only** for health checks, config, metadata

### Key Insight

**No serious trading firm uses GET query parameters for analytical queries.** The reasons:

1. **Query complexity** — Real queries involve 20+ filters, field selection, aggregation
2. **URL length limits** — Multi-symbol queries can easily exceed 2KB
3. **Cacheability is a liability** — Stale financial data is dangerous
4. **Auditability** — POST bodies are easier to log and analyze than URL strings
5. **Type safety** — JSON schemas enable validation; query strings are stringly-typed

---

## Specific Issues with Current Implementation

### Issue 1: URL Length Risk

```python
# Current: Works fine for single symbol
GET /v1/trades?symbol=BHP&start_time=2024-01-01T00:00:00&end_time=2024-01-31T23:59:59&limit=1000

# Problem: Multi-symbol query (real-world need)
GET /v1/trades?symbols=BHP,RIO,FMG,WDS,WPL,STO,ORG,AGL,WOW,COL,WES,JBH,HVN,SUL,...
# URL can exceed 2KB with 50+ symbols
```

### Issue 2: No Field Selection

```python
# Current: Returns all fields always
GET /v1/trades?symbol=BHP&limit=100

# Better: Let clients specify what they need
POST /v1/trades/query
{
  "symbols": ["BHP"],
  "fields": ["exchange_timestamp", "price", "volume"],  # Reduces payload 60%
  "limit": 100
}
```

### Issue 3: Complex Time Windows

```python
# Current: Simple range works
GET /v1/trades?start_time=2024-01-01T09:00:00&end_time=2024-01-01T16:00:00

# Problem: Trading hours only (exclude pre/post market)
# Can't express: "09:30-16:00 on weekdays, excluding holidays"
# With POST:
{
  "time_filter": {
    "trading_hours_only": true,
    "timezone": "Australia/Sydney",
    "exclude_dates": ["2024-01-26"]  # Australia Day
  }
}
```

### Issue 4: Aggregation Queries

```python
# Current: Only pre-defined summary endpoint
GET /v1/summary/BHP/2024-01-15

# Need: Custom aggregations
POST /v1/aggregations
{
  "symbols": ["BHP", "RIO"],
  "metrics": ["vwap", "twap", "volume_profile"],
  "interval": "5min",
  "date_range": {"start": "2024-01-01", "end": "2024-01-31"}
}
```

### Issue 5: Replay/Streaming Not Exposed

The query layer has `cold_start_replay()` and `query_at_snapshot()` but no REST endpoints expose them. These require POST because:
- Snapshot IDs are opaque strings
- Replay requires stateful cursor management
- Streaming responses need chunked encoding

---

## Recommended Architecture

### Tier 1: Keep as GET (Simple Lookups)

```
GET /v1/trades?symbol=BHP&limit=100           # Quick lookup
GET /v1/quotes?symbol=BHP&limit=100           # Quick lookup
GET /v1/summary/{symbol}/{date}               # Daily OHLCV
GET /v1/symbols                               # Reference data
GET /v1/snapshots                             # Snapshot list
GET /health                                   # Health check
```

### Tier 2: Add POST (Complex Queries)

```
POST /v1/trades/query                         # Multi-symbol, field selection
POST /v1/quotes/query                         # Multi-symbol, field selection
POST /v1/aggregations                         # Custom OHLCV, VWAP, etc.
POST /v1/replay                               # Historical replay initiation
POST /v1/snapshots/{id}/query                 # Point-in-time query
```

### Request Body Schema (Example)

```python
# src/k2/api/models.py - Add these

class TradeQueryRequest(BaseModel):
    """Complex trade query request."""
    symbols: list[str] = Field(default_factory=list, max_length=100)
    exchanges: list[str] = Field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    fields: list[str] | None = None  # None = all fields
    limit: int = Field(default=1000, ge=1, le=100000)
    offset: int = Field(default=0, ge=0)

    # Advanced filters
    min_price: float | None = None
    max_price: float | None = None
    min_volume: int | None = None

    # Output options
    format: Literal["json", "csv", "parquet"] = "json"

class ReplayRequest(BaseModel):
    """Historical replay request."""
    symbol: str | None = None
    exchange: str | None = None
    start_time: datetime
    end_time: datetime
    batch_size: int = Field(default=1000, ge=100, le=10000)
    snapshot_id: str | None = None  # For point-in-time replay
```

### Endpoint Implementation (Example)

```python
# src/k2/api/v1/endpoints.py - Add this

@router.post("/trades/query", response_model=TradesResponse)
async def query_trades_advanced(
    request: TradeQueryRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> TradesResponse:
    """
    Advanced trade query with multi-symbol support and field selection.

    Use GET /v1/trades for simple single-symbol lookups.
    Use POST /v1/trades/query for complex queries.
    """
    try:
        # Multi-symbol query
        all_trades = []
        for symbol in request.symbols or [None]:
            trades = engine.query_trades(
                symbol=symbol,
                exchange=request.exchanges[0] if request.exchanges else None,
                start_time=request.start_time,
                end_time=request.end_time,
                limit=request.limit,
            )
            all_trades.extend(trades)

        # Field selection
        if request.fields:
            all_trades = [
                {k: v for k, v in t.items() if k in request.fields}
                for t in all_trades
            ]

        return TradesResponse(
            success=True,
            data=[Trade(**t) for t in all_trades[:request.limit]],
            pagination=PaginationMeta(limit=request.limit, offset=request.offset),
            meta=_get_meta(),
        )
    except Exception as e:
        logger.error("Advanced trade query failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
```

---

## Security Considerations

### Current Implementation: Acceptable

- API key in header (not query string) ✓
- No sensitive data in URLs ✓
- Rate limiting per API key ✓

### With POST Bodies: Additional Considerations

1. **Request body size limits** — Set `max_request_size` in middleware
2. **SQL injection** — Never interpolate body params into raw SQL
3. **Schema validation** — Pydantic handles this well
4. **Audit logging** — Log POST bodies (sanitized) for compliance

---

## Performance Considerations

| Aspect | GET + Query Params | POST + Body |
|--------|-------------------|-------------|
| Cacheability | Excellent (CDN, browser) | None (requires app-level) |
| Parse overhead | Minimal | ~1-2ms for JSON decode |
| Payload size | Limited (~2KB) | Unlimited (practical: 10MB) |
| Connection reuse | Excellent | Excellent |
| Streaming response | Chunked encoding | Chunked encoding |

**For market data APIs, cacheability is usually undesirable** because stale data is dangerous. POST is actually preferable.

---

## Migration Path

### Phase 1 (Current): Query Params Only
- Simple filtering works
- Good for portfolio demo
- Technical debt: limited query expressiveness

### Phase 2 (Recommended): Hybrid Approach
- Keep GET for simple lookups (cacheable, bookmarkable)
- Add POST for complex queries (type-safe, unlimited)
- Add OpenAPI schema generation for client SDKs

### Phase 3 (Production): Full Query API
- GraphQL or custom query DSL for maximum flexibility
- gRPC for internal low-latency paths
- WebSocket for real-time streaming

---

## Recommendations Summary

| Priority | Recommendation | Effort |
|----------|---------------|--------|
| **High** | Add `POST /v1/trades/query` for multi-symbol queries | Medium |
| **High** | Add `POST /v1/replay` for historical replay | Medium |
| **Medium** | Add field selection to query endpoints | Low |
| **Medium** | Add `POST /v1/snapshots/{id}/query` for time-travel | Medium |
| **Low** | Add format options (JSON, CSV, Parquet) | Low |
| **Low** | Consider GraphQL for v2 API | High |

---

## Conclusion

The current GET + query parameters implementation is **acceptable for the portfolio demo phase** but **does not reflect production patterns at Tier-1 trading firms**.

A principal/staff engineer at Optiver or Jane Street would expect:
1. POST endpoints for complex queries
2. Typed request/response schemas
3. Multi-symbol batch support
4. Field selection for bandwidth optimization
5. Streaming responses for large datasets

**Recommendation**: Implement the Tier 2 POST endpoints before presenting this as production-ready. The current implementation is fine for demonstrating capabilities, but needs the hybrid approach for a real trading system.

---

## References

- [Microsoft REST API Guidelines](https://github.com/microsoft/api-guidelines)
- [Google API Design Guide](https://cloud.google.com/apis/design)
- [Stripe API Design](https://stripe.com/docs/api) — Industry gold standard
- [Bloomberg B-PIPE Documentation](https://www.bloomberg.com/professional/product/b-pipe/)
- Internal experience with FIX protocol, ITCH/OUCH, proprietary exchange APIs

---

*Review prepared for K2 Market Data Platform*
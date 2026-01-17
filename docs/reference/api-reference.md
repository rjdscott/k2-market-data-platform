# K2 API Reference

**Last Updated**: 2026-01-14
**API Version**: v1
**Base URL**: `http://localhost:8000/api` (local), `https://api.k2.prod` (production)

---

## Overview

The K2 Market Data Platform REST API provides programmatic access to market data stored in the Apache Iceberg lakehouse. The API supports querying trades, quotes, market summaries, and historical data replay.

### Key Features

- **RESTful Design**: Standard HTTP methods (GET for simple queries, POST for complex)
- **Multiple Output Formats**: JSON (default), CSV, Parquet
- **Pagination**: Cursor-based pagination for large result sets
- **Time-Travel Queries**: Query historical snapshots (Iceberg table snapshots)
- **Aggregations**: VWAP, TWAP, OHLCV calculations
- **Rate Limiting**: 100 requests/minute per API key (configurable)

### Authentication

All API requests require an API key passed via the `X-API-Key` header.

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/trades?symbol=BHP&limit=10
```

**Note**: Phase 1-2 (localhost) does not enforce authentication. Phase 3+ will require OAuth2 + JWT tokens.

---

## Interactive API Documentation

**Swagger UI**: http://localhost:8000/docs (when platform is running)
**ReDoc**: http://localhost:8000/redoc (alternative documentation view)

The interactive documentation includes:
- All endpoints with descriptions
- Request/response schemas
- Try-it-out functionality
- Example requests and responses

---

## Common Parameters

### Query Parameters (GET endpoints)

| Parameter | Type | Required | Description | Example |
|-----------|------|----------|-------------|---------|
| `symbol` | string | No | Filter by symbol | `BHP`, `BTCUSDT` |
| `exchange` | string | No | Filter by exchange | `ASX`, `BINANCE` |
| `table_type` | string | No | Table type to query (TRADES, QUOTES) | `TRADES` |
| `start_time` | ISO 8601 | No | Filter trades after this time | `2026-01-13T00:00:00Z` |
| `end_time` | ISO 8601 | No | Filter trades before this time | `2026-01-13T23:59:59Z` |
| `limit` | integer | No | Max results to return (default: 100, max: 10000) | `1000` |
| `offset` | integer | No | Number of results to skip | `100` |
| `cursor` | string | No | Cursor for pagination (alternative to offset) | `eyJwb3NpdGlvbiI6MTAwfQ==` |

### Response Fields (All endpoints)

| Field | Type | Description |
|-------|------|-------------|
| `data` | array/object | The requested data |
| `meta` | object | Response metadata (correlation_id, timestamp, pagination) |
| `error` | object | Error details (only present if error occurred) |

---

## GET Endpoints (Simple Queries)

### GET /v1/trades

Query trade records with filters.

**Request Examples**:
```bash
# Query BHP trades from ASX
GET /api/v1/trades?symbol=BHP&exchange=ASX&start_time=2026-01-13T00:00:00Z&limit=100

# Query crypto trades from Binance (consumer must be running)
GET /api/v1/trades?symbol=BTCUSDT&exchange=BINANCE&table_type=TRADES&limit=10

# Query all recent trades (works after consumer service is started)
GET /api/v1/trades?table_type=TRADES&limit=5
```

**Query Parameters**:
- `symbol` (string, optional): Filter by symbol
- `exchange` (string, optional): Filter by exchange
- `table_type` (string, optional): Table type (TRADES, QUOTES) - use TRADES for consumer data
- `start_time` (ISO 8601, optional): Filter trades after this timestamp
- `end_time` (ISO 8601, optional): Filter trades before this timestamp
- `limit` (integer, optional): Max results (default: 100, max: 10000)
- `offset` (integer, optional): Results to skip for pagination
- `cursor` (string, optional): Cursor for pagination (alternative to offset)

**Response** (200 OK):
```json
{
  "data": [
    {
      "message_id": "550e8400-e29b-41d4-a716-446655440000",
      "trade_id": "ASX-123456",
      "symbol": "BHP",
      "exchange": "ASX",
      "price": "45.67",
      "quantity": "1000.00",
      "trade_timestamp": "2026-01-13T14:30:00.123456Z",
      "side": "BUY",
      "conditions": ["REGULAR"],
      "sequence_number": 12345,
      "venue_trade_id": "123456",
      "venue_data": {
        "market_center_id": "ASX",
        "participant_id": "PARTICIPANT001"
      }
    }
  ],
  "meta": {
    "correlation_id": "req_abc123",
    "timestamp": "2026-01-14T10:00:00.000Z",
    "pagination": {
      "limit": 100,
      "offset": 0,
      "total": 1,
      "next_cursor": null
    }
  }
}
```

**Error Responses**:
- `400 Bad Request`: Invalid parameters
- `401 Unauthorized`: Missing or invalid API key
- `429 Too Many Requests`: Rate limit exceeded
- `500 Internal Server Error`: Server error

---

### GET /v1/quotes

Query quote (bid/ask) records with filters.

**Request Examples**:
```bash
# Query ASX equities
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&limit=10"

# Query crypto trades (after starting consumer service)
curl -s "http://localhost:8000/v1/trades?table_type=TRADES&limit=5" \
  -H "X-API-Key: k2-dev-api-key-2026" | jq .

# Query specific crypto symbol
curl -s "http://localhost:8000/v1/trades?symbol=BTCUSDT&exchange=BINANCE&table_type=TRADES&limit=10" \
  -H "X-API-Key: k2-dev-api-key-2026" | jq .
```

**Query Parameters**: Same as `/v1/trades`

**Response** (200 OK):
```json
{
  "data": [
    {
      "message_id": "550e8400-e29b-41d4-a716-446655440001",
      "quote_id": "ASX-Q123456",
      "symbol": "BHP",
      "exchange": "ASX",
      "bid_price": "45.66",
      "bid_size": "5000.00",
      "ask_price": "45.68",
      "ask_size": "3000.00",
      "quote_timestamp": "2026-01-13T14:30:00.123456Z",
      "sequence_number": 12346
    }
  ],
  "meta": {
    "correlation_id": "req_abc124",
    "timestamp": "2026-01-14T10:00:01.000Z",
    "pagination": {
      "limit": 10,
      "offset": 0,
      "total": 1,
      "next_cursor": null
    }
  }
}
```

---

### GET /v1/summary/{symbol}/{date}

Get daily market summary (OHLCV) for a symbol on a specific date.

**Request**:
```bash
GET /api/v1/summary/BHP/2026-01-13
```

**Path Parameters**:
- `symbol` (string, required): Symbol to get summary for
- `date` (YYYY-MM-DD, required): Date for summary

**Response** (200 OK):
```json
{
  "data": {
    "symbol": "BHP",
    "exchange": "ASX",
    "date": "2026-01-13",
    "open": "45.50",
    "high": "46.20",
    "low": "45.30",
    "close": "45.90",
    "volume": "12500000.00",
    "vwap": "45.75",
    "trade_count": 15234,
    "first_trade_time": "2026-01-13T10:00:00.000Z",
    "last_trade_time": "2026-01-13T16:00:00.000Z"
  },
  "meta": {
    "correlation_id": "req_abc125",
    "timestamp": "2026-01-14T10:00:02.000Z"
  }
}
```

**Error Responses**:
- `404 Not Found`: No data for symbol on that date

---

### GET /v1/symbols

List all available symbols with exchange information.

**Request**:
```bash
GET /api/v1/symbols?exchange=ASX
```

**Query Parameters**:
- `exchange` (string, optional): Filter by exchange

**Response** (200 OK):
```json
{
  "data": [
    {
      "symbol": "BHP",
      "exchange": "ASX",
      "asset_class": "EQUITY",
      "first_seen": "2026-01-10T00:00:00.000Z",
      "last_seen": "2026-01-13T23:59:59.999Z",
      "trade_count": 125000,
      "quote_count": 450000
    },
    {
      "symbol": "RIO",
      "exchange": "ASX",
      "asset_class": "EQUITY",
      "first_seen": "2026-01-10T00:00:00.000Z",
      "last_seen": "2026-01-13T23:59:59.999Z",
      "trade_count": 98000,
      "quote_count": 380000
    }
  ],
  "meta": {
    "correlation_id": "req_abc126",
    "timestamp": "2026-01-14T10:00:03.000Z"
  }
}
```

---

### GET /v1/stats

Get database statistics (table sizes, record counts, date ranges).

**Request**:
```bash
GET /api/v1/stats
```

**Response** (200 OK):
```json
{
  "data": {
    "trades": {
      "record_count": 12500000,
      "table_size_mb": 1024.5,
      "earliest_timestamp": "2026-01-10T00:00:00.000Z",
      "latest_timestamp": "2026-01-13T23:59:59.999Z",
      "symbol_count": 150,
      "exchange_count": 2
    },
    "quotes": {
      "record_count": 45000000,
      "table_size_mb": 3072.8,
      "earliest_timestamp": "2026-01-10T00:00:00.000Z",
      "latest_timestamp": "2026-01-13T23:59:59.999Z",
      "symbol_count": 150,
      "exchange_count": 2
    }
  },
  "meta": {
    "correlation_id": "req_abc127",
    "timestamp": "2026-01-14T10:00:04.000Z"
  }
}
```

---

### GET /v1/snapshots

List available Iceberg table snapshots (for time-travel queries).

**Request**:
```bash
GET /api/v1/snapshots?table=trades&limit=10
```

**Query Parameters**:
- `table` (string, required): Table name (`trades` or `quotes`)
- `limit` (integer, optional): Max results (default: 10)

**Response** (200 OK):
```json
{
  "data": [
    {
      "snapshot_id": "1234567890123456789",
      "timestamp": "2026-01-13T23:59:59.999Z",
      "operation": "append",
      "summary": {
        "added-records": "125000",
        "total-records": "12500000",
        "added-data-files": "50",
        "total-data-files": "5000"
      }
    }
  ],
  "meta": {
    "correlation_id": "req_abc128",
    "timestamp": "2026-01-14T10:00:05.000Z",
    "pagination": {
      "limit": 10,
      "offset": 0,
      "total": 50
    }
  }
}
```

---

## POST Endpoints (Complex Queries)

### POST /v1/trades/query

Multi-symbol trade queries with field selection and complex filters.

**Request**:
```bash
POST /api/v1/trades/query
Content-Type: application/json

{
  "symbols": ["BHP", "RIO", "FMG"],
  "exchanges": ["ASX"],
  "start_time": "2026-01-13T10:00:00Z",
  "end_time": "2026-01-13T16:00:00Z",
  "fields": ["symbol", "price", "quantity", "trade_timestamp", "side"],
  "filters": {
    "price_min": "40.00",
    "price_max": "50.00",
    "quantity_min": "1000.00"
  },
  "order_by": ["trade_timestamp"],
  "order_direction": "asc",
  "limit": 1000,
  "output_format": "json"
}
```

**Request Body**:
- `symbols` (array of strings, optional): Symbols to query
- `exchanges` (array of strings, optional): Exchanges to query
- `start_time` (ISO 8601, optional): Start of time range
- `end_time` (ISO 8601, optional): End of time range
- `fields` (array of strings, optional): Fields to return (default: all)
- `filters` (object, optional): Additional filters (price_min, price_max, quantity_min, quantity_max, sides)
- `order_by` (array of strings, optional): Fields to sort by
- `order_direction` (string, optional): `asc` or `desc` (default: `asc`)
- `limit` (integer, optional): Max results (default: 100, max: 10000)
- `output_format` (string, optional): `json`, `csv`, or `parquet` (default: `json`)

**Response** (200 OK):
```json
{
  "data": [
    {
      "symbol": "BHP",
      "price": "45.67",
      "quantity": "1000.00",
      "trade_timestamp": "2026-01-13T10:05:30.123456Z",
      "side": "BUY"
    }
  ],
  "meta": {
    "correlation_id": "req_abc129",
    "timestamp": "2026-01-14T10:00:06.000Z",
    "query_execution_time_ms": 123,
    "rows_scanned": 125000,
    "rows_returned": 1
  }
}
```

**CSV Output** (`output_format: "csv"`):
```csv
symbol,price,quantity,trade_timestamp,side
BHP,45.67,1000.00,2026-01-13T10:05:30.123456Z,BUY
```

**Parquet Output** (`output_format: "parquet"`):
Returns binary Parquet file with `Content-Type: application/octet-stream`

---

### POST /v1/quotes/query

Multi-symbol quote queries (same structure as `/v1/trades/query`).

**Request**: Same structure as `/v1/trades/query`, but for quote data.

---

### POST /v1/replay

Historical data replay with pagination (useful for backtesting).

**Request**:
```bash
POST /api/v1/replay
Content-Type: application/json

{
  "data_type": "trades",
  "symbols": ["BTCUSDT"],
  "exchanges": ["BINANCE"],
  "start_time": "2026-01-13T00:00:00Z",
  "end_time": "2026-01-13T01:00:00Z",
  "replay_speed": 1.0,
  "page_size": 1000,
  "cursor": null
}
```

**Request Body**:
- `data_type` (string, required): `trades` or `quotes`
- `symbols` (array of strings, required): Symbols to replay
- `exchanges` (array of strings, optional): Exchanges to filter
- `start_time` (ISO 8601, required): Replay start time
- `end_time` (ISO 8601, required): Replay end time
- `replay_speed` (float, optional): Speed multiplier (1.0 = real-time, 0 = as fast as possible)
- `page_size` (integer, optional): Records per page (default: 1000, max: 10000)
- `cursor` (string, optional): Cursor for next page

**Response** (200 OK):
```json
{
  "data": [
    {
      "trade_id": "BINANCE-123456",
      "symbol": "BTCUSDT",
      "exchange": "BINANCE",
      "price": "45678.90",
      "quantity": "0.05",
      "trade_timestamp": "2026-01-13T00:00:05.123Z",
      "side": "BUY"
    }
  ],
  "meta": {
    "correlation_id": "req_abc130",
    "timestamp": "2026-01-14T10:00:07.000Z",
    "pagination": {
      "page_size": 1000,
      "cursor": "eyJwb3NpdGlvbiI6MTAwMCwidGltZXN0YW1wIjoiMjAyNi0wMS0xM1QwMDowMDoxMC4xMjNaIn0=",
      "has_more": true,
      "total_records_returned": 1000,
      "replay_progress_pct": 0.5
    }
  }
}
```

---

### POST /v1/snapshots/{id}/query

Query a specific Iceberg table snapshot (time-travel query).

**Request**:
```bash
POST /api/v1/snapshots/1234567890123456789/query
Content-Type: application/json

{
  "symbols": ["BHP"],
  "limit": 100
}
```

**Path Parameters**:
- `id` (string, required): Snapshot ID from `/v1/snapshots`

**Request Body**: Same as `/v1/trades/query`

**Response**: Same as `/v1/trades/query`, but data is from the specified snapshot

---

### POST /v1/aggregations

Custom aggregations (VWAP, TWAP, OHLCV) with flexible bucketing.

**Request**:
```bash
POST /api/v1/aggregations
Content-Type: application/json

{
  "data_type": "trades",
  "symbols": ["BHP", "RIO"],
  "exchanges": ["ASX"],
  "start_time": "2026-01-13T10:00:00Z",
  "end_time": "2026-01-13T16:00:00Z",
  "interval": "5min",
  "metrics": ["vwap", "twap", "ohlcv", "volume"],
  "bucket_by": "symbol"
}
```

**Request Body**:
- `data_type` (string, required): `trades` or `quotes`
- `symbols` (array of strings, required): Symbols to aggregate
- `exchanges` (array of strings, optional): Exchanges to filter
- `start_time` (ISO 8601, required): Aggregation start
- `end_time` (ISO 8601, required): Aggregation end
- `interval` (string, required): `1min`, `5min`, `15min`, `1hour`, `1day`
- `metrics` (array of strings, required): `vwap`, `twap`, `ohlcv`, `volume`, `trade_count`
- `bucket_by` (string, optional): `symbol`, `exchange`, or `symbol_exchange` (default: `symbol`)

**Response** (200 OK):
```json
{
  "data": [
    {
      "symbol": "BHP",
      "exchange": "ASX",
      "bucket_start": "2026-01-13T10:00:00Z",
      "bucket_end": "2026-01-13T10:05:00Z",
      "open": "45.50",
      "high": "45.70",
      "low": "45.45",
      "close": "45.67",
      "volume": "125000.00",
      "vwap": "45.58",
      "twap": "45.59",
      "trade_count": 234
    }
  ],
  "meta": {
    "correlation_id": "req_abc131",
    "timestamp": "2026-01-14T10:00:08.000Z",
    "query_execution_time_ms": 456
  }
}
```

---

## Rate Limiting

**Limits**:
- **Anonymous** (localhost only): 1000 requests/minute
- **Authenticated**: 100 requests/minute per API key (default)
- **Premium tier**: 1000 requests/minute per API key

**Rate Limit Headers**:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1705228860
```

**429 Too Many Requests Response**:
```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Please retry after 60 seconds.",
    "retry_after": 60
  },
  "meta": {
    "correlation_id": "req_abc132",
    "timestamp": "2026-01-14T10:00:09.000Z"
  }
}
```

---

## Error Handling

### Standard Error Response

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {
      "field": "Additional context"
    }
  },
  "meta": {
    "correlation_id": "req_abc133",
    "timestamp": "2026-01-14T10:00:10.000Z"
  }
}
```

### Error Codes

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 400 | `INVALID_PARAMETER` | Invalid query parameter |
| 400 | `INVALID_REQUEST_BODY` | Malformed JSON or missing required fields |
| 400 | `INVALID_TIME_RANGE` | start_time > end_time or range too large |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `FORBIDDEN` | API key lacks required permissions |
| 404 | `NOT_FOUND` | Resource not found (symbol, snapshot, etc.) |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Server error |
| 503 | `SERVICE_UNAVAILABLE` | Service temporarily unavailable |
| 504 | `QUERY_TIMEOUT` | Query exceeded 30-second timeout |

---

## Pagination

### Offset-Based Pagination

**Request**:
```bash
GET /api/v1/trades?symbol=BHP&limit=100&offset=100
```

**Response**:
```json
{
  "meta": {
    "pagination": {
      "limit": 100,
      "offset": 100,
      "total": 1000,
      "has_more": true
    }
  }
}
```

### Cursor-Based Pagination (Recommended)

**Initial Request**:
```bash
GET /api/v1/trades?symbol=BHP&limit=100
```

**Response**:
```json
{
  "meta": {
    "pagination": {
      "limit": 100,
      "next_cursor": "eyJwb3NpdGlvbiI6MTAwfQ==",
      "has_more": true
    }
  }
}
```

**Next Page Request**:
```bash
GET /api/v1/trades?symbol=BHP&limit=100&cursor=eyJwb3NpdGlvbiI6MTAwfQ==
```

---

## Best Practices

### 1. Use Cursor-Based Pagination
- More efficient for large result sets
- Consistent results even if data is added during pagination

### 2. Request Only Needed Fields
```json
{
  "fields": ["symbol", "price", "quantity", "trade_timestamp"]
}
```
Reduces response size and query execution time.

### 3. Use Appropriate Time Ranges
- Queries over >7 days may be slow
- Break large queries into smaller time windows

### 4. Leverage Caching
- Responses include `ETag` headers
- Use `If-None-Match` header to avoid re-fetching unchanged data

### 5. Handle Rate Limits Gracefully
```python
import time
import requests

response = requests.get(url, headers=headers)
if response.status_code == 429:
    retry_after = int(response.headers.get('X-RateLimit-Reset', 60))
    time.sleep(retry_after)
    response = requests.get(url, headers=headers)
```

### 6. Use Correlation IDs for Debugging
```bash
curl -H "X-Correlation-ID: my-request-123" http://localhost:8000/api/v1/trades
```
Correlation IDs appear in logs and responses for easier debugging.

---

## Code Examples

### Python (requests)

```python
import requests

API_BASE = "http://localhost:8000/api"
API_KEY = "your-api-key"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

# Simple query
response = requests.get(
    f"{API_BASE}/v1/trades",
    headers=headers,
    params={
        "symbol": "BHP",
        "limit": 100
    }
)
trades = response.json()["data"]

# Complex query
query = {
    "symbols": ["BHP", "RIO"],
    "start_time": "2026-01-13T00:00:00Z",
    "end_time": "2026-01-13T23:59:59Z",
    "fields": ["symbol", "price", "quantity", "trade_timestamp"],
    "limit": 1000,
    "output_format": "json"
}

response = requests.post(
    f"{API_BASE}/v1/trades/query",
    headers=headers,
    json=query
)
results = response.json()["data"]
```

### JavaScript (fetch)

```javascript
const API_BASE = "http://localhost:8000/api";
const API_KEY = "your-api-key";

// Simple query
const response = await fetch(
  `${API_BASE}/v1/trades?symbol=BHP&limit=100`,
  {
    headers: {
      "X-API-Key": API_KEY
    }
  }
);
const { data } = await response.json();

// Complex query
const query = {
  symbols: ["BHP", "RIO"],
  start_time: "2026-01-13T00:00:00Z",
  end_time: "2026-01-13T23:59:59Z",
  fields: ["symbol", "price", "quantity", "trade_timestamp"],
  limit: 1000
};

const complexResponse = await fetch(
  `${API_BASE}/v1/trades/query`,
  {
    method: "POST",
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(query)
  }
);
const { data: results } = await complexResponse.json();
```

### cURL

```bash
# Simple query
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/trades?symbol=BHP&limit=10"

# Complex query
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["BHP"],
    "start_time": "2026-01-13T00:00:00Z",
    "end_time": "2026-01-13T23:59:59Z",
    "limit": 10
  }' \
  "http://localhost:8000/api/v1/trades/query"

# Download as CSV
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["BHP"],
    "output_format": "csv"
  }' \
  "http://localhost:8000/api/v1/trades/query" \
  -o trades.csv
```

---

## Related Documentation

- [Data Dictionary V2](./data-dictionary-v2.md) - Field definitions and schemas
- [Query Architecture](../design/query-architecture.md) - How queries are executed
- [Connection Pool Tuning](../operations/runbooks/connection-pool-tuning.md) - Performance optimization
- [Platform Principles](../architecture/platform-principles.md) - API design philosophy

---

## Changelog

### v1.0 (2026-01-14)
- Initial API release
- Support for trades and quotes queries
- Aggregations (VWAP, TWAP, OHLCV)
- Time-travel queries (Iceberg snapshots)
- Multiple output formats (JSON, CSV, Parquet)

### Future (v2.0 planned)
- OAuth2 + JWT authentication
- Streaming WebSocket endpoints
- Real-time subscriptions
- GraphQL support

---

**Maintained By**: API Team
**Support**: #k2-api (Slack)
**Report Issues**: GitHub Issues
**Last Updated**: 2026-01-14
**Next Review**: 2026-02-14 (monthly)

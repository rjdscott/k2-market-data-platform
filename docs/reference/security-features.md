# K2 Platform Security Features

**Version**: 1.0
**Last Updated**: 2026-01-22
**Status**: Production Ready

---

## Overview

The K2 Market Data Platform implements industry-standard security controls across all layers, from SQL injection protection to distributed circuit breakers. This document details the security architecture and best practices.

---

## Security Architecture

### Defense in Depth

K2 implements multiple security layers:

```
┌─────────────────────────────────────────────────────────────┐
│                    Rate Limiting Layer                      │
│              (100/min OHLCV, 20/min batch)                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  API Authentication                         │
│            (X-API-Key header validation)                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  Input Validation                           │
│       (Pydantic models + explicit SQL sanitization)         │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                 Circuit Breaker                             │
│          (Prevents cascade failures)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                   Query Engine                              │
│            (Parameterized queries only)                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. SQL Injection Protection

### Implementation

**Location**: `src/k2/query/engine.py`

All user inputs are validated and parameterized before query execution:

```python
def query_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000, ...):
    # CRITICAL: Explicit validation before SQL interpolation
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid limit: {limit}") from e

    if not (1 <= limit <= 10000):
        raise ValueError(f"Invalid limit: {limit}. Must be between 1 and 10,000")

    # Parameterized WHERE clause (DuckDB safely escapes values)
    conditions = ["symbol = ?"]
    params = [symbol]

    if exchange:
        conditions.append("exchange = ?")
        params.append(exchange)

    # Safe to use validated limit in query
    query = f"SELECT ... LIMIT {limit}"
    result = conn.execute(query, params).fetchdf()
```

### Protected Parameters

| Parameter | Validation | Protection |
|-----------|------------|------------|
| `symbol` | Parameterized (`?`) | SQL injection safe |
| `exchange` | Parameterized (`?`) | SQL injection safe |
| `start_time` | Parameterized (`?`) + ISO format | SQL injection safe |
| `end_time` | Parameterized (`?`) + ISO format | SQL injection safe |
| `limit` | Explicit int validation | Type coercion + bounds check |
| `timeframe` | Whitelist validation | Enum enforcement |

### Attack Prevention

**Blocked Attack Examples**:
```bash
# Attempt 1: SQL injection via limit
GET /v1/ohlcv/1h?symbol=BTCUSDT&limit=1000;DROP TABLE gold_ohlcv_1h;--
# Result: 400 Bad Request - "Invalid limit: must be an integer"

# Attempt 2: Boolean-based SQL injection
GET /v1/ohlcv/1h?symbol=BTCUSDT' OR '1'='1
# Result: Query executes with literal string "BTCUSDT' OR '1'='1" (safe)

# Attempt 3: Null byte injection
GET /v1/ohlcv/1h?symbol=BTCUSDT\x00
# Result: Parameterized query safely escapes input
```

---

## 2. Rate Limiting

### Implementation

**Technology**: SlowAPI (FastAPI integration)
**Location**: `src/k2/api/rate_limit.py`, `src/k2/api/v1/endpoints.py`

Rate limits are enforced per API key (falls back to IP address):

```python
from slowapi import Limiter
from k2.api.middleware import get_api_key_for_limit

limiter = Limiter(key_func=get_api_key_for_limit)

@limiter.limit("100/minute")
@router.get("/ohlcv/{timeframe}")
async def get_ohlcv_candles(...):
    ...

@limiter.limit("20/minute")  # Stricter for resource-intensive queries
@router.post("/ohlcv/batch")
async def get_ohlcv_batch(...):
    ...
```

### Rate Limit Configuration

| Endpoint | Limit | Scope | Reason |
|----------|-------|-------|--------|
| `GET /v1/ohlcv/{timeframe}` | 100/min | Per API key | Standard query load |
| `POST /v1/ohlcv/batch` | 20/min | Per API key | Resource-intensive (multi-query) |
| `GET /v1/trades` | Default | Per API key | Legacy endpoint (no explicit limit) |
| `GET /v1/health` | None | N/A | Health checks unrestricted |

### Rate Limit Responses

**429 Too Many Requests**:
```json
{
  "error": "RATE_LIMIT_EXCEEDED",
  "message": "Rate limit exceeded: 100 per 1 minute"
}
```

**Headers**:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Remaining requests in window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

---

## 3. Circuit Breaker Pattern

### Implementation

**Location**: `src/k2/query/engine.py`

Prevents cascade failures when downstream services (DuckDB, Iceberg) fail:

```python
class QueryEngine:
    def __init__(
        self,
        circuit_breaker_threshold: int = 5,     # Open after 5 failures
        circuit_breaker_timeout: int = 60,      # Auto-recovery after 60s
    ):
        self._consecutive_failures = 0
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_open_time: datetime | None = None

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open (requests rejected)."""
        if self._circuit_open_time is None:
            return False

        # Auto-recovery after timeout
        time_since_open = (datetime.now() - self._circuit_open_time).total_seconds()
        if time_since_open >= self._circuit_breaker_timeout:
            logger.info("Circuit breaker timeout elapsed, attempting recovery")
            self._circuit_open_time = None
            self._consecutive_failures = max(0, self._consecutive_failures - 2)
            return False

        return True

    def _record_failure(self) -> None:
        """Track failures and open circuit if threshold exceeded."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._circuit_open_time = datetime.now()
            logger.error("Circuit breaker opened", consecutive_failures=self._consecutive_failures)
```

### Circuit Breaker States

| State | Condition | Behavior |
|-------|-----------|----------|
| **Closed** | < 5 consecutive failures | Requests proceed normally |
| **Open** | ≥ 5 consecutive failures | Requests rejected immediately (503) |
| **Half-Open** | After 60s timeout | Next request attempts recovery |

### Metrics

Prometheus metrics track circuit breaker state:

```promql
# Consecutive failures before opening
circuit_breaker_consecutive_failures{pool="duckdb"}

# Total times circuit opened
circuit_breaker_opened_total{pool="duckdb"}

# Requests rejected due to open circuit
circuit_breaker_rejections_total{pool="duckdb"}
```

### Recovery Behavior

1. **Failure Detection**: 5 consecutive query failures
2. **Circuit Opens**: All requests immediately return 503
3. **Timeout Period**: 60 seconds (no requests attempted)
4. **Half-Open State**: Next request attempts query
5. **Success**: Circuit closes, counter resets
6. **Failure**: Circuit remains open, timeout resets

---

## 4. Resource Limits

### Batch Query Protection

**Location**: `src/k2/api/v1/endpoints.py`

Multi-layer protection against resource exhaustion:

```python
@router.post("/ohlcv/batch")
async def get_ohlcv_batch(requests: list[OHLCVQueryRequest], ...):
    # 1. Maximum queries per batch
    if len(requests) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 queries per batch")

    # 2. Total row limit (prevents memory exhaustion)
    total_rows_requested = sum(req.limit for req in requests)
    if total_rows_requested > 25000:
        raise HTTPException(status_code=400, detail="Total limit exceeds 25,000 rows")

    # 3. Timeout protection (30 seconds max)
    try:
        results = await asyncio.wait_for(
            process_batch_with_timeout(),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Batch query timeout")
```

### Resource Limits Summary

| Resource | Limit | Enforcement |
|----------|-------|-------------|
| Batch size | 5 queries | HTTP 400 |
| Total rows per batch | 25,000 rows | HTTP 400 |
| Query timeout | 30 seconds | HTTP 504 |
| Single query row limit | 10,000 rows | HTTP 400 |
| Connection pool size | 5 connections | Queue + timeout |
| Connection acquire timeout | 10 seconds | HTTP 503 |

---

## 5. Input Validation

### Pydantic Models

All API inputs validated with Pydantic v2:

```python
class OHLCVQueryRequest(BaseModel):
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    timeframe: str = Field(..., pattern="^(1m|5m|30m|1h|1d)$")  # Whitelist
    exchange: str | None = Field(None, description="Optional exchange filter")
    start_time: datetime | None = Field(None, description="Start time (ISO 8601)")
    end_time: datetime | None = Field(None, description="End time (ISO 8601)")
    limit: int = Field(1000, ge=1, le=10000, description="Max candles")  # Bounds
```

### OHLCV Data Validation

Candle price relationships validated at response boundary:

```python
class OHLCVCandle(BaseModel):
    @model_validator(mode="after")
    def validate_price_relationships(self) -> "OHLCVCandle":
        """Ensure OHLCV invariants:
        - high_price >= open_price, close_price
        - low_price <= open_price, close_price
        - volume >= 0
        - trade_count > 0
        """
        if self.high_price < self.open_price:
            raise ValueError(f"Invalid: high_price ({self.high_price}) < open_price")
        # ... additional validations
        return self
```

---

## 6. Authentication & Authorization

### API Key Authentication

**Header**: `X-API-Key`
**Middleware**: `src/k2/api/middleware.py:verify_api_key()`

```python
@asynccontextmanager
async def verify_api_key(request: Request) -> AsyncGenerator[None]:
    """Verify API key from X-API-Key header."""
    api_key = request.headers.get("X-API-Key")

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    # Validate against configured keys
    valid_keys = config.api.allowed_api_keys
    if api_key not in valid_keys:
        logger.warning("Invalid API key attempt", remote_addr=request.client.host)
        raise HTTPException(status_code=403, detail="Invalid API key")

    yield
```

### Excluded Endpoints

Public endpoints (no authentication required):
- `GET /health` - Liveness probe
- `GET /metrics` - Prometheus scraping
- `GET /docs` - OpenAPI documentation
- `GET /openapi.json` - OpenAPI spec

---

## 7. Request Tracing

### Correlation IDs

**Location**: `src/k2/api/middleware.py:CorrelationIdMiddleware`

Every request tracked with unique correlation ID:

```python
class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))

        # Store in context variable (accessible across async calls)
        correlation_id_var.set(correlation_id)

        # Add to response headers
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
```

**Usage in Logs**:
```python
logger.debug(
    "OHLCV query completed",
    request_id=correlation_id_var.get(),
    symbol=symbol,
    timeframe=timeframe,
    row_count=len(rows),
)
```

---

## 8. Health Monitoring

### OHLCV Health Check Endpoint

**Endpoint**: `GET /v1/ohlcv/health`
**Purpose**: Proactive monitoring of data freshness

```python
@router.get("/ohlcv/health")
async def get_ohlcv_health() -> dict[str, Any]:
    """Check health and freshness of all OHLCV tables.

    Returns:
        {
            "overall_status": "healthy" | "degraded" | "unhealthy",
            "tables": {
                "1m": {
                    "status": "healthy",
                    "last_update": "2026-01-22T15:30:00Z",
                    "age_minutes": 3.5,
                    "threshold_minutes": 10
                },
                ...
            }
        }
    """
```

**Freshness Thresholds**:
- 1m candles: 10 minutes
- 5m candles: 20 minutes
- 30m candles: 60 minutes
- 1h candles: 120 minutes
- 1d candles: 1500 minutes (25 hours)

---

## Security Best Practices

### For Operators

1. **API Key Rotation**: Rotate keys quarterly (configure in `.env`)
2. **Rate Limit Tuning**: Adjust limits based on usage patterns
3. **Circuit Breaker Monitoring**: Alert on `circuit_breaker_opened_total` increases
4. **Log Monitoring**: Review failed auth attempts in structured logs

### For Developers

1. **Never Bypass Validation**: Always use Pydantic models for inputs
2. **Parameterize All Queries**: Use `?` placeholders, never string interpolation
3. **Test SQL Injection**: Include malicious inputs in unit tests
4. **Respect Rate Limits**: Design clients to handle 429 responses

### For API Consumers

1. **Store API Keys Securely**: Use environment variables, never commit keys
2. **Implement Exponential Backoff**: Respect 429 rate limit responses
3. **Include Correlation IDs**: Pass `X-Correlation-ID` for request tracking
4. **Monitor Health Endpoints**: Check `/v1/ohlcv/health` before queries

---

## Incident Response

### SQL Injection Attempt Detected

**Indicators**:
- 400 errors with "Invalid limit" or "Invalid timeframe" messages
- Unusual characters in query parameters (`'`, `;`, `--`, `\x00`)

**Actions**:
1. Check logs for `api_key` to identify source
2. Review request patterns for automated scanning
3. Consider blocking API key if malicious
4. No data exposure risk (parameterized queries safe)

### Rate Limit Exceeded

**Indicators**:
- 429 responses in API logs
- `rate_limit_exceeded_total` metric increasing

**Actions**:
1. Identify API key from logs
2. Contact API consumer about usage patterns
3. Consider increasing limits for legitimate high-volume users
4. Implement API key-specific limits if needed

### Circuit Breaker Opened

**Indicators**:
- 503 responses from API
- `circuit_breaker_opened_total` metric incremented
- Logs show "Circuit breaker opened" message

**Actions**:
1. Check DuckDB/Iceberg health (database connection issues?)
2. Review query performance metrics (slow queries?)
3. Verify MinIO/S3 accessibility (storage issues?)
4. Circuit auto-recovers after 60s, monitor success rate

---

## Compliance & Audit

### GDPR Considerations

- **No PII Stored**: Market data contains no personal information
- **Access Logs**: All API access logged with correlation IDs
- **Data Retention**: Configurable per OHLCV timeframe

### Audit Trail

All security events logged with structured logging:

```json
{
  "timestamp": "2026-01-22T15:30:00Z",
  "level": "WARNING",
  "event": "invalid_api_key_attempt",
  "remote_addr": "192.168.1.100",
  "api_key_prefix": "k2-dev-***",
  "endpoint": "/v1/ohlcv/1h",
  "correlation_id": "a1b2c3d4-..."
}
```

### Security Metrics

Prometheus metrics for security monitoring:

```promql
# Failed authentication attempts
rate(http_request_errors_total{error_type="auth"}[5m])

# Rate limit violations
rate(rate_limit_exceeded_total[5m])

# Circuit breaker opens (service degradation)
increase(circuit_breaker_opened_total[1h])

# Query validation errors (potential attacks)
rate(http_request_errors_total{status_code="400"}[5m])
```

---

## Security Roadmap

### Implemented (v1.0)
- ✅ SQL injection protection
- ✅ Rate limiting (per API key)
- ✅ Circuit breaker pattern
- ✅ Input validation (Pydantic)
- ✅ Request tracing (correlation IDs)
- ✅ Resource limits (batch queries, timeouts)
- ✅ Health monitoring

### Planned (v1.1)
- 🔄 OAuth 2.0 / JWT authentication (alternative to API keys)
- 🔄 IP allowlisting (per API key)
- 🔄 Query cost limits (prevent expensive queries)
- 🔄 Audit log export (SIEM integration)

### Future Considerations
- mTLS for service-to-service communication
- Field-level encryption for sensitive metadata
- Query result caching with TTL
- Advanced anomaly detection (ML-based)

---

## References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [API Security Best Practices](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)

---

**Last Reviewed**: 2026-01-22
**Next Review**: 2026-04-22 (Quarterly)
**Owner**: Platform Security Team

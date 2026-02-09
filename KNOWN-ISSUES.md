# Known Issues - v0.1.0

**Last Updated**: 2026-02-09
**Release**: v0.1.0 (Preview)
**Status**: ⚠️ Not Production Ready

---

## ⚠️ Production Blockers

The following issues are documented from the Staff Engineer Review (2026-01-22) and **MUST** be resolved before production deployment. These issues are tracked for resolution in **v0.2.0** (target: 2026-02-20).

---

## Critical Issues (HIGH Severity)

### 1. SQL Injection Vulnerability - OHLCV LIMIT Clause

**Issue ID**: SECURITY-001
**Severity**: 🔴 **CRITICAL**
**Component**: Query Engine (`src/k2/query/engine.py:491`)
**Discovered**: 2026-01-22 (Staff Review)
**Status**: Documented, Fix Planned for v0.2

#### Description
The OHLCV query endpoint uses direct string interpolation for the `LIMIT` clause, allowing potential SQL injection attacks.

#### Vulnerable Code
```python
# src/k2/query/engine.py:491
query = f"""
    SELECT ...
    FROM iceberg_scan('{table_path}')
    {where_clause}
    ORDER BY window_start DESC
    LIMIT {limit}  # ❌ Direct string interpolation
"""
```

#### Attack Vector
```bash
# Malicious request
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/v1/ohlcv/1h?symbol=BTCUSDT&limit=1000;DROP TABLE gold_ohlcv_1h;--"

# Result: SQL injection, potential data loss
```

#### Impact
- **Remote Code Execution**: Attacker can execute arbitrary SQL
- **Data Exfiltration**: Access to other tables via UNION attacks
- **Data Loss**: DROP TABLE, TRUNCATE commands possible
- **Denial of Service**: Resource exhaustion via expensive queries

#### Workaround (v0.1)
1. **Use only trusted API keys** in development/testing
2. **Do not expose API publicly** on the internet
3. **Monitor API logs** for suspicious query patterns
4. **Validate all inputs** at application layer before API calls

#### Planned Fix (v0.2)
```python
# Option 1: Parameterized query (preferred)
query = f"""
    SELECT ...
    FROM iceberg_scan('{table_path}')
    {where_clause}
    ORDER BY window_start DESC
    LIMIT ?
"""
params.append(limit)

# Option 2: Explicit validation (alternative)
limit = int(limit)  # Raises ValueError if not integer
if not (1 <= limit <= 10000):
    raise ValueError(f"Invalid limit: {limit}")
query = f"... LIMIT {limit}"  # Safe after validation
```

#### Testing Plan (v0.2)
- [ ] Integration test for SQL injection attempts
- [ ] Fuzz testing with malicious payloads
- [ ] Security audit of all query paths

---

### 2. Resource Exhaustion - Batch Endpoint DoS

**Issue ID**: SECURITY-002
**Severity**: 🔴 **HIGH**
**Component**: API Batch Endpoint (`src/k2/api/v1/endpoints.py:662-669`)
**Discovered**: 2026-01-22 (Staff Review)
**Status**: Documented, Fix Planned for v0.2

#### Description
The `/v1/ohlcv/batch` endpoint processes up to 10 requests sequentially without timeout protection, connection pool limits, or memory constraints, enabling denial-of-service attacks.

#### Vulnerable Code
```python
# src/k2/api/v1/endpoints.py:662-669
@router.post("/batch")
async def get_ohlcv_batch(
    requests: list[OHLCVQueryRequest],
    engine: QueryEngine = Depends(get_query_engine),
) -> dict[str, OHLCVResponse]:
    # ❌ No timeout, no pool protection, no memory limits
    results = {}
    for req in requests:  # Up to 10 requests
        results[req.timeframe] = await query_ohlcv(...)
    return results
```

#### Attack Vector
```bash
# Request 10 expensive queries simultaneously
curl -X POST -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:8000/v1/ohlcv/batch -d '
[
  {"symbol": "BTCUSDT", "timeframe": "1m", "limit": 10000},
  {"symbol": "BTCUSDT", "timeframe": "5m", "limit": 10000},
  {"symbol": "BTCUSDT", "timeframe": "30m", "limit": 10000},
  {"symbol": "BTCUSDT", "timeframe": "1h", "limit": 10000},
  {"symbol": "BTCUSDT", "timeframe": "1d", "limit": 10000},
  {"symbol": "ETHUSDT", "timeframe": "1m", "limit": 10000},
  {"symbol": "ETHUSDT", "timeframe": "5m", "limit": 10000},
  {"symbol": "ETHUSDT", "timeframe": "30m", "limit": 10000},
  {"symbol": "ETHUSDT", "timeframe": "1h", "limit": 10000},
  {"symbol": "ETHUSDT", "timeframe": "1d", "limit": 10000}
]'

# Total: 100k rows, connection pool exhaustion, potential OOM
```

#### Impact
- **Denial of Service**: Connection pool exhaustion (10 connections blocked)
- **Memory Exhaustion**: 100K+ rows loaded into memory simultaneously
- **Slow Response**: Other requests starved while batch processes
- **API Unavailability**: System becomes unresponsive

#### Workaround (v0.1)
1. **Limit batch size manually** to 3-5 timeframes max
2. **Monitor connection pool usage** in Prometheus
3. **Set API key quotas** to prevent abuse
4. **Use separate API instance** for batch queries (if critical)

#### Planned Fix (v0.2)
```python
import asyncio

async def get_ohlcv_batch(
    requests: list[OHLCVQueryRequest],
    engine: QueryEngine = Depends(get_query_engine),
) -> dict[str, OHLCVResponse]:

    # 1. Reduce batch limit
    if len(requests) > 5:
        raise HTTPException(status_code=400, detail="Max 5 timeframes per batch")

    # 2. Add total timeout
    try:
        async with asyncio.timeout(30):  # 30s total timeout
            results = {}
            for req in requests:
                # 3. Add per-query timeout
                try:
                    async with asyncio.timeout(10):  # 10s per query
                        results[req.timeframe] = await query_ohlcv(...)
                except asyncio.TimeoutError:
                    results[req.timeframe] = {"error": "Query timeout"}
            return results
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Batch request timeout")
```

#### Testing Plan (v0.2)
- [ ] Load test with concurrent batch requests
- [ ] Timeout validation (per-query and total)
- [ ] Connection pool monitoring under load
- [ ] Memory usage profiling with large batches

---

## Medium Severity Issues

### 3. Missing Rate Limiting on OHLCV Endpoints

**Issue ID**: SECURITY-003
**Severity**: 🟡 **MEDIUM**
**Component**: API Endpoints (`src/k2/api/v1/endpoints.py`)
**Discovered**: 2026-01-22 (Staff Review)
**Status**: Documented, Fix Planned for v0.2

#### Description
OHLCV endpoints lack rate limiting, allowing unlimited requests per API key and enabling abuse or resource exhaustion.

#### Current State
- No rate limiting configured
- No request throttling
- No per-API-key quotas
- No burst protection

#### Impact
- API abuse by single client
- Fair usage policy violations
- Resource contention for legitimate users
- Increased infrastructure costs

#### Workaround (v0.1)
1. **Manual monitoring** of API usage per key in logs
2. **Revoke API keys** showing abusive patterns
3. **Deploy behind nginx** with rate limiting (temporary)

#### Planned Fix (v0.2)
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_api_key)

@router.get("/ohlcv/{timeframe}")
@limiter.limit("100/minute")  # 100 requests per minute per API key
async def get_ohlcv(...):
    ...

@router.post("/ohlcv/batch")
@limiter.limit("20/minute")  # Lower limit for batch queries
async def get_ohlcv_batch(...):
    ...
```

#### Recommended Limits (v0.2)
- **Single timeframe**: 100 req/min per API key
- **Batch queries**: 20 req/min per API key
- **Burst allowance**: 20 requests (short-term spike tolerance)

---

### 4. Incomplete Input Validation

**Issue ID**: SECURITY-004
**Severity**: 🟡 **MEDIUM**
**Component**: Multiple API endpoints
**Discovered**: 2026-01-22 (Staff Review)
**Status**: Documented, Fix Planned for v0.2

#### Description
Several endpoints lack comprehensive input validation, potentially allowing malformed or malicious inputs.

#### Missing Validations
- **Symbol validation**: No whitelist of allowed symbols (accepts any string)
- **Timeframe validation**: Enum check exists but error messages weak
- **Date range validation**: No maximum lookback period
- **Offset/limit validation**: Integer checks but no business rules
- **Exchange validation**: No whitelist enforcement

#### Example Issues
```python
# Accepts invalid symbol (no validation)
GET /v1/ohlcv/1h?symbol=<script>alert('XSS')</script>&limit=10

# Accepts unreasonable date range (resource exhaustion)
GET /v1/ohlcv/1m?symbol=BTCUSDT&start_time=2020-01-01&end_time=2026-01-01&limit=100000

# Accepts negative offset (undefined behavior)
GET /v1/ohlcv/1h?symbol=BTCUSDT&offset=-100&limit=10
```

#### Planned Fix (v0.2)
```python
from pydantic import Field, validator

class OHLCVQueryRequest(BaseModel):
    symbol: str = Field(..., regex=r"^[A-Z]{6,12}$")  # Alphanumeric, 6-12 chars
    timeframe: Literal["1m", "5m", "30m", "1h", "1d"]  # Enum validation
    start_time: datetime | None = Field(None)
    end_time: datetime | None = Field(None)
    limit: int = Field(default=100, ge=1, le=10000)  # 1-10k range
    offset: int = Field(default=0, ge=0, le=1000000)  # Non-negative

    @validator("end_time")
    def validate_date_range(cls, v, values):
        start = values.get("start_time")
        if start and v:
            max_days = {"1m": 90, "5m": 180, "30m": 365, "1h": 1095, "1d": 1825}
            timeframe = values.get("timeframe")
            max_range = max_days.get(timeframe, 90)
            if (v - start).days > max_range:
                raise ValueError(f"Date range exceeds {max_range} days for {timeframe}")
        return v
```

---

### 5. No Circuit Breaker Integration for OHLCV

**Issue ID**: RELIABILITY-001
**Severity**: 🟡 **LOW-MEDIUM**
**Component**: OHLCV Endpoints
**Discovered**: 2026-01-22 (Staff Review)
**Status**: Documented, Enhancement Planned for v0.2

#### Description
OHLCV endpoints do not integrate with the existing circuit breaker system, preventing graceful degradation during database failures.

#### Current State
- Circuit breaker exists for trades/quotes endpoints
- OHLCV endpoints bypass circuit breaker
- No fallback behavior on database failures
- No degradation levels for OHLCV queries

#### Impact
- Cascading failures during database outages
- No graceful degradation
- Poor error messages for users
- Increased MTTR (Mean Time To Recovery)

#### Planned Fix (v0.2)
- Integrate OHLCV queries with existing circuit breaker
- Add fallback responses (cached data or error messages)
- Implement degradation levels (fresh → cached → error)
- Add circuit breaker metrics for OHLCV

---

## Other Known Issues

### 6. Phase 13 Integration Tests Incomplete

**Issue ID**: TEST-001
**Severity**: 🔵 **LOW**
**Component**: Test Suite
**Status**: Documentation only, no functional impact

3 validation tests are excluded because they expect 100% success on OLD 1m candles generated before the OHLC ordering bug fix (Decision #030). These candles will self-correct as new data arrives.

**Impact**: None - fresh candles validated at 100% success rate.

---

## Monitoring & Detection

### How to Detect Exploitation (v0.1)

#### SQL Injection Attempts
```bash
# Monitor API logs for suspicious patterns
docker compose logs k2-query-api | grep -i "drop\|delete\|truncate\|union\|--;--"

# Check Prometheus metrics for failed queries
rate(query_errors_total{error_type="sql_error"}[5m]) > 0
```

#### Resource Exhaustion
```bash
# Monitor connection pool usage
duckdb_connection_pool_active > 8  # Alert if >80% utilization

# Monitor API response times
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 5
```

#### API Abuse
```bash
# Check request rates per API key
rate(http_requests_total{endpoint="/v1/ohlcv/*"}[1m]) > 200  # >200 req/min
```

---

## Upgrade Path to v0.2

When v0.2 is released (target: 2026-02-20), upgrade steps will include:

1. **Review CHANGELOG.md** for all security fixes
2. **Update to v0.2**: `git pull && docker compose up -d --build`
3. **Run security validation**: `make test-security` (new in v0.2)
4. **Update API keys**: Regenerate if exposed during v0.1
5. **Review rate limits**: Configure per your usage patterns
6. **Test application**: Ensure no breaking changes in your integration

---

## Reporting New Issues

If you discover additional security vulnerabilities:

1. **DO NOT** create public GitHub issues
2. **Email security contact**: security@example.com (or private channel)
3. **Include**: Detailed reproduction steps, impact assessment, suggested fix
4. **Wait for response** before public disclosure (responsible disclosure)

See [SECURITY.md](./SECURITY.md) for complete vulnerability reporting policy.

---

## References

- **Staff Review**: [docs/reviews/ohlcv-api-staff-review.md](./docs/reviews/ohlcv-api-staff-review.md)
- **Security Features**: [docs/reference/security-features.md](./docs/reference/security-features.md)
- **Phase 13 Status**: [docs/phases/phase-13-ohlcv-analytics/STATUS.md](./docs/phases/phase-13-ohlcv-analytics/STATUS.md)

---

**Last Updated**: 2026-02-09
**Next Review**: 2026-02-20 (v0.2 release)
**Maintained By**: Security & Engineering Team

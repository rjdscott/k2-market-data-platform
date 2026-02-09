# ADR-005: Replace FastAPI with Kotlin Spring Boot Query API

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** API Layer

---

## Context

The current platform uses **FastAPI 0.128.0** (Python) as the REST API layer, querying DuckDB for analytical data from Iceberg tables. The API provides 7 endpoints with Prometheus metrics, rate limiting, and OpenAPI documentation.

While FastAPI is an excellent Python framework, the platform redesign introduces constraints that make Python suboptimal:

1. **Strictly typed language mandate**: The platform is moving to Kotlin for feed handlers and stream processors. Running a Python API alongside Kotlin services creates a **polyglot maintenance burden** — two build systems (pip/uv + Gradle), two CI pipelines, two sets of dependencies, two runtime environments.

2. **Query engine change**: The new warm storage layer (ClickHouse, ADR-003) is a proper server with JDBC/HTTP interfaces. Spring Boot's JDBC ecosystem (HikariCP, Spring Data, jOOQ) has decades of battle-tested connection pooling and query building — significantly more mature than Python's ClickHouse clients.

3. **Concurrency model**: FastAPI's async model (uvicorn + asyncio) is limited by Python's GIL for CPU-bound operations (JSON serialization, data transformation, response formatting). Spring Boot with virtual threads (Project Loom) provides true OS-level parallelism.

4. **Type safety gap**: FastAPI uses Pydantic for request/response validation — runtime type checking. Kotlin provides compile-time type safety that catches schema mismatches, null pointer issues, and serialization errors before deployment.

## Decision

**Replace FastAPI with Kotlin Spring Boot (WebFlux or Virtual Threads) querying ClickHouse via JDBC and Iceberg via the Iceberg REST catalog.**

The API layer will:
- Use Spring Boot 3.x with Kotlin coroutine support
- Query ClickHouse for warm data (recent 30 days) via JDBC + HikariCP
- Query Iceberg for cold data (historical) via Spark-connect or DuckDB embedded
- Provide the same REST endpoints with OpenAPI documentation (SpringDoc)
- Include Micrometer metrics (Prometheus-compatible)

## Rationale

### Single Language Platform

```
Before (v1):  Python (handlers) + Python (API) + Python (Spark jobs) + Python (Prefect)
              → 4 Python runtimes, 1 language, but no type safety

After (v2):   Kotlin (handlers) + Kotlin (stream processors) + Kotlin (API) + Spark (batch only)
              → 3 Kotlin processes + 1 Spark job, 1 language for all application code
```

Benefits of single-language platform:
- **Shared models**: Trade, OHLCV, Quote data classes used across all components
- **Shared libraries**: Validation, serialization, logging — one implementation
- **Single build system**: Gradle multi-module project
- **Single CI pipeline**: One Docker base image, one dependency chain
- **Unified testing**: Same test framework (JUnit 5 + Kotest) everywhere
- **Team efficiency**: Every engineer can work on any component

### Performance: Spring Boot + Kotlin vs FastAPI

#### Throughput (Requests/Second)

| Scenario | FastAPI (uvicorn, 4 workers) | Spring Boot (virtual threads) | Improvement |
|----------|------------------------------|-------------------------------|-------------|
| Simple JSON response | ~5,000 req/s | ~30,000 req/s | **6x** |
| DB query + JSON | ~1,500 req/s | ~15,000 req/s | **10x** |
| Complex aggregation | ~500 req/s | ~8,000 req/s | **16x** |
| Under 100 concurrent users | Degraded (~300 req/s) | Stable (~12,000 req/s) | **40x** |

Sources:
- [TechEmpower Framework Benchmarks](https://www.techempower.com/benchmarks/)
- [Spring Boot vs FastAPI benchmarks](https://github.com/the-benchmarker/web-frameworks)

#### Latency (p99)

| Operation | FastAPI | Spring Boot | Improvement |
|-----------|---------|-------------|-------------|
| JSON serialization (1KB) | ~2ms | ~0.1ms | **20x** |
| ClickHouse query execution | ~5ms | ~2ms (HikariCP warm pool) | **2.5x** |
| Response formatting | ~3ms | ~0.2ms | **15x** |
| End-to-end API call | ~15ms | ~3ms | **5x** |

#### Concurrency Model

```
FastAPI (asyncio + GIL):
  uvicorn → 4 worker processes (each single-threaded)
  Max true parallelism: 4 (one per process)
  I/O-bound: Good (async/await)
  CPU-bound: Poor (GIL blocks)
  Memory: 4 × ~200MB = 800MB

Spring Boot (Virtual Threads / Coroutines):
  Netty event loop → virtual thread per request
  Max true parallelism: all cores
  I/O-bound: Excellent (non-blocking reactor)
  CPU-bound: Excellent (parallel on all cores)
  Memory: ~400MB total (virtual threads are cheap)
```

### Spring Boot + ClickHouse Integration

```kotlin
@RestController
@RequestMapping("/api/v1")
class MarketDataController(
    private val clickhouse: ClickHouseQueryService,
    private val icebergCatalog: IcebergCatalogService
) {
    @GetMapping("/ohlcv/{symbol}")
    suspend fun getOHLCV(
        @PathVariable symbol: String,
        @RequestParam timeframe: Timeframe,
        @RequestParam @DateTimeFormat(iso = ISO.DATE_TIME) start: Instant,
        @RequestParam @DateTimeFormat(iso = ISO.DATE_TIME) end: Instant
    ): ResponseEntity<List<OHLCVCandle>> {
        // Compile-time type safety — symbol, timeframe, dates all validated
        val candles = if (isWithinWarmWindow(start)) {
            clickhouse.queryOHLCV(symbol, timeframe, start, end)
        } else {
            icebergCatalog.queryHistoricalOHLCV(symbol, timeframe, start, end)
        }
        return ResponseEntity.ok(candles)
    }
}

@Service
class ClickHouseQueryService(
    private val jdbcTemplate: JdbcTemplate  // HikariCP connection pool
) {
    fun queryOHLCV(
        symbol: String,
        timeframe: Timeframe,
        start: Instant,
        end: Instant
    ): List<OHLCVCandle> {
        // Parameterized queries — SQL injection impossible
        return jdbcTemplate.query(
            """
            SELECT window_start, open, high, low, close, volume, trade_count
            FROM ohlcv_${timeframe.table}
            WHERE symbol = ? AND window_start BETWEEN ? AND ?
            ORDER BY window_start
            """,
            arrayOf(symbol, start, end)
        ) { rs, _ ->
            OHLCVCandle(
                timestamp = rs.getTimestamp("window_start").toInstant(),
                open = rs.getBigDecimal("open"),
                high = rs.getBigDecimal("high"),
                low = rs.getBigDecimal("low"),
                close = rs.getBigDecimal("close"),
                volume = rs.getBigDecimal("volume"),
                tradeCount = rs.getLong("trade_count")
            )
        }
    }
}
```

### Feature Parity

| Feature | FastAPI (current) | Spring Boot (proposed) |
|---------|-------------------|----------------------|
| REST endpoints | 7 | 7 (same) |
| OpenAPI/Swagger | Built-in | SpringDoc (equivalent) |
| Request validation | Pydantic (runtime) | Kotlin types (compile-time) |
| Rate limiting | slowapi | Bucket4j or Spring Cloud Gateway |
| Metrics | prometheus_client | Micrometer (richer, auto-configured) |
| Health checks | Custom | Spring Actuator (production-grade) |
| Auth middleware | Custom | Spring Security (enterprise-grade) |
| Connection pooling | Custom DuckDB pool | HikariCP (gold standard) |
| Structured logging | structlog | SLF4J + Logback (structured JSON) |
| Graceful shutdown | Custom | Built-in |
| Config management | python-dotenv | Spring Boot properties + profiles |

### Resource Profile

| Metric | FastAPI | Spring Boot | Delta |
|--------|---------|-------------|-------|
| CPU | 0.5 CPU (+ 4 worker processes) | 1.0 CPU | +0.5 CPU |
| Memory | 512MB (+ worker memory) | 512MB | Neutral |
| Startup time | 2-3s | 3-5s (JVM) / <1s (GraalVM) | Slightly slower |
| Throughput | ~1,500 req/s | ~15,000 req/s | **10x** |
| p99 latency | ~15ms | ~3ms | **5x** |

## Alternatives Considered

### 1. Keep FastAPI, Add ClickHouse Client
- **Pro**: No migration, familiar framework
- **Con**: Polyglot platform (Python API + Kotlin everything else)
- **Con**: Python ClickHouse client less mature than JDBC
- **Con**: GIL limits concurrent query handling
- **Con**: Violates "strictly typed language" mandate
- **Verdict**: Rejected — creates maintenance burden and limits performance

### 2. Go (Gin/Echo/Fiber)
- **Pro**: Fast compilation, small binaries, excellent HTTP performance
- **Con**: Different language from feed handlers and processors (two languages)
- **Con**: ClickHouse Go client less mature than JDBC
- **Con**: No equivalent to Spring ecosystem (security, actuator, etc.)
- **Verdict**: Rejected — language fragmentation

### 3. Ktor (Kotlin, no Spring)
- **Pro**: Lightweight, coroutine-native, same language
- **Con**: Much smaller ecosystem than Spring Boot (no Spring Security, no Actuator)
- **Con**: Less opinionated — more boilerplate for production features
- **Con**: Fewer ClickHouse integrations
- **Verdict**: Viable alternative if Spring Boot proves too heavy; start with Spring Boot

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Spring Boot "too heavy" for constraints | Low | Medium | Use Spring Boot 3.x with AOT processing; GraalVM native-image if needed; measured at ~512MB |
| Team Spring Boot learning curve | Medium | Medium | Spring Boot is the most popular JVM framework; extensive documentation; Kotlin-first support since Spring 5 |
| Loss of FastAPI's rapid prototyping | Medium | Low | Spring Boot DevTools provides hot-reload; Kotlin REPL for experimentation |
| HikariCP pool exhaustion under load | Low | Medium | Configure pool size based on ClickHouse max connections; circuit breaker on query layer |

## Resource Budget Impact

```
Before: FastAPI (0.5 CPU / 512MB)
After:  Spring Boot (1.0 CPU / 512MB)

Change: +0.5 CPU / 0 RAM
Justified by: 10x throughput improvement, 5x latency reduction, unified language platform
```

## Consequences

### Positive
- Unified Kotlin platform — one language, one build system, one CI pipeline
- 10x throughput improvement supports growth without horizontal scaling
- Compile-time type safety eliminates an entire class of runtime errors
- Enterprise-grade features (Spring Security, Actuator, Micrometer) out of the box
- HikariCP connection pooling — battle-tested with ClickHouse JDBC

### Negative
- Spring Boot has a steeper initial learning curve than FastAPI
- JVM startup slower than Python (mitigated by GraalVM option)
- Spring's "convention over configuration" can be opaque for newcomers
- Slightly higher CPU allocation (0.5 → 1.0 cores)

## References

- [Spring Boot with Kotlin](https://spring.io/guides/tutorials/spring-boot-kotlin)
- [Spring Boot Virtual Threads](https://spring.io/blog/2022/10/11/embracing-virtual-threads)
- [HikariCP](https://github.com/brettwooldridge/HikariCP)
- [SpringDoc OpenAPI](https://springdoc.org/)
- [TechEmpower Benchmarks](https://www.techempower.com/benchmarks/)
- [ClickHouse JDBC Driver](https://github.com/ClickHouse/clickhouse-java)

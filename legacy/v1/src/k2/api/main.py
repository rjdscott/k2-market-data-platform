"""K2 Market Data Platform - FastAPI REST API.

This module provides the main FastAPI application for the K2 platform:
- REST API for querying market data (trades, quotes, summaries)
- OpenAPI/Swagger documentation
- Production-ready middleware (auth, rate limiting, correlation IDs)
- Health check endpoints with dependency monitoring
- Prometheus metrics endpoint for observability

Architecture:
- /v1/* endpoints: Versioned API for market data queries
- /health: Health check endpoint (no auth required)
- /metrics: Prometheus metrics endpoint (no auth required)
- /docs: Swagger UI documentation
- /redoc: ReDoc documentation

Usage:
    # Development server
    uvicorn k2.api.main:app --reload --port 8000

    # Production server
    gunicorn k2.api.main:app -w 4 -k uvicorn.workers.UvicornWorker

Environment Variables:
    K2_API_KEY: API key for authentication (default: k2-dev-api-key-2026)
    K2_API_RATE_LIMIT: Rate limit per minute (default: 100)
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from k2.api.deps import get_query_engine, shutdown_engines, startup_engines
from k2.api.middleware import (
    CacheControlMiddleware,
    CorrelationIdMiddleware,
    RequestLoggingMiddleware,
    RequestSizeLimitMiddleware,
)
from k2.api.models import DependencyHealth, HealthResponse, HealthStatus
from k2.api.rate_limit import limiter
from k2.api.v1 import router as v1_router
from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics, initialize_metrics

logger = get_logger(__name__, component="api")
metrics = create_component_metrics("api")

# =============================================================================
# Application Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan manager.

    Handles startup and shutdown events:
    - Startup: Initialize query engines, warm up connections, set up metrics
    - Shutdown: Close connections, cleanup resources
    """
    # Startup
    logger.info("K2 API starting up")

    # Initialize Prometheus metrics with platform info
    environment = getattr(config, "environment", "dev")
    initialize_metrics(version="0.1.0", environment=environment)
    logger.info("Prometheus metrics initialized", version="0.1.0", environment=environment)

    await startup_engines()
    logger.info("K2 API startup complete")

    yield

    # Shutdown
    logger.info("K2 API shutting down")
    await shutdown_engines()
    logger.info("K2 API shutdown complete")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="K2 Market Data Platform API",
    description="""
## Overview

K2 is a real-time market data platform with streaming ingestion and lakehouse storage.
This API provides REST endpoints for querying market data stored in Apache Iceberg tables.

## Authentication

All `/v1/*` endpoints require API key authentication via the `X-API-Key` header.

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/v1/trades
```

## Rate Limiting

API requests are rate-limited to 100 requests per minute per API key.
Rate limit headers are included in all responses:
- `X-RateLimit-Limit`: Maximum requests per window
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: Seconds until window resets

## Correlation IDs

All requests include a correlation ID for distributed tracing.
Pass your own via `X-Correlation-ID` header or one will be generated.

## Endpoints

- **GET /v1/trades** - Query trade records with filters
- **GET /v1/quotes** - Query quote records with filters
- **GET /v1/summary/{symbol}/{date}** - Daily OHLCV market summary
- **GET /v1/symbols** - List available symbols
- **GET /v1/stats** - Database statistics
- **GET /v1/snapshots** - List Iceberg table snapshots
- **GET /health** - Health check (no auth required)
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    contact={
        "name": "K2 Platform Team",
        "url": "https://github.com/rjdscott/k2-market-data-platform",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
)

# =============================================================================
# Middleware Configuration
# =============================================================================

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware (configure for your domain in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React dev server
        "http://localhost:8080",  # Vue dev server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)

# Custom middleware (order matters - first added is outermost)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=10 * 1024 * 1024)  # 10MB limit

# =============================================================================
# Include Routers
# =============================================================================

app.include_router(v1_router)


# =============================================================================
# Root & Health Endpoints
# =============================================================================


@app.get(
    "/",
    summary="API Root",
    description="Returns API information and links to documentation.",
    tags=["Root"],
)
async def root() -> dict:
    """API root endpoint."""
    return {
        "name": "K2 Market Data Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
        "api": "/v1",
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    description="""
    Health check endpoint for monitoring and load balancers.

    Checks:
    - DuckDB connection status
    - Iceberg table accessibility
    - Overall API health

    No authentication required.
    """,
    tags=["Health"],
)
@limiter.limit("60/minute")
async def health_check(request: Request) -> HealthResponse:
    """Health check endpoint.

    Returns overall health status and dependency checks.
    Used by load balancers and monitoring systems.
    """
    dependencies = []
    overall_status = HealthStatus.HEALTHY

    # Check DuckDB connection
    duckdb_status = HealthStatus.HEALTHY
    duckdb_latency = None
    duckdb_message = None

    try:
        start = time.time()
        engine = get_query_engine()
        # Simple connectivity check using connection pool
        with engine.pool.acquire(timeout=5.0) as conn:
            conn.execute("SELECT 1").fetchone()
        duckdb_latency = (time.time() - start) * 1000

        if duckdb_latency > 1000:  # > 1 second is concerning
            duckdb_status = HealthStatus.DEGRADED
            duckdb_message = "High latency"
            overall_status = HealthStatus.DEGRADED

    except Exception as e:
        duckdb_status = HealthStatus.UNHEALTHY
        duckdb_message = str(e)
        overall_status = HealthStatus.UNHEALTHY

    dependencies.append(
        DependencyHealth(
            name="duckdb",
            status=duckdb_status,
            latency_ms=duckdb_latency,
            message=duckdb_message,
        ),
    )

    # Check Iceberg table access
    iceberg_status = HealthStatus.HEALTHY
    iceberg_latency = None
    iceberg_message = None

    try:
        start = time.time()
        engine = get_query_engine()
        # Try to read table metadata
        engine.get_symbols(exchange=None)
        iceberg_latency = (time.time() - start) * 1000

        if iceberg_latency > 5000:  # > 5 seconds is concerning
            iceberg_status = HealthStatus.DEGRADED
            iceberg_message = "High latency"
            if overall_status == HealthStatus.HEALTHY:
                overall_status = HealthStatus.DEGRADED

    except Exception as e:
        iceberg_status = HealthStatus.UNHEALTHY
        iceberg_message = str(e)
        overall_status = HealthStatus.UNHEALTHY

    dependencies.append(
        DependencyHealth(
            name="iceberg",
            status=iceberg_status,
            latency_ms=iceberg_latency,
            message=iceberg_message,
        ),
    )

    return HealthResponse(
        status=overall_status,
        version="1.0.0",
        timestamp=datetime.now(UTC),
        dependencies=dependencies,
    )


# =============================================================================
# Observability Endpoints
# =============================================================================


@app.get(
    "/metrics",
    summary="Prometheus Metrics",
    description="Prometheus metrics endpoint for scraping by Prometheus server.",
    tags=["Observability"],
    include_in_schema=False,  # Hide from OpenAPI docs (Prometheus endpoint convention)
)
async def prometheus_metrics() -> Response:
    """Expose Prometheus metrics for scraping.

    Returns all registered metrics in Prometheus exposition format.
    This endpoint is not rate-limited as Prometheus scrapes frequently.

    Metrics include:
    - HTTP request counts and durations (k2_http_*)
    - Kafka producer/consumer metrics (k2_kafka_*)
    - Iceberg storage metrics (k2_iceberg_*)
    - Query engine metrics (k2_query_*)
    - System health metrics (k2_degradation_*, k2_circuit_breaker_*)
    """
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


# =============================================================================
# Error Handlers
# =============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unhandled errors.

    Logs the error and returns a standardized error response.
    """
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=type(exc).__name__,
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            },
        },
    )


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "k2.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )

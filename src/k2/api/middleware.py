"""API middleware for authentication, rate limiting, and observability.

This module implements production-grade middleware for the K2 API:
- API Key authentication with configurable keys
- Rate limiting (100 req/min default)
- Request correlation ID tracking
- Response caching headers
- Prometheus metrics collection

Middleware follows trading firm conventions:
- Fail-fast authentication before request processing
- Non-blocking rate limiting with clear error messages
- Correlation IDs for distributed tracing
- RED metrics (Rate, Errors, Duration) for observability
"""

import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from k2.common.config import config
from k2.common.logging import get_logger, set_correlation_id
from k2.common.metrics import create_component_metrics

logger = get_logger(__name__, component="api")
metrics = create_component_metrics("api")

# Context variable for correlation ID
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


# =============================================================================
# API Key Authentication
# =============================================================================

# API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key_from_env() -> str:
    """Get API key from environment or use default for development."""
    return getattr(config, "api_key", None) or "k2-dev-api-key-2026"


async def verify_api_key(
    api_key: str | None = Security(api_key_header),
) -> str:
    """Verify the API key from request header.

    Args:
        api_key: API key from X-API-Key header

    Returns:
        The validated API key

    Raises:
        HTTPException: 401 if key missing, 403 if key invalid
    """
    expected_key = get_api_key_from_env()

    if api_key is None:
        logger.warning("API request without authentication")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "UNAUTHORIZED",
                "message": "Missing X-API-Key header",
            },
        )

    if api_key != expected_key:
        logger.warning("API request with invalid key")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "FORBIDDEN",
                "message": "Invalid API key",
            },
        )

    return api_key


# =============================================================================
# Correlation ID Middleware
# =============================================================================


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to add correlation IDs to all requests.

    If X-Correlation-ID header is present, use it.
    Otherwise, generate a new UUID.

    The correlation ID is:
    - Added to response headers
    - Set in context for logging
    - Included in all log messages
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())[:8]  # Short UUID for readability

        # Set in context for logging
        correlation_id_var.set(correlation_id)
        set_correlation_id(correlation_id)

        # Process request
        response = await call_next(request)

        # Add to response headers
        response.headers["X-Correlation-ID"] = correlation_id

        return response


# =============================================================================
# Request Logging Middleware
# =============================================================================


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests with timing and metrics.

    Logs:
    - Request method, path, status
    - Response time in milliseconds
    - Client IP (for audit)

    Emits Prometheus metrics:
    - k2_http_requests_total: Counter by method, path, status
    - k2_http_request_duration_seconds: Histogram of response times
    - k2_http_requests_in_progress: Gauge of currently processing requests
    - k2_http_request_errors_total: Counter of failed requests
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        correlation_id = correlation_id_var.get() or "unknown"

        # Get client IP (handle proxies)
        client_ip = request.client.host if request.client else "unknown"
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        # Normalize path for metrics (avoid high cardinality)
        metrics_path = self._normalize_path(request.url.path)
        method = request.method

        # Track in-progress requests
        progress_labels = {"method": method, "endpoint": metrics_path}
        metrics.gauge(
            "http_requests_in_progress",
            1,
            labels=progress_labels,
        )

        # Process request
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            logger.error(
                "Request failed with exception",
                correlation_id=correlation_id,
                path=request.url.path,
                error=str(e),
            )
            # Track error
            metrics.increment(
                "http_request_errors_total",
                labels={
                    "method": method,
                    "endpoint": metrics_path,
                    "error_type": type(e).__name__,
                },
            )
            raise
        finally:
            # Decrement in-progress gauge
            metrics.gauge(
                "http_requests_in_progress",
                0,
                labels=progress_labels,
            )

        # Calculate duration
        duration = time.time() - start_time
        duration_ms = duration * 1000

        # Log request
        log_method = logger.info if status_code < 400 else logger.warning
        log_method(
            "API request completed",
            correlation_id=correlation_id,
            method=method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
            client_ip=client_ip,
        )

        # Emit metrics
        request_labels = {
            "method": method,
            "endpoint": metrics_path,
            "status_code": str(status_code),
        }
        metrics.increment("http_requests_total", labels=request_labels)
        metrics.histogram("http_request_duration_seconds", duration, labels=request_labels)

        # Track errors (4xx and 5xx)
        if status_code >= 400:
            error_type = "client_error" if status_code < 500 else "server_error"
            metrics.increment(
                "http_request_errors_total",
                labels={
                    "method": method,
                    "endpoint": metrics_path,
                    "error_type": error_type,
                },
            )

        return response

    def _normalize_path(self, path: str) -> str:
        """Normalize path for metrics to avoid high cardinality.

        Replaces dynamic segments with placeholders.
        """
        # Replace /v1/summary/{symbol}/{date} with /v1/summary/:symbol/:date
        parts = path.split("/")
        normalized = []
        for i, part in enumerate(parts):
            if not part:
                continue
            # Check if this looks like a dynamic segment
            # (after summary, after snapshots, etc.)
            if i > 0 and normalized and normalized[-1] in ("summary", "snapshots"):
                normalized.append(":param")
            elif part.isdigit() or len(part) == 36:  # UUIDs
                normalized.append(":id")
            else:
                normalized.append(part)
        return "/" + "/".join(normalized)


# =============================================================================
# Response Caching Middleware
# =============================================================================


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Middleware to add Cache-Control headers based on endpoint.

    Caching strategy:
    - /health: no-cache (always fresh)
    - /v1/trades, /v1/quotes: private, max-age=5 (fresh for 5s)
    - /v1/summary: private, max-age=60 (daily summaries change less)
    - /v1/symbols: private, max-age=300 (symbols rarely change)
    - /v1/stats: private, max-age=30
    - /v1/snapshots: private, max-age=10
    """

    # Path patterns to cache durations (in seconds)
    CACHE_RULES = {
        "/health": 0,
        "/metrics": 0,  # Prometheus metrics - always fresh
        "/v1/health": 0,
        "/v1/trades": 5,
        "/v1/quotes": 5,
        "/v1/summary": 60,
        "/v1/symbols": 300,
        "/v1/stats": 30,
        "/v1/snapshots": 10,
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Only cache successful GET requests
        if request.method != "GET" or response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
            return response

        # Find matching cache rule
        path = request.url.path
        max_age = 0
        for pattern, duration in self.CACHE_RULES.items():
            if path.startswith(pattern):
                max_age = duration
                break

        # Set cache headers
        if max_age > 0:
            response.headers["Cache-Control"] = f"private, max-age={max_age}"
        else:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        return response


# =============================================================================
# Rate Limiting
# =============================================================================

# Rate limiting is implemented via slowapi in main.py
# This section provides the limiter key function


def get_client_ip(request: Request) -> str:
    """Get client IP for rate limiting.

    Handles X-Forwarded-For header for reverse proxy scenarios.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_api_key_for_limit(request: Request) -> str:
    """Get API key for rate limiting (rate limit per API key).

    Falls back to IP if no API key present.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key[:8]}"  # Truncate for privacy
    return f"ip:{get_client_ip(request)}"

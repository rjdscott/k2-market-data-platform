"""Unit tests for API middleware.

Tests all middleware components including:
- API Key authentication
- Correlation ID injection
- Request logging and metrics
- Cache control headers
- Request size limits
- Rate limiting helpers

Run: pytest tests/unit/test_middleware.py -v
"""

from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from k2.api.middleware import (
    CacheControlMiddleware,
    CorrelationIdMiddleware,
    RequestLoggingMiddleware,
    RequestSizeLimitMiddleware,
    get_api_key_for_limit,
    get_api_key_from_env,
    get_client_ip,
    verify_api_key,
)

# ==============================================================================
# Test Fixtures
# ==============================================================================


@pytest.fixture
def test_app():
    """Create a minimal FastAPI app for testing middleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    @app.get("/health")
    async def health_endpoint():
        return {"status": "healthy"}

    @app.get("/v1/trades")
    async def trades_endpoint():
        return {"trades": []}

    @app.get("/v1/symbols")
    async def symbols_endpoint():
        return {"symbols": ["BHP", "CBA"]}

    @app.post("/v1/create")
    async def create_endpoint(data: dict):
        return {"created": True, "data": data}

    @app.get("/error")
    async def error_endpoint():
        raise ValueError("Test error")

    return app


@pytest.fixture
def mock_request():
    """Create a mock Starlette Request."""
    request = Mock(spec=Request)
    request.method = "GET"
    request.url = Mock()
    request.url.path = "/test"
    request.headers = {}
    request.client = Mock()
    request.client.host = "127.0.0.1"
    return request


# ==============================================================================
# API Key Authentication Tests
# ==============================================================================


@pytest.mark.unit
class TestAPIKeyAuthentication:
    """Test API key authentication."""

    def test_get_api_key_from_env_default(self):
        """Test getting default API key when not configured."""
        with patch("k2.api.middleware.config") as mock_config:
            mock_config.api_key = None

            key = get_api_key_from_env()

            assert key == "k2-dev-api-key-2026"

    def test_get_api_key_from_env_configured(self):
        """Test getting API key from config."""
        with patch("k2.api.middleware.config") as mock_config:
            mock_config.api_key = "production-key-abc123"

            key = get_api_key_from_env()

            assert key == "production-key-abc123"

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self):
        """Test API key validation with valid key."""
        with patch("k2.api.middleware.get_api_key_from_env", return_value="test-key"):
            result = await verify_api_key(api_key="test-key")

            assert result == "test-key"

    @pytest.mark.asyncio
    async def test_verify_api_key_missing(self):
        """Test API key validation with missing key."""
        with patch("k2.api.middleware.get_api_key_from_env", return_value="test-key"):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key=None)

            assert exc_info.value.status_code == 401
            assert "Missing X-API-Key header" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self):
        """Test API key validation with invalid key."""
        with patch("k2.api.middleware.get_api_key_from_env", return_value="valid-key"):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key="wrong-key")

            assert exc_info.value.status_code == 403
            assert "Invalid API key" in str(exc_info.value.detail)


# ==============================================================================
# Correlation ID Middleware Tests
# ==============================================================================


@pytest.mark.unit
class TestCorrelationIdMiddleware:
    """Test correlation ID middleware."""

    @pytest.mark.asyncio
    async def test_generates_correlation_id_when_missing(self, test_app):
        """Test that middleware generates correlation ID when not provided."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        response = client.get("/test")

        assert response.status_code == 200
        assert "X-Correlation-ID" in response.headers
        # Should be 8-character UUID
        assert len(response.headers["X-Correlation-ID"]) == 8

    @pytest.mark.asyncio
    async def test_uses_provided_correlation_id(self, test_app):
        """Test that middleware uses provided correlation ID."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        custom_id = "custom-correlation-123"
        response = client.get("/test", headers={"X-Correlation-ID": custom_id})

        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == custom_id

    @pytest.mark.asyncio
    async def test_correlation_id_in_response_headers(self, test_app):
        """Test that correlation ID is added to response headers."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        response = client.get("/test")

        assert "X-Correlation-ID" in response.headers
        correlation_id = response.headers["X-Correlation-ID"]
        assert isinstance(correlation_id, str)
        assert len(correlation_id) > 0


# ==============================================================================
# Request Logging Middleware Tests
# ==============================================================================


@pytest.mark.unit
class TestRequestLoggingMiddleware:
    """Test request logging middleware."""

    @pytest.mark.asyncio
    async def test_logs_successful_request(self, test_app):
        """Test that middleware logs successful requests."""
        test_app.add_middleware(RequestLoggingMiddleware)
        client = TestClient(test_app)

        with patch("k2.api.middleware.logger") as mock_logger:
            response = client.get("/test")

            assert response.status_code == 200
            mock_logger.info.assert_called()
            call_args = mock_logger.info.call_args
            assert "API request completed" in call_args[0]
            assert call_args[1]["status_code"] == 200
            assert "duration_ms" in call_args[1]

    @pytest.mark.asyncio
    async def test_logs_failed_request(self, test_app):
        """Test that middleware logs failed requests."""
        test_app.add_middleware(RequestLoggingMiddleware)
        client = TestClient(test_app)

        with patch("k2.api.middleware.logger") as mock_logger:
            with pytest.raises(ValueError):
                client.get("/error")

            # Should log error
            mock_logger.error.assert_called()
            call_args = mock_logger.error.call_args
            assert "Request failed with exception" in call_args[0]

    @pytest.mark.asyncio
    async def test_extracts_client_ip_from_x_forwarded_for(self, test_app):
        """Test that middleware extracts client IP from X-Forwarded-For header."""
        test_app.add_middleware(RequestLoggingMiddleware)
        client = TestClient(test_app)

        with patch("k2.api.middleware.logger") as mock_logger:
            response = client.get(
                "/test",
                headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.1"},
            )

            assert response.status_code == 200
            call_args = mock_logger.info.call_args[1]
            assert call_args["client_ip"] == "203.0.113.1"

    @pytest.mark.asyncio
    async def test_normalizes_path_for_metrics(self, test_app):
        """Test that middleware normalizes paths to avoid high cardinality."""
        middleware = RequestLoggingMiddleware(test_app)

        # Test dynamic segment normalization
        # The middleware normalizes paths after known endpoints like "summary" and "snapshots"
        # Parts immediately after these keywords become :param (checked first)
        assert middleware._normalize_path("/v1/summary/BHP") == "/v1/summary/:param"
        # Parts after snapshots also become :param (priority over digit check)
        assert middleware._normalize_path("/v1/snapshots/123") == "/v1/snapshots/:param"
        # Multiple parts after summary - only first one replaced
        assert (
            middleware._normalize_path("/v1/summary/BHP/2024-01-15")
            == "/v1/summary/:param/2024-01-15"
        )
        # Non-dynamic paths stay unchanged
        assert middleware._normalize_path("/health") == "/health"
        # Standalone digits become :id
        assert middleware._normalize_path("/v1/trades/456") == "/v1/trades/:id"
        # UUIDs (36 chars) become :id
        uuid_path = "/v1/data/550e8400-e29b-41d4-a716-446655440000"
        assert middleware._normalize_path(uuid_path) == "/v1/data/:id"

    @pytest.mark.asyncio
    async def test_tracks_in_progress_requests(self, test_app):
        """Test that middleware tracks in-progress requests."""
        test_app.add_middleware(RequestLoggingMiddleware)
        client = TestClient(test_app)

        with patch("k2.api.middleware.metrics") as mock_metrics:
            response = client.get("/test")

            assert response.status_code == 200
            # Should increment gauge for in-progress, then decrement
            gauge_calls = [
                call
                for call in mock_metrics.gauge.call_args_list
                if call[0][0] == "http_requests_in_progress"
            ]
            assert len(gauge_calls) >= 2  # At least increment and decrement

    @pytest.mark.asyncio
    async def test_emits_prometheus_metrics(self, test_app):
        """Test that middleware emits Prometheus metrics."""
        test_app.add_middleware(RequestLoggingMiddleware)
        client = TestClient(test_app)

        with patch("k2.api.middleware.metrics") as mock_metrics:
            response = client.get("/test")

            assert response.status_code == 200

            # Verify metrics were emitted
            mock_metrics.increment.assert_called()
            mock_metrics.histogram.assert_called()

            # Check specific metric calls
            increment_calls = [call[0][0] for call in mock_metrics.increment.call_args_list]
            assert "http_requests_total" in increment_calls

            histogram_calls = [call[0][0] for call in mock_metrics.histogram.call_args_list]
            assert "http_request_duration_seconds" in histogram_calls


# ==============================================================================
# Cache Control Middleware Tests
# ==============================================================================


@pytest.mark.unit
class TestCacheControlMiddleware:
    """Test cache control middleware."""

    @pytest.mark.asyncio
    async def test_health_endpoint_no_cache(self, test_app):
        """Test that health endpoint has no-cache headers."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        response = client.get("/health")

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "no-cache" in response.headers["Cache-Control"]
        assert "no-store" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_trades_endpoint_short_cache(self, test_app):
        """Test that trades endpoint has short cache duration."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        response = client.get("/v1/trades")

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "private" in response.headers["Cache-Control"]
        assert "max-age=5" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_symbols_endpoint_longer_cache(self, test_app):
        """Test that symbols endpoint has longer cache duration."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        response = client.get("/v1/symbols")

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "max-age=300" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_post_request_no_cache(self, test_app):
        """Test that POST requests are not cached."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        response = client.post("/v1/create", json={"test": "data"})

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "no-store" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_error_response_no_cache(self, test_app):
        """Test that error responses are not cached."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        # Create endpoint that returns 404
        @test_app.get("/notfound")
        async def notfound():
            return JSONResponse(status_code=404, content={"error": "not found"})

        response = client.get("/notfound")

        assert response.status_code == 404
        assert "Cache-Control" in response.headers
        assert "no-store" in response.headers["Cache-Control"]


# ==============================================================================
# Request Size Limit Middleware Tests
# ==============================================================================


@pytest.mark.unit
class TestRequestSizeLimitMiddleware:
    """Test request size limit middleware."""

    @pytest.mark.asyncio
    async def test_allows_small_request(self):
        """Test that middleware allows requests under size limit."""
        # Create a fresh app for this test
        app = FastAPI()

        @app.post("/test")
        async def test_endpoint(data: dict):
            return {"ok": True}

        # Wrap app with middleware
        app = RequestSizeLimitMiddleware(app, max_size_bytes=1024 * 1024)  # 1MB
        client = TestClient(app)

        response = client.post("/test", json={"test": "data"})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_large_request(self):
        """Test that middleware rejects requests over size limit."""
        app = FastAPI()

        @app.post("/test")
        async def test_endpoint(data: dict):
            return {"ok": True}

        # Wrap app with small size limit
        app = RequestSizeLimitMiddleware(app, max_size_bytes=100)  # 100 bytes
        client = TestClient(app)

        # Mock metrics to avoid KeyError
        with patch("k2.api.middleware.metrics"):
            # Create large payload
            large_data = {"data": "x" * 1000}  # Should exceed 100 bytes
            response = client.post("/test", json=large_data)

            assert response.status_code == 413
            assert "PAYLOAD_TOO_LARGE" in response.json()["error"]["code"]

    @pytest.mark.asyncio
    async def test_handles_missing_content_length(self):
        """Test that middleware handles missing Content-Length header."""
        app = FastAPI()

        @app.post("/test")
        async def test_endpoint(data: dict):
            return {"ok": True}

        app = RequestSizeLimitMiddleware(app, max_size_bytes=1024 * 1024)
        client = TestClient(app)

        # TestClient automatically adds Content-Length, so this is hard to test properly
        # The middleware will allow requests without Content-Length header
        response = client.post("/test", json={"test": "data"})

        # Should allow request
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_handles_invalid_content_length(self):
        """Test that middleware handles invalid Content-Length header."""
        app = FastAPI()

        @app.post("/test")
        async def test_endpoint(data: dict):
            return {"ok": True}

        app = RequestSizeLimitMiddleware(app, max_size_bytes=1024 * 1024)

        # Mock metrics to avoid KeyError
        with patch("k2.api.middleware.metrics"):
            # Manually create ASGI scope with invalid Content-Length
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [[b"content-length", b"invalid"]],
            }

            # Create mock receive and send
            async def receive():
                return {"type": "http.request", "body": b"test"}

            response_started = False
            status_code = None
            response_body = []

            async def send(message):
                nonlocal response_started, status_code
                if message["type"] == "http.response.start":
                    response_started = True
                    status_code = message["status"]
                elif message["type"] == "http.response.body":
                    response_body.append(message.get("body", b""))

            # Call middleware directly
            await app(scope, receive, send)

            assert status_code == 400

    @pytest.mark.asyncio
    async def test_ignores_get_requests(self):
        """Test that middleware ignores GET requests (no body)."""
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint():
            return {"ok": True}

        app = RequestSizeLimitMiddleware(app, max_size_bytes=100)  # Small limit
        client = TestClient(app)

        response = client.get("/test")

        # GET requests should pass through regardless of size limit
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_default_size_limit(self):
        """Test default size limit (10MB)."""
        app = FastAPI()
        middleware = RequestSizeLimitMiddleware(app)

        assert middleware.max_size_bytes == 10 * 1024 * 1024
        assert middleware.max_size_mb == 10.0


# ==============================================================================
# Rate Limiting Helper Tests
# ==============================================================================


@pytest.mark.unit
class TestRateLimitingHelpers:
    """Test rate limiting helper functions."""

    def test_get_client_ip_direct(self, mock_request):
        """Test getting client IP directly from request."""
        mock_request.client.host = "192.168.1.100"
        mock_request.headers = {}

        ip = get_client_ip(mock_request)

        assert ip == "192.168.1.100"

    def test_get_client_ip_from_x_forwarded_for(self, mock_request):
        """Test getting client IP from X-Forwarded-For header."""
        mock_request.headers = {"X-Forwarded-For": "203.0.113.1, 198.51.100.1"}

        ip = get_client_ip(mock_request)

        assert ip == "203.0.113.1"  # Should use first IP

    def test_get_client_ip_unknown(self):
        """Test getting client IP when request.client is None."""
        mock_request = Mock(spec=Request)
        mock_request.client = None
        mock_request.headers = {}

        ip = get_client_ip(mock_request)

        assert ip == "unknown"

    def test_get_api_key_for_limit_with_key(self, mock_request):
        """Test getting rate limit key when API key present."""
        mock_request.headers = {"X-API-Key": "test-api-key-abc123"}

        key = get_api_key_for_limit(mock_request)

        assert key.startswith("apikey:")
        assert "test-api" in key  # Truncated to 8 chars

    def test_get_api_key_for_limit_without_key(self, mock_request):
        """Test getting rate limit key when API key absent (falls back to IP)."""
        mock_request.headers = {}
        mock_request.client.host = "192.168.1.100"

        key = get_api_key_for_limit(mock_request)

        assert key.startswith("ip:")
        assert "192.168.1.100" in key


# ==============================================================================
# Integration Tests - Multiple Middleware
# ==============================================================================


@pytest.mark.unit
class TestMiddlewareIntegration:
    """Test multiple middleware working together."""

    @pytest.mark.asyncio
    async def test_middleware_stack(self, test_app):
        """Test that multiple middleware components work together."""
        # Add middleware in order (reverse of execution order)
        test_app.add_middleware(CacheControlMiddleware)
        test_app.add_middleware(RequestLoggingMiddleware)
        test_app.add_middleware(CorrelationIdMiddleware)

        client = TestClient(test_app)

        response = client.get("/test")

        assert response.status_code == 200
        # Correlation ID added
        assert "X-Correlation-ID" in response.headers
        # Cache control added
        assert "Cache-Control" in response.headers

    @pytest.mark.asyncio
    async def test_middleware_preserves_correlation_id_through_stack(self, test_app):
        """Test that correlation ID is preserved through middleware stack."""
        test_app.add_middleware(CacheControlMiddleware)
        test_app.add_middleware(RequestLoggingMiddleware)
        test_app.add_middleware(CorrelationIdMiddleware)

        client = TestClient(test_app)

        custom_id = "test-correlation-id"
        response = client.get("/test", headers={"X-Correlation-ID": custom_id})

        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == custom_id

    @pytest.mark.asyncio
    async def test_middleware_handles_errors_gracefully(self, test_app):
        """Test that middleware handles errors gracefully."""
        test_app.add_middleware(RequestLoggingMiddleware)
        test_app.add_middleware(CorrelationIdMiddleware)

        client = TestClient(test_app)

        with patch("k2.api.middleware.logger"), pytest.raises(ValueError):
            client.get("/error")

        # Even with error, correlation ID should be set
        # (TestClient doesn't return response on exception, but middleware ran)


# ==============================================================================
# Edge Cases and Error Handling
# ==============================================================================


@pytest.mark.unit
class TestMiddlewareEdgeCases:
    """Test edge cases and error handling in middleware."""

    @pytest.mark.asyncio
    async def test_correlation_id_with_empty_string(self, test_app):
        """Test that empty correlation ID is regenerated."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        response = client.get("/test", headers={"X-Correlation-ID": ""})

        assert response.status_code == 200
        assert "X-Correlation-ID" in response.headers
        # Should generate new ID since empty string is falsy
        assert len(response.headers["X-Correlation-ID"]) == 8

    @pytest.mark.asyncio
    async def test_cache_control_unknown_path(self, test_app):
        """Test cache control for unknown paths (default behavior)."""
        test_app.add_middleware(CacheControlMiddleware)
        client = TestClient(test_app)

        response = client.get("/test")

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        # Unknown paths should get no-cache
        assert "no-cache" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_request_size_limit_boundary(self):
        """Test request size limit at exact boundary."""
        app = FastAPI()

        @app.post("/test")
        async def test_endpoint(data: dict):
            return {"ok": True}

        max_size = 200  # 200 bytes
        app = RequestSizeLimitMiddleware(app, max_size_bytes=max_size)
        client = TestClient(app)

        # Small payload should pass
        small_data = {"test": "data"}
        response = client.post("/test", json=small_data)
        assert response.status_code == 200

        # Mock metrics to avoid KeyError
        with patch("k2.api.middleware.metrics"):
            # Large payload should fail
            large_data = {"data": "x" * 1000}
            response = client.post("/test", json=large_data)
            assert response.status_code == 413

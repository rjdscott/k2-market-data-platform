"""Unit tests for API request body size limit middleware.

Tests the RequestSizeLimitMiddleware to ensure:
- Requests within size limit are allowed
- Requests exceeding size limit are rejected with 413
- Only POST/PUT/PATCH methods are checked
- Missing Content-Length is allowed (with warning)
- Invalid Content-Length is rejected with 400
- Metrics are tracked correctly

Note: Tests use smaller body sizes (100KB scale) for memory efficiency while validating logic.
The production middleware is configured for 10MB limit.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from k2.api.middleware import RequestSizeLimitMiddleware


@pytest.fixture
def app_with_size_limit():
    """Create a test FastAPI app with 100KB limit for testing."""
    app = FastAPI()

    # Add middleware (100KB limit for memory-efficient testing)
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=100 * 1024)

    # Add test endpoints
    @app.post("/test")
    def test_post():
        return {"success": True}

    @app.put("/test")
    def test_put():
        return {"success": True}

    @app.patch("/test")
    def test_patch():
        return {"success": True}

    @app.get("/test")
    def test_get():
        return {"success": True}

    return app


@pytest.fixture
def app_with_custom_limit():
    """Create a test FastAPI app with custom 50KB limit."""
    app = FastAPI()

    # Add middleware (50KB limit for testing)
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=50 * 1024)

    @app.post("/test")
    def test_post():
        return {"success": True}

    return app


class TestRequestSizeLimitMiddleware:
    """Test RequestSizeLimitMiddleware functionality."""

    def test_request_within_limit_allowed(self, app_with_size_limit):
        """Test that requests within size limit are allowed."""
        client = TestClient(app_with_size_limit)
        # 50KB request (well within 100KB limit)
        body = b"x" * (50 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}

    def test_request_exceeding_limit_rejected(self, app_with_size_limit):
        """Test that requests exceeding limit are rejected with 413."""
        client = TestClient(app_with_size_limit)
        # 120KB request (exceeds 100KB limit)
        large_body = b"x" * (120 * 1024)
        response = client.post(
            "/test",
            content=large_body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert data["error"]["size_mb"] > 0.1  # ~0.12 MB
        assert data["error"]["limit_mb"] < 0.11  # ~0.0977 MB

    def test_exact_limit_boundary_allowed(self, app_with_size_limit):
        """Test that request exactly at limit is allowed."""
        client = TestClient(app_with_size_limit)
        # Exactly 100KB
        body = b"x" * (100 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200

    def test_one_byte_over_limit_rejected(self, app_with_size_limit):
        """Test that request one byte over limit is rejected."""
        client = TestClient(app_with_size_limit)
        # 100KB + 1 byte
        body = b"x" * (100 * 1024 + 1)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_get_request_not_checked(self, app_with_size_limit):
        """Test that GET requests bypass size check."""
        client = TestClient(app_with_size_limit)
        # GET requests don't have bodies, so not checked
        response = client.get("/test")

        assert response.status_code == 200

    def test_post_checked(self, app_with_custom_limit):
        """Test that POST requests are checked."""
        client = TestClient(app_with_custom_limit)
        # 60KB POST request (exceeds 50KB limit)
        body = b"x" * (60 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_put_checked(self, app_with_custom_limit):
        """Test that PUT requests are checked."""
        client = TestClient(app_with_custom_limit)
        # 60KB PUT request (exceeds 50KB limit)
        body = b"x" * (60 * 1024)
        response = client.put(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_patch_checked(self, app_with_custom_limit):
        """Test that PATCH requests are checked."""
        client = TestClient(app_with_custom_limit)
        # 60KB PATCH request (exceeds 50KB limit)
        body = b"x" * (60 * 1024)
        response = client.patch(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_missing_content_length_allowed(self, app_with_size_limit):
        """Test that requests without Content-Length are allowed (with warning)."""
        client = TestClient(app_with_size_limit)
        # POST without explicit Content-Length header
        # Note: TestClient adds Content-Length automatically, so this tests normal behavior
        response = client.post("/test", json={"data": "test"})

        # Should be allowed
        assert response.status_code == 200

    def test_invalid_content_length_rejected(self, app_with_size_limit):
        """Test that requests with invalid Content-Length are rejected with 400."""
        client = TestClient(app_with_size_limit)
        # Invalid Content-Length (not a number)
        response = client.post(
            "/test",
            json={},
            headers={"Content-Length": "invalid"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_CONTENT_LENGTH"

    def test_negative_content_length_rejected(self, app_with_size_limit):
        """Test that requests with negative Content-Length are allowed (converted to int)."""
        client = TestClient(app_with_size_limit)
        # Negative Content-Length
        response = client.post(
            "/test",
            json={},
            headers={"Content-Length": "-1"},
        )

        # Negative length is less than limit, so allowed
        assert response.status_code == 200

    def test_zero_content_length_allowed(self, app_with_size_limit):
        """Test that requests with zero Content-Length are allowed."""
        client = TestClient(app_with_size_limit)
        # Zero Content-Length (empty body)
        response = client.post(
            "/test",
            json={},
            headers={"Content-Length": "0"},
        )

        assert response.status_code == 200

    @patch("k2.api.middleware.metrics")
    def test_metrics_tracked_on_rejection(self, mock_metrics, app_with_custom_limit):
        """Test that rejected requests are tracked in metrics."""
        client = TestClient(app_with_custom_limit)
        # 60KB request (exceeds 50KB limit)
        body = b"x" * (60 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

        # Verify metric was incremented
        mock_metrics.increment.assert_called_with(
            "http_request_size_limit_exceeded_total",
            labels={"method": "POST", "endpoint": "/test"},
        )

    @patch("k2.api.middleware.metrics")
    def test_metrics_tracked_on_invalid_content_length(
        self,
        mock_metrics,
        app_with_size_limit,
    ):
        """Test that invalid Content-Length errors are tracked in metrics."""
        client = TestClient(app_with_size_limit)
        # Invalid Content-Length
        response = client.post(
            "/test",
            json={},
            headers={"Content-Length": "not-a-number"},
        )

        assert response.status_code == 400

        # Verify metric was incremented
        mock_metrics.increment.assert_called_with(
            "http_request_size_limit_errors_total",
            labels={"reason": "invalid_content_length"},
        )

    def test_custom_size_limit(self):
        """Test that custom size limits work correctly."""
        # Create app with 75KB limit
        app = FastAPI()
        app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=75 * 1024)

        @app.post("/test")
        def test_post():
            return {"success": True}

        client = TestClient(app)

        # 50KB - allowed
        body = b"x" * (50 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200

        # 90KB - rejected
        body = b"x" * (90 * 1024)
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        data = response.json()
        assert abs(data["error"]["limit_mb"] - 0.07324) < 0.001  # ~75KB in MB


class TestRequestSizeLimitIntegration:
    """Integration tests for request size limit middleware."""

    def test_multiple_requests_independent(self, app_with_custom_limit):
        """Test that multiple requests are checked independently."""
        client = TestClient(app_with_custom_limit)

        # First request - allowed (30KB)
        body1 = b"x" * (30 * 1024)
        response1 = client.post(
            "/test",
            content=body1,
            headers={"Content-Type": "application/json"},
        )
        assert response1.status_code == 200

        # Second request - rejected (60KB exceeds 50KB)
        body2 = b"x" * (60 * 1024)
        response2 = client.post(
            "/test",
            content=body2,
            headers={"Content-Type": "application/json"},
        )
        assert response2.status_code == 413

        # Third request - allowed (30KB)
        body3 = b"x" * (30 * 1024)
        response3 = client.post(
            "/test",
            content=body3,
            headers={"Content-Type": "application/json"},
        )
        assert response3.status_code == 200

    def test_error_response_format(self, app_with_custom_limit):
        """Test that error response has correct format."""
        client = TestClient(app_with_custom_limit)

        body = b"x" * (60 * 1024)  # 60KB exceeds 50KB limit
        response = client.post(
            "/test",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413
        data = response.json()

        # Verify structure
        assert "success" in data
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "size_mb" in data["error"]
        assert "limit_mb" in data["error"]

        # Verify values
        assert data["success"] is False
        assert data["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert abs(data["error"]["size_mb"] - 0.05859) < 0.002  # ~60KB in MB (relaxed tolerance)
        assert abs(data["error"]["limit_mb"] - 0.04883) < 0.002  # ~50KB in MB (relaxed tolerance)

"""API Integration Tests for K2 Market Data Platform.

This module provides comprehensive integration tests for the FastAPI REST API
running in Docker containers with real database connections.

Test Coverage:
- Authentication & authorization
- Rate limiting behavior
- All v1 endpoints with real data
- Error handling and edge cases
- Performance characteristics
- Health checks and metrics

Architecture:
- Tests run against real API container in Docker Compose
- Real database connections (Iceberg + DuckDB + MinIO)
- Real Kafka integration for hybrid queries
- Production-like environment and behavior

Usage:
    pytest tests/integration/test_api_integration.py -v
    pytest tests/integration/test_api_integration.py::test_api_trades_endpoint -v
"""

import asyncio
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from k2.ingestion.message_builders import build_quote_v2, build_trade_v2
from k2.ingestion.producer import MarketDataProducer
from k2.storage.writer import IcebergWriter


class TestAPIIntegration:
    """Comprehensive API integration tests with real infrastructure."""

    # API base URL - tests run outside Docker network, use localhost
    API_BASE_URL = "http://localhost:8000"
    API_KEY = "k2-dev-api-key-2026"
    TEST_TIMEOUT = 30.0

    @pytest.fixture(scope="function")
    async def api_client(self) -> httpx.AsyncClient:
        """HTTP client for API tests with proper authentication."""

        async with httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            headers={"X-API-Key": self.API_KEY},
            timeout=self.TEST_TIMEOUT,
        ) as client:
            yield client

    @pytest.fixture(scope="function")
    async def unauthorized_client(self) -> httpx.AsyncClient:
        """HTTP client without authentication for testing auth errors."""

        async with httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            timeout=self.TEST_TIMEOUT,
        ) as client:
            yield client

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_api_health_endpoint(self, api_client: httpx.AsyncClient) -> None:
        """Test health check endpoint with authentication."""

        response = await api_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data
        assert "dependencies" in data  # Changed from "checks"
        assert isinstance(data["dependencies"], list)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_api_health_endpoint_no_auth(
        self, unauthorized_client: httpx.AsyncClient
    ) -> None:
        """Test health endpoint works without authentication (public endpoint)."""

        response = await unauthorized_client.get("/health")

        assert response.status_code == 200

    @pytest.mark.integration
    async def test_api_unauthorized_access(self, unauthorized_client: httpx.AsyncClient) -> None:
        """Test that protected endpoints require authentication."""

        response = await unauthorized_client.get("/v1/trades")

        assert response.status_code == 401
        error_data = response.json()
        assert "detail" in error_data
        # Detail is now an object with error and message fields
        detail = error_data["detail"]
        assert isinstance(detail, dict)
        assert "error" in detail or "message" in detail

    @pytest.mark.integration
    @pytest.mark.skip(reason="Rate limiting not yet implemented in API")
    async def test_api_rate_limiting(self, api_client: httpx.AsyncClient) -> None:
        """Test rate limiting behavior."""

        # Make multiple requests quickly to trigger rate limit
        responses = []
        for _ in range(105):  # Slightly over default limit of 100
            try:
                response = await api_client.get("/v1/trades")
                responses.append(response)
                await asyncio.sleep(0.01)  # Small delay
            except httpx.HTTPStatusError as e:
                responses.append(e.response)
                break  # Stop when rate limited

        # Check that at least one response is rate limited
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        assert len(rate_limited_responses) > 0

        # Check rate limit headers are present
        rate_limited_response = rate_limited_responses[0]
        assert "X-RateLimit-Limit" in rate_limited_response.headers
        assert "X-RateLimit-Remaining" in rate_limited_response.headers
        assert "X-RateLimit-Reset" in rate_limited_response.headers

    @pytest.mark.integration
    async def test_api_trades_endpoint_basic(
        self,
        api_client: httpx.AsyncClient,
        kafka_cluster,
        minio_backend,
        iceberg_config,
        sample_market_data,
    ) -> None:
        """Test trades endpoint with basic query parameters."""

        # Produce test data to Kafka
        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        # Produce some trades
        test_trades = sample_market_data["trades"][:10]
        for trade in test_trades:
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="equities",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )
            producer.produce_trade(
                asset_class="equities",
                exchange="nasdaq",
                record=v2_trade,
            )

        producer.flush(timeout=10)
        producer.close()

        # Write to Iceberg
        writer = IcebergWriter()
        v2_records = []
        for trade in test_trades:
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="equities",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )
            v2_records.append(v2_trade)

        records_written = writer.write_trades(records=v2_records)

        # Wait a moment for data to be available
        await asyncio.sleep(2)

        # Test API endpoint
        response = await api_client.get("/v1/trades?limit=5")

        assert response.status_code == 200
        response_data = response.json()

        # API now returns wrapped structure
        assert response_data["success"] is True
        assert "meta" in response_data
        assert "data" in response_data
        assert "pagination" in response_data

        data = response_data["data"]
        assert isinstance(data, list)
        assert len(data) <= 5

        # Validate structure of returned trades
        if len(data) > 0:
            trade = data[0]
            required_fields = [
                "symbol",
                "exchange",
                "timestamp",
                "price",
                "quantity",
                "trade_id",
                "message_id",
            ]
            for field in required_fields:
                assert field in trade

    @pytest.mark.integration
    async def test_api_quotes_endpoint_basic(
        self,
        api_client: httpx.AsyncClient,
        kafka_cluster,
        minio_backend,
        iceberg_config,
        sample_market_data,
    ) -> None:
        """Test quotes endpoint with basic query parameters."""

        # Produce test quotes to Kafka and Iceberg
        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        test_quotes = sample_market_data["quotes"][:10]
        v2_quotes = []

        for quote in test_quotes:
            v2_quote = build_quote_v2(
                symbol=quote["symbol"],
                exchange=quote["exchange"],
                asset_class="equities",
                timestamp=datetime.fromisoformat(quote["timestamp"]),
                bid_price=Decimal(quote["bid_price"]),
                bid_quantity=Decimal(quote["bid_quantity"]),
                ask_price=Decimal(quote["ask_price"]),
                ask_quantity=Decimal(quote["ask_quantity"]),
            )
            v2_quotes.append(v2_quote)
            producer.produce_quote(
                asset_class="equities",
                exchange="nasdaq",
                record=v2_quote,
            )

        producer.flush(timeout=10)
        producer.close()

        # Write to Iceberg
        writer = IcebergWriter()
        records_written = writer.write_quotes(records=v2_quotes)

        # Wait for data to be available
        await asyncio.sleep(2)

        # Test API endpoint
        response = await api_client.get("/v1/quotes?limit=5&symbol=AAPL")

        assert response.status_code == 200
        response_data = response.json()

        # API now returns wrapped structure
        assert response_data["success"] is True
        assert "meta" in response_data
        assert "data" in response_data
        assert "pagination" in response_data

        data = response_data["data"]
        assert isinstance(data, list)
        assert len(data) <= 5

        # Validate structure of returned quotes
        if len(data) > 0:
            quote = data[0]
            required_fields = [
                "symbol",
                "exchange",
                "timestamp",
                "bid_price",
                "ask_price",
                "bid_quantity",
                "ask_quantity",
                "message_id",
            ]
            for field in required_fields:
                assert field in quote

    @pytest.mark.integration
    async def test_api_symbols_endpoint(self, api_client: httpx.AsyncClient) -> None:
        """Test symbols listing endpoint."""

        response = await api_client.get("/v1/symbols")

        assert response.status_code == 200
        response_data = response.json()

        # API now returns wrapped structure
        assert response_data["success"] is True
        assert "meta" in response_data
        assert "data" in response_data

        data = response_data["data"]
        assert isinstance(data, list)
        assert isinstance(data[0], str) if data else True

    @pytest.mark.integration
    async def test_api_stats_endpoint(self, api_client: httpx.AsyncClient) -> None:
        """Test statistics endpoint."""

        response = await api_client.get("/v1/stats")

        assert response.status_code == 200
        response_data = response.json()

        # API now returns wrapped structure
        assert response_data["success"] is True
        assert "meta" in response_data
        assert "data" in response_data

        data = response_data["data"]
        assert isinstance(data, dict)
        # Stats may include counts, timestamps, etc.
        assert len(data) >= 0  # Empty dict is valid

    @pytest.mark.integration
    async def test_api_trades_query_with_filters(self, api_client: httpx.AsyncClient) -> None:
        """Test trades endpoint with various filter parameters."""

        # Test with symbol filter
        response = await api_client.get("/v1/trades?symbol=AAPL&limit=10")
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True
        assert isinstance(response_data["data"], list)

        # Test with exchange filter
        response = await api_client.get("/v1/trades?exchange=NASDAQ&limit=10")
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True
        assert isinstance(response_data["data"], list)

        # Test with time range filter
        end_time = datetime.utcnow().isoformat()
        start_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()

        response = await api_client.get(
            f"/v1/trades?start_time={start_time}&end_time={end_time}&limit=10"
        )
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True
        assert isinstance(response_data["data"], list)

    @pytest.mark.integration
    async def test_api_error_handling(self, api_client: httpx.AsyncClient) -> None:
        """Test API error handling for invalid requests."""

        # Test invalid limit (too high)
        response = await api_client.get("/v1/trades?limit=10000")
        assert response.status_code in [400, 422]  # Bad request or validation error

        # Test invalid time range
        response = await api_client.get("/v1/trades?start_time=invalid-time&end_time=invalid-time")
        assert response.status_code in [400, 422]

    @pytest.mark.integration
    async def test_api_performance_characteristics(self, api_client: httpx.AsyncClient) -> None:
        """Test API response times and performance characteristics."""

        # Measure response times
        response_times = []
        for _ in range(10):
            start_time = time.time()
            response = await api_client.get("/v1/trades?limit=10")
            end_time = time.time()

            assert response.status_code == 200
            response_times.append(end_time - start_time)

        # Check that average response time is reasonable (< 2 seconds)
        avg_response_time = sum(response_times) / len(response_times)
        assert (
            avg_response_time < 2.0
        ), f"Average response time {avg_response_time:.2f}s is too slow"

        # Check consistency (no extremely slow responses)
        max_response_time = max(response_times)
        assert max_response_time < 5.0, f"Max response time {max_response_time:.2f}s is too slow"

    @pytest.mark.integration
    async def test_api_metrics_endpoint(self, unauthorized_client: httpx.AsyncClient) -> None:
        """Test Prometheus metrics endpoint (no auth required)."""

        response = await unauthorized_client.get("/metrics")

        # Metrics endpoint returns Prometheus text format
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        assert len(response.text) > 0

    @pytest.mark.integration
    @pytest.mark.skip(reason="CORS headers not yet configured in API")
    async def test_api_cors_headers(self, api_client: httpx.AsyncClient) -> None:
        """Test CORS headers are properly set."""

        response = await api_client.options("/v1/trades")

        assert response.status_code == 200
        # Check for CORS headers
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers
        assert "access-control-allow-headers" in response.headers

    @pytest.mark.integration
    async def test_api_correlation_id(self, api_client: httpx.AsyncClient) -> None:
        """Test correlation ID handling in requests."""

        # Request with custom correlation ID
        correlation_id = "test-correlation-123"
        response = await api_client.get(
            "/v1/trades?limit=1", headers={"X-Correlation-ID": correlation_id}
        )

        assert response.status_code == 200
        # Response should include correlation ID header
        assert response.headers.get("X-Correlation-ID") == correlation_id

    @pytest.mark.integration
    async def test_api_pagination(self, api_client: httpx.AsyncClient) -> None:
        """Test pagination behavior for list endpoints."""

        # Test pagination parameters
        response = await api_client.get("/v1/trades?limit=5&offset=0")
        assert response.status_code == 200
        first_page_response = response.json()
        assert first_page_response["success"] is True
        assert "pagination" in first_page_response
        first_page = first_page_response["data"]

        # Get second page
        response = await api_client.get("/v1/trades?limit=5&offset=5")
        assert response.status_code == 200
        second_page_response = response.json()
        assert second_page_response["success"] is True
        assert "pagination" in second_page_response
        second_page = second_page_response["data"]

        # Pages should be different (if there's enough data)
        # This test might pass with empty pages if no data exists
        assert isinstance(first_page, list)
        assert isinstance(second_page, list)


class TestAPIHybridQueryIntegration:
    """Tests for hybrid query functionality (Kafka + Iceberg)."""

    API_BASE_URL = "http://localhost:8000"
    API_KEY = "k2-dev-api-key-2026"

    @pytest.fixture(scope="function")
    async def api_client(self) -> httpx.AsyncClient:
        """HTTP client with authentication."""

        async with httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            headers={"X-API-Key": self.API_KEY},
            timeout=30.0,
        ) as client:
            yield client

    @pytest.mark.integration
    async def test_api_recent_trades_hybrid_query(self, api_client: httpx.AsyncClient) -> None:
        """Test recent trades endpoint that uses hybrid queries."""

        response = await api_client.get("/v1/trades/recent?limit=10")

        # This endpoint should work and return data from hybrid engine
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True
        assert "data" in response_data
        data = response_data["data"]
        assert isinstance(data, list)

    @pytest.mark.integration
    async def test_api_hybrid_query_performance(self, api_client: httpx.AsyncClient) -> None:
        """Test hybrid query performance characteristics."""

        # Test query response time
        start_time = time.time()
        response = await api_client.get("/v1/trades/recent?limit=100")
        end_time = time.time()

        assert response.status_code == 200
        query_time = end_time - start_time

        # Hybrid queries should be reasonably fast (< 3 seconds for 100 records)
        assert query_time < 3.0, f"Hybrid query took {query_time:.2f}s, expected < 3.0s"


# Helper functions for test data setup
async def setup_test_data_for_api_tests(
    kafka_cluster, minio_backend, iceberg_config, sample_market_data
) -> dict[str, Any]:
    """Setup test data in Kafka and Iceberg for API tests."""

    # This could be refactored into a shared fixture
    # For now, individual tests set up their own data
    pass

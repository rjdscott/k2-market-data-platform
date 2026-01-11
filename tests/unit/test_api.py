"""Unit tests for K2 REST API.

Tests the API endpoints, middleware, and response models using FastAPI's
TestClient. These tests mock the QueryEngine and ReplayEngine to avoid
requiring Docker services.

Test categories:
- Authentication: API key validation
- Endpoints: trades, quotes, summary, symbols, stats, snapshots
- Middleware: correlation IDs, caching headers
- Error handling: 4xx and 5xx responses
"""

from datetime import datetime, date
from unittest.mock import MagicMock, patch
import pytest

from fastapi.testclient import TestClient

from k2.api.main import app
from k2.api.deps import get_query_engine, get_replay_engine, reset_engines
from k2.api.models import HealthStatus
from k2.query.engine import MarketSummary


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def mock_query_engine():
    """Create a mock QueryEngine for testing."""
    engine = MagicMock()

    # Mock query_trades
    engine.query_trades.return_value = [
        {
            "symbol": "BHP",
            "company_id": "BHP.AX",
            "exchange": "ASX",
            "exchange_timestamp": datetime(2024, 1, 15, 10, 30, 0),
            "price": 45.50,
            "volume": 1000,
            "qualifiers": "XT",
            "venue": "ASX",
            "buyer_id": "BROKER001",
            "ingestion_timestamp": datetime(2024, 1, 15, 10, 30, 1),
            "sequence_number": 12345,
        }
    ]

    # Mock query_quotes
    engine.query_quotes.return_value = [
        {
            "symbol": "BHP",
            "company_id": "BHP.AX",
            "exchange": "ASX",
            "exchange_timestamp": datetime(2024, 1, 15, 10, 30, 0),
            "bid_price": 45.48,
            "bid_volume": 500,
            "ask_price": 45.52,
            "ask_volume": 750,
            "ingestion_timestamp": datetime(2024, 1, 15, 10, 30, 1),
            "sequence_number": 12345,
        }
    ]

    # Mock get_market_summary
    engine.get_market_summary.return_value = MarketSummary(
        symbol="BHP",
        date=date(2024, 1, 15),
        open_price=45.20,
        high_price=46.10,
        low_price=45.00,
        close_price=45.80,
        volume=2500000,
        trade_count=15432,
        vwap=45.55,
    )

    # Mock get_symbols
    engine.get_symbols.return_value = ["BHP", "CBA", "CSL", "RIO"]

    # Mock get_stats
    engine.get_stats.return_value = {
        "s3_endpoint": "http://localhost:9000",
        "warehouse_path": "s3://warehouse",
        "connection_active": True,
        "trades_count": 100000,
        "quotes_count": 50000,
    }

    # Mock connection for health check
    engine.connection.execute.return_value.fetchone.return_value = (1,)

    return engine


@pytest.fixture(scope="module")
def mock_replay_engine():
    """Create a mock ReplayEngine for testing."""
    engine = MagicMock()

    # Mock list_snapshots with proper SnapshotInfo objects
    snapshot_mock = MagicMock()
    snapshot_mock.snapshot_id = 1234567890
    snapshot_mock.timestamp = datetime(2024, 1, 15, 10, 30, 0)
    snapshot_mock.manifest_list = "s3://warehouse/market_data/trades/metadata/snap-123.avro"
    snapshot_mock.summary = {"operation": "append", "added-records": "1000"}

    engine.list_snapshots.return_value = [snapshot_mock]

    return engine


@pytest.fixture(scope="module")
def client(mock_query_engine, mock_replay_engine):
    """Create a test client with mocked dependencies."""
    # Override dependencies
    app.dependency_overrides[get_query_engine] = lambda: mock_query_engine
    app.dependency_overrides[get_replay_engine] = lambda: mock_replay_engine

    yield TestClient(app)

    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
def api_key():
    """Return the default development API key."""
    return "k2-dev-api-key-2026"


@pytest.fixture
def auth_header(api_key):
    """Return authentication header."""
    return {"X-API-Key": api_key}


# =============================================================================
# Root & Health Tests
# =============================================================================


class TestRootEndpoint:
    """Tests for the root (/) endpoint."""

    def test_root_returns_api_info(self, client):
        """Root endpoint should return API information."""
        response = client.get("/")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "K2 Market Data Platform API"
        assert data["version"] == "1.0.0"
        assert "docs" in data
        assert "health" in data


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_returns_status(self, client):
        """Health endpoint should return overall status."""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ["healthy", "degraded", "unhealthy"]
        assert data["version"] == "1.0.0"
        assert "dependencies" in data

    def test_health_no_auth_required(self, client):
        """Health endpoint should not require authentication."""
        # No X-API-Key header
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_includes_correlation_id(self, client):
        """Health endpoint should include correlation ID in response."""
        response = client.get("/health")
        assert "X-Correlation-ID" in response.headers


# =============================================================================
# Authentication Tests
# =============================================================================


class TestAuthentication:
    """Tests for API key authentication."""

    def test_missing_api_key_returns_401(self, client):
        """Request without API key should return 401."""
        response = client.get("/v1/trades")
        assert response.status_code == 401

        data = response.json()
        assert "detail" in data

    def test_invalid_api_key_returns_403(self, client):
        """Request with invalid API key should return 403."""
        response = client.get(
            "/v1/trades",
            headers={"X-API-Key": "invalid-key"},
        )
        assert response.status_code == 403

    def test_valid_api_key_succeeds(self, client, auth_header):
        """Request with valid API key should succeed."""
        response = client.get("/v1/trades", headers=auth_header)
        assert response.status_code == 200


# =============================================================================
# Trades Endpoint Tests
# =============================================================================


class TestTradesEndpoint:
    """Tests for the /v1/trades endpoint."""

    def test_get_trades_returns_list(self, client, auth_header):
        """GET /v1/trades should return list of trades."""
        response = client.get("/v1/trades", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0

    def test_get_trades_with_symbol_filter(self, client, auth_header):
        """GET /v1/trades should accept symbol filter."""
        response = client.get(
            "/v1/trades",
            params={"symbol": "BHP"},
            headers=auth_header,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

    def test_get_trades_with_limit(self, client, auth_header):
        """GET /v1/trades should accept limit parameter."""
        response = client.get(
            "/v1/trades",
            params={"limit": 10},
            headers=auth_header,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["pagination"]["limit"] == 10

    def test_get_trades_limit_validation(self, client, auth_header):
        """GET /v1/trades should validate limit range."""
        # Limit too high
        response = client.get(
            "/v1/trades",
            params={"limit": 20000},  # Max is 10000
            headers=auth_header,
        )
        assert response.status_code == 422  # Validation error

    def test_get_trades_with_time_filter(self, client, auth_header):
        """GET /v1/trades should accept time range filters."""
        response = client.get(
            "/v1/trades",
            params={
                "start_time": "2024-01-15T09:00:00",
                "end_time": "2024-01-15T16:00:00",
            },
            headers=auth_header,
        )
        assert response.status_code == 200

    def test_get_trades_includes_meta(self, client, auth_header):
        """GET /v1/trades should include metadata."""
        response = client.get("/v1/trades", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        assert "meta" in data
        assert "correlation_id" in data["meta"]


# =============================================================================
# Quotes Endpoint Tests
# =============================================================================


class TestQuotesEndpoint:
    """Tests for the /v1/quotes endpoint."""

    def test_get_quotes_returns_list(self, client, auth_header):
        """GET /v1/quotes should return list of quotes."""
        response = client.get("/v1/quotes", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_get_quotes_includes_bid_ask(self, client, auth_header):
        """GET /v1/quotes should return bid/ask prices."""
        response = client.get("/v1/quotes", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        if data["data"]:
            quote = data["data"][0]
            assert "bid_price" in quote
            assert "ask_price" in quote


# =============================================================================
# Summary Endpoint Tests
# =============================================================================


class TestSummaryEndpoint:
    """Tests for the /v1/summary/{symbol}/{date} endpoint."""

    def test_get_summary_returns_ohlcv(self, client, auth_header):
        """GET /v1/summary should return OHLCV data."""
        response = client.get(
            "/v1/summary/BHP/2024-01-15",
            headers=auth_header,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["data"]["symbol"] == "BHP"
        assert "open_price" in data["data"]
        assert "high_price" in data["data"]
        assert "low_price" in data["data"]
        assert "close_price" in data["data"]
        assert "volume" in data["data"]
        assert "vwap" in data["data"]

    def test_get_summary_uppercase_symbol(self, client, auth_header):
        """GET /v1/summary should normalize symbol to uppercase."""
        response = client.get(
            "/v1/summary/bhp/2024-01-15",
            headers=auth_header,
        )
        assert response.status_code == 200

    def test_get_summary_invalid_date_format(self, client, auth_header):
        """GET /v1/summary should reject invalid date format."""
        response = client.get(
            "/v1/summary/BHP/15-01-2024",  # Wrong format
            headers=auth_header,
        )
        assert response.status_code == 422  # Validation error


# =============================================================================
# Symbols Endpoint Tests
# =============================================================================


class TestSymbolsEndpoint:
    """Tests for the /v1/symbols endpoint."""

    def test_get_symbols_returns_list(self, client, auth_header):
        """GET /v1/symbols should return list of symbols."""
        response = client.get("/v1/symbols", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_get_symbols_are_strings(self, client, auth_header):
        """GET /v1/symbols should return string symbols."""
        response = client.get("/v1/symbols", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        for symbol in data["data"]:
            assert isinstance(symbol, str)


# =============================================================================
# Stats Endpoint Tests
# =============================================================================


class TestStatsEndpoint:
    """Tests for the /v1/stats endpoint."""

    def test_get_stats_returns_info(self, client, auth_header):
        """GET /v1/stats should return database statistics."""
        response = client.get("/v1/stats", headers=auth_header)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "s3_endpoint" in data["data"]
        assert "warehouse_path" in data["data"]
        assert "connection_active" in data["data"]


# =============================================================================
# Snapshots Endpoint Tests
# =============================================================================


class TestSnapshotsEndpoint:
    """Tests for the /v1/snapshots endpoint."""

    def test_get_snapshots_returns_list(self, client, auth_header):
        """GET /v1/snapshots should return list of snapshots."""
        response = client.get(
            "/v1/snapshots",
            params={"table": "trades"},
            headers=auth_header,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_get_snapshots_invalid_table(self, client, auth_header):
        """GET /v1/snapshots should reject invalid table name."""
        response = client.get(
            "/v1/snapshots",
            params={"table": "invalid"},
            headers=auth_header,
        )
        assert response.status_code == 400


# =============================================================================
# Middleware Tests
# =============================================================================


class TestMiddleware:
    """Tests for API middleware functionality."""

    def test_correlation_id_generated(self, client, auth_header):
        """Requests without correlation ID should get one generated."""
        response = client.get("/v1/trades", headers=auth_header)
        assert "X-Correlation-ID" in response.headers
        assert len(response.headers["X-Correlation-ID"]) > 0

    def test_correlation_id_preserved(self, client, auth_header):
        """Requests with correlation ID should preserve it."""
        headers = {**auth_header, "X-Correlation-ID": "test-123"}
        response = client.get("/v1/trades", headers=headers)
        assert response.headers["X-Correlation-ID"] == "test-123"

    def test_cache_control_header_trades(self, client, auth_header):
        """Trades endpoint should have short cache duration."""
        response = client.get("/v1/trades", headers=auth_header)
        assert "Cache-Control" in response.headers
        assert "max-age=5" in response.headers["Cache-Control"]

    def test_cache_control_header_symbols(self, client, auth_header):
        """Symbols endpoint should have longer cache duration."""
        response = client.get("/v1/symbols", headers=auth_header)
        assert "Cache-Control" in response.headers
        assert "max-age=300" in response.headers["Cache-Control"]


# =============================================================================
# OpenAPI Documentation Tests
# =============================================================================


class TestOpenAPI:
    """Tests for OpenAPI documentation."""

    def test_openapi_endpoint_accessible(self, client):
        """OpenAPI JSON should be accessible."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_openapi_includes_endpoints(self, client):
        """OpenAPI should document all endpoints."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        assert "/v1/trades" in paths
        assert "/v1/quotes" in paths
        assert "/v1/symbols" in paths
        assert "/v1/stats" in paths
        assert "/health" in paths

    def test_swagger_ui_accessible(self, client):
        """Swagger UI should be accessible."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_accessible(self, client):
        """ReDoc should be accessible."""
        response = client.get("/redoc")
        assert response.status_code == 200


# =============================================================================
# POST Trades Query Tests
# =============================================================================


class TestPostTradesQuery:
    """Tests for POST /v1/trades/query endpoint."""

    def test_post_trades_query_basic(self, client, auth_header):
        """POST /v1/trades/query should return trades."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={"limit": 100},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "data" in data

    def test_post_trades_query_multi_symbol(self, client, auth_header):
        """POST /v1/trades/query should accept multiple symbols."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "symbols": ["BHP", "RIO"],
                "limit": 100,
            },
        )
        assert response.status_code == 200

    def test_post_trades_query_field_selection(self, client, auth_header):
        """POST /v1/trades/query should support field selection."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "fields": ["symbol", "price", "volume"],
                "limit": 100,
            },
        )
        assert response.status_code == 200

    def test_post_trades_query_invalid_field(self, client, auth_header):
        """POST /v1/trades/query should reject invalid fields."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "fields": ["symbol", "invalid_field"],
                "limit": 100,
            },
        )
        assert response.status_code == 422  # Validation error

    def test_post_trades_query_price_filter(self, client, auth_header):
        """POST /v1/trades/query should accept price filters."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "min_price": 40.0,
                "max_price": 50.0,
                "limit": 100,
            },
        )
        assert response.status_code == 200

    def test_post_trades_query_time_filter(self, client, auth_header):
        """POST /v1/trades/query should accept time range."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-31T23:59:59",
                "limit": 100,
            },
        )
        assert response.status_code == 200

    def test_post_trades_query_csv_format(self, client, auth_header):
        """POST /v1/trades/query should return CSV when requested."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "format": "csv",
                "limit": 100,
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers.get("content-disposition", "")

    def test_post_trades_query_parquet_format(self, client, auth_header):
        """POST /v1/trades/query should return Parquet when requested."""
        response = client.post(
            "/v1/trades/query",
            headers=auth_header,
            json={
                "format": "parquet",
                "limit": 100,
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"


# =============================================================================
# POST Quotes Query Tests
# =============================================================================


class TestPostQuotesQuery:
    """Tests for POST /v1/quotes/query endpoint."""

    def test_post_quotes_query_basic(self, client, auth_header):
        """POST /v1/quotes/query should return quotes."""
        response = client.post(
            "/v1/quotes/query",
            headers=auth_header,
            json={"limit": 100},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

    def test_post_quotes_query_multi_symbol(self, client, auth_header):
        """POST /v1/quotes/query should accept multiple symbols."""
        response = client.post(
            "/v1/quotes/query",
            headers=auth_header,
            json={
                "symbols": ["BHP", "RIO"],
                "limit": 100,
            },
        )
        assert response.status_code == 200

    def test_post_quotes_query_field_selection(self, client, auth_header):
        """POST /v1/quotes/query should support field selection."""
        response = client.post(
            "/v1/quotes/query",
            headers=auth_header,
            json={
                "fields": ["symbol", "bid_price", "ask_price"],
                "limit": 100,
            },
        )
        assert response.status_code == 200


# =============================================================================
# POST Replay Tests
# =============================================================================


class TestPostReplay:
    """Tests for POST /v1/replay endpoint."""

    def test_post_replay_basic(self, client, auth_header, mock_replay_engine):
        """POST /v1/replay should return batch with cursor."""
        # Setup mock for replay stats
        mock_replay_engine.get_replay_stats.return_value = {
            "total_records": 100,
            "min_timestamp": datetime(2024, 1, 1),
            "max_timestamp": datetime(2024, 1, 31),
        }

        response = client.post(
            "/v1/replay",
            headers=auth_header,
            json={
                "data_type": "trades",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-31T23:59:59",
                "batch_size": 100,
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "batch_info" in data

    def test_post_replay_with_symbol(self, client, auth_header, mock_replay_engine):
        """POST /v1/replay should accept symbol filter."""
        mock_replay_engine.get_replay_stats.return_value = {"total_records": 50}

        response = client.post(
            "/v1/replay",
            headers=auth_header,
            json={
                "data_type": "trades",
                "symbol": "BHP",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-31T23:59:59",
                "batch_size": 100,
            },
        )
        assert response.status_code == 200

    def test_post_replay_validates_time_range(self, client, auth_header):
        """POST /v1/replay should require start and end time."""
        response = client.post(
            "/v1/replay",
            headers=auth_header,
            json={
                "data_type": "trades",
                # Missing start_time and end_time
                "batch_size": 100,
            },
        )
        assert response.status_code == 422  # Validation error


# =============================================================================
# POST Snapshot Query Tests
# =============================================================================


class TestPostSnapshotQuery:
    """Tests for POST /v1/snapshots/{id}/query endpoint."""

    def test_post_snapshot_query_basic(self, client, auth_header, mock_replay_engine):
        """POST /v1/snapshots/{id}/query should return data at snapshot."""
        # Setup mock
        snapshot_mock = MagicMock()
        snapshot_mock.snapshot_id = 1234567890
        snapshot_mock.timestamp = datetime(2024, 1, 15, 10, 30, 0)
        mock_replay_engine.get_snapshot.return_value = snapshot_mock
        mock_replay_engine.query_at_snapshot.return_value = [
            {
                "symbol": "BHP",
                "exchange": "ASX",
                "exchange_timestamp": datetime(2024, 1, 15, 10, 0, 0),
                "price": 45.50,
                "volume": 1000,
            }
        ]

        response = client.post(
            "/v1/snapshots/1234567890/query",
            headers=auth_header,
            json={
                "data_type": "trades",
                "symbol": "BHP",
                "limit": 100,
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["snapshot_id"] == 1234567890

    def test_post_snapshot_query_not_found(self, client, auth_header, mock_replay_engine):
        """POST /v1/snapshots/{id}/query should return 404 for invalid snapshot."""
        mock_replay_engine.get_snapshot.return_value = None

        response = client.post(
            "/v1/snapshots/999999999/query",
            headers=auth_header,
            json={
                "data_type": "trades",
                "limit": 100,
            },
        )
        assert response.status_code == 404


# =============================================================================
# POST Aggregations Tests
# =============================================================================


class TestPostAggregations:
    """Tests for POST /v1/aggregations endpoint."""

    def test_post_aggregations_basic(self, client, auth_header, mock_query_engine):
        """POST /v1/aggregations should return bucketed data."""
        # Setup mock connection to return aggregation results
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "BHP",
                "bucket_start": datetime(2024, 1, 15, 10, 0, 0),
                "bucket_end": datetime(2024, 1, 15, 10, 5, 0),
                "open_price": 45.20,
                "high_price": 45.80,
                "low_price": 45.00,
                "close_price": 45.50,
                "volume": 10000,
                "trade_count": 100,
                "vwap": 45.40,
                "twap": 45.35,
            }
        ]
        mock_query_engine.connection.execute.return_value.fetchdf.return_value = mock_df

        response = client.post(
            "/v1/aggregations",
            headers=auth_header,
            json={
                "symbols": ["BHP"],
                "metrics": ["ohlcv", "vwap"],
                "interval": "5min",
                "start_time": "2024-01-15T09:00:00",
                "end_time": "2024-01-15T16:00:00",
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "request_summary" in data

    def test_post_aggregations_requires_symbols(self, client, auth_header):
        """POST /v1/aggregations should require at least one symbol."""
        response = client.post(
            "/v1/aggregations",
            headers=auth_header,
            json={
                "symbols": [],  # Empty
                "metrics": ["vwap"],
                "interval": "5min",
                "start_time": "2024-01-15T09:00:00",
                "end_time": "2024-01-15T16:00:00",
            },
        )
        assert response.status_code == 422  # Validation error

    def test_post_aggregations_requires_metrics(self, client, auth_header):
        """POST /v1/aggregations should require at least one metric."""
        response = client.post(
            "/v1/aggregations",
            headers=auth_header,
            json={
                "symbols": ["BHP"],
                "metrics": [],  # Empty
                "interval": "5min",
                "start_time": "2024-01-15T09:00:00",
                "end_time": "2024-01-15T16:00:00",
            },
        )
        assert response.status_code == 422  # Validation error

    def test_post_aggregations_csv_format(self, client, auth_header, mock_query_engine):
        """POST /v1/aggregations should return CSV when requested."""
        mock_df = MagicMock()
        mock_df.to_dict.return_value = []
        mock_query_engine.connection.execute.return_value.fetchdf.return_value = mock_df

        response = client.post(
            "/v1/aggregations",
            headers=auth_header,
            json={
                "symbols": ["BHP"],
                "metrics": ["vwap"],
                "interval": "1hour",
                "start_time": "2024-01-15T09:00:00",
                "end_time": "2024-01-15T16:00:00",
                "format": "csv",
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")


# =============================================================================
# POST Endpoints OpenAPI Tests
# =============================================================================


class TestPostEndpointsOpenAPI:
    """Tests for POST endpoints in OpenAPI documentation."""

    def test_openapi_includes_post_endpoints(self, client):
        """OpenAPI should document all POST endpoints."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})

        # Check POST endpoints exist
        assert "/v1/trades/query" in paths
        assert "post" in paths["/v1/trades/query"]

        assert "/v1/quotes/query" in paths
        assert "post" in paths["/v1/quotes/query"]

        assert "/v1/replay" in paths
        assert "post" in paths["/v1/replay"]

        assert "/v1/aggregations" in paths
        assert "post" in paths["/v1/aggregations"]

    def test_openapi_post_endpoints_have_request_body(self, client):
        """POST endpoints should have requestBody schema."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})

        # Check request body is documented
        assert "requestBody" in paths["/v1/trades/query"]["post"]
        assert "requestBody" in paths["/v1/quotes/query"]["post"]
        assert "requestBody" in paths["/v1/replay"]["post"]
        assert "requestBody" in paths["/v1/aggregations"]["post"]


# =============================================================================
# Prometheus Metrics Endpoint Tests
# =============================================================================


class TestMetricsEndpoint:
    """Tests for the /metrics Prometheus endpoint."""

    def test_metrics_returns_prometheus_format(self, client):
        """Metrics endpoint should return Prometheus exposition format."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Check content type is Prometheus format
        content_type = response.headers.get("content-type", "")
        assert "text/plain" in content_type or "text/plain" in content_type

    def test_metrics_includes_platform_info(self, client):
        """Metrics endpoint should include platform info metric."""
        response = client.get("/metrics")
        assert response.status_code == 200

        content = response.text
        # Platform info metric should be present
        assert "k2_platform_info" in content

    def test_metrics_includes_http_request_metrics(self, client, auth_header):
        """Metrics endpoint should include HTTP request metrics after traffic."""
        # Generate some traffic first
        client.get("/health")
        client.get("/v1/trades", headers=auth_header)

        # Now check metrics
        response = client.get("/metrics")
        assert response.status_code == 200

        content = response.text
        # Should have HTTP metrics
        assert "k2_http_requests_total" in content
        assert "k2_http_request_duration_seconds" in content

    def test_metrics_no_auth_required(self, client):
        """Metrics endpoint should not require authentication."""
        # No X-API-Key header
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_not_rate_limited(self, client):
        """Metrics endpoint should not be rate limited."""
        # Prometheus scrapes frequently - must not be limited
        for _ in range(20):
            response = client.get("/metrics")
            assert response.status_code == 200

    def test_metrics_includes_correlation_id(self, client):
        """Metrics endpoint should include correlation ID in response."""
        response = client.get("/metrics")
        assert "X-Correlation-ID" in response.headers

    def test_metrics_not_cached(self, client):
        """Metrics endpoint should not be cached."""
        response = client.get("/metrics")
        cache_control = response.headers.get("Cache-Control", "")
        # Should have no-cache or no-store
        assert "no-cache" in cache_control or "no-store" in cache_control

    def test_metrics_hidden_from_openapi(self, client):
        """Metrics endpoint should not appear in OpenAPI docs."""
        response = client.get("/openapi.json")
        data = response.json()
        paths = data.get("paths", {})

        # /metrics should not be documented (include_in_schema=False)
        assert "/metrics" not in paths

    def test_metrics_includes_histogram_buckets(self, client, auth_header):
        """Metrics should include histogram bucket data."""
        # Generate traffic
        client.get("/v1/trades", headers=auth_header)

        response = client.get("/metrics")
        content = response.text

        # Histogram buckets should be present
        assert "_bucket{" in content or "_bucket{" in content

    def test_metrics_includes_counter_totals(self, client, auth_header):
        """Metrics should include counter totals."""
        # Generate traffic
        client.get("/v1/trades", headers=auth_header)

        response = client.get("/metrics")
        content = response.text

        # Counter metrics should have _total suffix
        assert "_total{" in content or "_total " in content

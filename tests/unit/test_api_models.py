"""Unit tests for K2 API models and data validation.

Tests validate:
- Pydantic model field validation and type safety
- Business logic constraints and edge cases
- Input sanitization and security (SQL injection prevention)
- Timestamp handling and serialization
- Enum validation and field allowlists
- Complex query parameter validation
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from k2.api.models import (  # Base models; Enums and constants; Health and stats; Data models
    VALID_QUOTE_FIELDS,
    VALID_TRADE_FIELDS,
    AggregationInterval,
    AggregationMetric,
    AggregationRequest,
    APIResponse,
    DataType,
    ErrorResponse,
    HealthResponse,
    HealthStatus,
    OutputFormat,
    PaginationMeta,
    Quote,
    QuoteQueryRequest,
    ReplayRequest,
    StatsData,
    StatsResponse,
    Trade,
    TradeQueryRequest,
    TradesResponse,
)


class TestBaseModels:
    """Test base API response models."""

    def test_api_response_default_success(self):
        """APIResponse should default to success=True."""
        response = APIResponse()
        assert response.success is True
        assert response.meta == {}

    def test_api_response_with_metadata(self):
        """APIResponse should accept custom metadata."""
        meta = {"correlation_id": "test-123", "request_id": "req-456"}
        response = APIResponse(meta=meta)
        assert response.meta == meta

    def test_error_response_structure(self):
        """ErrorResponse should have proper structure."""
        error = {
            "code": "VALIDATION_ERROR",
            "message": "Invalid input",
            "details": {"field": "symbol", "value": ""},
        }
        response = ErrorResponse(error=error)

        assert response.success is False
        assert response.error == error

    def test_pagination_meta_validation(self):
        """PaginationMeta should validate numeric fields."""
        # Valid pagination
        pagination = PaginationMeta(limit=100, offset=0, total=500, has_more=True)
        assert pagination.limit == 100
        assert pagination.offset == 0
        assert pagination.total == 500
        assert pagination.has_more is True

    def test_pagination_meta_defaults(self):
        """PaginationMeta should have sensible defaults."""
        pagination = PaginationMeta(limit=50)
        assert pagination.limit == 50
        assert pagination.offset == 0
        assert pagination.total is None
        assert pagination.has_more is False


class TestTradeAndQuoteModels:
    """Test Trade and Quote data models."""

    def test_trade_model_validation(self):
        """Trade model should validate all required fields."""
        trade_data = {
            "message_id": "msg-123",
            "trade_id": "trade-456",
            "symbol": "BTCUSDT",
            "exchange": "BINANCE",
            "asset_class": "CRYPTO",
            "timestamp": "2024-01-15T10:30:00.123456",
            "price": 45000.50,
            "quantity": 0.5,
            "currency": "USDT",
            "side": "BUY",
            "source_sequence": 12345,
            "ingestion_timestamp": "2024-01-15T10:30:00.123456",
            "platform_sequence": 67890,
        }

        trade = Trade(**trade_data)
        assert trade.symbol == "BTCUSDT"
        assert trade.price == 45000.50
        assert trade.quantity == 0.5
        assert trade.side == "BUY"

    def test_trade_model_optional_fields(self):
        """Trade model should handle optional fields gracefully."""
        minimal_trade = {
            "message_id": "msg-123",
            "trade_id": "trade-456",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": "2024-01-15T10:30:00.123456",
            "price": 150.25,
            "quantity": 100.0,
            "currency": "USD",
            "side": "BUY",
            "ingestion_timestamp": "2024-01-15T10:30:00.123456",
        }

        trade = Trade(**minimal_trade)
        assert trade.trade_conditions is None
        assert trade.source_sequence is None
        assert trade.vendor_data is None

    def test_trade_model_timestamp_conversion(self):
        """Trade model should convert timestamps to ISO strings."""
        # Test with datetime object
        timestamp = datetime(2024, 1, 15, 10, 30, 0, 123456)
        trade_data = {
            "message_id": "msg-123",
            "trade_id": "trade-456",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": timestamp,
            "ingestion_timestamp": timestamp,
            "price": 150.25,
            "quantity": 100.0,
            "currency": "USD",
            "side": "BUY",
        }

        trade = Trade(**trade_data)
        assert trade.timestamp == timestamp.isoformat()
        assert trade.ingestion_timestamp == timestamp.isoformat()

    def test_quote_model_validation(self):
        """Quote model should validate bid/ask structure."""
        quote_data = {
            "message_id": "msg-789",
            "quote_id": "quote-012",
            "symbol": "ETHUSDT",
            "exchange": "BINANCE",
            "asset_class": "CRYPTO",
            "timestamp": "2024-01-15T10:30:00.123456",
            "bid_price": 3000.00,
            "bid_quantity": 1.5,
            "ask_price": 3005.00,
            "ask_quantity": 2.0,
            "currency": "USDT",
            "source_sequence": 54321,
            "ingestion_timestamp": "2024-01-15T10:30:00.123456",
        }

        quote = Quote(**quote_data)
        assert quote.symbol == "ETHUSDT"
        assert quote.bid_price == 3000.00
        assert quote.ask_price == 3005.00
        assert quote.bid_quantity == 1.5
        assert quote.ask_quantity == 2.0

    def test_trades_response_structure(self):
        """TradesResponse should wrap trade data properly."""
        trades = [
            Trade(
                message_id="msg-1",
                trade_id="trade-1",
                symbol="AAPL",
                exchange="NASDAQ",
                asset_class="EQUITY",
                timestamp="2024-01-15T10:30:00.123456",
                price=150.25,
                quantity=100.0,
                currency="USD",
                side="BUY",
                ingestion_timestamp="2024-01-15T10:30:00.123456",
            ),
            Trade(
                message_id="msg-2",
                trade_id="trade-2",
                symbol="GOOGL",
                exchange="NASDAQ",
                asset_class="EQUITY",
                timestamp="2024-01-15T10:31:00.123456",
                price=2500.50,
                quantity=50.0,
                currency="USD",
                side="SELL",
                ingestion_timestamp="2024-01-15T10:31:00.123456",
            ),
        ]

        pagination = PaginationMeta(limit=100, offset=0, total=2, has_more=False)
        response = TradesResponse(data=trades, pagination=pagination)

        assert len(response.data) == 2
        assert response.data[0].symbol == "AAPL"
        assert response.data[1].symbol == "GOOGL"
        assert response.pagination.total == 2


class TestQueryRequestValidation:
    """Test complex query request models."""

    def test_trade_query_request_minimal(self):
        """TradeQueryRequest should accept minimal valid request."""
        request = TradeQueryRequest()

        assert request.symbols == []
        assert request.exchanges == []
        assert request.start_time is None
        assert request.end_time is None
        assert request.limit == 1000
        assert request.offset == 0
        assert request.format == OutputFormat.JSON

    def test_trade_query_request_complex(self):
        """TradeQueryRequest should validate complex request."""
        start_time = datetime(2024, 1, 1, 9, 30)
        end_time = datetime(2024, 1, 31, 16, 0)

        request = TradeQueryRequest(
            symbols=["AAPL", "GOOGL", "MSFT"],
            exchanges=["NASDAQ", "NYSE"],
            start_time=start_time,
            end_time=end_time,
            fields=["symbol", "timestamp", "price", "quantity"],
            limit=5000,
            offset=100,
            min_price=100.0,
            max_price=1000.0,
            min_quantity=10.0,
            format=OutputFormat.CSV,
        )

        assert len(request.symbols) == 3
        assert "AAPL" in request.symbols
        assert request.start_time == start_time
        assert request.limit == 5000
        assert request.min_price == 100.0

    def test_trade_query_field_validation_invalid_fields(self):
        """TradeQueryRequest should reject invalid field names."""
        with pytest.raises(ValidationError) as exc_info:
            TradeQueryRequest(fields=["symbol", "invalid_field", "price"])

        assert "Invalid fields" in str(exc_info.value)
        assert "invalid_field" in str(exc_info.value)

    def test_trade_query_field_validation_valid_fields(self):
        """TradeQueryRequest should accept all valid field names."""
        valid_fields = list(VALID_TRADE_FIELDS)
        request = TradeQueryRequest(fields=valid_fields)

        # Should not raise ValidationError
        assert request.fields == valid_fields

    def test_trade_query_limit_validation(self):
        """TradeQueryRequest should validate limit constraints."""
        # Valid limits
        TradeQueryRequest(limit=1)  # minimum
        TradeQueryRequest(limit=100000)  # maximum

        # Invalid limits
        with pytest.raises(ValidationError):
            TradeQueryRequest(limit=0)  # too small

        with pytest.raises(ValidationError):
            TradeQueryRequest(limit=100001)  # too large

    def test_quote_query_request_structure(self):
        """QuoteQueryRequest should have similar structure to trade query."""
        request = QuoteQueryRequest(
            symbols=["BTCUSDT", "ETHUSDT"],
            start_time=datetime(2024, 1, 15),
            end_time=datetime(2024, 1, 16),
            limit=2000,
            format=OutputFormat.PARQUET,
        )

        assert len(request.symbols) == 2
        assert "BTCUSDT" in request.symbols
        assert request.format == OutputFormat.PARQUET

    def test_quote_query_field_validation(self):
        """QuoteQueryRequest should validate against quote field allowlist."""
        valid_fields = list(VALID_QUOTE_FIELDS)
        request = QuoteQueryRequest(fields=valid_fields)

        assert request.fields == valid_fields

    def test_aggregation_request_validation(self):
        """AggregationRequest should validate all parameters."""
        request = AggregationRequest(
            symbols=["AAPL", "GOOGL"],
            metrics=[AggregationMetric.VWAP, AggregationMetric.OHLCV],
            interval=AggregationInterval.FIVE_MIN,
            start_time=datetime(2024, 1, 15, 9, 30),
            end_time=datetime(2024, 1, 15, 16, 0),
        )

        assert len(request.symbols) == 2
        assert AggregationMetric.VWAP in request.metrics
        assert request.interval == AggregationInterval.FIVE_MIN

    def test_aggregation_request_symbols_required(self):
        """AggregationRequest should require at least one symbol."""
        with pytest.raises(ValidationError):
            AggregationRequest(
                symbols=[],  # Empty should fail
                metrics=[AggregationMetric.VWAP],
                interval=AggregationInterval.ONE_MIN,
                start_time=datetime(2024, 1, 15),
                end_time=datetime(2024, 1, 16),
            )

    def test_replay_request_validation(self):
        """ReplayRequest should validate replay parameters."""
        request = ReplayRequest(
            data_type=DataType.TRADES,
            symbol="AAPL",
            start_time=datetime(2024, 1, 1),
            end_time=datetime(2024, 1, 31),
            batch_size=1000,
            format=OutputFormat.JSON,
        )

        assert request.data_type == DataType.TRADES
        assert request.symbol == "AAPL"
        assert request.batch_size == 1000

    def test_replay_request_batch_size_validation(self):
        """ReplayRequest should validate batch_size constraints."""
        # Valid batch sizes
        ReplayRequest(
            start_time=datetime(2024, 1, 1),
            end_time=datetime(2024, 1, 2),
            batch_size=100,  # minimum
        )

        ReplayRequest(
            start_time=datetime(2024, 1, 1),
            end_time=datetime(2024, 1, 2),
            batch_size=10000,  # maximum
        )

        # Invalid batch sizes
        with pytest.raises(ValidationError):
            ReplayRequest(
                start_time=datetime(2024, 1, 1),
                end_time=datetime(2024, 1, 2),
                batch_size=50,  # too small
            )

        with pytest.raises(ValidationError):
            ReplayRequest(
                start_time=datetime(2024, 1, 1),
                end_time=datetime(2024, 1, 2),
                batch_size=10001,  # too large
            )


class TestEnumAndConstantValidation:
    """Test enums and constant definitions."""

    def test_data_type_enum(self):
        """DataType enum should have correct values."""
        assert DataType.TRADES == "trades"
        assert DataType.QUOTES == "quotes"

        # Test that it's a string enum
        assert isinstance(DataType.TRADES, str)
        assert isinstance(DataType.QUOTES, str)

    def test_output_format_enum(self):
        """OutputFormat enum should have correct values."""
        assert OutputFormat.JSON == "json"
        assert OutputFormat.CSV == "csv"
        assert OutputFormat.PARQUET == "parquet"

    def test_aggregation_metric_enum(self):
        """AggregationMetric enum should have comprehensive values."""
        expected_metrics = {"vwap", "twap", "ohlcv", "volume_profile", "trade_count"}
        actual_metrics = {metric.value for metric in AggregationMetric}
        assert actual_metrics == expected_metrics

    def test_aggregation_interval_enum(self):
        """AggregationInterval enum should have common intervals."""
        expected_intervals = {"1min", "5min", "15min", "30min", "1hour", "1day"}
        actual_intervals = {interval.value for interval in AggregationInterval}
        assert actual_intervals == expected_intervals

    def test_health_status_enum(self):
        """HealthStatus enum should have standard health values."""
        expected_statuses = {"healthy", "degraded", "unhealthy"}
        actual_statuses = {status.value for status in HealthStatus}
        assert actual_statuses == expected_statuses

    def test_valid_trade_fields_completeness(self):
        """VALID_TRADE_FIELDS should include all expected fields."""
        expected_fields = {
            "message_id",
            "trade_id",
            "symbol",
            "exchange",
            "asset_class",
            "timestamp",
            "price",
            "quantity",
            "currency",
            "side",
            "trade_conditions",
            "source_sequence",
            "ingestion_timestamp",
            "platform_sequence",
            "vendor_data",
        }
        assert VALID_TRADE_FIELDS == expected_fields

    def test_valid_quote_fields_completeness(self):
        """VALID_QUOTE_FIELDS should include all expected fields."""
        expected_fields = {
            "message_id",
            "quote_id",
            "symbol",
            "exchange",
            "asset_class",
            "timestamp",
            "bid_price",
            "bid_quantity",
            "ask_price",
            "ask_quantity",
            "currency",
            "source_sequence",
            "ingestion_timestamp",
            "platform_sequence",
            "vendor_data",
        }
        assert VALID_QUOTE_FIELDS == expected_fields


class TestHealthAndStatsModels:
    """Test health check and statistics models."""

    def test_health_response_structure(self):
        """HealthResponse should have proper structure."""
        response = HealthResponse(
            status=HealthStatus.HEALTHY,
            version="1.0.0",
            timestamp=datetime(2024, 1, 15, 10, 30),
        )

        assert response.status == HealthStatus.HEALTHY
        assert response.version == "1.0.0"
        assert isinstance(response.timestamp, datetime)
        assert response.dependencies == []

    def test_health_response_with_dependencies(self):
        """HealthResponse should include dependency health."""
        from k2.api.models import DependencyHealth

        deps = [
            DependencyHealth(
                name="duckdb",
                status=HealthStatus.HEALTHY,
                latency_ms=5.2,
                message="Connected successfully",
            ),
            DependencyHealth(
                name="kafka", status=HealthStatus.DEGRADED, latency_ms=150.5, message="High latency"
            ),
        ]

        response = HealthResponse(
            status=HealthStatus.DEGRADED,
            version="1.0.0",
            dependencies=deps,
        )

        assert len(response.dependencies) == 2
        assert response.dependencies[0].name == "duckdb"
        assert response.dependencies[1].status == HealthStatus.DEGRADED

    def test_stats_response_structure(self):
        """StatsResponse should wrap statistics data."""
        stats_data = StatsData(
            s3_endpoint="http://localhost:9000",
            warehouse_path="s3://test-lakehouse/warehouse",
            connection_active=True,
            trades_count=1000000,
            quotes_count=2000000,
        )

        response = StatsResponse(data=stats_data)

        assert response.data.trades_count == 1000000
        assert response.data.quotes_count == 2000000
        assert response.data.connection_active is True

    def test_stats_response_with_error(self):
        """StatsResponse should handle error conditions."""
        stats_data = StatsData(
            s3_endpoint="http://localhost:9000",
            warehouse_path="s3://test-lakehouse/warehouse",
            connection_active=False,
            error="Connection timeout",
        )

        response = StatsResponse(data=stats_data)

        assert response.data.connection_active is False
        assert response.data.error == "Connection timeout"
        assert response.data.trades_count is None


class TestModelSerialization:
    """Test model serialization and JSON output."""

    def test_trade_model_serialization(self):
        """Trade model should serialize to JSON correctly."""
        trade = Trade(
            message_id="msg-123",
            trade_id="trade-456",
            symbol="AAPL",
            exchange="NASDAQ",
            asset_class="EQUITY",
            timestamp="2024-01-15T10:30:00.123456",
            price=150.25,
            quantity=100.0,
            currency="USD",
            side="BUY",
            ingestion_timestamp="2024-01-15T10:30:00.123456",
        )

        # Test JSON serialization
        json_str = trade.model_dump_json()
        assert '"symbol":"AAPL"' in json_str
        assert '"price":150.25' in json_str
        assert '"side":"BUY"' in json_str

    def test_query_request_serialization(self):
        """Query request should serialize to JSON correctly."""
        request = TradeQueryRequest(
            symbols=["AAPL", "GOOGL"],
            start_time=datetime(2024, 1, 15, 9, 30),
            limit=1000,
            format=OutputFormat.CSV,
        )

        json_data = request.model_dump()
        assert json_data["symbols"] == ["AAPL", "GOOGL"]
        assert json_data["limit"] == 1000
        assert json_data["format"] == "csv"

    def test_model_deserialization_from_dict(self):
        """Models should deserialize from dictionaries correctly."""
        trade_data = {
            "message_id": "msg-123",
            "trade_id": "trade-456",
            "symbol": "BTCUSDT",
            "exchange": "BINANCE",
            "asset_class": "CRYPTO",
            "timestamp": "2024-01-15T10:30:00.123456",
            "price": 45000.50,
            "quantity": 0.5,
            "currency": "USDT",
            "side": "BUY",
            "ingestion_timestamp": "2024-01-15T10:30:00.123456",
        }

        trade = Trade(**trade_data)
        assert trade.symbol == "BTCUSDT"
        assert trade.price == 45000.50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

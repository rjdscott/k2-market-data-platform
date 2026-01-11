"""Unit tests for DuckDB Query Engine.

These tests mock the DuckDB connection to test query logic without
requiring actual Iceberg tables or S3 storage.
"""

from datetime import date, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from k2.query.engine import MarketSummary, QueryEngine, QueryType


@pytest.fixture
def mock_duckdb():
    """Mock DuckDB connection and cursor."""
    with patch("k2.query.engine.duckdb") as mock:
        mock_conn = MagicMock()
        mock.connect.return_value = mock_conn
        yield mock, mock_conn


@pytest.fixture
def engine(mock_duckdb):
    """Create QueryEngine with mocked DuckDB."""
    _, mock_conn = mock_duckdb
    engine = QueryEngine(
        s3_endpoint="http://localhost:9000",
        s3_access_key="test_key",
        s3_secret_key="test_secret",
        warehouse_path="s3://test-warehouse/",
    )
    return engine


class TestQueryEngineInitialization:
    """Tests for QueryEngine initialization."""

    def test_init_with_defaults(self, mock_duckdb):
        """Test engine initializes with default config."""
        mock, mock_conn = mock_duckdb

        engine = QueryEngine()

        # Should have created connection
        mock.connect.assert_called_once()
        # Should have installed extensions
        assert mock_conn.execute.call_count >= 2

    def test_init_with_custom_config(self, mock_duckdb):
        """Test engine initializes with custom config."""
        engine = QueryEngine(
            s3_endpoint="http://custom:9000",
            s3_access_key="custom_key",
            s3_secret_key="custom_secret",
            warehouse_path="s3://custom-bucket/",
        )

        assert engine.s3_endpoint == "http://custom:9000"
        assert engine.s3_access_key == "custom_key"
        assert engine.warehouse_path == "s3://custom-bucket/"

    def test_init_strips_protocol_from_endpoint(self, mock_duckdb):
        """Test S3 endpoint has http:// stripped for DuckDB."""
        engine = QueryEngine(s3_endpoint="http://localhost:9000")
        assert engine._s3_endpoint_host == "localhost:9000"

        engine2 = QueryEngine(s3_endpoint="https://s3.amazonaws.com")
        assert engine2._s3_endpoint_host == "s3.amazonaws.com"


class TestTablePathGeneration:
    """Tests for table path generation."""

    def test_get_table_path_simple_name(self, engine):
        """Test path generation for simple table name."""
        path = engine._get_table_path("trades")
        assert path == "s3://test-warehouse/market_data/trades"

    def test_get_table_path_qualified_name(self, engine):
        """Test path generation for qualified table name."""
        path = engine._get_table_path("market_data.trades")
        assert path == "s3://test-warehouse/market_data/trades"

    def test_get_table_path_custom_namespace(self, engine):
        """Test path generation for custom namespace."""
        path = engine._get_table_path("custom_ns.my_table")
        assert path == "s3://test-warehouse/custom_ns/my_table"


class TestQueryTrades:
    """Tests for trade query functionality."""

    def test_query_trades_basic(self, engine, mock_duckdb):
        """Test basic trade query."""
        _, mock_conn = mock_duckdb

        # Setup mock response
        mock_df = Mock()
        mock_df.to_dict.return_value = [{"symbol": "BHP", "price": 36.50, "volume": 1000}]
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        result = engine.query_trades(limit=100)

        assert len(result) == 1
        assert result[0]["symbol"] == "BHP"

    def test_query_trades_with_symbol_filter(self, engine, mock_duckdb):
        """Test trade query with symbol filter."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        engine.query_trades(symbol="BHP")

        # Check that query contains symbol filter
        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "symbol = 'BHP'" in query

    def test_query_trades_with_time_range(self, engine, mock_duckdb):
        """Test trade query with time range."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 31)
        engine.query_trades(start_time=start, end_time=end)

        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "exchange_timestamp >=" in query
        assert "exchange_timestamp <=" in query

    def test_query_trades_with_exchange_filter(self, engine, mock_duckdb):
        """Test trade query with exchange filter."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        engine.query_trades(exchange="ASX")

        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "exchange = 'ASX'" in query

    def test_query_trades_limit(self, engine, mock_duckdb):
        """Test trade query respects limit."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        engine.query_trades(limit=50)

        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "LIMIT 50" in query


class TestQueryQuotes:
    """Tests for quote query functionality."""

    def test_query_quotes_basic(self, engine, mock_duckdb):
        """Test basic quote query."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "BHP",
                "bid_price": 36.48,
                "ask_price": 36.52,
                "bid_volume": 1000,
                "ask_volume": 500,
            },
        ]
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        result = engine.query_quotes(symbol="BHP", limit=10)

        assert len(result) == 1
        assert result[0]["symbol"] == "BHP"
        assert result[0]["bid_price"] == 36.48
        assert result[0]["ask_price"] == 36.52


class TestMarketSummary:
    """Tests for market summary (OHLCV) functionality."""

    def test_get_market_summary(self, engine, mock_duckdb):
        """Test market summary generation."""
        _, mock_conn = mock_duckdb

        # Mock OHLCV result
        mock_conn.execute.return_value.fetchone.return_value = (
            "BHP",  # symbol
            "2024-01-15",  # date
            36.00,  # open
            37.50,  # high
            35.80,  # low
            37.25,  # close
            1000000,  # volume
            5000,  # trade_count
            36.75,  # vwap
        )

        result = engine.get_market_summary("BHP", date(2024, 1, 15))

        assert result is not None
        assert result.symbol == "BHP"
        assert result.date == date(2024, 1, 15)
        assert result.open_price == 36.00
        assert result.high_price == 37.50
        assert result.low_price == 35.80
        assert result.close_price == 37.25
        assert result.volume == 1000000
        assert result.trade_count == 5000
        assert result.vwap == 36.75

    def test_get_market_summary_no_data(self, engine, mock_duckdb):
        """Test market summary returns None when no data."""
        _, mock_conn = mock_duckdb

        # Mock empty result (trade_count is None)
        mock_conn.execute.return_value.fetchone.return_value = (
            "BHP",
            "2024-01-15",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

        result = engine.get_market_summary("BHP", date(2024, 1, 15))
        assert result is None

    def test_get_market_summary_with_exchange(self, engine, mock_duckdb):
        """Test market summary with exchange filter."""
        _, mock_conn = mock_duckdb

        mock_conn.execute.return_value.fetchone.return_value = (
            "BHP",
            "2024-01-15",
            36.0,
            37.5,
            35.8,
            37.25,
            1000,
            100,
            36.75,
        )

        engine.get_market_summary("BHP", date(2024, 1, 15), exchange="ASX")

        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "exchange = 'ASX'" in query


class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_symbols(self, engine, mock_duckdb):
        """Test getting distinct symbols."""
        _, mock_conn = mock_duckdb

        mock_conn.execute.return_value.fetchall.return_value = [("BHP",), ("RIO",), ("FMG",)]

        symbols = engine.get_symbols()

        assert symbols == ["BHP", "RIO", "FMG"]

    def test_get_symbols_with_exchange(self, engine, mock_duckdb):
        """Test getting symbols filtered by exchange."""
        _, mock_conn = mock_duckdb

        mock_conn.execute.return_value.fetchall.return_value = [("BHP",)]

        engine.get_symbols(exchange="ASX")

        call_args = mock_conn.execute.call_args_list
        query = str(call_args[-1])
        assert "exchange = 'ASX'" in query

    def test_get_date_range(self, engine, mock_duckdb):
        """Test getting date range."""
        _, mock_conn = mock_duckdb

        min_ts = datetime(2024, 1, 1, 9, 0, 0)
        max_ts = datetime(2024, 12, 31, 16, 0, 0)
        mock_conn.execute.return_value.fetchone.return_value = (min_ts, max_ts)

        result_min, result_max = engine.get_date_range()

        assert result_min == min_ts
        assert result_max == max_ts

    def test_get_stats(self, engine, mock_duckdb):
        """Test getting engine stats."""
        _, mock_conn = mock_duckdb

        mock_conn.execute.return_value.fetchone.return_value = (100,)

        stats = engine.get_stats()

        assert stats["s3_endpoint"] == "http://localhost:9000"
        assert stats["warehouse_path"] == "s3://test-warehouse/"
        assert stats["connection_active"] is True


class TestContextManager:
    """Tests for context manager functionality."""

    def test_context_manager(self, mock_duckdb):
        """Test engine works as context manager."""
        _, mock_conn = mock_duckdb

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        mock_conn.execute.return_value.fetchdf.return_value = mock_df

        with QueryEngine() as engine:
            engine.query_trades(limit=1)

        # Connection should be closed
        mock_conn.close.assert_called_once()

    def test_close(self, engine, mock_duckdb):
        """Test explicit close."""
        _, mock_conn = mock_duckdb

        engine.close()

        mock_conn.close.assert_called_once()
        assert engine._conn is None


class TestQueryType:
    """Tests for QueryType enum."""

    def test_query_types(self):
        """Test query type values."""
        assert QueryType.TRADES.value == "trades"
        assert QueryType.QUOTES.value == "quotes"
        assert QueryType.SUMMARY.value == "summary"
        assert QueryType.HISTORICAL.value == "historical"


class TestMarketSummaryDataclass:
    """Tests for MarketSummary dataclass."""

    def test_market_summary_creation(self):
        """Test MarketSummary creation."""
        summary = MarketSummary(
            symbol="BHP",
            date=date(2024, 1, 15),
            open_price=36.00,
            high_price=37.50,
            low_price=35.80,
            close_price=37.25,
            volume=1000000,
            trade_count=5000,
            vwap=36.75,
        )

        assert summary.symbol == "BHP"
        assert summary.date == date(2024, 1, 15)
        assert summary.vwap == 36.75

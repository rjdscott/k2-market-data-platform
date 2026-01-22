"""Unit tests for QueryEngine OHLCV methods.

Tests critical security and functionality aspects:
- SQL injection protection
- Input validation
- Circuit breaker behavior
- Error handling
- Symbol normalization
"""

import pytest
from datetime import UTC, datetime
from unittest.mock import Mock, MagicMock, patch
import pandas as pd

from k2.query.engine import QueryEngine


class TestOHLCVQueryValidation:
    """Test input validation and security in query_ohlcv method."""

    def test_query_ohlcv_sql_injection_protection_limit(self):
        """Test that limit parameter rejects SQL injection attempts."""
        engine = QueryEngine()

        # SQL injection attempts in limit parameter
        malicious_limits = [
            "1000; DROP TABLE gold_ohlcv_1h; --",
            "1000 OR 1=1",
            "1000' OR '1'='1",
            "1000\\x00",
            "<script>alert('xss')</script>",
        ]

        for malicious_limit in malicious_limits:
            with pytest.raises(ValueError, match="Invalid limit"):
                engine.query_ohlcv(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    limit=malicious_limit,  # type: ignore
                )

    def test_query_ohlcv_limit_validation_bounds(self):
        """Test that limit parameter enforces bounds (1-10000)."""
        engine = QueryEngine()

        # Test lower bound
        with pytest.raises(ValueError, match="Invalid limit: 0"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit=0)

        with pytest.raises(ValueError, match="Invalid limit: -1"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit=-1)

        # Test upper bound
        with pytest.raises(ValueError, match="Invalid limit: 10001"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit=10001)

        with pytest.raises(ValueError, match="Invalid limit: 100000"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit=100000)

    def test_query_ohlcv_limit_type_coercion(self):
        """Test that limit parameter coerces valid string integers."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Should successfully coerce string to int
        result = engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit="100")  # type: ignore
        assert result == []

        # Should fail on non-integer strings
        with pytest.raises(ValueError, match="Invalid limit"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h", limit="abc")  # type: ignore

    def test_query_ohlcv_invalid_timeframe(self):
        """Test validation of invalid timeframe values."""
        engine = QueryEngine()

        invalid_timeframes = ["15m", "2h", "1w", "invalid", "", None, 123]

        for timeframe in invalid_timeframes:
            with pytest.raises(ValueError, match="Invalid timeframe"):
                engine.query_ohlcv(
                    symbol="BTCUSDT",
                    timeframe=timeframe,  # type: ignore
                )

    def test_query_ohlcv_valid_timeframes(self):
        """Test that all valid timeframes are accepted."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        valid_timeframes = ["1m", "5m", "30m", "1h", "1d"]

        for timeframe in valid_timeframes:
            try:
                engine.query_ohlcv(symbol="BTCUSDT", timeframe=timeframe)
            except Exception as e:
                pytest.fail(f"Valid timeframe '{timeframe}' raised exception: {e}")

    def test_query_ohlcv_symbol_normalization(self):
        """Test that symbol is normalized to uppercase and trimmed."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_result = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "exchange": ["BINANCE"],
                "window_start": [datetime.now(UTC)],
                "window_end": [datetime.now(UTC)],
                "window_date": ["2026-01-22"],
                "open_price": [42000.0],
                "high_price": [42500.0],
                "low_price": [41800.0],
                "close_price": [42300.0],
                "volume": [100.0],
                "trade_count": [500],
                "vwap": [42150.0],
                "created_at": [datetime.now(UTC)],
                "updated_at": [datetime.now(UTC)],
            }
        )
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=mock_result)))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Test lowercase symbol
        result = engine.query_ohlcv(symbol="btcusdt", timeframe="1h")
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

        # Test symbol with whitespace
        result = engine.query_ohlcv(symbol="  btcusdt  ", timeframe="1h")
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

        # Test mixed case
        result = engine.query_ohlcv(symbol="BtCuSdT", timeframe="1h")
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    def test_circuit_breaker_opens_after_threshold(self):
        """Test that circuit breaker opens after consecutive failures."""
        engine = QueryEngine(circuit_breaker_threshold=3)

        # Mock pool to simulate failures
        mock_conn = Mock()
        mock_conn.execute = Mock(side_effect=Exception("Database connection failed"))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Cause 3 failures to open circuit
        for i in range(3):
            with pytest.raises(Exception):
                engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # 4th request should be rejected due to open circuit
        with pytest.raises(ConnectionError, match="circuit breaker is open"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

    def test_circuit_breaker_resets_on_success(self):
        """Test that circuit breaker resets after successful query."""
        engine = QueryEngine(circuit_breaker_threshold=3)

        # Mock pool to simulate 2 failures then success
        mock_conn = Mock()
        call_count = [0]

        def mock_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("Database connection failed")
            return Mock(fetchdf=Mock(return_value=pd.DataFrame()))

        mock_conn.execute = Mock(side_effect=mock_execute)
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Cause 2 failures
        for i in range(2):
            with pytest.raises(Exception):
                engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # Success should reset counter
        result = engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")
        assert engine._consecutive_failures == 0
        assert engine._circuit_open_time is None

    def test_circuit_breaker_auto_recovery(self):
        """Test that circuit breaker automatically attempts recovery after timeout."""
        engine = QueryEngine(circuit_breaker_threshold=2, circuit_breaker_timeout=1)

        # Mock pool to simulate failures
        mock_conn = Mock()
        mock_conn.execute = Mock(side_effect=Exception("Database connection failed"))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Open circuit
        for i in range(2):
            with pytest.raises(Exception):
                engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # Verify circuit is open
        assert engine._is_circuit_open() is True

        # Wait for timeout
        import time

        time.sleep(1.1)

        # Circuit should attempt recovery
        assert engine._is_circuit_open() is False


class TestErrorHandling:
    """Test error handling in query_ohlcv."""

    def test_query_ohlcv_connection_timeout(self):
        """Test graceful handling of connection timeout."""
        engine = QueryEngine()

        # Mock pool to simulate timeout
        engine.pool.acquire = Mock(side_effect=TimeoutError("Connection pool exhausted"))

        with pytest.raises(TimeoutError, match="Connection pool exhausted"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

    def test_query_ohlcv_empty_result(self):
        """Test handling of empty result set."""
        engine = QueryEngine()

        # Mock empty result
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        result = engine.query_ohlcv(symbol="INVALID_SYMBOL", timeframe="1h")
        assert result == []

    def test_query_ohlcv_database_error(self):
        """Test handling of database query errors."""
        engine = QueryEngine()

        # Mock database error
        mock_conn = Mock()
        mock_conn.execute = Mock(side_effect=Exception("Table not found: gold_ohlcv_1h"))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        with pytest.raises(Exception, match="Table not found"):
            engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # Verify failure was recorded for circuit breaker
        assert engine._consecutive_failures == 1


class TestParameterizedQueries:
    """Test that all user inputs are properly parameterized."""

    def test_query_ohlcv_parameterization(self):
        """Test that symbol, exchange, and time filters are parameterized."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        # Call with all parameters
        engine.query_ohlcv(
            symbol="BTCUSDT",
            timeframe="1h",
            exchange="BINANCE",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 31, tzinfo=UTC),
            limit=100,
        )

        # Verify execute was called with parameterized query
        call_args = mock_conn.execute.call_args
        query, params = call_args[0]

        # Query should contain placeholders (?)
        assert "symbol = ?" in query
        assert "exchange = ?" in query
        assert "window_start >= ?" in query
        assert "window_start <= ?" in query

        # Params should be a list
        assert isinstance(params, list)
        assert len(params) == 4  # symbol, exchange, start_time, end_time
        assert params[0] == "BTCUSDT"
        assert params[1] == "BINANCE"

    def test_query_ohlcv_malicious_symbol(self):
        """Test that malicious symbol inputs are safely parameterized."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        malicious_symbols = [
            "BTCUSDT'; DROP TABLE gold_ohlcv_1h; --",
            "BTCUSDT' OR '1'='1",
            "BTCUSDT\\x00",
        ]

        for symbol in malicious_symbols:
            # Should not raise exception (parameterization protects us)
            try:
                engine.query_ohlcv(symbol=symbol, timeframe="1h")
            except Exception:
                pass  # Database errors are fine, SQL injection is not

            # Verify query was parameterized
            call_args = mock_conn.execute.call_args
            query, params = call_args[0]
            assert "symbol = ?" in query
            # Malicious input should be in params (safely escaped by DuckDB)
            assert params[0] == symbol.upper().strip()


class TestMetrics:
    """Test that metrics are properly recorded."""

    @patch("k2.query.engine.metrics")
    def test_query_ohlcv_records_metrics(self, mock_metrics):
        """Test that query_ohlcv records performance metrics."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # Verify metrics were recorded
        mock_metrics.histogram.assert_called()
        mock_metrics.gauge.assert_called()

    @patch("k2.query.engine.metrics")
    def test_query_ohlcv_records_timeframe_label(self, mock_metrics):
        """Test that timeframe is included in metrics labels."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_result = pd.DataFrame({"symbol": ["BTCUSDT"]})
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=mock_result)))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        engine.query_ohlcv(symbol="BTCUSDT", timeframe="1h")

        # Verify timeframe label is included
        histogram_calls = mock_metrics.histogram.call_args_list
        found_timeframe_label = False
        for call in histogram_calls:
            if "labels" in call.kwargs:
                labels = call.kwargs["labels"]
                if "timeframe" in labels and labels["timeframe"] == "1h":
                    found_timeframe_label = True

        assert found_timeframe_label, "Timeframe label not found in metrics"


class TestTableMapping:
    """Test timeframe to table name mapping."""

    def test_query_ohlcv_table_mapping(self):
        """Test that correct table names are used for each timeframe."""
        engine = QueryEngine()

        # Mock the pool and connection
        mock_conn = Mock()
        mock_conn.execute = Mock(return_value=Mock(fetchdf=Mock(return_value=pd.DataFrame())))
        engine.pool.acquire = MagicMock(
            return_value=Mock(
                __enter__=Mock(return_value=mock_conn), __exit__=Mock(return_value=None)
            )
        )

        timeframe_mapping = {
            "1m": "gold_ohlcv_1m",
            "5m": "gold_ohlcv_5m",
            "30m": "gold_ohlcv_30m",
            "1h": "gold_ohlcv_1h",
            "1d": "gold_ohlcv_1d",
        }

        for timeframe, expected_table in timeframe_mapping.items():
            engine.query_ohlcv(symbol="BTCUSDT", timeframe=timeframe)

            # Verify correct table was used in query
            call_args = mock_conn.execute.call_args
            query = call_args[0][0]
            assert (
                expected_table in query
            ), f"Expected table {expected_table} not found in query for timeframe {timeframe}"

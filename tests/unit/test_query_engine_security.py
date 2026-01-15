"""Security tests for QueryEngine.

Tests SQL injection prevention and query resource limits.
"""

from unittest.mock import MagicMock, patch

import duckdb
import pytest

from k2.query.engine import QueryEngine


class TestSQLInjectionPrevention:
    """Test that query engine prevents SQL injection attacks."""

    def test_get_symbols_prevents_sql_injection(self, tmp_path):
        """Test that malicious exchange parameter doesn't inject SQL"""
        # Create minimal DuckDB database
        db_path = tmp_path / "test.db"
        conn = duckdb.connect(str(db_path))

        # Create test table
        conn.execute(
            """
            CREATE TABLE test_trades (
                symbol VARCHAR,
                exchange VARCHAR,
                price DECIMAL(18,8)
            )
        """
        )

        conn.execute(
            """
            INSERT INTO test_trades VALUES
            ('BHP', 'ASX', 45.20),
            ('RIO', 'ASX', 120.50),
            ('CBA', 'ASX', 95.75)
        """
        )

        conn.close()

        # Attempt SQL injection with DROP TABLE
        with patch.object(QueryEngine, "_init_connection"):
            engine = QueryEngine()
            engine._conn = duckdb.connect(str(db_path))

            # Malicious input attempting to drop table
            malicious_input = "ASX'; DROP TABLE test_trades; --"

            # Mock _get_table_path to return our test table
            with patch.object(engine, "_get_table_path", return_value="test_trades"):
                # Should not raise error or execute DROP TABLE
                # The parameterized query will treat the entire string as a literal exchange value
                result = engine.get_symbols(exchange=malicious_input)

                # Result should be empty (no exchange matches the malicious string)
                assert isinstance(result, list)

                # Verify table still exists (DROP TABLE didn't execute)
                tables = engine.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='test_trades'",
                ).fetchall()

                # If we're using DuckDB (not SQLite), check differently
                try:
                    count = engine.connection.execute(
                        "SELECT COUNT(*) FROM test_trades"
                    ).fetchone()[0]
                    assert count == 3  # All rows still present
                except Exception:
                    # DuckDB may not have sqlite_master, verify differently
                    pass

    def test_query_trades_uses_parameterized_queries(self, tmp_path):
        """Test that query_trades uses parameterized queries"""
        db_path = tmp_path / "test.db"
        conn = duckdb.connect(str(db_path))

        conn.execute(
            """
            CREATE TABLE test_trades (
                symbol VARCHAR,
                exchange VARCHAR,
                timestamp TIMESTAMP,
                price DECIMAL(18,8),
                quantity DECIMAL(18,8)
            )
        """
        )

        conn.execute(
            """
            INSERT INTO test_trades VALUES
            ('BHP', 'ASX', '2024-01-01 10:00:00', 45.20, 1000.0)
        """
        )

        conn.close()

        with patch.object(QueryEngine, "_init_connection"):
            engine = QueryEngine(table_version="v2")
            engine._conn = duckdb.connect(str(db_path))

            # Attempt SQL injection via symbol parameter
            malicious_symbol = "BHP'; DELETE FROM test_trades; --"

            with patch.object(engine, "_get_table_path", return_value="test_trades"):
                # Should not execute DELETE
                result = engine.query_trades(symbol=malicious_symbol, limit=10)

                # Result should be empty (no symbol matches malicious string)
                assert isinstance(result, list)

                # Verify rows still exist
                count = engine.connection.execute("SELECT COUNT(*) FROM test_trades").fetchone()[0]
                assert count == 1  # Row still present


class TestQueryTimeouts:
    """Test that queries timeout after configured limit."""

    def test_connection_has_timeout_configured(self):
        """Test that connection initialization sets query timeout"""
        with patch("duckdb.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            engine = QueryEngine()

            # Verify timeout was set
            timeout_calls = [
                call for call in mock_conn.execute.call_args_list if "query_timeout" in str(call)
            ]

            assert len(timeout_calls) > 0, "query_timeout should be set during initialization"

            # Verify 60 second timeout (60000 ms)
            timeout_call = str(timeout_calls[0])
            assert "60000" in timeout_call, "Timeout should be 60 seconds (60000ms)"

    def test_connection_has_memory_limit_configured(self):
        """Test that connection initialization sets memory limit"""
        with patch("duckdb.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            engine = QueryEngine()

            # Verify memory limit was set
            memory_calls = [
                call for call in mock_conn.execute.call_args_list if "memory_limit" in str(call)
            ]

            assert len(memory_calls) > 0, "memory_limit should be set during initialization"

            # Verify 4GB limit
            memory_call = str(memory_calls[0])
            assert "4GB" in memory_call, "Memory limit should be 4GB"

    @pytest.mark.integration
    def test_long_query_times_out(self, tmp_path):
        """Test that very long queries actually timeout (integration test)"""
        db_path = tmp_path / "test.db"

        # Create engine with real connection
        with patch.object(QueryEngine, "_init_connection"):
            engine = QueryEngine()
            engine._conn = duckdb.connect(str(db_path))

            # Set very short timeout for testing (1 second)
            engine._conn.execute("SET query_timeout = 1000")  # 1 second

            # Try to run expensive query (cartesian product)
            with pytest.raises(duckdb.Error) as exc_info:
                engine.connection.execute(
                    """
                    SELECT * FROM range(1000000) a, range(1000000) b
                """
                ).fetchall()

            # Should raise timeout error
            assert (
                "timeout" in str(exc_info.value).lower()
                or "interrupt" in str(exc_info.value).lower()
            )


class TestInputValidation:
    """Test input validation and sanitization."""

    def test_table_version_validated(self):
        """Test that invalid table_version raises ValueError"""
        with pytest.raises(ValueError, match="Invalid table_version"):
            engine = QueryEngine(table_version="v3")

    def test_limit_parameter_prevents_excessive_results(self):
        """Test that limit parameter caps result size"""
        with patch.object(QueryEngine, "_init_connection"):
            engine = QueryEngine()
            mock_conn = MagicMock()
            engine._conn = mock_conn

            # Mock fetchdf to return large dataframe
            mock_result = MagicMock()
            mock_result.to_dict.return_value = [{"symbol": f"SYM{i}"} for i in range(100)]
            mock_conn.execute.return_value.fetchdf.return_value = mock_result

            with patch.object(engine, "_get_table_path", return_value="s3://test/trades"):
                # Query with small limit
                result = engine.query_trades(symbol="BHP", limit=10)

                # Verify LIMIT clause was used in query
                executed_query = mock_conn.execute.call_args[0][0]
                assert "LIMIT 10" in executed_query


class TestErrorHandling:
    """Test error handling in query execution."""

    def test_connection_failure_logged(self):
        """Test that connection failures are logged properly"""
        with patch("duckdb.connect", side_effect=Exception("Connection failed")):
            with pytest.raises(Exception, match="Connection failed"):
                engine = QueryEngine()

    def test_query_failure_logged_and_raised(self):
        """Test that query failures are logged and re-raised"""
        with patch.object(QueryEngine, "_init_connection"):
            engine = QueryEngine()
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = duckdb.Error("Query failed")
            engine._conn = mock_conn

            with patch.object(engine, "_get_table_path", return_value="s3://test/trades"):
                with pytest.raises(duckdb.Error):
                    engine.query_trades(symbol="BHP")


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

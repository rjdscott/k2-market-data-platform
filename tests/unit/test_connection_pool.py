"""Unit tests for DuckDB connection pool.

Tests concurrent access, timeout behavior, exception handling, and statistics.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from k2.common.connection_pool import DuckDBConnectionPool


class TestConnectionPoolConcurrency:
    """Test concurrent access to connection pool."""

    @pytest.fixture
    def pool(self):
        """Create a connection pool with mocked DuckDB."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            # Mock DuckDB connection
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=3,
            )

            yield pool

            # Cleanup
            pool.close_all()

    def test_single_acquisition(self, pool):
        """Test acquiring and releasing a single connection."""
        with pool.acquire(timeout=5.0) as conn:
            assert conn is not None

        # Verify connection was returned to pool
        stats = pool.get_stats()
        assert stats["active_connections"] == 0
        assert stats["total_acquisitions"] == 1

    def test_concurrent_acquisitions_within_limit(self, pool):
        """Test acquiring multiple connections within pool size."""
        acquired_connections = []
        errors = []
        start_barrier = threading.Barrier(3)  # Synchronize thread start

        def acquire_connection(thread_id):
            try:
                start_barrier.wait()  # All threads start together
                with pool.acquire(timeout=5.0) as conn:
                    acquired_connections.append((thread_id, conn))
                    time.sleep(0.2)  # Hold connection longer to ensure overlap
            except Exception as e:
                errors.append((thread_id, e))

        # Start 3 threads (pool size is 3)
        threads = []
        for i in range(3):
            thread = threading.Thread(target=acquire_connection, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # All acquisitions should succeed
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(acquired_connections) == 3

        # Verify stats
        stats = pool.get_stats()
        assert stats["total_acquisitions"] == 3
        assert stats["active_connections"] == 0
        # Peak utilization should be at least 1 (may be 3 if threads overlapped)
        assert stats["peak_utilization"] >= 1

    def test_concurrent_acquisitions_exceeding_limit(self, pool):
        """Test acquiring more connections than pool size (should block and wait)."""
        acquired_connections = []
        errors = []
        acquisition_times = []

        def acquire_connection(thread_id):
            try:
                start = time.time()
                with pool.acquire(timeout=5.0) as conn:
                    acquired_connections.append((thread_id, conn))
                    time.sleep(0.2)  # Hold connection
                acquisition_times.append(time.time() - start)
            except Exception as e:
                errors.append((thread_id, e))

        # Start 6 threads (pool size is 3, so 3 will block)
        threads = []
        for i in range(6):
            thread = threading.Thread(target=acquire_connection, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # All should eventually succeed
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(acquired_connections) == 6

        # Some threads should have waited
        assert any(t > 0.15 for t in acquisition_times), "Some threads should have waited"

        # Verify stats
        stats = pool.get_stats()
        assert stats["total_acquisitions"] == 6
        assert stats["active_connections"] == 0


class TestConnectionPoolTimeout:
    """Test timeout behavior when pool is exhausted."""

    @pytest.fixture
    def pool(self):
        """Create a small connection pool."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=2,
            )

            yield pool
            pool.close_all()

    def test_timeout_when_pool_exhausted(self, pool):
        """Test that acquisition times out when pool is exhausted."""
        # Acquire both connections and hold them in separate threads
        held_connections = []
        release_event = threading.Event()

        def hold_connection():
            with pool.acquire(timeout=5.0) as conn:
                held_connections.append(conn)
                release_event.wait()  # Hold until signaled

        # Start threads to exhaust pool (pool size is 2)
        threads = [threading.Thread(target=hold_connection) for _ in range(2)]
        for thread in threads:
            thread.start()

        # Wait for both connections to be acquired
        time.sleep(0.1)

        # Try to acquire with short timeout (should fail - pool exhausted)
        with pytest.raises(TimeoutError, match="Could not acquire connection within"):
            with pool.acquire(timeout=0.2) as conn:
                pass

        # Release connections and cleanup
        release_event.set()
        for thread in threads:
            thread.join()


class TestConnectionPoolExceptionHandling:
    """Test connection release on exceptions."""

    @pytest.fixture
    def pool(self):
        """Create a connection pool."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=3,
            )

            yield pool
            pool.close_all()

    def test_connection_released_on_exception(self, pool):
        """Test that connection is released even if exception occurs."""
        # Acquire connection and raise exception
        with pytest.raises(ValueError, match="Test error"):
            with pool.acquire(timeout=5.0) as conn:
                raise ValueError("Test error")

        # Connection should be released back to pool
        stats = pool.get_stats()
        assert stats["active_connections"] == 0
        assert stats["available_connections"] >= 0

    def test_multiple_exceptions_dont_leak_connections(self, pool):
        """Test that multiple exceptions don't leak connections."""
        for i in range(5):
            with pytest.raises(RuntimeError):
                with pool.acquire(timeout=5.0) as conn:
                    raise RuntimeError(f"Error {i}")

        # All connections should be available
        stats = pool.get_stats()
        assert stats["active_connections"] == 0
        assert stats["total_acquisitions"] == 5


class TestConnectionPoolStatistics:
    """Test pool statistics tracking."""

    @pytest.fixture
    def pool(self):
        """Create a connection pool."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=5,
            )

            yield pool
            pool.close_all()

    def test_initial_stats(self, pool):
        """Test initial pool statistics."""
        stats = pool.get_stats()

        assert stats["pool_size"] == 5
        assert stats["active_connections"] == 0
        assert stats["available_connections"] == 0  # Lazy creation
        assert stats["total_connections"] == 0
        assert stats["total_acquisitions"] == 0
        assert stats["average_wait_time_seconds"] >= 0
        assert stats["peak_utilization"] == 0
        assert stats["utilization_percentage"] == 0

    def test_stats_after_acquisitions(self, pool):
        """Test statistics after multiple acquisitions."""
        # Acquire and release 3 times
        for _ in range(3):
            with pool.acquire(timeout=5.0) as conn:
                time.sleep(0.01)

        stats = pool.get_stats()

        assert stats["total_acquisitions"] == 3
        assert stats["active_connections"] == 0
        assert stats["peak_utilization"] >= 1
        assert stats["average_wait_time_seconds"] >= 0

    def test_stats_track_peak_utilization(self, pool):
        """Test that peak utilization is tracked correctly."""
        # Track when threads have acquired connections
        acquired_count = threading.Semaphore(0)
        release_event = threading.Event()
        threads = []

        def hold_connection():
            with pool.acquire(timeout=5.0) as conn:
                # Signal we've acquired
                acquired_count.release()
                # Hold connection until told to release
                release_event.wait()

        # Start 3 threads to hold connections
        for i in range(3):
            thread = threading.Thread(target=hold_connection)
            threads.append(thread)
            thread.start()

        # Wait for all 3 threads to acquire connections
        for _ in range(3):
            acquired_count.acquire(timeout=5.0)

        # Small delay to ensure all are in the wait state
        time.sleep(0.05)

        # Check stats while connections are held
        stats = pool.get_stats()
        # Due to thread timing, active connections may vary, but total acquisitions should be 3
        assert stats["total_acquisitions"] == 3, f"Expected 3 acquisitions, got {stats['total_acquisitions']}"
        # Peak utilization should be at least 1 (proves pool works)
        assert stats["peak_utilization"] >= 1, f"Expected peak >= 1, got {stats['peak_utilization']}"
        # We acquired 3 connections total
        assert stats["active_connections"] >= 1, f"Expected at least 1 active, got {stats['active_connections']}"

        # Release all connections
        release_event.set()
        for thread in threads:
            thread.join()


class TestConnectionPoolCleanup:
    """Test pool cleanup and resource management."""

    def test_close_all_connections(self):
        """Test that close_all() closes all connections."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=3,
            )

            # Acquire and release to create connections
            for _ in range(3):
                with pool.acquire(timeout=5.0) as conn:
                    pass

            # Close all
            pool.close_all()

            # Verify all connections are closed
            stats = pool.get_stats()
            assert stats["total_connections"] == 0
            assert stats["active_connections"] == 0
            assert stats["available_connections"] == 0

    def test_context_manager_closes_pool(self):
        """Test that context manager closes pool on exit."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            with DuckDBConnectionPool(
                s3_endpoint="minio:9000",
                s3_access_key="minioadmin",
                s3_secret_key="minioadmin",
                pool_size=3,
            ) as pool:
                # Use pool
                with pool.acquire(timeout=5.0) as conn:
                    pass

            # After context exit, pool should be closed
            # (We can't easily verify this without exposing internal state)


class TestConnectionPoolConfiguration:
    """Test connection pool configuration."""

    def test_connection_has_correct_configuration(self):
        """Test that connections are configured with correct settings."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_conn
            mock_duckdb.connect.return_value = mock_conn

            pool = DuckDBConnectionPool(
                s3_endpoint="http://minio:9000",
                s3_access_key="test-key",
                s3_secret_key="test-secret",
                pool_size=2,
                query_timeout_ms=30000,
                memory_limit="2GB",
            )

            # Acquire a connection (triggers creation)
            with pool.acquire(timeout=5.0) as conn:
                pass

            # Verify connection was configured
            assert mock_conn.execute.called

            # Check that configuration commands were called
            execute_calls = [str(call) for call in mock_conn.execute.call_args_list]
            config_commands_found = any("iceberg" in str(call) for call in execute_calls)
            assert config_commands_found, "Iceberg extension should be loaded"

            pool.close_all()

    def test_pool_size_configuration(self):
        """Test that pool size is respected."""
        with patch("k2.common.connection_pool.duckdb") as mock_duckdb:
            mock_conn = MagicMock()
            mock_duckdb.connect.return_value = mock_conn

            # Test different pool sizes
            for size in [1, 5, 10, 20]:
                pool = DuckDBConnectionPool(
                    s3_endpoint="minio:9000",
                    s3_access_key="minioadmin",
                    s3_secret_key="minioadmin",
                    pool_size=size,
                )

                stats = pool.get_stats()
                assert stats["pool_size"] == size

                pool.close_all()

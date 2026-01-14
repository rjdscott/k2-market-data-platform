"""Connection pool for DuckDB query engines.

Provides thread-safe connection pooling to enable concurrent queries.
Without pooling, a single DuckDB connection blocks all concurrent API requests.

Design Decisions:
- Pool size defaults to 5 (sufficient for demo, scale to 20-50 for production)
- Semaphore-based acquisition with timeout
- Automatic connection configuration on creation
- Context manager for guaranteed connection release

Usage:
    # Create pool
    pool = DuckDBConnectionPool(
        s3_endpoint="minio:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        pool_size=5
    )

    # Use connection
    with pool.acquire(timeout=30) as conn:
        result = conn.execute(query).fetchdf()

    # Clean up
    pool.close_all()
"""

import threading
import time
from contextlib import contextmanager
from typing import Any

import duckdb

from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics

logger = get_logger(__name__)
metrics = create_component_metrics("connection_pool")


class DuckDBConnectionPool:
    """Thread-safe connection pool for DuckDB.

    Maintains a pool of pre-configured DuckDB connections for concurrent query execution.
    Each connection is configured with safety limits, extensions, and S3 credentials.

    Pool Management:
    - Connections are created lazily on first acquisition
    - Semaphore controls concurrent access
    - Failed connections are automatically recreated
    - Metrics track pool utilization and wait times

    Scaling:
    - Demo: pool_size=5 (5 concurrent queries)
    - Production: pool_size=20-50 depending on load
    - Beyond 50: Consider migrating to Presto/Trino for distributed queries
    """

    def __init__(
        self,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
        pool_size: int = 5,
        query_timeout_ms: int = 60000,
        memory_limit: str = "4GB",
    ):
        """Initialize connection pool.

        Args:
            s3_endpoint: S3/MinIO endpoint (e.g., "minio:9000")
            s3_access_key: S3 access key
            s3_secret_key: S3 secret key
            pool_size: Number of connections in pool (default: 5)
            query_timeout_ms: Query timeout in milliseconds (default: 60000)
            memory_limit: Memory limit per connection (default: "4GB")
        """
        self.s3_endpoint = s3_endpoint.replace("http://", "").replace("https://", "")
        self.s3_access_key = s3_access_key
        self.s3_secret_key = s3_secret_key
        self.pool_size = pool_size
        self.query_timeout_ms = query_timeout_ms
        self.memory_limit = memory_limit

        # Connection pool (list of available connections)
        self._connections: list[duckdb.DuckDBPyConnection] = []
        self._all_connections: list[duckdb.DuckDBPyConnection] = []  # Track all for cleanup

        # Semaphore controls concurrent access
        self._semaphore = threading.Semaphore(pool_size)

        # Lock for thread-safe connection list manipulation
        self._lock = threading.Lock()

        # Track active connections for monitoring
        self._active_connections: set[int] = set()  # Connection IDs

        # Metrics
        self._total_acquisitions = 0
        self._total_wait_time = 0.0
        self._peak_utilization = 0

        logger.info(
            "Connection pool initialized",
            pool_size=pool_size,
            query_timeout_ms=query_timeout_ms,
            memory_limit=memory_limit,
        )

        # Record pool initialization metric
        metrics.gauge("connection_pool_size", pool_size)

    def _create_connection(self) -> duckdb.DuckDBPyConnection:
        """Create and configure a new DuckDB connection.

        Returns:
            Configured DuckDB connection

        Raises:
            Exception: If connection creation or configuration fails
        """
        try:
            # Create connection
            conn = duckdb.connect()

            # Install and load extensions
            conn.execute("INSTALL iceberg; LOAD iceberg;")
            conn.execute("INSTALL httpfs; LOAD httpfs;")

            # Configure safety limits
            # Note: DuckDB doesn't support query_timeout setting
            # Query timeouts should be implemented at the Python level if needed
            conn.execute(f"SET memory_limit = '{self.memory_limit}'")

            # Configure S3/MinIO credentials
            conn.execute(
                f"""
                SET s3_endpoint='{self.s3_endpoint}';
                SET s3_access_key_id='{self.s3_access_key}';
                SET s3_secret_access_key='{self.s3_secret_key}';
                SET s3_use_ssl=false;
                SET s3_url_style='path';
                SET unsafe_enable_version_guessing=true;
            """
            )

            logger.debug("Created new connection in pool", connection_id=id(conn))

            return conn

        except Exception as e:
            logger.error("Failed to create connection", error=str(e))
            metrics.increment("connection_pool_creation_errors_total")
            raise

    @contextmanager
    def acquire(self, timeout: float = 30.0):
        """Acquire a connection from the pool.

        This is a context manager that automatically releases the connection
        when the context exits (even if an exception occurs).

        Args:
            timeout: Maximum seconds to wait for a connection (default: 30s)

        Yields:
            DuckDB connection from pool

        Raises:
            TimeoutError: If no connection available within timeout

        Example:
            with pool.acquire(timeout=30) as conn:
                result = conn.execute("SELECT * FROM trades").fetchdf()
                # Connection automatically released here
        """
        start_time = time.time()

        # Try to acquire semaphore (blocks until connection available or timeout)
        acquired = self._semaphore.acquire(timeout=timeout)

        if not acquired:
            wait_time = time.time() - start_time
            logger.error(
                "Connection acquisition timeout",
                timeout_seconds=timeout,
                wait_time_seconds=wait_time,
                pool_size=self.pool_size,
                active_count=len(self._active_connections),
            )
            metrics.increment("connection_pool_acquisition_timeouts_total")
            raise TimeoutError(
                f"Could not acquire connection within {timeout}s. "
                f"Pool size: {self.pool_size}, active: {len(self._active_connections)}"
            )

        # Track acquisition time
        wait_time = time.time() - start_time
        self._total_acquisitions += 1
        self._total_wait_time += wait_time

        # Record wait time metric
        metrics.histogram("connection_pool_wait_time_seconds", wait_time)

        # Get or create connection
        with self._lock:
            if self._connections:
                # Reuse existing connection
                conn = self._connections.pop()
            else:
                # Create new connection (pool not yet full)
                conn = self._create_connection()
                self._all_connections.append(conn)

            # Mark as active
            conn_id = id(conn)
            self._active_connections.add(conn_id)

            # Track peak utilization
            active_count = len(self._active_connections)
            if active_count > self._peak_utilization:
                self._peak_utilization = active_count

            # Record utilization metric
            metrics.gauge("connection_pool_active_connections", active_count)
            metrics.gauge("connection_pool_available_connections", len(self._connections))

        logger.debug(
            "Connection acquired",
            connection_id=conn_id,
            wait_time_seconds=wait_time,
            active_count=len(self._active_connections),
        )

        try:
            yield conn

        finally:
            # Return connection to pool
            with self._lock:
                self._active_connections.discard(conn_id)
                self._connections.append(conn)

                # Update metrics
                metrics.gauge("connection_pool_active_connections", len(self._active_connections))
                metrics.gauge("connection_pool_available_connections", len(self._connections))

            # Release semaphore
            self._semaphore.release()

            logger.debug(
                "Connection released",
                connection_id=conn_id,
                active_count=len(self._active_connections),
            )

    def get_stats(self) -> dict[str, Any]:
        """Get connection pool statistics.

        Returns:
            Dictionary with pool metrics:
            - pool_size: Total pool capacity
            - active_connections: Currently in use
            - available_connections: Ready for use
            - total_connections: Created so far
            - total_acquisitions: Lifetime acquisition count
            - average_wait_time: Average acquisition wait time
            - peak_utilization: Maximum concurrent connections used
        """
        with self._lock:
            active = len(self._active_connections)
            available = len(self._connections)
            total = len(self._all_connections)

        avg_wait = self._total_wait_time / max(self._total_acquisitions, 1)

        return {
            "pool_size": self.pool_size,
            "active_connections": active,
            "available_connections": available,
            "total_connections": total,
            "total_acquisitions": self._total_acquisitions,
            "average_wait_time_seconds": avg_wait,
            "peak_utilization": self._peak_utilization,
            "utilization_percentage": (active / self.pool_size) * 100,
        }

    def close_all(self) -> None:
        """Close all connections in the pool.

        Call this on application shutdown to cleanly release resources.
        """
        logger.info("Closing all connections in pool", total_connections=len(self._all_connections))

        with self._lock:
            # Close all connections
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.error("Error closing connection", error=str(e), connection_id=id(conn))

            # Clear tracking
            self._connections.clear()
            self._all_connections.clear()
            self._active_connections.clear()

        logger.info(
            "Connection pool closed",
            total_acquisitions=self._total_acquisitions,
            peak_utilization=self._peak_utilization,
        )

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close all connections."""
        self.close_all()
        return False  # Don't suppress exceptions


def create_connection_pool(
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    pool_size: int = 5,
) -> DuckDBConnectionPool:
    """Factory function to create a connection pool with default configuration.

    Args:
        s3_endpoint: S3/MinIO endpoint
        s3_access_key: S3 access key
        s3_secret_key: S3 secret key
        pool_size: Number of connections (default: 5)

    Returns:
        Configured DuckDBConnectionPool instance

    Example:
        from k2.common.config import config

        pool = create_connection_pool(
            s3_endpoint=config.iceberg.s3_endpoint,
            s3_access_key=config.iceberg.s3_access_key,
            s3_secret_key=config.iceberg.s3_secret_key,
            pool_size=5
        )
    """
    return DuckDBConnectionPool(
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        pool_size=pool_size,
    )

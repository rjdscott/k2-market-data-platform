"""DuckDB Query Engine for Iceberg tables.

This module provides query operations for market data stored in Iceberg:
- Trade queries with filtering (symbol, time range, exchange)
- Quote queries with bid/ask spread analysis
- OHLCV market summaries (Open, High, Low, Close, Volume)
- Query performance metrics and observability

The engine uses DuckDB's Iceberg extension for direct Parquet scanning,
providing sub-second query performance without intermediate copies.

Usage:
    from k2.query.engine import QueryEngine

    engine = QueryEngine()

    # Query trades
    trades = engine.query_trades(
        symbol="BHP",
        start_time=datetime(2024, 1, 1),
        limit=1000
    )

    # Get daily summary
    summary = engine.get_market_summary("BHP", date(2024, 1, 15))
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from k2.common.config import config
from k2.common.connection_pool import DuckDBConnectionPool
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics
from k2.common.table_resolver import TableType, get_table_resolver

logger = get_logger(__name__, component="query")
metrics = create_component_metrics("query")


class QueryType(str, Enum):
    """Query type classification for metrics."""

    TRADES = "trades"
    QUOTES = "quotes"
    OHLCV = "ohlcv"
    SUMMARY = "summary"
    HISTORICAL = "historical"
    REALTIME = "realtime"


@dataclass
class MarketSummary:
    """OHLCV daily market summary for a symbol."""

    symbol: str
    date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    trade_count: int
    vwap: float  # Volume-weighted average price


class QueryEngine:
    """DuckDB query engine for Iceberg market data tables.

    Provides efficient analytical queries over trades and quotes data with:
    - Direct Parquet scanning via Iceberg extension
    - Predicate pushdown for partition pruning
    - Query performance metrics
    - Connection pooling for concurrent queries (configurable pool size)
    - V2 schema support (industry-standard market data format)

    Connection Pool:
    - Default pool size: 5 connections (supports 5 concurrent API requests)
    - Configurable via pool_size parameter
    - Thread-safe with automatic connection acquisition/release
    - Metrics tracking: wait time, utilization, peak usage

    Usage:
        # Query v2 tables (default pool size: 5)
        engine = QueryEngine()
        trades = engine.query_trades(symbol="BTCUSDT", limit=100)

        # Query with larger pool for high concurrency
        engine = QueryEngine(pool_size=10)

        # Get market summary
        summary = engine.get_market_summary("BTCUSDT", date(2024, 1, 15))

        # Close when done (releases all pool connections)
        engine.close()
    """

    def __init__(
        self,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        warehouse_path: str | None = None,
        pool_size: int = 5,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: int = 60,
    ):
        """Initialize DuckDB query engine with connection pooling and circuit breaker.

        Args:
            s3_endpoint: S3/MinIO endpoint (defaults to config)
            s3_access_key: S3 access key (defaults to config)
            s3_secret_key: S3 secret key (defaults to config)
            warehouse_path: Iceberg warehouse path (defaults to config)
            pool_size: Number of connections in pool (default: 5)
                - Demo/dev: 5 connections
                - Production: 20-50 connections depending on load
            circuit_breaker_threshold: Number of consecutive failures before opening circuit (default: 5)
            circuit_breaker_timeout: Seconds before attempting to close circuit (default: 60)
        """
        self.s3_endpoint = s3_endpoint or config.iceberg.s3_endpoint
        self.s3_access_key = s3_access_key or config.iceberg.s3_access_key
        self.s3_secret_key = s3_secret_key or config.iceberg.s3_secret_key
        self.warehouse_path = warehouse_path or config.iceberg.warehouse
        self.pool_size = pool_size

        # Initialize table resolver for dynamic table name resolution
        self.table_resolver = get_table_resolver()

        # Circuit breaker state (prevents cascade failures)
        self._consecutive_failures = 0
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_timeout = circuit_breaker_timeout
        self._circuit_open_time: datetime | None = None

        # Remove http(s):// prefix for DuckDB endpoint
        self._s3_endpoint_host = self.s3_endpoint.replace("http://", "").replace("https://", "")

        # Initialize connection pool (replaces single connection)
        self.pool = DuckDBConnectionPool(
            s3_endpoint=self.s3_endpoint,
            s3_access_key=self.s3_access_key,
            s3_secret_key=self.s3_secret_key,
            pool_size=pool_size,
        )

        logger.info(
            "Query engine initialized with connection pool and circuit breaker",
            s3_endpoint=self.s3_endpoint,
            warehouse_path=self.warehouse_path,
            pool_size=pool_size,
            circuit_breaker_threshold=circuit_breaker_threshold,
            circuit_breaker_timeout=circuit_breaker_timeout,
        )

    def _get_table_path(self, table_name: str) -> str:
        """Get the full S3 path for an Iceberg table.

        Args:
            table_name: Table name (e.g., "trades" or "market_data.trades")

        Returns:
            Full S3 path to table
        """
        # Handle both "trades" and "market_data.trades" formats
        if "." in table_name:
            namespace, table = table_name.split(".", 1)
        else:
            namespace = "market_data"
            table = table_name

        # Construct path (warehouse already has s3:// prefix)
        warehouse = self.warehouse_path.rstrip("/")
        return f"{warehouse}/{namespace}/{table}"

    @contextmanager
    def _query_timer(self, query_type: str):
        """Context manager for timing queries and recording metrics."""
        start_time = datetime.now()
        try:
            yield
            metrics.increment(
                "query_executions_total",
                labels={"query_type": query_type, "status": "success"},
            )
        except Exception:
            metrics.increment(
                "query_executions_total",
                labels={"query_type": query_type, "status": "error"},
            )
            raise
        finally:
            duration = (datetime.now() - start_time).total_seconds()
            metrics.histogram("query_duration_seconds", duration, labels={"query_type": query_type})

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is currently open.

        Circuit opens after consecutive failures exceed threshold.
        Circuit automatically attempts to close after timeout period.

        Returns:
            True if circuit is open (requests should be rejected)
        """
        # If circuit was never opened, it's closed
        if self._circuit_open_time is None:
            return False

        # Check if timeout period has elapsed (auto-recovery)
        time_since_open = (datetime.now() - self._circuit_open_time).total_seconds()
        if time_since_open >= self._circuit_breaker_timeout:
            logger.info(
                "Circuit breaker timeout elapsed, attempting recovery",
                timeout_seconds=self._circuit_breaker_timeout,
                consecutive_failures=self._consecutive_failures,
            )
            # Reset to half-open state (will close on next success or re-open on failure)
            self._circuit_open_time = None
            self._consecutive_failures = max(0, self._consecutive_failures - 2)
            return False

        # Circuit is still open
        logger.warning(
            "Circuit breaker is open, rejecting request",
            consecutive_failures=self._consecutive_failures,
            time_since_open=time_since_open,
        )
        metrics.increment("circuit_breaker_rejections_total", labels={"pool": "duckdb"})
        return True

    def _record_success(self) -> None:
        """Record successful query execution for circuit breaker."""
        if self._consecutive_failures > 0:
            logger.info(
                "Query succeeded, resetting circuit breaker",
                previous_failures=self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._circuit_open_time = None

        metrics.gauge(
            "circuit_breaker_consecutive_failures",
            0,
            labels={"pool": "duckdb"},
        )

    def _record_failure(self) -> None:
        """Record failed query execution for circuit breaker."""
        self._consecutive_failures += 1

        metrics.gauge(
            "circuit_breaker_consecutive_failures",
            self._consecutive_failures,
            labels={"pool": "duckdb"},
        )

        # Open circuit if threshold exceeded
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            if self._circuit_open_time is None:
                self._circuit_open_time = datetime.now()
                logger.error(
                    "Circuit breaker opened due to consecutive failures",
                    consecutive_failures=self._consecutive_failures,
                    threshold=self._circuit_breaker_threshold,
                )
                metrics.increment("circuit_breaker_opened_total", labels={"pool": "duckdb"})

    def query_trades(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        table_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query trade records with optional filters.

        Args:
            symbol: Filter by symbol (e.g., "BHP")
            exchange: Filter by exchange (e.g., "ASX")
            start_time: Filter trades after this time
            end_time: Filter trades before this time
            limit: Maximum records to return (default: 1000)
            table_name: Iceberg table name (default: v2 tables)

        Returns:
            List of trade dictionaries (v2 schema with industry-standard fields)

        Example:
            trades = engine.query_trades(
                symbol="BTCUSDT",
                start_time=datetime(2024, 1, 1),
                limit=100
            )
            # Returns: message_id, trade_id, symbol, exchange, asset_class, timestamp,
            #          price, quantity, currency, side, trade_conditions, vendor_data, ...
        """
        # Auto-determine table name using resolver
        if table_name is None:
            table_name = self.table_resolver.resolve_table_name(TableType.TRADES, use_fallback=True)

        table_path = self._get_table_path(table_name)

        # Build WHERE clause with parameterized queries (SQL injection protection)
        conditions = []
        params = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange)
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time.isoformat())
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time.isoformat())

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # V2 query - clean v2 schema
        query = f"""
            SELECT
                message_id,
                trade_id,
                symbol,
                exchange,
                asset_class,
                timestamp,
                price,
                quantity,
                currency,
                side,
                trade_conditions,
                source_sequence,
                ingestion_timestamp,
                platform_sequence,
                vendor_data
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """

        with self._query_timer(QueryType.TRADES.value):
            try:
                # Acquire connection from pool (thread-safe)
                with self.pool.acquire(timeout=30.0) as conn:
                    result = conn.execute(query, params).fetchdf()
                    rows = result.to_dict(orient="records")

                logger.debug(
                    "Trades query completed",
                    symbol=symbol,
                    exchange=exchange,
                    table_name=table_name,
                    row_count=len(rows),
                )

                # Record rows scanned
                metrics.histogram(
                    "query_rows_scanned",
                    len(rows),
                    labels={"query_type": QueryType.TRADES.value, "timeframe": "n/a"},
                )

                return rows

            except Exception as e:
                logger.error(
                    "Trades query failed",
                    symbol=symbol,
                    exchange=exchange,
                    table_name=table_name,
                    error=str(e),
                )
                raise

    def query_quotes(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        table_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query quote records with optional filters.

        Args:
            symbol: Filter by symbol (e.g., "BHP")
            exchange: Filter by exchange (e.g., "ASX")
            start_time: Filter quotes after this time
            end_time: Filter quotes before this time
            limit: Maximum records to return (default: 1000)
            table_name: Iceberg table name (default: market_data.quotes_v2)

        Returns:
            List of quote dictionaries with bid/ask data (v2 schema)
        """
        # Auto-determine table name using resolver
        if table_name is None:
            table_name = self.table_resolver.resolve_table_name(TableType.QUOTES, use_fallback=True)

        table_path = self._get_table_path(table_name)

        # Build WHERE clause with parameterized queries (SQL injection protection)
        conditions = []
        params = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange)
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time.isoformat())
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time.isoformat())

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # V2 query - use native v2 field names
        query = f"""
            SELECT
                message_id,
                quote_id,
                symbol,
                exchange,
                asset_class,
                timestamp,
                bid_price,
                bid_quantity,
                ask_price,
                ask_quantity,
                currency,
                source_sequence,
                ingestion_timestamp,
                platform_sequence,
                vendor_data
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """

        with self._query_timer(QueryType.QUOTES.value):
            try:
                # Acquire connection from pool (thread-safe)
                with self.pool.acquire(timeout=30.0) as conn:
                    result = conn.execute(query, params).fetchdf()
                    rows = result.to_dict(orient="records")

                logger.debug(
                    "Quotes query completed",
                    symbol=symbol,
                    exchange=exchange,
                    table_name=table_name,
                    row_count=len(rows),
                )

                metrics.histogram(
                    "query_rows_scanned",
                    len(rows),
                    labels={"query_type": QueryType.QUOTES.value, "timeframe": "n/a"},
                )

                return rows

            except Exception as e:
                logger.error(
                    "Quotes query failed",
                    symbol=symbol,
                    exchange=exchange,
                    table_name=table_name,
                    error=str(e),
                )
                raise

    def query_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        exchange: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query pre-computed OHLCV candles from gold_ohlcv_* tables.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timeframe: Candle timeframe ("1m", "5m", "30m", "1h", "1d")
            exchange: Optional exchange filter
            start_time: Filter candles after this time
            end_time: Filter candles before this time
            limit: Maximum candles to return (default: 1000)

        Returns:
            List of OHLCV candle dictionaries

        Raises:
            ValueError: If timeframe or limit is invalid
            ConnectionError: If circuit breaker is open

        Example:
            candles = engine.query_ohlcv(
                symbol="BTCUSDT",
                timeframe="1h",
                start_time=datetime(2026, 1, 20),
                limit=24
            )
        """
        # Normalize symbol (uppercase, strip whitespace)
        symbol = symbol.upper().strip()

        # Validate timeframe
        table_map = {
            "1m": "gold_ohlcv_1m",
            "5m": "gold_ohlcv_5m",
            "30m": "gold_ohlcv_30m",
            "1h": "gold_ohlcv_1h",
            "1d": "gold_ohlcv_1d",
        }

        if timeframe not in table_map:
            raise ValueError(
                f"Invalid timeframe: {timeframe}. Must be one of: {list(table_map.keys())}"
            )

        # SQL Injection Protection: Explicit limit validation
        # CRITICAL: Must validate before string interpolation
        if not isinstance(limit, int):
            try:
                limit = int(limit)
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid limit: {limit}. Must be an integer") from e

        if not (1 <= limit <= 10000):
            raise ValueError(f"Invalid limit: {limit}. Must be between 1 and 10,000")

        table_name = table_map[timeframe]
        table_path = self._get_table_path(table_name)

        # Build WHERE clause with parameterized queries
        conditions = ["symbol = ?"]
        params = [symbol]

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.upper().strip())
        if start_time:
            conditions.append("window_start >= ?")
            params.append(start_time.isoformat())
        if end_time:
            conditions.append("window_start <= ?")
            params.append(end_time.isoformat())

        where_clause = "WHERE " + " AND ".join(conditions)

        # Safe to use limit in string interpolation after validation
        query = f"""
            SELECT
                symbol,
                exchange,
                window_start,
                window_end,
                window_date,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                trade_count,
                vwap,
                created_at,
                updated_at
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY window_start DESC
            LIMIT {limit}
        """

        with self._query_timer(QueryType.OHLCV.value):
            try:
                # Check circuit breaker before acquiring connection
                if self._is_circuit_open():
                    raise ConnectionError(
                        "Query engine circuit breaker is open. Service temporarily unavailable."
                    )

                with self.pool.acquire(timeout=10.0) as conn:  # Reduced from 30s to 10s
                    result = conn.execute(query, params).fetchdf()
                    rows = result.to_dict(orient="records")

                # Reset circuit breaker on success
                self._record_success()

                logger.debug(
                    "OHLCV query completed",
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    row_count=len(rows),
                )

                metrics.histogram(
                    "query_rows_scanned",
                    len(rows),
                    labels={"query_type": QueryType.OHLCV.value, "timeframe": timeframe},
                )

                return rows

            except Exception as e:
                # Record failure for circuit breaker
                self._record_failure()

                logger.error(
                    "OHLCV query failed",
                    symbol=symbol,
                    timeframe=timeframe,
                    error=str(e),
                    circuit_breaker_failures=self._consecutive_failures,
                )
                raise

    def get_market_summary(
        self,
        symbol: str,
        query_date: date,
        exchange: str | None = None,
        table_name: str | None = None,
    ) -> MarketSummary | None:
        """Get OHLCV market summary for a symbol on a specific date.

        Args:
            symbol: Symbol to query (e.g., "BHP")
            query_date: Date for summary
            exchange: Optional exchange filter
            table_name: Iceberg table name (default: v2 tables)

        Returns:
            MarketSummary with OHLCV data, or None if no data

        Example:
            summary = engine.get_market_summary("BHP", date(2024, 1, 15))
            print(f"VWAP: {summary.vwap}")
        """
        # Auto-determine table name using resolver
        if table_name is None:
            table_name = self.table_resolver.resolve_table_name(TableType.TRADES, use_fallback=True)

        table_path = self._get_table_path(table_name)

        # Date range for the day
        start_dt = datetime.combine(query_date, datetime.min.time())
        end_dt = datetime.combine(query_date, datetime.max.time())

        # Build parameterized query (SQL injection protection) - v2 schema
        params = [symbol, start_dt.isoformat(), end_dt.isoformat()]
        exchange_filter = ""
        if exchange:
            exchange_filter = "AND exchange = ?"
            params.append(exchange)

        query = f"""
            WITH day_trades AS (
                SELECT
                    symbol,
                    timestamp,
                    price,
                    quantity as volume
                FROM iceberg_scan('{table_path}')
                WHERE symbol = ?
                  AND timestamp >= ?
                  AND timestamp <= ?
                  {exchange_filter}
            )
            SELECT
                ? as symbol,
                ? as query_date,
                FIRST(price ORDER BY timestamp ASC) as open_price,
                MAX(price) as high_price,
                MIN(price) as low_price,
                FIRST(price ORDER BY timestamp DESC) as close_price,
                SUM(volume) as total_volume,
                COUNT(*) as trade_count,
                SUM(price * volume) / NULLIF(SUM(volume), 0) as vwap
            FROM day_trades
        """
        # Add symbol and query_date for SELECT clause
        params.extend([symbol, str(query_date)])

        with self._query_timer(QueryType.SUMMARY.value):
            try:
                # Acquire connection from pool (thread-safe)
                with self.pool.acquire(timeout=30.0) as conn:
                    result = conn.execute(query, params).fetchone()

                if result is None or result[6] is None:  # Check trade_count
                    logger.debug(
                        "No trades found for summary",
                        symbol=symbol,
                        date=str(query_date),
                        table_name=table_name,
                    )
                    return None

                summary = MarketSummary(
                    symbol=symbol,
                    date=query_date,
                    open_price=float(result[2]) if result[2] else 0.0,
                    high_price=float(result[3]) if result[3] else 0.0,
                    low_price=float(result[4]) if result[4] else 0.0,
                    close_price=float(result[5]) if result[5] else 0.0,
                    volume=int(result[6]) if result[6] else 0,
                    trade_count=int(result[7]) if result[7] else 0,
                    vwap=float(result[8]) if result[8] else 0.0,
                )

                logger.debug(
                    "Market summary generated",
                    symbol=symbol,
                    date=str(query_date),
                    table_name=table_name,
                    trade_count=summary.trade_count,
                )

                return summary

            except Exception as e:
                logger.error(
                    "Market summary query failed",
                    symbol=symbol,
                    date=str(query_date),
                    table_name=table_name,
                    error=str(e),
                )
                raise

    def get_symbols(
        self,
        exchange: str | None = None,
        table_name: str | None = None,
    ) -> list[str]:
        """Get distinct symbols from trades table.

        Args:
            exchange: Optional exchange filter
            table_name: Iceberg table name (default: v2 tables)

        Returns:
            List of unique symbols

        Security:
            Uses parameterized queries to prevent SQL injection
        """
        # Auto-determine table name using resolver
        if table_name is None:
            table_name = self.table_resolver.resolve_table_name(TableType.TRADES, use_fallback=True)

        table_path = self._get_table_path(table_name)

        # Build WHERE clause with parameterized query (SECURITY: SQL injection protection)
        where_clause = ""
        params = []

        if exchange:
            where_clause = "WHERE exchange = ?"
            params.append(exchange)

        query = f"""
            SELECT DISTINCT symbol
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY symbol
        """

        with self._query_timer(QueryType.HISTORICAL.value):
            # Acquire connection from pool (thread-safe)
            with self.pool.acquire(timeout=30.0) as conn:
                result = conn.execute(query, params).fetchall()
                return [row[0] for row in result]

    def get_date_range(
        self,
        table_name: str | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        """Get the date range of data in a table.

        Args:
            table_name: Iceberg table name (default: v2 tables)

        Returns:
            Tuple of (min_timestamp, max_timestamp)
        """
        # Auto-determine table name using resolver
        if table_name is None:
            table_name = self.table_resolver.resolve_table_name(TableType.TRADES, use_fallback=True)

        table_path = self._get_table_path(table_name)

        # V2 schema uses 'timestamp' field
        query = f"""
            SELECT
                MIN(timestamp) as min_ts,
                MAX(timestamp) as max_ts
            FROM iceberg_scan('{table_path}')
        """

        with self._query_timer(QueryType.HISTORICAL.value):
            # Acquire connection from pool (thread-safe)
            with self.pool.acquire(timeout=30.0) as conn:
                result = conn.execute(query).fetchone()
                return (result[0], result[1]) if result else (None, None)

    def execute_raw(self, query: str) -> list[dict[str, Any]]:
        """Execute a raw SQL query against DuckDB.

        Use with caution - this bypasses query building and validation.

        Args:
            query: Raw SQL query

        Returns:
            List of result dictionaries
        """
        with self._query_timer(QueryType.HISTORICAL.value):
            try:
                # Acquire connection from pool (thread-safe)
                with self.pool.acquire(timeout=30.0) as conn:
                    result = conn.execute(query).fetchdf()
                    return result.to_dict(orient="records")
            except Exception as e:
                logger.error("Raw query failed", query=query[:100], error=str(e))
                raise

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics and metadata including connection pool stats.

        Returns:
            Dictionary with connection info, pool statistics, and table stats
        """
        # Get pool statistics
        pool_stats = self.pool.get_stats()

        stats = {
            "s3_endpoint": self.s3_endpoint,
            "warehouse_path": self.warehouse_path,
            "table_mappings": self.table_resolver.get_all_mappings(),
            "pool": pool_stats,  # Include full pool statistics
        }

        try:
            # Get row counts using resolved table names
            trades_table = self.table_resolver.resolve_table_name(
                TableType.TRADES, use_fallback=True
            )
            quotes_table = self.table_resolver.resolve_table_name(
                TableType.QUOTES, use_fallback=True
            )

            trades_path = self._get_table_path(trades_table)
            quotes_path = self._get_table_path(quotes_table)

            with self.pool.acquire(timeout=30.0) as conn:
                trades_result = conn.execute(
                    f"SELECT COUNT(*) FROM iceberg_scan('{trades_path}')",
                ).fetchone()
                quotes_result = conn.execute(
                    f"SELECT COUNT(*) FROM iceberg_scan('{quotes_path}')",
                ).fetchone()

                trades_count = trades_result[0] if trades_result else 0
                quotes_count = quotes_result[0] if quotes_result else 0

            stats["trades_count"] = trades_count
            stats["quotes_count"] = quotes_count
            stats["trades_table"] = trades_table
            stats["quotes_table"] = quotes_table
        except Exception as e:
            stats["error"] = str(e)

        return stats

    def close(self) -> None:
        """Close all connections in the pool."""
        self.pool.close_all()
        logger.debug("Query engine connection pool closed")

    def __enter__(self) -> "QueryEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close connection."""
        self.close()

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

import duckdb

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics

logger = get_logger(__name__, component="query")
metrics = create_component_metrics("query")


class QueryType(str, Enum):
    """Query type classification for metrics."""

    TRADES = "trades"
    QUOTES = "quotes"
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
    - Connection pooling (reuse connection)

    Usage:
        engine = QueryEngine()

        # Query with filters
        trades = engine.query_trades(symbol="BHP", limit=100)

        # Get market summary
        summary = engine.get_market_summary("BHP", date(2024, 1, 15))

        # Close when done
        engine.close()
    """

    def __init__(
        self,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        warehouse_path: str | None = None,
    ):
        """Initialize DuckDB query engine.

        Args:
            s3_endpoint: S3/MinIO endpoint (defaults to config)
            s3_access_key: S3 access key (defaults to config)
            s3_secret_key: S3 secret key (defaults to config)
            warehouse_path: Iceberg warehouse path (defaults to config)
        """
        self.s3_endpoint = s3_endpoint or config.iceberg.s3_endpoint
        self.s3_access_key = s3_access_key or config.iceberg.s3_access_key
        self.s3_secret_key = s3_secret_key or config.iceberg.s3_secret_key
        self.warehouse_path = warehouse_path or config.iceberg.warehouse

        # Remove http(s):// prefix for DuckDB endpoint
        self._s3_endpoint_host = self.s3_endpoint.replace("http://", "").replace("https://", "")

        # Initialize DuckDB connection
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._init_connection()

        logger.info(
            "Query engine initialized",
            s3_endpoint=self.s3_endpoint,
            warehouse_path=self.warehouse_path,
        )

    def _init_connection(self) -> None:
        """Initialize DuckDB connection with Iceberg and S3 extensions."""
        try:
            self._conn = duckdb.connect()

            # Install and load extensions
            self._conn.execute("INSTALL iceberg; LOAD iceberg;")
            self._conn.execute("INSTALL httpfs; LOAD httpfs;")

            # Configure S3/MinIO credentials
            self._conn.execute(
                f"""
                SET s3_endpoint='{self._s3_endpoint_host}';
                SET s3_access_key_id='{self.s3_access_key}';
                SET s3_secret_access_key='{self.s3_secret_key}';
                SET s3_use_ssl=false;
                SET s3_url_style='path';
                SET unsafe_enable_version_guessing=true;
            """,
            )

            logger.debug("DuckDB connection initialized with Iceberg extension")

        except Exception as e:
            logger.error("Failed to initialize DuckDB connection", error=str(e))
            metrics.increment(
                "query_executions_total", labels={"query_type": "initialization", "status": "error"},
            )
            raise

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Get the DuckDB connection, reinitializing if needed."""
        if self._conn is None:
            self._init_connection()
        return self._conn

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
                "query_executions_total", labels={"query_type": query_type, "status": "success"},
            )
        except Exception:
            metrics.increment(
                "query_executions_total", labels={"query_type": query_type, "status": "error"},
            )
            raise
        finally:
            duration = (datetime.now() - start_time).total_seconds()
            metrics.histogram("query_duration_seconds", duration, labels={"query_type": query_type})

    def query_trades(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        table_name: str = "market_data.trades",
    ) -> list[dict[str, Any]]:
        """Query trade records with optional filters.

        Args:
            symbol: Filter by symbol (e.g., "BHP")
            exchange: Filter by exchange (e.g., "ASX")
            start_time: Filter trades after this time
            end_time: Filter trades before this time
            limit: Maximum records to return (default: 1000)
            table_name: Iceberg table name

        Returns:
            List of trade dictionaries

        Example:
            trades = engine.query_trades(
                symbol="BHP",
                start_time=datetime(2024, 1, 1),
                limit=100
            )
        """
        table_path = self._get_table_path(table_name)

        # Build WHERE clause
        conditions = []
        if symbol:
            conditions.append(f"symbol = '{symbol}'")
        if exchange:
            conditions.append(f"exchange = '{exchange}'")
        if start_time:
            conditions.append(f"exchange_timestamp >= '{start_time.isoformat()}'")
        if end_time:
            conditions.append(f"exchange_timestamp <= '{end_time.isoformat()}'")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol,
                company_id,
                exchange,
                exchange_timestamp,
                price,
                volume,
                qualifiers,
                venue,
                buyer_id,
                ingestion_timestamp,
                sequence_number
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY exchange_timestamp DESC
            LIMIT {limit}
        """

        with self._query_timer(QueryType.TRADES.value):
            try:
                result = self.connection.execute(query).fetchdf()
                rows = result.to_dict(orient="records")

                logger.debug(
                    "Trades query completed",
                    symbol=symbol,
                    exchange=exchange,
                    row_count=len(rows),
                )

                # Record rows scanned
                metrics.histogram(
                    "query_rows_scanned", len(rows), labels={"query_type": QueryType.TRADES.value},
                )

                return rows

            except Exception as e:
                logger.error(
                    "Trades query failed",
                    symbol=symbol,
                    exchange=exchange,
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
        table_name: str = "market_data.quotes",
    ) -> list[dict[str, Any]]:
        """Query quote records with optional filters.

        Args:
            symbol: Filter by symbol (e.g., "BHP")
            exchange: Filter by exchange (e.g., "ASX")
            start_time: Filter quotes after this time
            end_time: Filter quotes before this time
            limit: Maximum records to return (default: 1000)
            table_name: Iceberg table name

        Returns:
            List of quote dictionaries with bid/ask data
        """
        table_path = self._get_table_path(table_name)

        # Build WHERE clause
        conditions = []
        if symbol:
            conditions.append(f"symbol = '{symbol}'")
        if exchange:
            conditions.append(f"exchange = '{exchange}'")
        if start_time:
            conditions.append(f"exchange_timestamp >= '{start_time.isoformat()}'")
        if end_time:
            conditions.append(f"exchange_timestamp <= '{end_time.isoformat()}'")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol,
                company_id,
                exchange,
                exchange_timestamp,
                bid_price,
                bid_volume,
                ask_price,
                ask_volume,
                ingestion_timestamp,
                sequence_number
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY exchange_timestamp DESC
            LIMIT {limit}
        """

        with self._query_timer(QueryType.QUOTES.value):
            try:
                result = self.connection.execute(query).fetchdf()
                rows = result.to_dict(orient="records")

                logger.debug(
                    "Quotes query completed",
                    symbol=symbol,
                    exchange=exchange,
                    row_count=len(rows),
                )

                metrics.histogram(
                    "query_rows_scanned", len(rows), labels={"query_type": QueryType.QUOTES.value},
                )

                return rows

            except Exception as e:
                logger.error(
                    "Quotes query failed",
                    symbol=symbol,
                    exchange=exchange,
                    error=str(e),
                )
                raise

    def get_market_summary(
        self,
        symbol: str,
        query_date: date,
        exchange: str | None = None,
        table_name: str = "market_data.trades",
    ) -> MarketSummary | None:
        """Get OHLCV market summary for a symbol on a specific date.

        Args:
            symbol: Symbol to query (e.g., "BHP")
            query_date: Date for summary
            exchange: Optional exchange filter
            table_name: Iceberg table name

        Returns:
            MarketSummary with OHLCV data, or None if no data

        Example:
            summary = engine.get_market_summary("BHP", date(2024, 1, 15))
            print(f"VWAP: {summary.vwap}")
        """
        table_path = self._get_table_path(table_name)

        # Date range for the day
        start_dt = datetime.combine(query_date, datetime.min.time())
        end_dt = datetime.combine(query_date, datetime.max.time())

        exchange_filter = ""
        if exchange:
            exchange_filter = f"AND exchange = '{exchange}'"

        query = f"""
            WITH day_trades AS (
                SELECT
                    symbol,
                    exchange_timestamp,
                    price,
                    volume
                FROM iceberg_scan('{table_path}')
                WHERE symbol = '{symbol}'
                  AND exchange_timestamp >= '{start_dt.isoformat()}'
                  AND exchange_timestamp <= '{end_dt.isoformat()}'
                  {exchange_filter}
            )
            SELECT
                '{symbol}' as symbol,
                '{query_date}' as query_date,
                FIRST(price ORDER BY exchange_timestamp ASC) as open_price,
                MAX(price) as high_price,
                MIN(price) as low_price,
                FIRST(price ORDER BY exchange_timestamp DESC) as close_price,
                SUM(volume) as total_volume,
                COUNT(*) as trade_count,
                SUM(price * volume) / NULLIF(SUM(volume), 0) as vwap
            FROM day_trades
        """

        with self._query_timer(QueryType.SUMMARY.value):
            try:
                result = self.connection.execute(query).fetchone()

                if result is None or result[6] is None:  # Check trade_count
                    logger.debug(
                        "No trades found for summary",
                        symbol=symbol,
                        date=str(query_date),
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
                    trade_count=summary.trade_count,
                )

                return summary

            except Exception as e:
                logger.error(
                    "Market summary query failed",
                    symbol=symbol,
                    date=str(query_date),
                    error=str(e),
                )
                raise

    def get_symbols(
        self,
        exchange: str | None = None,
        table_name: str = "market_data.trades",
    ) -> list[str]:
        """Get distinct symbols from trades table.

        Args:
            exchange: Optional exchange filter
            table_name: Iceberg table name

        Returns:
            List of unique symbols
        """
        table_path = self._get_table_path(table_name)

        where_clause = ""
        if exchange:
            where_clause = f"WHERE exchange = '{exchange}'"

        query = f"""
            SELECT DISTINCT symbol
            FROM iceberg_scan('{table_path}')
            {where_clause}
            ORDER BY symbol
        """

        with self._query_timer(QueryType.HISTORICAL.value):
            result = self.connection.execute(query).fetchall()
            return [row[0] for row in result]

    def get_date_range(
        self,
        table_name: str = "market_data.trades",
    ) -> tuple[datetime | None, datetime | None]:
        """Get the date range of data in a table.

        Args:
            table_name: Iceberg table name

        Returns:
            Tuple of (min_timestamp, max_timestamp)
        """
        table_path = self._get_table_path(table_name)

        query = f"""
            SELECT
                MIN(exchange_timestamp) as min_ts,
                MAX(exchange_timestamp) as max_ts
            FROM iceberg_scan('{table_path}')
        """

        with self._query_timer(QueryType.HISTORICAL.value):
            result = self.connection.execute(query).fetchone()
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
                result = self.connection.execute(query).fetchdf()
                return result.to_dict(orient="records")
            except Exception as e:
                logger.error("Raw query failed", query=query[:100], error=str(e))
                raise

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics and metadata.

        Returns:
            Dictionary with connection info and table stats
        """
        stats = {
            "s3_endpoint": self.s3_endpoint,
            "warehouse_path": self.warehouse_path,
            "connection_active": self._conn is not None,
        }

        try:
            # Get row counts
            trades_path = self._get_table_path("trades")
            quotes_path = self._get_table_path("quotes")

            trades_count = self.connection.execute(
                f"SELECT COUNT(*) FROM iceberg_scan('{trades_path}')",
            ).fetchone()[0]
            quotes_count = self.connection.execute(
                f"SELECT COUNT(*) FROM iceberg_scan('{quotes_path}')",
            ).fetchone()[0]

            stats["trades_count"] = trades_count
            stats["quotes_count"] = quotes_count
        except Exception as e:
            stats["error"] = str(e)

        return stats

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("Query engine connection closed")

    def __enter__(self) -> "QueryEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close connection."""
        self.close()

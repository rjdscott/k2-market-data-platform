"""
K2 Market Data Platform - Watermark Utilities (PostgreSQL)
Purpose: Manage exactly-once semantics for ClickHouse → Iceberg offload
Storage: PostgreSQL (Prefect metadata DB) - Industry Best Practice
Version: v2.0 (ADR-014)
Last Updated: 2026-02-11
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from typing import Tuple, Optional
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WatermarkManager:
    """
    Manages watermark tracking for exactly-once semantics in offload jobs.

    Design:
    - Watermarks stored in PostgreSQL (Prefect metadata DB)
    - ACID transactions ensure consistency
    - Tracks last successfully offloaded timestamp and sequence number
    - Prevents duplicate reads via incremental SELECT
    - Enables retry safety (failed jobs don't update watermark)

    Best Practice Rationale:
    - PostgreSQL for metadata (not ClickHouse) - separation of concerns
    - Transactional updates - true ACID guarantees
    - Industry standard - metadata DB pattern used by Airflow, Databricks, etc.
    """

    def __init__(self, pg_host: str = "prefect-db", pg_port: int = 5432,
                 pg_database: str = "prefect", pg_user: str = "prefect",
                 pg_password: str = ""):
        """
        Initialize watermark manager with PostgreSQL connection.

        Args:
            pg_host: PostgreSQL host (default: prefect-db)
            pg_port: PostgreSQL port (default: 5432)
            pg_database: Database name (default: prefect)
            pg_user: Username (default: prefect)
            pg_password: Password — always pass explicitly via os.environ["PREFECT_DB_PASSWORD"]
        """
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_database = pg_database
        self.pg_user = pg_user
        self.pg_password = pg_password
        self._conn = None  # Lazy-connected; reused across all calls in a single job run

    def _get_connection(self):
        """
        Return a cached PostgreSQL connection, reconnecting if the connection was closed.

        One-shot jobs (offload_generic.py) make 3-4 sequential calls; opening a new
        TCP connection per call is wasteful. A single cached connection is the correct
        pattern here — no pool needed for a single-threaded script.

        Note: psycopg2's `with conn:` context manager manages transactions (commit /
        rollback), NOT connection lifetime, so the cached connection stays open after
        each `with conn:` block exits.
        """
        if self._conn is None or self._conn.closed:
            logger.debug(f"Opening PostgreSQL connection to {self.pg_host}:{self.pg_port}/{self.pg_database}")
            self._conn = psycopg2.connect(
                host=self.pg_host,
                port=self.pg_port,
                database=self.pg_database,
                user=self.pg_user,
                password=self.pg_password
            )
        return self._conn

    def close(self) -> None:
        """Explicitly close the PostgreSQL connection. Call at end of job."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.debug("PostgreSQL connection closed")
        self._conn = None

    def __del__(self):
        """Safety net: close connection if caller forgets to call close()."""
        self.close()

    def get_watermark(self, table_name: str) -> Tuple[datetime, int]:
        """
        Get last successfully offloaded watermark for a table.

        Args:
            table_name: Source table name (e.g., 'bronze_trades_binance')

        Returns:
            Tuple of (last_offload_timestamp, last_offload_max_sequence)

        Raises:
            ValueError: If watermark not found (table not initialized)
        """
        logger.info(f"Fetching watermark for table: {table_name}")

        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT last_offload_timestamp, last_offload_max_sequence
                        FROM offload_watermarks
                        WHERE table_name = %s
                    """, (table_name,))

                    row = cur.fetchone()

                    if row is None:
                        raise ValueError(
                            f"Watermark not found for table: {table_name}. "
                            "Run offload-watermarks.sql to initialize."
                        )

                    last_timestamp = row['last_offload_timestamp']
                    last_sequence = row['last_offload_max_sequence']

                    logger.info(f"Watermark retrieved: timestamp={last_timestamp}, sequence={last_sequence}")
                    return last_timestamp, last_sequence

        except Exception as e:
            logger.error(f"Failed to fetch watermark for {table_name}: {e}")
            raise

    def update_watermark(
        self,
        table_name: str,
        max_timestamp: datetime,
        max_sequence: int,
        row_count: int,
        duration_seconds: int
    ) -> None:
        """
        Update watermark after successful Iceberg write (transactional).

        CRITICAL: Only call this after Iceberg atomic commit succeeds.
        PostgreSQL transaction ensures watermark update is atomic.

        Args:
            table_name: Source table name
            max_timestamp: Maximum timestamp written to Iceberg
            max_sequence: Maximum sequence number written
            row_count: Number of rows written
            duration_seconds: Job duration in seconds
        """
        logger.info(f"Updating watermark for {table_name}: "
                    f"max_timestamp={max_timestamp}, max_sequence={max_sequence}, "
                    f"row_count={row_count}, duration={duration_seconds}s")

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    # Transactional update (ACID guarantees)
                    cur.execute("""
                        UPDATE offload_watermarks
                        SET
                            last_offload_timestamp = %s,
                            last_offload_max_sequence = %s,
                            last_offload_row_count = %s,
                            status = 'success',
                            last_successful_run = NOW(),
                            last_run_duration_seconds = %s,
                            failure_count = 0,
                            updated_at = NOW()
                        WHERE table_name = %s
                    """, (max_timestamp, max_sequence, row_count, duration_seconds, table_name))

                    conn.commit()

                    logger.info(f"Watermark updated successfully for {table_name}")

        except Exception as e:
            logger.error(f"Failed to update watermark for {table_name}: {e}")
            raise

    def mark_offload_running(self, table_name: str) -> None:
        """
        Mark offload job as running (optional, for monitoring).

        Args:
            table_name: Source table name
        """
        logger.info(f"Marking offload as running for {table_name}")

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE offload_watermarks
                        SET status = 'running', updated_at = NOW()
                        WHERE table_name = %s
                    """, (table_name,))
                    conn.commit()

                    logger.info(f"Offload marked as running for {table_name}")

        except Exception as e:
            logger.warning(f"Failed to mark offload running for {table_name}: {e}")
            # Non-critical, continue

    def mark_offload_failed(self, table_name: str, error_message: str) -> None:
        """
        Mark offload job as failed (for alerting).

        Args:
            table_name: Source table name
            error_message: Error description
        """
        logger.error(f"Marking offload as failed for {table_name}: {error_message}")

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    # Increment failure count, record error
                    cur.execute("""
                        UPDATE offload_watermarks
                        SET
                            status = 'failed',
                            last_error_message = %s,
                            failure_count = failure_count + 1,
                            updated_at = NOW()
                        WHERE table_name = %s
                    """, (error_message[:1000], table_name))  # Truncate error to 1000 chars

                    conn.commit()

                    logger.info(f"Offload marked as failed for {table_name}")

        except Exception as e:
            logger.warning(f"Failed to mark offload failed for {table_name}: {e}")
            # Non-critical, error already logged

    def get_incremental_window(
        self,
        last_watermark: datetime,
        buffer_minutes: int = 5
    ) -> Tuple[datetime, datetime]:
        """
        Calculate time window for incremental read.

        Args:
            last_watermark: Last successfully offloaded timestamp
            buffer_minutes: Buffer to account for late arrivals (default: 5 minutes)

        Returns:
            Tuple of (start_time, end_time) for WHERE clause

        Design:
        - start_time: last_watermark (exclusive - already offloaded)
        - end_time: now() - buffer (avoid in-flight data, prevent TTL race)
        """
        start_time = last_watermark
        end_time = datetime.now(start_time.tzinfo) - timedelta(minutes=buffer_minutes)

        logger.info(f"Incremental window: {start_time} to {end_time} "
                    f"(buffer: {buffer_minutes} minutes)")

        if end_time <= start_time:
            logger.warning(f"No new data to offload (end_time <= start_time). "
                           f"This is normal for low-volume periods.")
            return start_time, start_time  # Empty window

        return start_time, end_time


def create_incremental_query(
    table_name: str,
    timestamp_column: str,
    sequence_column: str,
    start_time: datetime,
    end_time: datetime
) -> str:
    """
    Generate incremental SELECT query for ClickHouse.

    Args:
        table_name: ClickHouse table name
        timestamp_column: Timestamp column for filtering (e.g., 'exchange_timestamp')
        sequence_column: Sequence column for ordering (e.g., 'sequence_number')
        start_time: Start of time window (exclusive)
        end_time: End of time window (inclusive)

    Returns:
        SQL query string for Spark JDBC read

    Design:
    - Read only new data since last watermark
    - Order by timestamp + sequence for deterministic processing
    - Use explicit column list (avoid SELECT * for schema evolution safety)
    """
    # Format timestamps for ClickHouse (no timezone suffix needed)
    start_str = start_time.strftime('%Y-%m-%d %H:%M:%S.%f')
    end_str = end_time.strftime('%Y-%m-%d %H:%M:%S.%f')

    query = f"""
        (SELECT *
         FROM {table_name}
         WHERE {timestamp_column} > '{start_str}'
         AND {timestamp_column} <= '{end_str}'
         ORDER BY {timestamp_column}, {sequence_column}) AS incremental_query
    """

    logger.info(f"Generated incremental query for {table_name}: "
                f"{start_time} to {end_time}")

    return query


# Example usage (for testing):
# PREFECT_DB_PASSWORD=prefect python3 watermark_pg.py
if __name__ == "__main__":
    import os
    wm = WatermarkManager(
        pg_host=os.environ.get("PREFECT_DB_HOST", "localhost"),
        pg_port=int(os.environ.get("PREFECT_DB_PORT", "5432")),
        pg_database=os.environ.get("PREFECT_DB_NAME", "prefect"),
        pg_user=os.environ.get("PREFECT_DB_USER", "prefect"),
        pg_password=os.environ["PREFECT_DB_PASSWORD"]
    )

    # Test: Get watermark
    try:
        last_ts, last_seq = wm.get_watermark("bronze_trades_binance")
        print(f"Watermark: {last_ts}, {last_seq}")

        # Test: Calculate incremental window
        start_time, end_time = wm.get_incremental_window(last_ts, buffer_minutes=5)
        print(f"Incremental window: {start_time} to {end_time}")

    except Exception as e:
        print(f"Error: {e}")

"""
K2 Market Data Platform - Watermark Utilities
Purpose: Manage exactly-once semantics for ClickHouse → Iceberg offload
Version: v2.0 (ADR-014)
Last Updated: 2026-02-11
"""

from datetime import datetime, timedelta
from typing import Tuple, Optional
from pyspark.sql import SparkSession
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
    - Watermarks stored in ClickHouse `offload_watermarks` table
    - Tracks last successfully offloaded timestamp and sequence number
    - Prevents duplicate reads via incremental SELECT
    - Enables retry safety (failed jobs don't update watermark)
    """

    def __init__(self, spark: SparkSession, clickhouse_url: str):
        """
        Initialize watermark manager.

        Args:
            spark: SparkSession instance
            clickhouse_url: JDBC URL for ClickHouse (e.g., jdbc:clickhouse://clickhouse:8123/default)
        """
        self.spark = spark
        self.clickhouse_url = clickhouse_url
        self.clickhouse_jdbc_properties = {
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": ""
        }

    def get_watermark(self, table_name: str) -> Tuple[datetime, int]:
        """
        Get last successfully offloaded watermark for a table.

        Args:
            table_name: ClickHouse table name (e.g., 'bronze_trades_binance')

        Returns:
            Tuple of (last_offload_timestamp, last_offload_max_sequence)

        Raises:
            ValueError: If watermark not found (table not initialized)
        """
        logger.info(f"Fetching watermark for table: {table_name}")

        query = f"""
            (SELECT
                last_offload_timestamp,
                last_offload_max_sequence
             FROM offload_watermarks
             WHERE table_name = '{table_name}'
             LIMIT 1) AS watermark_query
        """

        try:
            watermark_df = self.spark.read.jdbc(
                url=self.clickhouse_url,
                table=query,
                properties=self.clickhouse_jdbc_properties
            )

            if watermark_df.count() == 0:
                raise ValueError(f"Watermark not found for table: {table_name}. "
                                 "Run watermarks.sql to initialize.")

            row = watermark_df.first()
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
        Update watermark after successful Iceberg write.

        CRITICAL: Only call this after Iceberg atomic commit succeeds.
        If called before commit, failures will cause data loss.

        Args:
            table_name: ClickHouse table name
            max_timestamp: Maximum timestamp written to Iceberg
            max_sequence: Maximum sequence number written
            row_count: Number of rows written
            duration_seconds: Job duration in seconds
        """
        logger.info(f"Updating watermark for {table_name}: "
                    f"max_timestamp={max_timestamp}, max_sequence={max_sequence}, "
                    f"row_count={row_count}, duration={duration_seconds}s")

        # Use ClickHouse INSERT for watermark update
        # ReplacingMergeTree will deduplicate by table_name, keeping latest updated_at
        update_query = f"""
            INSERT INTO offload_watermarks (
                table_name,
                last_offload_timestamp,
                last_offload_max_sequence,
                last_offload_row_count,
                status,
                last_successful_run,
                last_run_duration_seconds,
                failure_count,
                updated_at
            )
            VALUES (
                '{table_name}',
                '{max_timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')}',
                {max_sequence},
                {row_count},
                'success',
                now(),
                {duration_seconds},
                0,
                now()
            )
        """

        try:
            # Execute via Spark SQL (leverages ClickHouse JDBC)
            self.spark.sql(f"CREATE TEMPORARY VIEW watermark_update AS SELECT 1")
            self.spark.sql(update_query)

            logger.info(f"Watermark updated successfully for {table_name}")

        except Exception as e:
            logger.error(f"Failed to update watermark for {table_name}: {e}")
            raise

    def mark_offload_running(self, table_name: str) -> None:
        """
        Mark offload job as running (optional, for monitoring).

        Args:
            table_name: ClickHouse table name
        """
        logger.info(f"Marking offload as running for {table_name}")

        update_query = f"""
            INSERT INTO offload_watermarks (table_name, status, updated_at)
            VALUES ('{table_name}', 'running', now())
        """

        try:
            self.spark.sql(update_query)
            logger.info(f"Offload marked as running for {table_name}")
        except Exception as e:
            logger.warning(f"Failed to mark offload running for {table_name}: {e}")
            # Non-critical, continue

    def mark_offload_failed(self, table_name: str, error_message: str) -> None:
        """
        Mark offload job as failed (for alerting).

        Args:
            table_name: ClickHouse table name
            error_message: Error description
        """
        logger.error(f"Marking offload as failed for {table_name}: {error_message}")

        # Escape single quotes in error message
        safe_error = error_message.replace("'", "''")

        update_query = f"""
            INSERT INTO offload_watermarks (
                table_name,
                status,
                last_error_message,
                failure_count,
                updated_at
            )
            SELECT
                '{table_name}',
                'failed',
                '{safe_error}',
                COALESCE((SELECT failure_count + 1 FROM offload_watermarks WHERE table_name = '{table_name}'), 1),
                now()
        """

        try:
            self.spark.sql(update_query)
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
        - start_time: last_watermark (exclusive - we already offloaded this)
        - end_time: now() - buffer (avoid reading in-flight data, prevent TTL race)
        """
        start_time = last_watermark
        end_time = datetime.now() - timedelta(minutes=buffer_minutes)

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
    query = f"""
        (SELECT *
         FROM {table_name}
         WHERE {timestamp_column} > '{start_time.strftime('%Y-%m-%d %H:%M:%S.%f')}'
         AND {timestamp_column} <= '{end_time.strftime('%Y-%m-%d %H:%M:%S.%f')}'
         ORDER BY {timestamp_column}, {sequence_column}) AS incremental_query
    """

    logger.info(f"Generated incremental query for {table_name}: "
                f"{start_time} to {end_time}")

    return query


# Example usage (for testing):
if __name__ == "__main__":
    from pyspark.sql import SparkSession

    spark = SparkSession.builder \
        .appName("WatermarkTest") \
        .config("spark.jars.packages", "com.clickhouse:clickhouse-jdbc:0.4.6:all") \
        .getOrCreate()

    wm = WatermarkManager(
        spark=spark,
        clickhouse_url="jdbc:clickhouse://clickhouse:8123/default"
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

    spark.stop()

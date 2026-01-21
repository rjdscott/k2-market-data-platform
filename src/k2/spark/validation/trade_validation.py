"""Trade validation logic with DLQ (Dead Letter Queue) routing.

This module provides validation rules for trade records in the Silver layer.
Invalid records are routed to the DLQ table for observability and recovery.

Industry Best Practice (Medallion Architecture):
- Bronze: Raw immutable data
- Silver: Validated data (strict rules, DLQ for failures)
- Gold: Business logic and derived columns

Validation Rules:
1. Price must be positive (> 0)
2. Quantity must be positive (> 0)
3. Symbol must be non-null
4. Side must be BUY or SELL
5. Timestamp must not be in the future
6. Timestamp must not be too old (>1 year)
7. Message ID must be non-null
8. Asset class must be 'crypto'

Usage:
    from k2.spark.validation.trade_validation import validate_trade_record, write_to_dlq

    valid_df, invalid_df = validate_trade_record(deserialized_df)

    # Write valid records to Silver
    valid_df.writeStream.toTable("silver_binance_trades")

    # Write invalid records to DLQ
    write_to_dlq(invalid_df, "bronze_binance_trades")
"""

from pyspark.sql import DataFrame
from pyspark.sql.functions import (
    col,
    current_timestamp,
    lit,
    to_date,
    unix_timestamp,
    when,
)


def validate_trade_record(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Apply validation rules and split into valid/invalid streams.

    Args:
        df: DataFrame with deserialized trade records (V2 schema + raw_bytes, offset)

    Returns:
        Tuple of (valid_df, invalid_df)
        - valid_df: Records passing all validation rules
        - invalid_df: Records failing any rule (with error_reason column)

    Industry Best Practice:
    - Strict validation in Silver (fail fast)
    - DLQ captures ALL failures (no silent data loss)
    - Error reasons for debugging and recovery
    """
    # Define validation conditions
    # Note: unix_timestamp() returns seconds, exchange timestamp is in microseconds
    current_time_micros = unix_timestamp() * 1000000

    valid_conditions = (
        (col("price") > 0)
        & (col("quantity") > 0)
        & (col("symbol").isNotNull())
        & (col("side").isin(["BUY", "SELL"]))
        & (col("timestamp") < current_time_micros)  # Not in future
        & (col("timestamp") > (current_time_micros - 86400 * 365 * 1000000))  # Not >1 year old
        & (col("message_id").isNotNull())
        & (col("asset_class") == "crypto")
    )

    # Add validation flag
    df_with_validation = df.withColumn("is_valid", valid_conditions)

    # Split streams
    # IMPORTANT: Handle NULL is_valid explicitly (from_avro returning NULL causes NULL is_valid)
    # NULL is_valid records must go to DLQ (not disappear silently)
    valid_df = df_with_validation.filter(col("is_valid")).drop("is_valid")

    # Invalid: explicitly FALSE or NULL (capture silent failures from NULL fields)
    invalid_df = df_with_validation.filter((~col("is_valid")) | col("is_valid").isNull())

    # Add error reason to invalid records (first failing condition)
    # Industry best practice: Provide clear, actionable error messages
    invalid_df = invalid_df.withColumn(
        "error_reason",
        # Check for NULL is_valid first (indicates deserialization failure or NULL fields)
        when(col("is_valid").isNull(), "deserialization_failed_or_null_fields")
        .when(col("price").isNull(), "price_is_null")
        .when(col("price") <= 0, "price_must_be_positive")
        .when(col("quantity").isNull(), "quantity_is_null")
        .when(col("quantity") <= 0, "quantity_must_be_positive")
        .when(col("symbol").isNull(), "symbol_required")
        .when(~col("side").isin(["BUY", "SELL"]), "invalid_side")
        .when(col("timestamp") >= current_time_micros, "timestamp_in_future")
        .when(
            col("timestamp") <= (current_time_micros - 86400 * 365 * 1000000), "timestamp_too_old"
        )
        .when(col("message_id").isNull(), "message_id_required")
        .when(col("asset_class") != "crypto", "invalid_asset_class")
        .otherwise("unknown_validation_error"),
    )

    # Categorize error types (for alerting/dashboards)
    invalid_df = invalid_df.withColumn(
        "error_type",
        when(col("error_reason").contains("price"), "price_validation")
        .when(col("error_reason").contains("quantity"), "quantity_validation")
        .when(col("error_reason").contains("timestamp"), "timestamp_validation")
        .when(col("error_reason").contains("symbol"), "symbol_validation")
        .when(col("error_reason").contains("side"), "side_validation")
        .when(col("error_reason").contains("message_id"), "message_id_validation")
        .when(col("error_reason").contains("asset_class"), "asset_class_validation")
        .otherwise("unknown_error"),
    )

    return valid_df, invalid_df


def write_to_dlq(invalid_df: DataFrame, bronze_source: str, checkpoint_location: str):
    """Write invalid records to DLQ (Dead Letter Queue) table.

    Args:
        invalid_df: DataFrame of invalid records (with error_reason, error_type)
        bronze_source: Source Bronze table name (e.g., "bronze_binance_trades")
        checkpoint_location: Checkpoint location for DLQ writer

    Returns:
        StreamingQuery for DLQ write stream

    Industry Best Practice:
    - DLQ captures ALL validation failures (no silent data loss)
    - Includes raw_bytes for replay/debugging
    - Includes error context (reason, type, timestamp)
    - Includes Kafka offset for lineage tracking
    """
    # Prepare DLQ records
    # Capture: raw_bytes, error details, lineage metadata
    dlq_df = invalid_df.select(
        col("raw_bytes").alias("raw_record"),  # Original Bronze raw_bytes
        col("error_reason"),  # Specific validation failure
        col("error_type"),  # Error category
        current_timestamp().alias("error_timestamp"),  # When validation failed
        lit(bronze_source).alias("bronze_source"),  # Source Bronze table
        col("offset").alias("kafka_offset"),  # Bronze Kafka offset
        col("schema_id"),  # Schema Registry ID (nullable)
        to_date(current_timestamp()).alias("dlq_date"),  # Partition key
    )

    # Write to DLQ table
    # Industry best practice: Use append mode for DLQ (never overwrite failures)
    query = (
        dlq_df.writeStream.format("iceberg")
        .outputMode("append")
        .option("checkpointLocation", checkpoint_location)
        .trigger(processingTime="30 seconds")  # Same as Silver trigger
        .toTable("iceberg.market_data.silver_dlq_trades")
    )

    return query


def get_validation_metrics(spark, window_minutes: int = 5) -> DataFrame:
    """Get validation metrics for monitoring/alerting.

    Args:
        spark: SparkSession
        window_minutes: Time window for metrics (default: 5 minutes)

    Returns:
        DataFrame with validation metrics:
        - total_records: Total records validated
        - valid_records: Records passing validation
        - invalid_records: Records failing validation
        - dlq_rate: Failure rate (invalid / total)
        - error_type_counts: Breakdown by error type

    Usage:
        metrics_df = get_validation_metrics(spark, window_minutes=10)
        metrics_df.show()
    """
    # Query Silver tables for valid records
    # Query DLQ table for invalid records
    # Calculate rates and breakdowns
    # This is a placeholder for monitoring implementation
    pass

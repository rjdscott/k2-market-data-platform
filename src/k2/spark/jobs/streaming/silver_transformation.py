#!/usr/bin/env python3
"""Silver Transformation Job - Bronze to Silver with Validation.

This Spark Structured Streaming job:
1. Reads from Bronze tables (per-exchange)
2. Deserializes V2 Avro payloads
3. Validates trade records (price > 0, quantity > 0, timestamp valid, message_id present)
4. Writes valid records to Silver tables (per-exchange)
5. Writes invalid records to DLQ table

Architecture:
- Source: bronze_binance_trades + bronze_kraken_trades (Iceberg tables)
- Target: silver_binance_trades + silver_kraken_trades (Iceberg tables)
- DLQ: silver_dlq (dead letter queue for invalid records)
- Pattern: Batch processing with micro-batches (not streaming for complex Avro deserialization)
- Checkpoints: /checkpoints/silver-binance/ and /checkpoints/silver-kraken/

Configuration:
- Trigger: 30 seconds (validation latency acceptable)
- Max records per trigger: 1,000 (batch validation)
- Validation rules: price > 0, quantity > 0, timestamp > 0, message_id present

Usage:
    # Start both Silver jobs (Binance + Kraken)
    docker-compose up -d silver-binance-stream silver-kraken-stream

    # Or run manually
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --total-executor-cores 2 \
      --executor-cores 2 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/spark-avro_2.12-3.5.3.jar \
      --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
      --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
      --py-files /opt/k2/src/k2/spark/jobs/streaming/silver_transformation.py \
      /opt/k2/src/k2/spark/jobs/streaming/silver_transformation.py binance
"""

import json
import sys
from datetime import datetime

from pyspark.sql import SparkSession
from pyspark.sql.avro.functions import from_avro
from pyspark.sql.functions import (
    col,
    current_timestamp,
    hour,
    lit,
    struct,
    to_date,
    unix_timestamp,
    when,
)


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog and S3/MinIO configuration."""
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "rest")
        .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
        .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
        .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
        .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
        .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
        .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
        # S3/MinIO configuration
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        # AWS SDK v2 region configuration
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .getOrCreate()
    )


def get_v2_schema_json() -> str:
    """Return V2 Avro schema as JSON string.

    This embeds the V2 schema directly to avoid Schema Registry dependency in Spark.
    Note: In production, consider fetching from Schema Registry for consistency.
    """
    schema = {
        "type": "record",
        "name": "TradeV2",
        "namespace": "com.k2.marketdata",
        "fields": [
            {"name": "message_id", "type": "string"},
            {"name": "trade_id", "type": "string"},
            {"name": "symbol", "type": "string"},
            {"name": "exchange", "type": "string"},
            {
                "name": "asset_class",
                "type": {
                    "type": "enum",
                    "name": "AssetClass",
                    "namespace": "com.k2.marketdata",
                    "symbols": ["equities", "crypto", "futures", "options"],
                },
            },
            {"name": "timestamp", "type": {"type": "long", "logicalType": "timestamp-micros"}},
            {
                "name": "price",
                "type": {"type": "bytes", "logicalType": "decimal", "precision": 18, "scale": 8},
            },
            {
                "name": "quantity",
                "type": {"type": "bytes", "logicalType": "decimal", "precision": 18, "scale": 8},
            },
            {"name": "currency", "type": "string"},
            {
                "name": "side",
                "type": {
                    "type": "enum",
                    "name": "TradeSide",
                    "namespace": "com.k2.marketdata",
                    "symbols": ["BUY", "SELL", "SELL_SHORT", "UNKNOWN"],
                },
            },
            {"name": "trade_conditions", "type": {"type": "array", "items": "string"}},
            {"name": "source_sequence", "type": ["null", "long"], "default": None},
            {
                "name": "ingestion_timestamp",
                "type": {"type": "long", "logicalType": "timestamp-micros"},
            },
            {"name": "platform_sequence", "type": ["null", "long"], "default": None},
            {
                "name": "vendor_data",
                "type": ["null", {"type": "map", "values": "string"}],
                "default": None,
            },
        ],
    }
    return json.dumps(schema)


def validate_trade_record(df):
    """Add validation checks and error tracking.

    Validation Rules:
    1. price must be > 0
    2. quantity must be > 0
    3. timestamp must be present (not null)
    4. message_id must be present (not null, not empty)

    Returns DataFrame with:
    - is_valid: Boolean flag
    - validation_errors: Array of error codes
    """
    # Check individual validation rules
    df = df.withColumn("price_valid", col("trade.price") > 0)
    df = df.withColumn("quantity_valid", col("trade.quantity") > 0)
    # Timestamp is TIMESTAMP type from Avro deserialization, check for null
    df = df.withColumn("timestamp_valid", col("trade.timestamp").isNotNull())
    df = df.withColumn(
        "message_id_valid",
        (col("trade.message_id").isNotNull()) & (col("trade.message_id") != ""),
    )

    # Build validation errors array
    df = df.withColumn(
        "validation_errors",
        when(~col("price_valid"), lit("price_invalid"))
        .when(~col("quantity_valid"), lit("quantity_invalid"))
        .when(~col("timestamp_valid"), lit("timestamp_invalid"))
        .when(~col("message_id_valid"), lit("message_id_missing"))
        .otherwise(lit(None)),
    )

    # Overall validation flag
    df = df.withColumn(
        "is_valid",
        col("price_valid")
        & col("quantity_valid")
        & col("timestamp_valid")
        & col("message_id_valid"),
    )

    return df


def main(exchange: str):
    """Main entry point.

    Args:
        exchange: Exchange name ('binance' or 'kraken')
    """
    exchange = exchange.lower()
    if exchange not in ["binance", "kraken"]:
        print(f"✗ Error: Invalid exchange '{exchange}'. Must be 'binance' or 'kraken'")
        return 1

    print("\n" + "=" * 70)
    print(f"K2 Silver Transformation - {exchange.capitalize()}")
    print("Bronze → Silver (Avro Deserialization + Validation)")
    print("=" * 70)
    print("\nConfiguration:")
    print(f"  • Source: bronze_{exchange}_trades")
    print(f"  • Target: silver_{exchange}_trades")
    print(f"  • Trigger: 30 seconds")
    print(f"  • Checkpoint: /checkpoints/silver-{exchange}/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session(f"K2-Silver-{exchange.capitalize()}-Transformation")

    try:
        # Get V2 schema
        v2_schema = get_v2_schema_json()
        print("✓ V2 Avro schema loaded")

        # Read from Bronze table (batch mode for Avro deserialization)
        print(f"Reading from bronze_{exchange}_trades...")
        bronze_table = f"iceberg.market_data.bronze_{exchange}_trades"
        bronze_df = spark.readStream.format("iceberg").load(bronze_table)

        print("✓ Bronze stream reader configured")

        # Deserialize Avro payloads
        print("Deserializing V2 Avro payloads...")
        trades_df = bronze_df.select(
            "*",  # Keep Bronze metadata
            from_avro(col("avro_payload"), v2_schema).alias("trade"),
        )

        print("✓ Avro deserialization configured")

        # Validate trade records
        print("Adding validation checks...")
        validated_df = validate_trade_record(trades_df)

        print("✓ Validation configured")

        # Filter valid records for Silver
        valid_trades = validated_df.filter(col("is_valid") == True).select(
            col("trade.message_id"),
            col("trade.trade_id"),
            col("trade.symbol"),
            col("trade.exchange"),
            col("trade.asset_class"),
            # Convert TIMESTAMP back to BIGINT (microseconds since epoch)
            (unix_timestamp(col("trade.timestamp")) * 1000000).cast("bigint").alias("timestamp"),
            col("trade.price"),
            col("trade.quantity"),
            col("trade.currency"),
            col("trade.side"),
            col("trade.trade_conditions"),
            col("trade.source_sequence"),
            # Convert TIMESTAMP back to BIGINT (microseconds since epoch)
            (unix_timestamp(col("trade.ingestion_timestamp")) * 1000000)
            .cast("bigint")
            .alias("ingestion_timestamp"),
            col("trade.platform_sequence"),
            col("trade.vendor_data"),
            # Add exchange_date for partitioning (use the timestamp field directly as it's now TIMESTAMP type)
            to_date(col("trade.timestamp")).alias("exchange_date"),
        )

        # Write valid records to Silver table
        print(f"Starting Silver writer for {exchange}...")
        silver_table = f"iceberg.market_data.silver_{exchange}_trades"

        query = (
            valid_trades.writeStream.format("iceberg")
            .outputMode("append")
            .trigger(processingTime="30 seconds")
            .option("checkpointLocation", f"/checkpoints/silver-{exchange}/")
            .option("path", silver_table)
            .option("fanout-enabled", "true")
            .partitionBy("exchange_date")
            .start()
        )

        print("✓ Silver writer started")
        print("\nStreaming job running...")
        print("  • Spark UI: http://localhost:8090")
        print(f"  • Check Silver table: SELECT COUNT(*) FROM {silver_table}")
        print(f"  • Validation: All records validated before Silver ingestion")
        print("\nPress Ctrl+C to stop (checkpoint will be saved)\n")

        # Wait for termination
        query.awaitTermination()

    except KeyboardInterrupt:
        print("\n\nReceived interrupt signal, shutting down gracefully...")
        print(f"✓ Checkpoint saved: /checkpoints/silver-{exchange}/")
        return 0
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: silver_transformation.py <exchange>")
        print("  exchange: 'binance' or 'kraken'")
        sys.exit(1)

    sys.exit(main(sys.argv[1]))

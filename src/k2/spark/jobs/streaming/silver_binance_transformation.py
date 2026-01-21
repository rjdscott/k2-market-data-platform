#!/usr/bin/env python3
"""Silver Binance Transformation Job - Bronze → Silver with DLQ.

This Spark Structured Streaming job:
1. Reads raw bytes from bronze_binance_trades
2. Deserializes Avro using Schema Registry header (5-byte header + payload)
3. Validates trade records (price > 0, timestamp valid, etc.)
4. Routes valid records to silver_binance_trades
5. Routes invalid records to silver_dlq_trades (Dead Letter Queue)

Medallion Architecture - Silver Layer:
- Bronze: Raw immutable data (replayability)
- Silver: Validated, deserialized data (quality checks)
- Gold: Business logic, derived columns

Industry Best Practices Implemented:
✓ DLQ Pattern: Invalid records tracked for observability
✓ Schema Registry Integration: Proper Avro deserialization
✓ Validation Metadata: Tracks validation_timestamp, bronze_ingestion_timestamp, schema_id
✓ Error Handling: Graceful failure with detailed error reasons
✓ Monitoring: DLQ rate metrics for alerting

Configuration:
- Source: bronze_binance_trades (Iceberg table, raw bytes)
- Targets: silver_binance_trades (valid), silver_dlq_trades (invalid)
- Checkpoint: /checkpoints/silver-binance/
- Trigger: 30 seconds (balance latency vs throughput)
- DLQ Trigger: 30 seconds (same as Silver)

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-avro_2.12:3.5.3 \
      --executor-cores 2 \
      --num-executors 2 \
      /opt/k2/src/k2/spark/jobs/streaming/silver_binance_transformation.py

Related:
- Step 10: Bronze ingestion (raw bytes storage)
- Step 11: Silver transformation (THIS JOB)
- Step 12: Gold aggregation (unified analytics)
- Decision #011: Bronze stores raw bytes for replayability
- Decision #012: Silver uses DLQ pattern for invalid records
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

# Import directly to avoid loading k2/__init__.py (which imports structlog, etc.)
import importlib.util

spec_udfs = importlib.util.spec_from_file_location(
    "raw_to_v2_transformation",
    str(Path(__file__).parent.parent.parent / "udfs" / "raw_to_v2_transformation.py"),
)
avro_module = importlib.util.module_from_spec(spec_udfs)
spec_udfs.loader.exec_module(avro_module)
deserialize_binance_raw_to_v2 = avro_module.deserialize_binance_raw_to_v2
extract_schema_id_udf = avro_module.extract_schema_id_udf

spec_validation = importlib.util.spec_from_file_location(
    "trade_validation",
    str(Path(__file__).parent.parent.parent / "validation" / "trade_validation.py"),
)
validation_module = importlib.util.module_from_spec(spec_validation)
spec_validation.loader.exec_module(validation_module)
validate_trade_record = validation_module.validate_trade_record
write_to_dlq = validation_module.write_to_dlq


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
        .config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
        )
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        # AWS SDK v2 region configuration (for Iceberg)
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.streaming.checkpointLocation", "/checkpoints/silver-binance/")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Transformation - Binance")
    print("Bronze → Silver (Validated) + DLQ (Invalid)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Source: bronze_binance_trades (raw bytes)")
    print("  • Target: silver_binance_trades (validated V2 schema)")
    print("  • DLQ: silver_dlq_trades (invalid records)")
    print("  • Trigger: 30 seconds (Silver), 30 seconds (DLQ)")
    print("  • Validation: Strict (price > 0, quantity > 0, etc.)")
    print("  • Checkpoint: /checkpoints/silver-binance/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Silver-Binance-Transformation")

    try:
        # Read from Bronze (streaming)
        print("✓ Reading from bronze_binance_trades...")
        bronze_df = spark.readStream.table("iceberg.market_data.bronze_binance_trades")

        # Deserialize Binance raw Avro and transform to V2
        # UDF handles: strip 5-byte header, deserialize Binance raw, transform to V2
        print("✓ Deserializing Binance raw and transforming to V2...")
        deserialized_df = (
            bronze_df.withColumn("trade", deserialize_binance_raw_to_v2(col("raw_bytes")))
            .select(
                "trade.*",  # All V2 fields + schema_id
                col("ingestion_timestamp").alias("bronze_ingestion_timestamp"),
                col("raw_bytes"),  # Keep for DLQ
                col("offset"),  # Keep for DLQ lineage
            )
            .withColumn("validation_timestamp", current_timestamp())
        )

        print("✓ Applying validation rules...")
        # Validate and split streams (valid vs invalid)
        valid_df, invalid_df = validate_trade_record(deserialized_df)

        # Write valid records to Silver
        print("✓ Starting Silver write stream (valid records)...")
        silver_query = (
            valid_df.select(
                # V2 Avro Fields (15 fields)
                "message_id",
                "trade_id",
                "symbol",
                "exchange",
                "asset_class",
                "timestamp",
                "price",
                "quantity",
                "currency",
                "side",
                "trade_conditions",
                "source_sequence",
                "ingestion_timestamp",
                "platform_sequence",
                "vendor_data",
                # Silver Metadata (3 fields)
                "validation_timestamp",
                "bronze_ingestion_timestamp",
                "schema_id",
            )
            .writeStream.format("iceberg")
            .outputMode("append")
            .option("checkpointLocation", "/checkpoints/silver-binance/")
            .trigger(processingTime="30 seconds")
            .toTable("iceberg.market_data.silver_binance_trades")
        )

        # Write invalid records to DLQ
        print("✓ Starting DLQ write stream (invalid records)...")
        _dlq_query = write_to_dlq(
            invalid_df,
            bronze_source="bronze_binance_trades",
            checkpoint_location="/checkpoints/silver-binance-dlq/",
        )

        print("\n" + "=" * 70)
        print("Silver Binance Transformation - RUNNING")
        print("=" * 70)
        print("\nMonitoring:")
        print("  • Spark UI: http://localhost:8090")
        print("  • Silver records: SELECT COUNT(*) FROM silver_binance_trades;")
        print(
            "  • DLQ records: SELECT COUNT(*) FROM silver_dlq_trades WHERE bronze_source='bronze_binance_trades';"
        )
        print("\nMetrics:")
        print("  • DLQ Rate: (DLQ count / (Silver count + DLQ count)) * 100")
        print("  • Target: DLQ rate < 0.1% (alert if > 1%)")
        print("\nPress Ctrl+C to stop (checkpoint saved automatically)")
        print("=" * 70 + "\n")

        # Await termination (both streams run concurrently)
        silver_query.awaitTermination()

    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print("Received interrupt signal - shutting down gracefully")
        print("=" * 70)
        print("✓ Checkpoints saved:")
        print("  • Silver: /checkpoints/silver-binance/")
        print("  • DLQ: /checkpoints/silver-binance-dlq/")
        print("✓ Jobs can resume from last offset")
        return 0

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())

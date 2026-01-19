#!/usr/bin/env python3
"""Silver Kraken Transformation Job - Bronze → Silver with DLQ (Native Spark Avro).

This Spark Structured Streaming job:
1. Reads raw bytes from bronze_kraken_trades
2. Strips Schema Registry header (5-byte header)
3. Deserializes Kraken raw Avro using Spark's native from_avro
4. Transforms Kraken raw schema to V2 unified schema
5. Validates trade records (price > 0, timestamp valid, etc.)
6. Routes valid records to silver_kraken_trades
7. Routes invalid records to silver_dlq_trades (Dead Letter Queue)

Medallion Architecture - Silver Layer:
- Bronze: Raw immutable data (replayability)
- Silver: Validated, transformed to V2 unified schema (quality checks)
- Gold: Business logic, derived columns

Industry Best Practices:
✓ Native Spark Avro (no custom UDFs, better performance)
✓ DLQ Pattern (invalid records tracked for observability)
✓ Schema Registry Integration (proper Avro deserialization)
✓ Validation Metadata (tracks validation_timestamp, bronze_ingestion_timestamp)

Configuration:
- Source: bronze_kraken_trades (Iceberg table, raw bytes)
- Targets: silver_kraken_trades (valid), silver_dlq_trades (invalid)
- Checkpoint: /checkpoints/silver-kraken/
- Trigger: 30 seconds (balance latency vs throughput)

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-avro_2.12:3.5.3 \
      --executor-cores 2 \
      --num-executors 2 \
      /opt/k2/src/k2/spark/jobs/streaming/silver_kraken_transformation_v3.py

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
from pyspark.sql.functions import (
    col,
    current_timestamp,
    expr,
    substring,
    concat,
    lit,
    when,
    to_date,
    from_json,
    struct,
    array,
    regexp_replace,
    coalesce,
    split,
    md5,
)
from pyspark.sql.types import StructType, StructField, StringType, LongType, BooleanType

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

# Import validation module
import importlib.util
spec_validation = importlib.util.spec_from_file_location("trade_validation", str(Path(__file__).parent.parent.parent / "validation" / "trade_validation.py"))
validation_module = importlib.util.module_from_spec(spec_validation)
spec_validation.loader.exec_module(validation_module)
validate_trade_record = validation_module.validate_trade_record
write_to_dlq = validation_module.write_to_dlq


# Kraken Raw Schema (from Schema Registry)
KRAKEN_RAW_SCHEMA = """
{
  "type": "record",
  "name": "KrakenRawTrade",
  "namespace": "com.k2.marketdata.raw",
  "fields": [
    {"name": "channel_id", "type": "long"},
    {"name": "price", "type": "string"},
    {"name": "volume", "type": "string"},
    {"name": "timestamp", "type": "string"},
    {"name": "side", "type": "string"},
    {"name": "order_type", "type": "string"},
    {"name": "misc", "type": "string"},
    {"name": "pair", "type": "string"},
    {"name": "ingestion_timestamp", "type": "long"}
  ]
}
"""


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
        # AWS SDK v2 region configuration (for Iceberg)
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.streaming.checkpointLocation", "/checkpoints/silver-kraken/")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Transformation - Kraken (Native Spark Avro)")
    print("Bronze → Silver (Validated V2) + DLQ (Invalid)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Source: bronze_kraken_trades (raw bytes)")
    print("  • Target: silver_kraken_trades (V2 unified schema)")
    print("  • DLQ: silver_dlq_trades (invalid records)")
    print("  • Method: Spark native from_avro (no UDFs)")
    print("  • Trigger: 30 seconds (Silver), 30 seconds (DLQ)")
    print("  • Validation: Strict (price > 0, quantity > 0, etc.)")
    print("  • Checkpoint: /checkpoints/silver-kraken/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Silver-Kraken-Transformation-V3")

    try:
        # Read from Bronze (streaming)
        print("✓ Reading from bronze_kraken_trades...")
        bronze_df = spark.readStream.table("iceberg.market_data.bronze_kraken_trades")

        # Step 1: Strip Schema Registry header (5 bytes: 1 magic + 4 schema_id)
        print("✓ Stripping Schema Registry header...")
        bronze_with_payload = bronze_df.withColumn(
            "avro_payload", substring(col("raw_bytes"), 6, 999999)  # Skip first 5 bytes
        ).withColumn(
            "schema_id",
            # Extract schema_id from bytes 2-5 (big-endian int32)
            expr("cast(conv(hex(substring(raw_bytes, 2, 4)), 16, 10) as int)")
        ).withColumn(
            "debug_raw_bytes_length", expr("length(raw_bytes)")
        ).withColumn(
            "debug_payload_length", expr("length(avro_payload)")
        )

        # Step 2: Deserialize Kraken raw Avro using Spark's from_avro
        print("✓ Deserializing Kraken raw Avro...")
        from pyspark.sql.avro.functions import from_avro

        deserialized_df = bronze_with_payload.withColumn(
            "kraken_raw", from_avro(col("avro_payload"), KRAKEN_RAW_SCHEMA)
        ).withColumn(
            "debug_kraken_raw_is_null", col("kraken_raw").isNull()
        ).withColumn(
            "debug_schema_id", col("schema_id")
        )

        # Debug: Write foreachBatch to log sample records
        def debug_batch(batch_df, batch_id):
            print(f"\n{'='*70}")
            print(f"DEBUG BATCH {batch_id} - Kraken Deserialization")
            print(f"{'='*70}")
            count = batch_df.count()
            print(f"Total records in batch: {count}")

            if count > 0:
                # Show sample raw data
                print("\n--- Sample Record (first row) ---")
                sample = batch_df.select(
                    "debug_raw_bytes_length",
                    "debug_payload_length",
                    "debug_schema_id",
                    "debug_kraken_raw_is_null",
                    "kraken_raw"
                ).limit(1).collect()

                if sample:
                    row = sample[0]
                    print(f"Raw bytes length: {row['debug_raw_bytes_length']}")
                    print(f"Avro payload length: {row['debug_payload_length']}")
                    print(f"Schema ID: {row['debug_schema_id']}")
                    print(f"kraken_raw is NULL: {row['debug_kraken_raw_is_null']}")
                    print(f"kraken_raw struct: {row['kraken_raw']}")

                # Count NULL kraken_raw
                null_count = batch_df.filter(col("kraken_raw").isNull()).count()
                print(f"\nRecords with NULL kraken_raw: {null_count}/{count}")

            print(f"{'='*70}\n")

        # Debug stream disabled due to resource constraints
        # TODO: Re-enable once we understand the from_avro NULL issue
        # debug_query = (
        #     deserialized_df
        #     .writeStream
        #     .foreachBatch(debug_batch)
        #     .option("checkpointLocation", "/checkpoints/silver-kraken-debug/")
        #     .start()
        # )

        # Step 3: Transform Kraken raw → V2 unified schema
        print("✓ Transforming to V2 unified schema...")
        v2_df = deserialized_df.select(
            # V2 Fields (15 fields)
            expr("uuid()").alias("message_id"),  # Generate UUID for deduplication
            # Generate trade_id (Kraken doesn't provide one): KRAKEN-{timestamp}-{hash}
            concat(
                lit("KRAKEN-"),
                col("kraken_raw.timestamp"),
                lit("-"),
                expr("substring(md5(concat(kraken_raw.timestamp, kraken_raw.pair, kraken_raw.price)), 1, 8)")
            ).alias("trade_id"),
            # Normalize symbol: XBT/USD → BTCUSD, ETH/USD → ETHUSD
            regexp_replace(
                regexp_replace(col("kraken_raw.pair"), "XBT", "BTC"), "/", ""
            ).alias("symbol"),
            lit("KRAKEN").alias("exchange"),
            lit("crypto").alias("asset_class"),
            # Parse timestamp: "1705584123.123456" → microseconds (cast to double, multiply by 1M, cast to long)
            (col("kraken_raw.timestamp").cast("double") * 1000000).cast("long").alias("timestamp"),
            col("kraken_raw.price").cast("decimal(18,8)").alias("price"),
            col("kraken_raw.volume").cast("decimal(18,8)").alias("quantity"),
            # Extract currency from pair (after /)
            when(
                expr("instr(kraken_raw.pair, '/') > 0"),
                regexp_replace(
                    expr("substring_index(kraken_raw.pair, '/', -1)"),  # Get part after /
                    "XBT",
                    "BTC"
                )
            )
            .otherwise(lit("USD"))
            .alias("currency"),
            # Side: 'b' = buy, 's' = sell
            when(col("kraken_raw.side") == "b", lit("BUY"))
            .otherwise(lit("SELL"))
            .alias("side"),
            # Trade conditions: order type (limit/market)
            array(
                when(col("kraken_raw.order_type") == "l", lit("limit"))
                .when(col("kraken_raw.order_type") == "m", lit("market"))
                .otherwise(col("kraken_raw.order_type"))
            ).alias("trade_conditions"),
            lit(None).cast("long").alias("source_sequence"),  # Kraken doesn't provide
            col("kraken_raw.ingestion_timestamp").alias("ingestion_timestamp"),
            lit(None).cast("long").alias("platform_sequence"),  # Not used
            # Vendor data as JSON string
            expr(
                "to_json(struct(kraken_raw.channel_id, kraken_raw.order_type, kraken_raw.misc, kraken_raw.pair))"
            ).alias("vendor_data"),
            # Silver metadata
            current_timestamp().alias("validation_timestamp"),
            col("ingestion_timestamp").alias("bronze_ingestion_timestamp"),
            col("schema_id"),
            # Keep for DLQ
            col("raw_bytes"),
            col("offset"),
        )

        # Step 4: Validate and split streams
        print("✓ Applying validation rules...")
        valid_df, invalid_df = validate_trade_record(v2_df)

        # Step 5: Write valid records to Silver
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
            .option("checkpointLocation", "/checkpoints/silver-kraken/")
            .trigger(processingTime="30 seconds")
            .toTable("iceberg.market_data.silver_kraken_trades")
        )

        # Step 6: Write invalid records to DLQ
        print("✓ Starting DLQ write stream (invalid records)...")
        dlq_query = write_to_dlq(
            invalid_df, bronze_source="bronze_kraken_trades", checkpoint_location="/checkpoints/silver-kraken-dlq/"
        )

        print("\n" + "=" * 70)
        print("Silver Kraken Transformation - RUNNING (Native Avro)")
        print("=" * 70)
        print("\nMonitoring:")
        print("  • Spark UI: http://localhost:8090")
        print("  • Silver records: SELECT COUNT(*) FROM silver_kraken_trades;")
        print("  • DLQ records: SELECT COUNT(*) FROM silver_dlq_trades WHERE bronze_source='bronze_kraken_trades';")
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
        print("  • Silver: /checkpoints/silver-kraken/")
        print("  • DLQ: /checkpoints/silver-kraken-dlq/")
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

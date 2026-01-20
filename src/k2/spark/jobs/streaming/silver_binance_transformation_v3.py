#!/usr/bin/env python3
"""Silver Binance Transformation Job - Bronze → Silver with DLQ (Native Spark Avro).

This Spark Structured Streaming job:
1. Reads raw bytes from bronze_binance_trades
2. Strips Schema Registry header (5-byte header)
3. Deserializes Binance raw Avro using Spark's native from_avro
4. Transforms Binance raw schema to V2 unified schema
5. Validates trade records (price > 0, timestamp valid, etc.)
6. Routes valid records to silver_binance_trades
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
- Source: bronze_binance_trades (Iceberg table, raw bytes)
- Targets: silver_binance_trades (valid), silver_dlq_trades (invalid)
- Checkpoint: /checkpoints/silver-binance/
- Trigger: 30 seconds (balance latency vs throughput)

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-avro_2.12:3.5.3 \
      --executor-cores 2 \
      --num-executors 2 \
      /opt/k2/src/k2/spark/jobs/streaming/silver_binance_transformation_v3.py

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
)
from pyspark.sql.types import StructType, StructField, StringType, LongType, BooleanType

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

# Import spark_session module directly (bypasses k2 package initialization)
import importlib.util
spec_spark = importlib.util.spec_from_file_location(
    "spark_session",
    str(Path(__file__).parent.parent.parent / "utils" / "spark_session.py")
)
spark_session_module = importlib.util.module_from_spec(spec_spark)
spec_spark.loader.exec_module(spark_session_module)
create_streaming_spark_session = spark_session_module.create_streaming_spark_session

# Import validation module
import importlib.util
spec_validation = importlib.util.spec_from_file_location("trade_validation", str(Path(__file__).parent.parent.parent / "validation" / "trade_validation.py"))
validation_module = importlib.util.module_from_spec(spec_validation)
spec_validation.loader.exec_module(validation_module)
validate_trade_record = validation_module.validate_trade_record
write_to_dlq = validation_module.write_to_dlq


# Binance Raw Schema (from Schema Registry)
BINANCE_RAW_SCHEMA = """
{
  "type": "record",
  "name": "BinanceRawTrade",
  "namespace": "com.k2.marketdata.raw",
  "fields": [
    {"name": "event_type", "type": "string"},
    {"name": "event_time_ms", "type": "long"},
    {"name": "symbol", "type": "string"},
    {"name": "trade_id", "type": "long"},
    {"name": "price", "type": "string"},
    {"name": "quantity", "type": "string"},
    {"name": "trade_time_ms", "type": "long"},
    {"name": "is_buyer_maker", "type": "boolean"},
    {"name": "is_best_match", "type": ["null", "boolean"], "default": null},
    {"name": "ingestion_timestamp", "type": "long"}
  ]
}
"""


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Transformation - Binance (Native Spark Avro)")
    print("Bronze → Silver (Validated V2) + DLQ (Invalid)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Source: bronze_binance_trades (raw bytes)")
    print("  • Target: silver_binance_trades (V2 unified schema)")
    print("  • DLQ: silver_dlq_trades (invalid records)")
    print("  • Method: Spark native from_avro (no UDFs)")
    print("  • Trigger: 30 seconds (Silver), 30 seconds (DLQ)")
    print("  • Validation: Strict (price > 0, quantity > 0, etc.)")
    print("  • Checkpoint: /checkpoints/silver-binance/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session with production-ready configuration
    spark = create_streaming_spark_session(
        app_name="K2-Silver-Binance-Transformation-V3",
        checkpoint_location="/checkpoints/silver-binance/"
    )

    try:
        # Read from Bronze (streaming)
        print("✓ Reading from bronze_binance_trades...")
        bronze_df = spark.readStream.table("iceberg.market_data.bronze_binance_trades")

        # Step 1: Strip Schema Registry header (5 bytes: 1 magic + 4 schema_id)
        print("✓ Stripping Schema Registry header...")
        bronze_with_payload = bronze_df.withColumn(
            "avro_payload", substring(col("raw_bytes"), 6, 999999)  # Skip first 5 bytes
        ).withColumn(
            "schema_id",
            # Extract schema_id from bytes 2-5 (big-endian int32)
            expr("cast(conv(hex(substring(raw_bytes, 2, 4)), 16, 10) as int)")
        )

        # Step 2: Deserialize Binance raw Avro using Spark's from_avro
        print("✓ Deserializing Binance raw Avro...")
        from pyspark.sql.avro.functions import from_avro

        deserialized_df = bronze_with_payload.withColumn(
            "binance_raw", from_avro(col("avro_payload"), BINANCE_RAW_SCHEMA)
        )

        # Step 3: Transform Binance raw → V2 unified schema
        print("✓ Transforming to V2 unified schema...")
        v2_df = deserialized_df.select(
            # V2 Fields (15 fields)
            expr("uuid()").alias("message_id"),  # Generate UUID for deduplication
            concat(lit("BINANCE-"), col("binance_raw.trade_id").cast("string")).alias("trade_id"),
            col("binance_raw.symbol").alias("symbol"),
            lit("BINANCE").alias("exchange"),
            lit("crypto").alias("asset_class"),
            (col("binance_raw.trade_time_ms") * 1000).alias("timestamp"),  # ms → microseconds
            col("binance_raw.price").cast("decimal(18,8)").alias("price"),
            col("binance_raw.quantity").cast("decimal(18,8)").alias("quantity"),
            # Extract currency from symbol (BTCUSDT → USDT)
            when(col("binance_raw.symbol").endswith("USDT"), lit("USDT"))
            .when(col("binance_raw.symbol").endswith("USD"), lit("USD"))
            .when(col("binance_raw.symbol").endswith("BTC"), lit("BTC"))
            .when(col("binance_raw.symbol").endswith("ETH"), lit("ETH"))
            .otherwise(expr("substring(binance_raw.symbol, -4, 4)"))
            .alias("currency"),
            # Side: is_buyer_maker = true → aggressor was seller → SELL
            when(col("binance_raw.is_buyer_maker") == True, lit("SELL"))
            .otherwise(lit("BUY"))
            .alias("side"),
            array().cast("array<string>").alias("trade_conditions"),  # Empty for crypto
            lit(None).cast("long").alias("source_sequence"),  # Binance doesn't provide
            col("binance_raw.ingestion_timestamp").alias("ingestion_timestamp"),
            lit(None).cast("long").alias("platform_sequence"),  # Not used
            # Vendor data as JSON string
            expr(
                "to_json(struct(binance_raw.event_type, binance_raw.event_time_ms, binance_raw.is_buyer_maker, binance_raw.is_best_match))"
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
            .option("checkpointLocation", "/checkpoints/silver-binance/")
            .trigger(processingTime="30 seconds")
            .toTable("iceberg.market_data.silver_binance_trades")
        )

        # Step 6: Write invalid records to DLQ
        print("✓ Starting DLQ write stream (invalid records)...")
        dlq_query = write_to_dlq(
            invalid_df, bronze_source="bronze_binance_trades", checkpoint_location="/checkpoints/silver-binance-dlq/"
        )

        print("\n" + "=" * 70)
        print("Silver Binance Transformation - RUNNING (Native Avro)")
        print("=" * 70)
        print("\nMonitoring:")
        print("  • Spark UI: http://localhost:8090")
        print("  • Silver records: SELECT COUNT(*) FROM silver_binance_trades;")
        print("  • DLQ records: SELECT COUNT(*) FROM silver_dlq_trades WHERE bronze_source='bronze_binance_trades';")
        print("\nMetrics:")
        print("  • DLQ Rate: (DLQ count / (Silver count + DLQ count)) * 100")
        print("  • Target: DLQ rate < 0.1% (alert if > 1%)")
        print("\nPress Ctrl+C to stop (checkpoint saved automatically)")
        print("=" * 70 + "\n")

        # Await termination (both streams run concurrently)
        # Use awaitAnyTermination() to detect if either stream fails
        spark.streams.awaitAnyTermination()

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

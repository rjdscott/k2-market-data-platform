#!/usr/bin/env python3
"""Kraken Bronze Ingestion Job - Kafka to Bronze Layer.

This Spark Structured Streaming job ingests raw Kafka messages from the Kraken topic
and writes them to the bronze_kraken_trades Iceberg table WITHOUT deserialization.

Architecture:
- Source: market.crypto.trades.kraken (Kafka topic)
- Target: bronze_kraken_trades (Iceberg table)
- Pattern: Raw bytes ingestion (no Avro deserialization)
- Checkpoint: /checkpoints/bronze-kraken/

Configuration:
- Trigger: 30 seconds (lower volume than Binance)
- Max offsets per trigger: 1,000 messages
- Workers: 1 (lower volume)

Usage:
    # From host
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      --executor-cores 1 \
      --num-executors 1 \
      /opt/k2/src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py

    # From within container
    /opt/spark/bin/spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      /opt/k2/src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, to_date

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog configuration."""
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
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.streaming.checkpointLocation", "/checkpoints/bronze-kraken/")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Ingestion - Kraken")
    print("Kafka → Bronze Layer (Raw Bytes)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Topic: market.crypto.trades.kraken")
    print("  • Target: bronze_kraken_trades")
    print("  • Trigger: 30 seconds (lower volume)")
    print("  • Max offsets per trigger: 1,000")
    print("  • Checkpoint: /checkpoints/bronze-kraken/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Bronze-Kraken-Ingestion")

    try:
        # Read from Kafka (Kraken topic only)
        print("Starting Kafka stream reader...")
        kafka_df = (
            spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", "kafka:29092")
            .option("subscribe", "market.crypto.trades.kraken")
            .option("startingOffsets", "latest")
            .option("maxOffsetsPerTrigger", 1000)
            .option("failOnDataLoss", "false")
            .load()
        )

        print("✓ Kafka stream reader configured")

        # Transform to Bronze schema (NO deserialization)
        print("Transforming to Bronze schema (raw bytes)...")
        bronze_df = kafka_df.select(
            col("key").cast("string").alias("message_key"),
            col("value").alias("avro_payload"),  # Keep as binary
            col("topic"),
            col("partition"),
            col("offset"),
            col("timestamp").alias("kafka_timestamp"),
            current_timestamp().alias("ingestion_timestamp"),
            to_date(current_timestamp()).alias("ingestion_date"),
        )

        print("✓ Bronze schema transformation configured")

        # Write to Bronze table
        print("Starting Bronze writer...")
        query = (
            bronze_df.writeStream.format("iceberg")
            .outputMode("append")
            .trigger(processingTime="30 seconds")
            .option("checkpointLocation", "/checkpoints/bronze-kraken/")
            .option("path", "iceberg.market_data.bronze_kraken_trades")
            .option("fanout-enabled", "true")
            .start()
        )

        print("✓ Bronze writer started")
        print("\nStreaming job running...")
        print("  • Spark UI: http://localhost:8080")
        print("  • Check Bronze table: SELECT COUNT(*) FROM iceberg.market_data.bronze_kraken_trades")
        print("\nPress Ctrl+C to stop (checkpoint will be saved)\n")

        # Wait for termination
        query.awaitTermination()

    except KeyboardInterrupt:
        print("\n\nReceived interrupt signal, shutting down gracefully...")
        print("✓ Checkpoint saved: /checkpoints/bronze-kraken/")
        return 0
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())

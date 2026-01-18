#!/usr/bin/env python3
"""Binance Bronze Ingestion Job - Kafka to Bronze Layer.

This Spark Structured Streaming job ingests raw Kafka messages from the Binance topic
and writes them to the bronze_binance_trades Iceberg table WITHOUT deserialization.

Architecture:
- Source: market.crypto.trades.binance (Kafka topic)
- Target: bronze_binance_trades (Iceberg table)
- Pattern: Raw bytes ingestion (no Avro deserialization)
- Checkpoint: /checkpoints/bronze-binance/

Configuration:
- Trigger: 10 seconds (high volume)
- Max offsets per trigger: 10,000 messages
- Workers: 3 (high throughput)

Usage:
    # From host
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      --executor-cores 2 \
      --num-executors 3 \
      /opt/k2/src/k2/spark/jobs/streaming/bronze_binance_ingestion.py

    # From within container
    /opt/spark/bin/spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      /opt/k2/src/k2/spark/jobs/streaming/bronze_binance_ingestion.py
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, to_date

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))


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
        # S3/MinIO configuration (required for AWS SDK)
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        # Set dummy AWS region for MinIO (both Hadoop and AWS SDK v2)
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        # AWS SDK v2 region configuration (for Iceberg)
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.streaming.checkpointLocation", "/checkpoints/bronze-binance/")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Ingestion - Binance")
    print("Kafka → Bronze Layer (Raw Bytes)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Topic: market.crypto.trades.binance")
    print("  • Target: bronze_binance_trades")
    print("  • Trigger: 10 seconds (high volume)")
    print("  • Max offsets per trigger: 10,000")
    print("  • Checkpoint: /checkpoints/bronze-binance/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Bronze-Binance-Ingestion")

    try:
        # Read from Kafka (Binance RAW topic)
        print("Starting Kafka stream reader...")
        kafka_df = (
            spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", "kafka:29092")
            .option("subscribe", "market.crypto.trades.binance.raw")
            .option("startingOffsets", "latest")
            .option("maxOffsetsPerTrigger", 10000)
            .option("failOnDataLoss", "false")
            .load()
        )

        print("✓ Kafka stream reader configured")

        # Deserialize Avro (with Schema Registry format handling)
        print("Deserializing Avro from Schema Registry...")
        from pyspark.sql.avro.functions import from_avro
        from pyspark.sql.functions import expr

        # Option: Use Schema Registry URL for automatic schema fetching
        # from_avro can fetch schema from registry using the schema ID in the message
        options = {"mode": "FAILFAST"}

        # Alternative: Strip Schema Registry header (5 bytes: magic byte + 4-byte schema ID)
        # and use schema string
        print("Stripping Schema Registry header (5 bytes)...")
        kafka_df_no_header = kafka_df.selectExpr(
            "substring(value, 6, length(value)-5) as avro_data",
            "topic",
            "partition",
            "offset",
            "timestamp"
        )

        # Load schema
        schema_path = "/opt/k2/src/k2/schemas/binance_raw_trade.avsc"
        with open(schema_path) as f:
            avro_schema = f.read()

        # Deserialize plain Avro (without Schema Registry header)
        trades_df = kafka_df_no_header.select(
            from_avro(col("avro_data"), avro_schema, options).alias("trade")
        )

        # Debug: Print schema to see actual field names
        print("Deserialized schema:")
        trades_df.printSchema()

        # Expand to Bronze schema
        # Use descriptive field names from schema (Avro deserializes with schema field names)
        print("Transforming to Bronze schema...")
        bronze_df = trades_df.select(
            col("trade.event_type"),
            col("trade.event_time_ms"),
            col("trade.symbol"),
            col("trade.trade_id"),
            col("trade.price"),
            col("trade.quantity"),
            col("trade.trade_time_ms"),
            col("trade.is_buyer_maker"),
            col("trade.is_best_match"),
            col("trade.ingestion_timestamp"),
            # Convert microseconds to date: divide by 1M to get seconds, cast to timestamp, then to date
            to_date((col("trade.ingestion_timestamp") / 1000000).cast("timestamp")).alias("ingestion_date"),
        )

        print("✓ Bronze schema transformation configured")

        # Write to Bronze table
        # Note: For Spark Structured Streaming, we must use table name (not path)
        # and cannot use partitionBy() - partition spec is defined in table DDL
        print("Starting Bronze writer...")
        query = (
            bronze_df.writeStream.format("iceberg")
            .outputMode("append")
            .trigger(processingTime="10 seconds")
            .option("checkpointLocation", "/checkpoints/bronze-binance/")
            .option("fanout-enabled", "true")
            .toTable("iceberg.market_data.bronze_binance_trades")
        )

        print("✓ Bronze writer started")
        print("\nStreaming job running...")
        print("  • Spark UI: http://localhost:8090")
        print("  • Check Bronze table: SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades")
        print("\nPress Ctrl+C to stop (checkpoint will be saved)\n")

        # Wait for termination
        query.awaitTermination()

    except KeyboardInterrupt:
        print("\n\nReceived interrupt signal, shutting down gracefully...")
        print("✓ Checkpoint saved: /checkpoints/bronze-binance/")
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

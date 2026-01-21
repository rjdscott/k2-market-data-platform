#!/usr/bin/env python3
"""Bronze Ingestion Job - Kraken (Kafka → Bronze Iceberg).

This job streams raw Kraken trade data from Kafka and writes to Bronze Iceberg table.
No transformations - pure raw data storage for audit trail and reprocessing.

Architecture:
- Source: Kafka topic market.crypto.trades.kraken.raw
- Target: bronze_kraken_trades (Iceberg table)
- Checkpoints: /checkpoints/bronze-kraken/
- Trigger: 10 seconds (high throughput for raw data)

Usage:
    docker exec k2-spark-master bash -c "cd /opt/k2 && /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0,org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,org.apache.spark:spark-avro_2.12:3.5.0 \
      --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
      --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
      src/k2/spark/jobs/streaming/bronze_ingestion_kraken.py"
"""

import sys

from pyspark.sql import SparkSession
from pyspark.sql.avro.functions import from_avro
from pyspark.sql.functions import col, to_date


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog and Kafka configuration."""
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
        # AWS SDK v2 region configuration
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .getOrCreate()
    )


def fetch_avro_schema_from_registry(
    schema_name: str, schema_registry_url: str = "http://schema-registry-1:8081"
) -> str:
    """Fetch Avro schema from Schema Registry.

    Args:
        schema_name: Schema subject name
        schema_registry_url: Schema Registry base URL

    Returns:
        Avro schema as JSON string
    """
    import json
    import urllib.request

    subject = schema_name
    url = f"{schema_registry_url}/subjects/{subject}/versions/latest"

    print(f"Fetching schema from Schema Registry: {url}")

    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())
            schema_str = data["schema"]
            schema_id = data["id"]
            print(f"✓ Fetched schema (ID: {schema_id}) for subject: {subject}")
            return schema_str
    except Exception as e:
        print(f"✗ Failed to fetch schema from Registry: {e}")
        raise


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Ingestion - Kraken")
    print("Kafka (raw Avro) → Bronze Iceberg (no transformation)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Source: Kafka topic market.crypto.trades.kraken.raw")
    print("  • Target: bronze_kraken_trades")
    print("  • Partitioning: days(ingestion_date)")
    print("  • Trigger: 10 seconds")
    print("  • Mode: Append (raw data storage)")
    print("  • Checkpoint: /checkpoints/bronze-kraken/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Bronze-Kraken-Ingestion")

    try:
        # Fetch Kraken raw schema from Schema Registry
        avro_schema = fetch_avro_schema_from_registry("market.crypto.trades.kraken.raw-value")

        # Read from Kafka
        print("Reading from Kafka...")
        kafka_df = (
            spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", "kafka:9092")
            .option("subscribe", "market.crypto.trades.kraken.raw")
            .option("startingOffsets", "latest")
            .option("maxOffsetsPerTrigger", 10000)
            .option("failOnDataLoss", "false")
            .load()
        )

        print("✓ Kafka stream reader configured")

        # Deserialize Avro payloads
        print("Deserializing Avro payloads...")
        trades_df = kafka_df.select(from_avro(col("value"), avro_schema).alias("trade"))

        print("✓ Avro deserialization configured")

        # Expand struct and add partition field
        bronze_df = trades_df.select(
            col("trade.channel_id"),
            col("trade.price"),
            col("trade.volume"),
            col("trade.timestamp"),
            col("trade.side"),
            col("trade.order_type"),
            col("trade.misc"),
            col("trade.pair"),
            col("trade.ingestion_timestamp"),
            to_date(col("trade.ingestion_timestamp") / 1000000).alias("ingestion_date"),
        )

        # Write to Bronze Iceberg table
        print("Starting Bronze writer for Kraken...")
        bronze_table = "iceberg.market_data.bronze_kraken_trades"

        query = (
            bronze_df.writeStream.format("iceberg")
            .outputMode("append")
            .trigger(processingTime="10 seconds")
            .option("checkpointLocation", "/checkpoints/bronze-kraken/")
            .option("path", bronze_table)
            .option("fanout-enabled", "true")
            .start()
        )

        print("✓ Bronze writer started")
        print("\nStreaming job running...")
        print("  • Spark UI: http://localhost:8090")
        print(f"  • Check Bronze table: SELECT COUNT(*) FROM {bronze_table}")
        print("  • Raw data preserved: No transformations applied")
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

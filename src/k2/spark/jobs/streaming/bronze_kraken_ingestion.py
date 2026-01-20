#!/usr/bin/env python3
"""Kraken Bronze Ingestion Job - Kafka to Bronze Layer (Raw Bytes).

This Spark Structured Streaming job ingests raw Kafka messages from the Kraken topic
and writes them to bronze_kraken_trades Iceberg table as immutable raw bytes.

Best Practice (Medallion Architecture):
- Bronze: Immutable raw data landing zone (NO deserialization)
- Silver: Validated, deserialized data (validation + schema compliance)
- Gold: Business logic, derived columns, aggregations

Architecture:
- Source: market.crypto.trades.kraken.raw (Kafka topic)
- Target: bronze_kraken_trades (Iceberg table)
- Pattern: Raw bytes with Schema Registry headers (5-byte header + Avro payload)
- Checkpoint: /checkpoints/bronze-kraken/

Why Raw Bytes in Bronze:
1. Replayability: Replay Bronze → Silver if schema or logic changes
2. Schema Evolution: Bronze unchanged when schema versions evolve
3. Debugging: Inspect exact bytes producer sent (including schema ID)
4. Auditability: Immutable raw data for compliance

Configuration:
- Trigger: 30 seconds (lower volume than Binance)
- Max offsets per trigger: 1,000 messages
- Workers: 1 (lower volume)

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      --executor-cores 1 \
      --num-executors 1 \
      /opt/k2/src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py

Related:
- Step 11: Silver transformation (deserializes Bronze raw_bytes)
- Decision #011: Bronze stores raw bytes for replayability
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, to_date

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

# Import spark_session module directly (bypasses k2 package initialization)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "spark_session",
    str(Path(__file__).parent.parent.parent / "utils" / "spark_session.py")
)
spark_session_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark_session_module)
create_streaming_spark_session = spark_session_module.create_streaming_spark_session


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Ingestion - Kraken (Raw Bytes)")
    print("Kafka → Bronze Layer (Immutable Raw Data)")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Topic: market.crypto.trades.kraken.raw")
    print("  • Target: bronze_kraken_trades")
    print("  • Pattern: Raw bytes (5-byte header + Avro payload)")
    print("  • Trigger: 30 seconds (lower volume)")
    print("  • Max offsets per trigger: 1,000")
    print("  • Checkpoint: /checkpoints/bronze-kraken/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session with production-ready configuration
    spark = create_streaming_spark_session(
        app_name="K2-Bronze-Kraken-Ingestion",
        checkpoint_location="/checkpoints/bronze-kraken/"
    )

    try:
        # Read from Kafka (Kraken RAW topic)
        print("Starting Kafka stream reader...")
        kafka_df = (
            spark.readStream
            .format("kafka")
            .option("kafka.bootstrap.servers", "kafka:29092")
            .option("subscribe", "market.crypto.trades.kraken.raw")
            .option("startingOffsets", "latest")
            .option("maxOffsetsPerTrigger", 1000)
            .option("failOnDataLoss", "false")
            # Consumer group configuration (prevents abandoned groups)
            .option("kafka.group.id", "k2-bronze-kraken-ingestion")
            .option("kafka.session.timeout.ms", "30000")
            .option("kafka.request.timeout.ms", "40000")
            .load()
        )

        print("✓ Kafka stream reader configured")

        # Transform to Bronze schema (raw bytes + metadata)
        # NO deserialization - keep Schema Registry headers for Silver layer
        print("Transforming to Bronze schema (raw bytes)...")
        bronze_df = (
            kafka_df
            .selectExpr(
                "value as raw_bytes",              # Full Kafka value (header + payload)
                "topic",                           # Source topic name
                "partition",                       # Kafka partition
                "offset",                          # Kafka offset
                "timestamp as kafka_timestamp"     # Kafka message timestamp
            )
            .withColumn("ingestion_timestamp", current_timestamp())
            .withColumn("ingestion_date", to_date(current_timestamp()))
        )

        print("✓ Bronze schema configured (7 fields)")
        print("  • raw_bytes: Full Kafka value (5-byte header + Avro)")
        print("  • kafka_timestamp: Message timestamp from Kafka")
        print("  • ingestion_timestamp: When ingested to Bronze")
        print("  • ingestion_date: Partition key")

        # Write to Bronze table
        print("\nStarting Bronze writer...")
        query = (
            bronze_df.writeStream
            .format("iceberg")
            .outputMode("append")
            .trigger(processingTime="30 seconds")
            .option("checkpointLocation", "/checkpoints/bronze-kraken/")
            .option("fanout-enabled", "true")
            .toTable("iceberg.market_data.bronze_kraken_trades")
        )

        print("✓ Bronze writer started")
        print("\n" + "=" * 70)
        print("Streaming job RUNNING - Raw bytes ingestion")
        print("=" * 70)
        print("\nMonitor:")
        print("  • Spark UI: http://localhost:8090")
        print("  • Query Bronze: SELECT COUNT(*) FROM bronze_kraken_trades;")
        print("  • Check raw_bytes: SELECT raw_bytes, length(raw_bytes) FROM bronze_kraken_trades LIMIT 1;")
        print("\nNext Step:")
        print("  • Silver transformation will deserialize raw_bytes")
        print("\nPress Ctrl+C to stop (checkpoint saved automatically)")
        print("=" * 70 + "\n")

        # Wait for termination
        query.awaitTermination()

    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print("Received interrupt signal - shutting down gracefully")
        print("=" * 70)
        print("✓ Checkpoint saved: /checkpoints/bronze-kraken/")
        print("✓ Job can resume from last offset")
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

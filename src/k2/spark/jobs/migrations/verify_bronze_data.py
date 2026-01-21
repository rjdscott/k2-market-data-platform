#!/usr/bin/env python3
"""Quick verification - check Bronze tables have data."""

from pyspark.sql import SparkSession


def create_spark_session() -> SparkSession:
    """Create Spark session with Iceberg catalog."""
    return (
        SparkSession.builder.appName("K2-Bronze-Verify")
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "rest")
        .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
        .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
        .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
        .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
        .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
        .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
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
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .getOrCreate()
    )


def main():
    """Check Bronze tables."""
    print("\n" + "=" * 70)
    print("Bronze Raw Bytes Verification")
    print("=" * 70 + "\n")

    spark = create_spark_session()

    try:
        tables = ["bronze_binance_trades", "bronze_kraken_trades"]

        for table in tables:
            full_table_name = f"iceberg.market_data.{table}"
            print(f"\n→ {table}")
            print("-" * 70)

            # Count rows
            count_df = spark.sql(f"SELECT COUNT(*) as count FROM {full_table_name}")
            count = count_df.collect()[0]["count"]
            print(f"  Total rows: {count:,}")

            if count > 0:
                # Check raw_bytes length
                length_df = spark.sql(f"""
                    SELECT
                        MIN(length(raw_bytes)) as min_len,
                        MAX(length(raw_bytes)) as max_len,
                        AVG(length(raw_bytes)) as avg_len
                    FROM {full_table_name}
                """)
                length_row = length_df.collect()[0]
                print(
                    f"  raw_bytes length (min/avg/max): {length_row['min_len']}/{int(length_row['avg_len'])}/{length_row['max_len']} bytes"
                )

                # Sample record
                print("\n  Sample record:")
                sample_df = spark.sql(f"""
                    SELECT
                        topic,
                        partition,
                        offset,
                        length(raw_bytes) as raw_bytes_len,
                        kafka_timestamp,
                        ingestion_timestamp
                    FROM {full_table_name}
                    LIMIT 1
                """)
                sample_df.show(truncate=False)

                # Check if raw_bytes > 5 (has Schema Registry header)
                if length_row["min_len"] > 5:
                    print("  ✓ raw_bytes includes Schema Registry header (5 bytes)")
                else:
                    print("  ✗ raw_bytes too short (missing header?)")
            else:
                print("  ⚠ No data yet (jobs may still be starting)")

        print("\n" + "=" * 70)
        print("✓ Bronze verification complete!")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

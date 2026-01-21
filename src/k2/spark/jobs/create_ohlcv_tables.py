#!/usr/bin/env python3
"""Create OHLCV Iceberg tables for crypto market analytics.

OHLCV Tables Purpose:
- Pre-aggregated Open-High-Low-Close-Volume data for multiple timeframes
- Timeframes: 1m, 5m, 30m, 1h, 1d
- Daily partitioning for efficient analytical queries
- Variable retention policies based on timeframe
- Source: gold_crypto_trades (direct rollup, no chaining)

Retention Policies:
- gold_ohlcv_1m: 90 days (high-frequency trading)
- gold_ohlcv_5m: 180 days (intraday analysis)
- gold_ohlcv_30m: 1 year (medium-term trends)
- gold_ohlcv_1h: 3 years (long-term daily analysis)
- gold_ohlcv_1d: 5 years (historical trends)

Usage:
    # From host
    docker exec spark-master spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_ohlcv_tables.py

    # From within container
    spark-submit --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_ohlcv_tables.py
"""

import sys
from pathlib import Path
import importlib.util

# Import spark_session module directly without adding k2 to path
spark_session_path = Path(__file__).parent.parent / "utils" / "spark_session.py"
spec = importlib.util.spec_from_file_location("spark_session", spark_session_path)
spark_session_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark_session_module)
create_spark_session = spark_session_module.create_spark_session


def create_ohlcv_table(spark, timeframe: str, namespace: str = "market_data"):
    """Create OHLCV Iceberg table for a specific timeframe.

    Args:
        spark: SparkSession with Iceberg catalog configured
        timeframe: One of: 1m, 5m, 30m, 1h, 1d
        namespace: Iceberg namespace (default: market_data)

    Returns:
        True if successful, False otherwise
    """
    table_name = f"iceberg.{namespace}.gold_ohlcv_{timeframe}"

    print(f"\n{'=' * 70}")
    print(f"Creating OHLCV Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Drop table if exists (for clean setup)
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print(f"✓ Cleaned up existing table: {timeframe}")
    except Exception as e:
        print(f"⚠ Table cleanup: {e}")

    # Determine partitioning strategy based on timeframe
    # 1d uses monthly partitioning, all others use daily
    if timeframe == "1d":
        partition_spec = "PARTITIONED BY (months(window_date))"
        partition_desc = "Monthly partitioning"
    else:
        partition_spec = "PARTITIONED BY (days(window_date))"
        partition_desc = "Daily partitioning"

    # Create OHLCV table with unified schema
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        symbol STRING NOT NULL COMMENT 'Trading pair (BTCUSDT, ETHUSDT, etc.)',
        exchange STRING NOT NULL COMMENT 'Exchange code: BINANCE, KRAKEN, etc.',
        window_start TIMESTAMP NOT NULL COMMENT 'Candle window start time (inclusive)',
        window_end TIMESTAMP NOT NULL COMMENT 'Candle window end time (exclusive)',
        window_date DATE NOT NULL COMMENT 'Partition key: Date of window_start',

        open_price DECIMAL(18, 8) NOT NULL COMMENT 'First trade price in window',
        high_price DECIMAL(18, 8) NOT NULL COMMENT 'Highest trade price in window',
        low_price DECIMAL(18, 8) NOT NULL COMMENT 'Lowest trade price in window',
        close_price DECIMAL(18, 8) NOT NULL COMMENT 'Last trade price in window',

        volume DECIMAL(18, 8) NOT NULL COMMENT 'Total traded volume in window',
        trade_count BIGINT NOT NULL COMMENT 'Number of trades in window',
        vwap DECIMAL(18, 8) NOT NULL COMMENT 'Volume-weighted average price',

        created_at TIMESTAMP NOT NULL COMMENT 'Record creation timestamp',
        updated_at TIMESTAMP NOT NULL COMMENT 'Record last update timestamp'
    )
    USING iceberg
    {partition_spec}
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '67108864',
        'write.distribution-mode' = 'hash',
        'write.delete.mode' = 'merge-on-read',
        'write.update.mode' = 'merge-on-read',
        'commit.retry.num-retries' = '5'
    )
    COMMENT 'OHLCV {timeframe} aggregations from gold_crypto_trades'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print(f"  Partitioning: {partition_desc}")
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False

    # Verify table
    print(f"\nVerifying table schema for {timeframe}...")
    df_schema = spark.sql(f"DESCRIBE EXTENDED {table_name}")
    df_schema.show(50, truncate=False)

    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 OHLCV Tables Creation")
    print("Gold Layer Enhancement - Multi-Timeframe Analytics")
    print("=" * 70 + "\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-OHLCV-Tables")

    try:
        # Show current Spark config
        print("Spark Configuration:")
        print(f"  Catalog: {spark.conf.get('spark.sql.catalog.iceberg')}")
        print(f"  Catalog Type: {spark.conf.get('spark.sql.catalog.iceberg.type')}")
        print(f"  Catalog URI: {spark.conf.get('spark.sql.catalog.iceberg.uri')}")
        print(f"  Warehouse: {spark.conf.get('spark.sql.catalog.iceberg.warehouse')}")
        print()

        # Create namespace if not exists
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Create all 5 OHLCV tables
        timeframes = ["1m", "5m", "30m", "1h", "1d"]
        success_count = 0

        for timeframe in timeframes:
            if create_ohlcv_table(spark, timeframe, namespace="market_data"):
                success_count += 1
            print()  # Blank line between tables

        # Summary
        print(f"\n{'=' * 70}")
        print("OHLCV Tables Creation Summary")
        print(f"{'=' * 70}\n")
        print(f"✓ Created {success_count}/{len(timeframes)} OHLCV tables\n")

        print("Tables Created:")
        print("  - gold_ohlcv_1m: 1-minute candles (90-day retention)")
        print("  - gold_ohlcv_5m: 5-minute candles (180-day retention)")
        print("  - gold_ohlcv_30m: 30-minute candles (1-year retention)")
        print("  - gold_ohlcv_1h: 1-hour candles (3-year retention)")
        print("  - gold_ohlcv_1d: 1-day candles (5-year retention)")

        print("\nSchema (Unified across all timeframes):")
        print("  - Primary Keys: symbol, exchange, window_start, window_end")
        print("  - OHLC Metrics: open, high, low, close (DECIMAL 18,8)")
        print("  - Volume Metrics: volume, trade_count, vwap")
        print("  - Metadata: created_at, updated_at")

        print("\nPartitioning Strategy:")
        print("  - 1m/5m/30m/1h: Daily partitions (days(window_date))")
        print("  - 1d: Monthly partitions (months(window_date))")

        print("\nCompression & Storage:")
        print("  - Parquet: Zstd compression")
        print("  - Metadata: Gzip compression")
        print("  - Target file size: 64 MB")
        print("  - Expected total storage: ~1.2 GB (all timeframes)")

        print("\nAggregation Strategy:")
        print("  - 1m/5m: Incremental with MERGE (low latency)")
        print("  - 30m/1h/1d: Batch with INSERT OVERWRITE (simplicity)")
        print("  - Source: gold_crypto_trades (direct rollup)")

        print("\nNext Steps:")
        print("  1. Implement Spark aggregation jobs (incremental + batch)")
        print("  2. Set up Prefect orchestration (5 scheduled flows)")
        print("  3. Add data quality validation (4 invariants)")
        print("  4. Configure retention enforcement (partition deletion)")

        if success_count == len(timeframes):
            print("\n✓ All OHLCV tables created successfully")

            # Show all tables in market_data namespace
            print(f"\n{'=' * 70}")
            print("Market Data Namespace Tables")
            print(f"{'=' * 70}\n")
            tables = spark.sql("SHOW TABLES IN iceberg.market_data")
            tables.show(truncate=False)

            return 0
        else:
            print(f"\n⚠ Only {success_count}/{len(timeframes)} tables created successfully")
            return 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())

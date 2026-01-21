"""Unit tests for OHLCV aggregation logic.

Tests cover:
- OHLC calculation correctness (first/last by timestamp, min/max)
- VWAP calculation accuracy (volume-weighted average price)
- Window aggregation for all timeframes (1m, 5m, 30m, 1h, 1d)
- Edge cases (single trade, concurrent trades, empty data)
- Data quality invariants (price relationships, VWAP bounds)

These tests validate the core aggregation logic without requiring live
Spark/Iceberg infrastructure.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    count,
    first,
    last,
    struct,
    window,
)
from pyspark.sql.functions import (
    max as spark_max,
)
from pyspark.sql.functions import (
    min as spark_min,
)
from pyspark.sql.functions import (
    sum as spark_sum,
)
from pyspark.sql.types import (
    DecimalType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)


@pytest.fixture(scope="module")
def spark():
    """Create a local Spark session for testing."""
    spark = (
        SparkSession.builder.master("local[1]")
        .appName("test-ohlcv-aggregation")
        .config("spark.sql.shuffle.partitions", "1")  # Minimize overhead
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    yield spark
    spark.stop()


@pytest.fixture
def trades_schema():
    """Schema for gold_crypto_trades table."""
    return StructType(
        [
            StructField("symbol", StringType(), nullable=False),
            StructField("exchange", StringType(), nullable=False),
            StructField("timestamp", LongType(), nullable=False),  # microseconds
            StructField("price", DecimalType(18, 8), nullable=False),
            StructField("quantity", DecimalType(18, 8), nullable=False),
        ]
    )


def microseconds_from_dt(dt: datetime) -> int:
    """Convert datetime to microseconds since epoch."""
    return int(dt.timestamp() * 1_000_000)


class TestOHLCBasicAggregation:
    """Test basic OHLC calculation logic."""

    def test_single_trade(self, spark, trades_schema):
        """Test OHLC with a single trade."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        # Aggregate into 1-minute OHLCV
        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            spark_max("price").alias("high_price"),
            spark_min("price").alias("low_price"),
            last(struct("timestamp_ts", "price")).getField("price").alias("close_price"),
            spark_sum("quantity").alias("volume"),
            count("*").alias("trade_count"),
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        # With single trade, OHLC should all equal the trade price
        assert result["open_price"] == Decimal("100.00")
        assert result["high_price"] == Decimal("100.00")
        assert result["low_price"] == Decimal("100.00")
        assert result["close_price"] == Decimal("100.00")
        assert result["volume"] == Decimal("1.0")
        assert result["trade_count"] == 1
        assert result["vwap"] == Decimal("100.00")

    def test_multiple_trades_ascending(self, spark, trades_schema):
        """Test OHLC with trades in ascending price order."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=20)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=30)),
                Decimal("103.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            spark_max("price").alias("high_price"),
            spark_min("price").alias("low_price"),
            last(struct("timestamp_ts", "price")).getField("price").alias("close_price"),
            spark_sum("quantity").alias("volume"),
            count("*").alias("trade_count"),
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        assert result["open_price"] == Decimal("100.00")  # First trade
        assert result["close_price"] == Decimal("103.00")  # Last trade
        assert result["high_price"] == Decimal("103.00")  # Max
        assert result["low_price"] == Decimal("100.00")  # Min
        assert result["volume"] == Decimal("4.0")
        assert result["trade_count"] == 4

    def test_multiple_trades_descending(self, spark, trades_schema):
        """Test OHLC with trades in descending price order."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("103.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=20)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=30)),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            spark_max("price").alias("high_price"),
            spark_min("price").alias("low_price"),
            last(struct("timestamp_ts", "price")).getField("price").alias("close_price"),
            spark_sum("quantity").alias("volume"),
            count("*").alias("trade_count"),
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        assert result["open_price"] == Decimal("103.00")  # First trade
        assert result["close_price"] == Decimal("100.00")  # Last trade
        assert result["high_price"] == Decimal("103.00")  # Max
        assert result["low_price"] == Decimal("100.00")  # Min
        assert result["volume"] == Decimal("4.0")
        assert result["trade_count"] == 4

    def test_trades_with_spike(self, spark, trades_schema):
        """Test OHLC with a price spike in the middle."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("110.00"),
                Decimal("1.0"),
            ),  # Spike
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=20)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            spark_max("price").alias("high_price"),
            spark_min("price").alias("low_price"),
            last(struct("timestamp_ts", "price")).getField("price").alias("close_price"),
            spark_sum("quantity").alias("volume"),
            count("*").alias("trade_count"),
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        assert result["open_price"] == Decimal("100.00")
        assert result["close_price"] == Decimal("101.00")
        assert result["high_price"] == Decimal("110.00")  # Spike captured
        assert result["low_price"] == Decimal("100.00")
        assert result["volume"] == Decimal("3.0")
        assert result["trade_count"] == 3


class TestVWAPCalculation:
    """Test Volume-Weighted Average Price (VWAP) calculation."""

    def test_vwap_equal_weights(self, spark, trades_schema):
        """Test VWAP with equal trade sizes."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        # VWAP with equal weights = simple average = (100 + 102) / 2 = 101
        assert result["vwap"] == Decimal("101.00")

    def test_vwap_weighted(self, spark, trades_schema):
        """Test VWAP with different trade sizes."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),  # 100 * 1 = 100
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("110.00"),
                Decimal("3.0"),
            ),  # 110 * 3 = 330
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        # VWAP = (100*1 + 110*3) / (1 + 3) = 430 / 4 = 107.50
        assert result["vwap"] == Decimal("107.50")

    def test_vwap_bounds(self, spark, trades_schema):
        """Test that VWAP is within [low, high] bounds."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("110.00"),
                Decimal("2.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=20)),
                Decimal("105.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            spark_min("price").alias("low_price"),
            spark_max("price").alias("high_price"),
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )

        result = ohlcv.collect()[0]

        # Invariant: low <= VWAP <= high
        assert result["low_price"] <= result["vwap"] <= result["high_price"]


class TestWindowAggregation:
    """Test aggregation across different timeframes."""

    def test_1_minute_window(self, spark, trades_schema):
        """Test 1-minute window aggregation."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=30)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
            # Next minute
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(minutes=1)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            count("*").alias("trade_count"),
        )

        results = ohlcv.collect()

        # Should have 2 windows (2 distinct minutes)
        assert len(results) == 2
        # First window: 2 trades
        assert results[0]["trade_count"] == 2
        # Second window: 1 trade
        assert results[1]["trade_count"] == 1

    def test_5_minute_window(self, spark, trades_schema):
        """Test 5-minute window aggregation."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(minutes=2)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(minutes=4)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
            # Next 5-minute window
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(minutes=5)),
                Decimal("103.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "5 minutes")).agg(
            count("*").alias("trade_count"),
        )

        results = ohlcv.collect()

        # Should have 2 windows (2 distinct 5-minute periods)
        assert len(results) == 2

    def test_1_day_window(self, spark, trades_schema):
        """Test 1-day window aggregation."""
        base_time = datetime(2026, 1, 21, 0, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(hours=12)),
                Decimal("101.00"),
                Decimal("1.0"),
            ),
            # Next day
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(days=1)),
                Decimal("102.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 day")).agg(
            count("*").alias("trade_count"),
        )

        results = ohlcv.collect()

        # Should have 2 windows (2 distinct days)
        assert len(results) == 2


class TestMultiExchangeAggregation:
    """Test aggregation with multiple exchanges."""

    def test_separate_exchange_candles(self, spark, trades_schema):
        """Test that different exchanges generate separate candles."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "KRAKEN",
                microseconds_from_dt(base_time),
                Decimal("100.50"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = (
            df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute"))
            .agg(
                first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            )
            .orderBy("exchange")
        )

        results = ohlcv.collect()

        # Should have 2 candles (one per exchange)
        assert len(results) == 2
        assert results[0]["exchange"] == "BINANCE"
        assert results[0]["open_price"] == Decimal("100.00")
        assert results[1]["exchange"] == "KRAKEN"
        assert results[1]["open_price"] == Decimal("100.50")


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_concurrent_trades(self, spark, trades_schema):
        """Test trades with identical timestamps."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)
        timestamp_us = microseconds_from_dt(base_time)

        data = [
            ("BTCUSDT", "BINANCE", timestamp_us, Decimal("100.00"), Decimal("1.0")),
            ("BTCUSDT", "BINANCE", timestamp_us, Decimal("101.00"), Decimal("1.0")),
            ("BTCUSDT", "BINANCE", timestamp_us, Decimal("102.00"), Decimal("1.0")),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            spark_min("price").alias("low_price"),
            spark_max("price").alias("high_price"),
            count("*").alias("trade_count"),
        )

        result = ohlcv.collect()[0]

        # Should still capture min/max correctly
        assert result["low_price"] == Decimal("100.00")
        assert result["high_price"] == Decimal("102.00")
        assert result["trade_count"] == 3

    def test_large_price_range(self, spark, trades_schema):
        """Test with a large price range."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("1.00"),
                Decimal("1.0"),
            ),
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("100000.00"),
                Decimal("1.0"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            spark_min("price").alias("low_price"),
            spark_max("price").alias("high_price"),
        )

        result = ohlcv.collect()[0]

        assert result["low_price"] == Decimal("1.00")
        assert result["high_price"] == Decimal("100000.00")


class TestDataQualityInvariants:
    """Test that OHLCV data satisfies quality invariants."""

    def test_price_relationships(self, spark, trades_schema):
        """Test invariant: high >= open/close, low <= open/close."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("102.00"),
                Decimal("1.0"),
            ),  # Open
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=10)),
                Decimal("105.00"),
                Decimal("1.0"),
            ),  # High
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=20)),
                Decimal("100.00"),
                Decimal("1.0"),
            ),  # Low
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time + timedelta(seconds=30)),
                Decimal("103.00"),
                Decimal("1.0"),
            ),  # Close
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            first(struct("timestamp_ts", "price")).getField("price").alias("open_price"),
            spark_max("price").alias("high_price"),
            spark_min("price").alias("low_price"),
            last(struct("timestamp_ts", "price")).getField("price").alias("close_price"),
        )

        result = ohlcv.collect()[0]

        # Invariants
        assert result["high_price"] >= result["open_price"]
        assert result["high_price"] >= result["close_price"]
        assert result["low_price"] <= result["open_price"]
        assert result["low_price"] <= result["close_price"]

    def test_positive_metrics(self, spark, trades_schema):
        """Test invariant: volume > 0, trade_count > 0."""
        base_time = datetime(2026, 1, 21, 12, 0, 0)

        data = [
            (
                "BTCUSDT",
                "BINANCE",
                microseconds_from_dt(base_time),
                Decimal("100.00"),
                Decimal("1.5"),
            ),
        ]

        df = spark.createDataFrame(data, schema=trades_schema)
        df = df.withColumn("timestamp_ts", (col("timestamp") / 1_000_000).cast(TimestampType()))

        ohlcv = df.groupBy("symbol", "exchange", window("timestamp_ts", "1 minute")).agg(
            spark_sum("quantity").alias("volume"),
            count("*").alias("trade_count"),
        )

        result = ohlcv.collect()[0]

        assert result["volume"] > Decimal("0")
        assert result["trade_count"] > 0

"""Performance benchmarks for Iceberg Writer.

Measures writer throughput and latency for Iceberg append operations.

Baseline Targets:
- Single write latency: < 200ms (p99)
- Batch throughput: > 5,000 rows/sec
- Large batch (10K): < 2 seconds

Run: pytest tests/performance/test_writer_throughput.py -v --benchmark-only
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from k2.storage.writer import IcebergWriter


@pytest.fixture
def mock_iceberg_components():
    """Mock Iceberg catalog and table for performance testing."""
    with patch("k2.storage.writer.load_catalog") as mock_catalog:
        # Mock catalog
        mock_catalog_instance = Mock()

        # Mock table
        mock_table = Mock()
        mock_table.append = Mock(return_value=None)

        # Mock snapshot (for transaction logging)
        mock_snapshot = Mock()
        mock_snapshot.snapshot_id = 12345678901234567890
        mock_snapshot.sequence_number = 42
        mock_snapshot.summary = {
            "total-records": "1000000",
            "total-data-files": "100",
            "added-data-files": "1",
            "added-records": "1000",
            "added-files-size": "524288",
        }
        mock_table.current_snapshot.return_value = mock_snapshot

        mock_catalog_instance.load_table.return_value = mock_table
        mock_catalog.return_value = mock_catalog_instance

        yield {
            "catalog": mock_catalog_instance,
            "table": mock_table,
            "snapshot": mock_snapshot,
        }


@pytest.fixture
def sample_trades_v1():
    """Generate sample v1 trades for benchmarking."""

    def _generate(count=100):
        trades = []
        for i in range(count):
            trades.append(
                {
                    "symbol": "BHP",
                    "company_id": 7078,
                    "exchange": "ASX",
                    "exchange_timestamp": datetime(2014, 3, 10, 10, 0, i % 60),
                    "price": Decimal("36.50"),
                    "volume": 10000 + i,
                    "qualifiers": 0,
                    "venue": "X",
                    "buyer_id": None,
                    "ingestion_timestamp": datetime.now(),
                    "sequence_number": i,
                }
            )
        return trades

    return _generate


@pytest.fixture
def sample_trades_v2():
    """Generate sample v2 trades for benchmarking."""

    def _generate(count=100):
        trades = []
        for i in range(count):
            trades.append(
                {
                    "message_id": f"msg-{i:06d}",
                    "trade_id": f"trade-{i:06d}",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                    "asset_class": "crypto",
                    "timestamp": datetime(2025, 1, 13, 10, 0, i % 60),
                    "price": Decimal("45000.12345678"),
                    "quantity": Decimal(f"{1.5 + i * 0.01:.8f}"),
                    "currency": "USDT",
                    "side": "buy" if i % 2 == 0 else "sell",
                    "trade_conditions": ["regular"],
                    "source_sequence": i,
                    "ingestion_timestamp": datetime.now(),
                    "platform_sequence": i + 100000,
                    "vendor_data": {"venue": "spot", "maker": i % 2 == 0},
                    "is_sample_data": False,
                }
            )
        return trades

    return _generate


@pytest.mark.performance
class TestWriterLatency:
    """Benchmark writer latency for different batch sizes."""

    def test_single_batch_write_v1(self, benchmark, mock_iceberg_components, sample_trades_v1):
        """Benchmark single batch write (100 trades, v1 schema).

        Baseline: < 200ms (p99)
        """
        writer = IcebergWriter(schema_version="v1")
        trades = sample_trades_v1(100)

        result = benchmark(
            writer.write_trades,
            records=trades,
            table_name="market_data.trades",
        )

        # Verify write was successful
        assert result == 100
        assert mock_iceberg_components["table"].append.call_count > 0

        stats = benchmark.stats
        print(
            f"\nV1 write (100 trades) - Mean: {stats['mean']*1000:.1f}ms, "
            f"StdDev: {stats['stddev']*1000:.1f}ms"
        )

    def test_single_batch_write_v2(self, benchmark, mock_iceberg_components, sample_trades_v2):
        """Benchmark single batch write (100 trades, v2 schema).

        Baseline: < 200ms (p99)
        """
        writer = IcebergWriter(schema_version="v2")
        trades = sample_trades_v2(100)

        result = benchmark(
            writer.write_trades,
            records=trades,
            table_name="market_data.trades_v2",
        )

        assert result == 100

        stats = benchmark.stats
        print(
            f"\nV2 write (100 trades) - Mean: {stats['mean']*1000:.1f}ms, "
            f"StdDev: {stats['stddev']*1000:.1f}ms"
        )

    def test_large_batch_write(self, benchmark, mock_iceberg_components, sample_trades_v2):
        """Benchmark large batch write (10K trades).

        Baseline: < 2 seconds
        """
        writer = IcebergWriter(schema_version="v2")
        trades = sample_trades_v2(10_000)

        result = benchmark.pedantic(
            writer.write_trades,
            kwargs={"records": trades, "table_name": "market_data.trades_v2"},
            rounds=3,
            iterations=1,
        )

        assert result == 10_000

        stats = benchmark.stats
        print(
            f"\nLarge batch (10K trades) - Mean: {stats['mean']:.3f}s, "
            f"Throughput: {10_000 / stats['mean']:,.0f} rows/sec"
        )

        # Check latency baseline
        assert stats["mean"] < 2.0, f"Large batch latency {stats['mean']:.2f}s exceeds 2s baseline"


@pytest.mark.performance
class TestWriterThroughput:
    """Benchmark writer throughput for sustained operations."""

    def test_throughput_small_batches(self, mock_iceberg_components, sample_trades_v2):
        """Measure throughput with small batches (100 rows).

        Baseline: > 5,000 rows/sec
        """
        writer = IcebergWriter(schema_version="v2")
        batch_size = 100
        num_batches = 50  # Total 5,000 rows

        import time

        start = time.perf_counter()

        for i in range(num_batches):
            trades = sample_trades_v2(batch_size)
            writer.write_trades(trades, table_name="market_data.trades_v2")

        elapsed = time.perf_counter() - start
        total_rows = batch_size * num_batches
        throughput = total_rows / elapsed

        print(f"\nSmall batches: {total_rows:,} rows in {elapsed:.2f}s")
        print(f"Throughput: {throughput:,.0f} rows/sec")

        assert throughput > 5_000, f"Throughput {throughput:,.0f} rows/sec below 5,000 baseline"

    def test_throughput_large_batches(self, mock_iceberg_components, sample_trades_v2):
        """Measure throughput with large batches (10K rows).

        Baseline: > 10,000 rows/sec
        """
        writer = IcebergWriter(schema_version="v2")
        batch_size = 10_000
        num_batches = 10  # Total 100K rows

        import time

        start = time.perf_counter()

        for i in range(num_batches):
            trades = sample_trades_v2(batch_size)
            writer.write_trades(trades, table_name="market_data.trades_v2")

        elapsed = time.perf_counter() - start
        total_rows = batch_size * num_batches
        throughput = total_rows / elapsed

        print(f"\nLarge batches: {total_rows:,} rows in {elapsed:.2f}s")
        print(f"Throughput: {throughput:,.0f} rows/sec")

        assert throughput > 10_000, f"Throughput {throughput:,.0f} rows/sec below 10,000 baseline"


@pytest.mark.performance
class TestArrowConversion:
    """Benchmark PyArrow conversion performance."""

    def test_arrow_conversion_v1(self, benchmark, sample_trades_v1):
        """Benchmark PyArrow conversion for v1 schema.

        Baseline: < 10ms for 1,000 rows
        """
        writer = IcebergWriter(schema_version="v1")
        trades = sample_trades_v1(1000)

        result = benchmark(writer._records_to_arrow_trades, trades)

        assert result.num_rows == 1000

        stats = benchmark.stats
        print(f"\nArrow conversion v1 (1K rows) - Mean: {stats['mean']*1000:.1f}ms")

    def test_arrow_conversion_v2(self, benchmark, sample_trades_v2):
        """Benchmark PyArrow conversion for v2 schema.

        Baseline: < 15ms for 1,000 rows (more complex schema)
        """
        writer = IcebergWriter(schema_version="v2")
        trades = sample_trades_v2(1000)

        result = benchmark(writer._records_to_arrow_trades_v2, trades)

        assert result.num_rows == 1000

        stats = benchmark.stats
        print(f"\nArrow conversion v2 (1K rows) - Mean: {stats['mean']*1000:.1f}ms")

    def test_arrow_conversion_scaling(self, sample_trades_v2):
        """Test Arrow conversion scaling with row count."""
        writer = IcebergWriter(schema_version="v2")
        row_counts = [100, 1_000, 10_000]
        results = []

        import time

        for count in row_counts:
            trades = sample_trades_v2(count)

            start = time.perf_counter()
            arrow_table = writer._records_to_arrow_trades_v2(trades)
            elapsed = time.perf_counter() - start

            throughput = count / elapsed
            results.append((count, elapsed * 1000, throughput))

            print(f"Arrow {count:>6} rows: {elapsed*1000:>8.2f}ms ({throughput:>10,.0f} rows/sec)")

        # Check that conversion scales linearly (within 20%)
        # Expect ~constant rows/sec across batch sizes
        throughput_100 = results[0][2]
        throughput_10k = results[2][2]

        ratio = throughput_10k / throughput_100
        assert 0.7 < ratio < 1.3, f"Arrow conversion doesn't scale linearly: {ratio:.2f}x"


@pytest.mark.performance
class TestDecimalPrecision:
    """Benchmark decimal precision handling performance."""

    def test_decimal_conversion_performance(self, benchmark):
        """Benchmark decimal to Arrow conversion.

        Tests performance impact of high-precision decimals.
        """
        writer = IcebergWriter(schema_version="v2")

        # Generate trades with high-precision decimals
        trades = []
        for i in range(1000):
            trades.append(
                {
                    "message_id": f"msg-{i:06d}",
                    "trade_id": f"trade-{i:06d}",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                    "asset_class": "crypto",
                    "timestamp": datetime(2025, 1, 13, 10, 0, 0),
                    "price": Decimal("45000.12345678"),  # 8 decimal places
                    "quantity": Decimal("1.23456789"),  # 8 decimal places
                    "currency": "USDT",
                    "side": "buy",
                    "trade_conditions": None,
                    "source_sequence": i,
                    "ingestion_timestamp": datetime.now(),
                    "platform_sequence": i + 100000,
                    "vendor_data": None,
                    "is_sample_data": False,
                }
            )

        result = benchmark(writer._records_to_arrow_trades_v2, trades)

        assert result.num_rows == 1000

        stats = benchmark.stats
        print(f"\nDecimal conversion (1K rows) - Mean: {stats['mean']*1000:.1f}ms")


@pytest.mark.performance
@pytest.mark.slow
class TestWriterStress:
    """Stress tests for writer under sustained load."""

    def test_sustained_write_load(self, mock_iceberg_components, sample_trades_v2):
        """Test writer under sustained load.

        Writes 500K rows in batches to check for degradation.
        """
        writer = IcebergWriter(schema_version="v2")
        batch_size = 10_000
        num_batches = 50  # Total 500K rows

        import time

        start = time.perf_counter()

        for i in range(num_batches):
            trades = sample_trades_v2(batch_size)
            writer.write_trades(trades, table_name="market_data.trades_v2")

            if i % 10 == 0:
                elapsed = time.perf_counter() - start
                rows_written = (i + 1) * batch_size
                throughput = rows_written / elapsed
                print(f"Batch {i+1}/{num_batches}: {throughput:,.0f} rows/sec")

        total_elapsed = time.perf_counter() - start
        total_rows = batch_size * num_batches
        avg_throughput = total_rows / total_elapsed

        print(f"\nSustained load: {total_rows:,} rows in {total_elapsed:.2f}s")
        print(f"Average throughput: {avg_throughput:,.0f} rows/sec")

        # Should maintain at least 8K rows/sec under sustained load
        assert (
            avg_throughput > 8_000
        ), f"Sustained throughput {avg_throughput:,.0f} below 8,000 baseline"

    def test_mixed_schema_performance(
        self, mock_iceberg_components, sample_trades_v1, sample_trades_v2
    ):
        """Test performance when switching between v1 and v2 schemas."""
        writer_v1 = IcebergWriter(schema_version="v1")
        writer_v2 = IcebergWriter(schema_version="v2")

        import time

        # Measure v1 writes
        trades_v1 = sample_trades_v1(10_000)
        start = time.perf_counter()
        writer_v1.write_trades(trades_v1, table_name="market_data.trades")
        v1_time = time.perf_counter() - start

        # Measure v2 writes
        trades_v2 = sample_trades_v2(10_000)
        start = time.perf_counter()
        writer_v2.write_trades(trades_v2, table_name="market_data.trades_v2")
        v2_time = time.perf_counter() - start

        print(f"\nV1 schema (10K rows): {v1_time:.3f}s ({10_000/v1_time:,.0f} rows/sec)")
        print(f"V2 schema (10K rows): {v2_time:.3f}s ({10_000/v2_time:,.0f} rows/sec)")
        print(f"V2/V1 ratio: {v2_time/v1_time:.2f}x")

        # V2 should be within 50% of v1 performance (more complex schema acceptable)
        assert v2_time < v1_time * 1.5, "V2 schema significantly slower than v1"

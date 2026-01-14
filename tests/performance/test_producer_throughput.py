"""Performance benchmarks for Kafka Producer.

Measures producer throughput and latency to detect performance regressions.

Baseline Targets (single producer):
- Throughput: > 10,000 messages/sec
- Latency p99: < 10ms
- Memory: < 100MB for 1M messages

Run: pytest tests/performance/test_producer_throughput.py -v --benchmark-only
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from k2.ingestion.producer import MarketDataProducer


@pytest.fixture
def mock_kafka_producer():
    """Mock Kafka producer to isolate benchmarking from network."""
    with patch("k2.ingestion.producer.Producer") as mock_prod, \
         patch("k2.ingestion.producer.SchemaRegistryClient") as mock_sr, \
         patch("k2.ingestion.producer.get_topic_builder") as mock_builder:

        # Configure mock producer
        mock_instance = Mock()
        mock_instance.produce = Mock(return_value=None)
        mock_instance.poll = Mock(return_value=0)
        mock_instance.flush = Mock(return_value=0)
        mock_prod.return_value = mock_instance

        # Configure mock schema registry
        mock_sr_instance = Mock()
        mock_serializer = Mock()
        mock_serializer.return_value = b"serialized_data"
        mock_sr_instance.get_latest_version = Mock(return_value=Mock(schema=Mock()))
        mock_sr.return_value = mock_sr_instance

        # Configure mock topic builder
        mock_topic_config = Mock()
        mock_topic_config.topic_name = "market.trades.crypto.binance"
        mock_topic_config.schema_subject = "market.trades.crypto.binance-value"
        mock_topic_config.partition_key_field = "symbol"  # Important: must be a string

        mock_builder_instance = Mock()
        mock_builder_instance.get_topic_config.return_value = mock_topic_config
        mock_builder.return_value = mock_builder_instance

        yield mock_instance


@pytest.fixture
def sample_trade_v2():
    """Sample v2 trade record for benchmarking."""
    return {
        "message_id": "msg-12345",
        "trade_id": "trade-67890",
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "asset_class": "crypto",
        "timestamp": datetime(2025, 1, 13, 10, 0, 0),
        "price": Decimal("45000.12345678"),
        "quantity": Decimal("1.5"),
        "currency": "USDT",
        "side": "buy",
        "trade_conditions": ["regular"],
        "source_sequence": 12345,
        "ingestion_timestamp": datetime.now(),
        "platform_sequence": 67890,
        "vendor_data": {"venue": "spot", "maker": True},
        "is_sample_data": False,
    }


@pytest.fixture
def sample_quote_v2():
    """Sample v2 quote record for benchmarking."""
    return {
        "message_id": "msg-54321",
        "quote_id": "quote-09876",
        "symbol": "ETHUSDT",
        "exchange": "binance",
        "asset_class": "crypto",
        "timestamp": datetime(2025, 1, 13, 10, 0, 0),
        "bid_price": Decimal("3000.12345678"),
        "bid_quantity": Decimal("2.5"),
        "ask_price": Decimal("3001.12345678"),
        "ask_quantity": Decimal("3.0"),
        "currency": "USDT",
        "source_sequence": 54321,
        "ingestion_timestamp": datetime.now(),
        "platform_sequence": 98765,
        "vendor_data": {"venue": "spot", "level": 1},
    }


@pytest.mark.performance
class TestProducerThroughput:
    """Benchmark producer throughput and latency."""

    def test_single_trade_latency(self, benchmark, mock_kafka_producer, sample_trade_v2):
        """Benchmark single trade produce latency.

        Baseline: < 1ms per message (p99)
        """
        producer = MarketDataProducer(schema_version="v2")

        result = benchmark(
            producer.produce_trade,
            asset_class="crypto",
            exchange="binance",
            record=sample_trade_v2,
        )

        # Verify produce was called
        assert mock_kafka_producer.produce.call_count > 0

        # Print stats for reference
        stats = benchmark.stats
        print(f"\nSingle trade latency - Mean: {stats['mean']*1000:.3f}ms, "
              f"StdDev: {stats['stddev']*1000:.3f}ms, "
              f"Min: {stats['min']*1000:.3f}ms, Max: {stats['max']*1000:.3f}ms")

    def test_single_quote_latency(self, benchmark, mock_kafka_producer, sample_quote_v2):
        """Benchmark single quote produce latency.

        Baseline: < 1ms per message (p99)
        """
        producer = MarketDataProducer(schema_version="v2")

        result = benchmark(
            producer.produce_quote,
            asset_class="crypto",
            exchange="binance",
            record=sample_quote_v2,
        )

        # Verify produce was called
        assert mock_kafka_producer.produce.call_count > 0

        stats = benchmark.stats
        print(f"\nSingle quote latency - Mean: {stats['mean']*1000:.3f}ms, "
              f"StdDev: {stats['stddev']*1000:.3f}ms")

    def test_batch_trade_throughput(self, benchmark, mock_kafka_producer, sample_trade_v2):
        """Benchmark batch trade produce throughput.

        Baseline: > 10,000 messages/sec
        """
        producer = MarketDataProducer(schema_version="v2")
        batch_size = 1000

        def produce_batch():
            for i in range(batch_size):
                # Vary message_id to simulate realistic load
                record = sample_trade_v2.copy()
                record["message_id"] = f"msg-{i:06d}"
                record["trade_id"] = f"trade-{i:06d}"
                producer.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=record,
                )

        result = benchmark(produce_batch)

        # Calculate throughput
        stats = benchmark.stats
        throughput = batch_size / stats['mean']
        print(f"\nBatch throughput - {throughput:,.0f} messages/sec "
              f"(batch_size={batch_size}, time={stats['mean']:.3f}s)")

        # Verify throughput meets baseline
        assert throughput > 10_000, \
            f"Throughput {throughput:,.0f} msg/sec below baseline 10,000 msg/sec"

    def test_batch_quote_throughput(self, benchmark, mock_kafka_producer, sample_quote_v2):
        """Benchmark batch quote produce throughput.

        Baseline: > 10,000 messages/sec
        """
        producer = MarketDataProducer(schema_version="v2")
        batch_size = 1000

        def produce_batch():
            for i in range(batch_size):
                record = sample_quote_v2.copy()
                record["message_id"] = f"msg-{i:06d}"
                record["quote_id"] = f"quote-{i:06d}"
                producer.produce_quote(
                    asset_class="crypto",
                    exchange="binance",
                    record=record,
                )

        result = benchmark(produce_batch)

        stats = benchmark.stats
        throughput = batch_size / stats['mean']
        print(f"\nBatch quote throughput - {throughput:,.0f} messages/sec")

        assert throughput > 10_000, \
            f"Throughput {throughput:,.0f} msg/sec below baseline"

    def test_producer_memory_usage(self, mock_kafka_producer, sample_trade_v2):
        """Measure producer memory footprint.

        Baseline: < 100MB for 10K messages
        Note: Memory measurement requires manual profiling with py-spy or memory_profiler
        """
        import sys

        producer = MarketDataProducer(schema_version="v2")

        # Produce messages without benchmark overhead
        for i in range(10_000):
            record = sample_trade_v2.copy()
            record["message_id"] = f"msg-{i:06d}"
            record["trade_id"] = f"trade-{i:06d}"
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=record,
            )

        # Get rough memory estimate (not precise, but indicative)
        # For precise measurement, use: pytest --memprof tests/performance/
        memory_mb = sys.getsizeof(producer) / (1024 * 1024)

        print(f"\nProducer object size: ~{memory_mb:.2f}MB (rough estimate)")
        print("For precise memory profiling, use: py-spy or memory_profiler")


@pytest.mark.performance
class TestProducerScaling:
    """Benchmark producer scaling characteristics."""

    def test_throughput_scaling(self, mock_kafka_producer, sample_trade_v2):
        """Test throughput scaling with batch size.

        Measures how throughput changes with batch size (100, 1K, 10K).
        """
        producer = MarketDataProducer(schema_version="v2")
        batch_sizes = [100, 1_000, 10_000]
        results = []

        for batch_size in batch_sizes:
            import time
            start = time.perf_counter()

            for i in range(batch_size):
                record = sample_trade_v2.copy()
                record["message_id"] = f"msg-{i:06d}"
                producer.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=record,
                )

            elapsed = time.perf_counter() - start
            throughput = batch_size / elapsed
            results.append((batch_size, throughput))

            print(f"Batch {batch_size:>6}: {throughput:>10,.0f} msg/sec")

        # Check that throughput doesn't degrade significantly with larger batches
        # (should scale linearly or better)
        throughput_100 = results[0][1]
        throughput_10k = results[2][1]

        # Allow 20% degradation at larger batch sizes
        assert throughput_10k > throughput_100 * 0.8, \
            "Throughput degraded >20% at larger batch sizes"

    def test_serialization_caching(self, mock_kafka_producer, sample_trade_v2):
        """Test that serializer caching improves performance.

        Second batch should be faster due to cached serializers.
        """
        producer = MarketDataProducer(schema_version="v2")
        batch_size = 1000

        import time

        # First batch (cold cache)
        start = time.perf_counter()
        for i in range(batch_size):
            record = sample_trade_v2.copy()
            record["message_id"] = f"msg-{i:06d}"
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=record,
            )
        first_batch_time = time.perf_counter() - start

        # Second batch (warm cache)
        start = time.perf_counter()
        for i in range(batch_size):
            record = sample_trade_v2.copy()
            record["message_id"] = f"msg-{i:06d}"
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=record,
            )
        second_batch_time = time.perf_counter() - start

        print(f"\nFirst batch (cold): {first_batch_time:.3f}s")
        print(f"Second batch (warm): {second_batch_time:.3f}s")
        print(f"Speedup: {first_batch_time / second_batch_time:.2f}x")

        # Second batch should be at least as fast (cache helps or no impact)
        assert second_batch_time <= first_batch_time * 1.1, \
            "Second batch unexpectedly slower (cache not helping)"


@pytest.mark.performance
@pytest.mark.slow
class TestProducerStress:
    """Stress tests for producer under load."""

    def test_sustained_load(self, mock_kafka_producer, sample_trade_v2):
        """Test producer under sustained load.

        Produces 100K messages to check for memory leaks or degradation.
        """
        producer = MarketDataProducer(schema_version="v2")
        message_count = 100_000

        import time
        start = time.perf_counter()

        for i in range(message_count):
            record = sample_trade_v2.copy()
            record["message_id"] = f"msg-{i:06d}"
            record["trade_id"] = f"trade-{i:06d}"
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=record,
            )

            # Simulate periodic flush
            if i % 10_000 == 0 and i > 0:
                producer.flush()

        elapsed = time.perf_counter() - start
        throughput = message_count / elapsed

        print(f"\nSustained load test: {message_count:,} messages in {elapsed:.2f}s")
        print(f"Average throughput: {throughput:,.0f} msg/sec")

        # Should maintain at least 5K msg/sec under sustained load
        assert throughput > 5_000, \
            f"Sustained throughput {throughput:,.0f} msg/sec below baseline 5,000"

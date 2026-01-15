"""
Chaos Tests for Kafka Resilience.

Tests producer and consumer behavior under Kafka failure scenarios:
- Broker failures (stop/pause)
- Network partitions
- Resource constraints
- Network latency

These tests validate the platform's resilience to Kafka-related faults.

Usage:
    pytest tests/chaos/test_kafka_chaos.py -v -m chaos
"""

import logging
import time
from decimal import Decimal
from datetime import datetime

import pytest
from confluent_kafka import Producer, Consumer, KafkaError
from confluent_kafka.admin import AdminClient

from k2.ingestion.producer import MarketDataProducer

logger = logging.getLogger(__name__)

# Test configuration
KAFKA_BOOTSTRAP = "localhost:9092"
SCHEMA_REGISTRY_URL = "http://localhost:8081"
KAFKA_TIMEOUT = 30.0

# Mark all tests in this file as chaos tests with 60s timeout
pytestmark = [pytest.mark.chaos, pytest.mark.timeout(60)]


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def producer_v2():
    """Create v2 producer for chaos testing with resource limits."""
    producer = MarketDataProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        schema_registry_url=SCHEMA_REGISTRY_URL,
        schema_version="v2",
        config_overrides={
            "queue.buffering.max.messages": 10000,  # Limit buffer
            "queue.buffering.max.kbytes": 10240,    # 10MB max
        }
    )
    yield producer
    producer.flush(timeout=10)
    producer.close()  # Explicit cleanup


@pytest.fixture
def sample_trade():
    """Sample trade for chaos testing."""
    return {
        "message_id": f"chaos-{int(time.time() * 1000)}",
        "trade_id": f"CHAOS-{int(time.time() * 1000)}",
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "timestamp": datetime.now(),
        "price": Decimal("45000.0"),
        "quantity": Decimal("1.0"),
        "currency": "USDT",
        "side": "BUY",
        "trade_conditions": [],
        "source_sequence": int(time.time() * 1000),
        "ingestion_timestamp": datetime.now(),
        "platform_sequence": None,
        "vendor_data": None,
        "is_sample_data": True,
    }


# ==============================================================================
# Kafka Broker Failure Tests
# ==============================================================================


@pytest.mark.chaos_kafka
class TestKafkaBrokerFailure:
    """Test producer/consumer resilience to Kafka broker failures."""

    def test_producer_survives_brief_kafka_outage(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_failure,
    ):
        """Producer should buffer messages during brief Kafka outage."""
        # Produce a message successfully first
        producer_v2.produce_trade(
            asset_class="crypto",
            exchange="binance",
            record=sample_trade,
        )
        assert producer_v2.flush(timeout=KAFKA_TIMEOUT) == 0

        # Simulate Kafka failure for 5 seconds
        with chaos_kafka_failure(duration_seconds=5, mode="pause"):
            logger.info("Kafka is down - attempting to produce")

            # Try to produce during outage
            sample_trade["message_id"] = f"chaos-outage-{int(time.time() * 1000)}"
            producer_v2.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=sample_trade,
            )

            # Producer should buffer the message
            # Don't flush yet - Kafka is down

        # Kafka is back up - flush should succeed
        logger.info("Kafka restored - flushing buffered messages")
        remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)

        # All messages should be delivered after recovery
        assert remaining == 0, "Producer should deliver buffered messages after Kafka recovery"

    def test_producer_handles_kafka_stop(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_failure,
    ):
        """Producer should handle graceful Kafka shutdown."""
        # Simulate Kafka stop (graceful shutdown)
        with chaos_kafka_failure(duration_seconds=3, mode="stop"):
            logger.info("Kafka stopped - attempting to produce")

            # Try to produce during outage
            sample_trade["message_id"] = f"chaos-stop-{int(time.time() * 1000)}"

            try:
                producer_v2.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=sample_trade,
                )
            except Exception as e:
                # Producer may raise exception or buffer
                logger.info(f"Producer behavior during Kafka stop: {e}")

        # Kafka is back - verify recovery
        logger.info("Kafka restarted - testing recovery")
        sample_trade["message_id"] = f"chaos-recovery-{int(time.time() * 1000)}"
        producer_v2.produce_trade(
            asset_class="crypto",
            exchange="binance",
            record=sample_trade,
        )

        remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)
        assert remaining == 0, "Producer should recover after Kafka restart"

    @pytest.mark.skip(reason="Consumer fixture needs refactoring for chaos tests")
    def test_consumer_handles_kafka_pause(
        self,
        chaos_kafka_failure,
    ):
        """Consumer should handle Kafka pause gracefully."""
        consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": f"chaos-test-{int(time.time())}",
            "auto.offset.reset": "latest",
        })

        consumer.subscribe(["market.trades.crypto.binance"])

        try:
            # Poll successfully first
            msg = consumer.poll(timeout=1.0)
            logger.info(f"Initial poll result: {msg}")

            # Simulate Kafka pause
            with chaos_kafka_failure(duration_seconds=3, mode="pause"):
                logger.info("Kafka paused - polling should timeout")

                msg = consumer.poll(timeout=2.0)
                # Should timeout or return None
                assert msg is None or msg.error() is not None

            # Kafka restored - polling should work
            logger.info("Kafka restored - polling should succeed")
            # Give consumer time to reconnect
            time.sleep(2)

            msg = consumer.poll(timeout=5.0)
            # Should either get message or None (no new messages)
            # Should NOT get fatal error
            if msg and msg.error():
                assert not msg.error().fatal(), "Consumer should recover without fatal error"

        finally:
            consumer.close()


# ==============================================================================
# Network Partition Tests
# ==============================================================================


@pytest.mark.chaos_network
class TestKafkaNetworkPartition:
    """Test resilience to network partitions."""

    @pytest.mark.skip(reason="Network partition requires privileged container access")
    def test_producer_handles_network_partition(
        self,
        producer_v2,
        sample_trade,
        chaos_network_partition,
    ):
        """Producer should handle network partition gracefully."""
        # Produce successfully first
        producer_v2.produce_trade(
            asset_class="crypto",
            exchange="binance",
            record=sample_trade,
        )
        assert producer_v2.flush(timeout=KAFKA_TIMEOUT) == 0

        # Simulate network partition
        with chaos_network_partition(duration_seconds=5):
            logger.info("Network partitioned - Kafka unreachable")

            # Try to produce during partition
            sample_trade["message_id"] = f"chaos-partition-{int(time.time() * 1000)}"

            try:
                producer_v2.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=sample_trade,
                )

                # Flush will timeout or fail
                remaining = producer_v2.flush(timeout=5.0)
                logger.info(f"Flush during partition returned: {remaining}")

            except Exception as e:
                logger.info(f"Expected exception during partition: {e}")

        # Network restored
        logger.info("Network restored - verifying recovery")
        sample_trade["message_id"] = f"chaos-recovery-{int(time.time() * 1000)}"
        producer_v2.produce_trade(
            asset_class="crypto",
            exchange="binance",
            record=sample_trade,
        )

        remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)
        assert remaining == 0, "Producer should recover after network restoration"


# ==============================================================================
# Resource Constraint Tests
# ==============================================================================


@pytest.mark.chaos_kafka
class TestKafkaResourceConstraints:
    """Test behavior under resource constraints."""

    @pytest.mark.slow
    def test_producer_under_cpu_constraint(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_resource_limit,
    ):
        """Producer should handle Kafka under CPU pressure."""
        # Apply CPU constraint (50% of 1 core)
        with chaos_kafka_resource_limit(cpu_quota=50000):
            logger.info("Kafka CPU limited to 50% - testing producer throughput")

            start_time = time.time()
            message_count = 50

            for i in range(message_count):
                sample_trade["message_id"] = f"chaos-cpu-{int(time.time() * 1000)}-{i}"
                producer_v2.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=sample_trade,
                )

            remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)
            elapsed = time.time() - start_time

            # Should still succeed, just slower
            assert remaining == 0, "All messages should be delivered despite CPU constraint"

            throughput = message_count / elapsed
            logger.info(
                f"Throughput under CPU constraint: {throughput:.2f} msg/sec",
                messages=message_count,
                elapsed=elapsed,
            )

            # Throughput will be lower but should still be reasonable
            assert throughput > 10, "Should achieve at least 10 msg/sec under CPU pressure"

    @pytest.mark.slow
    def test_producer_under_memory_constraint(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_resource_limit,
    ):
        """Producer should handle Kafka under memory pressure."""
        # Apply memory constraint (512MB)
        with chaos_kafka_resource_limit(mem_limit="512m"):
            logger.info("Kafka memory limited to 512MB - testing producer")

            message_count = 20

            for i in range(message_count):
                sample_trade["message_id"] = f"chaos-mem-{int(time.time() * 1000)}-{i}"
                producer_v2.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=sample_trade,
                )

            remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)

            # Should succeed even with memory constraints
            assert remaining == 0, "All messages should be delivered despite memory constraint"


# ==============================================================================
# Latency Injection Tests
# ==============================================================================


@pytest.mark.chaos_network
@pytest.mark.skip(reason="Latency injection requires tc (traffic control) in container")
class TestKafkaLatency:
    """Test behavior under network latency."""

    def test_producer_under_high_latency(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_latency,
    ):
        """Producer should handle high network latency."""
        # Inject 200ms latency
        with chaos_kafka_latency(latency_ms=200, jitter_ms=50):
            logger.info("Kafka latency injected (200ms ± 50ms)")

            start_time = time.time()
            message_count = 10

            for i in range(message_count):
                sample_trade["message_id"] = f"chaos-latency-{int(time.time() * 1000)}-{i}"
                producer_v2.produce_trade(
                    asset_class="crypto",
                    exchange="binance",
                    record=sample_trade,
                )

            remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)
            elapsed = time.time() - start_time

            # Should succeed, but take longer
            assert remaining == 0, "All messages should be delivered despite latency"

            logger.info(
                f"Latency test completed",
                messages=message_count,
                elapsed=elapsed,
                latency_impact=f"{elapsed / message_count:.3f}s per message",
            )

            # Each message should take at least the latency time
            assert elapsed > message_count * 0.2, "Latency should be observable"


# ==============================================================================
# Concurrent Failure Tests
# ==============================================================================


@pytest.mark.chaos_kafka
class TestConcurrentFailures:
    """Test resilience to multiple simultaneous failures."""

    @pytest.mark.skip(reason="Requires coordinated multi-service chaos injection")
    def test_kafka_and_schema_registry_failure(
        self,
        producer_v2,
        sample_trade,
        chaos_kafka_failure,
        chaos_schema_registry_failure,
    ):
        """Producer should handle simultaneous Kafka and Schema Registry failure."""
        # Simulate both services failing
        with chaos_kafka_failure(duration_seconds=3, mode="pause"):
            with chaos_schema_registry_failure(duration_seconds=3, mode="pause"):
                logger.info("Both Kafka and Schema Registry down")

                # Try to produce - should fail gracefully
                try:
                    producer_v2.produce_trade(
                        asset_class="crypto",
                        exchange="binance",
                        record=sample_trade,
                    )
                except Exception as e:
                    logger.info(f"Expected failure with both services down: {e}")

        # Both services restored
        logger.info("Services restored - testing recovery")
        sample_trade["message_id"] = f"chaos-recovery-{int(time.time() * 1000)}"
        producer_v2.produce_trade(
            asset_class="crypto",
            exchange="binance",
            record=sample_trade,
        )

        remaining = producer_v2.flush(timeout=KAFKA_TIMEOUT)
        assert remaining == 0, "Producer should recover after both services restored"

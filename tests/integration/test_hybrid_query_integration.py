"""Hybrid Query Integration Tests.

Tests hybrid query functionality merging real-time and historical data:
Kafka Tail (real-time) + Iceberg (historical)

Validates:
- Automatic source selection based on time windows
- Deduplication across data sources
- Seamless query interface
- Performance characteristics

Uses V2 schema only for consistency.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from k2.ingestion.message_builders import build_trade_v2
from k2.ingestion.producer import MarketDataProducer
from k2.query.engine import QueryEngine
from k2.query.hybrid_engine import HybridQueryEngine
from k2.query.kafka_tail import KafkaTail


class TestHybridQueryIntegration:
    """Test hybrid query functionality."""

    @pytest.mark.integration
    def test_hybrid_query_time_window_selection(
        self, kafka_cluster, minio_backend, iceberg_config, sample_market_data
    ):
        """Test automatic source selection based on query time windows."""

        # Setup hybrid engine
        query_engine = QueryEngine()
        kafka_tail = KafkaTail()

        hybrid_engine = HybridQueryEngine(
            iceberg_engine=query_engine,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,  # 2-minute commit lag
        )

        test_trades = sample_market_data["trades"][:10]

        # Test 1: Real-time query (last 2 minutes)
        recent_trades = hybrid_engine.query_trades(
            symbol=test_trades[0]["symbol"],
            exchange="binance",
            start_time=datetime.now() - timedelta(minutes=2),
            end_time=datetime.now(),
        )

        # Should return data from Kafka tail (real-time)
        assert len(recent_trades) > 0, "Real-time query should return trades"
        print(f"✅ Real-time query: {len(recent_trades)} trades from last 2 minutes")

        # Test 2: Recent query (last 15 minutes)
        # Should return both Kafka + Iceberg data
        recent_15min_trades = hybrid_engine.query_trades(
            symbol=test_trades[0]["symbol"],
            exchange="binance",
            start_time=datetime.now() - timedelta(minutes=15),
            end_time=datetime.now(),
        )

        # Should have more trades than real-time only
        assert len(recent_15min_trades) > len(
            recent_trades
        ), "15-min query should include Kafka + Iceberg data"
        print(f"✅ Recent query: {len(recent_15min_trades)} trades from last 15 minutes")

        # Test 3: Historical query (more than 15 minutes ago)
        # Should return only Iceberg data
        historical_trades = hybrid_engine.query_trades(
            symbol=test_trades[0]["symbol"],
            exchange="binance",
            start_time=datetime.now() - timedelta(hours=2),
            end_time=datetime.now() - timedelta(minutes=16),  # More than 15 min ago
        )

        # Should have different data than recent queries
        assert (
            set(historical_trades) & set(recent_15min_trades) == set()
        ), "Historical query should be different from recent data"
        print(f"✅ Historical query: {len(historical_trades)} trades from more than 15 minutes ago")

        # Test 4: Very recent query (last 1 minute)
        # Should be almost all Kafka tail data
        very_recent_trades = hybrid_engine.query_trades(
            symbol=test_trades[0]["symbol"],
            exchange="binance",
            start_time=datetime.now() - timedelta(minutes=1),
            end_time=datetime.now(),
        )

        # Should have most recent data from Kafka tail
        assert len(very_recent_trades) >= len(
            recent_trades
        ), "Very recent query should include most recent Kafka data"
        print(f"✅ Very recent query: {len(very_recent_trades)} trades from last 1 minute")

    @pytest.mark.integration
    def test_hybrid_query_deduplication(self, kafka_cluster, sample_market_data):
        """Test deduplication across Kafka and Iceberg data sources."""

        # Setup components
        query_engine = QueryEngine()
        kafka_tail = KafkaTail()

        hybrid_engine = HybridQueryEngine(
            iceberg_engine=query_engine, kafka_tail=kafka_tail, commit_lag_seconds=120
        )

        # Create test data with overlapping message_id
        test_trades = []
        base_time = datetime.now() - timedelta(minutes=5)

        for i in range(5):
            v2_trade = build_trade_v2(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=base_time + timedelta(minutes=i),
                price=Decimal("50000.00"),
                quantity=1000,
                trade_id=f"DEDUP-TEST-{i:03d}",
            )
            test_trades.append(v2_trade)

        # Produce trades to Kafka (simulating overlapping data)
        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        for trade in test_trades:
            producer.produce_trade(asset_class="crypto", exchange="binance", record=trade)

        producer.flush(timeout=10)
        producer.close()

        # Query overlapping time window
        overlapping_trades = hybrid_engine.query_trades(
            symbol="BTCUSDT", exchange="binance", start_time=base_time, end_time=datetime.now()
        )

        # Should deduplicate by message_id
        unique_message_ids = set(trade["trade_id"] for trade in overlapping_trades)
        assert len(overlapping_trades) == len(
            unique_message_ids
        ), "Deduplication by message_id should work"
        print(
            f"✅ Deduplication test: {len(overlapping_trades)} unique trades from {len(test_trades)} produced"
        )

    @pytest.mark.integration
    def test_hybrid_query_performance_characteristics(self, kafka_cluster, sample_market_data):
        """Test performance characteristics of hybrid queries."""

        import time

        query_engine = QueryEngine()
        kafka_tail = KafkaTail()

        hybrid_engine = HybridQueryEngine(
            iceberg_engine=query_engine, kafka_tail=kafka_tail, commit_lag_seconds=120
        )

        # Test different query types and measure performance
        test_cases = [
            ("Real-time (2 min)", lambda: datetime.now() - timedelta(minutes=2)),
            ("Recent (15 min)", lambda: datetime.now() - timedelta(minutes=15)),
            ("Historical (2 hours)", lambda: datetime.now() - timedelta(hours=2)),
        ]

        for test_name, time_func in test_cases:
            start_time = time.perf_counter()

            # Execute query
            trades = hybrid_engine.query_trades(
                symbol="BTCUSDT",
                exchange="binance",
                start_time=time_func(),
                end_time=datetime.now(),
            )

            query_time = time.perf_counter() - start_time

            # Performance assertions
            assert (
                query_time < 1.0
            ), f"{test_name} query should complete in <1s, took {query_time:.3f}s"
            assert len(trades) > 0, f"{test_name} query should return data"

            print(f"✅ {test_name} query: {len(trades)} trades in {query_time:.3f}s")

        print("✅ All hybrid query performance tests completed successfully")

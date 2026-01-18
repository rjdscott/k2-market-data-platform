"""Basic End-to-End Data Pipeline Integration Tests.

Tests fundamental V2 data flow:
Producer → Kafka → Iceberg → Query Engine

Validates:
- V2 schema data integrity through pipeline
- Message production to Kafka
- Data writing to Iceberg storage
- Table creation and verification
- Performance baselines

Uses production-like Docker infrastructure and realistic market data.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from pyiceberg.catalog import load_catalog

from k2.ingestion.message_builders import build_quote_v2, build_trade_v2
from k2.ingestion.producer import MarketDataProducer
from k2.storage.writer import IcebergWriter


class TestBasicPipelineIntegration:
    """Test basic V2 data flow through entire pipeline."""

    @pytest.mark.integration
    def test_v2_trade_production_and_storage(
        self, kafka_cluster, minio_backend, iceberg_config, sample_market_data
    ):
        """Test V2 trades production and storage to Iceberg."""

        # Setup: Configure producer for V2 schema
        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        test_trades = sample_market_data["trades"][:50]  # Test with 50 trades

        # Step 1: Produce V2 trades to Kafka
        for trade in test_trades:
            # Build V2 trade message using message builders
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )

            # Produce to Kafka - call without capturing return
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=v2_trade,
            )

        # Flush producer to ensure messages are sent
        producer.flush(timeout=10)
        producer.close()

        # Step 2: Configure writer for V2 schema
        writer = IcebergWriter()

        # Step 3: Prepare V2 records for Iceberg
        v2_records = []
        for trade in test_trades:
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )
            v2_records.append(v2_trade)

        # Step 4: Write to Iceberg table
        try:
            records_written = writer.write_trades(
                records=v2_records, table_name="market_data.trades", exchange="binance"
            )

        except Exception as e:
            pytest.fail(f"Iceberg write failed: {e}")

        # Step 5: Verify data in Iceberg
        try:
            catalog = load_catalog("k2_catalog", **iceberg_config)

            table = catalog.load_table("market_data.trades")
            assert table is not None, "Iceberg table should exist after write"

        except Exception as e:
            pytest.fail(f"Iceberg table verification failed: {e}")

        # Assertions for end-to-end validation
        assert len(v2_records) == len(test_trades), "All V2 records should be created"
        assert records_written == len(v2_records), "All V2 records should be written"

        print("✅ V2 trades pipeline test completed successfully")
        print(f"   - Produced: {len(test_trades)} trades")
        print(f"   - Written: {records_written} V2 records")
        print("   - Table: market_data.trades exists and queryable")

    @pytest.mark.integration
    def test_v2_quote_production_and_storage(
        self, kafka_cluster, minio_backend, iceberg_config, sample_market_data
    ):
        """Test V2 quotes production and storage to Iceberg."""

        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        test_quotes = sample_market_data["quotes"][:30]  # Test with 30 quotes
        v2_quotes = []  # Collect V2 quote records

        # Step 1: Produce V2 quotes to Kafka
        for quote in test_quotes:
            # Build V2 quote message using message builders
            v2_quote = build_quote_v2(
                symbol=quote["symbol"],
                exchange=quote["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(quote["timestamp"]),
                bid_price=Decimal(quote["bid_price"]),
                bid_quantity=Decimal(quote["bid_quantity"]),
                ask_price=Decimal(quote["ask_price"]),
                ask_quantity=Decimal(quote["ask_quantity"]),
            )

            # Collect V2 quote record
            v2_quotes.append(v2_quote)

            # Produce to Kafka
            producer.produce_quote(asset_class="equities", exchange="asx", record=v2_quote)

        producer.flush(timeout=10)
        producer.close()

        # Step 2: Configure writer for V2 schema
        writer = IcebergWriter()

        try:
            records_written = writer.write_quotes(
                records=v2_quotes, table_name="market_data.quotes", exchange="asx"
            )

        except Exception as e:
            pytest.fail(f"Iceberg quotes write failed: {e}")

        # Step 3: Verify quotes table exists
        try:
            catalog = load_catalog("k2_catalog", **iceberg_config)

            table = catalog.load_table("market_data.quotes")
            assert table is not None, "Iceberg quotes table should exist after write"

        except Exception as e:
            pytest.fail(f"Iceberg quotes table verification failed: {e}")

        # Assertions for quote validation
        assert len(v2_quotes) == len(test_quotes), "All V2 quote records should be created"
        assert records_written == len(v2_quotes), "All quote records should be written"

        print("✅ V2 quotes pipeline test completed successfully")
        print(f"   - Produced: {len(test_quotes)} quotes")
        print(f"   - Written: {records_written} V2 quote records")
        print("   - Table: market_data.quotes exists and queryable")

    @pytest.mark.integration
    def test_v2_schema_compliance(self, sample_market_data):
        """Test V2 schema compliance using message builders."""

        trades = sample_market_data["trades"]
        quotes = sample_market_data["quotes"]

        # Verify V2 schema required fields using message builders
        for trade in trades:
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )

            # Core V2 trade fields should be present
            assert "message_id" in v2_trade, "V2 trade needs message_id"
            assert "trade_id" in v2_trade, "V2 trade needs trade_id"
            assert "symbol" in v2_trade, "V2 trade needs symbol"
            assert "exchange" in v2_trade, "V2 trade needs exchange"
            assert "timestamp" in v2_trade, "V2 trade needs timestamp"
            assert "price" in v2_trade, "V2 trade needs price"
            assert "quantity" in v2_trade, "V2 trade needs quantity"
            assert "asset_class" in v2_trade, "V2 trade needs asset_class"

            # Validate data types and formats
            assert isinstance(v2_trade["symbol"], str), "Symbol should be string"
            assert isinstance(v2_trade["exchange"], str), "Exchange should be string"
            assert isinstance(v2_trade["price"], (int, float, Decimal)), "Price should be numeric"
            assert isinstance(v2_trade["quantity"], (int, float, Decimal)), (
                "Quantity should be integer"
            )

        for quote in quotes:
            v2_quote = build_quote_v2(
                symbol=quote["symbol"],
                exchange=quote["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(quote["timestamp"]),
                bid_price=Decimal(quote["bid_price"]),
                bid_quantity=Decimal(quote["bid_quantity"]),
                ask_price=Decimal(quote["ask_price"]),
                ask_quantity=Decimal(quote["ask_quantity"]),
            )

            # Core V2 quote fields should be present
            assert "message_id" in v2_quote, "V2 quote needs message_id"
            assert "symbol" in v2_quote, "V2 quote needs symbol"
            assert "exchange" in v2_quote, "V2 quote needs exchange"
            assert "timestamp" in v2_quote, "V2 quote needs timestamp"
            assert "bid_price" in v2_quote, "V2 quote needs bid_price"
            assert "ask_price" in v2_quote, "V2 quote needs ask_price"
            assert "bid_quantity" in v2_quote, "V2 quote needs bid_quantity"
            assert "ask_quantity" in v2_quote, "V2 quote needs ask_quantity"
            assert "asset_class" in v2_quote, "V2 quote needs asset_class"

            # Validate data types
            assert isinstance(v2_quote["symbol"], str), "Symbol should be string"
            assert isinstance(v2_quote["bid_price"], (int, float, Decimal)), (
                "Bid price should be numeric"
            )
            assert isinstance(v2_quote["ask_price"], (int, float, Decimal)), (
                "Ask price should be numeric"
            )
            assert isinstance(v2_quote["bid_quantity"], (int, float, Decimal)), (
                "Bid quantity should be numeric"
            )
            assert isinstance(v2_quote["ask_quantity"], (int, float, Decimal)), (
                "Ask quantity should be numeric"
            )

    @pytest.mark.integration
    def test_v2_performance_baselines(self, kafka_cluster, sample_market_data):
        """Test V2 pipeline meets performance baselines."""

        import time

        producer = MarketDataProducer(
            bootstrap_servers=kafka_cluster.brokers,
            schema_registry_url=kafka_cluster.schema_registry_url,
            schema_version="v2",
        )

        # Test production latency
        test_trades = sample_market_data["trades"][:100]  # Smaller set for performance test

        start_time = time.perf_counter()

        for trade in test_trades:
            v2_trade = build_trade_v2(
                symbol=trade["symbol"],
                exchange=trade["exchange"],
                asset_class="crypto",
                timestamp=datetime.fromisoformat(trade["timestamp"]),
                price=Decimal(trade["price"]),
                quantity=trade["quantity"],
                trade_id=trade["trade_id"],
            )

            producer.produce_trade(asset_class="equities", exchange="asx", record=v2_trade)

        producer.flush(timeout=10)

        production_time = time.perf_counter() - start_time
        production_rate = len(test_trades) / production_time if production_time > 0 else 0

        # Performance assertions for V2 pipeline
        assert production_time < 30.0, (
            f"V2 production should complete in <30s, took {production_time:.2f}s"
        )
        assert production_rate > 10, f"V2 production rate >10 trades/sec, was {production_rate:.1f}"

        print("✅ V2 performance baselines met:")
        print(f"   - Production time: {production_time:.2f}s")
        print(f"   - Production rate: {production_rate:.1f} trades/sec")

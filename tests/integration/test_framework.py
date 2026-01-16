"""
Integration tests for K2 Market Data Platform - Basic functionality.

Tests the core testing framework components:
- Fixture functionality
- Market data generation
- Basic test execution
"""

import pytest


class TestFrameworkIntegration:
    """Test the testing framework itself works correctly."""

    def test_sample_data_fixture(self, sample_market_data):
        """Test sample market data fixture works."""
        assert "trades" in sample_market_data
        assert "quotes" in sample_market_data
        assert "symbols" in sample_market_data

        # Verify data structure
        assert len(sample_market_data["trades"]) > 0
        assert len(sample_market_data["quotes"]) > 0
        assert len(sample_market_data["symbols"]) > 0

        # Verify trade data integrity
        trade = sample_market_data["trades"][0]
        required_fields = ["symbol", "exchange", "timestamp", "price", "quantity", "trade_id"]
        for field in required_fields:
            assert field in trade

    def test_single_trade_fixture(self, single_trade):
        """Test single trade fixture."""
        assert single_trade["symbol"] is not None
        assert single_trade["price"] is not None
        assert single_trade["quantity"] > 0
        assert single_trade["trade_id"] is not None

    def test_single_quote_fixture(self, single_quote):
        """Test single quote fixture."""
        assert single_quote["symbol"] is not None
        assert single_quote["bid_price"] is not None
        assert single_quote["ask_price"] is not None
        assert single_quote["bid_size"] > 0
        assert single_quote["ask_size"] > 0
        # Ensure bid < ask
        assert float(single_quote["bid_price"]) < float(single_quote["ask_price"])

    def test_mixed_market_data(self, mixed_market_data):
        """Test mixed market data fixture."""
        assert mixed_market_data["total_trades"] > 0
        assert mixed_market_data["total_quotes"] > 0
        assert len(mixed_market_data["symbols"]) > 0

    def test_duckdb_connection(self, duckdb_connection):
        """Test DuckDB connection fixture."""
        # Should be able to execute simple queries
        result = duckdb_connection.execute("SELECT 1 as test_value").fetchall()
        assert result == [(1,)]

        # Should be able to create tables
        duckdb_connection.execute("CREATE TABLE test_table (id INTEGER, name VARCHAR)")
        duckdb_connection.execute("INSERT INTO test_table VALUES (1, 'test')")

        # Should be able to query back
        result = duckdb_connection.execute("SELECT * FROM test_table").fetchall()
        assert result == [(1, "test")]

    def test_configuration_fixtures(self, kafka_producer_config, kafka_consumer_config):
        """Test Kafka configuration fixtures."""
        assert kafka_producer_config["bootstrap.servers"] is not None
        assert kafka_producer_config["schema.registry.url"] is not None

        assert kafka_consumer_config["bootstrap.servers"] is not None
        assert kafka_consumer_config["schema.registry.url"] is not None
        assert kafka_consumer_config["group.id"] is not None

    def test_iceberg_config(self, iceberg_config):
        """Test Iceberg configuration fixture."""
        assert iceberg_config["warehouse"] is not None
        assert iceberg_config["catalog.backend"] is not None
        assert iceberg_config["s3.endpoint"] is not None

    def test_mock_external_apis(self, mock_external_apis):
        """Test external API mocking fixture."""
        assert "binance" in mock_external_apis

        # Test mock returns expected response
        mock_binance = mock_external_apis["binance"]
        response = mock_binance.return_value.json.return_value
        assert len(response) > 0
        assert response[0]["symbol"] == "BTCUSDT"

    def test_market_data_factory(self, market_data_factory):
        """Test market data factory fixture."""
        # Test creating custom data
        trade = market_data_factory.create_trade(symbol="TEST", quantity=100)
        assert trade["symbol"] == "TEST"
        assert trade["quantity"] == 100

        quote = market_data_factory.create_quote(symbol="TEST", bid_size=500)
        assert quote["symbol"] == "TEST"
        assert quote["bid_size"] == 500

        reference = market_data_factory.create_reference(symbol="TEST", sector="Technology")
        assert reference["symbol"] == "TEST"
        assert reference["sector"] == "Technology"

    def test_market_scenarios(self, market_scenario_factory):
        """Test market scenario factory fixture."""
        # Test market opening scenario
        opening_data = market_scenario_factory.create_market_opening()
        assert opening_data["scenario"] == "market_opening"
        assert "trades" in opening_data
        assert "quotes" in opening_data
        assert opening_data["characteristics"]["volume"] == "high"

        # Test volatile session scenario
        volatile_data = market_scenario_factory.create_volatile_session()
        assert volatile_data["scenario"] == "volatile_session"
        assert volatile_data["characteristics"]["volatility"] == "high"

        # Test low volume scenario
        low_volume_data = market_scenario_factory.create_low_volume_session()
        assert low_volume_data["scenario"] == "low_volume_session"
        assert low_volume_data["characteristics"]["volume"] == "low"

    @pytest.mark.parametrize("test_data_type", ["trades", "quotes", "reference"])
    def test_data_quality_helpers(self, test_data_type):
        """Test data quality assertion helpers."""
        from tests.conftest import assert_data_quality

        if test_data_type == "trades":
            test_data = [
                {
                    "symbol": "AAPL",
                    "timestamp": "2024-01-01T10:00:00",
                    "price": "150.00",
                    "quantity": 100,
                    "trade_id": "TRD-123",
                }
            ]
        elif test_data_type == "quotes":
            test_data = [
                {
                    "symbol": "AAPL",
                    "timestamp": "2024-01-01T10:00:00",
                    "bid_price": "149.99",
                    "ask_price": "150.01",
                }
            ]
        else:  # reference
            test_data = [{"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}]

        # Should not raise an assertion error
        assert_data_quality(test_data, test_data_type)

    def test_wait_for_condition_utility(self):
        """Test wait_for_condition utility function."""
        from tests.conftest import wait_for_condition

        # Test condition that becomes true quickly
        counter = 0

        def increment_condition():
            nonlocal counter
            counter += 1
            return counter >= 3

        result = wait_for_condition(increment_condition, timeout=5, interval=0.1)
        assert result is True
        assert counter == 3

        # Test condition that never becomes true
        def false_condition():
            return False

        result = wait_for_condition(false_condition, timeout=0.5, interval=0.1)
        assert result is False

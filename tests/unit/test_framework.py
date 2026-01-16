"""
Unit tests for testing framework - no external dependencies required.

Tests the core testing framework components that work without Docker:
- Market data generation
- Data quality validation
- Basic test utilities
"""

import pytest

# Test imports work correctly
try:
    from fixtures.market_data import MarketDataFactory, MarketScenarioFactory
    from conftest import assert_data_quality, wait_for_condition

    IMPORTS_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    IMPORTS_AVAILABLE = False


class TestMarketDataGeneration:
    """Test market data generation functionality."""

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_create_single_trade(self):
        """Test creating a single trade."""
        trade = MarketDataFactory.create_trade(symbol="AAPL", quantity=100, price=150.25)

        assert trade["symbol"] == "AAPL"
        assert trade["quantity"] == 100
        assert trade["price"] == "150.25"
        assert "trade_id" in trade
        assert "timestamp" in trade
        assert "exchange" in trade

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_create_single_quote(self):
        """Test creating a single quote."""
        quote = MarketDataFactory.create_quote(symbol="GOOGL", bid_size=1000, ask_size=2000)

        assert quote["symbol"] == "GOOGL"
        assert quote["bid_size"] == 1000
        assert quote["ask_size"] == 2000
        assert float(quote["bid_price"]) < float(quote["ask_price"])
        assert "timestamp" in quote

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_create_reference_data(self):
        """Test creating reference data."""
        reference = MarketDataFactory.create_reference(
            symbol="MSFT", sector="Technology", market_cap=2000.0
        )

        assert reference["symbol"] == "MSFT"
        assert reference["sector"] == "Technology"
        assert reference["market_cap"] == "2000.00"
        assert "name" in reference
        assert "shares_outstanding" in reference

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_trade_sequence_generation(self):
        """Test generating trade sequence."""
        trades = MarketDataFactory.create_trade_sequence(
            symbol="AAPL", count=10, duration_minutes=30
        )

        assert len(trades) == 10

        # Check timestamps are in order
        timestamps = [trade["timestamp"] for trade in trades]
        assert timestamps == sorted(timestamps)

        # All trades should have the same symbol
        assert all(trade["symbol"] == "AAPL" for trade in trades)

        # All should have positive quantities
        assert all(trade["quantity"] > 0 for trade in trades)

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_quote_sequence_generation(self):
        """Test generating quote sequence."""
        quotes = MarketDataFactory.create_quote_sequence(
            symbol="GOOGL", count=15, duration_minutes=45
        )

        assert len(quotes) == 15

        # All quotes should have the same symbol
        assert all(quote["symbol"] == "GOOGL" for quote in quotes)

        # All should have positive sizes
        assert all(quote["bid_size"] > 0 for quote in quotes)
        assert all(quote["ask_size"] > 0 for quote in quotes)

        # All should have bid < ask
        assert all(float(quote["bid_price"]) < float(quote["ask_price"]) for quote in quotes)


class TestMarketScenarios:
    """Test market scenario generation."""

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_market_opening_scenario(self):
        """Test market opening scenario generation."""
        scenario = MarketScenarioFactory.create_market_opening()

        assert scenario["scenario"] == "market_opening"
        assert "trades" in scenario
        assert "quotes" in scenario
        assert "symbols" in scenario
        assert "characteristics" in scenario

        # Market opening should have high volume
        assert scenario["characteristics"]["volume"] == "high"
        assert len(scenario["trades"]) > 0
        assert len(scenario["quotes"]) > 0

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_volatile_session_scenario(self):
        """Test volatile session scenario generation."""
        scenario = MarketScenarioFactory.create_volatile_session()

        assert scenario["scenario"] == "volatile_session"
        assert scenario["characteristics"]["volatility"] == "high"
        assert len(scenario["trades"]) > 0
        assert len(scenario["quotes"]) > 0

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_low_volume_scenario(self):
        """Test low volume scenario generation."""
        scenario = MarketScenarioFactory.create_low_volume_session()

        assert scenario["scenario"] == "low_volume_session"
        assert scenario["characteristics"]["volume"] == "low"
        assert len(scenario["trades"]) > 0
        assert len(scenario["quotes"]) > 0


class TestDataQuality:
    """Test data quality validation."""

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_trade_data_quality(self):
        """Test trade data quality validation."""
        # Valid trade data
        valid_trades = [
            {
                "symbol": "AAPL",
                "timestamp": "2024-01-01T10:00:00",
                "price": "150.00",
                "quantity": 100,
                "trade_id": "TRD-123",
            }
        ]

        # Should not raise any assertion
        assert_data_quality(valid_trades, "trades")

        # Invalid trade data (missing required field)
        invalid_trades = [
            {
                "symbol": "AAPL",
                "timestamp": "2024-01-01T10:00:00",
                "price": "150.00",
                # Missing quantity and trade_id
            }
        ]

        # Should raise assertion error
        with pytest.raises(AssertionError):
            assert_data_quality(invalid_trades, "trades")

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_quote_data_quality(self):
        """Test quote data quality validation."""
        # Valid quote data
        valid_quotes = [
            {
                "symbol": "AAPL",
                "timestamp": "2024-01-01T10:00:00",
                "bid_price": "149.99",
                "ask_price": "150.01",
            }
        ]

        # Should not raise any assertion
        assert_data_quality(valid_quotes, "quotes")

        # Invalid quote data (missing required field)
        invalid_quotes = [
            {
                "symbol": "AAPL",
                "timestamp": "2024-01-01T10:00:00",
                "bid_price": "149.99",
                # Missing ask_price
            }
        ]

        # Should raise assertion error
        with pytest.raises(AssertionError):
            assert_data_quality(invalid_quotes, "quotes")

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_empty_data_handling(self):
        """Test handling of empty data."""
        # Empty data should raise assertion error
        with pytest.raises(AssertionError):
            assert_data_quality([], "trades")


class TestFrameworkUtilities:
    """Test framework utility functions."""

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_wait_for_condition_success(self):
        """Test wait_for_condition with successful condition."""
        counter = 0

        def condition():
            nonlocal counter
            counter += 1
            return counter >= 3

        result = wait_for_condition(condition, timeout=1, interval=0.1)
        assert result is True
        assert counter == 3

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_wait_for_condition_timeout(self):
        """Test wait_for_condition with timeout."""

        def false_condition():
            return False

        result = wait_for_condition(false_condition, timeout=0, interval=0.1)
        assert result is False

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_wait_for_condition_immediate(self):
        """Test wait_for_condition with immediately true condition."""

        def true_condition():
            return True

        result = wait_for_condition(true_condition, timeout=1, interval=0.1)
        assert result is True


class TestDataIntegrity:
    """Test data integrity and consistency."""

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_price_consistency(self):
        """Test price consistency across data types."""
        from decimal import Decimal

        # Create related data
        base_price = 100.0
        trade = MarketDataFactory.create_trade(symbol="TEST", price=base_price)
        quote = MarketDataFactory.create_quote(symbol="TEST", base_price=base_price)

        # Prices should be reasonable
        trade_price = Decimal(trade["price"])
        bid_price = Decimal(quote["bid_price"])
        ask_price = Decimal(quote["ask_price"])

        # Trade price should be reasonable relative to bid/ask
        assert bid_price <= trade_price <= ask_price

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_timestamp_formatting(self):
        """Test timestamp formatting consistency."""
        from datetime import datetime

        # Create data with specific timestamp
        test_time = datetime(2024, 1, 1, 10, 0, 0)
        trade = MarketDataFactory.create_trade(symbol="TEST", timestamp=test_time)
        quote = MarketDataFactory.create_quote(symbol="TEST", timestamp=test_time)

        # Timestamps should be in ISO format
        assert "T" in trade["timestamp"]
        assert "T" in quote["timestamp"]

        # Should be parseable
        parsed_trade_time = datetime.fromisoformat(trade["timestamp"])
        parsed_quote_time = datetime.fromisoformat(quote["timestamp"])

        assert parsed_trade_time == test_time
        assert parsed_quote_time == test_time

    @pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="Test dependencies not available")
    def test_symbol_consistency(self):
        """Test symbol consistency across data types."""
        symbols = ["AAPL", "GOOGL", "MSFT"]

        for symbol in symbols:
            trade = MarketDataFactory.create_trade(symbol=symbol)
            quote = MarketDataFactory.create_quote(symbol=symbol)
            reference = MarketDataFactory.create_reference(symbol=symbol)

            assert trade["symbol"] == symbol
            assert quote["symbol"] == symbol
            assert reference["symbol"] == symbol


class TestBasicFramework:
    """Test basic framework functionality without dependencies."""

    def test_pytest_configuration(self):
        """Test that pytest is configured correctly."""
        # This test ensures pytest can run at all
        assert True

    def test_python_environment(self):
        """Test Python environment is set up correctly."""
        import sys

        assert sys.version_info >= (3, 13)

        # Test key packages are available
        import pytest

        assert hasattr(pytest, "main")

    def test_project_structure(self):
        """Test project structure is correct."""
        from pathlib import Path

        # Check that key directories exist
        project_root = Path.cwd()
        assert (project_root / "tests").exists()
        assert (project_root / "tests" / "conftest.py").exists()
        assert (project_root / "tests" / "unit").exists()
        assert (project_root / "tests" / "fixtures").exists()
        assert (project_root / "tests" / "docs").exists()

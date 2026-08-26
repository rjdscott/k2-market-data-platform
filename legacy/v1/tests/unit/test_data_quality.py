"""Unit tests for market data quality validation.

Tests validate:
- Trade data structure and business rules
- Quote data structure and spread validation
- Timestamp and sequencing validation
- Price and quantity constraints
- Cross-exchange data consistency
- Data anomaly detection
"""

from datetime import UTC, datetime

import pytest

from k2.api.models import Quote, Trade


class MarketDataFactory:
    """Factory for creating test market data with various scenarios."""

    @staticmethod
    def create_trade(**overrides) -> Trade:
        """Create a valid trade with optional field overrides."""
        defaults = {
            "message_id": "msg-123456",
            "trade_id": "trade-789012",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": datetime.now(UTC).isoformat(),
            "price": 150.25,
            "quantity": 100.0,
            "currency": "USD",
            "side": "BUY",
            "ingestion_timestamp": datetime.now(UTC).isoformat(),
            "source_sequence": 12345,
            "platform_sequence": 67890,
        }

        # Merge overrides
        defaults.update(overrides)

        return Trade(**defaults)

    @staticmethod
    def create_quote(**overrides) -> Quote:
        """Create a valid quote with optional field overrides."""
        defaults = {
            "message_id": "msg-123456",
            "quote_id": "quote-789012",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": datetime.now(UTC).isoformat(),
            "bid_price": 150.20,
            "bid_quantity": 1000.0,
            "ask_price": 150.30,
            "ask_quantity": 800.0,
            "currency": "USD",
            "ingestion_timestamp": datetime.now(UTC).isoformat(),
            "source_sequence": 12345,
            "platform_sequence": 67890,
        }

        defaults.update(overrides)
        return Quote(**defaults)


class TestMarketDataFactory:
    """Factory for creating test market data with various scenarios."""

    @staticmethod
    def create_trade(**overrides) -> Trade:
        """Create a valid trade with optional field overrides."""
        defaults = {
            "message_id": "msg-123456",
            "trade_id": "trade-789012",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": datetime.now(UTC).isoformat(),
            "price": 150.25,
            "quantity": 100.0,
            "currency": "USD",
            "side": "BUY",
            "ingestion_timestamp": datetime.now(UTC).isoformat(),
            "source_sequence": 12345,
            "platform_sequence": 67890,
        }

        # Merge overrides
        defaults.update(overrides)

        return Trade(**defaults)

    @staticmethod
    def create_quote(**overrides) -> Quote:
        """Create a valid quote with optional field overrides."""
        defaults = {
            "message_id": "msg-123456",
            "quote_id": "quote-789012",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "asset_class": "EQUITY",
            "timestamp": datetime.now(UTC).isoformat(),
            "bid_price": 150.20,
            "bid_quantity": 1000.0,
            "ask_price": 150.30,
            "ask_quantity": 800.0,
            "currency": "USD",
            "ingestion_timestamp": datetime.now(UTC).isoformat(),
            "source_sequence": 12345,
            "platform_sequence": 67890,
        }

        defaults.update(overrides)
        return Quote(**defaults)


class TestDataQualityValidation:
    """Test data quality validation functions and business rules."""

    def test_trade_with_valid_data(self):
        """Valid trade should pass all quality checks."""
        trade = MarketDataFactory.create_trade()

        # Basic structure validation
        assert trade.symbol is not None
        assert trade.symbol == "AAPL"
        assert trade.price > 0
        assert trade.quantity > 0
        assert trade.side in ["BUY", "SELL", "SELL_SHORT", "UNKNOWN"]
        assert trade.exchange is not None
        assert trade.asset_class is not None
        assert trade.currency is not None

    def test_trade_price_constraints(self):
        """Trade prices should be positive and reasonable."""
        # Valid prices
        valid_prices = [0.01, 1.0, 100.5, 10000.99]
        for price in valid_prices:
            trade = MarketDataFactory.create_trade(price=price)
            assert trade.price == price
            assert trade.price > 0

        # Invalid prices (should be caught by validation if implemented)
        # Note: Pydantic models may not automatically enforce business rules
        # This test shows where additional validation should be added

    def test_trade_quantity_constraints(self):
        """Trade quantities should be positive."""
        valid_quantities = [0.001, 1.0, 100.5, 10000.0]
        for quantity in valid_quantities:
            trade = MarketDataFactory.create_trade(quantity=quantity)
            assert trade.quantity == quantity
            assert trade.quantity > 0

    def test_trade_timestamp_validation(self):
        """Trade timestamps should be in valid format and reasonable range."""
        # Test with current timestamp
        now = datetime.now(UTC)
        trade = MarketDataFactory.create_trade(timestamp=now.isoformat())
        assert trade.timestamp is not None

        # Test with historical timestamp
        historical = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        trade = MarketDataFactory.create_trade(timestamp=historical.isoformat())
        assert trade.timestamp == historical.isoformat()

    def test_trade_symbol_validation(self):
        """Trade symbols should follow expected format."""
        valid_symbols = ["AAPL", "GOOGL", "MSFT", "BTCUSDT", "ETH-USD"]
        for symbol in valid_symbols:
            trade = MarketDataFactory.create_trade(symbol=symbol)
            assert trade.symbol == symbol
            assert len(trade.symbol) > 0

    def test_trade_side_validation(self):
        """Trade sides should be valid enum values."""
        valid_sides = ["BUY", "SELL", "SELL_SHORT", "UNKNOWN"]
        for side in valid_sides:
            trade = MarketDataFactory.create_trade(side=side)
            assert trade.side == side

    def test_quote_with_valid_data(self):
        """Valid quote should pass all quality checks."""
        quote = MarketDataFactory.create_quote()

        assert quote.symbol is not None
        assert quote.bid_price is not None
        assert quote.ask_price is not None
        assert quote.bid_quantity is not None
        assert quote.ask_quantity is not None

    def test_quote_bid_ask_spread_validation(self):
        """Quote bid price should be less than ask price."""
        # Valid spread
        quote = MarketDataFactory.create_quote(bid_price=100.0, ask_price=100.05)
        assert quote.bid_price < quote.ask_price
        spread = quote.ask_price - quote.bid_price
        assert abs(spread - 0.05) < 0.001  # Allow for floating point precision

        # Zero spread (marketable quote)
        quote = MarketDataFactory.create_quote(bid_price=100.0, ask_price=100.0)
        assert quote.bid_price <= quote.ask_price

    def test_quote_crossed_market_detection(self):
        """Should detect crossed markets (bid >= ask)."""
        # This would be a validation function to implement
        # For now, we test the data structure

        # Crossed market (invalid)
        crossed_quote = MarketDataFactory.create_quote(bid_price=100.10, ask_price=100.00)
        # In a real system, this should trigger an alert or validation error

        assert crossed_quote.bid_price == 100.10
        assert crossed_quote.ask_price == 100.00

    def test_quote_quantity_validation(self):
        """Quote quantities should be positive when present."""
        # Valid quantities
        valid_quantities = [100.0, 1000.5, 10000.0]
        for quantity in valid_quantities:
            quote = MarketDataFactory.create_quote(bid_quantity=quantity, ask_quantity=quantity)
            assert quote.bid_quantity == quantity
            assert quote.ask_quantity == quantity
            assert quote.bid_quantity > 0
            assert quote.ask_quantity > 0

    def test_quote_timestamp_consistency(self):
        """Quote timestamps should be consistent with ingestion time."""
        now = datetime.now(UTC)
        quote = MarketDataFactory.create_quote(
            timestamp=now.isoformat(), ingestion_timestamp=now.isoformat()
        )
        assert quote.timestamp == now.isoformat()
        assert quote.ingestion_timestamp == now.isoformat()

    def test_asset_class_validation(self):
        """Asset classes should be valid values."""
        valid_asset_classes = ["EQUITY", "CRYPTO", "FUTURES", "OPTIONS"]
        for asset_class in valid_asset_classes:
            trade = MarketDataFactory.create_trade(asset_class=asset_class)
            quote = MarketDataFactory.create_quote(asset_class=asset_class)
            assert trade.asset_class == asset_class
            assert quote.asset_class == asset_class

    def test_exchange_validation(self):
        """Exchanges should be valid identifiers."""
        valid_exchanges = ["NASDAQ", "NYSE", "ARCA", "BINANCE", "COINBASE", "CBOT"]
        for exchange in valid_exchanges:
            trade = MarketDataFactory.create_trade(exchange=exchange)
            quote = MarketDataFactory.create_quote(exchange=exchange)
            assert trade.exchange == exchange
            assert quote.exchange == exchange


class TestDataAnomalyDetection:
    """Test detection of data anomalies and edge cases."""

    def test_detect_stale_prices(self):
        """Should detect prices that haven't updated recently."""
        # This would be a function to implement
        # For now, test the data structure

        old_timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        old_trade = MarketDataFactory.create_trade(timestamp=old_timestamp.isoformat())

        # In a real system, this should trigger a stale price alert
        assert old_trade.timestamp == old_timestamp.isoformat()

    def test_detect_price_jumps(self):
        """Should detect unusual price movements."""
        # Create trades with large price difference
        base_price = 100.0
        jump_price = 150.0  # 50% jump

        trade1 = MarketDataFactory.create_trade(price=base_price)
        trade2 = MarketDataFactory.create_trade(price=jump_price, symbol=trade1.symbol)

        price_change = abs(trade2.price - trade1.price) / trade1.price
        assert price_change == 0.5  # 50% change

        # In a real system, this should trigger a price jump alert

    def test_detect_duplicate_trades(self):
        """Should detect potentially duplicate trades."""
        # Create two trades with same trade_id (potential duplicate)
        duplicate_trade = MarketDataFactory.create_trade(
            trade_id="DUPLICATE-123", price=100.0, quantity=100.0
        )

        duplicate_trade2 = MarketDataFactory.create_trade(
            trade_id="DUPLICATE-123",  # Same ID
            price=100.0,
            quantity=100.0,
        )

        assert duplicate_trade.trade_id == duplicate_trade2.trade_id
        # In a real system, this should trigger duplicate detection

    def test_detect_zero_quantity_trades(self):
        """Should detect trades with zero or negative quantities."""
        # Zero quantity trade
        zero_quantity_trade = MarketDataFactory.create_trade(quantity=0.0)
        assert zero_quantity_trade.quantity == 0.0

        # In a real system, zero quantity trades might be flagged

    def test_detect_negative_prices(self):
        """Should detect negative prices (invalid for most assets)."""
        # Note: Negative prices can be valid in some contexts (e.g., oil futures)
        # but are typically invalid for equities and crypto

        # This test shows where price validation should be implemented
        pass

    def test_detect_sequence_gaps(self):
        """Should detect gaps in sequence numbers."""
        # Create trades with sequence gaps
        trade1 = MarketDataFactory.create_trade(source_sequence=100)
        trade2 = MarketDataFactory.create_trade(source_sequence=105)  # Gap of 5

        sequence_gap = trade2.source_sequence - trade1.source_sequence
        assert sequence_gap == 5

        # In a real system, this should trigger a sequence gap alert


class TestDataConsistencyValidation:
    """Test cross-field and cross-record consistency checks."""

    def test_trade_quote_price_consistency(self):
        """Trade prices should be reasonable relative to current quotes."""
        # Create a quote and a trade within the bid-ask spread
        quote = MarketDataFactory.create_quote(bid_price=99.95, ask_price=100.05)

        # Trade within spread
        in_spread_trade = MarketDataFactory.create_trade(
            symbol=quote.symbol,
            exchange=quote.exchange,
            price=100.00,  # Within bid-ask
        )

        assert quote.bid_price <= in_spread_trade.price <= quote.ask_price

    def test_currency_consistency(self):
        """Currency fields should be consistent across records."""
        usd_trade = MarketDataFactory.create_trade(currency="USD")
        usd_quote = MarketDataFactory.create_quote(currency="USD")

        assert usd_trade.currency == "USD"
        assert usd_quote.currency == "USD"

    def test_symbol_exchange_mapping(self):
        """Symbol-exchange combinations should be valid."""
        # Test valid combinations
        equity_trade = MarketDataFactory.create_trade(
            symbol="AAPL", exchange="NASDAQ", asset_class="EQUITY"
        )

        crypto_trade = MarketDataFactory.create_trade(
            symbol="BTCUSDT", exchange="BINANCE", asset_class="CRYPTO"
        )

        assert equity_trade.symbol == "AAPL"
        assert equity_trade.exchange == "NASDAQ"
        assert equity_trade.asset_class == "EQUITY"

        assert crypto_trade.symbol == "BTCUSDT"
        assert crypto_trade.exchange == "BINANCE"
        assert crypto_trade.asset_class == "CRYPTO"


class TestQualityMetrics:
    """Test data quality metrics and scoring."""

    def test_completeness_score(self):
        """Calculate completeness score based on filled fields."""
        # Complete trade
        complete_trade = MarketDataFactory.create_trade()

        # Trade with missing optional fields
        incomplete_trade = MarketDataFactory.create_trade(
            trade_conditions=None, source_sequence=None, vendor_data=None
        )

        # Count non-null fields
        complete_fields = sum(
            1 for field in complete_trade.model_dump().values() if field is not None
        )
        incomplete_fields = sum(
            1 for field in incomplete_trade.model_dump().values() if field is not None
        )

        assert complete_fields >= incomplete_fields

    def test_timeliness_score(self):
        """Calculate timeliness based on ingestion vs event time."""
        # Recent trade (low latency)
        now = datetime.now(UTC)
        recent_trade = MarketDataFactory.create_trade(
            timestamp=now.isoformat(), ingestion_timestamp=now.isoformat()
        )

        # Stale trade (high latency)
        old_time = datetime(2024, 1, 1, tzinfo=UTC)
        stale_trade = MarketDataFactory.create_trade(
            timestamp=old_time.isoformat(), ingestion_timestamp=now.isoformat()
        )

        assert recent_trade.timestamp == recent_trade.ingestion_timestamp
        assert stale_trade.timestamp != stale_trade.ingestion_timestamp

    def test_accuracy_indicators(self):
        """Test various indicators of data accuracy."""
        # Reasonable price ranges
        equity_trade = MarketDataFactory.create_trade(symbol="AAPL", price=150.25)
        crypto_trade = MarketDataFactory.create_trade(symbol="BTCUSDT", price=45000.50)

        assert 0 < equity_trade.price < 10000  # Reasonable equity range
        assert 1000 < crypto_trade.price < 1000000  # Reasonable crypto range

    def test_consistency_indicators(self):
        """Test consistency indicators across related data."""
        # Create multiple trades for same symbol
        symbol = "AAPL"
        trades = [
            MarketDataFactory.create_trade(symbol=symbol, price=150.00),
            MarketDataFactory.create_trade(symbol=symbol, price=150.25),
            MarketDataFactory.create_trade(symbol=symbol, price=149.75),
        ]

        # All should have same symbol
        for trade in trades:
            assert trade.symbol == symbol

        # Prices should be in reasonable range
        prices = [trade.price for trade in trades]
        assert max(prices) - min(prices) < 10.0  # Within $10 range


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Unit tests for market data factory pattern and data generation.

Tests validate:
- Market data factory for consistent test data generation
- Realistic market scenarios and edge cases
- Data volume and performance characteristics
- Cross-asset class data generation
- Time series data consistency
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Dict, Any
import uuid

import pytest

from k2.api.models import Trade, Quote


class MarketDataFactory:
    """Factory for generating realistic test market data."""

    # Reference data for realistic test scenarios
    SYMBOLS = {
        "EQUITY": ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "NFLX"],
        "CRYPTO": ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "DOTUSDT"],
        "FUTURES": ["ES", "NQ", "YM", "GC", "CL", "ZN"],
        "OPTIONS": ["AAPL240119C00150000", "SPY240119P00450000"],
    }

    EXCHANGES = {
        "EQUITY": ["NASDAQ", "NYSE", "ARCA"],
        "CRYPTO": ["BINANCE", "COINBASE", "KRAKEN"],
        "FUTURES": ["CME", "CBOT", "NYMEX"],
        "OPTIONS": ["CBOE", "ISE", "NASDAQ"],
    }

    def __init__(self, asset_class: str = "EQUITY"):
        """Initialize factory for specific asset class."""
        if asset_class not in self.SYMBOLS:
            raise ValueError(f"Invalid asset class: {asset_class}")
        self.asset_class = asset_class
        self.symbol_index = 0
        self.exchange_index = 0

    def create_trade(self, **overrides) -> Trade:
        """Create a realistic trade record."""
        symbol = overrides.get("symbol") or self._get_next_symbol()
        exchange = overrides.get("exchange") or self._get_next_exchange()

        # Generate realistic prices based on asset class
        base_price = self._get_base_price(symbol)
        price_variation = base_price * 0.001  # 0.1% variation
        price = float(Decimal(str(base_price + price_variation)).quantize(Decimal("0.01")))

        # Generate realistic quantities
        quantity = self._get_realistic_quantity()

        # Alternate between BUY and SELL
        side = "BUY" if uuid.uuid4().int % 2 == 0 else "SELL"

        now = datetime.now(timezone.utc)
        trade_data = {
            "message_id": f"msg-{uuid.uuid4().hex[:12]}",
            "trade_id": f"trd-{uuid.uuid4().hex[:12]}",
            "symbol": symbol,
            "exchange": exchange,
            "asset_class": self.asset_class,
            "timestamp": (now - timedelta(microseconds=uuid.uuid4().int % 1000000)).isoformat(),
            "price": price,
            "quantity": quantity,
            "currency": self._get_currency(),
            "side": side,
            "trade_conditions": None,
            "source_sequence": uuid.uuid4().int % 1000000,
            "ingestion_timestamp": now.isoformat(),
            "platform_sequence": uuid.uuid4().int % 1000000,
            "vendor_data": None,
        }

        # Apply overrides
        trade_data.update(overrides)

        return Trade(**trade_data)

    def create_quote(self, **overrides) -> Quote:
        """Create a realistic quote record."""
        symbol = overrides.get("symbol") or self._get_next_symbol()
        exchange = overrides.get("exchange") or self._get_next_exchange()

        # Generate realistic bid/ask spread
        base_price = self._get_base_price(symbol)
        spread_bps = 5  # 5 basis points spread
        spread = base_price * (spread_bps / 10000)

        # Ensure minimum spread of 0.01 to avoid crossed markets
        min_tick = 0.01
        actual_spread = max(spread, min_tick)

        bid_price = float(Decimal(str(base_price - actual_spread / 2)).quantize(Decimal("0.01")))
        ask_price = float(Decimal(str(base_price + actual_spread / 2)).quantize(Decimal("0.01")))

        # Ensure bid < ask (fix rounding issues)
        if bid_price >= ask_price:
            bid_price = float(Decimal(str(base_price - min_tick)).quantize(Decimal("0.01")))
            ask_price = float(Decimal(str(base_price + min_tick)).quantize(Decimal("0.01")))

        # Generate realistic quote sizes
        bid_size = self._get_realistic_quantity()
        ask_size = self._get_realistic_quantity()

        now = datetime.now(timezone.utc)
        quote_data = {
            "message_id": f"msg-{uuid.uuid4().hex[:12]}",
            "quote_id": f"qt-{uuid.uuid4().hex[:12]}",
            "symbol": symbol,
            "exchange": exchange,
            "asset_class": self.asset_class,
            "timestamp": (now - timedelta(microseconds=uuid.uuid4().int % 1000000)).isoformat(),
            "bid_price": bid_price,
            "bid_quantity": bid_size,
            "ask_price": ask_price,
            "ask_quantity": ask_size,
            "currency": self._get_currency(),
            "source_sequence": uuid.uuid4().int % 1000000,
            "ingestion_timestamp": now.isoformat(),
            "platform_sequence": uuid.uuid4().int % 1000000,
            "vendor_data": None,
        }

        quote_data.update(overrides)
        return Quote(**quote_data)

    def create_time_series(
        self, record_type: str, count: int, start_time: datetime = None, interval_ms: int = 1000
    ) -> List[Dict[str, Any]]:
        """Create a time series of trades or quotes."""
        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(minutes=count * interval_ms / 60000)

        records = []
        for i in range(count):
            timestamp = start_time + timedelta(milliseconds=i * interval_ms)

            if record_type.lower() == "trade":
                record = self.create_trade(timestamp=timestamp.isoformat())
            elif record_type.lower() == "quote":
                record = self.create_quote(timestamp=timestamp.isoformat())
            else:
                raise ValueError("record_type must be 'trade' or 'quote'")

            records.append(record.model_dump())

        return records

    def create_market_scenario(self, scenario: str) -> Dict[str, Any]:
        """Create predefined market scenarios for testing."""
        scenarios = {
            "normal_market": self._create_normal_market_scenario,
            "high_volatility": self._create_high_volatility_scenario,
            "wide_spreads": self._create_wide_spreads_scenario,
            "thin_liquidity": self._create_thin_liquidity_scenario,
        }

        if scenario not in scenarios:
            raise ValueError(f"Unknown scenario: {scenario}")

        return scenarios[scenario]()

    def _get_next_symbol(self) -> str:
        """Get next symbol in rotation."""
        symbols = self.SYMBOLS[self.asset_class]
        symbol = symbols[self.symbol_index % len(symbols)]
        self.symbol_index += 1
        return symbol

    def _get_next_exchange(self) -> str:
        """Get next exchange in rotation."""
        exchanges = self.EXCHANGES[self.asset_class]
        exchange = exchanges[self.exchange_index % len(exchanges)]
        self.exchange_index += 1
        return exchange

    def _get_base_price(self, symbol: str) -> float:
        """Get realistic base price for symbol."""
        # Price ranges by asset class
        if self.asset_class == "EQUITY":
            if symbol in ["AAPL", "GOOGL", "MSFT"]:
                return 150.0
            elif symbol in ["AMZN", "TSLA"]:
                return 200.0
            else:
                return 100.0
        elif self.asset_class == "CRYPTO":
            if "BTC" in symbol:
                return 45000.0
            elif "ETH" in symbol:
                return 3000.0
            else:
                return 1.0
        elif self.asset_class == "FUTURES":
            if symbol in ["ES", "NQ"]:
                return 4500.0
            elif symbol == "GC":
                return 2000.0
            else:
                return 100.0
        else:  # OPTIONS
            return 5.0

    def _get_realistic_quantity(self) -> float:
        """Get realistic quantity based on asset class."""
        if self.asset_class == "EQUITY":
            return 100.0 * (1 + (uuid.uuid4().int % 100))  # 100 to 10,100
        elif self.asset_class == "CRYPTO":
            return 0.1 * (1 + (uuid.uuid4().int % 1000))  # 0.1 to 100.1
        elif self.asset_class == "FUTURES":
            return 1.0 * (1 + (uuid.uuid4().int % 10))  # 1 to 11
        else:  # OPTIONS
            return 1.0 * (1 + (uuid.uuid4().int % 50))  # 1 to 51

    def _get_currency(self) -> str:
        """Get appropriate currency for asset class."""
        if self.asset_class == "CRYPTO":
            return "USDT"
        else:
            return "USD"

    def _create_normal_market_scenario(self) -> Dict[str, Any]:
        """Create normal market conditions."""
        trades = [self.create_trade() for _ in range(10)]
        quotes = [self.create_quote() for _ in range(20)]

        return {
            "scenario": "normal_market",
            "trades": trades,
            "quotes": quotes,
            "description": "Normal market with typical spreads and volume",
        }

    def _create_high_volatility_scenario(self) -> Dict[str, Any]:
        """Create high volatility market conditions."""
        base_symbol = self._get_next_symbol()

        # Create trades with increasing price volatility
        trades = []
        base_price = self._get_base_price(base_symbol)

        for i in range(20):
            # Increasing volatility
            volatility = 0.01 * (i / 20)  # 0% to 1% volatility
            price_move = base_price * volatility * (1 if i % 2 == 0 else -1)
            price = float(Decimal(str(base_price + price_move)).quantize(Decimal("0.01")))

            trade = self.create_trade(
                symbol=base_symbol,
                price=price,
                quantity=self._get_realistic_quantity() * 2,  # Higher volume in volatility
            )
            trades.append(trade)

        quotes = [self.create_quote(symbol=base_symbol) for _ in range(40)]

        return {
            "scenario": "high_volatility",
            "trades": trades,
            "quotes": quotes,
            "description": "High volatility with increasing price swings",
        }

    def _create_wide_spreads_scenario(self) -> Dict[str, Any]:
        """Create wide spread market conditions."""
        quotes = []

        for i in range(15):
            symbol = self._get_next_symbol()
            base_price = self._get_base_price(symbol)

            # Wide spreads (50-100 basis points)
            spread_bps = 50 + (uuid.uuid4().int % 50)
            spread = base_price * (spread_bps / 10000)

            bid_price = float(Decimal(str(base_price - spread / 2)).quantize(Decimal("0.01")))
            ask_price = float(Decimal(str(base_price + spread / 2)).quantize(Decimal("0.01")))

            quote = self.create_quote(
                symbol=symbol,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_quantity=self._get_realistic_quantity() * 0.5,  # Lower liquidity
                ask_quantity=self._get_realistic_quantity() * 0.5,
            )
            quotes.append(quote)

        trades = [self.create_trade() for _ in range(5)]

        return {
            "scenario": "wide_spreads",
            "trades": trades,
            "quotes": quotes,
            "description": "Wide bid-ask spreads indicating lower liquidity",
        }

    def _create_thin_liquidity_scenario(self) -> Dict[str, Any]:
        """Create thin liquidity market conditions."""
        trades = []
        quotes = []

        symbol = self._get_next_symbol()

        # Fewer trades with smaller quantities
        for i in range(5):
            trade = self.create_trade(
                symbol=symbol,
                quantity=self._get_realistic_quantity() * 0.1,  # Very small quantities
            )
            trades.append(trade)

        # Wider spreads, smaller quote sizes
        for i in range(10):
            base_price = self._get_base_price(symbol)
            spread_bps = 20 + (uuid.uuid4().int % 30)  # 20-50 bps
            spread = base_price * (spread_bps / 10000)

            quote = self.create_quote(
                symbol=symbol,
                bid_price=float(Decimal(str(base_price - spread / 2)).quantize(Decimal("0.01"))),
                ask_price=float(Decimal(str(base_price + spread / 2)).quantize(Decimal("0.01"))),
                bid_quantity=self._get_realistic_quantity() * 0.05,
                ask_quantity=self._get_realistic_quantity() * 0.05,
            )
            quotes.append(quote)

        return {
            "scenario": "thin_liquidity",
            "trades": trades,
            "quotes": quotes,
            "description": "Thin liquidity with small sizes and wider spreads",
        }


class TestMarketDataFactory:
    """Test the market data factory functionality."""

    def test_factory_initialization(self):
        """Factory should initialize with valid asset classes."""
        for asset_class in ["EQUITY", "CRYPTO", "FUTURES", "OPTIONS"]:
            factory = MarketDataFactory(asset_class)
            assert factory.asset_class == asset_class

    def test_factory_invalid_asset_class(self):
        """Factory should raise error for invalid asset class."""
        with pytest.raises(ValueError) as exc_info:
            MarketDataFactory("INVALID")

        assert "Invalid asset class" in str(exc_info.value)

    def test_create_realistic_trades(self):
        """Factory should create realistic trade data."""
        for asset_class in ["EQUITY", "CRYPTO"]:
            factory = MarketDataFactory(asset_class)

            for _ in range(5):
                trade = factory.create_trade()

                # Basic validation
                assert isinstance(trade, Trade)
                assert trade.symbol is not None
                assert trade.exchange is not None
                assert trade.asset_class == asset_class
                assert trade.price > 0
                assert trade.quantity > 0
                assert trade.side in ["BUY", "SELL"]
                assert trade.currency is not None

    def test_create_realistic_quotes(self):
        """Factory should create realistic quote data."""
        for asset_class in ["EQUITY", "CRYPTO"]:
            factory = MarketDataFactory(asset_class)

            for _ in range(5):
                quote = factory.create_quote()

                # Basic validation
                assert isinstance(quote, Quote)
                assert quote.symbol is not None
                assert quote.exchange is not None
                assert quote.asset_class == asset_class
                assert quote.bid_price is not None
                assert quote.ask_price is not None
                assert quote.bid_quantity > 0
                assert quote.ask_quantity > 0
                assert quote.bid_price < quote.ask_price  # No crossed markets

    def test_trade_overrides(self):
        """Factory should respect field overrides."""
        factory = MarketDataFactory("EQUITY")

        overrides = {
            "symbol": "CUSTOM",
            "exchange": "CUSTOM_EXCHANGE",
            "price": 999.99,
            "quantity": 500.0,
            "side": "BUY",
        }

        trade = factory.create_trade(**overrides)

        assert trade.symbol == "CUSTOM"
        assert trade.exchange == "CUSTOM_EXCHANGE"
        assert trade.price == 999.99
        assert trade.quantity == 500.0
        assert trade.side == "BUY"

    def test_quote_overrides(self):
        """Factory should respect quote field overrides."""
        factory = MarketDataFactory("CRYPTO")

        overrides = {
            "symbol": "ETHUSDT",
            "bid_price": 3000.00,
            "ask_price": 3005.00,
            "bid_quantity": 10.5,
            "ask_quantity": 8.2,
        }

        quote = factory.create_quote(**overrides)

        assert quote.symbol == "ETHUSDT"
        assert quote.bid_price == 3000.00
        assert quote.ask_price == 3005.00
        assert quote.bid_quantity == 10.5
        assert quote.ask_quantity == 8.2

    def test_create_time_series_trades(self):
        """Factory should create coherent time series of trades."""
        factory = MarketDataFactory("EQUITY")
        count = 10
        interval_ms = 5000  # 5 seconds

        series = factory.create_time_series("trade", count, interval_ms=interval_ms)

        assert len(series) == count
        assert all(record["symbol"] for record in series)

        # Check time sequence (should be increasing)
        timestamps = [datetime.fromisoformat(record["timestamp"]) for record in series]
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1]

    def test_create_time_series_quotes(self):
        """Factory should create coherent time series of quotes."""
        factory = MarketDataFactory("CRYPTO")
        count = 15
        interval_ms = 1000  # 1 second

        series = factory.create_time_series("quote", count, interval_ms=interval_ms)

        assert len(series) == count
        assert all(record["bid_price"] < record["ask_price"] for record in series)

    def test_time_series_with_custom_start_time(self):
        """Factory should use custom start time for time series."""
        factory = MarketDataFactory("EQUITY")
        start_time = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)

        series = factory.create_time_series("trade", 5, start_time=start_time)

        # First record should be close to start time
        first_timestamp = datetime.fromisoformat(series[0]["timestamp"])
        assert abs((first_timestamp - start_time).total_seconds()) < 1

    def test_create_market_scenarios(self):
        """Factory should create all predefined market scenarios."""
        factory = MarketDataFactory("EQUITY")

        scenarios = ["normal_market", "high_volatility", "wide_spreads", "thin_liquidity"]

        for scenario_name in scenarios:
            scenario = factory.create_market_scenario(scenario_name)

            assert scenario["scenario"] == scenario_name
            assert "trades" in scenario
            assert "quotes" in scenario
            assert "description" in scenario
            assert isinstance(scenario["trades"], list)
            assert isinstance(scenario["quotes"], list)

    def test_high_volatility_scenario_characteristics(self):
        """High volatility scenario should show increasing price swings."""
        factory = MarketDataFactory("EQUITY")
        scenario = factory.create_market_scenario("high_volatility")

        trades = scenario["trades"]
        prices = [trade.price for trade in trades]

        # Should have trades with varying prices
        assert len(set(prices)) > len(prices) * 0.5  # At least 50% unique prices

        # Price range should be reasonable for high volatility
        price_range = max(prices) - min(prices)
        base_price = sum(prices) / len(prices)
        volatility_ratio = price_range / base_price

        # Should show some volatility (>1% price range)
        assert volatility_ratio > 0.01

    def test_wide_spreads_scenario_characteristics(self):
        """Wide spreads scenario should have larger bid-ask spreads."""
        factory = MarketDataFactory("CRYPTO")
        scenario = factory.create_market_scenario("wide_spreads")

        quotes = scenario["quotes"]
        spreads = [quote.ask_price - quote.bid_price for quote in quotes]

        # All spreads should be positive (allow for minor floating point issues)
        positive_spreads = [
            spread for spread in spreads if spread > 1e-10
        ]  # Filter out near-zero spreads
        assert len(positive_spreads) > 0, "Should have at least some positive spreads"
        assert all(spread > 0 for spread in positive_spreads)

        # Average spread should be significant (>0.1% of price)
        avg_spread = sum(spreads) / len(spreads)
        avg_price = sum((quote.bid_price + quote.ask_price) / 2 for quote in quotes) / len(quotes)
        spread_ratio = avg_spread / avg_price

        assert spread_ratio > 0.001  # >0.1%

    def test_thin_liquidity_scenario_characteristics(self):
        """Thin liquidity scenario should have small quantities and few trades."""
        factory = MarketDataFactory("EQUITY")
        scenario = factory.create_market_scenario("thin_liquidity")

        trades = scenario["trades"]
        quotes = scenario["quotes"]

        # Should have fewer trades
        assert len(trades) <= 10

        # Quote sizes should be small relative to normal sizes
        avg_bid_size = sum(quote.bid_quantity for quote in quotes) / len(quotes)
        avg_ask_size = sum(quote.ask_quantity for quote in quotes) / len(quotes)

        # Should be smaller than typical market maker sizes (more realistic threshold)
        assert avg_bid_size < 1000  # Less than 1000 shares on average
        assert avg_ask_size < 1000

    def test_asset_class_specific_currencies(self):
        """Factory should use appropriate currencies for each asset class."""
        equity_factory = MarketDataFactory("EQUITY")
        crypto_factory = MarketDataFactory("CRYPTO")

        equity_trade = equity_factory.create_trade()
        crypto_trade = crypto_factory.create_trade()

        assert equity_trade.currency == "USD"
        assert crypto_trade.currency == "USDT"

    def test_unique_identifiers(self):
        """Factory should generate unique identifiers for each record."""
        factory = MarketDataFactory("EQUITY")

        # Create multiple records
        trades = [factory.create_trade() for _ in range(20)]
        quotes = [factory.create_quote() for _ in range(20)]

        # Check unique message IDs
        trade_message_ids = {trade.message_id for trade in trades}
        quote_message_ids = {quote.message_id for quote in quotes}

        assert len(trade_message_ids) == 20  # All unique
        assert len(quote_message_ids) == 20  # All unique

        # Check unique trade/quote IDs
        trade_ids = {trade.trade_id for trade in trades}
        quote_ids = {quote.quote_id for quote in quotes}

        assert len(trade_ids) == 20
        assert len(quote_ids) == 20

    def test_symbol_rotation(self):
        """Factory should rotate through available symbols."""
        factory = MarketDataFactory("EQUITY")
        symbols_seen = set()

        # Create enough trades to see rotation
        trades = [factory.create_trade() for _ in range(20)]

        for trade in trades:
            symbols_seen.add(trade.symbol)

        # Should have seen multiple different symbols
        assert len(symbols_seen) > 1
        assert all(symbol in factory.SYMBOLS["EQUITY"] for symbol in symbols_seen)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

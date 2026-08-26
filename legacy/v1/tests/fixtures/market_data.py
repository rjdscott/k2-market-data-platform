"""
Sample data fixtures for K2 Market Data Platform testing.

Provides realistic market data for various test scenarios:
- Trade data with proper sequencing
- Quote data with bid-ask spreads
- Reference data for securities
- Market scenarios (opening, volatile, low volume)
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from faker import Faker

# Initialize faker for realistic data generation
fake = Faker()


class MarketDataFactory:
    """Factory for generating realistic market data."""

    # Realistic symbols from different sectors
    SYMBOLS = [
        # Technology
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "META",
        "NVDA",
        "TSLA",
        # Finance
        "JPM",
        "BAC",
        "WFC",
        "GS",
        "MS",
        "C",
        "AXP",
        # Healthcare
        "JNJ",
        "PFE",
        "UNH",
        "ABT",
        "T",
        "CVS",
        "MRK",
        # Energy
        "XOM",
        "CVX",
        "COP",
        "EOG",
        "SLB",
        "HAL",
        "BP",
        # Consumer
        "WMT",
        "PG",
        "KO",
        "PEP",
        "COST",
        "HD",
        "MCD",
    ]

    # Exchanges
    EXCHANGES = ["NASDAQ", "NYSE", "ARCA", "BATS", "EDGA", "EDGX"]

    # Realistic price ranges by sector
    PRICE_RANGES = {
        "TECH": (50, 1000),
        "FINANCE": (20, 200),
        "HEALTHCARE": (30, 500),
        "ENERGY": (10, 150),
        "CONSUMER": (20, 300),
    }

    @classmethod
    def create_trade(cls, **kwargs) -> dict[str, Any]:
        """Create a realistic trade record."""
        symbol = kwargs.get("symbol", random.choice(cls.SYMBOLS))
        exchange = kwargs.get("exchange", random.choice(cls.EXCHANGES))
        timestamp = kwargs.get("timestamp", datetime.now())

        # Determine price range based on symbol
        price_range = cls._get_price_range(symbol)
        base_price = kwargs.get("base_price", random.uniform(*price_range))
        price = kwargs.get("price", base_price + random.gauss(0, base_price * 0.001))

        trade = {
            "symbol": symbol,
            "exchange": exchange,
            "timestamp": timestamp.isoformat(),
            "price": str(Decimal(str(price)).quantize(Decimal("0.01"))),
            "quantity": kwargs.get("quantity", random.randint(100, 10000)),
            "trade_id": kwargs.get("trade_id", f"TRD-{uuid.uuid4().hex[:12]}"),
            "conditions": kwargs.get("conditions", ["regular"]),
        }

        return trade

    @classmethod
    def create_quote(cls, **kwargs) -> dict[str, Any]:
        """Create a realistic quote record."""
        symbol = kwargs.get("symbol", random.choice(cls.SYMBOLS))
        exchange = kwargs.get("exchange", random.choice(cls.EXCHANGES))
        timestamp = kwargs.get("timestamp", datetime.now())

        # Determine price range based on symbol
        price_range = cls._get_price_range(symbol)
        base_price = kwargs.get("base_price", random.uniform(*price_range))

        # Calculate bid-ask spread (realistic 0.01% to 0.1%)
        spread_percentage = random.uniform(0.0001, 0.001)
        spread = base_price * spread_percentage

        quote = {
            "symbol": symbol,
            "exchange": exchange,
            "timestamp": timestamp.isoformat(),
            "bid_price": str(Decimal(str(base_price - spread / 2)).quantize(Decimal("0.01"))),
            "bid_size": kwargs.get("bid_size", random.randint(100, 5000)),
            "ask_price": str(Decimal(str(base_price + spread / 2)).quantize(Decimal("0.01"))),
            "ask_size": kwargs.get("ask_size", random.randint(100, 5000)),
            "quote_condition": kwargs.get("quote_condition", "real-time"),
        }

        return quote

    @classmethod
    def create_reference(cls, **kwargs) -> dict[str, Any]:
        """Create a realistic reference data record."""
        symbol = kwargs.get("symbol", random.choice(cls.SYMBOLS))
        sector = kwargs.get(
            "sector", random.choice(["Technology", "Finance", "Healthcare", "Energy", "Consumer"])
        )

        # Generate company name based on symbol
        company_name = kwargs.get("name", cls._generate_company_name(symbol))

        # Generate realistic market cap (in billions)
        market_cap = kwargs.get("market_cap", random.uniform(10, 1000))

        # Calculate shares outstanding (rough estimate)
        shares_outstanding = int(market_cap * 1e9 / random.uniform(50, 500))

        reference = {
            "symbol": symbol,
            "name": company_name,
            "sector": sector,
            "industry": kwargs.get("industry", cls._generate_industry(sector)),
            "market_cap": str(Decimal(str(market_cap)).quantize(Decimal("0.01"))),
            "shares_outstanding": shares_outstanding,
            "listing_date": kwargs.get(
                "listing_date", fake.date_between(start_date="-30y", end_date="today").isoformat()
            ),
            "country": kwargs.get("country", "US"),
            "currency": kwargs.get("currency", "USD"),
            "corporate_actions": kwargs.get("corporate_actions", []),
        }

        return reference

    @classmethod
    def create_trade_sequence(
        cls, symbol: str, count: int, duration_minutes: int = 60
    ) -> list[dict[str, Any]]:
        """Create a sequence of related trades for a symbol."""
        trades = []
        start_time = datetime.now() - timedelta(minutes=duration_minutes)

        # Base price for this symbol
        price_range = cls._get_price_range(symbol)
        current_price = random.uniform(*price_range)

        for i in range(count):
            # Calculate timestamp
            timestamp = start_time + timedelta(seconds=i * (duration_minutes * 60 / count))

            # Simulate price movement (random walk)
            price_change = random.gauss(0, current_price * 0.0005)  # 0.05% std deviation
            current_price = max(current_price + price_change, 1.0)  # Ensure positive price

            # Vary quantity based on time of day (higher at open/close)
            minute_of_day = timestamp.hour * 60 + timestamp.minute
            if 570 <= minute_of_day <= 600 or 900 <= minute_of_day <= 930:  # Open/close
                quantity_multiplier = 2.0
            else:
                quantity_multiplier = 1.0

            trade = cls.create_trade(
                symbol=symbol,
                timestamp=timestamp,
                price=current_price,
                quantity=random.randint(100, int(5000 * quantity_multiplier)),
            )
            trades.append(trade)

        return trades

    @classmethod
    def create_quote_sequence(
        cls, symbol: str, count: int, duration_minutes: int = 60
    ) -> list[dict[str, Any]]:
        """Create a sequence of related quotes for a symbol."""
        quotes = []
        start_time = datetime.now() - timedelta(minutes=duration_minutes)

        # Base price for this symbol
        price_range = cls._get_price_range(symbol)
        current_price = random.uniform(*price_range)

        for i in range(count):
            # Calculate timestamp
            timestamp = start_time + timedelta(seconds=i * (duration_minutes * 60 / count))

            # Simulate price movement
            price_change = random.gauss(0, current_price * 0.0002)  # Less volatile than trades
            current_price = max(current_price + price_change, 1.0)

            quote = cls.create_quote(symbol=symbol, timestamp=timestamp, base_price=current_price)
            quotes.append(quote)

        return quotes

    @classmethod
    def _get_price_range(cls, symbol: str) -> tuple:
        """Get realistic price range for a symbol."""
        # Simple heuristic based on symbol characteristics
        if symbol.startswith(("A", "M", "G", "T")):  # Tech stocks tend to be higher
            return cls.PRICE_RANGES["TECH"]
        elif symbol.startswith(("J", "B", "W")):  # Finance/Consumer
            return random.choice([cls.PRICE_RANGES["FINANCE"], cls.PRICE_RANGES["CONSUMER"]])
        else:
            return random.choice(list(cls.PRICE_RANGES.values()))

    @classmethod
    def _generate_company_name(cls, symbol: str) -> str:
        """Generate realistic company name from symbol."""
        prefixes = [
            "Advanced",
            "American",
            "Applied",
            "Atlantic",
            "Bancorp",
            "Capital",
            "Digital",
            "Eastern",
            "First",
            "Global",
            "International",
            "National",
            "Pacific",
            "Royal",
            "Standard",
            "United",
            "Value",
        ]

        suffixes = [
            "Corporation",
            "Inc.",
            "Ltd.",
            "Group",
            "Holdings",
            "Company",
            "Industries",
            "Technologies",
            "Systems",
            "Solutions",
            "Services",
        ]

        prefix = random.choice(prefixes)
        suffix = random.choice(suffixes)

        return f"{prefix} {symbol.title()} {suffix}"

    @classmethod
    def _generate_industry(cls, sector: str) -> str:
        """Generate industry based on sector."""
        industries = {
            "Technology": ["Software", "Semiconductors", "Cloud Computing", "AI", "Cybersecurity"],
            "Finance": ["Banking", "Investment", "Insurance", "Wealth Management", "Payments"],
            "Healthcare": [
                "Pharmaceuticals",
                "Medical Devices",
                "Biotechnology",
                "Health Insurance",
            ],
            "Energy": ["Oil & Gas", "Renewable Energy", "Energy Services", "Pipelines"],
            "Consumer": ["Retail", "Food & Beverage", "Automotive", "Entertainment", "Apparel"],
        }

        return random.choice(industries.get(sector, ["Services"]))


class MarketScenarioFactory:
    """Factory for creating market scenario data."""

    @classmethod
    def create_market_opening(cls, symbols: list[str] | None = None) -> dict[str, Any]:
        """Create high-volume market opening scenario."""
        if symbols is None:
            symbols = random.sample(MarketDataFactory.SYMBOLS, 20)

        trades = []
        quotes = []

        # Opening auction: high volume, lots of price discovery
        opening_time = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)

        for symbol in symbols:
            # Pre-opening quotes
            for i in range(10):
                quote_time = opening_time - timedelta(minutes=10) + timedelta(seconds=i * 60)
                quote = MarketDataFactory.create_quote(symbol=symbol, timestamp=quote_time)
                quotes.append(quote)

            # Opening trades (high volume)
            for i in range(50):
                trade_time = opening_time + timedelta(seconds=i * 2)
                trade = MarketDataFactory.create_trade(
                    symbol=symbol,
                    timestamp=trade_time,
                    quantity=random.randint(1000, 50000),  # Larger volumes at open
                )
                trades.append(trade)

        return {
            "scenario": "market_opening",
            "timestamp": opening_time.isoformat(),
            "trades": trades,
            "quotes": quotes,
            "symbols": symbols,
            "characteristics": {
                "volume": "high",
                "volatility": "moderate",
                "price_discovery": "active",
            },
        }

    @classmethod
    def create_volatile_session(cls, symbols: list[str] | None = None) -> dict[str, Any]:
        """Create high-volatility trading session."""
        if symbols is None:
            symbols = random.sample(MarketDataFactory.SYMBOLS, 10)

        trades = []
        quotes = []

        start_time = datetime.now() - timedelta(hours=1)

        for symbol in symbols:
            # Start with base price
            price_range = MarketDataFactory._get_price_range(symbol)
            current_price = random.uniform(*price_range)

            # Create volatile pattern
            for i in range(100):
                timestamp = start_time + timedelta(seconds=i * 36)

                # Higher volatility
                price_change = random.gauss(0, current_price * 0.005)  # 0.5% std deviation
                current_price = max(current_price + price_change, 1.0)

                # Create both trades and quotes
                if i % 2 == 0:
                    trade = MarketDataFactory.create_trade(
                        symbol=symbol,
                        timestamp=timestamp,
                        price=current_price,
                        quantity=random.randint(500, 20000),
                    )
                    trades.append(trade)
                else:
                    quote = MarketDataFactory.create_quote(
                        symbol=symbol, timestamp=timestamp, base_price=current_price
                    )
                    quotes.append(quote)

        return {
            "scenario": "volatile_session",
            "timestamp": start_time.isoformat(),
            "trades": trades,
            "quotes": quotes,
            "symbols": symbols,
            "characteristics": {
                "volume": "moderate",
                "volatility": "high",
                "price_discovery": "rapid",
            },
        }

    @classmethod
    def create_low_volume_session(cls, symbols: list[str] | None = None) -> dict[str, Any]:
        """Create low-volume trading session."""
        if symbols is None:
            symbols = random.sample(MarketDataFactory.SYMBOLS, 15)

        trades = []
        quotes = []

        start_time = datetime.now() - timedelta(hours=2)

        for symbol in symbols:
            # Sparse trading - one trade every few minutes
            for i in range(10):
                timestamp = start_time + timedelta(minutes=i * 12)

                trade = MarketDataFactory.create_trade(
                    symbol=symbol,
                    timestamp=timestamp,
                    quantity=random.randint(100, 1000),  # Small quantities
                )
                trades.append(trade)

            # Quotes are also sparse
            for i in range(20):
                timestamp = start_time + timedelta(minutes=i * 6)
                quote = MarketDataFactory.create_quote(symbol=symbol, timestamp=timestamp)
                quotes.append(quote)

        return {
            "scenario": "low_volume_session",
            "timestamp": start_time.isoformat(),
            "trades": trades,
            "quotes": quotes,
            "symbols": symbols,
            "characteristics": {"volume": "low", "volatility": "low", "price_discovery": "slow"},
        }


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def market_data_factory():
    """Provide MarketDataFactory instance."""
    return MarketDataFactory


@pytest.fixture(scope="function")
def market_scenario_factory():
    """Provide MarketScenarioFactory instance."""
    return MarketScenarioFactory


@pytest.fixture(scope="function")
def single_trade(market_data_factory):
    """Single trade record for testing."""
    return market_data_factory.create_trade()


@pytest.fixture(scope="function")
def single_quote(market_data_factory):
    """Single quote record for testing."""
    return market_data_factory.create_quote()


@pytest.fixture(scope="function")
def single_reference(market_data_factory):
    """Single reference data record for testing."""
    return market_data_factory.create_reference()


@pytest.fixture(scope="function")
def trade_sequence(market_data_factory):
    """Sequence of trades for a single symbol."""
    symbol = random.choice(MarketDataFactory.SYMBOLS)
    return market_data_factory.create_trade_sequence(symbol, count=50)


@pytest.fixture(scope="function")
def quote_sequence(market_data_factory):
    """Sequence of quotes for a single symbol."""
    symbol = random.choice(MarketDataFactory.SYMBOLS)
    return market_data_factory.create_quote_sequence(symbol, count=100)


@pytest.fixture(scope="function")
def mixed_market_data(market_data_factory):
    """Mixed trades and quotes for multiple symbols."""
    symbols = random.sample(MarketDataFactory.SYMBOLS, 5)
    trades = []
    quotes = []

    for symbol in symbols:
        trades.extend(market_data_factory.create_trade_sequence(symbol, count=20))
        quotes.extend(market_data_factory.create_quote_sequence(symbol, count=40))

    return {
        "trades": trades,
        "quotes": quotes,
        "symbols": symbols,
        "total_trades": len(trades),
        "total_quotes": len(quotes),
    }


@pytest.fixture(scope="function")
def market_opening_scenario(market_scenario_factory):
    """Market opening scenario data."""
    return market_scenario_factory.create_market_opening()


@pytest.fixture(scope="function")
def volatile_session_scenario(market_scenario_factory):
    """Volatile trading session scenario."""
    return market_scenario_factory.create_volatile_session()


@pytest.fixture(scope="function")
def low_volume_session_scenario(market_scenario_factory):
    """Low volume trading session scenario."""
    return market_scenario_factory.create_low_volume_session()


@pytest.fixture(scope="function")
def reference_dataset(market_data_factory):
    """Complete reference dataset for symbols."""
    symbols = random.sample(MarketDataFactory.SYMBOLS, 50)
    return [market_data_factory.create_reference(symbol=symbol) for symbol in symbols]


@pytest.fixture(scope="function")
def corporate_action_data(market_data_factory):
    """Corporate action reference data."""
    actions = [
        {
            "type": "stock_split",
            "ratio": "2:1",
            "ex_date": "2024-01-15",
            "description": "2-for-1 stock split",
        },
        {
            "type": "dividend",
            "amount": "0.50",
            "ex_date": "2024-03-15",
            "description": "Quarterly dividend",
        },
        {
            "type": "merger",
            "target_company": "Target Corp",
            "terms": "cash_and_stock",
            "effective_date": "2024-06-01",
            "description": "Merger with Target Corp",
        },
    ]

    symbol = random.choice(MarketDataFactory.SYMBOLS)
    reference = market_data_factory.create_reference(symbol=symbol, corporate_actions=actions)

    return reference


@pytest.fixture(scope="function")
def sample_market_data(market_data_factory, market_scenario_factory):
    """Comprehensive sample market data for integration tests."""
    # Create various scenarios
    opening_data = market_scenario_factory.create_market_opening()
    volatile_data = market_scenario_factory.create_volatile_session()
    low_volume_data = market_scenario_factory.create_low_volume_session()

    # Combine all trades and quotes
    all_trades = opening_data["trades"] + volatile_data["trades"] + low_volume_data["trades"]

    all_quotes = opening_data["quotes"] + volatile_data["quotes"] + low_volume_data["quotes"]

    # Sort by timestamp
    all_trades.sort(key=lambda x: x["timestamp"])
    all_quotes.sort(key=lambda x: x["timestamp"])

    # Get unique symbols
    all_symbols = list(
        set(opening_data["symbols"] + volatile_data["symbols"] + low_volume_data["symbols"])
    )

    return {
        "trades": all_trades,
        "quotes": all_quotes,
        "symbols": all_symbols,
        "time_range": {
            "start": min(trade["timestamp"] for trade in all_trades),
            "end": max(trade["timestamp"] for trade in all_trades),
        },
        "scenarios": {
            "opening": opening_data,
            "volatile": volatile_data,
            "low_volume": low_volume_data,
        },
    }


# =============================================================================
# Data Persistence Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def fixture_data_dir():
    """Directory for storing persistent fixture data."""
    data_dir = Path(__file__).parent / "fixtures" / "sample_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture(scope="session")
def persisted_sample_data(fixture_data_dir):
    """Load or generate persistent sample data."""
    data_file = fixture_data_dir / "comprehensive_sample.json"

    if data_file.exists():
        with open(data_file) as f:
            return json.load(f)
    else:
        # Generate comprehensive dataset
        factory = MarketScenarioFactory()
        data = factory.create_market_opening(symbols=MarketDataFactory.SYMBOLS[:10])

        # Add additional scenarios
        volatile = factory.create_volatile_session(symbols=MarketDataFactory.SYMBOLS[:5])
        low_volume = factory.create_low_volume_session(symbols=MarketDataFactory.SYMBOLS[10:15])

        # Combine data
        data.update({"volatile_scenario": volatile, "low_volume_scenario": low_volume})

        # Persist to file
        with open(data_file, "w") as f:
            json.dump(data, f, indent=2)

        return data


@pytest.fixture(scope="session")
def csv_sample_files(fixture_data_dir):
    """Generate CSV sample files for testing."""
    trades_file = fixture_data_dir / "sample_trades.csv"
    quotes_file = fixture_data_dir / "sample_quotes.csv"

    if not trades_file.exists():
        # Generate trade data
        factory = MarketDataFactory()
        trades = []

        for symbol in MarketDataFactory.SYMBOLS[:20]:
            trades.extend(factory.create_trade_sequence(symbol, count=25))

        # Write CSV
        import pandas as pd

        df_trades = pd.DataFrame(trades)
        df_trades.to_csv(trades_file, index=False)

    if not quotes_file.exists():
        # Generate quote data
        factory = MarketDataFactory()
        quotes = []

        for symbol in MarketDataFactory.SYMBOLS[:20]:
            quotes.extend(factory.create_quote_sequence(symbol, count=50))

        # Write CSV
        import pandas as pd

        df_quotes = pd.DataFrame(quotes)
        df_quotes.to_csv(quotes_file, index=False)

    return {"trades_file": trades_file, "quotes_file": quotes_file}

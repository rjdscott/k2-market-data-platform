"""Crypto market data fixtures for testing Bronze, Silver, and Gold layers.

Provides realistic Binance and Kraken trade data matching production schemas:
- Binance raw schema (Bronze layer)
- Kraken raw schema (Bronze layer)
- V2 unified schema (Silver/Gold layers)
"""

import json
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest


class CryptoDataFactory:
    """Factory for generating realistic crypto trade data."""

    # Crypto trading pairs
    BINANCE_SYMBOLS = [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "ADAUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "DOTUSDT",
        "DOGEUSDT",
    ]

    KRAKEN_SYMBOLS = [
        "BTC/USD",
        "ETH/USD",
        "BNB/USD",
        "ADA/USD",
        "SOL/USD",
        "XRP/USD",
        "DOT/USD",
        "DOGE/USD",
    ]

    # Price ranges (USD)
    PRICE_RANGES = {
        "BTC": (30000, 70000),
        "ETH": (1500, 4500),
        "BNB": (200, 600),
        "ADA": (0.25, 1.50),
        "SOL": (20, 150),
        "XRP": (0.40, 1.20),
        "DOT": (5, 30),
        "DOGE": (0.05, 0.30),
    }

    @classmethod
    def create_binance_raw_trade(cls, **kwargs) -> dict[str, Any]:
        """Create Binance raw trade (matches Bronze schema).

        Schema: BinanceRawTrade from Schema Registry
        - event_type: "trade"
        - event_time_ms: long (event timestamp)
        - symbol: string (e.g., "BTCUSDT")
        - trade_id: long
        - price: string (decimal as string)
        - quantity: string (decimal as string)
        - trade_time_ms: long
        - is_buyer_maker: boolean
        - is_best_match: boolean (optional)
        - ingestion_timestamp: long
        """
        symbol = kwargs.get("symbol", random.choice(cls.BINANCE_SYMBOLS))
        base_currency = symbol.replace("USDT", "").replace("USD", "")

        # Get realistic price range
        price_range = cls.PRICE_RANGES.get(base_currency, (10, 1000))
        base_price = kwargs.get("base_price", random.uniform(*price_range))
        price_variance = base_price * 0.0001  # 0.01% variance
        price = kwargs.get("price", base_price + random.gauss(0, price_variance))

        # Trade timestamp
        trade_time = kwargs.get("trade_time", datetime.now())
        trade_time_ms = int(trade_time.timestamp() * 1000)
        event_time_ms = kwargs.get("event_time_ms", trade_time_ms)

        # Quantity based on price (inverse relationship)
        if base_price > 10000:  # BTC-like
            quantity = random.uniform(0.001, 0.1)
        elif base_price > 1000:  # ETH-like
            quantity = random.uniform(0.01, 1.0)
        elif base_price > 100:  # BNB-like
            quantity = random.uniform(0.1, 10.0)
        else:  # Altcoins
            quantity = random.uniform(10.0, 1000.0)

        return {
            "event_type": "trade",
            "event_time_ms": event_time_ms,
            "symbol": symbol,
            "trade_id": kwargs.get("trade_id", random.randint(1000000000, 9999999999)),
            "price": str(Decimal(str(price)).quantize(Decimal("0.00000001"))),
            "quantity": str(Decimal(str(quantity)).quantize(Decimal("0.00000001"))),
            "trade_time_ms": trade_time_ms,
            "is_buyer_maker": kwargs.get("is_buyer_maker", random.choice([True, False])),
            "is_best_match": kwargs.get("is_best_match", True),
            "ingestion_timestamp": kwargs.get(
                "ingestion_timestamp",
                int(datetime.now().timestamp() * 1000)
            ),
        }

    @classmethod
    def create_kraken_raw_trade(cls, **kwargs) -> dict[str, Any]:
        """Create Kraken raw trade (matches Bronze schema).

        Schema: KrakenRawTrade from Schema Registry
        - symbol: string (e.g., "BTC/USD")
        - price: string
        - quantity: string
        - trade_time_seconds: double (with microseconds)
        - side: string ("buy" or "sell")
        - order_type: string ("market" or "limit")
        - misc: string (additional info)
        - ingestion_timestamp: long
        """
        symbol = kwargs.get("symbol", random.choice(cls.KRAKEN_SYMBOLS))
        base_currency = symbol.split("/")[0]

        # Get realistic price range
        price_range = cls.PRICE_RANGES.get(base_currency, (10, 1000))
        base_price = kwargs.get("base_price", random.uniform(*price_range))
        price_variance = base_price * 0.0001
        price = kwargs.get("price", base_price + random.gauss(0, price_variance))

        # Trade timestamp (Kraken uses seconds with decimal microseconds)
        trade_time = kwargs.get("trade_time", datetime.now())
        trade_time_seconds = trade_time.timestamp()

        # Quantity based on price
        if base_price > 10000:  # BTC-like
            quantity = random.uniform(0.001, 0.1)
        elif base_price > 1000:  # ETH-like
            quantity = random.uniform(0.01, 1.0)
        elif base_price > 100:
            quantity = random.uniform(0.1, 10.0)
        else:
            quantity = random.uniform(10.0, 1000.0)

        return {
            "symbol": symbol,
            "price": str(Decimal(str(price)).quantize(Decimal("0.00000001"))),
            "quantity": str(Decimal(str(quantity)).quantize(Decimal("0.00000001"))),
            "trade_time_seconds": trade_time_seconds,
            "side": kwargs.get("side", random.choice(["buy", "sell"])),
            "order_type": kwargs.get("order_type", random.choice(["market", "limit"])),
            "misc": kwargs.get("misc", ""),
            "ingestion_timestamp": kwargs.get(
                "ingestion_timestamp",
                int(datetime.now().timestamp() * 1000)
            ),
        }

    @classmethod
    def create_v2_unified_trade(cls, exchange: str = "binance", **kwargs) -> dict[str, Any]:
        """Create V2 unified trade (Silver/Gold schema).

        V2 Unified Schema (15 required fields):
        - message_id: string (unique ID)
        - exchange: string ("binance" or "kraken")
        - symbol: string (normalized)
        - base_currency: string (e.g., "Bitcoin")
        - quote_currency: string (e.g., "US Dollar")
        - price: decimal
        - quantity: decimal
        - trade_value_usd: decimal
        - timestamp: timestamp (microseconds)
        - side: string ("buy" or "sell")
        - trade_id: string
        - is_market_maker: boolean
        - ingestion_timestamp: timestamp
        - validation_timestamp: timestamp
        - schema_id: int
        """
        exchange = exchange.lower()

        if exchange == "binance":
            raw_symbol = kwargs.get("symbol", random.choice(cls.BINANCE_SYMBOLS))
            base_currency_code = raw_symbol.replace("USDT", "").replace("USD", "")
        else:  # kraken
            raw_symbol = kwargs.get("symbol", random.choice(cls.KRAKEN_SYMBOLS))
            base_currency_code = raw_symbol.split("/")[0]

        # Currency name mapping
        currency_names = {
            "BTC": "Bitcoin",
            "ETH": "Ethereum",
            "BNB": "Binance Coin",
            "ADA": "Cardano",
            "SOL": "Solana",
            "XRP": "Ripple",
            "DOT": "Polkadot",
            "DOGE": "Dogecoin",
        }

        base_currency = currency_names.get(base_currency_code, base_currency_code)
        quote_currency = "US Dollar"

        # Price and quantity
        price_range = cls.PRICE_RANGES.get(base_currency_code, (10, 1000))
        price = kwargs.get("price", random.uniform(*price_range))

        if price > 10000:
            quantity = random.uniform(0.001, 0.1)
        elif price > 1000:
            quantity = random.uniform(0.01, 1.0)
        elif price > 100:
            quantity = random.uniform(0.1, 10.0)
        else:
            quantity = random.uniform(10.0, 1000.0)

        trade_value_usd = price * quantity

        # Timestamps (microseconds)
        trade_time = kwargs.get("trade_time", datetime.now())
        timestamp_us = int(trade_time.timestamp() * 1_000_000)
        ingestion_ts = kwargs.get("ingestion_timestamp", timestamp_us)
        validation_ts = kwargs.get("validation_timestamp", int(datetime.now().timestamp() * 1_000_000))

        return {
            "message_id": kwargs.get("message_id", f"{exchange}-{trade_time.strftime('%Y%m%d')}-{random.randint(1000000, 9999999)}"),
            "exchange": exchange,
            "symbol": raw_symbol,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "price": float(Decimal(str(price)).quantize(Decimal("0.00000001"))),
            "quantity": float(Decimal(str(quantity)).quantize(Decimal("0.00000001"))),
            "trade_value_usd": float(Decimal(str(trade_value_usd)).quantize(Decimal("0.00000001"))),
            "timestamp": timestamp_us,
            "side": kwargs.get("side", random.choice(["buy", "sell"])),
            "trade_id": str(kwargs.get("trade_id", random.randint(1000000000, 9999999999))),
            "is_market_maker": kwargs.get("is_market_maker", random.choice([True, False])),
            "ingestion_timestamp": ingestion_ts,
            "validation_timestamp": validation_ts,
            "schema_id": kwargs.get("schema_id", 1),
        }

    @classmethod
    def create_trade_sequence(
        cls,
        exchange: str,
        symbol: str,
        count: int,
        duration_minutes: int = 60,
        schema: str = "raw"
    ) -> list[dict[str, Any]]:
        """Create sequence of trades with realistic price movement.

        Args:
            exchange: "binance" or "kraken"
            symbol: Trading pair symbol
            count: Number of trades to generate
            duration_minutes: Time span for trades
            schema: "raw" for Bronze layer, "v2" for Silver/Gold layer
        """
        trades = []
        start_time = datetime.now() - timedelta(minutes=duration_minutes)

        # Get base currency and price range
        if exchange == "binance":
            base_currency = symbol.replace("USDT", "").replace("USD", "")
        else:
            base_currency = symbol.split("/")[0]

        price_range = cls.PRICE_RANGES.get(base_currency, (10, 1000))
        current_price = random.uniform(*price_range)

        for i in range(count):
            # Calculate timestamp
            trade_time = start_time + timedelta(seconds=i * (duration_minutes * 60 / count))

            # Random walk price movement
            price_change = random.gauss(0, current_price * 0.0005)
            current_price = max(current_price + price_change, 0.01)

            # Create trade based on schema
            if schema == "raw":
                if exchange == "binance":
                    trade = cls.create_binance_raw_trade(
                        symbol=symbol,
                        trade_time=trade_time,
                        base_price=current_price
                    )
                else:
                    trade = cls.create_kraken_raw_trade(
                        symbol=symbol,
                        trade_time=trade_time,
                        base_price=current_price
                    )
            else:  # v2 unified
                trade = cls.create_v2_unified_trade(
                    exchange=exchange,
                    symbol=symbol,
                    trade_time=trade_time,
                    price=current_price
                )

            trades.append(trade)

        return trades


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def crypto_data_factory():
    """Provide CryptoDataFactory instance."""
    return CryptoDataFactory


@pytest.fixture(scope="function")
def sample_binance_raw_trade(crypto_data_factory):
    """Single Binance raw trade (Bronze schema)."""
    return crypto_data_factory.create_binance_raw_trade()


@pytest.fixture(scope="function")
def sample_kraken_raw_trade(crypto_data_factory):
    """Single Kraken raw trade (Bronze schema)."""
    return crypto_data_factory.create_kraken_raw_trade()


@pytest.fixture(scope="function")
def sample_v2_unified_trade(crypto_data_factory):
    """Single V2 unified trade (Silver/Gold schema)."""
    return crypto_data_factory.create_v2_unified_trade(exchange="binance")


@pytest.fixture(scope="function")
def binance_trade_sequence(crypto_data_factory):
    """Sequence of 50 Binance raw trades."""
    return crypto_data_factory.create_trade_sequence(
        exchange="binance",
        symbol="BTCUSDT",
        count=50,
        schema="raw"
    )


@pytest.fixture(scope="function")
def kraken_trade_sequence(crypto_data_factory):
    """Sequence of 50 Kraken raw trades."""
    return crypto_data_factory.create_trade_sequence(
        exchange="kraken",
        symbol="BTC/USD",
        count=50,
        schema="raw"
    )


@pytest.fixture(scope="function")
def v2_mixed_trades(crypto_data_factory):
    """Mixed Binance and Kraken trades in V2 unified schema."""
    binance_trades = crypto_data_factory.create_trade_sequence(
        exchange="binance",
        symbol="BTCUSDT",
        count=25,
        schema="v2"
    )
    kraken_trades = crypto_data_factory.create_trade_sequence(
        exchange="kraken",
        symbol="BTC/USD",
        count=25,
        schema="v2"
    )

    # Combine and sort by timestamp
    all_trades = binance_trades + kraken_trades
    all_trades.sort(key=lambda x: x["timestamp"])

    return {
        "binance_trades": binance_trades,
        "kraken_trades": kraken_trades,
        "all_trades": all_trades,
        "total_count": len(all_trades),
    }


@pytest.fixture(scope="function")
def invalid_trade_records():
    """Trade records with validation errors (for DLQ testing)."""
    return {
        "missing_price": {
            "event_type": "trade",
            "event_time_ms": int(time.time() * 1000),
            "symbol": "BTCUSDT",
            "trade_id": 123456789,
            # Missing price field
            "quantity": "0.5",
            "trade_time_ms": int(time.time() * 1000),
            "is_buyer_maker": False,
            "ingestion_timestamp": int(time.time() * 1000),
        },
        "negative_price": {
            "event_type": "trade",
            "event_time_ms": int(time.time() * 1000),
            "symbol": "ETHUSDT",
            "trade_id": 987654321,
            "price": "-100.50",  # Invalid negative price
            "quantity": "1.0",
            "trade_time_ms": int(time.time() * 1000),
            "is_buyer_maker": True,
            "ingestion_timestamp": int(time.time() * 1000),
        },
        "zero_quantity": {
            "event_type": "trade",
            "event_time_ms": int(time.time() * 1000),
            "symbol": "BNBUSDT",
            "trade_id": 555555555,
            "price": "300.50",
            "quantity": "0.0",  # Invalid zero quantity
            "trade_time_ms": int(time.time() * 1000),
            "is_buyer_maker": False,
            "ingestion_timestamp": int(time.time() * 1000),
        },
        "invalid_symbol": {
            "event_type": "trade",
            "event_time_ms": int(time.time() * 1000),
            "symbol": "",  # Empty symbol
            "trade_id": 111111111,
            "price": "50000.00",
            "quantity": "0.1",
            "trade_time_ms": int(time.time() * 1000),
            "is_buyer_maker": True,
            "ingestion_timestamp": int(time.time() * 1000),
        },
    }


@pytest.fixture(scope="session")
def crypto_test_data_dir(tmp_path_factory):
    """Directory for storing crypto test data files."""
    return tmp_path_factory.mktemp("crypto_test_data")


@pytest.fixture(scope="session")
def binance_trades_json_file(crypto_test_data_dir, crypto_data_factory):
    """Generate Binance trades JSON file for testing."""
    trades = crypto_data_factory.create_trade_sequence(
        exchange="binance",
        symbol="BTCUSDT",
        count=100,
        duration_minutes=60,
        schema="raw"
    )

    file_path = crypto_test_data_dir / "binance_trades_sample.json"
    with open(file_path, "w") as f:
        json.dump(trades, f, indent=2)

    return file_path


@pytest.fixture(scope="session")
def kraken_trades_json_file(crypto_test_data_dir, crypto_data_factory):
    """Generate Kraken trades JSON file for testing."""
    trades = crypto_data_factory.create_trade_sequence(
        exchange="kraken",
        symbol="BTC/USD",
        count=100,
        duration_minutes=60,
        schema="raw"
    )

    file_path = crypto_test_data_dir / "kraken_trades_sample.json"
    with open(file_path, "w") as f:
        json.dump(trades, f, indent=2)

    return file_path

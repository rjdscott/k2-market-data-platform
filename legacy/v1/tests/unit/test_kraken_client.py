"""Unit tests for Kraken WebSocket client.

Tests cover:
- Pair parsing and currency extraction (XBT → BTC normalization)
- Message validation (array format)
- Message conversion to v2 schema
- Side mapping (b → BUY, s → SELL)
- Trade ID generation (timestamp + hash)
- Vendor data mapping
"""

import hashlib
from decimal import Decimal

import pytest

from k2.ingestion.kraken_client import (
    KrakenWebSocketClient,
    convert_kraken_trade_to_v2,
    parse_kraken_pair,
)


class TestPairParsing:
    """Test Kraken pair parsing and currency extraction."""

    def test_parse_xbt_usd(self):
        """Test parsing XBT/USD pair (normalizes XBT → BTC)."""
        base, quote, currency = parse_kraken_pair("XBT/USD")
        assert base == "BTC"  # XBT normalized to BTC
        assert quote == "USD"
        assert currency == "USD"

    def test_parse_eth_usd(self):
        """Test parsing ETH/USD pair."""
        base, quote, currency = parse_kraken_pair("ETH/USD")
        assert base == "ETH"
        assert quote == "USD"
        assert currency == "USD"

    def test_parse_eth_btc(self):
        """Test parsing ETH/BTC pair."""
        base, quote, currency = parse_kraken_pair("ETH/BTC")
        assert base == "ETH"
        assert quote == "BTC"
        assert currency == "BTC"

    def test_parse_dot_eur(self):
        """Test parsing DOT/EUR pair (fiat)."""
        base, quote, currency = parse_kraken_pair("DOT/EUR")
        assert base == "DOT"
        assert quote == "EUR"
        assert currency == "EUR"

    def test_parse_xbt_quote_normalizes(self):
        """Test that XBT in quote position also normalizes to BTC."""
        base, quote, currency = parse_kraken_pair("ETH/XBT")
        assert base == "ETH"
        assert quote == "BTC"  # XBT normalized to BTC
        assert currency == "BTC"

    def test_parse_no_slash_raises(self):
        """Test that pair without '/' raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Kraken pair format"):
            parse_kraken_pair("BTCUSD")

    def test_parse_empty_raises(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Kraken pair format"):
            parse_kraken_pair("")


class TestMessageValidation:
    """Test Kraken message validation."""

    def test_valid_message(self):
        """Test that valid message passes validation."""
        msg = [
            0,  # channelID
            [["5541.20000", "0.15850568", "1534614057.321597", "s", "l", ""]],
            "trade",
            "XBT/USD",
        ]
        # Should not raise
        result = convert_kraken_trade_to_v2(msg)
        assert result is not None

    def test_invalid_type_raises(self):
        """Test that non-list message raises ValueError."""
        msg = {"event": "trade"}
        with pytest.raises(ValueError, match="Invalid Kraken message type"):
            convert_kraken_trade_to_v2(msg)

    def test_invalid_length_raises(self):
        """Test that message with <4 elements raises ValueError."""
        msg = [0, [[]], "trade"]  # Missing pair
        with pytest.raises(ValueError, match="Invalid Kraken message length"):
            convert_kraken_trade_to_v2(msg)

    def test_invalid_channel_raises(self):
        """Test that non-trade channel raises ValueError."""
        msg = [0, [[]], "ticker", "XBT/USD"]  # Wrong channel
        with pytest.raises(ValueError, match="Invalid channel type"):
            convert_kraken_trade_to_v2(msg)

    def test_empty_trades_array_raises(self):
        """Test that empty trades array raises ValueError."""
        msg = [0, [], "trade", "XBT/USD"]  # Empty trades
        with pytest.raises(ValueError, match="Invalid trades array"):
            convert_kraken_trade_to_v2(msg)


class TestSideMapping:
    """Test side determination from Kraken side field."""

    def test_side_s_is_sell(self):
        """Test that 's' maps to SELL."""
        msg = [
            0,
            [["5541.20000", "0.15850568", "1534614057.321597", "s", "l", ""]],
            "trade",
            "XBT/USD",
        ]
        result = convert_kraken_trade_to_v2(msg)
        assert result["side"] == "SELL"

    def test_side_b_is_buy(self):
        """Test that 'b' maps to BUY."""
        msg = [
            0,
            [["5541.20000", "0.15850568", "1534614057.321597", "b", "m", ""]],
            "trade",
            "XBT/USD",
        ]
        result = convert_kraken_trade_to_v2(msg)
        assert result["side"] == "BUY"


class TestMessageConversion:
    """Test complete message conversion to v2 schema."""

    def test_xbt_usd_conversion(self):
        """Test complete conversion of XBT/USD trade."""
        msg = [
            0,  # channelID
            [
                [
                    "5541.20000",  # price
                    "0.15850568",  # volume
                    "1534614057.321597",  # timestamp
                    "s",  # side
                    "l",  # orderType
                    "",  # misc
                ]
            ],
            "trade",
            "XBT/USD",
        ]

        result = convert_kraken_trade_to_v2(msg)

        # Check core fields
        assert result["symbol"] == "BTCUSD"  # XBT → BTC, no slash
        assert result["exchange"] == "KRAKEN"
        assert result["asset_class"] == "crypto"
        assert result["price"] == Decimal("5541.20000")
        assert result["quantity"] == Decimal("0.15850568")
        assert result["currency"] == "USD"
        assert result["side"] == "SELL"

        # Check timestamp conversion (seconds.microseconds → microseconds)
        expected_timestamp = int(1534614057.321597 * 1_000_000)
        assert result["timestamp"] == expected_timestamp

        # Check trade_id format (timestamp + hash)
        assert result["trade_id"].startswith("KRAKEN-1534614057321597-")
        trade_hash = hashlib.sha256(b"XBT/USD1534614057.3215975541.200000.15850568").hexdigest()[:8]
        assert result["trade_id"] == f"KRAKEN-1534614057321597-{trade_hash}"

        # Check message_id is UUID
        assert len(result["message_id"]) == 36  # UUID format

        # Check vendor data
        assert result["vendor_data"]["pair"] == "XBT/USD"
        assert result["vendor_data"]["order_type"] == "l"
        assert result["vendor_data"]["misc"] == ""
        assert result["vendor_data"]["base_asset"] == "BTC"  # Normalized
        assert result["vendor_data"]["quote_asset"] == "USD"
        assert result["vendor_data"]["raw_timestamp"] == "1534614057.321597"

    def test_eth_usd_conversion(self):
        """Test conversion of ETH/USD trade."""
        msg = [
            0,
            [["187.50", "2.5", "1534614100.500000", "b", "m", ""]],
            "trade",
            "ETH/USD",
        ]

        result = convert_kraken_trade_to_v2(msg)

        assert result["symbol"] == "ETHUSD"
        assert result["exchange"] == "KRAKEN"
        assert result["price"] == Decimal("187.50")
        assert result["quantity"] == Decimal("2.5")
        assert result["currency"] == "USD"
        assert result["side"] == "BUY"

        # Check vendor data
        assert result["vendor_data"]["pair"] == "ETH/USD"
        assert result["vendor_data"]["order_type"] == "m"
        assert result["vendor_data"]["base_asset"] == "ETH"
        assert result["vendor_data"]["quote_asset"] == "USD"

    def test_eth_btc_conversion(self):
        """Test conversion of ETH/BTC trade (crypto pair)."""
        msg = [
            0,
            [["0.03250", "10.0", "1534614200.000000", "s", "l", ""]],
            "trade",
            "ETH/BTC",
        ]

        result = convert_kraken_trade_to_v2(msg)

        assert result["symbol"] == "ETHBTC"
        assert result["currency"] == "BTC"
        assert result["side"] == "SELL"

    def test_dot_eur_conversion(self):
        """Test conversion of DOT/EUR trade (fiat)."""
        msg = [
            0,
            [["8.50", "100.0", "1534614300.123456", "b", "m", ""]],
            "trade",
            "DOT/EUR",
        ]

        result = convert_kraken_trade_to_v2(msg)

        assert result["symbol"] == "DOTEUR"
        assert result["currency"] == "EUR"
        assert result["side"] == "BUY"

    def test_timestamp_precision(self):
        """Test that timestamp microseconds are preserved."""
        msg = [
            0,
            [["100.00", "1.0", "1534614057.123456", "b", "l", ""]],
            "trade",
            "ETH/USD",
        ]

        result = convert_kraken_trade_to_v2(msg)

        # Verify microsecond precision
        expected = int(1534614057.123456 * 1_000_000)
        assert result["timestamp"] == expected
        assert result["timestamp"] == 1534614057123456

    def test_trade_id_uniqueness(self):
        """Test that different trades generate different trade_ids."""
        msg1 = [
            0,
            [["100.00", "1.0", "1534614057.123456", "b", "l", ""]],
            "trade",
            "ETH/USD",
        ]
        msg2 = [
            0,
            [["100.00", "1.0", "1534614057.123457", "b", "l", ""]],  # Different timestamp
            "trade",
            "ETH/USD",
        ]

        result1 = convert_kraken_trade_to_v2(msg1)
        result2 = convert_kraken_trade_to_v2(msg2)

        # Different timestamps should generate different trade_ids
        assert result1["trade_id"] != result2["trade_id"]

    def test_order_type_mapping(self):
        """Test that order types are preserved in vendor_data."""
        # Limit order
        msg_limit = [
            0,
            [["100.00", "1.0", "1534614057.000000", "b", "l", ""]],
            "trade",
            "ETH/USD",
        ]
        result_limit = convert_kraken_trade_to_v2(msg_limit)
        assert result_limit["vendor_data"]["order_type"] == "l"

        # Market order
        msg_market = [
            0,
            [["100.00", "1.0", "1534614057.000000", "b", "m", ""]],
            "trade",
            "ETH/USD",
        ]
        result_market = convert_kraken_trade_to_v2(msg_market)
        assert result_market["vendor_data"]["order_type"] == "m"


class TestClientInitialization:
    """Test KrakenWebSocketClient initialization."""

    def test_default_initialization(self):
        """Test client initializes with default config."""
        client = KrakenWebSocketClient(symbols=["BTC/USD", "ETH/USD"])

        assert client.symbols == ["BTC/USD", "ETH/USD"]
        assert client.url == "wss://ws.kraken.com"
        assert client.reconnect_delay == 5
        assert client.max_reconnect_attempts == 10
        assert client.is_running is False

    def test_custom_url_initialization(self):
        """Test client can be initialized with custom URL."""
        custom_url = "wss://custom.kraken.com"
        client = KrakenWebSocketClient(
            symbols=["BTC/USD"],
            url=custom_url,
        )

        assert client.url == custom_url

    def test_failover_urls_initialization(self):
        """Test client can be initialized with failover URLs."""
        primary = "wss://ws.kraken.com"
        failovers = ["wss://ws-auth.kraken.com", "wss://ws2.kraken.com"]

        client = KrakenWebSocketClient(
            symbols=["BTC/USD"],
            url=primary,
            failover_urls=failovers,
        )

        assert client.all_urls == [primary] + failovers
        assert client.current_url_index == 0

    def test_circuit_breaker_enabled(self):
        """Test circuit breaker is enabled by default."""
        client = KrakenWebSocketClient(symbols=["BTC/USD"])

        assert client.circuit_breaker is not None
        assert client.circuit_breaker.name == "kraken_websocket"

    def test_circuit_breaker_disabled(self):
        """Test circuit breaker can be disabled."""
        client = KrakenWebSocketClient(
            symbols=["BTC/USD"],
            enable_circuit_breaker=False,
        )

        assert client.circuit_breaker is None


class TestMemoryLeakDetection:
    """Test memory leak detection scoring."""

    def test_insufficient_samples_returns_zero(self):
        """Test that <10 samples returns 0.0 score."""
        client = KrakenWebSocketClient(symbols=["BTC/USD"])

        # Add only 5 samples
        for i in range(5):
            client.memory_samples.append((float(i), 100_000_000))

        score = client._calculate_memory_leak_score()
        assert score == 0.0

    def test_flat_memory_returns_zero(self):
        """Test that flat memory usage returns 0.0 score."""
        client = KrakenWebSocketClient(symbols=["BTC/USD"])

        # Add 20 samples with same memory
        for i in range(20):
            client.memory_samples.append((float(i * 30), 100_000_000))

        score = client._calculate_memory_leak_score()
        assert score == 0.0

    def test_decreasing_memory_returns_zero(self):
        """Test that decreasing memory returns 0.0 score."""
        client = KrakenWebSocketClient(symbols=["BTC/USD"])

        # Add 20 samples with decreasing memory
        for i in range(20):
            client.memory_samples.append((float(i * 30), 200_000_000 - i * 1_000_000))

        score = client._calculate_memory_leak_score()
        assert score == 0.0

    def test_increasing_memory_returns_positive_score(self):
        """Test that increasing memory returns positive score."""
        client = KrakenWebSocketClient(symbols=["BTC/USD"])

        # Add 20 samples with increasing memory (10MB growth over 10 minutes)
        for i in range(20):
            timestamp = float(i * 30)  # 30s intervals
            memory = 100_000_000 + i * 500_000  # Increasing by ~500KB per sample
            client.memory_samples.append((timestamp, memory))

        score = client._calculate_memory_leak_score()
        assert score > 0.0
        assert score <= 1.0

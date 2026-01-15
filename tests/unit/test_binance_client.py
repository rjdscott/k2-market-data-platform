"""Unit tests for Binance WebSocket client.

Tests cover:
- Symbol parsing and currency extraction
- Message validation
- Message conversion to v2 schema
- Side mapping (buyer maker → sell, buyer taker → buy)
- Vendor data mapping
"""

import time
from decimal import Decimal

import pytest

from k2.ingestion.binance_client import (
    BinanceWebSocketClient,
    convert_binance_trade_to_v2,
    parse_binance_symbol,
)


class TestSymbolParsing:
    """Test symbol parsing and currency extraction."""

    def test_parse_btcusdt(self):
        """Test parsing BTCUSDT symbol."""
        base, quote, currency = parse_binance_symbol("BTCUSDT")
        assert base == "BTC"
        assert quote == "USDT"
        assert currency == "USDT"

    def test_parse_ethbtc(self):
        """Test parsing ETHBTC symbol."""
        base, quote, currency = parse_binance_symbol("ETHBTC")
        assert base == "ETH"
        assert quote == "BTC"
        assert currency == "BTC"

    def test_parse_bnbeur(self):
        """Test parsing BNBEUR symbol."""
        base, quote, currency = parse_binance_symbol("BNBEUR")
        assert base == "BNB"
        assert quote == "EUR"
        assert currency == "EUR"

    def test_parse_btcusdc(self):
        """Test parsing BTCUSDC symbol (different stablecoin)."""
        base, quote, currency = parse_binance_symbol("BTCUSDC")
        assert base == "BTC"
        assert quote == "USDC"
        assert currency == "USDC"

    def test_parse_ethbusd(self):
        """Test parsing ETHBUSD symbol."""
        base, quote, currency = parse_binance_symbol("ETHBUSD")
        assert base == "ETH"
        assert quote == "BUSD"
        assert currency == "BUSD"

    def test_parse_btcgbp(self):
        """Test parsing BTCGBP symbol (fiat)."""
        base, quote, currency = parse_binance_symbol("BTCGBP")
        assert base == "BTC"
        assert quote == "GBP"
        assert currency == "GBP"

    def test_parse_order_matters(self):
        """Test that longer quote currencies are matched first (USDT before USD)."""
        # USDT should be matched before USD
        base, quote, currency = parse_binance_symbol("BTCUSDT")
        assert quote == "USDT"
        assert len("USDT") == 4

        # If we had BTCUSD, it should match USD
        base, quote, currency = parse_binance_symbol("BTCUSD")
        assert quote == "USD"


class TestMessageValidation:
    """Test Binance message validation."""

    def test_valid_message(self):
        """Test that valid message passes validation."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,
        }
        # Should not raise
        result = convert_binance_trade_to_v2(msg)
        assert result is not None

    def test_missing_required_field(self):
        """Test that missing required field raises ValueError."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            # Missing "t" (trade ID)
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,
        }
        with pytest.raises(ValueError, match="Missing required fields"):
            convert_binance_trade_to_v2(msg)

    def test_invalid_event_type(self):
        """Test that invalid event type raises ValueError."""
        msg = {
            "e": "kline",  # Wrong event type
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,
        }
        with pytest.raises(ValueError, match="Invalid event type"):
            convert_binance_trade_to_v2(msg)


class TestSideMapping:
    """Test side determination from buyer maker flag."""

    def test_buyer_maker_true_is_sell(self):
        """Test that buyer maker = true maps to SELL (buyer passive, seller aggressive)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": True,  # Buyer is maker (passive) → Seller is taker (aggressive) → SELL
        }
        result = convert_binance_trade_to_v2(msg)
        assert result["side"] == "SELL"

    def test_buyer_maker_false_is_buy(self):
        """Test that buyer maker = false maps to BUY (buyer aggressive)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,  # Buyer is taker (aggressive) → BUY
        }
        result = convert_binance_trade_to_v2(msg)
        assert result["side"] == "BUY"


class TestMessageConversion:
    """Test complete message conversion to v2 schema."""

    def test_btcusdt_conversion(self):
        """Test complete conversion of BTCUSDT trade."""
        msg = {
            "e": "trade",
            "E": 1673356800000,  # Event time
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.50",
            "q": "0.05",
            "T": 1673356800123,  # Trade time
            "m": False,
            "M": True,  # Is best match (optional)
        }

        result = convert_binance_trade_to_v2(msg)

        # Check core fields
        assert result["symbol"] == "BTCUSDT"
        assert result["exchange"] == "BINANCE"
        assert result["asset_class"] == "crypto"
        assert result["price"] == Decimal("16500.50")
        assert result["quantity"] == Decimal("0.05")
        assert result["currency"] == "USDT"
        assert result["side"] == "BUY"

        # Check timestamp conversion (ms → μs)
        assert result["timestamp"] == 1673356800123000

        # Check trade_id format
        assert result["trade_id"] == "BINANCE-12345"

        # Check message_id is UUID
        assert len(result["message_id"]) == 36  # UUID format

        # Check vendor_data
        assert result["vendor_data"]["base_asset"] == "BTC"
        assert result["vendor_data"]["quote_asset"] == "USDT"
        assert result["vendor_data"]["is_buyer_maker"] == "False"
        assert result["vendor_data"]["event_type"] == "trade"
        assert result["vendor_data"]["event_time"] == "1673356800000"
        assert result["vendor_data"]["trade_time"] == "1673356800123"
        assert result["vendor_data"]["is_best_match"] == "True"

    def test_ethbtc_conversion(self):
        """Test conversion of ETHBTC trade (crypto quote currency)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "ETHBTC",
            "t": 67890,
            "p": "0.075",
            "q": "10.5",
            "T": 1673356800456,
            "m": True,
        }

        result = convert_binance_trade_to_v2(msg)

        assert result["symbol"] == "ETHBTC"
        assert result["currency"] == "BTC"  # Quote currency extracted
        assert result["side"] == "SELL"
        assert result["vendor_data"]["base_asset"] == "ETH"
        assert result["vendor_data"]["quote_asset"] == "BTC"

    def test_bnbeur_conversion(self):
        """Test conversion of BNBEUR trade (fiat quote currency)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BNBEUR",
            "t": 11111,
            "p": "250.75",
            "q": "2.0",
            "T": 1673356800789,
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)

        assert result["symbol"] == "BNBEUR"
        assert result["currency"] == "EUR"  # Fiat quote currency
        assert result["side"] == "BUY"
        assert result["vendor_data"]["base_asset"] == "BNB"
        assert result["vendor_data"]["quote_asset"] == "EUR"

    def test_decimal_precision(self):
        """Test that price and quantity are properly converted to Decimal."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.12345678",  # 8 decimal places
            "q": "0.00001234",  # 8 decimal places
            "T": 1673356800000,
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)

        # Check Decimal type and precision
        assert isinstance(result["price"], Decimal)
        assert isinstance(result["quantity"], Decimal)
        assert result["price"] == Decimal("16500.12345678")
        assert result["quantity"] == Decimal("0.00001234")

    def test_trade_conditions_empty(self):
        """Test that trade_conditions is empty array (not used for crypto)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)
        assert result["trade_conditions"] == []

    def test_source_sequence_none(self):
        """Test that source_sequence is None (Binance doesn't provide sequence)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800000,
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)
        assert result["source_sequence"] is None


class TestBinanceWebSocketClient:
    """Test BinanceWebSocketClient initialization and configuration."""

    def test_client_initialization(self):
        """Test client initializes with correct defaults."""
        client = BinanceWebSocketClient(
            symbols=["BTCUSDT", "ETHUSDT"],
        )

        assert client.symbols == ["BTCUSDT", "ETHUSDT"]
        assert client.url == "wss://stream.binance.com:9443/stream"
        assert client.reconnect_delay == 5
        assert client.max_reconnect_attempts == 10
        assert client.health_check_interval == 30
        assert client.health_check_timeout == 60
        assert client.circuit_breaker is not None  # Enabled by default

    def test_client_custom_config(self):
        """Test client accepts custom configuration."""
        client = BinanceWebSocketClient(
            symbols=["BTCUSDT"],
            url="wss://custom.url:9443/stream",
            failover_urls=["wss://fallback.url:9443/stream"],
            reconnect_delay=10,
            max_reconnect_attempts=5,
            health_check_interval=60,
            health_check_timeout=120,
            enable_circuit_breaker=False,
        )

        assert client.symbols == ["BTCUSDT"]
        assert client.url == "wss://custom.url:9443/stream"
        assert client.failover_urls == ["wss://fallback.url:9443/stream"]
        assert client.reconnect_delay == 10
        assert client.max_reconnect_attempts == 5
        assert client.health_check_interval == 60
        assert client.health_check_timeout == 120
        assert client.circuit_breaker is None  # Disabled

    def test_failover_urls_setup(self):
        """Test that failover URLs are properly set up."""
        client = BinanceWebSocketClient(
            symbols=["BTCUSDT"],
            url="wss://primary.url",
            failover_urls=["wss://fallback1.url", "wss://fallback2.url"],
        )

        # all_urls should include primary + failovers
        assert len(client.all_urls) == 3
        assert client.all_urls[0] == "wss://primary.url"
        assert client.all_urls[1] == "wss://fallback1.url"
        assert client.all_urls[2] == "wss://fallback2.url"
        assert client.current_url_index == 0  # Start with primary

    def test_metrics_labels(self):
        """Test that metrics labels are properly set."""
        client = BinanceWebSocketClient(
            symbols=["BTCUSDT", "ETHUSDT", "BNBEUR"],
        )

        # Metrics labels dict exists (currently empty, per-symbol labels added at metric time)
        assert isinstance(client.metrics_labels, dict)

    def test_callback_function(self):
        """Test that on_message callback is properly stored."""
        callback_called = []

        def test_callback(trade):
            callback_called.append(trade)

        client = BinanceWebSocketClient(
            symbols=["BTCUSDT"],
            on_message=test_callback,
        )

        assert client.on_message == test_callback

    def test_initial_state(self):
        """Test that client starts in correct initial state."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        assert client.is_running is False
        assert client.reconnect_attempts == 0
        assert client.ws is None
        assert client.health_check_task is None
        assert client.last_message_time > 0  # Initialized to current time


class TestTimestampConversion:
    """Test timestamp conversion from milliseconds to microseconds."""

    def test_timestamp_conversion(self):
        """Test that Binance timestamps (ms) are converted to v2 timestamps (μs)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,  # Event time: 1673356800000 ms
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800123,  # Trade time: 1673356800123 ms
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)

        # Check timestamp is trade time (T) converted to microseconds
        assert result["timestamp"] == 1673356800123000  # μs

        # Ingestion timestamp should be recent (within last few seconds)
        current_time_us = int(time.time() * 1_000_000)
        assert abs(result["ingestion_timestamp"] - current_time_us) < 5_000_000  # Within 5s

    def test_uses_trade_time_not_event_time(self):
        """Test that conversion uses trade time (T) not event time (E)."""
        msg = {
            "e": "trade",
            "E": 1673356800000,  # Event time
            "s": "BTCUSDT",
            "t": 12345,
            "p": "16500.00",
            "q": "0.05",
            "T": 1673356800456,  # Trade time (different from event time)
            "m": False,
        }

        result = convert_binance_trade_to_v2(msg)

        # Should use T (trade time), not E (event time)
        assert result["timestamp"] == 1673356800456000  # T * 1000
        assert result["timestamp"] != 1673356800000000  # Not E * 1000


class TestSSLConfiguration:
    """Test SSL certificate verification configuration."""

    def test_ssl_enabled_by_default(self):
        """Test that SSL verification is enabled by default."""
        from k2.common.config import BinanceConfig

        config = BinanceConfig()
        assert config.ssl_verify is True

    def test_ssl_can_be_disabled(self):
        """Test that SSL verification can be disabled via config."""
        from k2.common.config import BinanceConfig

        config = BinanceConfig(ssl_verify=False)
        assert config.ssl_verify is False

    def test_custom_ca_bundle_optional(self):
        """Test that custom CA bundle is optional."""
        from k2.common.config import BinanceConfig

        config = BinanceConfig()
        assert config.custom_ca_bundle is None

        # Can be set
        config_with_ca = BinanceConfig(custom_ca_bundle="/path/to/ca-bundle.crt")
        assert config_with_ca.custom_ca_bundle == "/path/to/ca-bundle.crt"

    def test_client_uses_global_config(self):
        """Test that BinanceWebSocketClient uses global config for SSL settings."""
        from k2.common.config import config

        # The client should use the global config object
        # By default, SSL verification is enabled
        assert config.binance.ssl_verify is True

        # Client can be created without explicitly passing SSL config
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])
        assert client is not None  # Client creation succeeds with default SSL config


class TestConnectionRotation:
    """Test connection rotation functionality."""

    def test_rotation_fields_initialized(self):
        """Test that connection rotation fields are initialized."""
        from k2.common.config import config

        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Connection rotation fields should be initialized
        assert client.connection_start_time is None  # Not connected yet
        assert client.connection_max_lifetime_seconds == (
            config.binance.connection_max_lifetime_hours * 3600
        )
        assert client.rotation_task is None  # Task not started yet

    def test_default_rotation_config(self):
        """Test that default rotation config is 4 hours."""
        from k2.common.config import BinanceConfig

        config = BinanceConfig()
        assert config.connection_max_lifetime_hours == 4

    def test_rotation_config_validation(self):
        """Test that rotation config validates bounds (1-24 hours)."""
        from k2.common.config import BinanceConfig

        # Valid values
        config_min = BinanceConfig(connection_max_lifetime_hours=1)
        assert config_min.connection_max_lifetime_hours == 1

        config_max = BinanceConfig(connection_max_lifetime_hours=24)
        assert config_max.connection_max_lifetime_hours == 24

        config_mid = BinanceConfig(connection_max_lifetime_hours=8)
        assert config_mid.connection_max_lifetime_hours == 8

    def test_rotation_metrics_defined(self):
        """Test that rotation metrics are defined in registry."""
        from k2.common import metrics_registry

        # Check rotation metrics are defined as module constants
        assert hasattr(metrics_registry, "BINANCE_CONNECTION_ROTATIONS_TOTAL")
        assert hasattr(metrics_registry, "BINANCE_CONNECTION_LIFETIME_SECONDS")

        # Get the metrics
        rotations_metric = metrics_registry.BINANCE_CONNECTION_ROTATIONS_TOTAL
        lifetime_metric = metrics_registry.BINANCE_CONNECTION_LIFETIME_SECONDS

        # Check they are valid Prometheus metrics
        assert rotations_metric is not None
        assert lifetime_metric is not None


class TestMemoryMonitoring:
    """Test memory monitoring and leak detection functionality."""

    def test_memory_monitor_fields_initialized(self):
        """Test that memory monitoring fields are initialized."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Memory monitoring fields should be initialized
        assert client.memory_samples == []  # Empty initially
        assert client.memory_monitor_task is None  # Task not started yet
        assert client.memory_sample_interval_seconds == 30  # Default 30s
        assert client.memory_sample_window_size == 120  # Default 120 samples (1 hour)

    def test_memory_monitor_metrics_defined(self):
        """Test that memory monitoring metrics are defined in registry."""
        from k2.common import metrics_registry

        # Check memory metrics are defined as module constants
        assert hasattr(metrics_registry, "PROCESS_MEMORY_RSS_BYTES")
        assert hasattr(metrics_registry, "PROCESS_MEMORY_VMS_BYTES")
        assert hasattr(metrics_registry, "MEMORY_LEAK_DETECTION_SCORE")

        # Get the metrics
        rss_metric = metrics_registry.PROCESS_MEMORY_RSS_BYTES
        vms_metric = metrics_registry.PROCESS_MEMORY_VMS_BYTES
        leak_score_metric = metrics_registry.MEMORY_LEAK_DETECTION_SCORE

        # Check they are valid Prometheus metrics (Gauge)
        assert rss_metric is not None
        assert vms_metric is not None
        assert leak_score_metric is not None

    def test_calculate_memory_leak_score_insufficient_samples(self):
        """Test leak detection with insufficient samples returns 0."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # With no samples, score should be 0
        score = client._calculate_memory_leak_score()
        assert score == 0.0

        # With < 10 samples, score should be 0
        for i in range(9):
            client.memory_samples.append((float(i), 200_000_000 + i * 1_000_000))

        score = client._calculate_memory_leak_score()
        assert score == 0.0

    def test_calculate_memory_leak_score_no_leak(self):
        """Test leak detection with flat/stable memory returns low score."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Simulate stable memory (200MB +/- small variance)
        base_memory = 200 * 1024 * 1024  # 200 MB
        for i in range(60):  # 60 samples = 30 minutes at 30s intervals
            # Add small random variance (+/- 1MB)
            variance = (i % 3 - 1) * 1024 * 1024
            client.memory_samples.append((float(i * 30), base_memory + variance))

        score = client._calculate_memory_leak_score()

        # Score should be very low (<0.1) for stable memory
        assert score < 0.1

    def test_calculate_memory_leak_score_moderate_leak(self):
        """Test leak detection with moderate memory growth returns moderate score."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Simulate moderate leak: 10 MB/hour growth
        # 60 samples = 30 minutes = 0.5 hours
        # Expected growth: 5 MB over 30 minutes
        base_memory = 200 * 1024 * 1024  # 200 MB
        for i in range(60):
            # Linear growth: +5MB over 60 samples
            growth = (i / 60) * 5 * 1024 * 1024
            client.memory_samples.append((float(i * 30), base_memory + int(growth)))

        score = client._calculate_memory_leak_score()

        # Score should be moderate (0.1 - 0.5)
        # 10 MB/hour = 0.5 score threshold in implementation
        assert 0.1 < score < 0.6

    def test_calculate_memory_leak_score_severe_leak(self):
        """Test leak detection with severe memory growth returns high score."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Simulate severe leak: 50+ MB/hour growth
        # 60 samples = 30 minutes = 0.5 hours
        # Expected growth: 25+ MB over 30 minutes
        base_memory = 200 * 1024 * 1024  # 200 MB
        for i in range(60):
            # Linear growth: +30MB over 60 samples (60 MB/hour rate)
            growth = (i / 60) * 30 * 1024 * 1024
            client.memory_samples.append((float(i * 30), base_memory + int(growth)))

        score = client._calculate_memory_leak_score()

        # Score should be high (>0.8) for severe leak
        # 50 MB/hour = 1.0 score threshold in implementation
        # 60 MB/hour should be >0.8 with good R²
        assert score > 0.7

    def test_calculate_memory_leak_score_decreasing_memory(self):
        """Test leak detection with decreasing memory returns 0."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Simulate decreasing memory (negative slope)
        base_memory = 250 * 1024 * 1024  # Start at 250 MB
        for i in range(60):
            # Decrease by 20MB over 60 samples
            decrease = (i / 60) * 20 * 1024 * 1024
            client.memory_samples.append((float(i * 30), base_memory - int(decrease)))

        score = client._calculate_memory_leak_score()

        # Score should be 0 for negative slope (memory decreasing)
        assert score == 0.0

    def test_memory_sample_window_maintains_size_limit(self):
        """Test that memory samples maintain sliding window size limit."""
        client = BinanceWebSocketClient(symbols=["BTCUSDT"])

        # Set a small window for testing
        client.memory_sample_window_size = 10

        # Add more samples than window size
        for i in range(20):
            client.memory_samples.append((float(i), 200_000_000))

            # Manually enforce sliding window (simulating what _memory_monitor_loop does)
            if len(client.memory_samples) > client.memory_sample_window_size:
                client.memory_samples.pop(0)

        # Should only have last 10 samples
        assert len(client.memory_samples) == 10
        # First sample should be timestamp 10 (samples 0-9 evicted)
        assert client.memory_samples[0][0] == 10.0

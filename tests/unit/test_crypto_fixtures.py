"""Tests for crypto fixtures to verify they generate valid data."""

import pytest
from decimal import Decimal


class TestCryptoFixtures:
    """Verify crypto fixtures generate valid test data."""

    def test_binance_raw_trade_structure(self, sample_binance_raw_trade):
        """Test Binance raw trade has all required fields."""
        trade = sample_binance_raw_trade

        # Verify all required fields exist
        assert "event_type" in trade
        assert "event_time_ms" in trade
        assert "symbol" in trade
        assert "trade_id" in trade
        assert "price" in trade
        assert "quantity" in trade
        assert "trade_time_ms" in trade
        assert "is_buyer_maker" in trade
        assert "ingestion_timestamp" in trade

        # Verify data types
        assert trade["event_type"] == "trade"
        assert isinstance(trade["event_time_ms"], int)
        assert isinstance(trade["symbol"], str)
        assert isinstance(trade["trade_id"], int)
        assert isinstance(trade["price"], str)
        assert isinstance(trade["quantity"], str)
        assert isinstance(trade["is_buyer_maker"], bool)

        # Verify values are valid
        assert float(trade["price"]) > 0
        assert float(trade["quantity"]) > 0
        assert trade["symbol"].endswith("USDT")

    def test_kraken_raw_trade_structure(self, sample_kraken_raw_trade):
        """Test Kraken raw trade has all required fields."""
        trade = sample_kraken_raw_trade

        # Verify all required fields exist
        assert "symbol" in trade
        assert "price" in trade
        assert "quantity" in trade
        assert "trade_time_seconds" in trade
        assert "side" in trade
        assert "order_type" in trade
        assert "ingestion_timestamp" in trade

        # Verify data types
        assert isinstance(trade["symbol"], str)
        assert isinstance(trade["price"], str)
        assert isinstance(trade["quantity"], str)
        assert isinstance(trade["trade_time_seconds"], float)
        assert isinstance(trade["side"], str)
        assert isinstance(trade["order_type"], str)

        # Verify values are valid
        assert float(trade["price"]) > 0
        assert float(trade["quantity"]) > 0
        assert "/" in trade["symbol"]
        assert trade["side"] in ["buy", "sell"]
        assert trade["order_type"] in ["market", "limit"]

    def test_v2_unified_trade_structure(self, sample_v2_unified_trade):
        """Test V2 unified trade has all 15 required fields."""
        trade = sample_v2_unified_trade

        # Verify all 15 required V2 fields exist
        required_fields = [
            "message_id",
            "exchange",
            "symbol",
            "base_currency",
            "quote_currency",
            "price",
            "quantity",
            "trade_value_usd",
            "timestamp",
            "side",
            "trade_id",
            "is_market_maker",
            "ingestion_timestamp",
            "validation_timestamp",
            "schema_id",
        ]

        for field in required_fields:
            assert field in trade, f"Missing required field: {field}"

        # Verify exchange is valid
        assert trade["exchange"] in ["binance", "kraken"]

        # Verify numeric fields are positive
        assert trade["price"] > 0
        assert trade["quantity"] > 0
        assert trade["trade_value_usd"] > 0

        # Verify calculated field
        calculated_value = trade["price"] * trade["quantity"]
        assert abs(calculated_value - trade["trade_value_usd"]) < 0.01

    def test_binance_trade_sequence(self, binance_trade_sequence):
        """Test Binance trade sequence generates multiple trades."""
        assert len(binance_trade_sequence) == 50
        assert all(trade["symbol"] == "BTCUSDT" for trade in binance_trade_sequence)

        # Verify timestamps are ordered
        timestamps = [trade["trade_time_ms"] for trade in binance_trade_sequence]
        assert timestamps == sorted(timestamps), "Trades should be chronologically ordered"

    def test_kraken_trade_sequence(self, kraken_trade_sequence):
        """Test Kraken trade sequence generates multiple trades."""
        assert len(kraken_trade_sequence) == 50
        assert all(trade["symbol"] == "BTC/USD" for trade in kraken_trade_sequence)

        # Verify timestamps are ordered
        timestamps = [trade["trade_time_seconds"] for trade in kraken_trade_sequence]
        assert timestamps == sorted(timestamps), "Trades should be chronologically ordered"

    def test_v2_mixed_trades(self, v2_mixed_trades):
        """Test V2 mixed trades combines Binance and Kraken."""
        assert len(v2_mixed_trades["binance_trades"]) == 25
        assert len(v2_mixed_trades["kraken_trades"]) == 25
        assert len(v2_mixed_trades["all_trades"]) == 50
        assert v2_mixed_trades["total_count"] == 50

        # Verify exchanges are correct
        binance_count = sum(1 for t in v2_mixed_trades["all_trades"] if t["exchange"] == "binance")
        kraken_count = sum(1 for t in v2_mixed_trades["all_trades"] if t["exchange"] == "kraken")

        assert binance_count == 25
        assert kraken_count == 25

        # Verify combined list is sorted by timestamp
        timestamps = [trade["timestamp"] for trade in v2_mixed_trades["all_trades"]]
        assert timestamps == sorted(timestamps), "Mixed trades should be chronologically ordered"

    def test_invalid_trade_records(self, invalid_trade_records):
        """Test invalid trade records for DLQ testing."""
        assert "missing_price" in invalid_trade_records
        assert "negative_price" in invalid_trade_records
        assert "zero_quantity" in invalid_trade_records
        assert "invalid_symbol" in invalid_trade_records

        # Verify missing_price doesn't have price field
        assert "price" not in invalid_trade_records["missing_price"]

        # Verify negative price
        assert float(invalid_trade_records["negative_price"]["price"]) < 0

        # Verify zero quantity
        assert float(invalid_trade_records["zero_quantity"]["quantity"]) == 0

        # Verify invalid symbol
        assert invalid_trade_records["invalid_symbol"]["symbol"] == ""

    def test_price_ranges_realistic(self, crypto_data_factory):
        """Test that generated prices fall within realistic ranges."""
        # Test BTC prices
        for _ in range(10):
            trade = crypto_data_factory.create_binance_raw_trade(symbol="BTCUSDT")
            price = float(trade["price"])
            assert 30000 <= price <= 70000, f"BTC price {price} outside realistic range"

        # Test ETH prices
        for _ in range(10):
            trade = crypto_data_factory.create_binance_raw_trade(symbol="ETHUSDT")
            price = float(trade["price"])
            assert 1500 <= price <= 4500, f"ETH price {price} outside realistic range"

    def test_quantity_inversely_related_to_price(self, crypto_data_factory):
        """Test that higher priced assets have smaller quantities."""
        # BTC (high price) should have small quantities
        btc_trade = crypto_data_factory.create_binance_raw_trade(symbol="BTCUSDT")
        btc_quantity = float(btc_trade["quantity"])

        # DOGE (low price) should have large quantities
        doge_trade = crypto_data_factory.create_binance_raw_trade(symbol="DOGEUSDT")
        doge_quantity = float(doge_trade["quantity"])

        assert btc_quantity < 1.0, "BTC quantities should be < 1.0"
        assert doge_quantity > 10.0, "DOGE quantities should be > 10.0"

    def test_trade_id_uniqueness(self, binance_trade_sequence):
        """Test that trade IDs are unique within a sequence."""
        trade_ids = [trade["trade_id"] for trade in binance_trade_sequence]
        assert len(trade_ids) == len(set(trade_ids)), "Trade IDs should be unique"

    def test_message_id_format(self, v2_mixed_trades):
        """Test V2 message_id follows expected format."""
        for trade in v2_mixed_trades["all_trades"]:
            message_id = trade["message_id"]
            assert isinstance(message_id, str)
            assert "-" in message_id, "Message ID should contain hyphens"

            # Should start with exchange name
            assert message_id.startswith(("binance", "kraken"))

    def test_currency_name_normalization(self, sample_v2_unified_trade):
        """Test that currency codes are normalized to full names."""
        trade = sample_v2_unified_trade

        # Currency name mappings
        valid_base_currencies = [
            "Bitcoin",
            "Ethereum",
            "Binance Coin",
            "Cardano",
            "Solana",
            "Ripple",
            "Polkadot",
            "Dogecoin",
        ]

        assert trade["base_currency"] in valid_base_currencies
        assert trade["quote_currency"] == "US Dollar"

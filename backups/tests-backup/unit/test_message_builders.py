"""Unit tests-backup for v2 message builders.

Tests ensure v2 messages conform to schema requirements:
- UUID message_id generation
- Timestamp conversion (datetime → microseconds)
- Decimal precision handling
- Enum validation (asset_class, side)
- vendor_data mapping
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from k2.ingestion.message_builders import build_quote_v2, build_trade_v2, convert_v1_to_v2_trade


@pytest.mark.unit
class TestTradeV2Builder:
    """Test v2 trade message builder."""

    def test_build_trade_v2_minimal(self):
        """Build v2 trade with minimal required fields."""
        trade = build_trade_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=datetime(2026, 1, 12, 10, 30, 0, tzinfo=UTC),
            price=Decimal("45.67"),
            quantity=Decimal("1000"),
        )

        # Check required fields
        assert trade["symbol"] == "BHP"
        assert trade["exchange"] == "ASX"
        assert trade["asset_class"] == "equities"
        assert isinstance(trade["price"], Decimal)
        assert trade["price"] == Decimal("45.67")
        assert isinstance(trade["quantity"], Decimal)
        assert trade["quantity"] == Decimal("1000")

        # Check auto-generated fields
        assert "message_id" in trade
        assert len(trade["message_id"]) == 36  # UUID format
        assert "trade_id" in trade
        assert trade["trade_id"].startswith("ASX-")

        # Check defaults
        assert trade["currency"] == "USD"  # Default
        assert trade["side"] == "UNKNOWN"  # Default
        assert trade["trade_conditions"] == []
        assert trade["source_sequence"] is None
        assert trade["platform_sequence"] is None
        assert trade["vendor_data"] is None

        # Check timestamps
        assert isinstance(trade["timestamp"], int)
        assert isinstance(trade["ingestion_timestamp"], int)
        assert trade["timestamp"] > 0
        assert trade["ingestion_timestamp"] > 0

    def test_build_trade_v2_with_all_fields(self):
        """Build v2 trade with all optional fields."""
        vendor_data = {"company_id": "123", "qualifiers": "0", "venue": "X"}

        trade = build_trade_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=datetime(2026, 1, 12, 10, 30, 0, tzinfo=UTC),
            price=Decimal("45.67"),
            quantity=Decimal("1000"),
            currency="AUD",
            side="BUY",
            trade_id="ASX-123456",
            message_id="550e8400-e29b-41d4-a716-446655440000",
            trade_conditions=["0"],
            source_sequence=12345,
            platform_sequence=67890,
            vendor_data=vendor_data,
        )

        # Check all fields
        assert trade["currency"] == "AUD"
        assert trade["side"] == "BUY"
        assert trade["trade_id"] == "ASX-123456"
        assert trade["message_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert trade["trade_conditions"] == ["0"]
        assert trade["source_sequence"] == 12345
        assert trade["platform_sequence"] == 67890
        assert trade["vendor_data"] == vendor_data
        assert trade["vendor_data"]["company_id"] == "123"

    def test_build_trade_v2_crypto(self):
        """Build v2 crypto trade with fractional quantity."""
        trade = build_trade_v2(
            symbol="BTCUSDT",
            exchange="BINANCE",
            asset_class="crypto",
            timestamp=datetime.utcnow(),
            price=Decimal("16500.00"),
            quantity=Decimal("0.05"),
            currency="USDT",
            side="SELL",
            vendor_data={"is_buyer_maker": "true", "event_type": "trade"},
        )

        assert trade["symbol"] == "BTCUSDT"
        assert trade["exchange"] == "BINANCE"
        assert trade["asset_class"] == "crypto"
        assert trade["currency"] == "USDT"
        assert trade["side"] == "SELL"
        assert trade["quantity"] == Decimal("0.05")
        assert trade["vendor_data"]["is_buyer_maker"] == "true"

    def test_build_trade_v2_invalid_asset_class(self):
        """Invalid asset_class should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid asset_class"):
            build_trade_v2(
                symbol="BHP",
                exchange="ASX",
                asset_class="invalid_class",
                timestamp=datetime.utcnow(),
                price=Decimal("45.67"),
                quantity=Decimal("1000"),
            )

    def test_build_trade_v2_invalid_side(self):
        """Invalid side should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            build_trade_v2(
                symbol="BHP",
                exchange="ASX",
                asset_class="equities",
                timestamp=datetime.utcnow(),
                price=Decimal("45.67"),
                quantity=Decimal("1000"),
                side="INVALID_SIDE",
            )

    def test_build_trade_v2_timestamp_conversion(self):
        """Test timestamp conversion from datetime to microseconds."""
        dt = datetime(2026, 1, 12, 10, 30, 0, tzinfo=UTC)
        expected_micros = int(dt.timestamp() * 1_000_000)

        trade = build_trade_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=dt,
            price=Decimal("45.67"),
            quantity=Decimal("1000"),
        )

        assert trade["timestamp"] == expected_micros

    def test_build_trade_v2_timestamp_from_millis(self):
        """Test timestamp conversion from milliseconds to microseconds."""
        timestamp_millis = 1672531199900  # milliseconds
        expected_micros = 1672531199900000  # microseconds

        trade = build_trade_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=timestamp_millis,
            price=Decimal("45.67"),
            quantity=Decimal("1000"),
        )

        assert trade["timestamp"] == expected_micros

    def test_build_trade_v2_price_quantity_from_float(self):
        """Test Decimal conversion from float/string."""
        trade = build_trade_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=datetime.utcnow(),
            price=45.67,  # float
            quantity="1000",  # string
        )

        assert isinstance(trade["price"], Decimal)
        assert isinstance(trade["quantity"], Decimal)
        assert trade["price"] == Decimal("45.67")
        assert trade["quantity"] == Decimal("1000")


@pytest.mark.unit
class TestQuoteV2Builder:
    """Test v2 quote message builder."""

    def test_build_quote_v2_minimal(self):
        """Build v2 quote with minimal required fields."""
        quote = build_quote_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=datetime(2026, 1, 12, 10, 30, 0, tzinfo=UTC),
            bid_price=Decimal("45.60"),
            bid_quantity=Decimal("1000"),
            ask_price=Decimal("45.70"),
            ask_quantity=Decimal("500"),
        )

        # Check required fields
        assert quote["symbol"] == "BHP"
        assert quote["exchange"] == "ASX"
        assert quote["asset_class"] == "equities"
        assert quote["bid_price"] == Decimal("45.60")
        assert quote["bid_quantity"] == Decimal("1000")
        assert quote["ask_price"] == Decimal("45.70")
        assert quote["ask_quantity"] == Decimal("500")

        # Check auto-generated fields
        assert "message_id" in quote
        assert "quote_id" in quote
        assert quote["quote_id"].startswith("ASX-")

        # Check defaults
        assert quote["currency"] == "USD"
        assert quote["source_sequence"] is None
        assert quote["platform_sequence"] is None
        assert quote["vendor_data"] is None

    def test_build_quote_v2_with_currency(self):
        """Build v2 quote with custom currency."""
        quote = build_quote_v2(
            symbol="BHP",
            exchange="ASX",
            asset_class="equities",
            timestamp=datetime.utcnow(),
            bid_price=Decimal("45.60"),
            bid_quantity=Decimal("1000"),
            ask_price=Decimal("45.70"),
            ask_quantity=Decimal("500"),
            currency="AUD",
        )

        assert quote["currency"] == "AUD"


@pytest.mark.unit
class TestV1ToV2Converter:
    """Test v1 to v2 trade conversion."""

    def test_convert_v1_to_v2_trade(self):
        """Convert v1 trade to v2 format."""
        v1_record = {
            "symbol": "BHP",
            "company_id": 123,
            "exchange": "ASX",
            "exchange_timestamp": 1672531199900,  # millis
            "price": Decimal("45.67"),
            "volume": 1000,
            "qualifiers": 0,
            "venue": "X",
            "buyer_id": None,
            "ingestion_timestamp": 1672531200000,
            "sequence_number": 12345,
        }

        v2_record = convert_v1_to_v2_trade(v1_record, default_currency="AUD")

        # Check field mappings
        assert v2_record["symbol"] == "BHP"
        assert v2_record["exchange"] == "ASX"
        assert v2_record["asset_class"] == "equities"
        assert v2_record["quantity"] == Decimal("1000")  # volume → quantity
        assert v2_record["price"] == Decimal("45.67")
        assert v2_record["currency"] == "AUD"
        assert v2_record["source_sequence"] == 12345

        # Check timestamp conversion (millis → micros)
        assert v2_record["timestamp"] == 1672531199900000

        # Check vendor_data mapping
        assert v2_record["vendor_data"] is not None
        assert v2_record["vendor_data"]["company_id"] == "123"
        assert v2_record["vendor_data"]["qualifiers"] == "0"
        assert v2_record["vendor_data"]["venue"] == "X"

        # Check auto-generated fields
        assert "message_id" in v2_record
        assert "trade_id" in v2_record

    def test_convert_v1_to_v2_crypto_exchange(self):
        """Convert v1 crypto trade determines asset_class from exchange."""
        v1_record = {
            "symbol": "BTCUSDT",
            "company_id": 0,
            "exchange": "BINANCE",
            "exchange_timestamp": 1672531199900,
            "price": Decimal("16500.00"),
            "volume": 50,  # This would be 0.05 BTC in v2
            "qualifiers": 0,
            "venue": "",
            "buyer_id": None,
            "ingestion_timestamp": 1672531200000,
            "sequence_number": None,
        }

        v2_record = convert_v1_to_v2_trade(v1_record)

        # Should detect crypto from exchange name
        assert v2_record["exchange"] == "BINANCE"
        assert v2_record["asset_class"] == "crypto"

"""Data validation tests using Pandera.

Tests data quality constraints for market data records to ensure:
- Price and quantity constraints (> 0)
- Timestamp ordering and validity
- Symbol format validation
- Decimal precision constraints
- Required field presence
- Enum value validation
- Cross-field validation (e.g., bid < ask)

Run: pytest tests/unit/test_data_validation.py -v
"""

import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.pandas import Check, Column, DataFrameSchema

# ==============================================================================
# Trade V2 Schema Definition
# ==============================================================================


TRADE_V2_SCHEMA = DataFrameSchema(
    columns={
        "message_id": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=255),
                Check(lambda s: s.notna().all(), error="message_id cannot be null"),
            ],
            nullable=False,
            description="Unique message identifier (UUID v4)",
        ),
        "trade_id": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=255),
                Check(lambda s: s.notna().all(), error="trade_id cannot be null"),
            ],
            nullable=False,
            description="Exchange-specific trade identifier",
        ),
        "symbol": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=50),
                Check.str_matches(r"^[A-Z0-9]+$"),  # Alphanumeric uppercase only
                Check(lambda s: s.notna().all(), error="symbol cannot be null"),
            ],
            nullable=False,
            description="Trading symbol (e.g., BHP, BTCUSDT)",
        ),
        "exchange": Column(
            str,
            checks=[
                Check.isin(["ASX", "BINANCE", "NYSE", "CME", "NASDAQ"]),
                Check(lambda s: s.notna().all(), error="exchange cannot be null"),
            ],
            nullable=False,
            description="Exchange code",
        ),
        "asset_class": Column(
            str,
            checks=[
                Check.isin(["equities", "crypto", "futures", "options"]),
                Check(lambda s: s.notna().all(), error="asset_class cannot be null"),
            ],
            nullable=False,
            description="Asset class categorization",
        ),
        "timestamp": Column(
            pd.Timestamp,
            checks=[
                Check(
                    lambda ts: (ts >= pd.Timestamp("2000-01-01")).all(),
                    error="Timestamps must be >= 2000-01-01",
                ),
                Check(
                    lambda ts: (ts <= pd.Timestamp.now() + pd.Timedelta(minutes=5)).all(),
                    error="Timestamps cannot be > 5 minutes in future",
                ),
            ],
            nullable=False,
            description="Exchange-reported trade timestamp",
        ),
        "price": Column(
            float,
            checks=[
                Check.greater_than(0, error="Price must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000, error="Price exceeds max"),
            ],
            nullable=False,
            description="Trade execution price",
        ),
        "quantity": Column(
            float,
            checks=[
                Check.greater_than(0, error="Quantity must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000_000, error="Quantity exceeds max"),
            ],
            nullable=False,
            description="Trade quantity",
        ),
        "currency": Column(
            str,
            checks=[
                Check.str_length(min_value=3, max_value=10),
                Check(lambda s: s.notna().all(), error="currency cannot be null"),
            ],
            nullable=False,
            description="Currency code (ISO 4217 or crypto ticker)",
        ),
        "side": Column(
            str,
            checks=[
                Check.isin(["BUY", "SELL", "SELL_SHORT", "UNKNOWN"]),
                Check(lambda s: s.notna().all(), error="side cannot be null"),
            ],
            nullable=False,
            description="Trade side from aggressor perspective",
        ),
        "ingestion_timestamp": Column(
            pd.Timestamp,
            checks=[
                Check(lambda ts: (ts >= pd.Timestamp("2000-01-01")).all()),
            ],
            nullable=False,
            description="Platform ingestion timestamp",
        ),
    },
    strict=False,  # Allow optional fields (source_sequence, platform_sequence, vendor_data)
    coerce=True,  # Coerce types where possible
)


# ==============================================================================
# Quote V2 Schema Definition
# ==============================================================================


QUOTE_V2_SCHEMA = DataFrameSchema(
    columns={
        "message_id": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=255),
                Check(lambda s: s.notna().all(), error="message_id cannot be null"),
            ],
            nullable=False,
            description="Unique message identifier (UUID v4)",
        ),
        "quote_id": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=255),
                Check(lambda s: s.notna().all(), error="quote_id cannot be null"),
            ],
            nullable=False,
            description="Exchange-specific quote identifier",
        ),
        "symbol": Column(
            str,
            checks=[
                Check.str_length(min_value=1, max_value=50),
                Check.str_matches(r"^[A-Z0-9]+$"),
                Check(lambda s: s.notna().all(), error="symbol cannot be null"),
            ],
            nullable=False,
            description="Trading symbol",
        ),
        "exchange": Column(
            str,
            checks=[
                Check.isin(["ASX", "BINANCE", "NYSE", "CME", "NASDAQ"]),
                Check(lambda s: s.notna().all(), error="exchange cannot be null"),
            ],
            nullable=False,
            description="Exchange code",
        ),
        "asset_class": Column(
            str,
            checks=[
                Check.isin(["equities", "crypto", "futures", "options"]),
                Check(lambda s: s.notna().all(), error="asset_class cannot be null"),
            ],
            nullable=False,
            description="Asset class categorization",
        ),
        "timestamp": Column(
            pd.Timestamp,
            checks=[
                Check(lambda ts: (ts >= pd.Timestamp("2000-01-01")).all()),
                Check(lambda ts: (ts <= pd.Timestamp.now() + pd.Timedelta(minutes=5)).all()),
            ],
            nullable=False,
            description="Exchange-reported quote timestamp",
        ),
        "bid_price": Column(
            float,
            checks=[
                Check.greater_than(0, error="Bid price must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000, error="Bid price exceeds max"),
            ],
            nullable=False,
            description="Best bid price",
        ),
        "bid_quantity": Column(
            float,
            checks=[
                Check.greater_than(0, error="Bid quantity must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000_000, error="Bid quantity exceeds max"),
            ],
            nullable=False,
            description="Quantity at best bid",
        ),
        "ask_price": Column(
            float,
            checks=[
                Check.greater_than(0, error="Ask price must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000, error="Ask price exceeds max"),
            ],
            nullable=False,
            description="Best ask price",
        ),
        "ask_quantity": Column(
            float,
            checks=[
                Check.greater_than(0, error="Ask quantity must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000_000, error="Ask quantity exceeds max"),
            ],
            nullable=False,
            description="Quantity at best ask",
        ),
        "currency": Column(
            str,
            checks=[
                Check.str_length(min_value=3, max_value=10),
                Check(lambda s: s.notna().all(), error="currency cannot be null"),
            ],
            nullable=False,
            description="Currency code",
        ),
        "ingestion_timestamp": Column(
            pd.Timestamp,
            checks=[
                Check(lambda ts: (ts >= pd.Timestamp("2000-01-01")).all()),
            ],
            nullable=False,
            description="Platform ingestion timestamp",
        ),
    },
    # Cross-column validation: ask_price must be >= bid_price
    checks=[
        Check(
            lambda df: (df["ask_price"] >= df["bid_price"]).all(),
            error="Ask price must be >= bid price (bid-ask spread cannot be negative)",
        ),
    ],
    strict=False,
    coerce=True,
)


# ==============================================================================
# Test Fixtures
# ==============================================================================


@pytest.fixture
def valid_trades_df():
    """Create a valid trades DataFrame for testing."""
    return pd.DataFrame(
        [
            {
                "message_id": "msg-001",
                "trade_id": "BINANCE-12345",
                "symbol": "BTCUSDT",
                "exchange": "BINANCE",
                "asset_class": "crypto",
                "timestamp": pd.Timestamp("2025-01-13 10:00:00"),
                "price": 45000.12345678,
                "quantity": 1.5,
                "currency": "USDT",
                "side": "BUY",
                "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.100"),
            },
            {
                "message_id": "msg-002",
                "trade_id": "ASX-67890",
                "symbol": "BHP",
                "exchange": "ASX",
                "asset_class": "equities",
                "timestamp": pd.Timestamp("2025-01-13 10:00:01"),
                "price": 45.67,
                "quantity": 1000.0,
                "currency": "AUD",
                "side": "SELL",
                "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:01.050"),
            },
        ]
    )


@pytest.fixture
def valid_quotes_df():
    """Create a valid quotes DataFrame for testing."""
    return pd.DataFrame(
        [
            {
                "message_id": "msg-101",
                "quote_id": "BINANCE-54321",
                "symbol": "ETHUSDT",
                "exchange": "BINANCE",
                "asset_class": "crypto",
                "timestamp": pd.Timestamp("2025-01-13 10:00:00"),
                "bid_price": 3000.12,
                "bid_quantity": 2.5,
                "ask_price": 3001.12,
                "ask_quantity": 3.0,
                "currency": "USDT",
                "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.050"),
            },
            {
                "message_id": "msg-102",
                "quote_id": "ASX-98765",
                "symbol": "CBA",
                "exchange": "ASX",
                "asset_class": "equities",
                "timestamp": pd.Timestamp("2025-01-13 10:00:01"),
                "bid_price": 105.50,
                "bid_quantity": 500.0,
                "ask_price": 105.55,
                "ask_quantity": 300.0,
                "currency": "AUD",
                "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:01.025"),
            },
        ]
    )


# ==============================================================================
# Trade Validation Tests
# ==============================================================================


@pytest.mark.unit
class TestTradeValidation:
    """Test data validation for Trade v2 records."""

    def test_valid_trades_pass_validation(self, valid_trades_df):
        """Test that valid trades pass all validation checks."""
        validated_df = TRADE_V2_SCHEMA.validate(valid_trades_df)
        assert len(validated_df) == 2
        assert validated_df["price"].min() > 0
        assert validated_df["quantity"].min() > 0

    def test_negative_price_fails(self, valid_trades_df):
        """Test that negative price fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "price"] = -100.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'price' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_zero_price_fails(self, valid_trades_df):
        """Test that zero price fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "price"] = 0.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'price' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_negative_quantity_fails(self, valid_trades_df):
        """Test that negative quantity fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "quantity"] = -10.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'quantity' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_zero_quantity_fails(self, valid_trades_df):
        """Test that zero quantity fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "quantity"] = 0.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'quantity' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_invalid_symbol_format_fails(self, valid_trades_df):
        """Test that lowercase or special characters in symbol fail validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "symbol"] = "btc-usdt"  # Lowercase with hyphen

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_invalid_exchange_fails(self, valid_trades_df):
        """Test that unknown exchange code fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "exchange"] = "UNKNOWN_EXCHANGE"

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_invalid_asset_class_fails(self, valid_trades_df):
        """Test that unknown asset class fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "asset_class"] = "bonds"  # Not in enum

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_invalid_side_fails(self, valid_trades_df):
        """Test that invalid trade side fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "side"] = "INVALID"

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_future_timestamp_fails(self, valid_trades_df):
        """Test that timestamp too far in future fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "timestamp"] = pd.Timestamp.now() + pd.Timedelta(hours=1)

        with pytest.raises(pa.errors.SchemaError, match="Column 'timestamp' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_very_old_timestamp_fails(self, valid_trades_df):
        """Test that timestamp before year 2000 fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "timestamp"] = pd.Timestamp("1999-12-31")

        with pytest.raises(pa.errors.SchemaError, match="Column 'timestamp' failed"):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_missing_required_field_fails(self, valid_trades_df):
        """Test that missing required field fails validation."""
        invalid_df = valid_trades_df.drop(columns=["symbol"])

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_null_required_field_fails(self, valid_trades_df):
        """Test that null in required field fails validation."""
        invalid_df = valid_trades_df.copy()
        invalid_df.loc[0, "symbol"] = None

        with pytest.raises(pa.errors.SchemaError):
            TRADE_V2_SCHEMA.validate(invalid_df)

    def test_decimal_precision_crypto(self, valid_trades_df):
        """Test that high-precision crypto prices are handled correctly."""
        crypto_df = valid_trades_df[valid_trades_df["asset_class"] == "crypto"].copy()
        crypto_df.loc[0, "price"] = 0.00012345  # High precision crypto price
        crypto_df.loc[0, "quantity"] = 0.05  # Fractional crypto quantity

        validated_df = TRADE_V2_SCHEMA.validate(crypto_df)
        assert validated_df.loc[0, "price"] > 0
        assert validated_df.loc[0, "quantity"] > 0

    def test_timestamp_ordering_validation(self):
        """Test validation of timestamp ordering across multiple records."""
        df = pd.DataFrame(
            [
                {
                    "message_id": "msg-001",
                    "trade_id": "BINANCE-1",
                    "symbol": "BTCUSDT",
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:00"),
                    "price": 45000.0,
                    "quantity": 1.0,
                    "currency": "USDT",
                    "side": "BUY",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.100"),
                },
                {
                    "message_id": "msg-002",
                    "trade_id": "BINANCE-2",
                    "symbol": "BTCUSDT",
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:01"),
                    "price": 45001.0,
                    "quantity": 1.0,
                    "currency": "USDT",
                    "side": "SELL",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:01.100"),
                },
            ]
        )

        validated_df = TRADE_V2_SCHEMA.validate(df)

        # Verify timestamps are monotonically increasing
        assert (validated_df["timestamp"].diff().dropna() >= pd.Timedelta(0)).all()


# ==============================================================================
# Quote Validation Tests
# ==============================================================================


@pytest.mark.unit
class TestQuoteValidation:
    """Test data validation for Quote v2 records."""

    def test_valid_quotes_pass_validation(self, valid_quotes_df):
        """Test that valid quotes pass all validation checks."""
        validated_df = QUOTE_V2_SCHEMA.validate(valid_quotes_df)
        assert len(validated_df) == 2
        assert (validated_df["ask_price"] >= validated_df["bid_price"]).all()

    def test_negative_bid_price_fails(self, valid_quotes_df):
        """Test that negative bid price fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "bid_price"] = -100.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'bid_price' failed"):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_negative_ask_price_fails(self, valid_quotes_df):
        """Test that negative ask price fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "ask_price"] = -100.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'ask_price' failed"):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_zero_bid_quantity_fails(self, valid_quotes_df):
        """Test that zero bid quantity fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "bid_quantity"] = 0.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'bid_quantity' failed"):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_zero_ask_quantity_fails(self, valid_quotes_df):
        """Test that zero ask quantity fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "ask_quantity"] = 0.0

        with pytest.raises(pa.errors.SchemaError, match="Column 'ask_quantity' failed"):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_negative_spread_fails(self, valid_quotes_df):
        """Test that negative spread (bid > ask) fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "bid_price"] = 3002.0
        invalid_df.loc[0, "ask_price"] = 3000.0  # Ask < Bid (invalid)

        with pytest.raises(pa.errors.SchemaError):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_zero_spread_passes(self, valid_quotes_df):
        """Test that zero spread (bid == ask) passes validation."""
        df = valid_quotes_df.copy()
        df.loc[0, "bid_price"] = 3000.0
        df.loc[0, "ask_price"] = 3000.0  # Zero spread

        validated_df = QUOTE_V2_SCHEMA.validate(df)
        assert validated_df.loc[0, "bid_price"] == validated_df.loc[0, "ask_price"]

    def test_invalid_symbol_format_fails(self, valid_quotes_df):
        """Test that invalid symbol format fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "symbol"] = "eth_usdt"  # Underscore not allowed

        with pytest.raises(pa.errors.SchemaError):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_missing_required_field_fails(self, valid_quotes_df):
        """Test that missing required field fails validation."""
        invalid_df = valid_quotes_df.drop(columns=["bid_price"])

        with pytest.raises(pa.errors.SchemaError):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_null_required_field_fails(self, valid_quotes_df):
        """Test that null in required field fails validation."""
        invalid_df = valid_quotes_df.copy()
        invalid_df.loc[0, "ask_price"] = None

        with pytest.raises(pa.errors.SchemaError):
            QUOTE_V2_SCHEMA.validate(invalid_df)

    def test_decimal_precision_crypto(self, valid_quotes_df):
        """Test that high-precision crypto quotes are handled correctly."""
        crypto_df = valid_quotes_df[valid_quotes_df["asset_class"] == "crypto"].copy()
        crypto_df.loc[0, "bid_price"] = 0.00012345
        crypto_df.loc[0, "ask_price"] = 0.00012346
        crypto_df.loc[0, "bid_quantity"] = 1000.12345678
        crypto_df.loc[0, "ask_quantity"] = 500.87654321

        validated_df = QUOTE_V2_SCHEMA.validate(crypto_df)
        assert validated_df.loc[0, "bid_price"] > 0
        assert validated_df.loc[0, "ask_price"] > validated_df.loc[0, "bid_price"]

    def test_wide_spread_passes(self, valid_quotes_df):
        """Test that wide but valid spread passes validation."""
        df = valid_quotes_df.copy()
        df.loc[0, "bid_price"] = 3000.0
        df.loc[0, "ask_price"] = 3100.0  # Wide spread (3.3%)

        validated_df = QUOTE_V2_SCHEMA.validate(df)
        spread = (
            validated_df.loc[0, "ask_price"] - validated_df.loc[0, "bid_price"]
        ) / validated_df.loc[0, "bid_price"]
        assert spread > 0


# ==============================================================================
# Integration Tests - Multiple Records
# ==============================================================================


@pytest.mark.unit
class TestBatchValidation:
    """Test validation of batches of records."""

    def test_large_trade_batch_validation(self):
        """Test validation of large batch of trade records."""
        # Generate 1000 valid trades
        trades = []
        for i in range(1000):
            trades.append(
                {
                    "message_id": f"msg-{i:06d}",
                    "trade_id": f"BINANCE-{i:06d}",
                    "symbol": "BTCUSDT",
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:00") + pd.Timedelta(seconds=i),
                    "price": 45000.0 + i * 0.01,
                    "quantity": 1.0 + i * 0.001,
                    "currency": "USDT",
                    "side": "BUY" if i % 2 == 0 else "SELL",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.100")
                    + pd.Timedelta(seconds=i),
                }
            )

        df = pd.DataFrame(trades)
        validated_df = TRADE_V2_SCHEMA.validate(df)

        assert len(validated_df) == 1000
        assert validated_df["price"].min() > 0
        assert validated_df["quantity"].min() > 0

    def test_large_quote_batch_validation(self):
        """Test validation of large batch of quote records."""
        # Generate 1000 valid quotes
        quotes = []
        for i in range(1000):
            bid = 3000.0 + i * 0.01
            ask = bid + 1.0  # Fixed spread
            quotes.append(
                {
                    "message_id": f"msg-{i:06d}",
                    "quote_id": f"BINANCE-{i:06d}",
                    "symbol": "ETHUSDT",
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:00") + pd.Timedelta(seconds=i),
                    "bid_price": bid,
                    "bid_quantity": 2.5 + i * 0.01,
                    "ask_price": ask,
                    "ask_quantity": 3.0 + i * 0.01,
                    "currency": "USDT",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.050")
                    + pd.Timedelta(seconds=i),
                }
            )

        df = pd.DataFrame(quotes)
        validated_df = QUOTE_V2_SCHEMA.validate(df)

        assert len(validated_df) == 1000
        assert (validated_df["ask_price"] >= validated_df["bid_price"]).all()

    def test_mixed_asset_classes_validation(self):
        """Test validation of mixed asset classes in single batch."""
        trades = pd.DataFrame(
            [
                {
                    "message_id": "msg-001",
                    "trade_id": "BINANCE-1",
                    "symbol": "BTCUSDT",
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:00"),
                    "price": 45000.0,
                    "quantity": 1.0,
                    "currency": "USDT",
                    "side": "BUY",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:00.100"),
                },
                {
                    "message_id": "msg-002",
                    "trade_id": "ASX-1",
                    "symbol": "BHP",
                    "exchange": "ASX",
                    "asset_class": "equities",
                    "timestamp": pd.Timestamp("2025-01-13 10:00:01"),
                    "price": 45.67,
                    "quantity": 1000.0,
                    "currency": "AUD",
                    "side": "SELL",
                    "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:01.050"),
                },
            ]
        )

        validated_df = TRADE_V2_SCHEMA.validate(trades)
        assert len(validated_df) == 2
        assert set(validated_df["asset_class"]) == {"crypto", "equities"}

    def test_partial_batch_failure(self, valid_trades_df):
        """Test that batch fails if any record is invalid."""
        # Add an invalid trade to valid batch
        invalid_trade = {
            "message_id": "msg-003",
            "trade_id": "BINANCE-999",
            "symbol": "BTCUSDT",
            "exchange": "BINANCE",
            "asset_class": "crypto",
            "timestamp": pd.Timestamp("2025-01-13 10:00:02"),
            "price": -100.0,  # Invalid: negative price
            "quantity": 1.0,
            "currency": "USDT",
            "side": "BUY",
            "ingestion_timestamp": pd.Timestamp("2025-01-13 10:00:02.100"),
        }

        df = pd.concat([valid_trades_df, pd.DataFrame([invalid_trade])], ignore_index=True)

        with pytest.raises(pa.errors.SchemaError, match="Column 'price' failed"):
            TRADE_V2_SCHEMA.validate(df)

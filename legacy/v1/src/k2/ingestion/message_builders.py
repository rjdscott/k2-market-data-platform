"""Message builders for v2 schemas.

This module provides builder functions for constructing v2 trade and quote messages
that conform to the industry-standard hybrid schemas (core fields + vendor_data).

Key Features:
- UUID generation for message_id (deduplication)
- Timestamp conversion (ms → µs, datetime → µs)
- Decimal precision handling for price/quantity
- vendor_data pattern for exchange-specific fields
- Side enum validation (BUY, SELL, SELL_SHORT, UNKNOWN)
- Asset class enum validation (equities, crypto, futures, options)

Usage:
    from k2.ingestion.message_builders import build_trade_v2, build_quote_v2
    from decimal import Decimal
    from datetime import datetime

    trade = build_trade_v2(
        symbol="BHP",
        exchange="ASX",
        asset_class="equities",
        timestamp=datetime.utcnow(),
        price=Decimal("45.67"),
        quantity=Decimal("1000"),
        currency="AUD",
        side="BUY",
        vendor_data={"company_id": "123", "qualifiers": "0"}
    )
"""

import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any


def _validate_decimal_precision(
    value: Decimal,
    max_digits: int = 18,
    max_scale: int = 8,
    field_name: str = "value",
) -> None:
    """Validate Decimal fits within (max_digits, max_scale) precision.

    This validates that Decimal values fit within Iceberg's Decimal(18,8) schema
    constraints. Failing fast here prevents silent failures at write time.

    Args:
        value: Decimal to validate
        max_digits: Maximum total digits (default 18)
        max_scale: Maximum decimal places (default 8)
        field_name: Field name for error message

    Raises:
        ValueError: If value is negative, exceeds max_digits, or exceeds max_scale

    Examples:
        >>> _validate_decimal_precision(Decimal("45.67"), field_name="price")  # OK
        >>> _validate_decimal_precision(Decimal("12345678901234567890.12"))  # Raises ValueError (20 digits > 18 max)
        >>> _validate_decimal_precision(Decimal("45.123456789"))  # Raises ValueError (9 decimals > 8 max)
        >>> _validate_decimal_precision(Decimal("-45.67"), field_name="price")  # Raises ValueError (negative)
    """
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value}")

    # Check total digits (including decimal places)
    sign, digits, exponent = value.as_tuple()
    total_digits = len(digits)

    if total_digits > max_digits:
        raise ValueError(
            f"{field_name} exceeds maximum precision: {total_digits} digits > {max_digits} max. "
            f"Value: {value}",
        )

    # Check decimal places
    scale = -exponent if exponent < 0 else 0
    if scale > max_scale:
        raise ValueError(
            f"{field_name} exceeds maximum scale: {scale} decimal places > {max_scale} max. "
            f"Value: {value}",
        )


def build_trade_v2(
    symbol: str,
    exchange: str,
    asset_class: str,
    timestamp: datetime | int,
    price: Decimal | float | str,
    quantity: Decimal | float | str,
    currency: str = "USD",
    side: str = "UNKNOWN",
    trade_id: str | None = None,
    message_id: str | None = None,
    trade_conditions: list[str] | None = None,
    source_sequence: int | None = None,
    platform_sequence: int | None = None,
    vendor_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a v2 Trade message conforming to TradeV2 Avro schema.

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT', 'ETHUSD')
        exchange: Exchange code (e.g., 'BINANCE', 'KRAKEN')
        asset_class: Asset class enum ('equities', 'crypto', 'futures', 'options')
        timestamp: Exchange timestamp (datetime or microseconds since epoch)
        price: Trade price (Decimal, float, or string - will convert to Decimal)
        quantity: Trade quantity (Decimal, float, or string - will convert to Decimal)
        currency: Currency code (default: 'USD'). Examples: 'AUD', 'USDT', 'BTC'
        side: Trade side enum ('BUY', 'SELL', 'SELL_SHORT', 'UNKNOWN')
        trade_id: Exchange trade identifier (default: auto-generated '{EXCHANGE}-{timestamp}')
        message_id: Unique message ID (default: auto-generated UUID)
        trade_conditions: Array of trade condition codes (default: empty array)
        source_sequence: Exchange sequence number (default: None)
        platform_sequence: Platform sequence number (default: None)
        vendor_data: Exchange-specific fields as key-value pairs (default: None)

    Returns:
        Dictionary conforming to TradeV2 Avro schema

    Raises:
        ValueError: If asset_class or side not in enum values

    Examples:
        >>> from decimal import Decimal
        >>> from datetime import datetime
        >>>
        >>> # Binance crypto trade
        >>> trade = build_trade_v2(
        ...     symbol="BTCUSDT",
        ...     exchange="BINANCE",
        ...     asset_class="crypto",
        ...     timestamp=datetime.utcnow(),
        ...     price=Decimal("16500.00"),
        ...     quantity=Decimal("0.05"),
        ...     currency="USDT",
        ...     side="SELL",
        ...     vendor_data={"is_buyer_maker": "true", "event_type": "trade"}
        ... )
    """
    # Validate enums
    valid_asset_classes = {"equities", "crypto", "futures", "options"}
    if asset_class not in valid_asset_classes:
        raise ValueError(
            f"Invalid asset_class: {asset_class}. Must be one of {valid_asset_classes}",
        )

    valid_sides = {"BUY", "SELL", "SELL_SHORT", "UNKNOWN"}
    if side not in valid_sides:
        raise ValueError(f"Invalid side: {side}. Must be one of {valid_sides}")

    # Convert timestamp to microseconds
    if isinstance(timestamp, datetime):
        timestamp_micros = int(timestamp.timestamp() * 1_000_000)
    elif isinstance(timestamp, int):
        # Assume microseconds if > 1e15, otherwise milliseconds
        timestamp_micros = timestamp if timestamp > 1_000_000_000_000_000 else timestamp * 1000
    else:
        raise ValueError(f"Invalid timestamp type: {type(timestamp)}")

    # Convert price and quantity to Decimal
    if not isinstance(price, Decimal):
        price = Decimal(str(price))
    if not isinstance(quantity, Decimal):
        quantity = Decimal(str(quantity))

    # Validate Decimal precision (18,8) for Iceberg schema
    _validate_decimal_precision(price, field_name="price")
    _validate_decimal_precision(quantity, field_name="quantity")

    # Generate defaults
    current_time_micros = int(time.time() * 1_000_000)
    if message_id is None:
        message_id = str(uuid.uuid4())
    if trade_id is None:
        trade_id = f"{exchange}-{int(timestamp_micros / 1000)}"  # Use ms timestamp
    if trade_conditions is None:
        trade_conditions = []

    return {
        "message_id": message_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "timestamp": timestamp_micros,
        "price": price,
        "quantity": quantity,
        "currency": currency,
        "side": side,
        "trade_conditions": trade_conditions,
        "source_sequence": source_sequence,
        "ingestion_timestamp": current_time_micros,
        "platform_sequence": platform_sequence,
        "vendor_data": vendor_data,
    }


def build_quote_v2(
    symbol: str,
    exchange: str,
    asset_class: str,
    timestamp: datetime | int,
    bid_price: Decimal | float | str,
    bid_quantity: Decimal | float | str,
    ask_price: Decimal | float | str,
    ask_quantity: Decimal | float | str,
    currency: str = "USD",
    quote_id: str | None = None,
    message_id: str | None = None,
    source_sequence: int | None = None,
    platform_sequence: int | None = None,
    vendor_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a v2 Quote message conforming to QuoteV2 Avro schema.

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT', 'ETHUSD')
        exchange: Exchange code (e.g., 'BINANCE', 'KRAKEN')
        asset_class: Asset class enum ('equities', 'crypto', 'futures', 'options')
        timestamp: Exchange timestamp (datetime or microseconds since epoch)
        bid_price: Best bid price (Decimal, float, or string - will convert to Decimal)
        bid_quantity: Bid quantity (Decimal, float, or string - will convert to Decimal)
        ask_price: Best ask price (Decimal, float, or string - will convert to Decimal)
        ask_quantity: Ask quantity (Decimal, float, or string - will convert to Decimal)
        currency: Currency code (default: 'USD'). Examples: 'AUD', 'USDT', 'BTC'
        quote_id: Exchange quote identifier (default: auto-generated '{EXCHANGE}-{timestamp}')
        message_id: Unique message ID (default: auto-generated UUID)
        source_sequence: Exchange sequence number (default: None)
        platform_sequence: Platform sequence number (default: None)
        vendor_data: Exchange-specific fields as key-value pairs (default: None)

    Returns:
        Dictionary conforming to QuoteV2 Avro schema

    Raises:
        ValueError: If asset_class not in enum values

    Examples:
        >>> from decimal import Decimal
        >>> from datetime import datetime
        >>>
        >>> # Binance crypto quote
        >>> quote = build_quote_v2(
        ...     symbol="BTCUSDT",
        ...     exchange="BINANCE",
        ...     asset_class="crypto",
        ...     timestamp=datetime.utcnow(),
        ...     bid_price=Decimal("16500.00"),
        ...     bid_quantity=Decimal("1.5"),
        ...     ask_price=Decimal("16500.50"),
        ...     ask_quantity=Decimal("2.0"),
        ...     currency="USDT"
        ... )
    """
    # Validate enum
    valid_asset_classes = {"equities", "crypto", "futures", "options"}
    if asset_class not in valid_asset_classes:
        raise ValueError(
            f"Invalid asset_class: {asset_class}. Must be one of {valid_asset_classes}",
        )

    # Convert timestamp to microseconds
    if isinstance(timestamp, datetime):
        timestamp_micros = int(timestamp.timestamp() * 1_000_000)
    elif isinstance(timestamp, int):
        # Assume microseconds if > 1e15, otherwise milliseconds
        timestamp_micros = timestamp if timestamp > 1_000_000_000_000_000 else timestamp * 1000
    else:
        raise ValueError(f"Invalid timestamp type: {type(timestamp)}")

    # Convert prices and quantities to Decimal
    if not isinstance(bid_price, Decimal):
        bid_price = Decimal(str(bid_price))
    if not isinstance(bid_quantity, Decimal):
        bid_quantity = Decimal(str(bid_quantity))
    if not isinstance(ask_price, Decimal):
        ask_price = Decimal(str(ask_price))
    if not isinstance(ask_quantity, Decimal):
        ask_quantity = Decimal(str(ask_quantity))

    # Validate Decimal precision (18,8) for Iceberg schema
    _validate_decimal_precision(bid_price, field_name="bid_price")
    _validate_decimal_precision(bid_quantity, field_name="bid_quantity")
    _validate_decimal_precision(ask_price, field_name="ask_price")
    _validate_decimal_precision(ask_quantity, field_name="ask_quantity")

    # Generate defaults
    current_time_micros = int(time.time() * 1_000_000)
    if message_id is None:
        message_id = str(uuid.uuid4())
    if quote_id is None:
        quote_id = f"{exchange}-{int(timestamp_micros / 1000)}"  # Use ms timestamp

    return {
        "message_id": message_id,
        "quote_id": quote_id,
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "timestamp": timestamp_micros,
        "bid_price": bid_price,
        "bid_quantity": bid_quantity,
        "ask_price": ask_price,
        "ask_quantity": ask_quantity,
        "currency": currency,
        "source_sequence": source_sequence,
        "ingestion_timestamp": current_time_micros,
        "platform_sequence": platform_sequence,
        "vendor_data": vendor_data,
    }


def convert_v1_to_v2_trade(
    v1_record: dict[str, Any],
    default_currency: str = "AUD",
    default_side: str = "UNKNOWN",
) -> dict[str, Any]:
    """Convert a v1 Trade record to v2 Trade format.

    Handles field mappings:
    - volume → quantity
    - exchange_timestamp (millis) → timestamp (micros)
    - company_id, qualifiers, venue → vendor_data

    Args:
        v1_record: v1 Trade record
        default_currency: Default currency if not in v1 (default: 'AUD' for ASX)
        default_side: Default side if not in v1 (default: 'UNKNOWN')

    Returns:
        v2 Trade record

    Example:
        >>> v1 = {
        ...     "symbol": "BHP",
        ...     "company_id": 123,
        ...     "exchange": "ASX",
        ...     "exchange_timestamp": 1672531199900,  # millis
        ...     "price": Decimal("45.67"),
        ...     "volume": 1000,
        ...     "qualifiers": 0,
        ...     "venue": "X",
        ...     "buyer_id": None,
        ...     "ingestion_timestamp": 1672531200000,
        ...     "sequence_number": 12345,
        ... }
        >>> v2 = convert_v1_to_v2_trade(v1)
        >>> assert v2["quantity"] == Decimal("1000")
        >>> assert v2["vendor_data"]["company_id"] == "123"
    """
    # Extract vendor-specific fields
    vendor_data = {
        "company_id": str(v1_record.get("company_id", "")),
        "qualifiers": str(v1_record.get("qualifiers", "")),
        "venue": str(v1_record.get("venue", "")),
    }
    if v1_record.get("buyer_id"):
        vendor_data["buyer_id"] = str(v1_record["buyer_id"])

    # Determine asset class from exchange
    exchange = v1_record.get("exchange", "").upper()
    if exchange in ("ASX", "NYSE", "NASDAQ"):
        asset_class = "equities"
    elif exchange in ("BINANCE", "COINBASE", "KRAKEN"):
        asset_class = "crypto"
    else:
        asset_class = "equities"  # Default

    return build_trade_v2(
        symbol=v1_record["symbol"],
        exchange=v1_record.get("exchange", "ASX"),
        asset_class=asset_class,
        timestamp=v1_record["exchange_timestamp"] * 1000,  # millis → micros
        price=v1_record["price"],
        quantity=Decimal(str(v1_record["volume"])),  # volume → quantity
        currency=default_currency,
        side=default_side,
        trade_id=None,  # Will auto-generate
        message_id=None,  # Will auto-generate
        source_sequence=v1_record.get("sequence_number"),
        platform_sequence=None,
        vendor_data=vendor_data,
    )

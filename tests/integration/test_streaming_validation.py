"""Integration tests for WebSocket streaming validation.

These tests validate that Binance and Kraken clients can connect,
receive trades, and produce valid v2 schema messages.

Note: These tests require internet connectivity and working WebSocket APIs.
They are marked as 'integration' and can be skipped in CI if needed.
"""

import asyncio
import time

import pytest

from k2.ingestion.binance_client import BinanceWebSocketClient
from k2.ingestion.kraken_client import KrakenWebSocketClient


def validate_v2_schema_basic(trade: dict) -> tuple[bool, list[str]]:
    """Basic v2 schema validation.

    Args:
        trade: Trade record to validate

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Required fields
    required_fields = [
        "message_id",
        "trade_id",
        "symbol",
        "exchange",
        "asset_class",
        "timestamp",
        "price",
        "quantity",
        "currency",
        "side",
    ]

    for field in required_fields:
        if field not in trade:
            errors.append(f"Missing required field: {field}")

    # Validate field values
    if "asset_class" in trade and trade["asset_class"] != "crypto":
        errors.append(f"Invalid asset_class: {trade['asset_class']}")

    if "side" in trade and trade["side"] not in ["BUY", "SELL"]:
        errors.append(f"Invalid side: {trade['side']}")

    # Validate positive values
    if "price" in trade and float(trade["price"]) <= 0:
        errors.append(f"Invalid price: {trade['price']}")

    if "quantity" in trade and float(trade["quantity"]) <= 0:
        errors.append(f"Invalid quantity: {trade['quantity']}")

    return (len(errors) == 0, errors)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_binance_streaming_validation():
    """Test that Binance client streams valid v2 trades."""
    trades_received = []
    validation_errors = []
    target_trades = 3  # Just need a few trades to validate

    stop_event = asyncio.Event()

    def handle_trade(trade: dict) -> None:
        """Handle incoming trade."""
        trades_received.append(trade)

        # Validate schema
        is_valid, errors = validate_v2_schema_basic(trade)
        if not is_valid:
            validation_errors.extend(errors)

        # Stop after target
        if len(trades_received) >= target_trades:
            stop_event.set()

    client = BinanceWebSocketClient(
        symbols=["BTCUSDT"],  # Single symbol for faster test
        on_message=handle_trade,
    )

    try:
        # Start client and wait for trades
        client_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        await client.disconnect()

        # Give a moment for cleanup
        await asyncio.sleep(0.5)

    except TimeoutError:
        await client.disconnect()
        pytest.fail(
            f"Timeout: Only received {len(trades_received)} trades (expected {target_trades})"
        )
    except Exception as e:
        await client.disconnect()
        pytest.fail(f"Client error: {e}")

    # Validate results
    assert (
        len(trades_received) >= target_trades
    ), f"Expected at least {target_trades} trades, got {len(trades_received)}"
    assert len(validation_errors) == 0, f"Schema validation errors: {validation_errors}"

    # Validate exchange-specific fields
    for trade in trades_received:
        assert trade["exchange"] == "BINANCE"
        assert trade["symbol"] in ["BTCUSDT"]
        assert "vendor_data" in trade
        assert "base_asset" in trade["vendor_data"]
        assert "quote_asset" in trade["vendor_data"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_kraken_streaming_validation():
    """Test that Kraken client streams valid v2 trades."""
    trades_received = []
    validation_errors = []
    target_trades = 3  # Just need a few trades to validate

    stop_event = asyncio.Event()

    def handle_trade(trade: dict) -> None:
        """Handle incoming trade."""
        trades_received.append(trade)

        # Validate schema
        is_valid, errors = validate_v2_schema_basic(trade)
        if not is_valid:
            validation_errors.extend(errors)

        # Stop after target
        if len(trades_received) >= target_trades:
            stop_event.set()

    client = KrakenWebSocketClient(
        symbols=["BTC/USD"],  # Single symbol for faster test
        on_message=handle_trade,
    )

    try:
        # Start client and wait for trades
        client_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        await client.disconnect()

        # Give a moment for cleanup
        await asyncio.sleep(0.5)

    except TimeoutError:
        await client.disconnect()
        pytest.fail(
            f"Timeout: Only received {len(trades_received)} trades (expected {target_trades})"
        )
    except Exception as e:
        await client.disconnect()
        pytest.fail(f"Client error: {e}")

    # Validate results
    assert (
        len(trades_received) >= target_trades
    ), f"Expected at least {target_trades} trades, got {len(trades_received)}"
    assert len(validation_errors) == 0, f"Schema validation errors: {validation_errors}"

    # Validate exchange-specific fields
    for trade in trades_received:
        assert trade["exchange"] == "KRAKEN"
        assert trade["symbol"] == "BTCUSD"  # BTC/USD → BTCUSD
        assert "vendor_data" in trade
        assert "pair" in trade["vendor_data"]
        assert trade["vendor_data"]["pair"] == "BTC/USD"
        assert trade["vendor_data"]["base_asset"] == "BTC"  # XBT normalized to BTC
        assert trade["vendor_data"]["quote_asset"] == "USD"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_concurrent_streaming():
    """Test that both Binance and Kraken can stream concurrently."""
    binance_trades = []
    kraken_trades = []
    target_per_exchange = 3

    binance_stop = asyncio.Event()
    kraken_stop = asyncio.Event()

    def handle_binance_trade(trade: dict) -> None:
        binance_trades.append(trade)
        if len(binance_trades) >= target_per_exchange:
            binance_stop.set()

    def handle_kraken_trade(trade: dict) -> None:
        kraken_trades.append(trade)
        if len(kraken_trades) >= target_per_exchange:
            kraken_stop.set()

    binance_client = BinanceWebSocketClient(
        symbols=["BTCUSDT"],
        on_message=handle_binance_trade,
    )

    kraken_client = KrakenWebSocketClient(
        symbols=["BTC/USD"],
        on_message=handle_kraken_trade,
    )

    try:
        # Start both clients concurrently
        binance_task = asyncio.create_task(binance_client.connect())
        kraken_task = asyncio.create_task(kraken_client.connect())

        # Wait for both to receive target trades
        await asyncio.wait_for(
            asyncio.gather(binance_stop.wait(), kraken_stop.wait()), timeout=60.0
        )

        # Disconnect both
        await binance_client.disconnect()
        await kraken_client.disconnect()

        # Give a moment for cleanup
        await asyncio.sleep(0.5)

    except TimeoutError:
        await binance_client.disconnect()
        await kraken_client.disconnect()
        pytest.fail(
            f"Timeout: Binance={len(binance_trades)}, Kraken={len(kraken_trades)} "
            f"(expected {target_per_exchange} each)"
        )
    except Exception as e:
        await binance_client.disconnect()
        await kraken_client.disconnect()
        pytest.fail(f"Client error: {e}")

    # Validate results
    assert len(binance_trades) >= target_per_exchange
    assert len(kraken_trades) >= target_per_exchange

    # Validate exchanges are distinct
    assert all(t["exchange"] == "BINANCE" for t in binance_trades)
    assert all(t["exchange"] == "KRAKEN" for t in kraken_trades)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_kraken_xbt_normalization():
    """Test that Kraken XBT pairs are normalized to BTC."""
    trades_received = []
    stop_event = asyncio.Event()

    def handle_trade(trade: dict) -> None:
        trades_received.append(trade)
        if len(trades_received) >= 2:
            stop_event.set()

    client = KrakenWebSocketClient(
        symbols=["BTC/USD"],  # XBT/USD should normalize to BTC
        on_message=handle_trade,
    )

    try:
        client_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(stop_event.wait(), timeout=20.0)
        await client.disconnect()
        await asyncio.sleep(0.5)
    except TimeoutError:
        await client.disconnect()
        pytest.fail(f"Timeout: Only received {len(trades_received)} trades")

    # Verify normalization
    for trade in trades_received:
        # Symbol should not contain "XBT"
        assert "XBT" not in trade["symbol"]
        # Base asset should be "BTC" not "XBT"
        assert trade["vendor_data"]["base_asset"] == "BTC"
        # Original pair may be "XBT/USD" from Kraken
        assert trade["vendor_data"]["pair"] in ["XBT/USD", "BTC/USD"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_message_rate():
    """Test that message rates are reasonable."""
    trades_received = []
    timestamps = []
    target = 10

    stop_event = asyncio.Event()

    def handle_trade(trade: dict) -> None:
        trades_received.append(trade)
        timestamps.append(time.time())
        if len(trades_received) >= target:
            stop_event.set()

    client = BinanceWebSocketClient(
        symbols=["BTCUSDT", "ETHUSDT"],  # Multiple symbols for higher rate
        on_message=handle_trade,
    )

    try:
        client_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(stop_event.wait(), timeout=20.0)
        await client.disconnect()
        await asyncio.sleep(0.5)
    except TimeoutError:
        await client.disconnect()
        pytest.fail(f"Timeout: Only received {len(trades_received)} trades")

    # Calculate message rate
    if len(timestamps) >= 2:
        duration = timestamps[-1] - timestamps[0]
        rate = (len(timestamps) - 1) / duration if duration > 0 else 0

        # Sanity check: rate should be > 0.1 trades/sec (very conservative)
        assert rate > 0.1, f"Message rate too low: {rate:.2f} trades/sec"
        # Rate should be < 1000 trades/sec (sanity check)
        assert rate < 1000, f"Message rate suspiciously high: {rate:.2f} trades/sec"

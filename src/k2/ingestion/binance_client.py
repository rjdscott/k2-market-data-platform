"""Binance WebSocket client for real-time trade streaming.

This module provides an async WebSocket client that connects to Binance's
trade stream API and converts trade messages to v2 schema format.

Features:
- Async/await pattern for efficient streaming
- Automatic reconnection with exponential backoff
- Message validation and error handling
- Dynamic currency extraction from symbol
- Base/quote asset separation

Usage:
    from k2.ingestion.binance_client import BinanceWebSocketClient
    from k2.common.config import config

    async def handle_trade(trade_data: dict) -> None:
        print(f"Received trade: {trade_data['symbol']} @ {trade_data['price']}")

    client = BinanceWebSocketClient(
        symbols=config.binance.symbols,
        on_message=handle_trade,
    )
    await client.connect()
"""

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Optional

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = structlog.get_logger(__name__)


def parse_binance_symbol(symbol: str) -> tuple[str, str, str]:
    """
    Parse Binance symbol into base asset, quote asset, and quote currency.

    Args:
        symbol: Binance symbol (e.g., BTCUSDT, ETHBTC, BNBEUR)

    Returns:
        (base_asset, quote_asset, quote_currency)

    Examples:
        >>> parse_binance_symbol("BTCUSDT")
        ("BTC", "USDT", "USDT")
        >>> parse_binance_symbol("ETHBTC")
        ("ETH", "BTC", "BTC")
        >>> parse_binance_symbol("BNBEUR")
        ("BNB", "EUR", "EUR")
    """
    # Common quote currencies (order matters - check longest first)
    quote_currencies = [
        "USDT",
        "USDC",
        "BUSD",
        "TUSD",
        "USDP",  # Stablecoins
        "BTC",
        "ETH",
        "BNB",  # Crypto
        "EUR",
        "GBP",
        "AUD",
        "USD",  # Fiat
        "TRY",
        "ZAR",
        "UAH",
        "NGN",  # Other fiat
    ]

    for quote in quote_currencies:
        if symbol.endswith(quote):
            base_asset = symbol[: -len(quote)]
            return (base_asset, quote, quote)

    # Fallback: assume last 4 chars are quote (USDT pattern)
    return (symbol[:-4], symbol[-4:], symbol[-4:])


def _validate_binance_message(msg: dict[str, Any]) -> None:
    """
    Validate Binance trade message has required fields.

    Args:
        msg: Binance trade payload

    Raises:
        ValueError: If required fields missing
    """
    required_fields = ["e", "E", "s", "t", "p", "q", "T", "m"]
    missing = [field for field in required_fields if field not in msg]

    if missing:
        raise ValueError(f"Missing required fields in Binance trade: {missing}")

    if msg["e"] != "trade":
        raise ValueError(f"Invalid event type: {msg['e']}, expected 'trade'")


def convert_binance_trade_to_v2(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Convert Binance WebSocket trade to v2 Trade schema.

    Args:
        msg: Binance trade payload from WebSocket

    Returns:
        v2 Trade record conforming to TradeV2 Avro schema

    Raises:
        ValueError: If required fields missing or invalid
    """
    # Validate required fields
    _validate_binance_message(msg)

    # Parse symbol to extract base/quote assets and currency
    symbol = msg["s"]
    base_asset, quote_asset, currency = parse_binance_symbol(symbol)

    # Determine trade side from isBuyerMaker
    # m=true → buyer was maker (passive), seller was taker (aggressive) → SELL
    # m=false → buyer was taker (aggressive) → BUY
    side = "SELL" if msg["m"] else "BUY"

    # Convert timestamps (Binance uses milliseconds, v2 uses microseconds)
    exchange_timestamp_micros = msg["T"] * 1000  # Trade time (T)
    event_timestamp_micros = msg["E"] * 1000  # Event time (E)
    ingestion_timestamp_micros = int(time.time() * 1_000_000)

    # Build v2 trade record
    return {
        "message_id": str(uuid.uuid4()),
        "trade_id": f"BINANCE-{msg['t']}",
        "symbol": symbol,
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "timestamp": exchange_timestamp_micros,  # Use trade time (T) not event time (E)
        "price": Decimal(msg["p"]),
        "quantity": Decimal(msg["q"]),
        "currency": currency,  # EXTRACTED from symbol, not hardcoded!
        "side": side,
        "trade_conditions": [],
        "source_sequence": None,  # Binance doesn't provide sequence numbers
        "ingestion_timestamp": ingestion_timestamp_micros,
        "platform_sequence": None,
        "vendor_data": {
            "is_buyer_maker": str(msg["m"]),
            "event_type": msg["e"],
            "event_time": str(msg["E"]),
            "trade_time": str(msg["T"]),
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "is_best_match": str(msg.get("M", "")),  # M field (optional)
        },
    }


class BinanceWebSocketClient:
    """Async WebSocket client for Binance trade streams.

    Connects to Binance WebSocket API, subscribes to trade streams for
    specified symbols, and converts messages to v2 schema format.

    Attributes:
        symbols: List of symbols to stream (e.g., ["BTCUSDT", "ETHUSDT"])
        on_message: Callback function for handling converted trades
        url: Binance WebSocket stream URL
        reconnect_delay: Initial reconnect delay in seconds
        max_reconnect_attempts: Maximum number of reconnect attempts
    """

    def __init__(
        self,
        symbols: list[str],
        on_message: Optional[Callable[[dict[str, Any]], None]] = None,
        url: str = "wss://stream.binance.com:9443/stream",
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
    ) -> None:
        """Initialize Binance WebSocket client.

        Args:
            symbols: List of symbols to stream (e.g., ["BTCUSDT", "ETHUSDT"])
            on_message: Callback function for handling converted v2 trades
            url: Binance WebSocket stream URL
            reconnect_delay: Initial reconnect delay in seconds
            max_reconnect_attempts: Maximum number of reconnect attempts
        """
        self.symbols = symbols
        self.on_message = on_message
        self.url = url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_running = False
        self.reconnect_attempts = 0

    async def connect(self) -> None:
        """Connect to Binance WebSocket and start receiving messages.

        Implements automatic reconnection with exponential backoff on failure.
        """
        self.is_running = True

        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                await self._connect_and_stream()
            except ConnectionClosed:
                logger.warning(
                    "binance_connection_closed",
                    attempt=self.reconnect_attempts + 1,
                    max_attempts=self.max_reconnect_attempts,
                )
                await self._handle_reconnect()
            except WebSocketException as e:
                logger.error("binance_websocket_error", error=str(e), type=type(e).__name__)
                await self._handle_reconnect()
            except Exception as e:
                logger.error("binance_unexpected_error", error=str(e), type=type(e).__name__)
                await self._handle_reconnect()

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(
                "binance_max_reconnects_exceeded",
                attempts=self.reconnect_attempts,
                max_attempts=self.max_reconnect_attempts,
            )

    async def _connect_and_stream(self) -> None:
        """Establish WebSocket connection and stream messages."""
        # Build stream URL with multiple symbols
        streams = [f"{s.lower()}@trade" for s in self.symbols]
        stream_param = "/".join(streams)
        ws_url = f"{self.url}?streams={stream_param}"

        logger.info("binance_connecting", url=ws_url, symbols=self.symbols)

        async with websockets.connect(ws_url) as ws:
            self.ws = ws
            self.reconnect_attempts = 0  # Reset on successful connection
            logger.info("binance_connected", symbols=self.symbols)

            async for message in ws:
                try:
                    await self._handle_message(message)
                except Exception as e:
                    logger.error("binance_message_error", error=str(e), message=message[:100])

    async def _handle_message(self, message: str) -> None:
        """Parse and handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message (JSON string)
        """
        # Parse JSON
        data = json.loads(message)

        # Binance multi-stream format wraps messages in {"stream": "...", "data": {...}}
        if "data" in data:
            trade_data = data["data"]
        else:
            trade_data = data

        # Convert to v2 schema
        v2_trade = convert_binance_trade_to_v2(trade_data)

        # Call user callback
        if self.on_message:
            self.on_message(v2_trade)

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self.reconnect_attempts += 1

        if self.reconnect_attempts < self.max_reconnect_attempts:
            # Exponential backoff: 5s, 10s, 20s, 40s, ...
            delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))
            delay = min(delay, 60)  # Cap at 60 seconds

            logger.info(
                "binance_reconnecting",
                attempt=self.reconnect_attempts,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)

    async def disconnect(self) -> None:
        """Gracefully disconnect from WebSocket."""
        self.is_running = False
        if self.ws:
            await self.ws.close()
            logger.info("binance_disconnected")

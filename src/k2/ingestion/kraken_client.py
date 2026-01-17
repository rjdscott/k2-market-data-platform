"""Kraken WebSocket client for real-time trade streaming.

This module provides an async WebSocket client that connects to Kraken's
trade stream API and converts trade messages to v2 schema format.

Features:
- Async/await pattern for efficient streaming
- Automatic reconnection with exponential backoff
- Message validation and error handling
- Dynamic currency extraction from pair
- Base/quote asset separation
- 6-layer resilience pattern (mirroring Binance client)

Usage:
    from k2.ingestion.kraken_client import KrakenWebSocketClient
    from k2.common.config import config

    async def handle_trade(trade_data: dict) -> None:
        print(f"Received trade: {trade_data['symbol']} @ {trade_data['price']}")

    client = KrakenWebSocketClient(
        symbols=config.kraken.symbols,
        on_message=handle_trade,
    )
    await client.connect()
"""

import asyncio
import hashlib
import json
import ssl
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import certifi
import psutil
import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from k2.common.circuit_breaker import CircuitBreaker
from k2.common.config import config
from k2.common.metrics import create_component_metrics

logger = structlog.get_logger(__name__)
metrics = create_component_metrics("kraken_streaming")


def parse_kraken_pair(pair: str) -> tuple[str, str, str]:
    """Parse Kraken pair into base asset, quote asset, and quote currency.

    Kraken uses '/' separator (e.g., 'XBT/USD', 'ETH/EUR') and maps
    XBT → BTC for compatibility with other exchanges.

    Args:
        pair: Kraken pair (e.g., 'XBT/USD', 'ETH/BTC', 'DOT/EUR')

    Returns:
        (base_asset, quote_asset, quote_currency)

    Examples:
        >>> parse_kraken_pair("XBT/USD")
        ("BTC", "USD", "USD")
        >>> parse_kraken_pair("ETH/BTC")
        ("ETH", "BTC", "BTC")
        >>> parse_kraken_pair("DOT/EUR")
        ("DOT", "EUR", "EUR")
    """
    if "/" not in pair:
        raise ValueError(f"Invalid Kraken pair format (expected '/'): {pair}")

    base, quote = pair.split("/", 1)

    # Normalize XBT → BTC for consistency with other exchanges
    if base == "XBT":
        base = "BTC"
    if quote == "XBT":
        quote = "BTC"

    return (base, quote, quote)


def _validate_kraken_message(msg: list[Any]) -> None:
    """Validate Kraken trade message has required structure.

    Kraken format: [channelID, [trade_array], "trade", "PAIR"]

    Args:
        msg: Kraken trade payload (array format)

    Raises:
        ValueError: If required structure is invalid
    """
    if not isinstance(msg, list):
        raise ValueError(f"Invalid Kraken message type: {type(msg)}, expected list")

    if len(msg) < 4:
        raise ValueError(f"Invalid Kraken message length: {len(msg)}, expected 4")

    if msg[2] != "trade":
        raise ValueError(f"Invalid channel type: {msg[2]}, expected 'trade'")

    if not isinstance(msg[1], list) or len(msg[1]) == 0:
        raise ValueError(f"Invalid trades array: {msg[1]}")


def convert_kraken_trade_to_v2(msg: list[Any]) -> dict[str, Any]:
    """Convert Kraken WebSocket trade to v2 Trade schema.

    Kraken message format:
    [
        channelID,
        [
            ["price", "volume", "timestamp", "side", "orderType", "misc"],
            ["price2", "volume2", "timestamp2", "side2", "orderType2", "misc2"]
        ],
        "trade",
        "XBT/USD"
    ]

    Args:
        msg: Kraken trade payload from WebSocket

    Returns:
        v2 Trade record conforming to TradeV2 Avro schema

    Raises:
        ValueError: If required fields missing or invalid
    """
    # Validate message structure
    _validate_kraken_message(msg)

    # Extract components
    pair = msg[3]  # e.g., "XBT/USD"
    trades_array = msg[1]  # Array of trades

    # Parse pair to extract base/quote assets and currency
    base_asset, quote_asset, currency = parse_kraken_pair(pair)

    # Build symbol without slash (e.g., "XBT/USD" → "BTCUSD")
    symbol = f"{base_asset}{currency}"

    # Process first trade from array (typically only 1 trade per message)
    trade = trades_array[0]

    # Kraken trade array: [price, volume, timestamp, side, orderType, misc]
    price_str = trade[0]
    volume_str = trade[1]
    timestamp_str = trade[2]  # Unix timestamp with microseconds (e.g., "1534614057.321597")
    side_str = trade[3]  # "b" = BUY, "s" = SELL
    order_type = trade[4]  # "l" = limit, "m" = market
    misc = trade[5] if len(trade) > 5 else ""

    # Convert side
    side = "BUY" if side_str == "b" else "SELL"

    # Convert timestamp (Kraken: seconds.microseconds → v2: microseconds)
    timestamp_float = float(timestamp_str)
    exchange_timestamp_micros = int(timestamp_float * 1_000_000)
    ingestion_timestamp_micros = int(time.time() * 1_000_000)

    # Generate trade_id from timestamp + hash of trade data for uniqueness
    # Kraken doesn't provide trade IDs, so we create deterministic ones
    trade_hash = hashlib.sha256(f"{pair}{timestamp_str}{price_str}{volume_str}".encode()).hexdigest()[:8]
    trade_id = f"KRAKEN-{int(timestamp_float * 1_000_000)}-{trade_hash}"

    # Build v2 trade record
    return {
        "message_id": str(uuid.uuid4()),
        "trade_id": trade_id,
        "symbol": symbol,
        "exchange": "KRAKEN",
        "asset_class": "crypto",
        "timestamp": exchange_timestamp_micros,
        "price": Decimal(price_str),
        "quantity": Decimal(volume_str),
        "currency": currency,
        "side": side,
        "trade_conditions": [],
        "source_sequence": None,  # Kraken doesn't provide sequence numbers
        "ingestion_timestamp": ingestion_timestamp_micros,
        "platform_sequence": None,
        "vendor_data": {
            "pair": pair,
            "order_type": order_type,
            "misc": misc,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "raw_timestamp": timestamp_str,
        },
    }


class KrakenWebSocketClient:
    """Async WebSocket client for Kraken trade streams.

    Connects to Kraken WebSocket API, subscribes to trade streams for
    specified symbols, and converts messages to v2 schema format.

    Attributes:
        symbols: List of symbols to stream (e.g., ["BTC/USD", "ETH/USD"])
        on_message: Callback function for handling converted trades
        url: Kraken WebSocket stream URL
        reconnect_delay: Initial reconnect delay in seconds
        max_reconnect_attempts: Maximum number of reconnect attempts
    """

    def __init__(
        self,
        symbols: list[str],
        on_message: Callable[[dict[str, Any]], None] | None = None,
        url: str = "wss://ws.kraken.com",
        failover_urls: list[str] | None = None,
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
        health_check_interval: int = 30,
        health_check_timeout: int = 30,
        enable_circuit_breaker: bool = True,
    ) -> None:
        """Initialize Kraken WebSocket client.

        Args:
            symbols: List of symbols to stream (e.g., ["BTC/USD", "ETH/USD"])
            on_message: Callback function for handling converted v2 trades
            url: Kraken WebSocket stream URL
            failover_urls: Failover URLs (tried in order if primary fails)
            reconnect_delay: Initial reconnect delay in seconds
            max_reconnect_attempts: Maximum number of reconnect attempts
            health_check_interval: Health check interval in seconds
            health_check_timeout: Max seconds without message before reconnect (0=disabled)
            enable_circuit_breaker: Enable circuit breaker protection
        """
        self.symbols = symbols
        self.on_message = on_message
        self.url = url
        self.failover_urls = failover_urls or []
        self.all_urls = [self.url] + self.failover_urls
        self.current_url_index = 0
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self.health_check_interval = health_check_interval
        self.health_check_timeout = health_check_timeout
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.is_running = False
        self.reconnect_attempts = 0

        # Health check state
        self.last_message_time: float = time.time()
        self.health_check_task: asyncio.Task | None = None

        # Connection rotation state (prevent memory accumulation in long-lived connections)
        self.connection_start_time: float | None = None
        self.connection_max_lifetime_seconds = config.kraken.connection_max_lifetime_hours * 3600
        self.rotation_task: asyncio.Task | None = None

        # Memory monitoring state (detect memory leaks via linear regression)
        self.memory_samples: list[tuple[float, int]] = []  # [(timestamp, rss_bytes)]
        self.memory_monitor_task: asyncio.Task | None = None
        self.memory_sample_interval_seconds = 30  # Sample memory every 30s
        self.memory_sample_window_size = 120  # Keep last 120 samples (1 hour at 30s intervals)

        # WebSocket ping-pong heartbeat state (detect silent connection drops)
        self.last_pong_time: float | None = None  # Track when last pong was received
        self.ping_task: asyncio.Task | None = None  # Async task for ping loop
        self.ping_interval_seconds = config.kraken.ping_interval_seconds  # Default: 60s
        self.ping_timeout_seconds = config.kraken.ping_timeout_seconds  # Default: 10s

        # Circuit breaker for resilience
        self.circuit_breaker: CircuitBreaker | None = None
        if enable_circuit_breaker:
            self.circuit_breaker = CircuitBreaker(
                name="kraken_websocket",
                failure_threshold=3,  # Open after 3 consecutive failures
                success_threshold=2,  # Close after 2 consecutive successes
                timeout_seconds=30.0,  # Try reset after 30s
            )

        # Metrics labels
        self.metrics_labels = {}

    async def _health_check_loop(self) -> None:
        """Monitor connection health and reconnect if stale.

        Checks last_message_time periodically and triggers reconnect
        if no messages received within timeout period.
        """
        logger.info(
            "kraken_health_check_started",
            interval_seconds=self.health_check_interval,
            timeout_seconds=self.health_check_timeout,
        )

        while self.is_running:
            await asyncio.sleep(self.health_check_interval)

            if not self.is_running:
                break

            # Check if connection is stale
            if self.health_check_timeout > 0:
                elapsed = time.time() - self.last_message_time

                if elapsed > self.health_check_timeout:
                    logger.warning(
                        "kraken_connection_stale_reconnecting",
                        seconds_since_last_message=elapsed,
                        timeout_seconds=self.health_check_timeout,
                    )

                    # Update metric
                    metrics.increment(
                        "kraken_reconnects_total",
                        labels={**self.metrics_labels, "reason": "health_check_timeout"},
                    )

                    # Close current connection to trigger reconnect
                    if self.ws:
                        await self.ws.close()

    async def _connection_rotation_loop(self) -> None:
        """Monitor connection lifetime and trigger periodic rotation.

        Rotates connections every N hours to prevent memory accumulation
        in long-lived WebSocket connections.
        """
        logger.info(
            "kraken_connection_rotation_enabled",
            max_lifetime_hours=config.kraken.connection_max_lifetime_hours,
            max_lifetime_seconds=self.connection_max_lifetime_seconds,
        )

        while self.is_running:
            await asyncio.sleep(60)  # Check every minute

            if not self.is_running:
                break

            # Skip if no active connection
            if not self.connection_start_time:
                continue

            # Calculate connection lifetime
            lifetime = time.time() - self.connection_start_time

            # Update lifetime metric
            metrics.gauge(
                "kraken_connection_lifetime_seconds",
                value=lifetime,
                labels=self.metrics_labels,
            )

            # Check if rotation needed
            if lifetime > self.connection_max_lifetime_seconds:
                logger.info(
                    "kraken_connection_rotation_triggered",
                    lifetime_seconds=lifetime,
                    max_lifetime_seconds=self.connection_max_lifetime_seconds,
                )

                # Increment rotation metric
                metrics.increment(
                    "kraken_connection_rotations_total",
                    labels={**self.metrics_labels, "reason": "scheduled_rotation"},
                )

                # Trigger graceful reconnect by closing connection
                if self.ws:
                    await self.ws.close(code=1000, reason="Periodic rotation")

    def _calculate_memory_leak_score(self) -> float:
        """Calculate memory leak detection score using linear regression.

        Analyzes memory samples over time to detect upward trend that
        indicates a memory leak. Returns a score from 0 to 1.

        Returns:
            float: Leak detection score (0.0 to 1.0)
        """
        if len(self.memory_samples) < 10:
            # Need at least 10 samples for meaningful regression
            return 0.0

        # Extract x (time elapsed) and y (memory in MB) from samples
        first_timestamp = self.memory_samples[0][0]
        x_values = [(timestamp - first_timestamp) for timestamp, _ in self.memory_samples]
        y_values = [
            rss_bytes / (1024 * 1024) for _, rss_bytes in self.memory_samples
        ]  # Convert to MB

        # Calculate means
        n = len(x_values)
        x_mean = sum(x_values) / n
        y_mean = sum(y_values) / n

        # Calculate slope (beta) using least squares
        numerator = sum((x_values[i] - x_mean) * (y_values[i] - y_mean) for i in range(n))
        denominator = sum((x_values[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator

        # Calculate R² (coefficient of determination) for confidence
        y_pred = [y_mean + slope * (x_values[i] - x_mean) for i in range(n)]
        ss_res = sum((y_values[i] - y_pred[i]) ** 2 for i in range(n))
        ss_tot = sum((y_values[i] - y_mean) ** 2 for i in range(n))

        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0.0

        # Calculate leak score
        if slope <= 0:
            return 0.0

        # Calculate growth rate in MB per hour
        mb_per_hour = slope * 3600  # Convert slope from MB/s to MB/hour

        # Score thresholds: 10 MB/hour = 0.5, 50 MB/hour = 1.0
        raw_score = min(mb_per_hour / 50, 1.0)

        # Weight by R² (confidence in linear fit)
        leak_score = raw_score * r_squared

        return min(leak_score, 1.0)

    async def _memory_monitor_loop(self) -> None:
        """Monitor process memory usage and detect leaks via linear regression."""
        logger.info(
            "kraken_memory_monitor_started",
            sample_interval_seconds=self.memory_sample_interval_seconds,
            sample_window_size=self.memory_sample_window_size,
        )

        # Get current process
        process = psutil.Process()

        while self.is_running:
            await asyncio.sleep(self.memory_sample_interval_seconds)

            if not self.is_running:
                break

            try:
                # Sample memory usage
                memory_info = process.memory_info()
                rss_bytes = memory_info.rss
                vms_bytes = memory_info.vms

                timestamp = time.time()

                # Add sample to sliding window
                self.memory_samples.append((timestamp, rss_bytes))

                # Maintain sliding window
                if len(self.memory_samples) > self.memory_sample_window_size:
                    self.memory_samples.pop(0)

                # Update memory metrics
                metrics.gauge(
                    "process_memory_rss_bytes",
                    value=rss_bytes,
                    labels=self.metrics_labels,
                )

                metrics.gauge(
                    "process_memory_vms_bytes",
                    value=vms_bytes,
                    labels=self.metrics_labels,
                )

                # Calculate and update leak detection score
                leak_score = self._calculate_memory_leak_score()
                metrics.gauge(
                    "memory_leak_detection_score",
                    value=leak_score,
                    labels=self.metrics_labels,
                )

                # Log memory stats periodically
                if len(self.memory_samples) % 10 == 0:
                    logger.info(
                        "kraken_memory_status",
                        rss_mb=rss_bytes / (1024 * 1024),
                        vms_mb=vms_bytes / (1024 * 1024),
                        leak_score=leak_score,
                        samples_count=len(self.memory_samples),
                    )

                    # Warning if leak score is high
                    if leak_score > 0.8:
                        logger.warning(
                            "kraken_memory_leak_detected",
                            leak_score=leak_score,
                            rss_mb=rss_bytes / (1024 * 1024),
                            message="High leak detection score - consider restarting or investigating",
                        )

            except Exception as e:
                logger.error(
                    "kraken_memory_monitor_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def _ping_loop(self) -> None:
        """Send WebSocket ping frames periodically to detect silent connection drops."""
        logger.info(
            "kraken_ping_loop_started",
            ping_interval_seconds=self.ping_interval_seconds,
            ping_timeout_seconds=self.ping_timeout_seconds,
        )

        while self.is_running:
            # Wait for ping interval
            await asyncio.sleep(self.ping_interval_seconds)

            if not self.is_running:
                break

            # Skip if no active connection
            if not self.ws or self.ws.closed:
                continue

            try:
                # Send ping frame and wait for pong with timeout
                pong_waiter = await self.ws.ping()

                logger.debug(
                    "kraken_ping_sent",
                    ping_interval_seconds=self.ping_interval_seconds,
                )

                # Wait for pong with timeout
                try:
                    await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout_seconds)

                    # Pong received successfully
                    self.last_pong_time = time.time()

                    # Update last pong timestamp metric
                    metrics.gauge(
                        "kraken_last_pong_timestamp_seconds",
                        value=self.last_pong_time,
                        labels=self.metrics_labels,
                    )

                    logger.debug(
                        "kraken_pong_received",
                        last_pong_time=self.last_pong_time,
                    )

                except TimeoutError:
                    # Pong timeout - connection is likely dead
                    logger.warning(
                        "kraken_pong_timeout",
                        ping_timeout_seconds=self.ping_timeout_seconds,
                        last_pong_time=self.last_pong_time,
                        message="No pong received within timeout - triggering reconnect",
                    )

                    # Increment pong timeout counter
                    metrics.increment(
                        "kraken_pong_timeouts_total",
                        labels=self.metrics_labels,
                    )

                    # Trigger reconnect by closing the websocket
                    if self.ws:
                        await self.ws.close(code=1000, reason="Pong timeout")

            except Exception as e:
                logger.error(
                    "kraken_ping_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def connect(self) -> None:
        """Connect to Kraken WebSocket and start receiving messages.

        Implements automatic reconnection with exponential backoff on failure.
        Starts health check monitoring if configured.
        """
        self.is_running = True

        # Start health check loop
        if self.health_check_timeout > 0:
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            logger.info(
                "kraken_health_check_enabled",
                interval=self.health_check_interval,
                timeout=self.health_check_timeout,
            )

        # Start connection rotation loop
        self.rotation_task = asyncio.create_task(self._connection_rotation_loop())
        logger.info(
            "kraken_connection_rotation_enabled",
            max_lifetime_hours=config.kraken.connection_max_lifetime_hours,
        )

        # Start memory monitoring loop
        self.memory_monitor_task = asyncio.create_task(self._memory_monitor_loop())
        logger.info(
            "kraken_memory_monitor_enabled",
            sample_interval_seconds=self.memory_sample_interval_seconds,
            sample_window_size=self.memory_sample_window_size,
        )

        # Start ping-pong heartbeat loop
        self.ping_task = asyncio.create_task(self._ping_loop())
        logger.info(
            "kraken_ping_loop_enabled",
            ping_interval_seconds=self.ping_interval_seconds,
            ping_timeout_seconds=self.ping_timeout_seconds,
        )

        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                await self._connect_and_stream()
            except ConnectionClosed:
                logger.warning(
                    "kraken_connection_closed",
                    attempt=self.reconnect_attempts + 1,
                    max_attempts=self.max_reconnect_attempts,
                )
                metrics.increment(
                    "kraken_reconnects_total",
                    labels={**self.metrics_labels, "reason": "connection_closed"},
                )
                await self._handle_reconnect()
            except WebSocketException as e:
                logger.error("kraken_websocket_error", error=str(e), type=type(e).__name__)
                metrics.increment(
                    "kraken_connection_errors_total",
                    labels={**self.metrics_labels, "error_type": type(e).__name__},
                )
                metrics.increment(
                    "kraken_reconnects_total",
                    labels={**self.metrics_labels, "reason": "websocket_error"},
                )
                await self._handle_reconnect()
            except Exception as e:
                logger.error("kraken_unexpected_error", error=str(e), type=type(e).__name__)
                metrics.increment(
                    "kraken_connection_errors_total",
                    labels={**self.metrics_labels, "error_type": type(e).__name__},
                )
                metrics.increment(
                    "kraken_reconnects_total",
                    labels={**self.metrics_labels, "reason": "unexpected_error"},
                )
                await self._handle_reconnect()

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(
                "kraken_max_reconnects_exceeded",
                attempts=self.reconnect_attempts,
                max_attempts=self.max_reconnect_attempts,
            )
            # Set connection status to disconnected
            metrics.gauge(
                "kraken_connection_status",
                value=0.0,
                labels=self.metrics_labels,
            )

    async def _connect_and_stream(self) -> None:
        """Establish WebSocket connection and stream messages.

        Uses failover URLs if primary connection fails.
        """
        # Try current URL (with failover rotation)
        current_url = self.all_urls[self.current_url_index]

        logger.info(
            "kraken_connecting",
            url=current_url,
            url_index=self.current_url_index,
            symbols=self.symbols,
        )

        # SSL certificate verification configuration
        if config.kraken.ssl_verify:
            # Production: Enable SSL certificate verification
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED

            # Optional: Load custom CA bundle for corporate proxies
            if config.kraken.custom_ca_bundle:
                ssl_context.load_verify_locations(config.kraken.custom_ca_bundle)
                logger.info(
                    "kraken_custom_ca_loaded",
                    ca_bundle=config.kraken.custom_ca_bundle,
                )
        else:
            # Development only: Disable SSL verification (NOT for production)
            logger.warning(
                "kraken_ssl_disabled",
                message="SSL verification disabled - NOT RECOMMENDED for production",
            )
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(current_url, ssl=ssl_context) as ws:
            self.ws = ws
            self.reconnect_attempts = 0  # Reset on successful connection
            self.last_message_time = time.time()  # Reset health check timer
            self.connection_start_time = time.time()  # Track connection lifetime for rotation

            # Update connection status metric
            metrics.gauge(
                "kraken_connection_status",
                value=1.0,
                labels=self.metrics_labels,
            )

            logger.info(
                "kraken_connected",
                url=current_url,
                symbols=self.symbols,
            )

            # Notify circuit breaker of success (if enabled)
            if self.circuit_breaker:
                self.circuit_breaker._on_success()

            # Subscribe to trade streams
            subscribe_msg = {
                "event": "subscribe",
                "pair": self.symbols,
                "subscription": {"name": "trade"},
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info("kraken_subscribed", symbols=self.symbols)

            async for message in ws:
                try:
                    await self._handle_message(message)
                except Exception as e:
                    logger.error(
                        "kraken_message_error",
                        error=str(e),
                        error_type=type(e).__name__,
                        message=message[:100],
                    )
                    metrics.increment(
                        "kraken_message_errors_total",
                        labels={**self.metrics_labels, "error_type": type(e).__name__},
                    )

    async def _handle_message(self, message: str) -> None:
        """Parse and handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message (JSON string)
        """
        # Update health check timestamp
        self.last_message_time = time.time()

        # Update last message timestamp metric
        metrics.gauge(
            "kraken_last_message_timestamp_seconds",
            value=self.last_message_time,
            labels=self.metrics_labels,
        )

        # Parse JSON
        data = json.loads(message)

        # Skip system messages (subscriptionStatus, heartbeat, etc.)
        if isinstance(data, dict):
            # System messages like {"event": "subscriptionStatus", ...}
            if data.get("event") in ["subscriptionStatus", "systemStatus", "heartbeat"]:
                logger.debug(
                    "kraken_system_message",
                    event=data.get("event"),
                    status=data.get("status"),
                )
                return

        # Trade messages are arrays: [channelID, [...], "trade", "PAIR"]
        if isinstance(data, list) and len(data) >= 4 and data[2] == "trade":
            # Convert to v2 schema
            v2_trade = convert_kraken_trade_to_v2(data)

            # Update message received metric (per symbol)
            metrics.increment(
                "kraken_messages_received_total",
                labels={**self.metrics_labels, "symbol": v2_trade["symbol"]},
            )

            # Call user callback
            if self.on_message:
                self.on_message(v2_trade)

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff and failover rotation."""
        self.reconnect_attempts += 1

        # Set connection status to disconnected
        metrics.gauge(
            "kraken_connection_status",
            value=0.0,
            labels=self.metrics_labels,
        )

        if self.reconnect_attempts < self.max_reconnect_attempts:
            # Try next failover URL (if available)
            if len(self.all_urls) > 1:
                self.current_url_index = (self.current_url_index + 1) % len(self.all_urls)
                logger.info(
                    "kraken_failover_url_rotation",
                    new_url_index=self.current_url_index,
                    new_url=self.all_urls[self.current_url_index],
                )

            # Exponential backoff: 5s, 10s, 20s, 40s, ...
            delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))
            delay = min(delay, 60)  # Cap at 60 seconds

            # Update reconnect delay metric
            metrics.gauge(
                "kraken_reconnect_delay_seconds",
                value=float(delay),
                labels=self.metrics_labels,
            )

            logger.info(
                "kraken_reconnecting",
                attempt=self.reconnect_attempts,
                delay_seconds=delay,
                url=self.all_urls[self.current_url_index],
            )
            await asyncio.sleep(delay)

    async def disconnect(self) -> None:
        """Gracefully disconnect from WebSocket and stop monitoring tasks."""
        self.is_running = False

        # Cancel health check task
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                logger.info("kraken_health_check_cancelled")

        # Cancel rotation task
        if self.rotation_task:
            self.rotation_task.cancel()
            try:
                await self.rotation_task
            except asyncio.CancelledError:
                logger.info("kraken_rotation_task_cancelled")

        # Cancel memory monitor task
        if self.memory_monitor_task:
            self.memory_monitor_task.cancel()
            try:
                await self.memory_monitor_task
            except asyncio.CancelledError:
                logger.info("kraken_memory_monitor_cancelled")

        # Cancel ping task
        if self.ping_task:
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                logger.info("kraken_ping_task_cancelled")

        # Close WebSocket
        if self.ws:
            await self.ws.close()

        # Update connection status metric
        metrics.gauge(
            "kraken_connection_status",
            value=0.0,
            labels=self.metrics_labels,
        )

        logger.info("kraken_disconnected")

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
import ssl
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Optional

import certifi
import psutil
import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from k2.common.circuit_breaker import CircuitBreaker
from k2.common.config import config
from k2.common.metrics import create_component_metrics

logger = structlog.get_logger(__name__)
metrics = create_component_metrics("binance_streaming")


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
        failover_urls: Optional[list[str]] = None,
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
        health_check_interval: int = 30,
        health_check_timeout: int = 60,
        enable_circuit_breaker: bool = True,
    ) -> None:
        """Initialize Binance WebSocket client.

        Args:
            symbols: List of symbols to stream (e.g., ["BTCUSDT", "ETHUSDT"])
            on_message: Callback function for handling converted v2 trades
            url: Binance WebSocket stream URL
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
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_running = False
        self.reconnect_attempts = 0

        # Health check state
        self.last_message_time: float = time.time()
        self.health_check_task: Optional[asyncio.Task] = None

        # Connection rotation state (prevent memory accumulation in long-lived connections)
        self.connection_start_time: Optional[float] = None
        self.connection_max_lifetime_seconds = (
            config.binance.connection_max_lifetime_hours * 3600
        )
        self.rotation_task: Optional[asyncio.Task] = None

        # Memory monitoring state (detect memory leaks via linear regression)
        self.memory_samples: list[tuple[float, int]] = []  # [(timestamp, rss_bytes)]
        self.memory_monitor_task: Optional[asyncio.Task] = None
        self.memory_sample_interval_seconds = 30  # Sample memory every 30s
        self.memory_sample_window_size = 120  # Keep last 120 samples (1 hour at 30s intervals)

        # WebSocket ping-pong heartbeat state (detect silent connection drops)
        self.last_pong_time: Optional[float] = None  # Track when last pong was received
        self.ping_task: Optional[asyncio.Task] = None  # Async task for ping loop
        self.ping_interval_seconds = config.binance.ping_interval_seconds  # Default: 180s (3 min)
        self.ping_timeout_seconds = config.binance.ping_timeout_seconds  # Default: 10s

        # Circuit breaker for resilience
        self.circuit_breaker: Optional[CircuitBreaker] = None
        if enable_circuit_breaker:
            self.circuit_breaker = CircuitBreaker(
                name="binance_websocket",
                failure_threshold=3,  # Open after 3 consecutive failures
                success_threshold=2,  # Close after 2 consecutive successes
                timeout_seconds=30.0,  # Try reset after 30s
            )

        # Metrics labels (removed symbols label - not defined in metrics registry)
        self.metrics_labels = {}

    async def _health_check_loop(self) -> None:
        """Monitor connection health and reconnect if stale.

        Checks last_message_time periodically and triggers reconnect
        if no messages received within timeout period.
        """
        logger.info(
            "binance_health_check_started",
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
                        "binance_connection_stale_reconnecting",
                        seconds_since_last_message=elapsed,
                        timeout_seconds=self.health_check_timeout,
                    )

                    # Update metric
                    metrics.increment(
                        "binance_reconnects_total",
                        labels={**self.metrics_labels, "reason": "health_check_timeout"},
                    )

                    # Close current connection to trigger reconnect
                    if self.ws:
                        await self.ws.close()

    async def _connection_rotation_loop(self) -> None:
        """Monitor connection lifetime and trigger periodic rotation.

        Rotates connections every N hours to prevent memory accumulation
        in long-lived WebSocket connections. This addresses memory leaks
        from frame buffers and other connection state.
        """
        logger.info(
            "binance_connection_rotation_enabled",
            max_lifetime_hours=config.binance.connection_max_lifetime_hours,
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
                "binance_connection_lifetime_seconds",
                value=lifetime,
                labels=self.metrics_labels,
            )

            # Check if rotation needed
            if lifetime > self.connection_max_lifetime_seconds:
                logger.info(
                    "binance_connection_rotation_triggered",
                    lifetime_seconds=lifetime,
                    max_lifetime_seconds=self.connection_max_lifetime_seconds,
                )

                # Increment rotation metric
                metrics.increment(
                    "binance_connection_rotations_total",
                    labels={**self.metrics_labels, "reason": "scheduled_rotation"},
                )

                # Trigger graceful reconnect by closing connection
                # The main connect loop will automatically reconnect
                if self.ws:
                    await self.ws.close(code=1000, reason="Periodic rotation")

    def _calculate_memory_leak_score(self) -> float:
        """Calculate memory leak detection score using linear regression.

        Analyzes memory samples over time to detect upward trend that
        indicates a memory leak. Returns a score from 0 to 1:
        - 0.0 = no leak detected (flat or decreasing trend)
        - 0.5 = moderate growth
        - 0.8+ = likely memory leak (significant upward trend)

        Uses simple linear regression on (timestamp, rss_bytes) samples.

        Returns:
            float: Leak detection score (0.0 to 1.0)
        """
        if len(self.memory_samples) < 10:
            # Need at least 10 samples for meaningful regression
            return 0.0

        # Extract x (time elapsed) and y (memory in MB) from samples
        first_timestamp = self.memory_samples[0][0]
        x_values = [(timestamp - first_timestamp) for timestamp, _ in self.memory_samples]
        y_values = [rss_bytes / (1024 * 1024) for _, rss_bytes in self.memory_samples]  # Convert to MB

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

        # Calculate leak score:
        # - If slope is negative (memory decreasing), score = 0
        # - If slope is positive, scale based on MB/hour growth rate and R²
        # - R² weights the score by how well the linear model fits (confidence)
        if slope <= 0:
            return 0.0

        # Calculate growth rate in MB per hour
        time_span_hours = (x_values[-1] - x_values[0]) / 3600 if x_values[-1] != x_values[0] else 1
        mb_per_hour = slope * 3600  # Convert slope from MB/s to MB/hour

        # Score thresholds:
        # - 10 MB/hour = 0.5 score (moderate)
        # - 50 MB/hour = 1.0 score (severe)
        raw_score = min(mb_per_hour / 50, 1.0)

        # Weight by R² (confidence in linear fit)
        # If R² is low, reduce the score (less confident it's a real leak)
        leak_score = raw_score * r_squared

        return min(leak_score, 1.0)

    async def _memory_monitor_loop(self) -> None:
        """Monitor process memory usage and detect leaks via linear regression.

        Samples RSS and VMS memory every 30 seconds, maintains a sliding window
        of samples, calculates leak detection score, and updates Prometheus metrics.
        """
        logger.info(
            "binance_memory_monitor_started",
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
                rss_bytes = memory_info.rss  # Resident Set Size (actual physical memory)
                vms_bytes = memory_info.vms  # Virtual Memory Size (total virtual memory)

                timestamp = time.time()

                # Add sample to sliding window
                self.memory_samples.append((timestamp, rss_bytes))

                # Maintain sliding window (keep last N samples)
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

                # Log memory stats periodically (every 10 samples = 5 minutes at 30s intervals)
                if len(self.memory_samples) % 10 == 0:
                    logger.info(
                        "binance_memory_status",
                        rss_mb=rss_bytes / (1024 * 1024),
                        vms_mb=vms_bytes / (1024 * 1024),
                        leak_score=leak_score,
                        samples_count=len(self.memory_samples),
                    )

                    # Warning if leak score is high
                    if leak_score > 0.8:
                        logger.warning(
                            "binance_memory_leak_detected",
                            leak_score=leak_score,
                            rss_mb=rss_bytes / (1024 * 1024),
                            message="High leak detection score - consider restarting or investigating",
                        )

            except Exception as e:
                logger.error(
                    "binance_memory_monitor_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def _ping_loop(self) -> None:
        """Send WebSocket ping frames periodically to detect silent connection drops.

        Binance requires ping every <10 minutes to keep connection alive.
        This loop sends pings every 3 minutes (configurable) and monitors
        for pong responses. If pong is not received within timeout (10s),
        triggers reconnection.
        """
        logger.info(
            "binance_ping_loop_started",
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
                # The websockets library automatically handles pong responses
                pong_waiter = await self.ws.ping()

                logger.debug(
                    "binance_ping_sent",
                    ping_interval_seconds=self.ping_interval_seconds,
                )

                # Wait for pong with timeout
                try:
                    await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout_seconds)

                    # Pong received successfully
                    self.last_pong_time = time.time()

                    # Update last pong timestamp metric
                    metrics.gauge(
                        "binance_last_pong_timestamp_seconds",
                        value=self.last_pong_time,
                        labels=self.metrics_labels,
                    )

                    logger.debug(
                        "binance_pong_received",
                        last_pong_time=self.last_pong_time,
                    )

                except asyncio.TimeoutError:
                    # Pong timeout - connection is likely dead
                    logger.warning(
                        "binance_pong_timeout",
                        ping_timeout_seconds=self.ping_timeout_seconds,
                        last_pong_time=self.last_pong_time,
                        message="No pong received within timeout - triggering reconnect",
                    )

                    # Increment pong timeout counter
                    metrics.increment(
                        "binance_pong_timeouts_total",
                        labels=self.metrics_labels,
                    )

                    # Trigger reconnect by closing the websocket
                    # The main connect loop will automatically reconnect
                    if self.ws:
                        await self.ws.close(code=1000, reason="Pong timeout")

            except Exception as e:
                logger.error(
                    "binance_ping_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def connect(self) -> None:
        """Connect to Binance WebSocket and start receiving messages.

        Implements automatic reconnection with exponential backoff on failure.
        Starts health check monitoring if configured.
        """
        self.is_running = True

        # Start health check loop
        if self.health_check_timeout > 0:
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            logger.info(
                "binance_health_check_enabled",
                interval=self.health_check_interval,
                timeout=self.health_check_timeout,
            )

        # Start connection rotation loop
        self.rotation_task = asyncio.create_task(self._connection_rotation_loop())
        logger.info(
            "binance_connection_rotation_enabled",
            max_lifetime_hours=config.binance.connection_max_lifetime_hours,
        )

        # Start memory monitoring loop
        self.memory_monitor_task = asyncio.create_task(self._memory_monitor_loop())
        logger.info(
            "binance_memory_monitor_enabled",
            sample_interval_seconds=self.memory_sample_interval_seconds,
            sample_window_size=self.memory_sample_window_size,
        )

        # Start ping-pong heartbeat loop
        self.ping_task = asyncio.create_task(self._ping_loop())
        logger.info(
            "binance_ping_loop_enabled",
            ping_interval_seconds=self.ping_interval_seconds,
            ping_timeout_seconds=self.ping_timeout_seconds,
        )

        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                await self._connect_and_stream()
            except ConnectionClosed:
                logger.warning(
                    "binance_connection_closed",
                    attempt=self.reconnect_attempts + 1,
                    max_attempts=self.max_reconnect_attempts,
                )
                metrics.increment(
                    "binance_reconnects_total",
                    labels={**self.metrics_labels, "reason": "connection_closed"},
                )
                await self._handle_reconnect()
            except WebSocketException as e:
                logger.error("binance_websocket_error", error=str(e), type=type(e).__name__)
                metrics.increment(
                    "binance_connection_errors_total",
                    labels={**self.metrics_labels, "error_type": type(e).__name__},
                )
                metrics.increment(
                    "binance_reconnects_total",
                    labels={**self.metrics_labels, "reason": "websocket_error"},
                )
                await self._handle_reconnect()
            except Exception as e:
                logger.error("binance_unexpected_error", error=str(e), type=type(e).__name__)
                metrics.increment(
                    "binance_connection_errors_total",
                    labels={**self.metrics_labels, "error_type": type(e).__name__},
                )
                metrics.increment(
                    "binance_reconnects_total",
                    labels={**self.metrics_labels, "reason": "unexpected_error"},
                )
                await self._handle_reconnect()

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(
                "binance_max_reconnects_exceeded",
                attempts=self.reconnect_attempts,
                max_attempts=self.max_reconnect_attempts,
            )
            # Set connection status to disconnected
            metrics.gauge(
                "binance_connection_status",
                value=0.0,
                labels=self.metrics_labels,
            )

    async def _connect_and_stream(self) -> None:
        """Establish WebSocket connection and stream messages.

        Uses failover URLs if primary connection fails.
        """
        # Build stream URL with multiple symbols
        streams = [f"{s.lower()}@trade" for s in self.symbols]
        stream_param = "/".join(streams)

        # Try current URL (with failover rotation)
        current_url = self.all_urls[self.current_url_index]
        ws_url = f"{current_url}?streams={stream_param}"

        logger.info(
            "binance_connecting",
            url=current_url,
            url_index=self.current_url_index,
            symbols=self.symbols,
        )

        # SSL certificate verification configuration
        if config.binance.ssl_verify:
            # Production: Enable SSL certificate verification
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED

            # Optional: Load custom CA bundle for corporate proxies
            if config.binance.custom_ca_bundle:
                ssl_context.load_verify_locations(config.binance.custom_ca_bundle)
                logger.info(
                    "binance_custom_ca_loaded",
                    ca_bundle=config.binance.custom_ca_bundle,
                )
        else:
            # Development only: Disable SSL verification (NOT for production)
            logger.warning(
                "binance_ssl_disabled",
                message="SSL verification disabled - NOT RECOMMENDED for production",
            )
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(ws_url, ssl=ssl_context) as ws:
            self.ws = ws
            self.reconnect_attempts = 0  # Reset on successful connection
            self.last_message_time = time.time()  # Reset health check timer
            self.connection_start_time = time.time()  # Track connection lifetime for rotation

            # Update connection status metric
            metrics.gauge(
                "binance_connection_status",
                value=1.0,
                labels=self.metrics_labels,
            )

            logger.info(
                "binance_connected",
                url=current_url,
                symbols=self.symbols,
            )

            # Notify circuit breaker of success (if enabled)
            if self.circuit_breaker:
                self.circuit_breaker._on_success()

            async for message in ws:
                try:
                    await self._handle_message(message)
                except Exception as e:
                    logger.error(
                        "binance_message_error",
                        error=str(e),
                        error_type=type(e).__name__,
                        message=message[:100],
                    )
                    metrics.increment(
                        "binance_message_errors_total",
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
            "binance_last_message_timestamp_seconds",
            value=self.last_message_time,
            labels=self.metrics_labels,
        )

        # Parse JSON
        data = json.loads(message)

        # Binance multi-stream format wraps messages in {"stream": "...", "data": {...}}
        if "data" in data:
            trade_data = data["data"]
        else:
            trade_data = data

        # Convert to v2 schema
        v2_trade = convert_binance_trade_to_v2(trade_data)

        # Update message received metric (per symbol)
        metrics.increment(
            "binance_messages_received_total",
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
            "binance_connection_status",
            value=0.0,
            labels=self.metrics_labels,
        )

        if self.reconnect_attempts < self.max_reconnect_attempts:
            # Try next failover URL (if available)
            if len(self.all_urls) > 1:
                self.current_url_index = (self.current_url_index + 1) % len(self.all_urls)
                logger.info(
                    "binance_failover_url_rotation",
                    new_url_index=self.current_url_index,
                    new_url=self.all_urls[self.current_url_index],
                )

            # Exponential backoff: 5s, 10s, 20s, 40s, ...
            delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))
            delay = min(delay, 60)  # Cap at 60 seconds

            # Update reconnect delay metric
            metrics.gauge(
                "binance_reconnect_delay_seconds",
                value=float(delay),
                labels=self.metrics_labels,
            )

            logger.info(
                "binance_reconnecting",
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
                logger.info("binance_health_check_cancelled")

        # Cancel rotation task
        if self.rotation_task:
            self.rotation_task.cancel()
            try:
                await self.rotation_task
            except asyncio.CancelledError:
                logger.info("binance_rotation_task_cancelled")

        # Cancel memory monitor task
        if self.memory_monitor_task:
            self.memory_monitor_task.cancel()
            try:
                await self.memory_monitor_task
            except asyncio.CancelledError:
                logger.info("binance_memory_monitor_cancelled")

        # Cancel ping task
        if self.ping_task:
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                logger.info("binance_ping_task_cancelled")

        # Close WebSocket
        if self.ws:
            await self.ws.close()

        # Update connection status metric
        metrics.gauge(
            "binance_connection_status",
            value=0.0,
            labels=self.metrics_labels,
        )

        logger.info("binance_disconnected")

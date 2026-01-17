#!/usr/bin/env python3
"""Binance streaming service - production daemon.

This service connects to Binance WebSocket API, streams live trades,
converts them to v2 schema format, and publishes to Kafka.

Architecture:
    Binance WebSocket → on_message → MarketDataProducer → Kafka

Features:
- Real-time trade streaming from Binance
- Automatic reconnection with exponential backoff
- V2 schema conversion with vendor_data
- Idempotent Kafka production (at-least-once with deduplication)
- Graceful shutdown (SIGINT/SIGTERM handlers)
- Comprehensive logging and statistics

Usage:
    # Stream default symbols (from config or BTCUSDT, ETHUSDT)
    python scripts/binance_stream.py

    # Stream custom symbols
    python scripts/binance_stream.py --symbols BTCUSDT ETHBTC BNBEUR

    # Run as daemon with info logging
    python scripts/binance_stream.py --daemon --log-level info

    # Stop with Ctrl+C (graceful shutdown)

Environment Variables:
    K2_KAFKA_BOOTSTRAP_SERVERS: Kafka brokers (default: localhost:9092)
    K2_KAFKA_SCHEMA_REGISTRY_URL: Schema Registry URL (default: http://localhost:8081)
    K2_BINANCE_WEBSOCKET_URL: Binance WebSocket URL (default: wss://stream.binance.com:9443/stream)
    K2_BINANCE_SYMBOLS: Comma-separated symbols (default: BTCUSDT,ETHUSDT)
"""

import asyncio
import signal
import sys
from pathlib import Path

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from rich.console import Console

from k2.common.config import config
from k2.ingestion.binance_client import BinanceWebSocketClient
from k2.ingestion.producer import MarketDataProducer

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger(__name__)
console = Console()

# Global flag for graceful shutdown
shutdown_event = asyncio.Event()


def signal_handler(signum: int, frame) -> None:
    """Handle SIGINT and SIGTERM for graceful shutdown.

    Args:
        signum: Signal number
        frame: Current stack frame
    """
    logger.info("shutdown_signal_received", signal=signal.Signals(signum).name)
    shutdown_event.set()


async def main() -> None:
    """Main streaming service."""
    # Parse command line arguments
    import argparse

    parser = argparse.ArgumentParser(
        description="Binance WebSocket streaming service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stream default symbols
  python scripts/binance_stream.py

  # Stream custom symbols
  python scripts/binance_stream.py --symbols BTCUSDT ETHBTC BNBEUR

  # Run as daemon
  python scripts/binance_stream.py --daemon --log-level info

Environment Variables:
  K2_KAFKA_BOOTSTRAP_SERVERS    Kafka brokers
  K2_KAFKA_SCHEMA_REGISTRY_URL  Schema Registry URL
  K2_BINANCE_WEBSOCKET_URL      Binance WebSocket URL
  K2_BINANCE_SYMBOLS            Comma-separated symbols
        """,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help=f"Symbols to stream (default: {config.binance.symbols})",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon (no console output, logs only)",
    )
    args = parser.parse_args()

    # Use config symbols if not overridden
    symbols = args.symbols or config.binance.symbols

    # Set log level (note: structlog configuration is global, this is informational)
    log_level = args.log_level.upper()

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Print startup banner (unless daemon mode)
    if not args.daemon:
        console.print("\n[bold blue]Binance Streaming Service[/bold blue]")
        console.print("=" * 50)
        console.print(f"Symbols: {', '.join(symbols)}")
        console.print(f"Log Level: {log_level}")
        console.print(f"Kafka: {config.kafka.bootstrap_servers}")
        console.print(f"Schema Registry: {config.kafka.schema_registry_url}")
        console.print("=" * 50)
        console.print("\n[yellow]Initializing...[/yellow]\n")

    # Initialize Kafka producer with v2 schema
    try:
        producer = MarketDataProducer(schema_version="v2")
        logger.info(
            "producer_initialized",
            bootstrap_servers=config.kafka.bootstrap_servers,
            schema_version="v2",
        )
    except Exception as e:
        logger.error("producer_init_failed", error=str(e))
        if not args.daemon:
            console.print(f"\n[red]Failed to initialize Kafka producer: {e}[/red]\n")
        sys.exit(1)

    # Track statistics
    trade_count = 0
    error_count = 0

    def handle_trade(trade: dict) -> None:
        """Handle incoming trade from WebSocket.

        Args:
            trade: v2 Trade record from convert_binance_trade_to_v2()
        """
        nonlocal trade_count, error_count

        try:
            # Produce to Kafka topic: market.crypto.trades
            producer.produce_trade(
                asset_class="crypto",
                exchange="binance",
                record=trade,
            )

            trade_count += 1

            # Log every 100 trades
            if trade_count % 100 == 0:
                logger.info(
                    "streaming_progress",
                    trades_streamed=trade_count,
                    errors=error_count,
                    symbol=trade["symbol"],
                    last_price=str(trade["price"]),
                    last_quantity=str(trade["quantity"]),
                )

                if not args.daemon:
                    console.print(
                        f"[green]✓[/green] Streamed {trade_count} trades "
                        f"(errors: {error_count}) | Last: {trade['symbol']} @ {trade['price']}"
                    )

        except Exception as e:
            error_count += 1
            logger.error(
                "trade_produce_error",
                error=str(e),
                symbol=trade.get("symbol"),
                trade_id=trade.get("trade_id"),
            )

    # Create WebSocket client with resilience features
    client = BinanceWebSocketClient(
        symbols=symbols,
        on_message=handle_trade,
        url=config.binance.websocket_url,
        failover_urls=config.binance.failover_urls,
        reconnect_delay=config.binance.reconnect_delay,
        max_reconnect_attempts=config.binance.max_reconnect_attempts,
        health_check_interval=config.binance.health_check_interval,
        health_check_timeout=config.binance.health_check_timeout,
        enable_circuit_breaker=True,  # Enable circuit breaker for production resilience
    )

    logger.info(
        "binance_client_created",
        symbols=symbols,
        websocket_url=config.binance.websocket_url,
        failover_urls=config.binance.failover_urls,
        reconnect_delay=config.binance.reconnect_delay,
        max_reconnect_attempts=config.binance.max_reconnect_attempts,
        health_check_interval=config.binance.health_check_interval,
        health_check_timeout=config.binance.health_check_timeout,
    )

    # Start streaming
    try:
        # Create client task
        client_task = asyncio.create_task(client.connect())

        # Wait for shutdown signal
        logger.info("streaming_started", symbols=symbols)
        if not args.daemon:
            console.print("[bold green]✓ Streaming started![/bold green]\n")
            console.print("[dim]Press Ctrl+C to stop gracefully[/dim]\n")

        # Wait for shutdown event
        await shutdown_event.wait()

        # Graceful shutdown sequence
        logger.info("initiating_graceful_shutdown")
        if not args.daemon:
            console.print("\n[yellow]Shutting down gracefully...[/yellow]")

        # Step 1: Disconnect WebSocket (stop receiving new trades)
        logger.info("disconnecting_websocket")
        await client.disconnect()

        # Step 2: Wait for client task to complete (with timeout)
        try:
            await asyncio.wait_for(client_task, timeout=5.0)
            logger.info("client_task_completed")
        except TimeoutError:
            logger.warning("client_task_timeout", timeout_seconds=5)
            client_task.cancel()
            try:
                await client_task
            except asyncio.CancelledError:
                logger.info("client_task_cancelled")

        # Step 3: Flush producer queue (send all buffered messages)
        logger.info("flushing_producer_queue")
        if not args.daemon:
            console.print("[yellow]Flushing Kafka producer queue...[/yellow]")

        remaining = producer.flush(timeout=10.0)
        if remaining > 0:
            logger.warning("messages_not_flushed", remaining=remaining)
            if not args.daemon:
                console.print(f"[yellow]Warning: {remaining} messages not flushed[/yellow]")

        # Step 4: Close producer
        producer.close()
        logger.info("producer_closed")

        # Print final statistics
        stats = producer.get_stats()
        logger.info(
            "shutdown_complete",
            trades_received=trade_count,
            trades_produced=stats["produced"],
            producer_errors=stats["errors"],
            handler_errors=error_count,
            retries=stats["retries"],
        )

        if not args.daemon:
            console.print("\n[bold green]✓ Shutdown complete![/bold green]")
            console.print("=" * 50)
            console.print(f"Trades received: {trade_count}")
            console.print(f"Trades produced: {stats['produced']}")
            console.print(f"Producer errors: {stats['errors']}")
            console.print(f"Handler errors: {error_count}")
            console.print(f"Retries: {stats['retries']}")
            console.print("=" * 50 + "\n")

    except KeyboardInterrupt:
        # This should be caught by signal handler, but handle it anyway
        logger.info("keyboard_interrupt")
        if not args.daemon:
            console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        logger.error("streaming_service_error", error=str(e), type=type(e).__name__)
        if not args.daemon:
            console.print(f"\n[red]Fatal error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Already handled in main(), just exit cleanly
        pass

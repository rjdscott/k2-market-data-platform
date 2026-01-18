#!/usr/bin/env python3
"""Binance streaming service - RAW data to Bronze.

This service connects to Binance WebSocket API, streams live trades,
and publishes RAW data to Kafka (no V2 conversion).

Architecture:
    Binance WebSocket → on_message → RawBinanceProducer → Kafka (raw)

This implements industry best practice:
- Bronze layer stores exchange-native data
- Silver layer applies V2 transformation (Spark job)
- Gold layer unifies across exchanges

Usage:
    # Stream default symbols
    python scripts/binance_stream_raw.py

    # Stream custom symbols
    python scripts/binance_stream_raw.py --symbols BTCUSDT ETHUSDT ETHBTC

Environment Variables:
    K2_KAFKA_BOOTSTRAP_SERVERS: Kafka brokers (default: localhost:9092)
    K2_KAFKA_SCHEMA_REGISTRY_URL: Schema Registry URL (default: http://localhost:8081)
    K2_BINANCE_WEBSOCKET_URL: Binance WebSocket URL
    K2_BINANCE_SYMBOLS: Comma-separated symbols
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
from k2.ingestion.raw_producer import RawBinanceProducer

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
    """Handle SIGINT and SIGTERM for graceful shutdown."""
    logger.info("shutdown_signal_received", signal=signal.Signals(signum).name)
    shutdown_event.set()


async def main() -> None:
    """Main streaming service - RAW data."""
    # Parse command line arguments
    import argparse

    parser = argparse.ArgumentParser(
        description="Binance WebSocket streaming service (RAW data to Bronze)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Print startup banner (unless daemon mode)
    if not args.daemon:
        console.print("\n[bold blue]Binance Streaming Service (RAW)[/bold blue]")
        console.print("=" * 50)
        console.print(f"Symbols: {', '.join(symbols)}")
        console.print(f"Log Level: {args.log_level.upper()}")
        console.print(f"Kafka: {config.kafka.bootstrap_servers}")
        console.print(f"Schema Registry: {config.kafka.schema_registry_url}")
        console.print("[yellow]Mode: RAW data (Bronze layer)[/yellow]")
        console.print("=" * 50)
        console.print("\n[yellow]Initializing...[/yellow]\n")

    # Initialize Kafka producer for raw data
    try:
        producer = RawBinanceProducer()
        logger.info(
            "raw_producer_initialized",
            bootstrap_servers=config.kafka.bootstrap_servers,
            topic="market.crypto.trades.binance.raw",
        )
    except Exception as e:
        logger.error("raw_producer_initialization_failed", error=str(e))
        console.print(f"\n[red]✗ Failed to initialize raw producer: {e}[/red]\n")
        return 1

    # Message counter for periodic flushing
    message_count = 0

    # Define callback for WebSocket messages
    def on_message(raw_trade: dict) -> None:
        """Handle raw Binance trade - send directly to Kafka."""
        nonlocal message_count
        try:
            producer.produce(raw_trade)
            message_count += 1

            # Flush every 100 messages to ensure messages are sent
            if message_count % 100 == 0:
                producer.flush(timeout=1.0)
                logger.debug("producer_flushed", message_count=message_count)
        except Exception as e:
            logger.error(
                "raw_message_produce_failed",
                error=str(e),
                symbol=raw_trade.get("s"),
            )

    # Initialize Binance WebSocket client in RAW mode
    try:
        # Get failover URLs if available (optional)
        failover_urls = getattr(config.binance, 'failover_websocket_urls', None)

        client = BinanceWebSocketClient(
            symbols=symbols,
            on_message=on_message,
            url=config.binance.websocket_url,
            failover_urls=failover_urls,
            raw_mode=True,  # Pass raw Binance data without V2 conversion
        )
        logger.info("binance_websocket_client_initialized", symbols=symbols, mode="RAW")
    except Exception as e:
        logger.error("binance_client_initialization_failed", error=str(e))
        console.print(f"\n[red]✗ Failed to initialize Binance client: {e}[/red]\n")
        producer.close()
        return 1

    if not args.daemon:
        console.print("[green]✓ Initialization complete[/green]\n")
        console.print("Starting data stream...")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        # Start WebSocket connection
        await client.connect()

        # Wait for shutdown signal
        await shutdown_event.wait()

        logger.info("shutdown_initiated")

    except Exception as e:
        logger.error("stream_error", error=str(e))
        if not args.daemon:
            console.print(f"\n[red]✗ Stream error: {e}[/red]\n")
        return 1

    finally:
        # Cleanup
        logger.info("cleanup_started")
        if client:
            await client.close()
        if producer:
            logger.info("flushing_producer")
            producer.flush()
            producer.close()
        logger.info("cleanup_complete")

        if not args.daemon:
            console.print("\n[green]✓ Shutdown complete[/green]\n")

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console = Console()
        console.print("\n[yellow]Interrupted by user[/yellow]\n")
        sys.exit(0)

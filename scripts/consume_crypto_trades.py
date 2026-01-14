#!/usr/bin/env python3
"""Kafka → Iceberg Consumer for Cryptocurrency Trades (v2 Schema).

This script consumes trade messages from the market.crypto.trades Kafka topic
and writes them to the Iceberg trades_v2 table. Designed for production use
with the Binance WebSocket streaming service.

Features:
- Consumes from market.crypto.trades topic
- Deserializes v2 Avro messages with Schema Registry
- Writes to market_data.trades_v2 Iceberg table
- Batch processing (default: 1000 messages per batch)
- Graceful shutdown on SIGINT/SIGTERM
- Comprehensive metrics and logging
- Daemon mode for continuous ingestion

Architecture:
    Kafka (market.crypto.trades)
        ↓ [Avro v2 messages]
    Consumer (this script)
        ↓ [Batch processing]
    Iceberg Writer
        ↓ [PyArrow conversion]
    Iceberg Table (trades_v2)
        ↓ [Parquet files]
    MinIO (S3-compatible storage)

Usage:
    # Daemon mode (runs until Ctrl+C)
    python scripts/consume_crypto_trades.py

    # Daemon mode with custom batch size
    python scripts/consume_crypto_trades.py --batch-size 500

    # Batch mode (consume N messages and exit)
    python scripts/consume_crypto_trades.py --max-messages 1000

    # With custom log level
    python scripts/consume_crypto_trades.py --log-level DEBUG

    # With custom consumer group
    python scripts/consume_crypto_trades.py --consumer-group my-consumer-group

Docker Usage:
    docker compose up -d consumer-crypto

Environment Variables:
    K2_KAFKA_BOOTSTRAP_SERVERS: Kafka broker addresses (default: localhost:9092)
    K2_KAFKA_SCHEMA_REGISTRY_URL: Schema Registry URL (default: http://localhost:8081)
    K2_ICEBERG_CATALOG_URI: Iceberg REST catalog URI (default: http://localhost:8181)
    K2_LOG_LEVEL: Logging level (default: INFO)

Related:
- Binance streaming: scripts/binance_stream.py
- Query API: src/k2/api/v1/endpoints.py
- Consumer module: src/k2/ingestion/consumer.py
"""

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, NoReturn

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from k2.common.config import config
from k2.common.logging import get_logger
from k2.ingestion.consumer import MarketDataConsumer
from k2.storage.writer import IcebergWriter

# Initialize logger and Rich console
logger = get_logger(__name__)
console = Console()

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    """Handle shutdown signals gracefully.

    Args:
        signum: Signal number
        frame: Current stack frame (unused)
    """
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    console.print(f"\n[yellow]⚠ Received {signal_name}, shutting down gracefully...[/yellow]")
    shutdown_requested = True


def create_stats_table(consumer: MarketDataConsumer, start_time: float) -> Table:
    """Create a Rich table showing consumer statistics.

    Args:
        consumer: MarketDataConsumer instance
        start_time: Unix timestamp when consumer started

    Returns:
        Rich Table with formatted statistics
    """
    stats = consumer.stats
    elapsed = time.time() - start_time

    table = Table(title="🔄 Crypto Trades Consumer Stats", show_header=False)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")

    # Runtime
    table.add_row("Runtime", f"{elapsed:.1f}s")

    # Messages
    table.add_row("Messages Consumed", f"{stats.messages_consumed:,}")
    table.add_row("Messages Written", f"{stats.messages_written:,}")
    table.add_row("Throughput", f"{stats.throughput:.1f} msg/s")

    # Errors
    if stats.errors > 0:
        table.add_row("Errors", f"[red]{stats.errors:,}[/red]")
    else:
        table.add_row("Errors", "0")

    # Sequence tracking
    if stats.sequence_gaps > 0:
        table.add_row("Sequence Gaps", f"[yellow]{stats.sequence_gaps:,}[/yellow]")
    if stats.duplicates_skipped > 0:
        table.add_row("Duplicates Skipped", f"{stats.duplicates_skipped:,}")

    # Current state
    table.add_row("Batch Size", f"{consumer.batch_size:,}")
    table.add_row("Current Batch", f"{len(consumer.current_batch):,}")

    return table


def run_consumer_with_ui(
    consumer: MarketDataConsumer,
    max_messages: int | None = None,
) -> None:
    """Run consumer with Rich UI updates.

    Args:
        consumer: MarketDataConsumer instance
        max_messages: Maximum messages to consume (None for unlimited)
    """
    start_time = time.time()

    # Create progress bar
    with Live(console=console, refresh_per_second=2) as live:
        while not shutdown_requested:
            # Update display
            stats_table = create_stats_table(consumer, start_time)
            panel = Panel(
                stats_table,
                title=f"[bold green]K2 Consumer[/bold green] | Topic: market.crypto.trades | "
                f"Schema: v2",
                border_style="green",
            )
            live.update(panel)

            # Check if max messages reached
            if max_messages and consumer.stats.messages_consumed >= max_messages:
                console.print(f"\n[green]✓ Reached maximum messages: {max_messages:,}[/green]")
                break

            # Small sleep to avoid CPU spinning (consumer polls in background)
            time.sleep(0.5)


def main() -> NoReturn:
    """Main entry point for crypto trades consumer."""
    parser = argparse.ArgumentParser(
        description="Consume cryptocurrency trades from Kafka to Iceberg (v2 schema)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Daemon mode (runs until Ctrl+C)
  python scripts/consume_crypto_trades.py

  # Consume 1000 messages and exit
  python scripts/consume_crypto_trades.py --max-messages 1000

  # Custom batch size and log level
  python scripts/consume_crypto_trades.py --batch-size 500 --log-level DEBUG

  # Custom consumer group
  python scripts/consume_crypto_trades.py --consumer-group my-group
        """,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of messages to batch before writing to Iceberg (default: 1000)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Maximum messages to consume before exiting (default: unlimited)",
    )
    parser.add_argument(
        "--consumer-group",
        type=str,
        default="k2-iceberg-writer-crypto-v2",
        help="Kafka consumer group ID (default: k2-iceberg-writer-crypto-v2)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable Rich UI (useful for Docker logs)",
    )

    args = parser.parse_args()

    # Configure logging
    # Note: log_level configuration removed as it's not part of K2Config

    # Display startup banner
    console.print(Panel.fit(
        "[bold cyan]K2 Market Data Platform[/bold cyan]\n"
        "[white]Crypto Trades Consumer (v2 Schema)[/white]\n"
        f"[dim]Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}[/dim]",
        border_style="cyan",
    ))

    # Display configuration
    config_table = Table(title="⚙ Configuration", show_header=False)
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="yellow")
    config_table.add_row("Kafka Brokers", config.kafka.bootstrap_servers)
    config_table.add_row("Schema Registry", config.kafka.schema_registry_url)
    config_table.add_row("Topic", "market.crypto.trades")
    config_table.add_row("Consumer Group", args.consumer_group)
    config_table.add_row("Batch Size", f"{args.batch_size:,}")
    config_table.add_row("Max Messages", f"{args.max_messages:,}" if args.max_messages else "Unlimited")
    config_table.add_row("Schema Version", "v2")
    config_table.add_row("Iceberg Table", "market_data.trades_v2")
    config_table.add_row("Log Level", args.log_level)
    console.print(config_table)
    console.print()

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Initialize Iceberg writer
        console.print("[cyan]Initializing Iceberg writer...[/cyan]")
        writer = IcebergWriter(schema_version="v2")
        console.print("[green]✓ Iceberg writer initialized[/green]\n")

        # Initialize consumer
        console.print("[cyan]Initializing Kafka consumer...[/cyan]")
        consumer = MarketDataConsumer(
            topics=["market.crypto.trades.binance"],  # Binance crypto trades topic
            batch_size=args.batch_size,
            iceberg_writer=writer,
            consumer_group=args.consumer_group,
        )
        console.print("[green]✓ Kafka consumer initialized[/green]\n")

        # Start consuming
        console.print("[bold green]🚀 Starting consumption...[/bold green]\n")

        if args.no_ui:
            # No UI mode (for Docker logs)
            consumer.run(max_messages=args.max_messages)
        else:
            # Rich UI mode (for terminal)
            import threading

            # Run consumer in background thread
            consumer_thread = threading.Thread(
                target=consumer.run,
                kwargs={"max_messages": args.max_messages},
                daemon=True,
            )
            consumer_thread.start()

            # Run UI in main thread
            run_consumer_with_ui(consumer, args.max_messages)

            # Wait for consumer thread to finish
            consumer_thread.join(timeout=5.0)

        # Print final statistics
        stats = consumer.stats
        console.print(Panel.fit(
            f"[bold green]✓ Consumer Completed[/bold green]\n\n"
            f"Messages Consumed: {stats.messages_consumed:,}\n"
            f"Messages Written: {stats.messages_written:,}\n"
            f"Errors: {stats.errors:,}\n"
            f"Throughput: {stats.throughput:.1f} msg/s\n"
            f"Duration: {stats.duration_seconds:.1f}s",
            border_style="green",
        ))

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Interrupted by user[/yellow]")
        sys.exit(130)  # Standard exit code for SIGINT
    except Exception as e:
        logger.exception("Consumer failed with unexpected error")
        console.print(f"\n[bold red]✗ Error: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()

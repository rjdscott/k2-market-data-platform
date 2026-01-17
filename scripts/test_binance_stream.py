#!/usr/bin/env python3
"""Test script for Binance WebSocket streaming.

This script connects to Binance WebSocket API, streams live trades for
BTCUSDT and ETHUSDT, and prints the converted v2 trade data.

Usage:
    python scripts/test_binance_stream.py
    # Or with custom symbols:
    python scripts/test_binance_stream.py --symbols BTCUSDT ETHBTC BNBEUR
"""

import asyncio
import sys
from pathlib import Path

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from rich.console import Console
from rich.table import Table

from k2.ingestion.binance_client import BinanceWebSocketClient

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

# Global counter for received trades
trade_count = 0
MAX_TRADES = 10  # Stop after receiving this many trades


def print_trade(trade: dict) -> None:
    """Print trade data in a nicely formatted table.

    Args:
        trade: v2 Trade record
    """
    global trade_count
    trade_count += 1

    # Create table for first trade
    if trade_count == 1:
        console.print("\n[bold green]✓ Connected to Binance WebSocket[/bold green]\n")
        console.print(
            f"[yellow]Streaming trades... (will stop after {MAX_TRADES} trades)[/yellow]\n"
        )

    # Create trade info table
    table = Table(title=f"Trade #{trade_count}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="magenta")

    # Display key fields
    table.add_row("Symbol", trade["symbol"])
    table.add_row("Exchange", trade["exchange"])
    table.add_row("Side", trade["side"])
    table.add_row("Price", f"{trade['price']}")
    table.add_row("Quantity", f"{trade['quantity']}")
    table.add_row("Currency", trade["currency"])
    table.add_row("Base Asset", trade["vendor_data"]["base_asset"])  # From vendor_data
    table.add_row("Quote Asset", trade["vendor_data"]["quote_asset"])  # From vendor_data
    table.add_row("Trade ID", trade["trade_id"])

    console.print(table)
    console.print()  # Empty line


async def main() -> None:
    """Main function to test Binance WebSocket client."""
    # Parse command line arguments
    import argparse

    parser = argparse.ArgumentParser(description="Test Binance WebSocket streaming")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT"],
        help="Symbols to stream (default: BTCUSDT ETHUSDT)",
    )
    args = parser.parse_args()

    console.print("[bold blue]Testing Binance WebSocket Client[/bold blue]\n")
    console.print(f"Symbols: {', '.join(args.symbols)}")
    console.print("Connecting...")

    # Track if we should stop
    stop_event = asyncio.Event()

    def handle_trade(trade: dict) -> None:
        """Handle incoming trade."""
        print_trade(trade)

        # Stop after MAX_TRADES trades
        if trade_count >= MAX_TRADES:
            logger.info("max_trades_reached", count=trade_count)
            stop_event.set()

    # Create and connect client
    client = BinanceWebSocketClient(
        symbols=args.symbols,
        on_message=handle_trade,
    )

    # Run client and wait for stop event
    try:
        client_task = asyncio.create_task(client.connect())
        await stop_event.wait()
        await client.disconnect()
        await client_task
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        await client.disconnect()
    except Exception as e:
        logger.error("test_failed", error=str(e))
        console.print(f"\n[red]Error: {e}[/red]")
        await client.disconnect()

    console.print("\n[bold green]✓ Test complete![/bold green]")
    console.print(f"Received {trade_count} trades")


if __name__ == "__main__":
    asyncio.run(main())

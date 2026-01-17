#!/usr/bin/env python3
"""Comprehensive validation script for WebSocket streaming.

This script validates both Binance and Kraken WebSocket clients by:
1. Connecting to both exchanges simultaneously
2. Collecting 10 trades from each exchange
3. Validating v2 schema compliance
4. Checking message rates and latency
5. Generating a validation report

Usage:
    python scripts/validate_streaming.py
    # Or test only one exchange:
    python scripts/validate_streaming.py --exchange binance
    python scripts/validate_streaming.py --exchange kraken
"""

import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from rich.console import Console
from rich.table import Table

from k2.ingestion.binance_client import BinanceWebSocketClient
from k2.ingestion.kraken_client import KrakenWebSocketClient

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

# Validation state
validation_results = defaultdict(list)
trade_timestamps = defaultdict(list)
TARGET_TRADES_PER_EXCHANGE = 10


def validate_v2_schema(trade: dict, exchange: str) -> tuple[bool, list[str]]:
    """Validate that trade conforms to v2 schema.

    Args:
        trade: Trade record to validate
        exchange: Expected exchange name

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
        "trade_conditions",
        "source_sequence",
        "ingestion_timestamp",
        "platform_sequence",
        "vendor_data",
    ]

    for field in required_fields:
        if field not in trade:
            errors.append(f"Missing required field: {field}")

    # Validate field values
    if "exchange" in trade and trade["exchange"] != exchange:
        errors.append(f"Invalid exchange: {trade['exchange']} (expected {exchange})")

    if "asset_class" in trade and trade["asset_class"] != "crypto":
        errors.append(f"Invalid asset_class: {trade['asset_class']} (expected 'crypto')")

    if "side" in trade and trade["side"] not in ["BUY", "SELL"]:
        errors.append(f"Invalid side: {trade['side']} (expected BUY or SELL)")

    # Validate price and quantity are positive
    if "price" in trade:
        try:
            price_val = float(trade["price"])
            if price_val <= 0:
                errors.append(f"Invalid price: {price_val} (must be positive)")
        except (ValueError, TypeError):
            errors.append(f"Invalid price type: {type(trade['price'])}")

    if "quantity" in trade:
        try:
            qty_val = float(trade["quantity"])
            if qty_val <= 0:
                errors.append(f"Invalid quantity: {qty_val} (must be positive)")
        except (ValueError, TypeError):
            errors.append(f"Invalid quantity type: {type(trade['quantity'])}")

    # Validate timestamps
    if "timestamp" in trade:
        try:
            ts_val = int(trade["timestamp"])
            if ts_val <= 0:
                errors.append(f"Invalid timestamp: {ts_val} (must be positive)")
            # Check timestamp is reasonable (after 2020, before 2030)
            if ts_val < 1577836800000000 or ts_val > 1893456000000000:  # 2020-01-01 to 2030-01-01
                errors.append(f"Timestamp out of reasonable range: {ts_val}")
        except (ValueError, TypeError):
            errors.append(f"Invalid timestamp type: {type(trade['timestamp'])}")

    # Validate message_id is UUID-like (36 chars with hyphens)
    if "message_id" in trade:
        msg_id = trade["message_id"]
        if not isinstance(msg_id, str) or len(msg_id) != 36:
            errors.append(f"Invalid message_id format: {msg_id} (expected UUID)")

    return (len(errors) == 0, errors)


async def validate_binance(stop_event: asyncio.Event) -> None:
    """Validate Binance streaming."""
    console.print("\n[cyan]Starting Binance validation...[/cyan]")

    trade_count = 0

    def handle_trade(trade: dict) -> None:
        nonlocal trade_count
        trade_count += 1

        # Record timestamp for rate calculation
        trade_timestamps["BINANCE"].append(time.time())

        # Validate schema
        is_valid, errors = validate_v2_schema(trade, "BINANCE")
        validation_results["BINANCE"].append(
            {"trade_id": trade.get("trade_id", "unknown"), "valid": is_valid, "errors": errors}
        )

        if not is_valid:
            console.print(f"[red]❌ Invalid Binance trade: {errors}[/red]")
        else:
            console.print(
                f"[green]✓[/green] Binance trade #{trade_count}: {trade['symbol']} @ {trade['price']}"
            )

        # Stop after target
        if trade_count >= TARGET_TRADES_PER_EXCHANGE:
            logger.info("binance_target_reached", count=trade_count)
            stop_event.set()

    client = BinanceWebSocketClient(
        symbols=["BTCUSDT", "ETHUSDT"],
        on_message=handle_trade,
    )

    try:
        client_task = asyncio.create_task(client.connect())
        await stop_event.wait()
        await client.disconnect()
        await client_task
    except Exception as e:
        logger.error("binance_validation_failed", error=str(e))
        await client.disconnect()


async def validate_kraken(stop_event: asyncio.Event) -> None:
    """Validate Kraken streaming."""
    console.print("\n[cyan]Starting Kraken validation...[/cyan]")

    trade_count = 0

    def handle_trade(trade: dict) -> None:
        nonlocal trade_count
        trade_count += 1

        # Record timestamp for rate calculation
        trade_timestamps["KRAKEN"].append(time.time())

        # Validate schema
        is_valid, errors = validate_v2_schema(trade, "KRAKEN")
        validation_results["KRAKEN"].append(
            {"trade_id": trade.get("trade_id", "unknown"), "valid": is_valid, "errors": errors}
        )

        if not is_valid:
            console.print(f"[red]❌ Invalid Kraken trade: {errors}[/red]")
        else:
            console.print(
                f"[green]✓[/green] Kraken trade #{trade_count}: {trade['symbol']} @ {trade['price']}"
            )

        # Stop after target
        if trade_count >= TARGET_TRADES_PER_EXCHANGE:
            logger.info("kraken_target_reached", count=trade_count)
            stop_event.set()

    client = KrakenWebSocketClient(
        symbols=["BTC/USD", "ETH/USD"],
        on_message=handle_trade,
    )

    try:
        client_task = asyncio.create_task(client.connect())
        await stop_event.wait()
        await client.disconnect()
        await client_task
    except Exception as e:
        logger.error("kraken_validation_failed", error=str(e))
        await client.disconnect()


def generate_report() -> None:
    """Generate validation report."""
    console.print("\n" + "=" * 60)
    console.print("[bold yellow]Validation Report[/bold yellow]")
    console.print("=" * 60 + "\n")

    for exchange in ["BINANCE", "KRAKEN"]:
        if exchange not in validation_results:
            continue

        results = validation_results[exchange]
        timestamps = trade_timestamps[exchange]

        # Calculate statistics
        total = len(results)
        valid = sum(1 for r in results if r["valid"])
        invalid = total - valid

        # Calculate rate (trades per second)
        if len(timestamps) >= 2:
            duration = timestamps[-1] - timestamps[0]
            rate = (len(timestamps) - 1) / duration if duration > 0 else 0
        else:
            rate = 0

        # Create summary table
        table = Table(title=f"{exchange} Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Total Trades", str(total))
        table.add_row("Valid Trades", f"[green]{valid}[/green]")
        table.add_row("Invalid Trades", f"[red]{invalid}[/red]" if invalid > 0 else "0")
        table.add_row("Success Rate", f"{(valid / total * 100):.1f}%" if total > 0 else "N/A")
        table.add_row("Message Rate", f"{rate:.2f} trades/sec")

        console.print(table)
        console.print()

        # Show errors if any
        if invalid > 0:
            console.print(f"[red]Errors for {exchange}:[/red]")
            for result in results:
                if not result["valid"]:
                    console.print(f"  Trade {result['trade_id']}: {result['errors']}")
            console.print()

    # Overall summary
    total_valid = sum(
        sum(1 for r in validation_results[ex] if r["valid"])
        for ex in validation_results
    )
    total_trades = sum(len(validation_results[ex]) for ex in validation_results)

    if total_trades > 0:
        overall_success = (total_valid / total_trades) * 100
        if overall_success == 100:
            console.print("[bold green]✓ All validations passed![/bold green]")
        else:
            console.print(
                f"[bold yellow]⚠ {overall_success:.1f}% success rate[/bold yellow]"
            )
    else:
        console.print("[bold red]✗ No trades received[/bold red]")


async def main() -> None:
    """Main validation function."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate WebSocket streaming")
    parser.add_argument(
        "--exchange",
        choices=["binance", "kraken", "both"],
        default="both",
        help="Exchange to validate (default: both)",
    )
    args = parser.parse_args()

    console.print("[bold blue]WebSocket Streaming Validation[/bold blue]")
    console.print(f"Exchange: {args.exchange}")
    console.print(f"Target: {TARGET_TRADES_PER_EXCHANGE} trades per exchange\n")

    try:
        if args.exchange in ["binance", "both"]:
            binance_stop = asyncio.Event()
            binance_task = asyncio.create_task(validate_binance(binance_stop))
        else:
            binance_task = None

        if args.exchange in ["kraken", "both"]:
            kraken_stop = asyncio.Event()
            kraken_task = asyncio.create_task(validate_kraken(kraken_stop))
        else:
            kraken_task = None

        # Wait for both to complete
        tasks = [t for t in [binance_task, kraken_task] if t is not None]
        await asyncio.gather(*tasks, return_exceptions=True)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        logger.error("validation_failed", error=str(e))
        console.print(f"\n[red]Error: {e}[/red]")

    # Generate report
    generate_report()


if __name__ == "__main__":
    asyncio.run(main())

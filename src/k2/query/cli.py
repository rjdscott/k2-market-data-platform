"""Query CLI - Command-line interface for K2 market data queries.

Provides user-friendly commands for querying trades, quotes, and snapshots
stored in Iceberg tables via DuckDB.

Usage:
    k2-query trades --symbol BHP --limit 10
    k2-query quotes --symbol BHP --limit 10
    k2-query summary BHP 2024-01-15
    k2-query snapshots
    k2-query stats
    k2-query replay --symbol BHP --start 2024-01-01

Entry point configured in pyproject.toml as 'k2-query'.
"""
from typing import Optional
from datetime import datetime, date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from k2.query.engine import QueryEngine
from k2.query.replay import ReplayEngine

app = typer.Typer(
    name="k2-query",
    help="Query K2 market data stored in Iceberg tables.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def _parse_date(date_str: str) -> date:
    """Parse date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise typer.BadParameter(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def _parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string in various formats."""
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise typer.BadParameter(
        f"Invalid datetime format: {dt_str}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
    )


@app.command()
def trades(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
    exchange: Optional[str] = typer.Option(None, "--exchange", "-e", help="Filter by exchange"),
    start: Optional[str] = typer.Option(None, "--start", help="Start time (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, "--end", help="End time (YYYY-MM-DD)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum rows to return"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table, csv, json"),
):
    """
    Query trade records.

    Examples:
        k2-query trades --symbol BHP --limit 10
        k2-query trades --exchange ASX --start 2024-01-01
        k2-query trades -s BHP -n 5 -o json
    """
    try:
        start_time = _parse_datetime(start) if start else None
        end_time = _parse_datetime(end) if end else None

        with QueryEngine() as engine:
            trades = engine.query_trades(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

        if not trades:
            console.print("[yellow]No trades found matching criteria[/yellow]")
            raise typer.Exit(0)

        if output == "json":
            import json
            console.print(json.dumps(trades, indent=2, default=str))
        elif output == "csv":
            if trades:
                headers = list(trades[0].keys())
                console.print(",".join(headers))
                for trade in trades:
                    console.print(",".join(str(trade.get(h, "")) for h in headers))
        else:
            table = Table(title=f"Trades ({len(trades)} rows)", box=box.ROUNDED)
            table.add_column("Symbol", style="cyan")
            table.add_column("Exchange", style="green")
            table.add_column("Timestamp")
            table.add_column("Price", justify="right", style="yellow")
            table.add_column("Volume", justify="right")
            table.add_column("Venue")

            for trade in trades:
                table.add_row(
                    str(trade.get("symbol", "")),
                    str(trade.get("exchange", "")),
                    str(trade.get("exchange_timestamp", ""))[:19],
                    f"{trade.get('price', 0):.4f}",
                    str(trade.get("volume", "")),
                    str(trade.get("venue", "")),
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def quotes(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
    exchange: Optional[str] = typer.Option(None, "--exchange", "-e", help="Filter by exchange"),
    start: Optional[str] = typer.Option(None, "--start", help="Start time (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, "--end", help="End time (YYYY-MM-DD)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum rows to return"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table, csv, json"),
):
    """
    Query quote records (bid/ask).

    Examples:
        k2-query quotes --symbol BHP --limit 10
        k2-query quotes --exchange ASX
    """
    try:
        start_time = _parse_datetime(start) if start else None
        end_time = _parse_datetime(end) if end else None

        with QueryEngine() as engine:
            quote_data = engine.query_quotes(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

        if not quote_data:
            console.print("[yellow]No quotes found matching criteria[/yellow]")
            raise typer.Exit(0)

        if output == "json":
            import json
            console.print(json.dumps(quote_data, indent=2, default=str))
        elif output == "csv":
            if quote_data:
                headers = list(quote_data[0].keys())
                console.print(",".join(headers))
                for quote in quote_data:
                    console.print(",".join(str(quote.get(h, "")) for h in headers))
        else:
            table = Table(title=f"Quotes ({len(quote_data)} rows)", box=box.ROUNDED)
            table.add_column("Symbol", style="cyan")
            table.add_column("Exchange", style="green")
            table.add_column("Timestamp")
            table.add_column("Bid", justify="right", style="green")
            table.add_column("Bid Vol", justify="right")
            table.add_column("Ask", justify="right", style="red")
            table.add_column("Ask Vol", justify="right")
            table.add_column("Spread", justify="right", style="yellow")

            for quote in quote_data:
                bid = quote.get("bid_price", 0) or 0
                ask = quote.get("ask_price", 0) or 0
                spread = float(ask) - float(bid) if bid and ask else 0

                table.add_row(
                    str(quote.get("symbol", "")),
                    str(quote.get("exchange", "")),
                    str(quote.get("exchange_timestamp", ""))[:19],
                    f"{bid:.4f}" if bid else "-",
                    str(quote.get("bid_volume", "")),
                    f"{ask:.4f}" if ask else "-",
                    str(quote.get("ask_volume", "")),
                    f"{spread:.4f}" if spread else "-",
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def summary(
    symbol: str = typer.Argument(..., help="Symbol to query (e.g., BHP)"),
    query_date: str = typer.Argument(..., help="Date to summarize (YYYY-MM-DD)"),
    exchange: Optional[str] = typer.Option(None, "--exchange", "-e", help="Filter by exchange"),
):
    """
    Get OHLCV market summary for a symbol on a date.

    Examples:
        k2-query summary BHP 2024-01-15
        k2-query summary BHP 2024-01-15 --exchange ASX
    """
    try:
        parsed_date = _parse_date(query_date)

        with QueryEngine() as engine:
            result = engine.get_market_summary(
                symbol=symbol,
                query_date=parsed_date,
                exchange=exchange,
            )

        if not result:
            console.print(f"[yellow]No data found for {symbol} on {query_date}[/yellow]")
            raise typer.Exit(0)

        panel = Panel(
            f"""
[cyan]Symbol:[/cyan]      {result.symbol}
[cyan]Date:[/cyan]        {result.date}

[green]Open:[/green]        {result.open_price:.4f}
[green]High:[/green]        {result.high_price:.4f}
[green]Low:[/green]         {result.low_price:.4f}
[green]Close:[/green]       {result.close_price:.4f}

[yellow]Volume:[/yellow]      {result.volume:,}
[yellow]Trades:[/yellow]      {result.trade_count:,}
[yellow]VWAP:[/yellow]        {result.vwap:.4f}
            """.strip(),
            title=f"Market Summary: {symbol}",
            border_style="cyan",
        )
        console.print(panel)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def snapshots(
    table: str = typer.Option("market_data.trades", "--table", "-t", help="Table name"),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum snapshots to show"),
):
    """
    List Iceberg table snapshots.

    Examples:
        k2-query snapshots
        k2-query snapshots --table market_data.quotes --limit 5
    """
    try:
        with ReplayEngine() as engine:
            snapshot_list = engine.list_snapshots(table_name=table, limit=limit)

        if not snapshot_list:
            console.print(f"[yellow]No snapshots found for {table}[/yellow]")
            raise typer.Exit(0)

        snap_table = Table(
            title=f"Snapshots: {table} ({len(snapshot_list)} shown)",
            box=box.ROUNDED,
        )
        snap_table.add_column("Snapshot ID", style="cyan")
        snap_table.add_column("Timestamp")
        snap_table.add_column("Records", justify="right", style="green")
        snap_table.add_column("Added", justify="right", style="yellow")
        snap_table.add_column("Parent ID")

        for snapshot in snapshot_list:
            snap_table.add_row(
                str(snapshot.snapshot_id),
                str(snapshot.timestamp)[:19],
                str(snapshot.total_records),
                str(snapshot.added_records),
                str(snapshot.parent_id) if snapshot.parent_id else "-",
            )

        console.print(snap_table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def stats(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
):
    """
    Show query engine and data statistics.

    Examples:
        k2-query stats
        k2-query stats --symbol BHP
    """
    try:
        with QueryEngine() as engine:
            engine_stats = engine.get_stats()

        with ReplayEngine() as replay:
            replay_stats = replay.get_replay_stats(symbol=symbol)

        console.print()
        console.print(Panel("[bold]K2 Query Engine Statistics[/bold]", border_style="cyan"))

        # Engine info
        table = Table(box=box.SIMPLE)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("S3 Endpoint", engine_stats.get("s3_endpoint", "N/A"))
        table.add_row("Warehouse Path", engine_stats.get("warehouse_path", "N/A"))
        table.add_row("Connection Active", str(engine_stats.get("connection_active", False)))
        table.add_row("Total Trades", f"{engine_stats.get('trades_count', 0):,}")
        table.add_row("Total Quotes", f"{engine_stats.get('quotes_count', 0):,}")

        console.print(table)

        # Replay stats
        console.print()
        if symbol:
            console.print(f"[bold]Replay Stats for {symbol}:[/bold]")
        else:
            console.print("[bold]Replay Stats (All Data):[/bold]")

        replay_table = Table(box=box.SIMPLE)
        replay_table.add_column("Metric", style="cyan")
        replay_table.add_column("Value", style="green")

        replay_table.add_row("Total Records", f"{replay_stats.get('total_records', 0):,}")
        replay_table.add_row("Unique Symbols", str(replay_stats.get("unique_symbols", 0)))
        replay_table.add_row("Unique Exchanges", str(replay_stats.get("unique_exchanges", 0)))
        replay_table.add_row("Snapshot Count", str(replay_stats.get("snapshot_count", 0)))
        replay_table.add_row(
            "Date Range",
            f"{replay_stats.get('min_timestamp', 'N/A')} to {replay_stats.get('max_timestamp', 'N/A')}"
        )

        console.print(replay_table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def symbols(
    exchange: Optional[str] = typer.Option(None, "--exchange", "-e", help="Filter by exchange"),
):
    """
    List available symbols.

    Examples:
        k2-query symbols
        k2-query symbols --exchange ASX
    """
    try:
        with QueryEngine() as engine:
            symbol_list = engine.get_symbols(exchange=exchange)

        if not symbol_list:
            console.print("[yellow]No symbols found[/yellow]")
            raise typer.Exit(0)

        console.print(f"\n[bold]Available Symbols ({len(symbol_list)}):[/bold]\n")

        # Display in columns
        cols = 5
        for i in range(0, len(symbol_list), cols):
            row = symbol_list[i:i + cols]
            console.print("  " + "  ".join(f"[cyan]{s:10}[/cyan]" for s in row))

        console.print()

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def replay(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
    exchange: Optional[str] = typer.Option(None, "--exchange", "-e", help="Filter by exchange"),
    start: Optional[str] = typer.Option(None, "--start", help="Start time (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, "--end", help="End time (YYYY-MM-DD)"),
    batch_size: int = typer.Option(100, "--batch-size", "-b", help="Records per batch"),
    max_batches: int = typer.Option(5, "--max-batches", "-m", help="Maximum batches to show"),
):
    """
    Replay historical data in batches.

    Examples:
        k2-query replay --symbol BHP --batch-size 50
        k2-query replay --start 2024-01-01 --end 2024-01-31
    """
    try:
        start_time = _parse_datetime(start) if start else None
        end_time = _parse_datetime(end) if end else None

        with ReplayEngine() as engine:
            batch_count = 0
            total_records = 0

            console.print(f"\n[bold]Replaying data (max {max_batches} batches)...[/bold]\n")

            for batch in engine.cold_start_replay(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
                batch_size=batch_size,
            ):
                batch_count += 1
                total_records += len(batch)

                if batch:
                    first = batch[0]
                    last = batch[-1]
                    console.print(
                        f"  Batch {batch_count}: {len(batch)} records "
                        f"({first['exchange_timestamp']} to {last['exchange_timestamp']})"
                    )

                if batch_count >= max_batches:
                    console.print(f"\n[yellow]Stopped after {max_batches} batches (use --max-batches for more)[/yellow]")
                    break

            console.print(f"\n[green]Replayed {total_records:,} records in {batch_count} batches[/green]\n")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def main():
    """Entry point for k2-query CLI."""
    app()


if __name__ == "__main__":
    main()

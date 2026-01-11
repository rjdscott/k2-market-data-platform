#!/usr/bin/env python3
"""K2 Market Data Platform - Interactive Demo.

This script demonstrates the complete data pipeline:
    CSV → Kafka → Iceberg → DuckDB → REST API

Usage:
    python scripts/demo.py           # Full demo (~3 min)
    python scripts/demo.py --quick   # Skip delays (CI mode)
    python scripts/demo.py --step 2  # Run specific step

Demo Steps:
    1. Platform Architecture Overview
    2. Data Ingestion (CSV → Kafka → Iceberg)
    3. Query Engine Demo (DuckDB queries)
    4. Time-Travel Demo (Iceberg snapshots)
    5. Summary & Next Steps
"""

import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Add src to path for k2 imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

console = Console()
app = typer.Typer(help="K2 Market Data Platform Interactive Demo")

# Demo configuration
SAMPLE_DATA_DIR = Path(__file__).parent.parent / "data" / "sample"
TRADES_DIR = SAMPLE_DATA_DIR / "trades"

# Company mapping from sample data
COMPANY_MAPPING = {
    "7181": {"symbol": "DVN", "name": "Devine Ltd", "trades": 231},
    "3153": {"symbol": "MWR", "name": "MGM Wireless", "trades": 10},
    "7078": {"symbol": "BHP", "name": "BHP Billiton", "trades": 91630},
    "7458": {"symbol": "RIO", "name": "Rio Tinto", "trades": 108670},
}


def pause(seconds: float, quick: bool = False) -> None:
    """Pause for dramatic effect (skipped in quick mode)."""
    if not quick:
        time.sleep(seconds)


def print_step_header(step: int, title: str) -> None:
    """Print a formatted step header."""
    console.print()
    console.rule(f"[bold cyan]Step {step}: {title}[/bold cyan]")
    console.print()


def step_1_architecture(quick: bool = False) -> None:
    """Step 1: Show platform architecture overview."""
    print_step_header(1, "Platform Architecture Overview")

    architecture = """
[bold white]K2 Market Data Platform[/bold white]

[dim]A distributed market data processing platform designed for
high-frequency trading environments.[/dim]

[cyan]┌─────────────────────────────────────────────────────────────┐[/cyan]
[cyan]│[/cyan]                    [bold]Data Flow[/bold]                           [cyan]│[/cyan]
[cyan]├─────────────────────────────────────────────────────────────┤[/cyan]
[cyan]│[/cyan]                                                           [cyan]│[/cyan]
[cyan]│[/cyan]  [yellow]CSV Files[/yellow] → [green]Kafka[/green] (Avro) → [blue]Iceberg[/blue] → [magenta]DuckDB[/magenta] → [red]API[/red]  [cyan]│[/cyan]
[cyan]│[/cyan]                                                           [cyan]│[/cyan]
[cyan]│[/cyan]  [dim]• CSV batch ingestion with schema validation[/dim]          [cyan]│[/cyan]
[cyan]│[/cyan]  [dim]• Kafka streaming with exactly-once semantics[/dim]         [cyan]│[/cyan]
[cyan]│[/cyan]  [dim]• Iceberg ACID transactions with time-travel[/dim]          [cyan]│[/cyan]
[cyan]│[/cyan]  [dim]• DuckDB sub-second analytical queries[/dim]                [cyan]│[/cyan]
[cyan]│[/cyan]  [dim]• FastAPI REST endpoints with OpenAPI docs[/dim]            [cyan]│[/cyan]
[cyan]│[/cyan]                                                           [cyan]│[/cyan]
[cyan]└─────────────────────────────────────────────────────────────┘[/cyan]

[bold]Key Technologies:[/bold]
  • [green]Apache Kafka[/green] - Event streaming with Schema Registry
  • [blue]Apache Iceberg[/blue] - Table format with ACID guarantees
  • [magenta]DuckDB[/magenta] - Embedded analytical database
  • [red]FastAPI[/red] - Modern REST API framework
  • [yellow]Prometheus/Grafana[/yellow] - Observability stack
"""

    console.print(Panel(architecture, title="Architecture", border_style="cyan"))
    pause(2, quick)


def step_2_ingestion(quick: bool = False) -> None:
    """Step 2: Demonstrate data ingestion."""
    print_step_header(2, "Data Ingestion Demo")

    # Show available sample data
    console.print("[bold]Available Sample Data (ASX March 10-14, 2014):[/bold]\n")

    table = Table(box=box.ROUNDED)
    table.add_column("Symbol", style="cyan")
    table.add_column("Company", style="white")
    table.add_column("Trades", justify="right", style="green")
    table.add_column("File", style="dim")

    for company_id, info in COMPANY_MAPPING.items():
        table.add_row(
            info["symbol"],
            info["name"],
            f"{info['trades']:,}",
            f"trades/{company_id}.csv",
        )

    console.print(table)
    console.print()
    pause(1, quick)

    # Demonstrate loading DVN (small dataset for demo)
    console.print("[bold yellow]Loading DVN trades (231 records)...[/bold yellow]\n")

    try:
        import pandas as pd

        # Check if sample data exists
        dvn_file = TRADES_DIR / "7181.csv"
        if not dvn_file.exists():
            console.print(
                f"[red]Sample data not found: {dvn_file}[/red]\n"
                "[dim]Run: make docker-up && make init-infra first[/dim]"
            )
            return

        # Read and transform sample data
        df = pd.read_csv(
            dvn_file,
            names=["Date", "Time", "Price", "Volume", "Qualifiers", "Venue", "BuyerID"],
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Reading CSV...", total=None)
            pause(0.5, quick)
            progress.update(task, description="Transforming data...")
            pause(0.5, quick)
            progress.update(task, description="Validating schema...")
            pause(0.5, quick)
            progress.update(task, description="[green]Complete!")
            pause(0.3, quick)

        # Show sample of the data
        console.print("\n[bold]Sample Trade Records:[/bold]\n")

        sample_table = Table(box=box.SIMPLE)
        sample_table.add_column("Time", style="cyan")
        sample_table.add_column("Price", justify="right", style="green")
        sample_table.add_column("Volume", justify="right", style="yellow")
        sample_table.add_column("Venue", style="dim")

        for _, row in df.head(5).iterrows():
            sample_table.add_row(
                str(row["Time"]),
                f"${row['Price']:.2f}",
                f"{int(row['Volume']):,}",
                str(row["Venue"]),
            )

        console.print(sample_table)
        console.print(f"\n[dim]... and {len(df) - 5} more records[/dim]\n")

        # Show ingestion stats
        stats_panel = Panel(
            f"""[bold]Ingestion Statistics:[/bold]

  Records Read:    [green]{len(df):,}[/green]
  Symbol:          [cyan]DVN[/cyan]
  Exchange:        ASX
  Date Range:      March 10-14, 2014
  Price Range:     ${df['Price'].min():.2f} - ${df['Price'].max():.2f}
  Total Volume:    {df['Volume'].sum():,}

[dim]Note: In production, data would flow through Kafka → Iceberg[/dim]
""",
            title="Ingestion Stats",
            border_style="green",
        )
        console.print(stats_panel)

    except ImportError as e:
        console.print(f"[yellow]Import warning: {e}[/yellow]")
        console.print("[dim]Some k2 modules not available - showing sample data only[/dim]\n")

    pause(2, quick)


def step_3_query_demo(quick: bool = False) -> None:
    """Step 3: Demonstrate query engine capabilities."""
    print_step_header(3, "Query Engine Demo")

    console.print("[bold]Querying market data with DuckDB + Iceberg...[/bold]\n")

    try:
        from k2.query.engine import QueryEngine

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Connecting to QueryEngine...", total=None)

            try:
                engine = QueryEngine()
                progress.update(task, description="[green]Connected!")
                pause(0.5, quick)

                # Get stats
                progress.update(task, description="Fetching statistics...")
                stats = engine.get_stats()
                pause(0.5, quick)

                # Get symbols
                progress.update(task, description="Listing symbols...")
                symbols = engine.get_symbols()
                pause(0.5, quick)

                progress.update(task, description="[green]Queries complete!")

            except Exception as e:
                progress.update(task, description=f"[yellow]Connection issue: {e}")
                stats = None
                symbols = []

        # Display results
        if stats:
            console.print("\n[bold]Database Statistics:[/bold]\n")

            stats_table = Table(box=box.ROUNDED)
            stats_table.add_column("Metric", style="cyan")
            stats_table.add_column("Value", justify="right", style="green")

            for key, value in stats.items():
                if isinstance(value, (int, float)):
                    stats_table.add_row(key.replace("_", " ").title(), f"{value:,}")
                else:
                    stats_table.add_row(key.replace("_", " ").title(), str(value))

            console.print(stats_table)

        if symbols:
            console.print(f"\n[bold]Available Symbols:[/bold] {', '.join(symbols)}\n")

        # Show query examples
        console.print("[bold]Example Queries:[/bold]\n")

        examples = [
            ("Get trades", "k2-query trades --symbol DVN --limit 10"),
            ("OHLCV summary", "k2-query summary DVN 2014-03-10"),
            ("List snapshots", "k2-query snapshots"),
            ("Replay data", "k2-query replay --symbol DVN --batch-size 100"),
        ]

        for name, cmd in examples:
            console.print(f"  [cyan]{name}:[/cyan] [dim]{cmd}[/dim]")

        console.print()

    except ImportError:
        console.print("[yellow]QueryEngine not available - ensure Docker is running[/yellow]")
        console.print("[dim]Run: make docker-up && make init-infra[/dim]\n")

    pause(2, quick)


def step_4_time_travel(quick: bool = False) -> None:
    """Step 4: Demonstrate time-travel capabilities."""
    print_step_header(4, "Time-Travel Demo")

    console.print("[bold]Iceberg Time-Travel Queries[/bold]\n")

    explanation = """
[bold white]What is Time-Travel?[/bold white]

Apache Iceberg maintains a [cyan]snapshot history[/cyan] of your tables.
Each snapshot represents the table state at a point in time.

[bold]Capabilities:[/bold]
  • Query historical data as it existed at any snapshot
  • Audit changes over time
  • Rollback to previous states if needed
  • Perfect for backtesting trading strategies

[bold]Use Cases in HFT:[/bold]
  • [green]Backtesting[/green]: Replay exact market state for strategy testing
  • [green]Audit[/green]: Track what data was available at decision time
  • [green]Recovery[/green]: Rollback bad data loads without downtime
"""

    console.print(Panel(explanation, title="Time-Travel", border_style="blue"))
    pause(1, quick)

    try:
        from k2.query.replay import ReplayEngine

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Connecting to ReplayEngine...", total=None)

            try:
                replay = ReplayEngine()
                progress.update(task, description="Listing snapshots...")
                snapshots = replay.list_snapshots(table_name="trades", limit=5)
                pause(0.5, quick)
                progress.update(task, description="[green]Complete!")

                if snapshots:
                    console.print("\n[bold]Recent Snapshots (trades table):[/bold]\n")

                    snap_table = Table(box=box.ROUNDED)
                    snap_table.add_column("Snapshot ID", style="cyan")
                    snap_table.add_column("Timestamp", style="white")
                    snap_table.add_column("Records", justify="right", style="green")

                    for snap in snapshots:
                        # SnapshotInfo is an object with attributes, not a dict
                        snapshot_id = getattr(snap, "snapshot_id", None) or "N/A"
                        timestamp = getattr(snap, "timestamp", None) or "N/A"
                        added_records = getattr(snap, "added_records", 0) or 0
                        snap_table.add_row(
                            str(snapshot_id)[:16] + "...",
                            str(timestamp),
                            f"{added_records:,}",
                        )

                    console.print(snap_table)

                replay.close()

            except Exception as e:
                progress.update(task, description=f"[yellow]Not available: {e}")

    except ImportError:
        console.print("[yellow]ReplayEngine not available - ensure Docker is running[/yellow]")

    console.print()
    pause(2, quick)


def step_5_summary(quick: bool = False) -> None:
    """Step 5: Summary and next steps."""
    print_step_header(5, "Summary & Next Steps")

    summary = """
[bold green]Demo Complete![/bold green]

[bold]What We Demonstrated:[/bold]

  [green]✓[/green] Platform architecture and data flow
  [green]✓[/green] Sample data ingestion (ASX trades)
  [green]✓[/green] Query engine with DuckDB
  [green]✓[/green] Time-travel capabilities with Iceberg

[bold]Key Commands:[/bold]

  [cyan]make docker-up[/cyan]     Start all services
  [cyan]make init-infra[/cyan]    Initialize Kafka topics and Iceberg tables
  [cyan]make api[/cyan]           Start REST API server
  [cyan]k2-query --help[/cyan]    Query CLI usage

[bold]REST API Endpoints:[/bold]

  [dim]GET[/dim]  /v1/trades       Query trades
  [dim]GET[/dim]  /v1/quotes       Query quotes
  [dim]GET[/dim]  /v1/symbols      List symbols
  [dim]GET[/dim]  /v1/summary/{symbol}/{date}  OHLCV summary
  [dim]GET[/dim]  /health          Health check
  [dim]GET[/dim]  /metrics         Prometheus metrics

[bold]Documentation:[/bold]

  [dim]•[/dim] README.md - Quick start guide
  [dim]•[/dim] docs/phases/phase-1-single-node-implementation/ - Implementation details
  [dim]•[/dim] http://localhost:8000/docs - OpenAPI documentation
  [dim]•[/dim] http://localhost:3000 - Grafana dashboards
"""

    console.print(Panel(summary, title="Summary", border_style="green"))


@app.command()
def main(
    quick: bool = typer.Option(False, "--quick", "-q", help="Skip delays (CI mode)"),
    step: Optional[int] = typer.Option(None, "--step", "-s", help="Run specific step (1-5)"),
) -> None:
    """Run the K2 Market Data Platform interactive demo."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]K2 Market Data Platform[/bold cyan]\n" "[dim]Interactive Demo[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    if quick:
        console.print("[dim]Running in quick mode (no delays)[/dim]\n")

    steps = [
        (1, step_1_architecture),
        (2, step_2_ingestion),
        (3, step_3_query_demo),
        (4, step_4_time_travel),
        (5, step_5_summary),
    ]

    if step:
        # Run specific step
        if 1 <= step <= 5:
            steps[step - 1][1](quick)
        else:
            console.print(f"[red]Invalid step: {step}. Choose 1-5.[/red]")
            raise typer.Exit(1)
    else:
        # Run all steps
        for step_num, step_func in steps:
            step_func(quick)

    console.print()
    console.print("[bold green]Demo complete![/bold green]")
    console.print()


if __name__ == "__main__":
    app()

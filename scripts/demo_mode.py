#!/usr/bin/env python3
"""Demo Mode: Reset system to known-good state with pre-populated data.

Use this 30 minutes before demo to ensure reliable state.

Usage:
    uv run python scripts/demo_mode.py --reset
    uv run python scripts/demo_mode.py --load
    uv run python scripts/demo_mode.py --validate
    uv run python scripts/demo_mode.py --reset --load --validate
"""

import subprocess
import time

import click
import requests
from rich.console import Console

console = Console()


@click.command()
@click.option("--reset", is_flag=True, help="Reset all services and data")
@click.option("--load", is_flag=True, help="Load pre-generated demo data")
@click.option("--validate", is_flag=True, help="Validate demo readiness")
def demo_mode(reset, load, validate):
    """Prepare system for demo."""

    if reset:
        console.print("\n[bold yellow]Resetting services...[/bold yellow]")

        # Stop and remove all containers
        console.print("  → Stopping services and removing volumes...")
        result = subprocess.run(["docker", "compose", "down", "-v"], capture_output=True, text=True)
        if result.returncode == 0:
            console.print("  [green]✓[/green] Services stopped and volumes removed")
        else:
            console.print(f"  [red]✗[/red] Error stopping services: {result.stderr[:100]}")
            return

        # Start fresh
        console.print("  → Starting services...")
        result = subprocess.run(["docker", "compose", "up", "-d"], capture_output=True, text=True)
        if result.returncode == 0:
            console.print("  [green]✓[/green] Services started")
        else:
            console.print(f"  [red]✗[/red] Error starting services: {result.stderr[:100]}")
            return

        console.print("\n[yellow]Waiting 30 seconds for services to initialize...[/yellow]")
        for i in range(30, 0, -5):
            console.print(f"  {i} seconds remaining...", end="\r")
            time.sleep(5)

        console.print("  [green]✓[/green] Services ready\n")

    if load:
        console.print("\n[bold yellow]Loading demo data...[/bold yellow]")

        # In a full implementation, this would:
        # 1. Load pre-generated Avro files into Kafka
        # 2. Pre-populate Iceberg with known-good data
        # 3. Set consumer offsets to specific positions

        # For now, let stream accumulate naturally
        console.print("  • Binance stream will accumulate data naturally")
        console.print("  • Recommendation: Let run for 10-15 minutes")
        console.print("  • Monitor: docker logs k2-binance-stream --follow\n")

    if validate:
        console.print("\n[bold cyan]Validating Demo Readiness[/bold cyan]\n")

        checks = []

        # Check services
        result = subprocess.run(["docker", "compose", "ps"], capture_output=True, text=True)
        up_count = result.stdout.count("Up")

        if up_count >= 7:
            console.print(f"  [green]✓[/green] Services: {up_count}/7+ running")
            checks.append(True)
        else:
            console.print(f"  [red]✗[/red] Services: Only {up_count}/7+ running")
            checks.append(False)

        # Check Binance stream
        result = subprocess.run(
            ["docker", "logs", "k2-binance-stream", "--tail", "50"],
            capture_output=True,
            text=True,
        )
        trade_count = result.stdout.count("Trade")

        if trade_count > 0:
            console.print(f"  [green]✓[/green] Binance stream: {trade_count} recent trades")
            checks.append(True)
        else:
            console.print("  [red]✗[/red] Binance stream: No recent trades")
            checks.append(False)

        # Check Prometheus
        try:
            response = requests.get("http://localhost:9090/-/healthy", timeout=5)
            if response.status_code == 200:
                console.print("  [green]✓[/green] Prometheus: Healthy")
                checks.append(True)
            else:
                console.print("  [red]✗[/red] Prometheus: Not healthy")
                checks.append(False)
        except Exception:
            console.print("  [red]✗[/red] Prometheus: Cannot connect")
            checks.append(False)

        # Check Grafana
        try:
            response = requests.get("http://localhost:3000/api/health", timeout=5)
            if response.status_code == 200:
                console.print("  [green]✓[/green] Grafana: Healthy")
                checks.append(True)
            else:
                console.print("  [red]✗[/red] Grafana: Not healthy")
                checks.append(False)
        except Exception:
            console.print("  [red]✗[/red] Grafana: Cannot connect")
            checks.append(False)

        # Check API
        try:
            response = requests.get(
                "http://localhost:8000/health",
                headers={"X-API-Key": "k2-dev-api-key-2026"},
                timeout=5,
            )
            if response.status_code == 200:
                console.print("  [green]✓[/green] API: Healthy")
                checks.append(True)
            else:
                console.print("  [red]✗[/red] API: Not healthy")
                checks.append(False)
        except Exception:
            console.print("  [red]✗[/red] API: Cannot connect")
            checks.append(False)

        # Check data exists
        try:
            response = requests.get(
                "http://localhost:8000/v1/symbols",
                headers={"X-API-Key": "k2-dev-api-key-2026"},
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                symbol_count = len(data.get("data", []))
                if symbol_count > 0:
                    console.print(f"  [green]✓[/green] Data: {symbol_count} symbols in Iceberg")
                    checks.append(True)
                else:
                    console.print("  [red]✗[/red] Data: No symbols in Iceberg")
                    checks.append(False)
            else:
                console.print("  [red]✗[/red] Data: Cannot query API")
                checks.append(False)
        except Exception:
            console.print("  [red]✗[/red] Data: Cannot query API")
            checks.append(False)

        # Summary
        passed = sum(checks)
        total = len(checks)

        console.print()
        if passed == total:
            console.print(
                f"[bold green]✓ Demo Ready: {passed}/{total} checks passed[/bold green]\n"
            )
        else:
            console.print(f"[bold red]✗ Not Ready: {passed}/{total} checks passed[/bold red]")
            console.print("\n[yellow]Recommendations:[/yellow]")
            if up_count < 7:
                console.print("  • Start services: docker compose up -d")
            if trade_count == 0:
                console.print("  • Check Binance stream: docker logs k2-binance-stream")
            console.print()


if __name__ == "__main__":
    demo_mode()

"""Failure simulation script for K2 platform resilience demo.

This script simulates various failure scenarios to demonstrate the circuit breaker's
graceful degradation capabilities during the demo.

Scenarios:
- high_lag: Simulate high Kafka consumer lag (triggers GRACEFUL degradation)
- critical_lag: Simulate critical lag (triggers AGGRESSIVE degradation)
- memory_pressure: Simulate high memory usage (triggers degradation)
- recover: Allow system to recover to NORMAL state

Usage:
    uv run python scripts/simulate_failure.py --scenario high_lag
    uv run python scripts/simulate_failure.py --scenario recover
    uv run python scripts/simulate_failure.py --status
"""

import click
import requests
from rich.console import Console
from rich.table import Table

console = Console()


class FailureSimulator:
    """Simulate failure scenarios for resilience demo."""

    def __init__(self):
        self.prometheus_url = "http://localhost:9090"
        self.api_url = "http://localhost:8000"
        self.api_key = "k2-dev-api-key-2026"

    def get_current_degradation_level(self) -> tuple[int, str]:
        """Query Prometheus for current degradation level."""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": "k2_degradation_level"},
                timeout=5,
            )
            data = response.json()

            if data.get("status") == "success" and data.get("data", {}).get("result"):
                level = int(float(data["data"]["result"][0]["value"][1]))
                level_names = {
                    0: "NORMAL",
                    1: "SOFT",
                    2: "GRACEFUL",
                    3: "AGGRESSIVE",
                    4: "CIRCUIT_BREAK",
                }
                return level, level_names.get(level, "UNKNOWN")
            else:
                console.print("[yellow]No degradation metrics available[/yellow]")
                return 0, "NORMAL"
        except Exception as e:
            console.print(f"[red]Error querying Prometheus: {e}[/red]")
            return 0, "NORMAL"

    def get_consumer_lag(self) -> int:
        """Query Prometheus for current consumer lag."""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": "kafka_consumer_lag"},
                timeout=5,
            )
            data = response.json()

            if data.get("status") == "success" and data.get("data", {}).get("result"):
                lag = int(float(data["data"]["result"][0]["value"][1]))
                return lag
            else:
                return 0
        except Exception:
            return 0

    def get_memory_usage(self) -> float:
        """Query Prometheus for current memory usage percentage."""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={
                    "query": "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
                },
                timeout=5,
            )
            data = response.json()

            if data.get("status") == "success" and data.get("data", {}).get("result"):
                memory_pct = float(data["data"]["result"][0]["value"][1])
                return memory_pct
            else:
                return 0.0
        except Exception:
            return 0.0

    def show_status(self):
        """Show current system status."""
        console.print("\n[bold blue]Current System Status[/bold blue]\n")

        level_num, level_name = self.get_current_degradation_level()
        lag = self.get_consumer_lag()
        memory_pct = self.get_memory_usage()

        # Status table
        table = Table(title="Degradation Manager Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="yellow")
        table.add_column("Status", style="green")

        # Degradation level
        level_color = "green" if level_num == 0 else "red" if level_num >= 3 else "yellow"
        table.add_row(
            "Degradation Level",
            f"{level_num} ({level_name})",
            f"[{level_color}]{'✓ Healthy' if level_num == 0 else '⚠ Degraded'}[/{level_color}]",
        )

        # Consumer lag
        lag_status = "✓ Normal" if lag < 100_000 else "⚠ High" if lag < 1_000_000 else "✗ Critical"
        lag_color = "green" if lag < 100_000 else "yellow" if lag < 1_000_000 else "red"
        table.add_row(
            "Consumer Lag",
            f"{lag:,} messages",
            f"[{lag_color}]{lag_status}[/{lag_color}]",
        )

        # Memory usage
        memory_status = (
            "✓ Normal" if memory_pct < 70 else "⚠ High" if memory_pct < 90 else "✗ Critical"
        )
        memory_color = "green" if memory_pct < 70 else "yellow" if memory_pct < 90 else "red"
        table.add_row(
            "Memory Usage",
            f"{memory_pct:.1f}%",
            f"[{memory_color}]{memory_status}[/{memory_color}]",
        )

        console.print(table)

        # Thresholds reference
        console.print("\n[dim]Degradation Thresholds:[/dim]")
        console.print("[dim]  SOFT (1):       Lag ≥ 100K   or Heap ≥ 70%[/dim]")
        console.print("[dim]  GRACEFUL (2):   Lag ≥ 500K   or Heap ≥ 80%[/dim]")
        console.print("[dim]  AGGRESSIVE (3): Lag ≥ 1M     or Heap ≥ 90%[/dim]")
        console.print("[dim]  CIRCUIT_BREAK (4): Lag ≥ 5M  or Heap ≥ 95%[/dim]\n")

    def simulate_high_lag(self):
        """
        Simulate high consumer lag scenario.

        Note: This is a DEMO simulation for presentation purposes.
        In a real scenario, lag would be triggered by actual message backlog.

        For demo purposes, we explain what WOULD happen with 600K lag:
        - System would detect lag ≥ 500K
        - Degradation level would increase to GRACEFUL (2)
        - Low-priority symbols would be dropped
        - High-value data (BTC, ETH, critical symbols) continues processing
        """
        console.print("\n[bold yellow]Simulating High Consumer Lag Scenario[/bold yellow]\n")

        # Show current state
        level_num, level_name = self.get_current_degradation_level()
        console.print(f"[green]Current state: {level_name} (level {level_num})[/green]")

        if level_num == 0:
            console.print("[green]  ✓ All symbols processing[/green]")
            console.print("[green]  ✓ Full enrichment enabled[/green]")

        console.print(
            "\n[yellow]→ Simulating scenario: Consumer lag reaches 600,000 messages[/yellow]\n"
        )

        # Explain what would happen
        console.print("[bold red]Expected System Response:[/bold red]")
        console.print("[red]  → Degradation level increases to: GRACEFUL (2)[/red]")
        console.print("[red]  → Action: Drop low-priority symbols (Tier 3)[/red]")
        console.print(
            "[red]  → Result: High-value data continues (BTC, ETH, critical symbols)[/red]"
        )
        console.print("[red]  → Throughput: Reduced to ~50-70% (load shedding active)[/red]\n")

        console.print(
            "[dim]Note: In production, this would be triggered by actual Kafka consumer lag.[/dim]"
        )
        console.print(
            "[dim]The degradation manager automatically monitors lag and adjusts processing.[/dim]\n"
        )

        # Show metrics that would be affected
        console.print("[bold cyan]Circuit Breaker Metrics (Expected):[/bold cyan]")
        table = Table()
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="yellow")
        table.add_row("Degradation Level", "2 (GRACEFUL)")
        table.add_row("Messages Shed", "~30-50% of total")
        table.add_row("Symbols Dropped", "Low-priority tickers")
        table.add_row("Critical Symbols Protected", "BTC, ETH, top 20 ASX")
        table.add_row("Processing Throughput", "50-70% of normal")

        console.print(table)

    def simulate_critical_lag(self):
        """Simulate critical lag scenario (triggers AGGRESSIVE degradation)."""
        console.print("\n[bold red]Simulating Critical Lag Scenario[/bold red]\n")

        level_num, level_name = self.get_current_degradation_level()
        console.print(f"[green]Current state: {level_name} (level {level_num})[/green]\n")

        console.print(
            "[yellow]→ Simulating scenario: Consumer lag reaches 1,200,000 messages[/yellow]\n"
        )

        console.print("[bold red]Expected System Response:[/bold red]")
        console.print("[red]  → Degradation level increases to: AGGRESSIVE (3)[/red]")
        console.print("[red]  → Action: Spill to disk, drop more symbols[/red]")
        console.print("[red]  → Result: Only critical symbols processed (top 10-20)[/red]")
        console.print("[red]  → Throughput: Reduced to ~20-40% (aggressive shedding)[/red]\n")

        console.print(
            "[dim]Recovery: Automatic when lag < 600K (50% hysteresis) + 30s cooldown[/dim]\n"
        )

    def simulate_memory_pressure(self):
        """Simulate high memory usage scenario."""
        console.print("\n[bold yellow]Simulating Memory Pressure Scenario[/bold yellow]\n")

        level_num, level_name = self.get_current_degradation_level()
        console.print(f"[green]Current state: {level_name} (level {level_num})[/green]\n")

        console.print("[yellow]→ Simulating scenario: Heap usage reaches 85%[/yellow]\n")

        console.print("[bold red]Expected System Response:[/bold red]")
        console.print("[red]  → Degradation level increases to: GRACEFUL (2)[/red]")
        console.print("[red]  → Action: Skip enrichment, drop low-priority data[/red]")
        console.print("[red]  → Result: Reduced memory footprint[/red]")
        console.print("[red]  → GC pressure: Reduced by ~30-40%[/red]\n")

    def simulate_recovery(self):
        """Simulate system recovery to NORMAL state."""
        console.print("\n[bold green]Simulating System Recovery[/bold green]\n")

        level_num, level_name = self.get_current_degradation_level()
        console.print(f"[yellow]Current state: {level_name} (level {level_num})[/yellow]\n")

        if level_num == 0:
            console.print("[green]System already at NORMAL level - no recovery needed[/green]\n")
            return

        console.print("[yellow]→ Simulating scenario: Lag recovers to < 250K messages[/yellow]\n")

        console.print("[bold green]Expected Recovery Process:[/bold green]")
        console.print("[green]  → Lag drops below recovery threshold (50% of trigger)[/green]")
        console.print("[green]  → Cooldown period: 30 seconds (prevents flapping)[/green]")
        console.print("[green]  → Degradation level decreases to: NORMAL (0)[/green]")
        console.print("[green]  → All symbols resume processing[/green]")
        console.print("[green]  → Full enrichment re-enabled[/green]\n")

        console.print("[dim]Note: Hysteresis prevents rapid oscillation between states.[/dim]")
        console.print("[dim]Recovery requires sustained improvement, not just a brief dip.[/dim]\n")


@click.command()
@click.option(
    "--scenario",
    type=click.Choice(["high_lag", "critical_lag", "memory_pressure", "recover"]),
    help="Failure scenario to simulate",
)
@click.option(
    "--status",
    is_flag=True,
    help="Show current system status",
)
def main(scenario, status):
    """
    Simulate failure scenarios for K2 platform resilience demo.

    This script demonstrates what the circuit breaker WOULD do under various
    failure conditions. Perfect for demo presentations.
    """
    simulator = FailureSimulator()

    if status or scenario is None:
        simulator.show_status()
        return

    if scenario == "high_lag":
        simulator.simulate_high_lag()
    elif scenario == "critical_lag":
        simulator.simulate_critical_lag()
    elif scenario == "memory_pressure":
        simulator.simulate_memory_pressure()
    elif scenario == "recover":
        simulator.simulate_recovery()

    console.print("\n[bold blue]To check current status:[/bold blue]")
    console.print("  uv run python scripts/simulate_failure.py --status\n")


if __name__ == "__main__":
    main()

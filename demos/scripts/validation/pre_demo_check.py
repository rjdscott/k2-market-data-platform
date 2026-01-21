#!/usr/bin/env python3
"""Pre-demo validation script for K2 platform.

Run this script 30 minutes before demo to catch issues early.

Usage:
    uv run python scripts/pre_demo_check.py
    uv run python scripts/pre_demo_check.py --full  # Includes backup material checks
"""

import subprocess
from pathlib import Path

import click
import requests
from rich.console import Console
from rich.panel import Panel

console = Console()


class PreDemoValidator:
    """Validate system readiness for demo."""

    def __init__(self):
        self.prometheus_url = "http://localhost:9090"
        self.grafana_url = "http://localhost:3000"
        self.api_url = "http://localhost:8000"
        self.api_key = "k2-dev-api-key-2026"
        self.checks_passed = []
        self.checks_failed = []

    def check_services(self) -> bool:
        """Check all Docker services are running."""
        console.print("\n[bold cyan]1. Infrastructure Health[/bold cyan]")

        try:
            result = subprocess.run(
                ["docker", "compose", "ps"],
                capture_output=True,
                text=True,
                check=True,
            )
            up_count = result.stdout.count("Up")

            if up_count >= 7:
                console.print(f"  [green]✓[/green] Docker services: {up_count}/7+ running")
                self.checks_passed.append("Docker services")
                return True
            else:
                console.print(f"  [red]✗[/red] Docker services: Only {up_count}/7+ running")
                console.print("  [yellow]→ Run: docker compose up -d[/yellow]")
                self.checks_failed.append("Docker services")
                return False
        except subprocess.CalledProcessError as e:
            console.print(f"  [red]✗[/red] Docker check failed: {e}")
            self.checks_failed.append("Docker services")
            return False

    def check_binance_stream(self) -> bool:
        """Check Binance stream is ingesting."""
        console.print("\n[bold cyan]2. Live Data Ingestion[/bold cyan]")

        try:
            result = subprocess.run(
                ["docker", "logs", "k2-binance-stream", "--tail", "50"],
                capture_output=True,
                text=True,
            )

            # Strip ANSI color codes from logs
            import re

            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
            clean_output = ansi_escape.sub("", result.stdout)

            # Look for actual log patterns from Binance stream
            trade_count = clean_output.count("trades_streamed")
            streamed_count = clean_output.count("Streamed")

            # Extract latest trade count if available (find the highest count = most recent)
            matches = re.findall(r"trades_streamed=(\d+)", clean_output)
            latest_count = max([int(m) for m in matches]) if matches else 0

            if trade_count > 0 or streamed_count > 0:
                if latest_count > 0:
                    console.print(
                        f"  [green]✓[/green] Binance stream: {latest_count:,} total trades streamed"
                    )
                else:
                    console.print(
                        f"  [green]✓[/green] Binance stream: {max(trade_count, streamed_count)} recent log entries"
                    )
                self.checks_passed.append("Binance stream")
                return True
            else:
                console.print(
                    "  [yellow]⚠[/yellow] Binance stream: No recent trades (may be OK if just started)"
                )
                console.print("  [yellow]→ Check: docker logs k2-binance-stream --follow[/yellow]")
                self.checks_failed.append("Binance stream (warning)")
                return False
        except Exception as e:
            console.print(f"  [red]✗[/red] Binance stream check failed: {e}")
            self.checks_failed.append("Binance stream")
            return False

    def check_data_availability(self) -> bool:
        """Check data exists in Iceberg."""
        console.print("\n[bold cyan]3. Data Availability[/bold cyan]")

        try:
            response = requests.get(
                f"{self.api_url}/v1/symbols",
                headers={"X-API-Key": self.api_key},
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                symbol_count = len(data.get("data", []))

                if symbol_count > 0:
                    console.print(
                        f"  [green]✓[/green] Iceberg data: {symbol_count} symbols available"
                    )

                    # Check row count
                    trades_response = requests.get(
                        f"{self.api_url}/v1/trades?symbol=BTCUSDT&limit=1",
                        headers={"X-API-Key": self.api_key},
                        timeout=10,
                    )

                    if trades_response.status_code == 200:
                        trades_data = trades_response.json()
                        if trades_data.get("data"):
                            console.print("  [green]✓[/green] Trade queries: Returning data")
                            self.checks_passed.append("Data availability")
                            return True
                        else:
                            console.print("  [yellow]⚠[/yellow] Trade queries: Empty results")
                            self.checks_failed.append("Trade queries (warning)")
                            return False
                else:
                    console.print("  [red]✗[/red] Iceberg data: No symbols found")
                    console.print("  [yellow]→ Wait for data accumulation (10-15 minutes)[/yellow]")
                    self.checks_failed.append("Iceberg data")
                    return False
            else:
                console.print(f"  [red]✗[/red] API request failed: HTTP {response.status_code}")
                self.checks_failed.append("API availability")
                return False
        except Exception as e:
            console.print(f"  [red]✗[/red] Data availability check failed: {e}")
            self.checks_failed.append("Data availability")
            return False

    def check_monitoring(self) -> bool:
        """Check Prometheus and Grafana."""
        console.print("\n[bold cyan]4. Monitoring Stack[/bold cyan]")

        checks_ok = True

        # Prometheus
        try:
            response = requests.get(f"{self.prometheus_url}/-/healthy", timeout=5)
            if response.status_code == 200:
                console.print("  [green]✓[/green] Prometheus: Healthy")
                self.checks_passed.append("Prometheus")
            else:
                console.print("  [red]✗[/red] Prometheus: Not healthy")
                checks_ok = False
                self.checks_failed.append("Prometheus")
        except Exception as e:
            console.print(f"  [red]✗[/red] Prometheus: Cannot connect - {e}")
            checks_ok = False
            self.checks_failed.append("Prometheus")

        # Grafana
        try:
            response = requests.get(f"{self.grafana_url}/api/health", timeout=5)
            if response.status_code == 200:
                console.print("  [green]✓[/green] Grafana: Healthy")
                self.checks_passed.append("Grafana")
            else:
                console.print("  [red]✗[/red] Grafana: Not healthy")
                checks_ok = False
                self.checks_failed.append("Grafana")
        except Exception as e:
            console.print(f"  [red]✗[/red] Grafana: Cannot connect - {e}")
            checks_ok = False
            self.checks_failed.append("Grafana")

        return checks_ok

    def check_api_endpoints(self) -> bool:
        """Check API endpoints responding."""
        console.print("\n[bold cyan]5. API Endpoints[/bold cyan]")

        checks_ok = True

        # Health endpoint
        try:
            response = requests.get(
                f"{self.api_url}/health",
                headers={"X-API-Key": self.api_key},
                timeout=5,
            )
            if response.status_code == 200:
                console.print("  [green]✓[/green] API /health: Responding")
                self.checks_passed.append("API health")
            else:
                console.print(f"  [red]✗[/red] API /health: HTTP {response.status_code}")
                checks_ok = False
                self.checks_failed.append("API health")
        except Exception as e:
            console.print(f"  [red]✗[/red] API /health: Cannot connect - {e}")
            checks_ok = False
            self.checks_failed.append("API health")

        # Symbols endpoint
        try:
            response = requests.get(
                f"{self.api_url}/v1/symbols",
                headers={"X-API-Key": self.api_key},
                timeout=5,
            )
            if response.status_code == 200:
                console.print("  [green]✓[/green] API /v1/symbols: Responding")
                self.checks_passed.append("API symbols")
            else:
                console.print(f"  [red]✗[/red] API /v1/symbols: HTTP {response.status_code}")
                checks_ok = False
                self.checks_failed.append("API symbols")
        except Exception as e:
            console.print(f"  [red]✗[/red] API /v1/symbols: Cannot connect - {e}")
            checks_ok = False
            self.checks_failed.append("API symbols")

        return checks_ok

    def check_backup_materials(self) -> bool:
        """Check backup materials are ready."""
        console.print("\n[bold cyan]6. Backup Materials[/bold cyan]")

        checks_ok = True
        base_path = Path(__file__).parent.parent

        # Contingency plan
        contingency_path = (
            base_path / "docs/phases/phase-4-demo-readiness/reference/contingency-plan.md"
        )
        if contingency_path.exists():
            console.print("  [green]✓[/green] Contingency plan: Available")
            self.checks_passed.append("Contingency plan")
        else:
            console.print("  [red]✗[/red] Contingency plan: Not found")
            checks_ok = False
            self.checks_failed.append("Contingency plan")

        # Quick reference
        quick_ref_path = (
            base_path / "docs/phases/phase-4-demo-readiness/reference/demo-quick-reference.md"
        )
        if quick_ref_path.exists():
            console.print("  [green]✓[/green] Quick reference: Available")
            self.checks_passed.append("Quick reference")
        else:
            console.print("  [red]✗[/red] Quick reference: Not found")
            checks_ok = False
            self.checks_failed.append("Quick reference")

        # Screenshots directory
        screenshots_path = base_path / "docs/phases/phase-4-demo-readiness/reference/screenshots"
        if screenshots_path.exists():
            console.print("  [green]✓[/green] Screenshots directory: Ready")
            self.checks_passed.append("Screenshots directory")
        else:
            console.print("  [yellow]⚠[/yellow] Screenshots directory: Not found")
            checks_ok = False
            self.checks_failed.append("Screenshots directory")

        # Pre-executed notebook (optional)
        notebook_path = base_path / "notebooks/binance-demo-with-outputs.ipynb"
        if notebook_path.exists():
            console.print("  [green]✓[/green] Pre-executed notebook: Available")
            self.checks_passed.append("Pre-executed notebook")
        else:
            console.print(
                "  [yellow]⚠[/yellow] Pre-executed notebook: Not created yet (create before demo)"
            )

        return checks_ok

    def check_demo_scripts(self) -> bool:
        """Check demo scripts are available."""
        console.print("\n[bold cyan]7. Demo Scripts[/bold cyan]")

        checks_ok = True
        base_path = Path(__file__).parent

        scripts = [
            ("performance_benchmark.py", "Performance benchmark"),
            ("simulate_failure.py", "Failure simulator"),
            ("demo_mode.py", "Demo mode"),
        ]

        for script_name, description in scripts:
            script_path = base_path / script_name
            if script_path.exists():
                console.print(f"  [green]✓[/green] {description}: Available")
                self.checks_passed.append(description)
            else:
                console.print(f"  [red]✗[/red] {description}: Not found")
                checks_ok = False
                self.checks_failed.append(description)

        return checks_ok

    def display_summary(self):
        """Display validation summary."""
        console.print("\n" + "=" * 60)

        passed = len(self.checks_passed)
        failed = len(self.checks_failed)
        total = passed + failed

        if failed == 0:
            panel = Panel(
                f"[bold green]✓ ALL CHECKS PASSED ({passed}/{total})[/bold green]\n\n"
                "System is ready for demo!\n\n"
                "[dim]Run dress rehearsal when ready:[/dim]\n"
                "[dim]  1. Execute full notebook with timing[/dim]\n"
                "[dim]  2. Practice talking points out loud[/dim]\n"
                "[dim]  3. Test failure recovery[/dim]",
                title="Demo Readiness",
                border_style="green",
            )
            console.print(panel)
        else:
            panel = Panel(
                f"[bold yellow]⚠ {failed} ISSUE(S) FOUND ({passed}/{total} checks passed)[/bold yellow]\n\n"
                f"[red]Failed checks:[/red]\n"
                + "\n".join(f"  • {check}" for check in self.checks_failed)
                + "\n\n[dim]Fix issues before proceeding with demo.[/dim]",
                title="Demo Readiness",
                border_style="yellow",
            )
            console.print(panel)

        console.print()


@click.command()
@click.option("--full", is_flag=True, help="Include backup material checks")
def main(full):
    """Pre-demo validation for K2 platform.

    Run this 30 minutes before demo to catch issues early.
    """
    console.print("\n[bold blue]K2 Platform - Pre-Demo Validation[/bold blue]")
    console.print("[dim]Checking system readiness for demo...[/dim]\n")

    validator = PreDemoValidator()

    # Run all checks
    validator.check_services()
    validator.check_binance_stream()
    validator.check_data_availability()
    validator.check_monitoring()
    validator.check_api_endpoints()

    if full:
        validator.check_backup_materials()
        validator.check_demo_scripts()

    # Display summary
    validator.display_summary()

    # Exit code
    if validator.checks_failed:
        exit(1)
    else:
        exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Degradation demo script.

Demonstrates graceful degradation under load by:
1. Starting at NORMAL
2. Increasing load to trigger degradation
3. Showing metrics and behavior at each level
4. Recovering back to NORMAL

Usage:
    python scripts/demo_degradation.py
    python scripts/demo_degradation.py --quick  # Fast mode for CI
"""

import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from k2.common.degradation_manager import DegradationManager, DegradationLevel
from k2.common.load_shedder import LoadShedder
from k2.common.logging import get_logger

console = Console()
logger = get_logger(__name__, component="degradation_demo")


class DegradationDemo:
    """Interactive degradation demonstration."""

    def __init__(self, quick_mode: bool = False):
        self.quick_mode = quick_mode
        self.degradation_manager = DegradationManager()
        self.load_shedder = LoadShedder()
        self.simulated_lag = 0
        self.simulated_heap = 50.0

    def run(self):
        """Run the full degradation demo."""
        console.print("\n[bold blue]K2 Platform: Graceful Degradation Demo[/bold blue]\n")

        self._show_intro()
        self._demonstrate_normal()
        self._demonstrate_degradation()
        self._demonstrate_recovery()
        self._show_summary()

    def _show_intro(self):
        """Show introduction panel."""
        intro = """
This demo shows how K2 handles system overload through graceful degradation.

[yellow]Degradation Levels:[/yellow]
  Level 0 - NORMAL:        Full processing, all features enabled
  Level 1 - SOFT:          Skip enrichment, reduced validation
  Level 2 - GRACEFUL:      Drop low-priority symbols (top 100 only)
  Level 3 - AGGRESSIVE:    Critical symbols only (top 20), spill to disk
  Level 4 - CIRCUIT_BREAK: Stop accepting new data, system overwhelmed

[yellow]Triggers:[/yellow]
  • Kafka consumer lag thresholds
  • Memory (heap) usage thresholds
  • Automatic recovery with hysteresis (prevents flapping)

[yellow]Load Shedding Behavior:[/yellow]
  • NORMAL/SOFT:     Process all symbols
  • GRACEFUL:        Drop LOW priority (reference data only)
  • AGGRESSIVE:      Drop NORMAL priority (Tier 3 symbols, keep top 100)
  • CIRCUIT_BREAK:   Drop HIGH priority (only Tier 1 top 20 symbols)
"""
        console.print(Panel(intro, title="Introduction", border_style="blue"))
        self._pause(3)

    def _demonstrate_normal(self):
        """Show NORMAL operation."""
        console.print("\n[green]Phase 1: Normal Operation[/green]\n")

        self.simulated_lag = 10_000
        self.simulated_heap = 50.0

        level = self.degradation_manager.check_and_degrade(
            lag=self.simulated_lag,
            heap_pct=self.simulated_heap,
        )

        self._show_status_table()

        console.print(f"\n[green]✓ System operating at {level.name}[/green]")
        console.print("  • All messages processed")
        console.print("  • Full enrichment enabled")
        console.print("  • All symbols (Tier 1, 2, 3) processed")

        self._pause(3)

    def _demonstrate_degradation(self):
        """Show degradation sequence."""
        console.print("\n[yellow]Phase 2: Increasing Load[/yellow]\n")
        console.print("Simulating increased message backlog...\n")

        # Degradation stages with realistic lag values
        stages = [
            (50_000, 65.0, "Load increasing..."),
            (150_000, 75.0, "Lag exceeding SOFT threshold (100K)..."),
            (600_000, 82.0, "Lag exceeding GRACEFUL threshold (500K)..."),
            (1_500_000, 92.0, "Lag exceeding AGGRESSIVE threshold (1M)..."),
        ]

        for lag, heap, message in stages:
            self._pause(2 if not self.quick_mode else 0.5)

            console.print(f"[yellow]→ {message}[/yellow]")
            self.simulated_lag = lag
            self.simulated_heap = heap

            level = self.degradation_manager.check_and_degrade(
                lag=self.simulated_lag,
                heap_pct=self.simulated_heap,
            )

            self._show_status_table()
            self._show_level_behavior(level)

    def _demonstrate_recovery(self):
        """Show recovery sequence."""
        console.print("\n[blue]Phase 3: Recovery[/blue]\n")
        console.print("Load decreasing, initiating recovery...\n")

        # Recovery stages (with hysteresis - recovery thresholds are 50% lower)
        stages = [
            (400_000, 75.0, "Lag decreasing..."),
            (100_000, 60.0, "Approaching recovery threshold (lag < 500K)..."),
            (20_000, 50.0, "Recovery complete (lag < 50K)!"),
        ]

        for lag, heap, message in stages:
            self._pause(3 if not self.quick_mode else 1)

            console.print(f"[blue]→ {message}[/blue]")
            self.simulated_lag = lag
            self.simulated_heap = heap

            level = self.degradation_manager.check_and_degrade(
                lag=self.simulated_lag,
                heap_pct=self.simulated_heap,
            )

            self._show_status_table()

            if level == DegradationLevel.NORMAL:
                console.print("\n[green]✓ System fully recovered to NORMAL![/green]")
                console.print("  • All features restored")
                console.print("  • Processing all symbols again")
                break
            else:
                console.print(f"  Still in {level.name}, waiting for cooldown period...")

    def _show_status_table(self):
        """Display current status table."""
        table = Table(title="System Status", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="magenta", width=15)
        table.add_column("Threshold", style="yellow", width=25)
        table.add_column("Status", style="white", width=15)

        status = self.degradation_manager.get_status()
        level = DegradationLevel(status['level_value'])

        # Color based on level
        level_color = {
            DegradationLevel.NORMAL: "green",
            DegradationLevel.SOFT: "yellow",
            DegradationLevel.GRACEFUL: "yellow",
            DegradationLevel.AGGRESSIVE: "red",
            DegradationLevel.CIRCUIT_BREAK: "red bold",
        }.get(level, "white")

        table.add_row(
            "Degradation Level",
            f"[{level_color}]{status['level']} ({status['level_value']})[/{level_color}]",
            "-",
            f"[{level_color}]Active[/{level_color}]",
        )

        # Lag status
        lag_status = "OK" if self.simulated_lag < 100_000 else "WARNING" if self.simulated_lag < 1_000_000 else "CRITICAL"
        lag_color = "green" if lag_status == "OK" else "yellow" if lag_status == "WARNING" else "red"

        table.add_row(
            "Consumer Lag",
            f"{self.simulated_lag:,}",
            f"SOFT: {self.degradation_manager.thresholds.lag_soft:,}\n"
            f"GRACEFUL: {self.degradation_manager.thresholds.lag_graceful:,}\n"
            f"AGGRESSIVE: {self.degradation_manager.thresholds.lag_aggressive:,}",
            f"[{lag_color}]{lag_status}[/{lag_color}]",
        )

        # Heap status
        heap_status = "OK" if self.simulated_heap < 70.0 else "WARNING" if self.simulated_heap < 90.0 else "CRITICAL"
        heap_color = "green" if heap_status == "OK" else "yellow" if heap_status == "WARNING" else "red"

        table.add_row(
            "Heap Usage",
            f"{self.simulated_heap:.1f}%",
            f"SOFT: {self.degradation_manager.thresholds.heap_soft}%\n"
            f"GRACEFUL: {self.degradation_manager.thresholds.heap_graceful}%\n"
            f"AGGRESSIVE: {self.degradation_manager.thresholds.heap_aggressive}%",
            f"[{heap_color}]{heap_status}[/{heap_color}]",
        )

        table.add_row(
            "Accepting Data",
            "Yes" if status['is_accepting_data'] else "[red bold]NO[/red bold]",
            "CIRCUIT_BREAK stops data",
            "[green]Active[/green]" if status['is_accepting_data'] else "[red]Stopped[/red]",
        )

        console.print(table)
        console.print()

    def _show_level_behavior(self, level: DegradationLevel):
        """Show what happens at this degradation level."""
        behaviors = {
            DegradationLevel.NORMAL: [
                "• All symbols processed (Tier 1, 2, 3)",
                "• Full enrichment enabled",
                "• Normal batch sizes",
                "• All message priorities accepted",
            ],
            DegradationLevel.SOFT: [
                "• All symbols still processed",
                "• [yellow]Enrichment SKIPPED[/yellow]",
                "• Reduced validation overhead",
                "• All message priorities accepted",
            ],
            DegradationLevel.GRACEFUL: [
                "• [yellow]LOW priority messages DROPPED (reference data)[/yellow]",
                "• Top 100 symbols (Tier 1 + 2) processed",
                "• Tier 3 symbols still processed",
                "• Enrichment skipped",
            ],
            DegradationLevel.AGGRESSIVE: [
                "• [red]NORMAL priority DROPPED (Tier 3 symbols)[/red]",
                "• [red]HIGH priority DROPPED (Tier 2 symbols)[/red]",
                "• Only top 20 symbols (Tier 1) processed",
                "• Spilling to disk buffer",
                "• Emergency mode active",
            ],
            DegradationLevel.CIRCUIT_BREAK: [
                "• [red bold]ALL incoming data REJECTED[/red bold]",
                "• System completely overwhelmed",
                "• Manual intervention required",
                "• Check runbook: docs/operations/runbooks/",
            ],
        }

        console.print(f"[bold]Behavior at {level.name}:[/bold]")
        for behavior in behaviors.get(level, []):
            console.print(f"  {behavior}")

        # Show load shedding statistics
        stats = self.load_shedder.get_stats()
        if stats['total_checked'] > 0:
            console.print(f"\n[dim]Load Shedding: {stats['total_shed']:,} shed / {stats['total_checked']:,} checked ({stats['shed_rate']:.1f}%)[/dim]")

        console.print()

    def _show_summary(self):
        """Show demo summary."""
        summary = """
[bold green]Demo Complete![/bold green]

[yellow]Key Takeaways:[/yellow]

1. [bold]Graceful Degradation[/bold]: System progressively sheds load rather than crashing
   • NORMAL → SOFT → GRACEFUL → AGGRESSIVE → CIRCUIT_BREAK
   • Each level reduces processing scope

2. [bold]Priority-Based Load Shedding[/bold]: Critical symbols always processed
   • Tier 1 (Top 20): BHP, CBA, CSL, NAB, WBC, etc. (always processed)
   • Tier 2 (Top 100): REA, QAN, BXB, etc. (dropped at AGGRESSIVE)
   • Tier 3 (All others): Dropped at AGGRESSIVE
   • Reference data: Dropped at GRACEFUL

3. [bold]Hysteresis Prevents Flapping[/bold]: Recovery slower than degradation
   • Degradation threshold: 500K lag → GRACEFUL
   • Recovery threshold: 250K lag (50% lower)
   • 30-second cooldown between transitions

4. [bold]Observable & Measurable[/bold]: Degradation level exposed via metrics
   • Prometheus metric: k2_degradation_level
   • Prometheus metric: k2_messages_shed_total
   • Grafana dashboard: System Health panel

[yellow]Production Benefits:[/yellow]

• Survives market volatility (earnings announcements, news events)
• Maintains SLAs for critical symbols (top 20)
• Automatic recovery without human intervention
• Full visibility via monitoring dashboards

[cyan]Next Steps:[/cyan]

1. View in Grafana: http://localhost:3000/d/k2-platform
   Look for "Degradation Level" panel in System Health row

2. Run with real load: Use load generator script
   python scripts/demo/load_generator.py --ramp-to 500000

3. Monitor Prometheus metrics:
   curl http://localhost:8000/metrics | grep degradation

[dim]For more information, see:
• docs/operations/runbooks/degradation-recovery.md
• docs/design/data-guarantees/degradation-cascade.md
• docs/phases/phase-2-demo-enhancements/steps/step-02-circuit-breaker.md[/dim]
"""
        console.print(Panel(summary, title="Summary", border_style="green"))

    def _pause(self, seconds: float):
        """Pause with optional quick mode."""
        if self.quick_mode:
            seconds = min(seconds, 0.5)
        time.sleep(seconds)


def main():
    parser = argparse.ArgumentParser(
        description="K2 Platform: Graceful Degradation Demo",
        epilog="Demonstrates the 5-level degradation cascade under simulated load"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode for CI (shorter pauses)",
    )
    args = parser.parse_args()

    try:
        demo = DegradationDemo(quick_mode=args.quick)
        demo.run()
        console.print("\n[green]✓ Demo completed successfully![/green]\n")
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Demo interrupted by user[/yellow]")
    except Exception as err:
        console.print(f"\n\n[red]Demo failed: {err}[/red]")
        logger.exception("Demo failed")
        raise


if __name__ == "__main__":
    main()

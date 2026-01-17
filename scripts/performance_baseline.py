#!/usr/bin/env python3
"""
Performance baseline management for K2 Market Data Platform.

This script establishes and manages performance baselines for the testing framework.
It creates baseline data, compares new results, and detects regressions.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


class PerformanceBaseline:
    """Manage performance baselines for regression detection."""

    def __init__(self, baseline_file: str = "performance_baseline.json"):
        self.baseline_file = Path(baseline_file)
        self.baselines = self._load_baselines()

    def _load_baselines(self) -> Dict[str, Dict]:
        """Load existing baselines from file."""
        if self.baseline_file.exists():
            with open(self.baseline_file, "r") as f:
                return json.load(f)
        return {}

    def _save_baselines(self):
        """Save baselines to file."""
        with open(self.baseline_file, "w") as f:
            json.dump(self.baselines, f, indent=2)

    def update_baseline(self, test_name: str, metrics: Dict[str, float]):
        """Update baseline with new metrics."""
        self.baselines[test_name] = {
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
            "history": self.baselines.get(test_name, {}).get("history", []),
        }

        # Keep history of last 10 runs
        if len(self.baselines[test_name]["history"]) >= 10:
            self.baselines[test_name]["history"] = self.baselines[test_name]["history"][-9:]

        self.baselines[test_name]["history"].append(
            {"timestamp": self.baselines[test_name]["timestamp"], "metrics": metrics}
        )

        self._save_baselines()
        print(f"Updated baseline for {test_name}: {metrics}")

    def check_regression(
        self,
        test_name: str,
        current_metrics: Dict[str, float],
        thresholds: Optional[Dict[str, float]] = None,
    ) -> Dict[str, bool]:
        """Check for performance regression against baseline."""
        if thresholds is None:
            thresholds = {
                "throughput": 0.1,  # 10% degradation allowed
                "latency": 0.2,  # 20% degradation allowed
                "memory": 0.15,  # 15% increase allowed
                "error_rate": 0.5,  # 50% increase allowed
            }

        if test_name not in self.baselines:
            print(f"No baseline found for {test_name}, creating new baseline")
            self.update_baseline(test_name, current_metrics)
            return {}

        baseline_metrics = self.baselines[test_name]["metrics"]
        regressions = {}

        for metric, current_value in current_metrics.items():
            if metric in baseline_metrics and metric in thresholds:
                baseline_value = baseline_metrics[metric]
                threshold = thresholds[metric]

                if "throughput" in metric or "rate" in metric:
                    # For throughput/rate, lower is worse
                    regression_ratio = (baseline_value - current_value) / baseline_value
                    if regression_ratio > threshold:
                        regressions[metric] = True
                        print(f"REGRESSION: {metric} degraded by {regression_ratio:.1%}")

                elif "latency" in metric or "time" in metric:
                    # For latency/time, higher is worse
                    regression_ratio = (current_value - baseline_value) / baseline_value
                    if regression_ratio > threshold:
                        regressions[metric] = True
                        print(f"REGRESSION: {metric} increased by {regression_ratio:.1%}")

                elif "memory" in metric or "usage" in metric:
                    # For memory/usage, higher is worse
                    regression_ratio = (current_value - baseline_value) / baseline_value
                    if regression_ratio > threshold:
                        regressions[metric] = True
                        print(f"REGRESSION: {metric} increased by {regression_ratio:.1%}")

                elif "error" in metric or "failure" in metric:
                    # For error/failure rates, higher is worse
                    regression_ratio = (current_value - baseline_value) / baseline_value
                    if regression_ratio > threshold:
                        regressions[metric] = True
                        print(f"REGRESSION: {metric} increased by {regression_ratio:.1%}")

        return regressions

    def get_baseline(self, test_name: str) -> Optional[Dict]:
        """Get baseline for a specific test."""
        return self.baselines.get(test_name)

    def list_baselines(self) -> List[str]:
        """List all available baselines."""
        return list(self.baselines.keys())

    def generate_report(self) -> str:
        """Generate a performance baseline report."""
        report = ["# Performance Baseline Report", ""]
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append(f"Total baselines: {len(self.baselines)}")
        report.append("")

        for test_name, data in self.baselines.items():
            report.append(f"## {test_name}")
            report.append(f"Created: {data['timestamp']}")
            report.append("")
            report.append("### Current Baseline Metrics:")
            for metric, value in data["metrics"].items():
                report.append(f"- {metric}: {value}")

            if len(data["history"]) > 1:
                report.append("")
                report.append("### Historical Trend:")
                for metric in data["metrics"].keys():
                    values = [h["metrics"].get(metric, 0) for h in data["history"]]
                    if len(values) > 1:
                        trend = "stable"
                        if values[-1] > values[0] * 1.1:
                            trend = "degrading"
                        elif values[-1] < values[0] * 0.9:
                            trend = "improving"

                        report.append(f"- {metric}: {trend} (latest: {values[-1]:.3f})")

            report.append("")

        return "\n".join(report)


def load_benchmark_results(results_file: str) -> Dict[str, Dict]:
    """Load benchmark results from pytest-benchmark."""
    with open(results_file, "r") as f:
        data = json.load(f)

    metrics = {}
    for benchmark_name, benchmark_data in data["benchmarks"].items():
        # Extract key metrics
        stats = benchmark_data["stats"]

        metrics[benchmark_name] = {
            "mean": stats["mean"],
            "min": stats["min"],
            "max": stats["max"],
            "stddev": stats["stddev"],
            "ops": 1 / stats["mean"] if stats["mean"] > 0 else 0,
        }

    return metrics


def main():
    """Main entry point for baseline management."""
    parser = argparse.ArgumentParser(description="Manage performance baselines")
    parser.add_argument(
        "action", choices=["create", "check", "list", "report"], help="Action to perform"
    )
    parser.add_argument("--test-name", help="Name of the test")
    parser.add_argument("--results-file", help="Benchmark results file")
    parser.add_argument(
        "--baseline-file", default="performance_baseline.json", help="Baseline file path"
    )
    parser.add_argument("--thresholds", help="Regression thresholds as JSON")
    parser.add_argument("--output", help="Output file for reports")

    args = parser.parse_args()

    baseline = PerformanceBaseline(args.baseline_file)

    if args.action == "create":
        if not args.test_name or not args.results_file:
            print("Error: --test-name and --results-file required for create action")
            sys.exit(1)

        metrics = load_benchmark_results(args.results_file)

        for test_name, test_metrics in metrics.items():
            if args.test_name in test_name or test_name == args.test_name:
                baseline.update_baseline(test_name, test_metrics)

    elif args.action == "check":
        if not args.test_name or not args.results_file:
            print("Error: --test-name and --results-file required for check action")
            sys.exit(1)

        thresholds = {}
        if args.thresholds:
            thresholds = json.loads(args.thresholds)

        metrics = load_benchmark_results(args.results_file)

        for test_name, test_metrics in metrics.items():
            if args.test_name in test_name or test_name == args.test_name:
                regressions = baseline.check_regression(test_name, test_metrics, thresholds)

                if regressions:
                    print(f"Regressions detected for {test_name}: {regressions}")
                    sys.exit(1)
                else:
                    print(f"No regressions detected for {test_name}")

    elif args.action == "list":
        baselines = baseline.list_baselines()
        print("Available baselines:")
        for test_name in baselines:
            baseline_data = baseline.get_baseline(test_name)
            print(f"  {test_name} (updated: {baseline_data['timestamp']})")

    elif args.action == "report":
        report = baseline.generate_report()

        if args.output:
            with open(args.output, "w") as f:
                f.write(report)
            print(f"Report written to {args.output}")
        else:
            print(report)


if __name__ == "__main__":
    main()

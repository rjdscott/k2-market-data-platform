#!/usr/bin/env python3
"""
Quality gates for K2 Market Data Platform CI/CD pipeline.

This script implements quality gates that can fail builds based on:
- Test coverage thresholds
- Performance regression detection
- Security scan results
- Code quality metrics
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class QualityGate:
    """Implement quality gates for CI/CD pipeline."""

    def __init__(self):
        self.failures = []
        self.warnings = []

    def add_failure(self, gate: str, message: str):
        """Add a failure that will cause the gate to fail."""
        self.failures.append({"gate": gate, "message": message})

    def add_warning(self, gate: str, message: str):
        """Add a warning that will not cause the gate to fail."""
        self.warnings.append({"gate": gate, "message": message})

    def check_test_coverage(self, coverage_file: str, min_coverage: float = 75.0):
        """Check test coverage against minimum threshold."""
        if not Path(coverage_file).exists():
            self.add_failure("coverage", f"Coverage file not found: {coverage_file}")
            return

        try:
            tree = ET.parse(coverage_file)
            root = tree.getroot()

            # Extract line coverage
            coverage_elem = root.find(".//coverage")
            if coverage_elem is not None:
                line_rate = float(coverage_elem.get("line-rate", 0))
                coverage_percent = line_rate * 100

                if coverage_percent < min_coverage:
                    self.add_failure(
                        "coverage",
                        f"Coverage {coverage_percent:.1f}% below minimum {min_coverage}%",
                    )
                else:
                    print(f"✓ Coverage {coverage_percent:.1f}% meets minimum {min_coverage}%")
            else:
                self.add_failure("coverage", "Could not find coverage metrics in XML")

        except Exception as e:
            self.add_failure("coverage", f"Error parsing coverage file: {e}")

    def check_junit_results(self, junit_file: str, max_failures: int = 0):
        """Check JUnit test results for failures and errors."""
        if not Path(junit_file).exists():
            self.add_failure("tests", f"Test results file not found: {junit_file}")
            return

        try:
            tree = ET.parse(junit_file)
            root = tree.getroot()

            # Count test results
            total_tests = 0
            failures = 0
            errors = 0
            skipped = 0

            for testcase in root.findall(".//testcase"):
                total_tests += 1

                failure = testcase.find("failure")
                error = testcase.find("error")
                skipped_elem = testcase.find("skipped")

                if failure is not None:
                    failures += 1
                if error is not None:
                    errors += 1
                if skipped_elem is not None:
                    skipped += 1

            print(
                f"✓ Test results: {total_tests} tests, {failures} failures, {errors} errors, {skipped} skipped"
            )

            # Check for excessive failures
            total_issues = failures + errors
            if total_issues > max_failures:
                self.add_failure(
                    "tests", f"Too many test failures/errors: {total_issues} (max: {max_failures})"
                )

        except Exception as e:
            self.add_failure("tests", f"Error parsing test results: {e}")

    def check_performance_regression(
        self, benchmark_file: str, baseline_file: str, thresholds: dict[str, float] | None = None
    ):
        """Check for performance regressions."""
        if not Path(benchmark_file).exists():
            self.add_failure("performance", f"Benchmark file not found: {benchmark_file}")
            return

        if not Path(baseline_file).exists():
            self.add_warning("performance", f"No baseline file found: {baseline_file}")
            return

        if thresholds is None:
            thresholds = {
                "mean": 0.2,  # 20% degradation allowed
                "ops": 0.1,  # 10% degradation in operations/sec
            }

        try:
            with open(benchmark_file) as f:
                benchmark_data = json.load(f)

            with open(baseline_file) as f:
                baseline_data = json.load(f)

            regressions = []

            for benchmark_name, benchmark_stats in benchmark_data["benchmarks"].items():
                if benchmark_name in baseline_data.get("benchmarks", {}):
                    baseline_stats = baseline_data["benchmarks"][benchmark_name]["stats"]

                    # Check mean execution time
                    if "mean" in benchmark_stats["stats"]:
                        current_mean = benchmark_stats["stats"]["mean"]
                        baseline_mean = baseline_stats["mean"]

                        if baseline_mean > 0:
                            regression_ratio = (current_mean - baseline_mean) / baseline_mean
                            if regression_ratio > thresholds["mean"]:
                                regressions.append(
                                    {
                                        "benchmark": benchmark_name,
                                        "metric": "mean",
                                        "current": current_mean,
                                        "baseline": baseline_mean,
                                        "regression": f"{regression_ratio:.1%}",
                                    }
                                )

                    # Check operations per second
                    if "ops" in benchmark_stats["stats"]:
                        current_ops = benchmark_stats["stats"]["ops"]
                        baseline_ops = baseline_stats["ops"]

                        if baseline_ops > 0:
                            regression_ratio = (baseline_ops - current_ops) / baseline_ops
                            if regression_ratio > thresholds["ops"]:
                                regressions.append(
                                    {
                                        "benchmark": benchmark_name,
                                        "metric": "ops",
                                        "current": current_ops,
                                        "baseline": baseline_ops,
                                        "regression": f"{regression_ratio:.1%}",
                                    }
                                )

            if regressions:
                for regression in regressions:
                    self.add_failure(
                        "performance",
                        f"Performance regression in {regression['benchmark']}: "
                        f"{regression['metric']} degraded by {regression['regression']}",
                    )
            else:
                print("✓ No performance regressions detected")

        except Exception as e:
            self.add_failure("performance", f"Error checking performance: {e}")

    def check_security_scan(
        self,
        security_file: str,
        max_high_vulnerabilities: int = 0,
        max_medium_vulnerabilities: int = 5,
    ):
        """Check security scan results for vulnerabilities."""
        if not Path(security_file).exists():
            self.add_warning("security", f"Security scan file not found: {security_file}")
            return

        try:
            with open(security_file) as f:
                security_data = json.load(f)

            # Count vulnerabilities by severity
            high_count = 0
            medium_count = 0
            low_count = 0

            for result in security_data.get("results", []):
                severity = result.get("metadata", {}).get("severity", "UNKNOWN").upper()

                if severity == "HIGH":
                    high_count += 1
                elif severity == "MEDIUM":
                    medium_count += 1
                elif severity == "LOW":
                    low_count += 1

            print(
                f"✓ Security scan results: {high_count} high, {medium_count} medium, {low_count} low vulnerabilities"
            )

            # Check thresholds
            if high_count > max_high_vulnerabilities:
                self.add_failure(
                    "security",
                    f"Too many high vulnerabilities: {high_count} (max: {max_high_vulnerabilities})",
                )

            if medium_count > max_medium_vulnerabilities:
                self.add_failure(
                    "security",
                    f"Too many medium vulnerabilities: {medium_count} (max: {max_medium_vulnerabilities})",
                )

        except Exception as e:
            self.add_failure("security", f"Error parsing security scan: {e}")

    def check_code_quality(self, lint_file: str, max_issues: int = 10):
        """Check code quality linting results."""
        if not Path(lint_file).exists():
            self.add_warning("quality", f"Lint file not found: {lint_file}")
            return

        try:
            tree = ET.parse(lint_file)
            root = tree.getroot()

            # Count issues by severity
            error_count = 0
            warning_count = 0

            for testcase in root.findall(".//testcase"):
                failure = testcase.find("failure")
                if failure is not None:
                    severity = failure.get("severity", "ERROR").upper()

                    if severity == "ERROR":
                        error_count += 1
                    elif severity in ["WARNING", "INFO"]:
                        warning_count += 1

            total_issues = error_count + warning_count
            print(f"✓ Code quality results: {error_count} errors, {warning_count} warnings")

            if total_issues > max_issues:
                self.add_failure(
                    "quality", f"Too many code quality issues: {total_issues} (max: {max_issues})"
                )

            if error_count > 0:
                self.add_failure("quality", f"Code quality errors found: {error_count}")

        except Exception as e:
            self.add_failure("quality", f"Error parsing lint results: {e}")

    def check_file_size_limits(self, directory: str, max_size_mb: float = 100.0):
        """Check that file sizes are within limits."""
        try:
            total_size = 0
            large_files = []

            for file_path in Path(directory).rglob("*"):
                if file_path.is_file():
                    size_mb = file_path.stat().st_size / (1024 * 1024)
                    total_size += size_mb

                    if size_mb > 10:  # Individual files larger than 10MB
                        large_files.append(str(file_path))

            print(f"✓ File size check: {total_size:.1f}MB total, {len(large_files)} large files")

            if total_size > max_size_mb:
                self.add_failure(
                    "size", f"Directory too large: {total_size:.1f}MB (max: {max_size_mb}MB)"
                )

            if large_files:
                self.add_warning(
                    "size", f"Found {len(large_files)} large files (>10MB): {large_files[:3]}"
                )

        except Exception as e:
            self.add_failure("size", f"Error checking file sizes: {e}")

    def exit_with_status(self):
        """Exit with appropriate status based on failures and warnings."""
        if self.failures:
            print("\n❌ QUALITY GATE FAILED")
            print("Failures:")
            for failure in self.failures:
                print(f"  - {failure['gate']}: {failure['message']}")

            if self.warnings:
                print("\nWarnings:")
                for warning in self.warnings:
                    print(f"  - {warning['gate']}: {warning['message']}")

            sys.exit(1)

        elif self.warnings:
            print("\n⚠️  QUALITY GATE PASSED WITH WARNINGS")
            for warning in self.warnings:
                print(f"  - {warning['gate']}: {warning['message']}")

            sys.exit(0)

        else:
            print("\n✅ QUALITY GATE PASSED")
            sys.exit(0)


def main():
    """Main entry point for quality gates."""
    parser = argparse.ArgumentParser(description="Check quality gates")
    parser.add_argument("--coverage-file", help="Coverage XML file")
    parser.add_argument("--junit-file", help="JUnit test results file")
    parser.add_argument("--benchmark-file", help="Benchmark results JSON file")
    parser.add_argument("--baseline-file", help="Performance baseline file")
    parser.add_argument("--security-file", help="Security scan results JSON file")
    parser.add_argument("--lint-file", help="Lint results XML file")
    parser.add_argument("--check-directory", help="Directory to check for size limits")

    parser.add_argument(
        "--min-coverage", type=float, default=75.0, help="Minimum coverage percentage"
    )
    parser.add_argument(
        "--max-test-failures", type=int, default=0, help="Maximum allowed test failures"
    )
    parser.add_argument(
        "--max-high-vulns",
        type=int,
        default=0,
        help="Maximum allowed high severity vulnerabilities",
    )
    parser.add_argument(
        "--max-medium-vulns",
        type=int,
        default=5,
        help="Maximum allowed medium severity vulnerabilities",
    )
    parser.add_argument(
        "--max-quality-issues", type=int, default=10, help="Maximum allowed code quality issues"
    )
    parser.add_argument(
        "--max-size-mb", type=float, default=100.0, help="Maximum directory size in MB"
    )

    args = parser.parse_args()

    gate = QualityGate()

    # Run all requested checks
    if args.coverage_file:
        gate.check_test_coverage(args.coverage_file, args.min_coverage)

    if args.junit_file:
        gate.check_junit_results(args.junit_file, args.max_test_failures)

    if args.benchmark_file and args.baseline_file:
        gate.check_performance_regression(args.benchmark_file, args.baseline_file)

    if args.security_file:
        gate.check_security_scan(args.security_file, args.max_high_vulns, args.max_medium_vulns)

    if args.lint_file:
        gate.check_code_quality(args.lint_file, args.max_quality_issues)

    if args.check_directory:
        gate.check_file_size_limits(args.check_directory, args.max_size_mb)

    # Exit with appropriate status
    gate.exit_with_status()


if __name__ == "__main__":
    main()

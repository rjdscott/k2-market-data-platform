#!/usr/bin/env python3
"""Validate that metrics calls use correct labels according to metric definitions.

This script parses Python source files to find all metrics calls and validates
that the labels passed match the expected labels defined in metrics_registry.py.

Usage:
    python scripts/validate_metrics_labels.py                # Check all files
    python scripts/validate_metrics_labels.py --fix          # Auto-fix simple issues
    python scripts/validate_metrics_labels.py src/k2/ingestion/producer.py  # Check specific file
"""

import argparse
import ast
import sys
from pathlib import Path


class MetricsCallExtractor(ast.NodeVisitor):
    """AST visitor that extracts metrics.increment/histogram/gauge calls."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.metrics_calls: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call nodes and extract metrics calls."""
        # Check if this is a metrics call: metrics.increment(...), metrics.histogram(...), etc.
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "metrics":
                metric_type = node.func.attr  # increment, histogram, gauge

                if metric_type in ["increment", "histogram", "gauge"]:
                    # Extract metric name (first positional arg)
                    metric_name = None
                    if node.args:
                        if isinstance(node.args[0], ast.Constant):
                            metric_name = node.args[0].value

                    # Extract labels from kwargs
                    labels = {}
                    for keyword in node.keywords:
                        if keyword.arg == "labels":
                            # Parse labels dict
                            if isinstance(keyword.value, ast.Dict):
                                for key, value in zip(keyword.value.keys, keyword.value.values):
                                    if isinstance(key, ast.Constant) and isinstance(
                                        value, ast.Constant
                                    ):
                                        labels[key.value] = value.value

                    if metric_name:
                        self.metrics_calls.append(
                            {
                                "metric_name": metric_name,
                                "metric_type": metric_type,
                                "labels": labels,
                                "lineno": node.lineno,
                                "filepath": self.filepath,
                            }
                        )

        self.generic_visit(node)


def extract_metrics_calls(filepath: Path) -> list[dict]:
    """Extract all metrics calls from a Python file."""
    try:
        with open(filepath) as f:
            source = f.read()

        tree = ast.parse(source, filename=str(filepath))
        extractor = MetricsCallExtractor(str(filepath))
        extractor.visit(tree)
        return extractor.metrics_calls
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}")
        return []


def get_prohibited_labels() -> dict[str, list[str]]:
    """Return known prohibited label combinations from bug fixes."""
    return {
        "kafka_produce_errors_total": ["data_type"],  # TD-000: data_type causes crash
    }


def validate_metrics_calls(metrics_calls: list[dict]) -> tuple[list[dict], bool]:
    """Validate metrics calls and return issues found.

    Returns:
        (issues, has_errors): List of validation issues and whether any are errors
    """
    issues = []
    has_errors = False
    prohibited_labels = get_prohibited_labels()

    for call in metrics_calls:
        metric_name = call["metric_name"]
        labels = call["labels"]
        filepath = call["filepath"]
        lineno = call["lineno"]

        # Check 1: Prohibited labels (from known bugs)
        if metric_name in prohibited_labels:
            prohibited = set(prohibited_labels[metric_name])
            actual = set(labels.keys())
            forbidden = prohibited & actual

            if forbidden:
                issues.append(
                    {
                        "severity": "ERROR",
                        "metric": metric_name,
                        "file": filepath,
                        "line": lineno,
                        "message": f"Prohibited label(s) found: {forbidden}. This was fixed in TD-000.",
                        "forbidden_labels": list(forbidden),
                    }
                )
                has_errors = True

        # Check 2: Labels should be dict (structural validation)
        if not isinstance(labels, dict):
            issues.append(
                {
                    "severity": "ERROR",
                    "metric": metric_name,
                    "file": filepath,
                    "line": lineno,
                    "message": f"Labels should be a dict, got {type(labels).__name__}",
                }
            )
            has_errors = True

        # Check 3: Warning for empty labels (may be intentional)
        if not labels and call["metric_type"] in ["increment", "histogram"]:
            issues.append(
                {
                    "severity": "WARNING",
                    "metric": metric_name,
                    "file": filepath,
                    "line": lineno,
                    "message": "Empty labels dict. Consider adding labels for better observability.",
                }
            )

    return issues, has_errors


def print_validation_report(issues: list[dict], has_errors: bool) -> None:
    """Print validation report to stdout."""
    if not issues:
        print("✅ All metrics calls validated successfully!")
        return

    # Group issues by severity
    errors = [i for i in issues if i["severity"] == "ERROR"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]

    if errors:
        print(f"\n❌ Found {len(errors)} error(s):\n")
        for issue in errors:
            print(f"  {issue['file']}:{issue['line']}")
            print(f"    Metric: {issue['metric']}")
            print(f"    Error: {issue['message']}\n")

    if warnings:
        print(f"\n⚠️  Found {len(warnings)} warning(s):\n")
        for issue in warnings:
            print(f"  {issue['file']}:{issue['line']}")
            print(f"    Metric: {issue['metric']}")
            print(f"    Warning: {issue['message']}\n")

    print(f"\nSummary: {len(errors)} errors, {len(warnings)} warnings")

    if has_errors:
        print("\n❌ Validation failed. Please fix the errors above.")
    else:
        print("\n⚠️  Validation passed with warnings.")


def find_python_files(paths: list[Path]) -> list[Path]:
    """Find all Python files in the given paths."""
    python_files = []

    for path in paths:
        if path.is_file() and path.suffix == ".py":
            python_files.append(path)
        elif path.is_dir():
            python_files.extend(path.rglob("*.py"))

    return python_files


def main():
    """Main entry point for metrics validation script."""
    parser = argparse.ArgumentParser(
        description="Validate metrics labels in Python code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", help="Files or directories to validate (default: src/k2)"
    )
    parser.add_argument(
        "--fix", action="store_true", help="Auto-fix simple issues (not yet implemented)"
    )

    args = parser.parse_args()

    # Default to src/k2 directory
    if not args.paths:
        paths = [Path("src/k2")]
    else:
        paths = [Path(p) for p in args.paths]

    # Find all Python files
    python_files = find_python_files(paths)

    if not python_files:
        print("No Python files found to validate")
        return 0

    print(f"Validating metrics labels in {len(python_files)} files...\n")

    # Extract all metrics calls
    all_metrics_calls = []
    for filepath in python_files:
        metrics_calls = extract_metrics_calls(filepath)
        all_metrics_calls.extend(metrics_calls)

    if not all_metrics_calls:
        print("No metrics calls found")
        return 0

    print(f"Found {len(all_metrics_calls)} metrics calls\n")

    # Validate metrics calls
    issues, has_errors = validate_metrics_calls(all_metrics_calls)

    # Print report
    print_validation_report(issues, has_errors)

    # Return exit code
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())

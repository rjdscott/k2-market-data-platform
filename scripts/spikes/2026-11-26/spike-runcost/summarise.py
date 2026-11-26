"""Min / median / max per step from a run-cost spike's result files.

    python3 summarise.py <dir>     # <dir> holds spark-runs.txt and dbt-runs.txt

run.sh calls it on the files it just wrote; on results-2026-11-26/ it prints the table
published in docs/benchmarks/2026-11-26-product-run-cost.md. With n = 5 there is no
p90 to speak of: the maximum is the only tail figure the sample supports.
"""

import json
import re
import statistics
import sys
from pathlib import Path

STEPS = [("session", "Spark session with the catalog loaded"), ("create_branch", "Create branch"),
         ("append_branch", "Append 1,000 rows"), ("audit_count", "Audit: count"),
         ("audit_unique", "Audit: uniqueness"), ("fast_forward", "Fast-forward"),
         ("drop_branch", "Drop branch"), ("stop", "Stop"), ("host_wall", "Total, wall clock")]
SPARK = re.compile(r"args=run (\w) host_wall=([\d.]+) (?:ingest_at_end=\w+ )?(\{.*\})$")
DBT = re.compile(r"run=(\d+) rc=0 wall=([\d.]+)")


def spread(xs):
    return f"{min(xs):.2f} / {statistics.median(xs):.2f} / {max(xs):.2f}"


def main(d: Path) -> None:
    runs = {"a": [], "b": []}
    for line in (d / "spark-runs.txt").read_text().splitlines():
        if m := SPARK.search(line):
            runs[m[1]].append({**json.loads(m[3]), "host_wall": float(m[2])})
    n = {k: len(v) for k, v in runs.items()}
    print(f"Spark, seconds, min / median / max; small table n = {n['a']}, 200 snapshots n = {n['b']}")
    print("| Step | Small table | Table with 200 snapshots, 200k rows |\n|---|---|---|")
    for key, label in STEPS:
        print(f"| {label} | {spread([r[key] for r in runs['a']])} | {spread([r[key] for r in runs['b']])} |")
    dbt = [float(m[2]) for line in (d / "dbt-runs.txt").read_text().splitlines()
           if (m := DBT.match(line)) and m[1] != "0"]
    print(f"dbt build, one incremental model and two tests, wall clock: {spread(dbt)} s (n = {len(dbt)})")
    small = statistics.median(r["host_wall"] for r in runs["a"])
    verdict = ("under the 200 s chain line, so per-product Spark sessions stay" if small < 200 else
               "over the 200 s chain line: take the first escape of plan 004 (one session for the connectors)")
    print(f"finding: one small product run costs {small:.2f} s of Spark (median, n = {n['a']}), {verdict}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

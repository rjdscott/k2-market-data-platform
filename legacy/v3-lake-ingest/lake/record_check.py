#!/usr/bin/env python3
"""
One row into `audit.checks` from the command line — the reproducibility record
of a replay run (scripts/replay-lake.sh, ADR-029).

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
        --job replay --check replay --scope market.crypto.v3.raw.kraken/<conn_id> \
        --observed <records> --detail 'snapshot=… sha256=… crate=…'

Same table and the same writer as the nightly audit and the ingest findings
(catalog.write_audit_rows), with `k2.job=<job>` on the commit so neither
`k2_lake_audit_failures_total` (reads the newest maintenance summary) nor the
ingest gauges (newest ingest summary) can be moved by a replay row.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from catalog import write_audit_rows
from pyspark.sql import Row

from spark_conf import lake_session


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", default="replay", choices=("replay", "operator"))
    ap.add_argument("--check", required=True, help="check_name, e.g. replay")
    ap.add_argument("--scope", required=True, help="what was replayed: topic/conn_id")
    ap.add_argument("--observed", type=int, required=True, help="records the replay produced")
    ap.add_argument("--detail", required=True, help="snapshot id, output sha256, crate sha — the reproducibility triple")
    ap.add_argument("--failed", action="store_true", help="record passed=false (the replay did not complete)")
    args = ap.parse_args()

    spark = lake_session("k2-record-check")
    row = Row(
        run_ts=datetime.now(timezone.utc),
        job=args.job,
        check_name=args.check,
        scope=args.scope,
        passed=not args.failed,
        observed=args.observed,
        detail=args.detail,
    )
    if not write_audit_rows(spark, [row], {}, job=args.job):
        sys.exit(1)
    print(f"audit.checks: {args.job}/{args.check} {args.scope} observed={args.observed}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Register (upsert) the two v3 lake deployments. Run by the `prefect-worker`
service at start, after the two v2 offload deployments and before the worker
starts — the v2 deployments stay registered through the parallel-run window.

    python /opt/prefect/lake-flows/deploy_lake.py

Work pool `iceberg-offload` is reused rather than a new `lake` pool created: it
already exists, already has a running worker, and a second process-type pool on
the same host would be a second thing to create, monitor and forget to start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lake_flows import lake_ingest, lake_maintenance  # noqa: E402 - after sys.path

os.environ.setdefault("PREFECT_API_URL", "http://localhost:4200/api")

SOURCE = "/opt/prefect/lake-flows"
WORK_POOL = "iceberg-offload"


def main() -> int:
    print(f"Prefect API: {os.environ['PREFECT_API_URL']}")

    ingest_id = lake_ingest.from_source(
        source=SOURCE, entrypoint="lake_flows.py:lake_ingest"
    ).deploy(
        name="lake-ingest-5min",
        work_pool_name=WORK_POOL,
        cron="*/5 * * * *",
        # 1, and it is load-bearing rather than tidy. Two ingests running at once
        # would both read from the same committed offsets and both write the same
        # records: the snapshot summary makes a *sequence* of runs exactly-once,
        # not a pair of concurrent ones.
        concurrency_limit=1,
        tags=["lake", "v3", "ingest"],
        description="Redpanda -> Iceberg raw.messages -> bronze.*, every 5 minutes (ADR-018)",
        version="1.0.0",
        parameters={"end_timestamp": ""},
        paused=False,
    )
    print(f"  lake-ingest-5min       {ingest_id}")

    # 03:00 UTC, an hour after the v2 iceberg-maintenance-daily at 02:00, so the
    # two compactions do not contend for the same Spark container during the
    # parallel-run window.
    maintenance_id = lake_maintenance.from_source(
        source=SOURCE, entrypoint="lake_flows.py:lake_maintenance"
    ).deploy(
        name="lake-maintenance-daily",
        work_pool_name=WORK_POOL,
        cron="0 3 * * *",
        concurrency_limit=1,
        tags=["lake", "v3", "maintenance"],
        description="Compact, expire snapshots, audit. Non-zero exit on a failed audit.",
        version="1.0.0",
        parameters={"days": 2, "retain_days": 7},
        paused=False,
    )
    print(f"  lake-maintenance-daily {maintenance_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the message is the point
        print(f"lake deployment failed: {exc}", file=sys.stderr)
        sys.exit(1)

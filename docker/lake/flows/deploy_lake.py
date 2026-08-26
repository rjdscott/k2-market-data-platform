#!/usr/bin/env python3
"""
Register (upsert) the two v3 lake deployments. Run by the `prefect-worker`
service at start, before the worker itself starts.

    python /opt/prefect/lake-flows/deploy_lake.py

These are the only deployments on the stack. v2's pair went with
docker/offload/, and the `iceberg-offload` work pool they ran on went with them:
this file and the compose command both name `lake`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lake_flows import lake_ingest, lake_maintenance  # noqa: E402 - after sys.path

os.environ.setdefault("PREFECT_API_URL", "http://localhost:4200/api")

SOURCE = "/opt/prefect/lake-flows"
WORK_POOL = "lake"


def main() -> int:
    print(f"Prefect API: {os.environ['PREFECT_API_URL']}")

    ingest_id = lake_ingest.from_source(
        source=SOURCE, entrypoint="lake_flows.py:lake_ingest"
    ).deploy(
        name="lake-ingest-5min",
        work_pool_name=WORK_POOL,
        # Minutes 1, 6, 11 … 56 — offset by one from `*/5`, and kept that way.
        #
        # The offset was introduced when v2's `iceberg-offload` (`*/15`) shared
        # this container: on `*/5` the two collided at :00, :15, :30 and :45 and
        # started two Spark drivers at once in a 2 CPU / 4 GiB box. That path is
        # gone, so the collision is gone with it — but the offset still costs
        # nothing (the ingest resumes from the offsets in its own last snapshot,
        # so *when* a cycle runs changes nothing about what it reads) and it
        # keeps this job off the top of the minute that every cron on a host
        # crowds into. The cadence is 5 minutes either way.
        cron="1-59/5 * * * *",
        # 1, and belt-and-braces rather than the contract. Two ingests at once
        # both read the same committed offsets and both write the same records —
        # the snapshot summary makes a *sequence* of runs exactly-once, not a
        # pair of concurrent ones. But this setting only gates runs Prefect
        # launched, and the runbooks, the chaos scripts and `make lake-verify`
        # all `docker exec` an ingest directly. The guard that covers every path
        # is the flock in ingest.py's main().
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

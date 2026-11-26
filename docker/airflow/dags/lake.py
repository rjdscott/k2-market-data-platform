"""
The lake schedules under Airflow 3 (ADR-034): `lake_ingest`, `lake_maintenance`
and `lake_rebuild`. Three DAGs, written out by hand. The file sources' DAGs are
generated instead, by factory.py (ADR-036), over the same operator.

Each one is a single task that runs one docker/lake script in the Spark
container (lake_script.py), exactly as the retired flows did (archived in legacy/prefect/flows/):

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/<script> ...

The DAGs add no lake logic. Position, idempotency and mutual exclusion live in
the scripts: offsets in the Iceberg snapshot summary (ADR-022), one writer at a
time through the flock in docker/lake/lock.py. What Airflow contributes is the
schedule, one run at a time, a timeout that cannot leave the lock held, and
metrics Prometheus can alert on.

Airflow is the only scheduler of the lake writes since the cutover (plan phase
P2, pull request 2). The retired scheduler is archived under legacy/; the
recoveries are in docs/runbooks/airflow-scheduling.md.

Rules every DAG here keeps, each from an incident on this stack:

- `max_active_runs=1` and `catchup=False`, and nothing else that replays a
  missed interval. The ingest reads from its committed offsets, so a late run
  reads what a punctual one would have; replaying intervals only stacks drivers.
- `dagrun_timeout` slightly above the task's own timeout and retries, so a run
  whose task process died without reporting cannot hold the only slot.
- An `execution_timeout` whose handler kills the driver inside the Spark
  container. Killing the `docker exec` client alone leaves the driver running,
  and the driver holds the lock.
"""

from __future__ import annotations

from datetime import timedelta

from airflow.sdk import DAG, Param
from lake_script import COMMON, LakeScript

with DAG(
    dag_id="lake_ingest",
    # Minutes 1, 6, 11 ... 56: the cron the retired scheduler ran, kept off the top of the
    # minute that every other cron on a host crowds into.
    schedule="1-59/5 * * * *",
    # Two tries of 3600 s and the 60 s between them, plus slack.
    dagrun_timeout=timedelta(seconds=2 * 3600 + 60 + 240),
    is_paused_upon_creation=False,
    # A catch-up after a long stall is a manual trigger with these set, not a
    # shell loop. Unset means the script's own default.
    params={
        "driver_memory": Param(
            None,
            type=["null", "string"],
            pattern=r"^[1-9][0-9]*[mg]$",
            description="K2_LAKE_DRIVER_MEMORY for this run, e.g. 2g. Default 1536m "
            "(docker/lake/spark_conf.py). The Spark container is capped at 8 GiB.",
        ),
        "max_offsets_per_partition": Param(
            None,
            type=["null", "integer"],
            minimum=0,
            description="--max-offsets-per-partition for this run; 0 reads to latest. "
            "Default 600000 (docker/lake/ingest.py).",
        ),
    },
    **COMMON,
):
    LakeScript(
        task_id="ingest",
        script="ingest.py",
        args=[
            "{{ '--max-offsets-per-partition=%d' % params.max_offsets_per_partition"
            " if params.max_offsets_per_partition is not none else '' }}",
        ],
        env={"K2_LAKE_DRIVER_MEMORY": "{{ params.driver_memory or '' }}"},
        # One stage, one task: split it and maintenance could take the lock
        # between stages. Only this task maps exit 75 to skipped.
        skip_on_lock_held=True,
        # One retry: what this sees is a Lakekeeper commit losing an
        # optimistic-concurrency race. Safe at any point (ADR-022).
        retries=1,
        retry_delay=timedelta(seconds=60),
        execution_timeout=timedelta(seconds=3600),
    )

with DAG(
    dag_id="lake_maintenance",
    schedule="0 3 * * *",
    dagrun_timeout=timedelta(seconds=7200 + 300),
    # Read only for a DAG the database has never seen. A stack that ran the
    # schedule shadow holds this one paused, and the cutover unpauses it by
    # hand. Unpausing starts the most recent 03:00 slot at once (catchup=False
    # still runs the latest slot), so the first maintenance follows within a
    # scheduler loop.
    is_paused_upon_creation=False,
    params={
        "days": Param(2, type="integer", minimum=1, description="compaction window, days back"),
        "retain_days": Param(7, type="integer", minimum=7, description="snapshot retention floor"),
    },
    **COMMON,
):
    LakeScript(
        task_id="maintain",
        script="maintenance.py",
        args=["--days", "{{ params.days }}", "--retain-days", "{{ params.retain_days }}"],
        # No retry: a failed audit is a finding, not a blip.
        retries=0,
        execution_timeout=timedelta(seconds=7200),
    )

with DAG(
    dag_id="lake_rebuild",
    schedule=None,
    # A whole-archive rebuild runs for hours and grows with the archive. A
    # rebuild stopped by this timeout leaves the layer part-built: run it again.
    dagrun_timeout=timedelta(hours=24, minutes=5),
    is_paused_upon_creation=False,
    params={
        "layer": Param("bronze", type="string", description="bronze, silver, gold, books or bars (rebuild.py --layer)"),
        "exchange": Param(None, type=["null", "string"], description="one venue only (rebuild.py --exchange)"),
        # Dropping a layer is destructive, so a trigger with the defaults only
        # prints the plan. Untick to rebuild.
        "dry_run": Param(True, type="boolean", description="print the plan, drop nothing"),
    },
    **COMMON,
):
    LakeScript(
        task_id="rebuild",
        script="rebuild.py",
        args=[
            "--layer", "{{ params.layer }}",
            "{{ '--exchange=' ~ params.exchange if params.exchange else '' }}",
            "{{ '--dry-run' if params.dry_run else '' }}",
        ],
        retries=0,
        execution_timeout=timedelta(hours=24),
    )

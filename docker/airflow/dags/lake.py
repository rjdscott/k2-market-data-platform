"""
The lake schedules under Airflow 3 (ADR-034): `lake_ingest`, `lake_maintenance`
and `lake_rebuild`. Three DAGs, written out by hand; there is no factory.

Each one is a single task that runs one docker/lake script in the Spark
container, exactly as the Prefect flows did (docker/lake/flows/lake_flows.py):

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/<script> ...

The DAGs add no lake logic. Position, idempotency and mutual exclusion live in
the scripts: offsets in the Iceberg snapshot summary (ADR-022), one writer at a
time through the flock in docker/lake/lock.py. What Airflow contributes is the
schedule, one run at a time, a timeout that cannot leave the lock held, and
metrics Prometheus can alert on.

Airflow is the only scheduler of the lake writes since the cutover (plan phase
P2, pull request 2). Prefect is stopped behind the compose `legacy` profile as
the documented rollback: docs/runbooks/airflow-scheduling.md.

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

import subprocess
from datetime import datetime, timedelta, timezone

from airflow.sdk import DAG, BaseOperator, Param
from airflow.sdk.exceptions import AirflowException, AirflowSkipException

SPARK_CONTAINER = "k2-spark-iceberg"
LAKE_DIR = "/home/iceberg/lake"

# os.EX_TEMPFAIL: ingest.py found the writer lock held. Not 2, which is also
# what Python returns for a missing script and argparse for a bad flag.
EXIT_LOCK_HELD = 75

# Runs inside the Spark container as `sh -c WRAPPER <pidfile> <script> <args>`.
# The driver runs in the background so the shell can record its pid where
# on_kill can find it, then waits, removes the file and exits with the
# driver's status. Python's own pid is the one that holds the flock.
WRAPPER = 'python3 "$@" & echo $! > "$0"; wait $!; rc=$?; rm -f "$0"; exit $rc'

# Runs inside the Spark container as `sh -c KILL <pidfile>`. TERM to the
# driver, up to KILL_GRACE_S for it to go, then KILL. The Spark JVM is not
# signalled: PySpark's gateway exits when its stdin from the driver closes,
# measured at 2 s after a TERM and after a KILL in the Spark image. A missing
# file means the driver already exited.
KILL_GRACE_S = 10
KILL = (
    'p=$(cat "$0" 2>/dev/null) || exit 0; kill -TERM "$p" 2>/dev/null; i=0; '
    f'while kill -0 "$p" 2>/dev/null && [ $i -lt {KILL_GRACE_S} ]; do sleep 1; i=$((i+1)); done; '
    'kill -KILL "$p" 2>/dev/null; rm -f "$0"; true'
)


class LakeScript(BaseOperator):
    """`docker exec` one docker/lake script; fail on a non-zero exit.

    `args` and `env` are templated. An argument or an environment value that
    renders empty is dropped, which is how an unset param means "the script's
    own default" rather than an empty flag.
    """

    template_fields = ("args", "env")

    def __init__(self, *, script, args=(), env=None, skip_on_lock_held=False, **kwargs):
        super().__init__(**kwargs)
        self.script = script
        self.args = list(args)
        self.env = dict(env or {})
        self.skip_on_lock_held = skip_on_lock_held
        self._pidfile = None
        self._proc = None

    def command(self, pidfile):
        env = [f"--env={k}={v}" for k, v in self.env.items() if v]
        args = [a for a in self.args if a]
        return [
            "docker", "exec", *env, SPARK_CONTAINER,
            "sh", "-c", WRAPPER, pidfile, f"{LAKE_DIR}/{self.script}", *args,
        ]

    def execute(self, context):
        # One file per task try: the id is a UUID that changes on every retry.
        self._pidfile = f"/tmp/k2-airflow-{context['ti'].id}.pid"
        cmd = self.command(self._pidfile)
        self.log.info("running: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in self._proc.stdout:
            self.log.info("%s", line.rstrip())
        rc = self._proc.wait()
        if rc == EXIT_LOCK_HELD and self.skip_on_lock_held:
            raise AirflowSkipException(f"{self.script} exited {rc}: another lake writer holds the lock")
        if rc != 0:
            raise AirflowException(f"{self.script} exited {rc}")

    def on_kill(self):
        """Airflow calls this on execution_timeout and on SIGTERM (task
        killed, run timed out). The driver is stopped first, so the lock is
        free before this task reports anything."""
        if self._pidfile:
            subprocess.run(
                ["docker", "exec", SPARK_CONTAINER, "sh", "-c", KILL, self._pidfile],
                timeout=KILL_GRACE_S + 30,
                check=False,
            )
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


COMMON = dict(
    start_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["lake"],
)

with DAG(
    dag_id="lake_ingest",
    # Minutes 1, 6, 11 ... 56: the cron Prefect ran, kept off the top of the
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
            description="K2_LAKE_DRIVER_MEMORY for this run, e.g. 2g. Default 768m "
            "(docker/lake/spark_conf.py). The Spark container is capped at 8 GiB.",
        ),
        "max_offsets_per_partition": Param(
            None,
            type=["null", "integer"],
            minimum=0,
            description="--max-offsets-per-partition for this run; 0 reads to latest. "
            "Default 200000 (docker/lake/ingest.py).",
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

"""
The one way a DAG here runs a lake script: `docker exec` into the Spark
container, with a kill path that stops the driver, which holds the writer lock.

Used by lake.py (three hand-written DAGs) and factory.py (one DAG per file
source). The rules each comes from are in lake.py's docstring.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from airflow.sdk import BaseOperator
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
    own default" rather than an empty flag. With `return_last_line`, the
    script's last non-empty line of output is the task's return value.
    """

    template_fields = ("args", "env")

    def __init__(self, *, script, args=(), env=None, skip_on_lock_held=False, return_last_line=False, **kwargs):
        super().__init__(**kwargs)
        self.script = script
        self.args = list(args)
        self.env = dict(env or {})
        self.skip_on_lock_held = skip_on_lock_held
        self.return_last_line = return_last_line
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
        last = None
        for line in self._proc.stdout:
            self.log.info("%s", line.rstrip())
            last = line.strip() or last
        rc = self._proc.wait()
        if rc == EXIT_LOCK_HELD and self.skip_on_lock_held:
            raise AirflowSkipException(f"{self.script} exited {rc}: another lake writer holds the lock")
        if rc != 0:
            raise AirflowException(f"{self.script} exited {rc}")
        # The return value is the task's XCom: how a file source's fetch hands the
        # landed key to its load, audit and publish (factory.py).
        return last if self.return_last_line else None

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

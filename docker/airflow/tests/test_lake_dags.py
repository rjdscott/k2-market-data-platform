"""
docker/airflow/dags/lake.py, checked inside the Airflow image with no stack:

    make test-airflow

stdlib unittest, because the image carries no pytest and the root suite
(`make test-python`) has no Airflow. The repository is mounted read-only at
/repo; nothing here reaches Docker, Postgres or the network.
"""

from __future__ import annotations

import copy
import fcntl
import os
import subprocess
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from airflow.dag_processing.dagbag import BundleDagBag
from airflow.sdk.exceptions import AirflowException, AirflowSkipException, ParamValidationError

REPO = Path(__file__).resolve().parents[3]
# As the dag-processor and the task runner parse it: the folder is the bundle, and on
# sys.path, which is how lake.py and factory.py import lake_script.py.
BAG = BundleDagBag(dag_folder=str(REPO / "docker/airflow/dags"), bundle_path=REPO / "docker/airflow/dags")
LAKE = ("lake_ingest", "lake_maintenance", "lake_rebuild")
CONTEXT = {"ti": SimpleNamespace(id="0192f0c4-test")}


def task(dag_id):
    (only,) = BAG.dags[dag_id].tasks
    return only


def rendered(dag_id, **params):
    """A copy of the DAG's task with its templates rendered against `params`."""
    t = copy.deepcopy(task(dag_id))
    t.render_template_fields({"params": {**BAG.dags[dag_id].params.dump(), **params}})
    return t.command("/tmp/x.pid")


class FakeProc:
    def __init__(self, rc):
        self.rc, self.stdout = rc, iter(["a line of script output\n"])

    def wait(self):
        return self.rc

    def poll(self):
        return self.rc


def run_with_exit(dag_id, rc):
    with mock.patch("subprocess.Popen", return_value=FakeProc(rc)) as popen:
        task(dag_id).execute(CONTEXT)
    return popen.call_args.args[0]


class Structure(unittest.TestCase):
    def test_the_file_imports_and_holds_exactly_three_dags(self):
        self.assertEqual(BAG.import_errors, {})
        self.assertEqual({d for d, dag in BAG.dags.items() if dag.fileloc.endswith("/lake.py")}, set(LAKE))

    def test_no_dag_replays_or_overlaps(self):
        for dag in (BAG.dags[d] for d in LAKE):
            with self.subTest(dag.dag_id):
                self.assertFalse(dag.catchup)
                self.assertEqual(dag.max_active_runs, 1)
                timeout = task(dag.dag_id).execution_timeout
                self.assertIsNotNone(timeout)
                # A run outlives its task's tries by a margin, never by hours.
                t = task(dag.dag_id)
                tries = (t.retries + 1) * timeout + t.retries * t.retry_delay
                self.assertGreater(dag.dagrun_timeout, tries)
                self.assertLess(dag.dagrun_timeout, tries + timedelta(minutes=10))

    def test_schedules_retries_and_timeouts_match_the_plan(self):
        ingest, maint, rebuild = (task(d) for d in ("lake_ingest", "lake_maintenance", "lake_rebuild"))
        self.assertEqual(BAG.dags["lake_ingest"].schedule, "1-59/5 * * * *")
        self.assertEqual((ingest.retries, ingest.retry_delay), (1, timedelta(seconds=60)))
        self.assertEqual(ingest.execution_timeout, timedelta(seconds=3600))
        self.assertEqual(BAG.dags["lake_maintenance"].schedule, "0 3 * * *")
        # A cron trigger fires at the cron time and has no data interval to
        # fill in after a stall, which is the other half of catchup=False.
        for dag_id in ("lake_ingest", "lake_maintenance"):
            self.assertEqual(type(BAG.dags[dag_id].timetable).__name__, "CronTriggerTimetable")
        self.assertEqual((maint.retries, maint.execution_timeout), (0, timedelta(seconds=7200)))
        self.assertIsNone(BAG.dags["lake_rebuild"].schedule)
        self.assertEqual(rebuild.retries, 0)

    def test_after_the_cutover_ingest_writes_and_no_dag_starts_paused(self):
        # The real ingest: the bare script with no flag at the defaults, not
        # the shadow's write-nothing probe.
        cmd = rendered("lake_ingest")
        self.assertNotIn("--probe", cmd)
        self.assertEqual(cmd[-1], "/home/iceberg/lake/ingest.py")
        # The cutover runbook greps the mounted file for this string to tell a
        # stale bind mount from a deployed one, so it must not appear at all.
        self.assertNotIn("--probe", (REPO / "docker/airflow/dags/lake.py").read_text())
        for dag_id in BAG.dags:
            with self.subTest(dag_id):
                self.assertFalse(BAG.dags[dag_id].is_paused_upon_creation)


class ExitCodes(unittest.TestCase):
    def test_ingest_maps_75_to_skipped(self):
        with self.assertRaises(AirflowSkipException):
            run_with_exit("lake_ingest", 75)

    def test_ingest_fails_on_any_other_non_zero_exit(self):
        for rc in (1, 2, 137):
            with self.subTest(rc), self.assertRaises(AirflowException) as err:
                run_with_exit("lake_ingest", rc)
            self.assertNotIsInstance(err.exception, AirflowSkipException)

    def test_only_ingest_skips_on_75(self):
        for dag_id in ("lake_maintenance", "lake_rebuild"):
            with self.subTest(dag_id), self.assertRaises(AirflowException) as err:
                run_with_exit(dag_id, 75)
            self.assertNotIsInstance(err.exception, AirflowSkipException)

    def test_success_is_a_docker_exec_into_the_spark_container(self):
        cmd = run_with_exit("lake_maintenance", 0)
        self.assertEqual(cmd[:2], ["docker", "exec"])
        self.assertIn("k2-spark-iceberg", cmd)
        self.assertIn("/home/iceberg/lake/maintenance.py", cmd)
        self.assertIn("/tmp/k2-airflow-0192f0c4-test.pid", cmd)


class Params(unittest.TestCase):
    def test_unset_params_leave_the_script_defaults_alone(self):
        cmd = rendered("lake_ingest")
        self.assertFalse([a for a in cmd if "K2_LAKE_DRIVER_MEMORY" in a or "max-offsets" in a])
        self.assertNotIn("", cmd)

    def test_catch_up_params_map_to_the_env_and_the_flag(self):
        cmd = rendered("lake_ingest", driver_memory="4g", max_offsets_per_partition=500000)
        self.assertIn("--env=K2_LAKE_DRIVER_MEMORY=4g", cmd)
        self.assertIn("--max-offsets-per-partition=500000", cmd)
        # docker exec options go before the container name, script flags after.
        self.assertLess(cmd.index("--env=K2_LAKE_DRIVER_MEMORY=4g"), cmd.index("k2-spark-iceberg"))
        self.assertGreater(cmd.index("--max-offsets-per-partition=500000"), cmd.index("k2-spark-iceberg"))

    def test_zero_offsets_means_read_to_latest_and_is_passed(self):
        self.assertIn("--max-offsets-per-partition=0", rendered("lake_ingest", max_offsets_per_partition=0))

    def test_bad_params_are_refused_before_a_run(self):
        params = BAG.dags["lake_ingest"].params
        for name, bad in (("driver_memory", "4 gigs"), ("driver_memory", "0g"), ("max_offsets_per_partition", -1)):
            with self.subTest(name=name, value=bad), self.assertRaises(ParamValidationError):
                params.get_param(name).resolve(bad)

    def test_rebuild_defaults_to_a_dry_run(self):
        self.assertIn("--dry-run", rendered("lake_rebuild"))
        cmd = rendered("lake_rebuild", layer="silver", exchange="kraken", dry_run=False)
        self.assertNotIn("--dry-run", cmd)
        self.assertEqual(cmd[-3:], ["--layer", "silver", "--exchange=kraken"])


class KillPath(unittest.TestCase):
    """The wrapper and the kill script for real, as processes, with
    docker/lake/lock.py held by the "driver". `sh` here stands in for the
    Spark container's; both images are Debian, where /bin/sh is dash."""

    def test_on_kill_runs_the_kill_script_in_the_spark_container(self):
        t = copy.deepcopy(task("lake_ingest"))
        t._pidfile = "/tmp/k2-airflow-x.pid"
        with mock.patch("subprocess.run") as run:
            t.on_kill()
        self.assertEqual(
            run.call_args.args[0],
            ["docker", "exec", "k2-spark-iceberg", "sh", "-c", t_module().KILL, "/tmp/k2-airflow-x.pid"],
        )

    def test_wrapper_passes_the_drivers_exit_status_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = f"{d}/run.pid"
            rc = subprocess.run(["sh", "-c", t_module().WRAPPER, pidfile, "-c", "raise SystemExit(75)"]).returncode
            self.assertEqual(rc, 75)
            self.assertFalse(os.path.exists(pidfile))

    def test_kill_script_stops_the_driver_and_frees_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile, lockfile = f"{d}/run.pid", f"{d}/lake.lock"
            driver = (
                f"import sys, time; sys.path.insert(0, '{REPO}/docker/lake'); "
                f"from lock import acquire_lock; h = acquire_lock('{lockfile}'); "
                "assert h is not None; print('locked', flush=True); time.sleep(120)"
            )
            wrapper = subprocess.Popen(
                ["sh", "-c", t_module().WRAPPER, pidfile, "-c", driver], stdout=subprocess.PIPE, text=True
            )
            self.assertEqual(wrapper.stdout.readline().strip(), "locked")
            self.assertTrue(os.path.exists(pidfile))
            # What the bug looked like: the lock is held while the driver lives.
            self.assertFalse(try_lock(lockfile))

            started = time.monotonic()
            subprocess.run(["sh", "-c", t_module().KILL, pidfile], check=True, timeout=30)
            self.assertLess(time.monotonic() - started, t_module().KILL_GRACE_S)
            self.assertNotEqual(wrapper.wait(timeout=10), 0)
            wrapper.stdout.close()
            self.assertTrue(try_lock(lockfile))
            self.assertFalse(os.path.exists(pidfile))

    def test_kill_script_is_a_no_op_once_the_driver_has_exited(self):
        rc = subprocess.run(["sh", "-c", t_module().KILL, "/tmp/k2-airflow-gone.pid"]).returncode
        self.assertEqual(rc, 0)


def t_module():
    """The module DagBag loaded lake.py as, so constants and classes match."""
    import sys

    return sys.modules[type(task("lake_ingest")).__module__]


def try_lock(path):
    with open(path, "w") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True


if __name__ == "__main__":
    unittest.main()

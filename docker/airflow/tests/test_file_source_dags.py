"""
docker/airflow/dags/factory.py, checked inside the Airflow image with no stack:

    make test-airflow

The repository declares no file source yet, so the factory must build nothing from
the committed file_sources.json; every check of a DAG's shape runs on the synthetic
source, synthetic_file_sources.json beside this file, which is what k2gen generates
for platform/k2gen/tests/synthetic/ (held there by platform/k2gen/tests/test_file_source.py).
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from airflow.dag_processing.dagbag import BundleDagBag
from airflow.sdk.exceptions import AirflowException, AirflowSkipException

REPO = Path(__file__).resolve().parents[3]
DAGS = REPO / "docker/airflow/dags"
BAG = BundleDagBag(dag_folder=str(DAGS), bundle_path=DAGS)  # puts DAGS on sys.path, as Airflow does
import factory  # noqa: E402 - importable once the bundle is on sys.path

TABLE = "bronze.synthetic_rates"
SPEC = json.loads((Path(__file__).with_name("synthetic_file_sources.json")).read_text())[TABLE]
DAG = factory.build(TABLE, SPEC)
KEY = "fx-reference/synthetic-rates/2026-01-05/" + "a" * 64 + ".xml"


def rendered(task_id):
    """The task's docker exec command, its templates rendered with fetch's XCom set to KEY."""
    task = copy.deepcopy(DAG.get_task(task_id))
    ti = SimpleNamespace(xcom_pull=lambda task_ids: KEY if task_ids == "fetch" else None)
    task.render_template_fields({"ti": ti, "params": {}})
    return task.command("/tmp/x.pid")


class FakeProc:
    def __init__(self, rc, lines):
        self.rc, self.stdout = rc, iter(lines)

    def wait(self):
        return self.rc

    def poll(self):
        return self.rc


def run(task_id, rc, lines=("a line\n",)):
    with mock.patch("subprocess.Popen", return_value=FakeProc(rc, list(lines))):
        return DAG.get_task(task_id).execute({"ti": SimpleNamespace(id="0192f0c4-test")})


class Production(unittest.TestCase):
    def test_no_file_source_is_declared_so_the_factory_builds_no_dag(self):
        self.assertEqual(json.loads((DAGS / "file_sources.json").read_text()), {})
        self.assertEqual(BAG.import_errors, {})
        self.assertEqual([d for d, dag in BAG.dags.items() if dag.fileloc.endswith("/factory.py")], [])


class Shape(unittest.TestCase):
    def test_four_tasks_in_a_line_fetch_load_audit_publish(self):
        self.assertEqual(DAG.dag_id, "synthetic_rates_ingest")
        self.assertEqual([t.task_id for t in DAG.topological_sort()], ["fetch", "load", "audit", "publish"])
        for upstream, task in (("fetch", "load"), ("load", "audit"), ("audit", "publish")):
            self.assertEqual(DAG.get_task(task).upstream_task_ids, {upstream})
            # publish runs only if every task before it succeeded: a failed audit stops it
            self.assertEqual(DAG.get_task(task).trigger_rule, "all_success")

    def test_the_schedule_owner_and_tags_come_from_the_contract(self):
        self.assertEqual(DAG.schedule, "0 6 * * *")
        self.assertEqual(type(DAG.timetable).__name__, "CronTriggerTimetable")
        self.assertEqual({t.owner for t in DAG.tasks}, {"fx-reference-team"})
        self.assertEqual(DAG.tags, {"lake", "file-source", "fx-reference"})

    def test_one_run_at_a_time_no_replay_and_a_bounded_run(self):
        self.assertFalse(DAG.catchup)
        self.assertEqual(DAG.max_active_runs, 1)
        self.assertFalse(DAG.is_paused_upon_creation)
        tries = sum(((t.retries + 1) * t.execution_timeout + t.retries * t.retry_delay for t in DAG.tasks), timedelta())
        self.assertGreater(DAG.dagrun_timeout, tries)
        self.assertLess(DAG.dagrun_timeout, tries + timedelta(minutes=10))

    def test_an_audit_is_never_retried(self):
        self.assertEqual({t.task_id: t.retries for t in DAG.tasks}, {"fetch": 2, "load": 1, "audit": 0, "publish": 1})


class Commands(unittest.TestCase):
    def test_fetch_runs_with_the_table_and_no_object(self):
        cmd = rendered("fetch")
        self.assertEqual(cmd[-4:], ["/home/iceberg/lake/file_source.py", "fetch", "--table", TABLE])
        self.assertFalse([a for a in cmd if "K2_LAKE_DRIVER_MEMORY" in a])

    def test_load_audit_and_publish_take_the_key_fetch_returned(self):
        for step in ("load", "audit", "publish"):
            with self.subTest(step):
                cmd = rendered(step)
                self.assertEqual(cmd[-6:], ["/home/iceberg/lake/file_source.py", step, "--table", TABLE, "--object", KEY])
                self.assertLess(cmd.index("--env=K2_LAKE_DRIVER_MEMORY=512m"), cmd.index("k2-spark-iceberg"))

    def test_fetch_returns_its_last_line_the_landed_key(self):
        self.assertEqual(run("fetch", 0, ["landed s3://k2-landing/" + KEY + ", 120 bytes\n", KEY + "\n", "\n"]), KEY)

    def test_the_other_steps_return_nothing(self):
        self.assertIsNone(run("load", 0, ["load: 3 rows\n"]))

    def test_a_failed_audit_fails_its_task_and_is_not_a_skip(self):
        for rc in (1, 75):
            with self.subTest(rc), self.assertRaises(AirflowException) as err:
                run("audit", rc)
            self.assertNotIsInstance(err.exception, AirflowSkipException)


if __name__ == "__main__":
    sys.exit(unittest.main())

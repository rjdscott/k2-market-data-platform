"""
docker/airflow/dags/dbt_marts.py, checked inside the Airflow image with no stack:

    make test-airflow

The repository is mounted read-only at /repo and dbt/ at /opt/k2/dbt, where the
DAG reads it, as docker-compose.yml mounts it; there is no network and no
metadata database, so cosmos's dbt ls cache is off (AIRFLOW__COSMOS__ENABLE_CACHE_DBT_LS)
and the DAG is rendered by a real `dbt ls` in the image's dbt virtualenv.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml
from airflow.dag_processing.dagbag import DagBag

REPO = Path(__file__).resolve().parents[3]
BAG = DagBag(dag_folder=str(REPO / "docker/airflow/dags"))
DAG = BAG.dags["dbt_marts"]
SELECTORS = [s["name"] for s in yaml.safe_load((REPO / "dbt/selectors.yml").read_text())["selectors"]]


def dbt_tasks(domain):
    """The tasks of a domain that run dbt. A source with tests and no freshness gets an
    EmptyOperator for its freshness task, which runs nothing (cosmos 1.16)."""
    mine = [t for t in DAG.tasks if t.task_id.startswith(f"{domain}.") or t.task_id == f"{domain}_expire_mart_snapshots"]
    return [t for t in mine if type(t).__module__.startswith("cosmos")]


class Structure(unittest.TestCase):
    def test_it_imports_hourly_at_minute_10_and_never_overlaps_or_replays(self):
        self.assertEqual(BAG.import_errors, {})
        self.assertEqual(DAG.schedule, "10 * * * *")
        self.assertEqual(type(DAG.timetable).__name__, "CronTriggerTimetable")
        self.assertFalse(DAG.catchup)
        self.assertEqual(DAG.max_active_runs, 1)
        self.assertFalse(DAG.is_paused_upon_creation)
        # A run, maintenance included, ends inside its hour.
        self.assertLess(DAG.dagrun_timeout, timedelta(hours=1))
        for t in DAG.tasks:
            with self.subTest(t.task_id):
                self.assertLessEqual(t.execution_timeout, DAG.dagrun_timeout)

    def test_it_leaves_two_of_the_four_executor_slots_to_the_lake(self):
        # AIRFLOW__CORE__PARALLELISM is 4 (docker-compose.yml) and lake_ingest runs every 5 minutes.
        self.assertEqual(DAG.max_active_tasks, 2)

    def test_one_task_group_per_selector(self):
        self.assertTrue(SELECTORS)
        groups = {t.task_group.group_id.split(".")[0] for t in DAG.tasks if t.task_group and t.task_group.group_id}
        self.assertEqual(groups, set(SELECTORS))

    def test_every_model_has_a_build_task_and_every_freshness_a_source_task(self):
        for domain in SELECTORS:
            ids = {t.task_id for t in dbt_tasks(domain)}
            for sql in (REPO / "dbt/models" / domain).rglob("*.sql"):
                self.assertIn(f"{domain}.{sql.stem}_model_build", ids)
        sources = yaml.safe_load((REPO / "dbt/models/_sources.yml").read_text())["sources"]
        fresh = [(s["name"], t["name"]) for s in sources for t in s["tables"] if "freshness" in t.get("config", {})]
        self.assertTrue(fresh)
        for ns, table in fresh:
            (src,) = [t for t in DAG.tasks if t.task_id.endswith(f".{ns}_{table}.source")]
            self.assertEqual((type(src).__name__, src.select), ("DbtSourceLocalOperator", f"source:{ns}.{table}"))


class Identity(unittest.TestCase):
    def test_every_dbt_task_runs_as_its_domains_writer_against_trino_in_compose(self):
        for domain in SELECTORS:
            for t in dbt_tasks(domain):
                with self.subTest(t.task_id):
                    self.assertEqual(t.env["K2_TRINO_USER"], f"dbt_{domain}")
                    self.assertEqual((t.env["K2_TRINO_HOST"], t.env["K2_TRINO_PORT"]), ("trino", "8080"))
                    self.assertTrue(t.append_env)

    def test_dbt_is_the_images_own_virtualenv_with_the_images_packages(self):
        for t in DAG.tasks:
            if type(t).__module__.startswith("cosmos"):
                with self.subTest(t.task_id):
                    self.assertEqual(t.dbt_executable_path, "/opt/k2/dbt-venv/bin/dbt")
                    self.assertEqual(t.env["K2_DBT_PACKAGES"], "/opt/k2/dbt_packages")
                    self.assertFalse(t.install_deps)

    def test_a_model_task_is_dbt_build_of_that_node_alone(self):
        t = DAG.get_task(f"{SELECTORS[0]}.ohlcv_1m_model_build")
        self.assertEqual(t.base_cmd, ["build"])
        self.assertEqual(t.select, "fqn:k2.market_analytics.marts.candles.ohlcv_1m")


class Maintenance(unittest.TestCase):
    def gate(self, domain=None):
        return DAG.get_task(f"{domain or SELECTORS[0]}_maintenance_hour")

    def opens(self, logical_date, run_after=None):
        run = SimpleNamespace(logical_date=logical_date, run_after=run_after or logical_date)
        return self.gate().python_callable(dag_run=run)

    def test_the_expiry_is_the_macro_run_as_the_writer_after_every_build(self):
        for domain in SELECTORS:
            maintain = DAG.get_task(f"{domain}_expire_mart_snapshots")
            self.assertEqual((type(maintain).__name__, maintain.macro_name), ("DbtRunOperationLocalOperator", "expire_mart_snapshots"))
            self.assertEqual(maintain.upstream_task_ids, {self.gate(domain).task_id})
            builds = {t.task_id for t in dbt_tasks(domain) if t.task_id.endswith("_model_build")}
            self.assertEqual(self.gate(domain).upstream_task_ids, {b for b in builds if not DAG.get_task(b).downstream_task_ids & builds})

    def test_it_runs_once_a_day_in_the_04_10_run(self):
        utc = timezone.utc
        self.assertTrue(self.opens(datetime(2026, 11, 25, 4, 10, tzinfo=utc)))
        for hour in (3, 5, 16):
            with self.subTest(hour):
                self.assertFalse(self.opens(datetime(2026, 11, 25, hour, 10, tzinfo=utc)))
        # In UTC, whatever zone the date arrives in.
        self.assertTrue(self.opens(datetime(2026, 11, 25, 15, 10, tzinfo=timezone(timedelta(hours=11)))))
        # A manual run with no logical date goes by its trigger time.
        self.assertTrue(self.opens(None, run_after=datetime(2026, 11, 25, 4, 40, tzinfo=utc)))
        self.assertFalse(self.opens(None, run_after=datetime(2026, 11, 25, 6, 0, tzinfo=utc)))


if __name__ == "__main__":
    unittest.main()

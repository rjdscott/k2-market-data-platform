"""
The DAG factory (docker/airflow/dags/factory.py) from k2gen's golden dags.json, with docker faked:
the DAG ids, tasks, edges, pools, trigger rules and run limits; resolve pinning the release while the
pointer moves; the fetch exit 10 and exit 30 paths; expire after a failed build; record after a
failure and a skip; each Spark task's exact argv and its kill sequence; no product import; and a
malformed entry as an import error.

    make test-airflow     # inside the Airflow image, no Docker daemon and no network
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from airflow.dag_processing.dagbag import BundleDagBag
from airflow.models.dagrun import DagRun
from airflow.sdk import TriggerRule
from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException
from airflow.ti_deps.deps.trigger_rule_dep import TriggerRuleDep
from airflow.utils.state import State, TaskInstanceState

REPO = Path(__file__).resolve().parents[3]
DAGS = REPO / "docker/airflow/dags"
SOURCE = DAGS / "factory.py"
GOLDEN = REPO / "platform/k2gen/tests/golden/pipeline"
CANARY = GOLDEN / "repository"
sys.path.insert(0, str(DAGS))
import factory  # noqa: E402
import oneshot  # noqa: E402

RID = "v3.0.0-demo-0123456789ab"
RP = f"/k2/releases/{RID}"
LAKE = f"{RP}/platform/docker/lake"
RUN = "scheduled__2026-11-27T02:15:00+00:00"
COMMON = ["--release", RID, "--product", "sensor-net.station-readings", "--run-id", f"station_readings/{RUN}"]
HOST = {"K2_RELEASES": "/home/owner/k2-releases", "K2_SECRETS": "/home/owner/k2-secrets",
        "K2_COMPOSE_PROJECT": "k2proj", "K2_TRINO_HOST": "trino", "K2_TRINO_INTERNAL_PORT": "8443",
        "K2_RUNS_USER": "svc.runs", "K2_CATALOG_URI": "http://lakekeeper:8181/catalog",
        "K2_RUN_UID": "1000", "K2_RUN_GID": "1001"}
FULL = ["resolve", "fetch", "load", "audit", "publish", "build", "expire", "record", "maintain"]
DBT_ONLY = ["resolve", "build", "expire", "record", "maintain"]
TIState = namedtuple("TIState", "task_id state")


def golden_json(path):
    """factory.read_json with /k2/releases/<RID> served from the golden release."""
    return json.loads(Path(str(path).replace(RP, str(GOLDEN))).read_text())


class TI:
    """The task instance a task sees: XCom by (task, key), breadcrumbs, and pushes recorded."""

    def __init__(self, xcom=None, crumbs=(), dag_id="station_readings", tid="0193-try"):
        self.dag_id, self.run_id, self.id, self.try_number = dag_id, RUN, tid, 1
        self.xcom = {("resolve", "return_value"): {"release": RID, "releasePath": RP}, **(xcom or {})}
        self.crumbs, self.pushed = list(crumbs), {}

    def xcom_pull(self, task_ids, key="return_value"):
        return self.xcom.get((task_ids, key))

    def xcom_push(self, key, value):
        self.pushed[key] = value

    def get_task_breadcrumbs(self, dag_id, run_id):
        assert (dag_id, run_id) == (self.dag_id, RUN)
        return self.crumbs


def ctx(ti, when=datetime(2026, 11, 27, 2, 15, tzinfo=timezone.utc)):
    return {"ti": ti, "dag_run": SimpleNamespace(run_after=when)}


class Docker:
    """subprocess faked for both modules: every command line in order, one exit and stdout."""

    def __init__(self, lines=(), code=0):
        self.lines, self.code, self.calls = lines, code, []

    def run(self, argv, **_):
        self.calls.append(argv)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    def popen(self, argv, **_):
        self.calls.append(argv)
        return SimpleNamespace(stdout=iter(f"{x}\n" for x in self.lines), wait=lambda: self.code, poll=lambda: None,
                               kill=lambda: self.calls.append(["<client killed>"]))

    def __enter__(self):
        self.patches = [mock.patch.object(m.subprocess, name, side_effect=getattr(self, attr))
                        for m in (factory, oneshot) for name, attr in (("run", "run"), ("Popen", "popen"))]
        self.patches += [mock.patch.object(factory, "read_json", side_effect=golden_json),
                         mock.patch.object(oneshot, "load_oneshots", return_value=oneshot.load_oneshots(str(GOLDEN))),
                         mock.patch.dict(os.environ, HOST)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def task(dag_id, task_id, release=GOLDEN):
    return factory.build(release)[dag_id].get_task(task_id)


def spark(*argv):
    return ["docker", "exec", "k2-spark-iceberg", "sh", "-c", factory.WRAPPER, "/tmp/k2-airflow-0193-try.pid", *argv]


class Shape(unittest.TestCase):
    def test_one_dag_per_entry_with_the_entry_tasks_schedule_and_limits(self):
        dags = factory.build(GOLDEN)
        self.assertEqual(sorted(dags), ["daily_load", "station_readings"])
        self.assertEqual(set(dags["station_readings"].task_dict), set(FULL))
        self.assertEqual(set(dags["daily_load"].task_dict), set(DBT_ONLY))
        self.assertEqual(sorted(factory.build(CANARY)), ["access_canary", "calendars", "currency_master", "ecb_fx_rates", "quality_results"])
        for d in dags.values():
            self.assertEqual((d.max_active_runs, d.catchup, d.is_paused_upon_creation), (1, False, False))
        self.assertEqual((dags["station_readings"].schedule, dags["daily_load"].schedule), ("15 * * * *", "30 1 * * *"))
        self.assertEqual(dags["station_readings"].dagrun_timeout.total_seconds(), 600)  # its timeoutMinutes
        self.assertIsNone(dags["daily_load"].dagrun_timeout)

    def test_edges_pools_and_trigger_rules(self):
        d = factory.build(GOLDEN)["station_readings"]
        edges = {(t.task_id, down) for t in d.tasks for down in t.downstream_task_ids}
        self.assertEqual(edges, {("resolve", "fetch"), ("fetch", "load"), ("load", "audit"), ("audit", "publish"),
                                 ("publish", "build"), ("build", "expire"), ("expire", "record"),
                                 ("expire", "maintain"), ("maintain", "record")})
        self.assertEqual({t.task_id: t.pool for t in d.tasks},
                         {"resolve": "default_pool", "fetch": "fetch", "load": "lake_spark", "audit": "lake_spark",
                          "publish": "lake_spark", "build": "dbt", "expire": "lake_spark", "record": "default_pool",
                          "maintain": "lake_spark"})
        rules = {t.task_id: t.trigger_rule for t in d.tasks}
        self.assertEqual({t for t, r in rules.items() if r != TriggerRule.ALL_SUCCESS}, {"build", "expire", "record", "maintain"})
        self.assertEqual(rules["build"], TriggerRule.NONE_FAILED)
        self.assertEqual(rules["maintain"], TriggerRule.ALL_DONE)
        self.assertEqual({rules["expire"], rules["record"]}, {TriggerRule.ALL_DONE_SETUP_SUCCESS})
        self.assertEqual({t.task_id for t in d.tasks if t.is_teardown}, {"expire", "record"})
        self.assertFalse(any(t.is_setup for t in d.tasks))  # no setup: the teardowns behave as all_done
        self.assertEqual(d.get_task("build").retries, 0)
        dbt_only = factory.build(GOLDEN)["daily_load"]
        self.assertEqual({(t.task_id, down) for t in dbt_only.tasks for down in t.downstream_task_ids},
                         {("resolve", "build"), ("build", "expire"), ("expire", "record"),
                          ("expire", "maintain"), ("maintain", "record")})

    def test_a_failed_build_or_audit_fails_the_run_and_record_does_not_decide_it(self):
        """Airflow's own rule for a run's state: the leaves, teardowns left out."""
        for dag in factory.build(GOLDEN).values():
            tis = [TIState(t, "success") for t in dag.task_dict]
            leaves = {ti.task_id for ti in DagRun._tis_for_dagrun_state(None, dag=dag, tis=tis)}
            self.assertEqual(leaves, {"build", "maintain"}, dag.dag_id)  # audit failed: build upstream_failed

    def test_a_malformed_entry_raises(self):
        good = json.loads((GOLDEN / "compiled/airflow/dags.json").read_text())["dags"][1]
        for change in ({"tasks": ["fetch", "resolve", "record"]}, {"tasks": ["resolve", "fetch", "teleport", "record"]},
                       {"tasks": ["resolve", "fetch"]}, {"tasks": ["resolve", "expire", "record"]},
                       {"dagId": "Bad-Id"}, {"schedule": "hourly"}, {"pipeline": "compiled/products/x.y/pipeline.json"},
                       {"status": "on"}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                factory.check_entry(0, {**good, **change})
        bad = {k: v for k, v in good.items() if k != "schedule"}
        with self.assertRaises(ValueError):
            factory.check_entry(0, bad)


S, SK, F, UF = (TaskInstanceState.SUCCESS, TaskInstanceState.SKIPPED, TaskInstanceState.FAILED,
                TaskInstanceState.UPSTREAM_FAILED)


def schedule(dag, outcomes: dict) -> tuple[dict, str]:
    """One run as the scheduler decides it, with Airflow's own TriggerRuleDep and run-state rule: a
    task whose upstreams are done gets the state its rule sets (skipped, upstream_failed) or, when
    the rule passes, its outcome (success unless `outcomes` says otherwise)."""
    states: dict = {}
    while len(states) < len(dag.tasks):
        for t in dag.tasks:
            if t.task_id in states or not t.upstream_task_ids <= set(states):
                continue
            finished = [SimpleNamespace(task_id=u, state=states[u], map_index=-1, task=dag.get_task(u))
                        for u in t.upstream_task_ids]
            dep = SimpleNamespace(ensure_finished_tis=lambda dr, session, f=finished: f, flag_upstream_failed=True,
                                  wait_for_past_depends_before_skipping=False, have_changed_ti_states=False,
                                  upstream_task_id_counts={})
            ti = SimpleNamespace(task=t, task_id=t.task_id, dag_id=dag.dag_id, run_id=RUN, map_index=-1,
                                 get_dagrun=lambda session: None,
                                 set_state=lambda state, session, tid=t.task_id: states.__setitem__(tid, state) or True)
            failing = [d for d in TriggerRuleDep()._get_dep_statuses(ti, dep, session=None) if not d.passed]
            if t.task_id not in states:
                if failing:
                    raise AssertionError(f"{t.task_id} would wait forever: {failing[0].reason}")
                states[t.task_id] = outcomes.get(t.task_id, S)
    tis = [TIState(t, s) for t, s in states.items()]
    leaves = DagRun._tis_for_dagrun_state(None, dag=dag, tis=tis)
    return states, "failed" if any(ti.state in State.failed_states for ti in leaves) else "success"


class Scheduling(unittest.TestCase):
    """Whole runs through Airflow's trigger rules: what runs, and the run's final state."""

    def test_an_unchanged_file_still_builds_and_the_run_succeeds(self):
        dag = factory.build(GOLDEN)["station_readings"]
        for maintain in (S, SK):  # maintenance due or not
            states, run = schedule(dag, {"fetch": SK, "maintain": maintain})
            self.assertEqual({t: states[t] for t in ("load", "audit", "publish")}, dict.fromkeys(("load", "audit", "publish"), SK))
            self.assertEqual((states["build"], states["expire"], states["record"], run), (S, S, S, "success"))

    def test_a_failed_audit_or_a_refused_load_does_not_build_and_fails_the_run(self):
        dag = factory.build(GOLDEN)["station_readings"]
        for failed in ("audit", "load", "fetch"):
            with self.subTest(failed=failed):
                states, run = schedule(dag, {failed: F})
                self.assertEqual((states["build"], states["expire"], states["record"], run), (UF, S, S, "failed"))

    def test_a_failed_build_still_expires_and_records_and_fails_the_run(self):
        for dag in factory.build(GOLDEN).values():
            states, run = schedule(dag, {"build": F})
            self.assertEqual((states["expire"], states["record"], run), (S, S, "failed"), dag.dag_id)

    def test_maintain_runs_after_the_data_path_whatever_build_did_and_a_failed_one_fails_the_run(self):
        # review F9: maintain beside build could expire the snapshot_before a rollback needs
        for dag in factory.build(GOLDEN).values():
            self.assertIn("build", dag.get_task("maintain").get_flat_relative_ids(upstream=True), dag.dag_id)
            cases = [({}, "success"), ({"build": F}, "failed")] + ([({"audit": F}, "failed")] if "audit" in dag.task_dict else [])
            for outcomes, want in cases:  # audit failed: build upstream_failed, and maintain still runs
                states, run = schedule(dag, outcomes)
                self.assertEqual((states["maintain"], run), (S, want), (dag.dag_id, outcomes))
            states, run = schedule(dag, {"maintain": F})
            self.assertEqual((states["build"], states["record"], run), (S, S, "failed"), dag.dag_id)

    def test_a_clean_run_and_a_dbt_only_run_succeed(self):
        for dag in factory.build(GOLDEN).values():
            states, run = schedule(dag, {})
            self.assertEqual((set(states.values()), run), ({S}, "success"), dag.dag_id)


class Parse(unittest.TestCase):
    """The module as Airflow parses it: from a release tree, the bundle folder on sys.path."""

    def release(self, dags_json: str) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        rel = root / RID
        shutil.copytree(GOLDEN / "compiled", rel / "compiled")
        (rel / "compiled/airflow/dags.json").write_text(dags_json)
        (rel / "manifest.json").write_text("{}")
        dags = rel / "platform/docker/airflow/dags"
        dags.mkdir(parents=True)
        for f in ("factory.py", "oneshot.py"):
            shutil.copy(DAGS / f, dags / f)
        (root / "pointers").mkdir()
        (root / "pointers/airflow").symlink_to(f"../{RID}")
        return root / "pointers/airflow/platform/docker/airflow/dags"  # Airflow's DAG folder

    def test_through_the_pointer_it_builds_its_own_release_dags(self):
        folder = self.release((GOLDEN / "compiled/airflow/dags.json").read_text())
        bag = BundleDagBag(dag_folder=str(folder), bundle_path=folder)
        self.assertEqual(bag.import_errors, {})
        self.assertEqual(sorted(bag.dag_ids), ["daily_load", "station_readings"])

    def test_a_malformed_entry_is_an_import_error_not_a_missing_dag(self):
        doc = json.loads((GOLDEN / "compiled/airflow/dags.json").read_text())
        doc["dags"][1]["tasks"] = ["resolve", "teleport", "record"]
        folder = self.release(json.dumps(doc))
        bag = BundleDagBag(dag_folder=str(folder), bundle_path=folder)
        self.assertEqual(bag.dag_ids, [])
        self.assertIn("dags.json entry 1 ('station_readings'): bad tasks", " ".join(map(str, bag.import_errors.values())))

    def test_a_checkout_builds_nothing(self):
        self.assertFalse((Path(factory.__file__).resolve().parents[4] / "manifest.json").exists())
        self.assertFalse([v for v in vars(factory).values() if type(v).__name__ == "DAG"])


class Resolve(unittest.TestCase):
    def test_resolve_reads_the_pointer_at_run_time_and_a_moved_pointer_does_not_move_the_run(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        r1, r2 = "v3.0.0-a-111111111111", "v3.0.0-b-222222222222"
        for r in (r1, r2):
            (root / r).mkdir()
        (root / "pointers").mkdir()
        (root / "pointers/airflow").symlink_to(f"../{r1}")
        with mock.patch.multiple(factory, RELEASES_MOUNT=str(root), POINTER=str(root / "pointers/airflow")):
            pin = factory.Resolve(task_id="resolve").execute({})
            self.assertEqual(pin, {"release": r1, "releasePath": f"{root}/{r1}"})
            (root / "pointers/next").symlink_to(f"../{r2}")
            os.replace(root / "pointers/next", root / "pointers/airflow")  # a deploy, mid-run
            self.assertEqual(factory.Resolve(task_id="resolve").execute({})["release"], r2)  # the next run's
            ti = TI({("resolve", "return_value"): pin})
            with Docker() as docker:
                task("station_readings", "load").execute(ctx(ti))
            self.assertEqual(docker.calls[0][7], f"{root}/{r1}/platform/docker/lake/connector.py")
            self.assertEqual(docker.calls[0][9:11], ["--release", r1])

    def test_a_pointer_outside_the_releases_root_is_refused(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "pointers").mkdir()
        (root / "pointers/airflow").symlink_to("/etc")
        with mock.patch.multiple(factory, RELEASES_MOUNT=str(root), POINTER=str(root / "pointers/airflow")), \
                self.assertRaises(AirflowFailException):
            factory.Resolve(task_id="resolve").execute({})

    def test_a_task_without_a_pinned_release_runs_nothing(self):
        ti = TI({("resolve", "return_value"): None})
        with Docker() as docker, self.assertRaises(AirflowFailException):
            task("station_readings", "load").execute(ctx(ti))
        self.assertEqual(docker.calls, [])


class Spark(unittest.TestCase):
    def test_the_exact_argv_of_each_spark_task(self):
        result = {("build", "result"): {"exit": 1, "expire": [{"table": "t", "ids": [7, 8]}]}}
        cases = {"load": [f"{LAKE}/connector.py", "load", *COMMON], "audit": [f"{LAKE}/connector.py", "audit", *COMMON],
                 "publish": [f"{LAKE}/connector.py", "publish", *COMMON],
                 "expire": [f"{LAKE}/expire.py", *COMMON, "--expire", '[{"table":"t","ids":[7,8]}]'],
                 "maintain": [f"{LAKE}/maintain.py", *COMMON]}
        for t, argv in cases.items():
            with self.subTest(task=t), Docker(lines=['{"exit": 0}']) as docker:
                ti = TI(result)
                task("station_readings", t).execute(ctx(ti))
                self.assertEqual(docker.calls, [spark(*argv)])
                self.assertEqual(ti.pushed, {"result": {"exit": 0}})

    def test_kill_stops_the_driver_in_the_container_then_the_client(self):
        op = task("station_readings", "load")
        with Docker(lines=["{}"]) as docker:
            op.execute(ctx(TI()))
            op.on_kill()
        self.assertEqual(docker.calls, [spark(f"{LAKE}/connector.py", "load", *COMMON),
                                        ["docker", "exec", "k2-spark-iceberg", "sh", "-c", factory.KILL,
                                         "/tmp/k2-airflow-0193-try.pid"],
                                        ["<client killed>"]])

    def test_refused_and_audit_failed_do_not_retry_an_error_may(self):
        for code, exc in ((2, AirflowFailException), (3, AirflowFailException), (1, AirflowException)):
            with self.subTest(code=code), Docker(lines=[f'{{"exit": {code}}}'], code=code), self.assertRaises(exc) as e:
                task("station_readings", "audit").execute(ctx(TI()))
            self.assertEqual(type(e.exception), exc)

    def test_maintain_runs_only_in_the_run_its_daily_cron_falls_in(self):
        # station_readings: schedule 15 * * * *, maintain 7 2 * * *: the 02:15 run, not 03:15's
        with Docker(lines=["{}"]) as docker, self.assertRaises(AirflowSkipException):
            task("station_readings", "maintain").execute(ctx(TI(), datetime(2026, 11, 27, 3, 15, tzinfo=timezone.utc)))
        self.assertEqual(docker.calls, [])
        with Docker(lines=["{}"]) as docker:
            task("station_readings", "maintain").execute(ctx(TI()))
        self.assertEqual(len(docker.calls), 1)


class Paths(unittest.TestCase):
    """The run's paths through fetch, the Spark tasks, build, expire and record."""

    def test_fetch_and_build_commands_from_the_pinned_release(self):
        with Docker() as docker:
            task("station_readings", "fetch").execute(ctx(TI()))
        self.assertEqual(docker.calls[-1][-9:], ["k2-fetch", *COMMON, "--ds", "2026-11-27"])
        result = '{"exit": 0, "expire": []}'
        with Docker(lines=[result]) as docker:
            ti = TI()
            task("station_readings", "build").execute(ctx(ti))
        argv = docker.calls[-1]
        self.assertEqual(argv[-8:], ["k2-dbt", "build", *COMMON])
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-e"],
                         oneshot.load_oneshots(str(GOLDEN))["dbt-runner"]["env"])
        self.assertIn("type=bind,source=/home/owner/k2-secrets/principals/dbt_station_readings.password,"
                      "target=/run/k2-secrets/builder.password,readonly", argv)
        self.assertIn("type=bind,source=/home/owner/k2-secrets/principals/pub_station_readings.password,"
                      "target=/run/k2-secrets/publisher.password,readonly", argv)
        self.assertEqual(ti.pushed, {"result": {"exit": 0, "expire": []}})

    def test_fetch_10_skips_and_the_data_path_after_it_follows_the_skip(self):
        with Docker(code=10), self.assertRaises(AirflowSkipException):
            task("station_readings", "fetch").execute(ctx(TI()))
        d = factory.build(GOLDEN)["station_readings"]
        for t in ("load", "audit", "publish"):  # all_success: a skipped upstream skips them; build runs (Scheduling)
            self.assertEqual(d.get_task(t).trigger_rule, TriggerRule.ALL_SUCCESS)
        with Docker() as docker, self.assertRaises(AirflowSkipException):  # expire: build left no ids
            task("station_readings", "expire").execute(ctx(TI()))
        self.assertEqual(docker.calls, [])
        crumbs = [{"task_id": "fetch", "state": "skipped"}, *({"task_id": t, "state": "skipped"}
                                                              for t in ("load", "audit", "publish", "build", "expire", "maintain"))]
        self.assertEqual([(r["task"], r["event"], r["detail"]) for r in factory.rows(TI(crumbs=crumbs), "p.q", RID)],
                         [("fetch", "passed", '{"exit":10,"unchanged":true}')])

    def test_fetch_30_succeeds_load_lands_audit_fails_and_stops(self):
        with Docker(code=30):
            task("station_readings", "fetch").execute(ctx(TI()))  # the bytes are kept and land
        with Docker(lines=['{"exit": 0, "task": "load", "rows": 0}']):
            task("station_readings", "load").execute(ctx(TI()))
        ti = TI()
        failed = '{"exit": 3, "task": "audit", "failed": ["parses"]}'
        with Docker(lines=[failed], code=3), self.assertRaises(AirflowFailException):
            task("station_readings", "audit").execute(ctx(ti))
        self.assertEqual(ti.pushed["result"]["failed"], ["parses"])  # pushed on the failure too, for record
        self.assertEqual(task("station_readings", "publish").trigger_rule, TriggerRule.ALL_SUCCESS)

    def test_expire_after_a_failed_build_gets_its_ids(self):
        ids = [{"table": "sensor_net_build.station_readings__base.silver_readings", "ids": [41, 42]}]
        with Docker(lines=[json.dumps({"exit": 1, "expire": ids})], code=1), self.assertRaises(AirflowException):
            build_ti = TI()
            task("station_readings", "build").execute(ctx(build_ti))
        self.assertEqual(build_ti.pushed["result"]["expire"], ids)  # oneshot pushes it on failure
        with Docker(lines=['{"exit": 0, "task": "expire", "expired": []}']) as docker:
            task("station_readings", "expire").execute(ctx(TI({("build", "result"): build_ti.pushed["result"]})))
        self.assertEqual(docker.calls[0][-2:], ["--expire", json.dumps(ids, separators=(",", ":"))])


class Record(unittest.TestCase):
    CRUMBS = [{"task_id": "resolve", "state": "success"}, {"task_id": "fetch", "state": "success", "duration": 2.5},
              {"task_id": "load", "state": "success"}, {"task_id": "audit", "state": "failed"},
              {"task_id": "publish", "state": "upstream_failed"}, {"task_id": "build", "state": "upstream_failed"},
              {"task_id": "expire", "state": "skipped"}, {"task_id": "maintain", "state": "skipped"}]
    XCOM = {("load", "result"): {"exit": 0, "rows": 12, "snapshotAfter": 99},
            ("audit", "result"): {"exit": 3, "failed": ["parses"]}}

    def record(self, k2runs):
        with mock.patch.object(factory, "load_k2runs", return_value=k2runs) as load:
            factory.build(GOLDEN)["station_readings"].get_task("record").execute(ctx(TI(self.XCOM, self.CRUMBS)))
        load.assert_called_once_with(RP)

    def test_after_a_failure_one_row_per_task_that_ran(self):
        written = []
        k2runs = SimpleNamespace(connect=lambda: "conn", record=lambda conn, **row: written.append(row))
        self.record(k2runs)
        self.assertEqual([(r["task"], r["event"]) for r in written], [("fetch", "passed"), ("load", "passed"), ("audit", "failed")])
        load = written[1]
        self.assertEqual((load["run_id"], load["product_id"], load["release_id"], load["rows_written"], load["snapshot_after"]),
                         (f"station_readings/{RUN}", "sensor-net.station-readings", RID, 12, 99))
        self.assertEqual(written[0]["duration_s"], 2.5)
        self.assertEqual(json.loads(written[2]["detail"]), {"exit": 3, "failed": ["parses"]})

    def test_it_fails_loudly_only_when_it_cannot_write(self):
        def refuse():
            raise ConnectionError("trino unreachable")
        with self.assertRaises(AirflowException) as e:
            self.record(SimpleNamespace(connect=refuse, record=None))
        self.assertIn("run record not written", str(e.exception))

    def test_the_real_k2runs_validates_every_row_the_factory_makes(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "platform").symlink_to(REPO)  # <release>/platform/<repository path>
        k2runs = factory.load_k2runs(str(root))
        self.assertEqual(Path(k2runs.__file__).resolve(), REPO / "platform/runs/k2runs.py")
        cur = mock.MagicMock()
        for row in factory.rows(TI(self.XCOM, self.CRUMBS), "sensor-net.station-readings", RID):
            k2runs.record(SimpleNamespace(cursor=lambda: cur), **row)
        self.assertEqual(cur.execute.call_count, 3)

    def test_no_pinned_release_skips(self):
        with self.assertRaises(AirflowSkipException):
            task("station_readings", "record").execute(ctx(TI({("resolve", "return_value"): None})))


class NoProductCode(unittest.TestCase):
    ALLOWED = {"__future__", "importlib", "json", "os", "re", "subprocess", "datetime", "pathlib", "airflow",
               "croniter", "oneshot"}

    def test_it_imports_only_stdlib_airflow_and_oneshot_and_loads_only_k2runs(self):
        tree = ast.parse(SOURCE.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "no relative import")
                imported.append(node.module)
            elif isinstance(node, ast.Name):
                self.assertNotIn(node.id, {"exec", "eval", "compile", "__import__", "runpy"})
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"import_module", "load_module", "__import__"})
        self.assertEqual({m.split(".")[0] for m in imported} - self.ALLOWED, set())
        loads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "spec_from_file_location"]
        self.assertEqual(len(loads), 1, "one dynamic load, in load_k2runs")
        self.assertEqual(factory.K2RUNS, "platform/platform/runs/k2runs.py")
        self.assertNotIn("domains", SOURCE.read_text().replace("never anything under `domains/`", ""))


if __name__ == "__main__":
    unittest.main()

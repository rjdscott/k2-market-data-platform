"""scripts/rebuild.py: make rebuild, with the holds client, Airflow and the containers faked; the two
snippets it runs in the stack's containers run here against stand-ins (the release's own oneshot.py
with Airflow's names stubbed, and a stand-in for docker and for the Spark session builder)."""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "platform" / "runs")]
import k2runs  # noqa: E402
import rebuild  # noqa: E402

GOLDEN = ROOT / "platform" / "k2gen" / "tests" / "golden"
PRODUCTS = GOLDEN / "pipeline" / "repository" / "compiled" / "products"
KAFKA, DERIVED = "market-capture.kraken-trades", "market-analytics.canonical-trades"
DAG = "kraken_trades"
RELEASE = "v3.0.0-alpha.6-rc.1-0123456789ab"
NOW = datetime(2026, 11, 28, 10, 15, tzinfo=timezone.utc)
RUN = "kraken_trades/rebuild__2026-11-28T10:15:00+00:00"
BRONZE_DDL = "compiled/lake/ddl/spark/k2.kraken_trades.bronze_trade.sql"


def release(tmp_path, *products, pipeline=None):
    """A release directory holding the golden pipelines (or `pipeline` for the first) and bronze's DDL."""
    rel = tmp_path / "rels" / RELEASE
    for i, p in enumerate(products):
        (rel / "compiled" / "products" / p).mkdir(parents=True)
        doc = pipeline if (pipeline is not None and i == 0) else json.loads((PRODUCTS / p / "pipeline.json").read_text())
        (rel / "compiled" / "products" / p / "pipeline.json").write_text(json.dumps(doc))
    (rel / BRONZE_DDL).parent.mkdir(parents=True)
    shutil.copy(GOLDEN / "lake" / "repository" / BRONZE_DDL, rel / BRONZE_DDL)
    return rel


class Holds:
    """Stands in for k2runs' hold calls: `open` the open (kind, held_by) pairs, oldest first; `reasons`
    a reason by kind; `others` the open holds of other products, as open_holds returns them."""
    def __init__(self, mp, log, open_=(), reasons=None, others=None, product=KAFKA):
        self.open, self.log = list(open_), log
        self.release_error = None
        self.reasons, self.others = dict(reasons or {}), dict(others or {})
        mp.setattr(rebuild, "open_holds", lambda c, p: self.others.get(p, []) if p != product else
                   [{"kind": k, "held_by": b, "held_at": "t", "reason": self.reasons.get(k, "")} for k, b in self.open])
        mp.setattr(k2runs, "held", lambda c, p: {"kind": self.open[-1][0], "held_by": self.open[-1][1], "held_at": "t"}
                   if self.open else None)
        mp.setattr(k2runs, "hold", self.hold)
        mp.setattr(k2runs, "release", self.release)

    def hold(self, conn, product, reason, by, kind="operator"):
        if kind in [k for k, _ in self.open]:
            raise k2runs.HoldError("held already")
        self.open.append((kind, by))
        self.log.append(("hold", kind, by, reason))

    def release(self, conn, product, by, kind="operator"):
        if self.release_error:
            raise self.release_error
        if kind not in [k for k, _ in self.open]:
            raise k2runs.HoldError("none")
        self.open = [h for h in self.open if h[0] != kind]
        self.log.append(("release", kind, by))


class IO:
    """Airflow and the containers: each call logged, each result set by the test."""
    def __init__(self, log, paused=False, busy=((),), codes=None, rows=12):
        self.log, self.was_paused, self.busy_answers = log, paused, [list(b) for b in busy]
        self.codes = {"load": 0, "audit": 0, "publish": 0, "build": 0, **(codes or {})}
        self.rows, self.t, self.fail = rows, 0.0, {}
        self.alive = False

    def _maybe(self, step):
        if step in self.fail:
            raise self.fail[step]

    def paused(self, dag):
        return self.was_paused

    def pause(self, dag):
        self.log.append(("pause", dag))

    def unpause(self, dag):
        self._maybe("unpause")
        self.log.append(("unpause", dag))

    def busy(self, dag):
        return self.busy_answers.pop(0) if len(self.busy_answers) > 1 else self.busy_answers[0]

    def replace(self, inside, statements):
        self._maybe("replace")
        self.log.append(("replace", inside, statements))

    def spark_task(self, inside, task, *args):
        self.log.append((task, inside, args))
        return self.codes[task], ({"exit": self.codes[task], "rows": self.rows} if task == "load" else {"exit": self.codes[task]})

    def build(self, inside, dag, product, run):
        self._maybe("build")
        self.log.append(("build", inside, dag, product, run))
        return self.codes["build"], {"exit": self.codes["build"], "expire": []}

    def running(self, name):
        return self.alive

    def sleep(self, s):
        self.t += s
        self.log.append(("sleep", s))

    def clock(self):
        return self.t


def go(tmp_path, monkeypatch, product=KAFKA, open_=(), pipeline=None, reasons=None, others=None, also=(), **io):
    log = []
    holds = Holds(monkeypatch, log, open_, reasons, others, product)
    fake = IO(log, **io)
    rel = release(tmp_path, product, *also, pipeline=pipeline)
    dag = json.loads((rel / "compiled" / "products" / product / "pipeline.json").read_text())["dagId"]
    return (lambda: rebuild.rebuild(None, rel, product, dag, "rob", fake, NOW)), log, holds, fake


def steps(log):
    return [e[0] for e in log if e[0] != "sleep"]


def test_a_kafka_product_is_locked_drained_restaged_built_and_released_in_that_order(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, busy=(["load (run scheduled__x, running)"], []))
    assert run() == 0
    assert steps(log) == ["hold", "pause", "replace", "load", "audit", "publish", "build", "release", "unpause"]
    assert log[0] == ("hold", "rebuild", "make rebuild", "make rebuild by rob") and ("sleep", rebuild.POLL_S) in log
    inside = f"/k2/releases/{RELEASE}"
    for task in ("load", "audit", "publish"):
        assert next(e for e in log if e[0] == task) == (task, inside, ("--release", RELEASE, "--product", KAFKA, "--run-id", RUN))
    assert next(e for e in log if e[0] == "build") == ("build", inside, DAG, KAFKA, RUN)
    assert log[-2] == ("release", "rebuild", "rob") and holds.open == []


def test_a_product_with_no_kafka_connector_is_built_only(tmp_path, monkeypatch):
    run, log, _, _ = go(tmp_path, monkeypatch, product=DERIVED)
    assert run() == 0
    assert steps(log) == ["hold", "pause", "build", "release", "unpause"]


@pytest.mark.parametrize("change", [{"status": "paused"}, {"status": "retired"}, {"build": None}])
def test_a_product_with_nothing_to_rebuild_is_refused_before_the_lock(tmp_path, monkeypatch, change):
    doc = {**json.loads((PRODUCTS / KAFKA / "pipeline.json").read_text()), **change}
    run, log, holds, _ = go(tmp_path, monkeypatch, pipeline=doc)
    with pytest.raises(rebuild.Refused):
        run()
    assert log == [] and holds.open == []


def test_a_bronze_ddl_of_another_shape_is_refused_before_the_lock(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch)
    ddl = tmp_path / "rels" / RELEASE / BRONZE_DDL
    ddl.write_text(ddl.read_text() + "DROP TABLE \"lake\".\"kraken_trades\".\"landing\";\n")
    with pytest.raises(rebuild.Refused):
        run()
    assert log == [] and holds.open == []


def test_an_open_rebuild_lock_is_refused_and_left_open(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, open_=[("rebuild", "make rebuild")])
    with pytest.raises(rebuild.Refused, match="UNLOCK=1"):
        run()
    assert log == [] and holds.open == [("rebuild", "make rebuild")]


def test_a_product_a_restore_holds_is_refused_before_the_lock(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, open_=[("restore", "make restore")])
    with pytest.raises(rebuild.Refused, match="restore"):
        run()
    assert log == [] and holds.open == [("restore", "make restore")]


def left(recreated=(), unpause=True):
    return "make restore of backup run 20261128T020000Z left it for rebuild" + rebuild.AWAITING + json.dumps(
        {"run": "20261128T020000Z", "recreated": list(recreated), "unpause": unpause})


def test_a_product_the_restore_left_is_rebuilt_over_its_hold_which_ends_only_after_it_passes(tmp_path, monkeypatch, capsys):
    run, log, holds, fake = go(tmp_path, monkeypatch, open_=[("restore", "make restore")], paused=True,
                               reasons={"restore": left(["k2.kraken_trades.bronze_trade"])})
    assert run() == 0
    assert steps(log) == ["hold", "replace", "load", "audit", "publish", "build", "release", "release", "unpause"]
    assert [e[1] for e in log if e[0] == "release"] == ["rebuild", "restore"] and holds.open == []
    assert "DAG kraken_trades unpaused, as the restore found it" in capsys.readouterr().out


def test_a_failed_rebuild_keeps_the_restore_hold(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, open_=[("restore", "make restore")], paused=True,
                            reasons={"restore": left()}, codes={"build": 1})
    assert run() == 1 and holds.open == [("restore", "make restore")] and "unpause" not in steps(log)


def test_a_dag_paused_before_the_restore_stays_paused_after_its_rebuild(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, open_=[("restore", "make restore")], paused=True,
                            reasons={"restore": left(unpause=False)})
    assert run() == 0 and holds.open == [] and "unpause" not in steps(log)


def test_a_downstream_product_waits_for_its_upstream_rebuild(tmp_path, monkeypatch):
    # canonical-trades reads kraken-trades' silver_trade; the restore left both
    up = [{"kind": "restore", "held_by": "make restore", "held_at": "t", "reason": left()}]
    run, log, holds, _ = go(tmp_path, monkeypatch, product=DERIVED, also=(KAFKA,), open_=[("restore", "make restore")],
                            reasons={"restore": left()}, others={KAFKA: up})
    with pytest.raises(rebuild.Refused, match=f"make rebuild PRODUCT={KAFKA} first"):
        run()
    assert log == [] and holds.open == [("restore", "make restore")]


def test_a_file_product_whose_bronze_the_restore_recreated_is_refused(tmp_path, monkeypatch):
    doc = json.loads((PRODUCTS / KAFKA / "pipeline.json").read_text())
    doc["connector"] = {"kind": "https-file"}
    run, log, holds, _ = go(tmp_path, monkeypatch, open_=[("restore", "make restore")], pipeline=doc,
                            reasons={"restore": left(["k2.kraken_trades.bronze_trade"])})
    with pytest.raises(rebuild.Refused, match="a file product's bronze is its parser's"):
        run()
    assert log == []


def test_open_holds_reads_every_open_hold_through_the_reader_catalog():
    seen = []

    class Cur:
        def execute(self, sql, params):
            seen.append((sql, params))

        def fetchall(self):
            return [("p", "restore", "r", "make restore", "t", None, None)]

    class Conn:
        def cursor(self):
            return Cur()

    [h] = rebuild.open_holds(Conn(), "p")
    assert h["kind"] == "restore" and seen[0][1] == ["p"]
    assert k2runs.HOLDS_READ in seen[0][0] and "released_at IS NULL" in seen[0][0] and "LIMIT" not in seen[0][0]
    assert rebuild.awaiting({"kind": "restore", "reason": left(["a.b.c"])})["recreated"] == ["a.b.c"]
    assert rebuild.awaiting({"kind": "restore", "reason": "make restore of backup run x"}) is None
    assert rebuild.awaiting({"kind": "operator", "reason": left()}) is None


FAILURES = {
    "replace": ({"fail": {"replace": rebuild.Failed("spark exited 1")}}, []),  # raised before it is logged
    "load refused": ({"codes": {"load": 2}}, ["replace", "load"]),
    "load staged nothing": ({"rows": 0}, ["replace", "load"]),
    "audit failed": ({"codes": {"audit": 3}}, ["replace", "load", "audit"]),
    "publish refused": ({"codes": {"publish": 2}}, ["replace", "load", "audit", "publish"]),
    "build failed": ({"codes": {"build": 1}}, ["replace", "load", "audit", "publish", "build"]),
    "build pages": ({"codes": {"build": 3}}, ["replace", "load", "audit", "publish", "build"]),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_a_failed_step_stops_there_releases_the_lock_and_unpauses_what_it_paused(tmp_path, monkeypatch, capsys, case):
    setup, ran = FAILURES[case]
    run, log, holds, fake = go(tmp_path, monkeypatch, **{k: v for k, v in setup.items() if k != "fail"})
    fake.fail = setup.get("fail", {})
    assert run() == 1
    assert steps(log) == ["hold", "pause", *ran, "release", "unpause"] and holds.open == []
    assert "rebuild: FAIL" in capsys.readouterr().err


def test_a_drain_that_never_ends_is_refused_released_and_unpaused(tmp_path, monkeypatch):
    run, log, holds, _ = go(tmp_path, monkeypatch, busy=(["build (run scheduled__x, running)"],))
    assert run() == 2
    assert steps(log) == ["hold", "pause", "release", "unpause"] and holds.open == []
    assert sum(1 for e in log if e[0] == "sleep") == rebuild.WAIT_S // rebuild.POLL_S


def test_an_interrupt_leaves_the_lock_and_the_pause_and_says_how_to_end_them(tmp_path, monkeypatch, capsys):
    run, log, holds, fake = go(tmp_path, monkeypatch)
    fake.fail = {"build": KeyboardInterrupt()}
    assert run() == 130
    assert steps(log)[-1] == "publish" and holds.open == [("rebuild", "make rebuild")]
    assert "UNLOCK=1" in capsys.readouterr().err


def test_a_dag_paused_before_is_never_unpaused(tmp_path, monkeypatch):
    run, log, _, _ = go(tmp_path, monkeypatch, product=DERIVED, paused=True)
    assert run() == 0
    assert steps(log) == ["hold", "build", "release"]


def test_another_hold_keeps_the_dag_paused_and_is_never_released(tmp_path, monkeypatch, capsys):
    run, log, holds, _ = go(tmp_path, monkeypatch, product=DERIVED, open_=[("operator", "rob")])
    assert run() == 0
    assert steps(log) == ["hold", "pause", "build", "release"] and holds.open == [("operator", "rob")]
    assert "stays paused: a operator hold is open" in capsys.readouterr().out


def test_a_lock_that_cannot_be_released_is_loud_and_names_unlock(tmp_path, monkeypatch, capsys):
    run, log, holds, _ = go(tmp_path, monkeypatch, product=DERIVED)
    holds.release_error = RuntimeError("Trino is down")
    assert run() == 1
    assert "unpause" not in steps(log) and "LOUD FAILURE" in (err := capsys.readouterr().err) and "UNLOCK=1" in err


def test_unlock_ends_only_a_rebuild_lock_and_never_while_its_one_shot_runs(monkeypatch):
    log = []
    holds = Holds(monkeypatch, log, [("operator", "rob"), ("rebuild", "make rebuild")])
    fake = IO(log)
    fake.alive = True
    with pytest.raises(rebuild.Refused, match="still runs"):
        rebuild.unlock(None, KAFKA, DAG, "rob", fake)
    fake.alive = False
    assert rebuild.unlock(None, KAFKA, DAG, "rob", fake) == 0
    assert holds.open == [("operator", "rob")] and "unpause" not in steps(log)
    with pytest.raises(rebuild.Refused, match="no rebuild lock"):
        rebuild.unlock(None, KAFKA, DAG, "rob", fake)
    assert holds.open == [("operator", "rob")]


def test_bronze_is_replaced_by_its_released_ddl_as_create_or_replace_after_its_audit_branch_goes():
    ddl = (GOLDEN / "lake" / "repository" / rebuild.bronze_ddl("lake.kraken_trades.bronze_trade")).read_text()
    stmts = rebuild.replace_statements(ddl, "lake.kraken_trades.bronze_trade")
    assert stmts[0] == "ALTER TABLE `lake`.`kraken_trades`.`bronze_trade` DROP BRANCH IF EXISTS `audit`"
    assert stmts[1:3] == ["SET spark.sql.ansi.enabled=true", "SET spark.sql.ansi.doubleQuotedIdentifiers=true"]
    assert stmts[3].startswith('CREATE OR REPLACE TABLE "lake"."kraken_trades"."bronze_trade" (\n    "kafka_partition" INT NOT NULL,')
    assert stmts[3].replace("CREATE OR REPLACE TABLE", "CREATE TABLE") + ";" in ddl and len(stmts) == 4
    for bad in (ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"), ddl + "SELECT 1;", ddl.split("CREATE")[0]):
        with pytest.raises(rebuild.Refused):
            rebuild.replace_statements(bad, "lake.kraken_trades.bronze_trade")
    with pytest.raises(rebuild.Refused):
        rebuild.replace_statements(ddl, "lake.kraken_trades.landing")  # another table's DDL
    for table in ("lake.kraken_trades", "kraken_trades_build.x.bronze_trade", "lake.kraken trades.b"):
        with pytest.raises(rebuild.Refused):
            rebuild.bronze_ddl(table)


def test_the_run_id_is_the_dags_and_a_utc_instant_both_runners_accept():
    assert rebuild.run_id(DAG, NOW) == RUN
    # docker/dbt-runner/k2_dbt.py RUN_ID and docker/lake/connector.py RUN_NAME, each as its file spells it
    run = RUN.split("/", 1)[1]
    import re
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,200}", run) and re.fullmatch(r"[A-Za-z0-9_~:+-][A-Za-z0-9_.~:+-]*", run)
    assert 'RUN_ID = re.compile(r"([a-z][a-z0-9_]*)/([A-Za-z0-9][A-Za-z0-9_.:+-]{0,200})")' in (ROOT / "docker/dbt-runner/k2_dbt.py").read_text()
    assert 'RUN_NAME = re.compile(r"[A-Za-z0-9_~:+-][A-Za-z0-9_.~:+-]*")' in (ROOT / "docker/lake/connector.py").read_text()


class Done:
    def __init__(self, out="", code=0):
        self.stdout, self.returncode = out, code


def test_the_live_calls_are_the_factorys_and_deploys_with_no_secret_in_any_argv(monkeypatch, capsys):
    calls = []
    answers = {"list-runs": '[{"run_id": "scheduled__x"}]',
               "states-for-dag-run": '[{"task_id": "load", "state": "running"}, {"task_id": "audit", "state": "scheduled"}]',
               "list": f'[{{"dag_id": "{DAG}", "is_paused": "False"}}]'}

    def run(argv, input=None, **k):
        calls.append((argv, input))
        verb = next((a for a in ("list-runs", "states-for-dag-run", "list") if a in argv), None)
        return Done("INFO noise\n" + answers[verb] + "\n" if verb else '{"exit": 0, "rows": 3}\n')
    monkeypatch.setattr(rebuild.subprocess, "run", run)
    io, inside = rebuild.Live(), f"/k2/releases/{RELEASE}"
    assert io.busy(DAG) == ["load (run scheduled__x, running)"]  # scheduled: a paused DAG never queues it
    assert io.paused(DAG) is False
    assert io.spark_task(inside, "load", "--run-id", RUN) == (0, {"exit": 0, "rows": 3})
    assert io.build(inside, DAG, KAFKA, RUN) == (0, {"exit": 0, "rows": 3})
    io.replace(inside, ["SET x=1"])
    sched = ["docker", "exec", "k2-airflow-scheduler", "airflow"]
    assert [a for a, _ in calls[:3]] == [
        [*sched, "dags", "list-runs", DAG, "--state", "running", "-o", "json"],
        [*sched, "tasks", "states-for-dag-run", DAG, "scheduled__x", "-o", "json"], [*sched, "dags", "list", "-o", "json"]]
    assert calls[3][0] == ["docker", "exec", "k2-spark-iceberg", "python3", f"{inside}/platform/docker/lake/connector.py",
                           "load", "--run-id", RUN]
    assert calls[4] == (["docker", "exec", "-i", "k2-airflow-scheduler", "python3", "-", inside, DAG, KAFKA, RUN,
                         "k2-dbt-runner-kraken_trades-rebuild"], rebuild.BUILD_PY)
    assert calls[5] == (["docker", "exec", "-i", "k2-spark-iceberg", "python3", "-", inside, '["SET x=1"]'], rebuild.REPLACE_PY)
    assert all("password" not in " ".join(a) and "secret" not in " ".join(a) for a, _ in calls)
    monkeypatch.setattr(rebuild.subprocess, "run", lambda argv, **k: Done("", 1))
    with pytest.raises(rebuild.Failed):
        io.replace(inside, ["SET x=1"])
    with pytest.raises(RuntimeError):
        io.busy(DAG)


AIRFLOW_STUB = {"airflow/__init__.py": "", "airflow/sdk/__init__.py": "class BaseOperator:\n    pass\n",
                "airflow/sdk/exceptions.py": "".join(f"class {n}(Exception):\n    pass\n" for n in
                                                     ("AirflowException", "AirflowFailException", "AirflowSkipException"))}
DOCKER_STUB = """#!/bin/sh
printf '%s\\n' "$@" > "$DOCKER_ARGV"
echo '{"exit": 0, "expire": []}'
"""


def test_the_build_snippet_runs_the_releases_own_one_shot_as_a_dag_build_with_full_refresh(tmp_path):
    rel = release(tmp_path, KAFKA)
    dags = rel / "platform" / "docker" / "airflow" / "dags"
    dags.mkdir(parents=True)
    # the release's own oneshot.py, its mount point moved to where this test keeps the release
    text, mount = (ROOT / "docker" / "airflow" / "dags" / "oneshot.py").read_text(), 'RELEASES_MOUNT = "/k2/releases"'
    assert text.count(mount) == 1
    (dags / "oneshot.py").write_text(text.replace(mount, f"RELEASES_MOUNT = {str(rel.parent)!r}"))
    shutil.copy(GOLDEN / "pipeline" / "compiled" / "oneshots.json", rel / "compiled" / "oneshots.json")
    stub, bin_ = tmp_path / "stub", tmp_path / "bin"
    for path, text in AIRFLOW_STUB.items():
        (stub / path).parent.mkdir(parents=True, exist_ok=True)
        (stub / path).write_text(text)
    bin_.mkdir()
    (bin_ / "docker").write_text(DOCKER_STUB)
    (bin_ / "docker").chmod(0o755)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "PYTHONPATH": str(stub), "DOCKER_ARGV": str(tmp_path / "argv"),
           "K2_RELEASES": "/host/rels", "K2_SECRETS": "/host/secrets", "K2_COMPOSE_PROJECT": "k2",
           "K2_RUN_UID": "1000", "K2_RUN_GID": "1000", "K2_TRINO_HOST": "trino", "K2_TRINO_INTERNAL_PORT": "8443",
           "K2_RUNS_USER": "svc.runs", "K2_CATALOG_URI": "http://lakekeeper:8181/catalog"}
    done = subprocess.run([sys.executable, "-", str(rel), DAG, KAFKA, RUN, rebuild.oneshot_name(DAG)], input=rebuild.BUILD_PY,
                          env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert rebuild.last_json(done.stdout) == {"exit": 0, "expire": []}
    argv = (tmp_path / "argv").read_text().splitlines()
    build = json.loads((PRODUCTS / KAFKA / "pipeline.json").read_text())["build"]
    assert argv[:4] == ["run", "--rm", "--name", "k2-dbt-runner-kraken_trades-rebuild"]
    assert argv[argv.index("dbt-runner:5a6b7c8d9e0f") + 1:] == ["k2-dbt", "build", "--release", RELEASE, "--product", KAFKA,
                                                               "--run-id", RUN, "--full-refresh"]
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert f"type=bind,source=/host/secrets/principals/{build['writer']}.password,target=/run/k2-secrets/builder.password,readonly" in mounts
    assert f"type=bind,source=/host/secrets/principals/{build['publisher']}.password,target=/run/k2-secrets/publisher.password,readonly" in mounts
    assert "type=bind,source=/host/secrets/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" in mounts
    assert not [m for m in mounts if "svc.operator" in m] and "--network" in argv and "k2_k2-catalog" in argv


def test_the_replace_snippet_runs_each_statement_in_the_releases_own_spark_session(tmp_path):
    lake = tmp_path / "rel" / "platform" / "docker" / "lake"
    lake.mkdir(parents=True)
    (lake / "spark_conf.py").write_text(
        "import os\nclass S:\n    def sql(self, s):\n        open(os.environ['RAN'], 'a').write(s + '\\n--\\n')\n"
        "    def stop(self):\n        open(os.environ['RAN'], 'a').write('stopped\\n')\n"
        "def lake_session(name):\n    assert name == 'k2-rebuild-bronze'\n    return S()\n")
    ran = tmp_path / "ran"
    stmts = ["ALTER TABLE `lake`.`a`.`b` DROP BRANCH IF EXISTS `audit`", 'CREATE OR REPLACE TABLE "lake"."a"."b" (\n "x" INT\n)']
    done = subprocess.run([sys.executable, "-", str(tmp_path / "rel"), json.dumps(stmts)], input=rebuild.REPLACE_PY,
                          env={**os.environ, "RAN": str(ran)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert ran.read_text() == "".join(s + "\n--\n" for s in stmts) + "stopped\n"


def releases(tmp_path):
    rel = release(tmp_path, KAFKA)
    (rel / "compiled" / "airflow").mkdir(parents=True)
    (rel / "compiled" / "airflow" / "dags.json").write_text(json.dumps({"dags": [{"product": KAFKA, "dagId": DAG}]}))
    (tmp_path / "rels" / "pointers").mkdir()
    (tmp_path / "rels" / "pointers" / "airflow").symlink_to(f"../{RELEASE}")
    return tmp_path / "rels"


def test_main_connects_as_svc_operator_from_its_file_and_reads_the_live_release(monkeypatch, tmp_path, capsys):
    secrets = tmp_path / "secrets"
    monkeypatch.setenv("K2_RELEASES", str(releases(tmp_path)))
    monkeypatch.setenv("K2_SECRETS", str(secrets))
    calls = []
    monkeypatch.setattr(k2runs, "connect", lambda **k: calls.append(k) or "conn")
    monkeypatch.setattr(rebuild, "rebuild", lambda conn, rel, p, d, by, io, now: calls.append((conn, rel.name, p, d)) or 0)
    monkeypatch.setattr(rebuild, "unlock", lambda conn, p, d, by, io: calls.append(("unlock", p)) or 0)
    assert rebuild.main(["kraken-trades"]) == 2 and calls == []  # no svc.operator file: refused before connecting
    assert "make password PRINCIPAL=svc.operator TO_FILE=1" in capsys.readouterr().err
    (secrets / "principals").mkdir(parents=True)
    (secrets / "principals" / "svc.operator.password").write_text("x")
    assert rebuild.main(["market-capture/kraken-trades"]) == 0 and rebuild.main(["kraken-trades", "--unlock"]) == 0
    assert calls[0]["user"] == "svc.operator" and calls[1] == ("conn", RELEASE, KAFKA, DAG) and calls[-1] == ("unlock", KAFKA)
    for bad in ([], ["kraken-trades", "--force"], ["nope"]):
        assert rebuild.main(bad) == 2


def test_make_rebuild_passes_unlock_only_when_asked():
    def dry(*args):
        return subprocess.run(["make", "-n", "-C", str(ROOT), "rebuild", *args], capture_output=True, text=True).stdout
    assert 'scripts/rebuild.py "market-capture/kraken-trades"' in dry("PRODUCT=market-capture/kraken-trades")
    assert "--unlock" not in dry("PRODUCT=x") and "--unlock" in dry("PRODUCT=x", "UNLOCK=1")


def test_the_help_and_header_say_bronze_reads_no_row_until_the_restage_publishes():
    assert "no snapshot" in (ROOT / "mk/rebuild.mk").read_text() and "readers see no row" in (ROOT / "mk/rebuild.mk").read_text()
    assert "bronze main has\n          no snapshot" in rebuild.__doc__

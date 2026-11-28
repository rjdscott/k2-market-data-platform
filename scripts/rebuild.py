#!/usr/bin/env python3
"""make rebuild: one product of the live release rebuilt from what it is built from (plan 004, step W1-15b).

  make rebuild PRODUCT=<domain/name | domain.name | name>
  make rebuild PRODUCT=<...> UNLOCK=1    # end the lock a killed rebuild left, once none of it runs

Host-run, as scripts/hold.py is, through its helpers: the live release is the airflow pointer's, and
the lock is written over TLS on localhost:${K2_TRINO_PORT:-8443} as svc.operator, the holds' one
writer, its password read from ${K2_SECRETS:-~/k2-secrets}/principals/svc.operator.password and never
printed (`make password PRINCIPAL=svc.operator TO_FILE=1` makes it once). In order, stopping at the
first step that fails:

1. lock   a `rebuild` hold on the product (phase-001-interfaces.md, "Holds"), refused while one is
          open; its DAG paused unless it is already, then every task of its running runs waited for,
          as deploy's drain reads them (`dags list-runs`, `tasks states-for-dag-run`): from here the
          product's runs fail in resolve, and nothing else builds it.
2. bronze a Kafka product's only: bronze replaced empty by its release's own DDL (CREATE OR REPLACE,
          which keeps the table and its old snapshots, after its `audit` branch is dropped), then
          restaged by the product's own connector.py load, audit and publish, run as the DAG runs
          them in the Spark container. The landing holds every record ever read and is never expired
          (ADR-063), and bronze with no staged-through offsets stages from the landing's first
          offset, so the restage is one batch of everything, audited as any batch. A load that
          stages nothing stops here: the full refresh would rebuild silver from an empty bronze. A
          file product's bronze is its parser's (the fetch container's) and stays as it is.
3. build  `k2-dbt build --full-refresh` in the dbt-runner one-shot, started in the scheduler by the
          release's own oneshot.py, so its image, mounts, principals and limits are a DAG build's,
          under the container name `k2-dbt-runner-<dag>-rebuild`, which no DAG run uses.
4. unlock the lock released only if step 1 placed it, and the DAG unpaused only if step 1 paused it
          and no other hold (an operator's, a restore's) is open. Interrupted (Ctrl-C), the lock and
          the pause are left: a step may still run in its container, and `UNLOCK=1` ends the lock
          once `docker ps` shows no `k2-dbt-runner-<dag>-rebuild` and the Spark container no
          connector.py of the rebuild's run.

The run id is `<dag>/rebuild__<UTC start, ISO 8601>`: the restage's bronze commit and audit rows and
the build's run records carry it. Exits: 0 rebuilt; 1 a step failed (said, with what it left); 2
refused before anything changed (or the lock refused, or the drain timed out); 130 interrupted.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hold
from hold import Refused, k2runs

SPARK = "k2-spark-iceberg"  # compose's container_name, as docker/airflow/dags/factory.py names it
RELEASES_MOUNT = "/k2/releases"  # where the scheduler and Spark see the releases root (k2gen.model.RELEASES_MOUNT)
LAKE_WAREHOUSE = "k2"  # the `lake` catalog's warehouse (k2gen.model.LAKE_WAREHOUSE): bronze's DDL is under it
LOCK, BY = "rebuild", "make rebuild"  # the hold's kind, and its held_by: the make target, for a lock (I-7)
BUSY = ("queued", "running", "restarting")  # scripts/deploy.py's: a task instance's states a pause does not stop
WAIT_S, POLL_S = 900, 10  # the drain: a Kafka product's run times out at 10 minutes
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")  # docker/lake/connector.py's, which bronze's names pass
RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # oneshot.py's, for a release id in a container path

# The bronze statements, in the Spark container through the release's own session builder.
REPLACE_PY = """\
import json, sys
sys.path.insert(0, sys.argv[1] + "/platform/docker/lake")
from spark_conf import lake_session
spark = lake_session("k2-rebuild-bronze")
try:
    for statement in json.loads(sys.argv[2]):
        print("rebuild: spark: " + statement.splitlines()[0], flush=True)
        spark.sql(statement)
finally:
    spark.stop()
"""
# The full refresh, in the scheduler: oneshot.py's own docker_run over the release (it reads only
# these five attributes of its operator), with what factory.PinnedOneShot gives a build.
BUILD_PY = """\
import importlib.util, json, subprocess, sys, types
release_path, dag, product, run_id, name = sys.argv[1:6]
spec = importlib.util.spec_from_file_location("oneshot", release_path + "/platform/docker/airflow/dags/oneshot.py")
oneshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oneshot)
build = json.load(open(f"{release_path}/compiled/products/{product}/pipeline.json"))["build"]
op = types.SimpleNamespace(kind="dbt-runner", release_path=release_path, env={"K2_TRINO_USER": build["writer"], "K2_PUB_USER": build["publisher"]},
                           principals=[build["writer"], build["publisher"], "svc.runs"],
                           command=["k2-dbt", "build", "--release", release_path.rsplit("/", 1)[1], "--product", product,
                                    "--run-id", run_id, "--full-refresh"])
argv, env, _ = oneshot.OneShotOperator.docker_run(op, name, dag)  # dbt-runner has no step before it (SPOOL is fetch's)
sys.exit(subprocess.run(argv, env=env, stdin=subprocess.DEVNULL).returncode)
"""


class Failed(Exception):
    """A step failed after the lock was placed: exit 1, its message saying what it left."""


def run_id(dag: str, now: datetime) -> str:
    return f"{dag}/rebuild__{now.astimezone(timezone.utc).isoformat(timespec='seconds')}"


def oneshot_name(dag: str) -> str:
    return f"k2-dbt-runner-{dag}-rebuild"


def bronze_ddl(table: str) -> str:
    """The release path of bronze's Spark DDL (k2gen.model.ddl_path): `lake` is warehouse k2."""
    parts = table.split(".")
    if len(parts) != 3 or parts[0] != "lake" or not all(NAME.fullmatch(p) for p in parts):
        raise Refused(f"bronze {table!r} is not lake.<namespace>.<table>")
    return f"compiled/lake/ddl/spark/{LAKE_WAREHOUSE}.{parts[1]}.{parts[2]}.sql"


def replace_statements(ddl: str, table: str) -> list[str]:
    """Bronze made empty from the release's own DDL: its `audit` branch dropped (backquoted, the only
    names Iceberg's branch parser reads), then the DDL's two session SETs and its CREATE TABLE as
    CREATE OR REPLACE TABLE. The replace keeps the table, its old snapshots and its other refs, and
    gives main no snapshot, so the next stage finds no staged-through offsets and stages from the
    landing's first. Refused on a DDL of any other shape: a statement it does not know is never run."""
    _, ns, name = table.split(".")
    stmts = [s.strip() for s in ddl.split(";") if s.strip()]  # k2gen's Spark DDL: no COMMENT, no quoted ';'
    create = f'CREATE TABLE "lake"."{ns}"."{name}" ('
    if len(stmts) != 3 or not all(s.startswith("SET spark.sql.") for s in stmts[:2]) or not stmts[2].startswith(create):
        raise Refused(f"{table}: its release DDL is not two SETs and its CREATE TABLE")
    return [f"ALTER TABLE `lake`.`{ns}`.`{name}` DROP BRANCH IF EXISTS `audit`", *stmts[:2],
            "CREATE OR REPLACE TABLE" + stmts[2][len("CREATE TABLE"):]]


def last_json(out: str) -> dict[str, Any]:
    """A task's result, its last stdout line ({"exit": n, ...}), or {} when there is none."""
    lines = [x for x in out.splitlines() if x.strip()]
    try:
        found = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


class Live:
    """The scheduler and the Spark container by `docker exec`, as the DAG factory reaches them."""

    @staticmethod
    def _run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        # stderr is the operator's terminal: Spark's and dbt's logs show as they come
        return subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, text=True, errors="replace")

    def airflow_json(self, *argv: str) -> Any:
        """One Airflow CLI listing in the scheduler, its rows: piped, `-o json` prints one line
        (scripts/deploy.py's airflow(), whose argv were checked against the 3.3.2 scheduler)."""
        done = self._run(["docker", "exec", hold.SCHEDULER, "airflow", *argv, "-o", "json"])
        rows = [x for x in done.stdout.splitlines() if x.startswith("[")]
        if done.returncode or not rows:
            raise RuntimeError(f"airflow {' '.join(argv)} exit {done.returncode}: {done.stdout.strip()[-200:]}")
        return json.loads(rows[-1])

    def paused(self, dag: str) -> bool:
        found = [d for d in self.airflow_json("dags", "list") if d.get("dag_id") == dag]
        if not found:
            raise Refused(f"Airflow lists no DAG {dag}")
        return str(found[0].get("is_paused")) == "True"

    def pause(self, dag: str) -> None:
        hold.airflow("pause", dag)

    def unpause(self, dag: str) -> None:
        hold.airflow("unpause", dag)

    def busy(self, dag: str) -> list[str]:
        """Each task of the DAG's running runs in a state a pause does not stop: a paused DAG's run
        stays `running` with its later tasks unscheduled, so the tasks are asked, not the runs."""
        return [f"{t['task_id']} (run {r['run_id']}, {t['state']})"
                for r in self.airflow_json("dags", "list-runs", dag, "--state", "running")
                for t in self.airflow_json("tasks", "states-for-dag-run", dag, r["run_id"]) if t.get("state") in BUSY]

    def replace(self, release_path: str, statements: list[str]) -> None:
        done = self._run(["docker", "exec", "-i", SPARK, "python3", "-", release_path, json.dumps(statements)], REPLACE_PY)
        print(done.stdout, end="")
        if done.returncode:
            raise Failed(f"bronze's replace exited {done.returncode}")

    def spark_task(self, release_path: str, task: str, *args: str) -> tuple[int, dict[str, Any]]:
        done = self._run(["docker", "exec", SPARK, "python3", f"{release_path}/platform/docker/lake/connector.py", task, *args])
        print(done.stdout, end="")
        return done.returncode, last_json(done.stdout)

    def build(self, release_path: str, dag: str, product: str, run: str) -> tuple[int, dict[str, Any]]:
        done = self._run(["docker", "exec", "-i", hold.SCHEDULER, "python3", "-", release_path, dag, product, run,
                          oneshot_name(dag)], BUILD_PY)
        print(done.stdout, end="")
        return done.returncode, last_json(done.stdout)

    def running(self, name: str) -> bool:
        done = self._run(["docker", "ps", "-q", "--filter", f"name=^/{name}$"])
        if done.returncode:
            raise RuntimeError(f"docker ps exit {done.returncode}")
        return bool(done.stdout.strip())

    sleep = staticmethod(time.sleep)
    clock = staticmethod(time.monotonic)


def check(pipeline: dict[str, Any], product: str) -> bool:
    """Whether the product's bronze is restaged (a Kafka product); a refusal when it is no product
    to rebuild: paused (a paused Kafka load stages nothing) or retired, or with no dbt build."""
    if pipeline.get("status") in ("paused", "retired"):
        raise Refused(f"{product} is {pipeline['status']} in the live release: nothing rebuilds it")
    if (pipeline.get("build") or {}).get("engine") != "dbt":
        raise Refused(f"{product} has no dbt build to rebuild")
    return (pipeline.get("connector") or {}).get("kind") == "kafka"


def drain(dag: str, io: Any) -> None:
    deadline = io.clock() + WAIT_S
    while busy := io.busy(dag):
        if io.clock() >= deadline:
            raise Refused(f"{dag} still runs after {WAIT_S} s: {'; '.join(busy)}; rebuild again once it ends")
        print(f"rebuild: waiting for {'; '.join(busy)}", flush=True)
        io.sleep(POLL_S)


def restage(release: str, inside: str, product: str, run: str, table: str, statements: list[str], io: Any) -> None:
    io.replace(inside, statements)
    left = (f"bronze {table} holds no published row and its restage stopped; the landing is untouched, and a "
            "later load stages bronze again from the landing's first offset (after a failed audit, once its "
            "batch is cleared as connector.py says)")
    for task in ("load", "audit", "publish"):
        code, result = io.spark_task(inside, task, "--release", release, "--product", product, "--run-id", run)
        if code:
            raise Failed(f"restage {task} exited {code}: {left}")
        if task == "load" and not result.get("rows"):
            raise Failed(f"restage load staged no row from the landing, so no full refresh is run: {left}")
        print(f"rebuild: restage {task} done", flush=True)


def finish(conn: Any, product: str, dag: str, by: str, paused_here: bool, io: Any) -> int:
    """Step 4: this command's lock released, then the DAG unpaused if this command paused it and no
    other hold is open."""
    try:
        k2runs.release(conn, product, by, kind=LOCK)
    except Exception as e:
        print(f"rebuild: LOUD FAILURE: the rebuild lock on {product} is still open ({type(e).__name__}: {e}); its "
              f"runs fail in resolve until `make rebuild PRODUCT={product} UNLOCK=1` ends it", file=sys.stderr)
        return 1
    if not paused_here:
        print(f"rebuild: released the lock on {product}; DAG {dag} is left as this command found it")
        return 0
    if other := k2runs.held(conn, product):
        print(f"rebuild: released the lock on {product}; DAG {dag} stays paused: a {other['kind']} hold is open")
        return 0
    try:
        io.unpause(dag)
    except Exception as e:
        print(f"rebuild: released the lock on {product}, but DAG {dag} was NOT unpaused ({e}); "
              f"`docker exec {hold.SCHEDULER} airflow dags unpause {dag} -y`", file=sys.stderr)
        return 1
    print(f"rebuild: released the lock on {product}; DAG {dag} unpaused")
    return 0


def rebuild(conn: Any, release_path: Path, product: str, dag: str, by: str, io: Any, now: datetime) -> int:
    """Steps 1 to 4; every refusal that changes nothing comes before the lock."""
    release = release_path.name
    if not RELEASE.fullmatch(release):
        raise Refused(f"the live release {release!r} is not a release id")
    inside, run = f"{RELEASES_MOUNT}/{release}", run_id(dag, now)
    pipeline = json.loads((release_path / "compiled" / "products" / product / "pipeline.json").read_text())
    table, statements = None, []
    if kafka := check(pipeline, product):
        [bronze] = pipeline["bronze"]  # a Kafka product has exactly one (I-1)
        table = bronze["table"]
        statements = replace_statements((release_path / bronze_ddl(table)).read_text(), table)
    # ponytail: held() names the newest open hold only, so a restore's lock under a newer operator
    # hold is not seen; read every open hold here if a restore and an operator hold ever overlap.
    if (open_ := k2runs.held(conn, product)) and open_["kind"] == "restore":
        raise Refused(f"{product} is held by a restore ({open_['held_by']} at {open_['held_at']}); rebuild after it")
    try:
        k2runs.hold(conn, product, f"make rebuild by {by}", BY, kind=LOCK)
    except k2runs.HoldError:
        raise Refused(f"{product} has a rebuild lock open already: another make rebuild runs, or one was killed; "
                      f"once none of it runs, `make rebuild PRODUCT={product} UNLOCK=1`") from None
    print(f"rebuild: {product} locked ({LOCK} hold); run {run} on release {release}", flush=True)
    paused_here, code = False, 0
    try:
        if not io.paused(dag):
            io.pause(dag)
            paused_here = True
        drain(dag, io)
        if kafka:
            restage(release, inside, product, run, table, statements, io)
        built, result = io.build(inside, dag, product, run)
        if built:
            raise Failed(f"k2-dbt build --full-refresh exited {built} ({result}): its outputs are back at their audited "
                         "tags (k2-dbt restores every output whose main moved), and what is above stays as it is")
        print(f"rebuild: {product} rebuilt: {result}")
    except KeyboardInterrupt:
        print(f"rebuild: interrupted: the rebuild lock on {product} stays open and DAG {dag} as it is, since a step may "
              f"still run; once `docker ps` shows no {oneshot_name(dag)} and `docker exec {SPARK} ps -ef` no connector.py "
              f"of {run}, `make rebuild PRODUCT={product} UNLOCK=1`", file=sys.stderr)
        return 130
    except Refused as e:
        print(f"rebuild: REFUSED: {e}", file=sys.stderr)
        code = 2
    except Exception as e:
        print(f"rebuild: FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        code = 1
    return finish(conn, product, dag, by, paused_here, io) or code


def unlock(conn: Any, product: str, dag: str, by: str, io: Any) -> int:
    """The rebuild lock a killed rebuild left, released; refused while its one-shot still runs. The
    DAG is left as it is: whether that rebuild paused it is not known."""
    if io.running(oneshot_name(dag)):
        raise Refused(f"{oneshot_name(dag)} still runs: that rebuild is alive; wait for it")
    try:
        k2runs.release(conn, product, by, kind=LOCK)
    except k2runs.HoldError:
        raise Refused(f"{product} has no rebuild lock open") from None
    print(f"rebuild: released the rebuild lock on {product}; DAG {dag} is left as it is "
          f"(`docker exec {hold.SCHEDULER} airflow dags unpause {dag} -y` once it should run)")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[1:] not in ([], ["--unlock"]):
        print("usage: rebuild.py <product> [--unlock]", file=sys.stderr)
        return 2
    env = os.environ
    try:
        releases = Path(env.get("K2_RELEASES") or Path.home() / "k2-releases")
        product, dag = hold.resolve(argv[0], hold.live_dags(releases))
        release_path = (releases / "pointers" / "airflow").resolve()
        secrets = Path(env.get("K2_SECRETS") or Path.home() / "k2-secrets")
        conn = k2runs.connect(host="localhost", port=int(env.get("K2_TRINO_PORT", "8443")), user=hold.WRITER,
                              password_file=str(hold.password_file(secrets)), ca=str(secrets / "tls" / "ca.pem"))
        by = getpass.getuser()
        if argv[1:] == ["--unlock"]:
            return unlock(conn, product, dag, by, Live())
        return rebuild(conn, release_path, product, dag, by, Live(), datetime.now(timezone.utc))
    except (Refused, k2runs.HoldError) as e:
        print(f"rebuild: REFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"rebuild: FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

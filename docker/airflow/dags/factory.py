"""
The DAG factory: one DAG per entry of its own release's `compiled/airflow/dags.json`, built at
parse time. The run-time contracts are docs/development/phase-001-interfaces.md, "Run-time
contracts" (the DAG, resolve, pools, the fetch exits, the wrapper, expire).

Which release: this file's own real path. Airflow reads the DAG folder through the pointer
`/k2/releases/pointers/airflow/platform/docker/airflow/dags`; resolving `__file__` follows the pointer
to `/k2/releases/<id>/platform/docker/airflow/dags/factory.py`, four levels below the release. A tree
without a `manifest.json` there (a checkout, the tests) builds no DAG; a release always has one, and
then a missing or malformed `dags.json` raises, which Airflow shows as an import error.

Which release a run uses: `resolve` takes the pointer's target once, at the run's start, and hands it
on by XCom; every later task reads that path and nothing else, so a deploy mid-run leaves a running
run on its release (plan 002's deploy-mid-run). Airflow re-parses this file when each task starts, so
the task graph and this code are the parse-time release's; that is why deploy drains a DAG whose
shape changes (ADR-058, `dagShapeChange`). What each task runs (script paths, the product's
principals, maintain's crons) is read at execute from the pinned release's files.

The edges, for the full task list (any entry's list is a subset, in this order):

    resolve >> fetch >> load >> audit >> publish >> build >> expire >> record
                                                              expire >> maintain >> record

A Kafka product (pipeline.json's connector kind `kafka`) has no fetch: its load reads the topic
itself. Its DAG adds compact after maintain, which dags.json does not list (dags.schema.json's task
enum has no such id), so the task follows from the kind; a kind change always changes dags.json's
tasks (fetch comes or goes), so deploy's drain still sees every shape change:

    resolve >> load >> audit >> publish >> build >> expire >> maintain >> compact >> record

A public-API product runs the same fetch one-shot as a file product (fetch.py reads the kind), so
its DAG is the file kind's.

A product whose pipeline.json names a refresh target (key `refresh`, {product, rules}: the target's
product id and the warn rules that start it) adds `build >> refresh` (W1-6, phase-001-interfaces.md,
"Holds"): Airflow's TriggerDagRunOperator, skipped unless build's result says `degraded: true` and its
`warned` names one of the rules, starting the target's DAG at most once per target and hour and
never waiting for it. A target is another product of the release whose own pipeline names no refresh target, so the
chain is one hop; the parse refuses anything else. dags.json does not list `refresh`, so a pipeline
that gains or loses the key changes the DAG's shape without a drain. ponytail: safe, since the task
reads only build's result and triggers at most once per target and hour; list it in dags.json's tasks if a
shape change must ever drain.

Holds (I-7): resolve refuses a run of a held product, naming the hold, read as svc.runs through the
pinned release's k2runs.held. A release without the holds table holds nothing; a read that fails
refuses the run (fail closed).

Accepting data loss (phase-001-interfaces.md, "Accepting data loss"): a manually triggered run of a
Kafka product's DAG with conf exactly {"acceptDataLoss": true, "reason": "<non-empty>"} gives load,
and no other task, `--accept-data-loss`; resolve refuses any other conf before anything is read.
Record then writes one `offset_gap` row when load's result shows the landing commit with the gap.
A scheduled run is created with no conf (the DAG declares no params), and resolve refuses the conf
on any run that is not manual, so a scheduled run cannot accept a loss.

Every task is all_success but four. build is none_failed: an unchanged fetch (exit 10) skips load,
audit and publish and build still runs, so a build fixed by a deploy, or an input that moved, is
built without new bytes (the build is idempotent: one build per run); a failed or refused data task
before it leaves build upstream_failed. expire and record are teardowns with no setup: they run once everything upstream is done, whatever
its state (all_done), and a teardown does not decide the run's state, so a failed build or audit
still fails the run while record succeeds after it. A record that cannot write fails as a task
(AirflowTaskFailed), loudly, but does not repaint the run. maintain is all_done after the data path's
last task: it expires snapshots by retention, so beside build it could expire the untagged
snapshot_before that build's rollback, or the next reconcile, restores to. All_done runs it whatever
build did; it is not a teardown, so with build it still decides the run's state, as before.

Spark tasks (load, audit, publish, expire, maintain, compact) are `docker exec` into the long-lived
Spark container, as the retired LakeScript ran them (IMPLEMENTATION-N0-N1 A13), from the pinned
release's `platform/docker/lake/`. The scheduler holds the Docker socket (ADR-059 Outcome,
2026-11-27); this module imports only the standard library, Airflow and oneshot.py, and loads one
platform module, `platform/runs/k2runs.py`, from the pinned release in `resolve` and `record`;
never anything under `domains/` (docker/airflow/tests/test_factory.py holds it to that).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The standard provider: apache-airflow 3.3.2's own dependency, 1.19.0 under the image's constraints.
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import DAG, BaseOperator, TriggerRule
from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException
from croniter import croniter  # Airflow's own cron dependency
from oneshot import RELEASE, RELEASES_MOUNT, OneShotOperator

POINTER = RELEASES_MOUNT + "/pointers/airflow"
SPARK = "k2-spark-iceberg"  # compose's container_name, the same under any project name
LAKE = "platform/docker/lake"  # the Spark scripts inside a release
K2RUNS = "platform/platform/runs/k2runs.py"
# k2gen's model.ddl_path for the holds, deploy's HOLDS_DDL: a release with this file has the table.
HOLDS_DDL = "compiled/lake/ddl/trino/governance.run_records.holds.sql"
REFRESH = "refresh"  # pipeline.json: {product, rules}, the DAG a build degraded by one of rules starts (W1-6)
POOLS = {"fetch": "fetch", "build": "dbt",
         **dict.fromkeys(("load", "audit", "publish", "expire", "maintain", "compact"), "lake_spark")}
TASKS = ("resolve", "fetch", "load", "audit", "publish", "build", "expire", "record", "maintain")
RECORDED_AS = {"compact": "maintain"}  # run-record.schema.json has no compact task: the pass is maintenance
CONF = {"acceptDataLoss", "reason"}  # both or neither (phase-001-interfaces.md, "Accepting data loss")
# ponytail: 200 keeps a 12-partition gap's offset_gap detail under k2runs' 1000-character cut; a topic
# with many more partitions needs a wider detail column, not a longer reason.
REASON_MAX = 200
DATA = ("fetch", "load", "audit", "publish", "build", "expire")  # one chain, in this order
START = datetime(2026, 11, 1, tzinfo=timezone.utc)
ENTRY_KEYS = {"dagId", "product", "status", "schedule", "pipeline", "tasks"}
DAG_ID = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
PRODUCT = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z][a-z0-9]*(-[a-z0-9]+)*")
CRON = re.compile(r"\S+( \S+){4}")
NO_RETRY = {2, 3}  # connector.py: refused, audit failed; the same batch gives the same answer

# Runs in the Spark container as `sh -c WRAPPER <pidfile> <script> <args>`: the arguments are the
# shell's positional parameters, never parsed as shell text. The driver runs in the background so
# its pid lands where on_kill finds it; killing the `docker exec` client does not stop it.
WRAPPER = 'python3 "$@" & echo $! > "$0"; wait $!; rc=$?; rm -f "$0"; exit $rc'
# `sh -c KILL <pidfile>`: TERM to the driver, KILL_GRACE_S for it to go, then KILL. PySpark's JVM
# exits when the driver's pipe closes (measured at 2 s for the retired LakeScript). No file: gone.
KILL_GRACE_S = 10
FETCH_TIMEOUT = timedelta(minutes=10)  # a fetch's execution_timeout without the run's timeoutMinutes: over fetch.py's 300 s
KILL = ('p=$(cat "$0" 2>/dev/null) || exit 0; kill -TERM "$p" 2>/dev/null; i=0; '
        f'while kill -0 "$p" 2>/dev/null && [ $i -lt {KILL_GRACE_S} ]; do sleep 1; i=$((i+1)); done; '
        'kill -KILL "$p" 2>/dev/null; rm -f "$0"; true')


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def pinned(ti) -> dict:
    """The run's release, as resolve handed it on; a task never reads the pointer."""
    pin = ti.xcom_pull(task_ids="resolve")
    if not isinstance(pin, dict) or not RELEASE.fullmatch(str(pin.get("release"))) \
            or pin.get("releasePath") != f"{RELEASES_MOUNT}/{pin['release']}":
        raise AirflowFailException(f"no release pinned by resolve: {pin!r}")
    return pin


def pipeline(pin: dict, product: str, dag_id: str) -> dict:
    doc = read_json(f"{pin['releasePath']}/compiled/products/{product}/pipeline.json")
    if doc.get("dagId") != dag_id or doc.get("product") != product:
        raise AirflowFailException(f"release {pin['release']}: {product}'s pipeline is not DAG {dag_id}'s")
    return doc


def run_id(ti) -> str:
    return f"{ti.dag_id}/{ti.run_id}"


def data_loss_reason(dag_run, kafka: bool) -> str | None:
    """The operator's reason when this run accepts data loss, None for a normal run (no conf, or
    `{}`), or a refusal naming the problem. Resolve calls it first; load and record call it again
    for the flag and the reason, so the run's conf is read by one rule."""
    conf = getattr(dag_run, "conf", None) or {}
    if not conf:
        return None
    if not isinstance(conf, dict):
        raise AirflowFailException(f"conf refused: {conf!r} is not an object")
    if extra := sorted(set(conf) - CONF):
        raise AirflowFailException(f"conf refused: unknown keys {extra}; the only conf is acceptDataLoss and "
                                   "reason, both or neither")
    if missing := sorted(CONF - set(conf)):
        raise AirflowFailException(f"conf refused: {', '.join(sorted(conf))} without {', '.join(missing)}; both or neither")
    flag, reason = conf["acceptDataLoss"], conf["reason"]
    if flag is not True:
        raise AirflowFailException(f"conf refused: acceptDataLoss must be the JSON boolean true, not {flag!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise AirflowFailException(f"conf refused: reason must be a non-empty string, not {reason!r}")
    if len(reason) > REASON_MAX:
        raise AirflowFailException(f"conf refused: reason is {len(reason)} characters, at most {REASON_MAX}")
    if not kafka:
        raise AirflowFailException("conf refused: accepting data loss is for a Kafka product's DAG only, and this "
                                   "product's connector is not kafka")
    run_type = getattr(dag_run, "run_type", None)  # a str enum: compared by value, never by str()
    if run_type != "manual":
        raise AirflowFailException(f"conf refused: only a manually triggered run accepts data loss, this run is "
                                   f"{getattr(run_type, 'value', run_type)!r}")
    return reason


def refuse_if_held(release_path: str, product: str) -> None:
    """I-7: a held product's run fails here, naming the hold, read as svc.runs (the password the
    scheduler mounts for record). A release without the holds table holds nothing. A read that fails
    refuses the run too, so a hold is never skipped because Trino was down.
    ponytail: fail closed, so an unplanned Trino outage refuses every run while it lasts; such a run
    would fail at build and record anyway, and a deploy that restarts Trino drains every DAG first."""
    if not Path(release_path, HOLDS_DDL).is_file():
        return
    try:
        k2runs = load_k2runs(release_path)
        hold = k2runs.held(k2runs.connect(), product)
    except Exception as e:  # noqa: BLE001 - unread holds refuse the run
        raise AirflowException(f"run refused: the holds of {product} could not be read: {type(e).__name__}: {e}") from None
    if hold:
        raise AirflowFailException(f"run refused: {product} is held ({hold['kind']} hold by {hold['held_by']} at "
                                   f"{hold['held_at']}): {hold['reason']}; it runs again once the hold is released")


class Resolve(BaseOperator):
    """The run's conf checked, then realpath of the airflow pointer, once per run, then the product's
    holds under that release: `{"release", "releasePath"}` by XCom."""

    def __init__(self, *, product: str, kafka: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.product, self.kafka = product, kafka

    def execute(self, context) -> dict:
        data_loss_reason(context.get("dag_run"), self.kafka)  # before the pointer, before any task
        path = os.path.realpath(POINTER)
        parent, release = os.path.split(path)
        if parent != RELEASES_MOUNT or not RELEASE.fullmatch(release) or release == "pointers":
            raise AirflowFailException(f"{POINTER} resolves to {path!r}, not {RELEASES_MOUNT}/<release id>")
        refuse_if_held(path, self.product)
        return {"release": release, "releasePath": path}


class Refresh(TriggerDagRunOperator):
    """W1-6: the refresh target's DAG started when build's result says `degraded: true` and its
    `warned` (test.<project>.<rule>.<hash>, the dbt emitter naming each test by its rule) names one of
    `rules`, else a skip: a rule the target's run cannot cure (a malformed frame) starts nothing.
    At most one run per target and hour: the fixed run id is the triggering run's hour, skipped when
    it exists, so twelve degraded five-minute builds start one. The reason is the venue's API budget:
    the target fetches a public API the owner allows a few calls an hour, and a symbol that stays
    unresolved would otherwise call it every build. Never waited for, no retry. No conf, and no
    OpenLineage keys injected into it: the target's resolve refuses any other conf."""

    def __init__(self, *, target: str, rules: list[str], **kwargs) -> None:
        super().__init__(task_id=REFRESH, trigger_dag_id=target, skip_when_already_exists=True,
                         wait_for_completion=False, openlineage_inject_parent_info=False, retries=0, **kwargs)
        self.rules = list(rules)

    def execute(self, context) -> None:
        result = context["ti"].xcom_pull(task_ids="build", key="result")
        if not (isinstance(result, dict) and result.get("degraded") is True):
            raise AirflowSkipException(f"build is not degraded, so no {self.trigger_dag_id} run: {result!r}")
        warned = {w.split(".", 2)[2].rsplit(".", 1)[0] for w in result.get("warned") or [] if isinstance(w, str) and w.count(".") >= 3}
        if not warned & set(self.rules):
            raise AirflowSkipException(f"no rule of {self.rules} warned ({result.get('warned')!r}), so no {self.trigger_dag_id} run")
        hour = context["dag_run"].run_after.astimezone(timezone.utc).strftime("%Y%m%dT%H")
        self.trigger_run_id = f"refresh__{hour}"  # per target: run ids are the target DAG's own
        return super().execute(context)


class PinnedOneShot(OneShotOperator):
    """fetch or build: the one-shot with the run's release and the command the interface gives,
    its product's principals read from the pinned release's pipeline.json."""

    def __init__(self, *, product: str, **kwargs) -> None:
        super().__init__(release_path="", command=[], **kwargs)
        self.product = product

    def execute(self, context) -> None:
        ti = context["ti"]
        pin = pinned(ti)
        doc = pipeline(pin, self.product, ti.dag_id)
        common = ["--release", pin["release"], "--product", self.product, "--run-id", run_id(ti)]
        self.release_path = pin["releasePath"]
        if self.kind == "fetch":
            ds = context["dag_run"].run_after.astimezone(timezone.utc).strftime("%Y-%m-%d")
            self.command = ["k2-fetch", *common, "--ds", ds]
        else:
            build = doc["build"]
            self.command = ["k2-dbt", "build", *common]
            self.env = {"K2_TRINO_USER": build["writer"], "K2_PUB_USER": build["publisher"]}
            self.principals = [build["writer"], build["publisher"], "svc.runs"]
        return super().execute(context)


def maintain_due(doc: dict, when: datetime) -> bool:
    """True when a maintain time falls after the schedule's previous slot and at or before `when`.
    ponytail: a day whose maintain slot falls in a run Airflow never created (paused, overrun with
    catchup off) goes unmaintained; the next day's run expires by the same retention."""
    last = croniter(doc["maintain"]["cron"], when + timedelta(seconds=1)).get_prev(datetime)
    return croniter(doc["schedule"]["cron"], when).get_prev(datetime) < last <= when


class SparkTask(BaseOperator):
    """One release script in the Spark container, by `docker exec`; its last stdout line, the
    script's JSON result, is pushed as XCom `result` whatever the exit, for record."""

    def __init__(self, *, product: str, kafka: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.product, self.kafka = product, kafka
        self._pidfile: str | None = None
        self._proc = None

    def script(self, context) -> list[str]:
        """The script and its arguments inside the container, or a skip with nothing to run."""
        ti = context["ti"]
        pin = pinned(ti)
        lake = f"{pin['releasePath']}/{LAKE}"
        common = ["--release", pin["release"], "--product", self.product, "--run-id", run_id(ti)]
        if self.task_id in ("load", "audit", "publish"):
            loss = self.task_id == "load" and data_loss_reason(context["dag_run"], self.kafka)
            return [f"{lake}/connector.py", self.task_id, *common, *(["--accept-data-loss"] if loss else [])]
        if self.task_id == "compact":
            # W1-21: maintain.py --compact, a Kafka product's landing and bronze compacted with the
            # Spark key (maintain.py picks the key by pass; this argv carries none), the whole pass
            # skipped while bronze's `audit` is ahead of `main`.
            # ponytail: daily, due when maintain is (maintain's skip skips it, all_success); every
            # run is trigger rule none_failed, if W0-12's chain timing leaves room for a Spark start.
            return [f"{lake}/maintain.py", *common, "--compact"]
        if self.task_id == "expire":
            result = ti.xcom_pull(task_ids="build", key="result")
            ids = result.get("expire") if isinstance(result, dict) else None
            if not ids:
                raise AirflowSkipException(f"nothing to expire: build's result is {result!r}")
            return [f"{lake}/expire.py", *common, "--expire", json.dumps(ids, separators=(",", ":"))]
        if self.task_id == "maintain":
            when = context["dag_run"].run_after
            if not maintain_due(pipeline(pin, self.product, ti.dag_id), when):
                raise AirflowSkipException(f"maintenance is not due in the run at {when.isoformat()}")
            return [f"{lake}/maintain.py", *common]
        raise AirflowFailException(f"{self.task_id}: not a Spark task")

    def execute(self, context) -> None:
        ti = context["ti"]
        argv = self.script(context)
        self._pidfile = f"/tmp/k2-airflow-{ti.id}.pid"  # one per try: the id changes on every retry
        cmd = ["docker", "exec", SPARK, "sh", "-c", WRAPPER, self._pidfile, *argv]
        self.log.info("spark: %s", " ".join(cmd))
        # stdin is the task supervisor's request socket: never hand it on.
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, errors="replace")
        last = ""
        for line in self._proc.stdout:
            self.log.info("%s", line.rstrip("\n"))
            last = line.strip() or last
        code = self._proc.wait()
        try:
            result = json.loads(last)
        except ValueError:
            result = None
        if isinstance(result, dict):
            ti.xcom_push(key="result", value=result)
        if code in NO_RETRY:
            raise AirflowFailException(f"{self.task_id} exited {code}")
        if code:
            raise AirflowException(f"{self.task_id} exited {code}")

    def on_kill(self) -> None:
        """Airflow calls this on SIGTERM (task killed, run timed out) and on execution_timeout. The
        driver is stopped inside the container first; then the client."""
        if self._pidfile:
            subprocess.run(["docker", "exec", SPARK, "sh", "-c", KILL, self._pidfile],
                           stdin=subprocess.DEVNULL, timeout=KILL_GRACE_S + 30, check=False)
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


def load_k2runs(release_path: str):
    """platform/runs/k2runs.py from the pinned release: platform code, never a product's."""
    spec = importlib.util.spec_from_file_location("k2runs", f"{release_path}/{K2RUNS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def offset_gap(ti, product: str, release: str, reason: str) -> dict | None:
    """The `offset_gap` row (I-3, I-1) of an accept-data-loss run, or None: written when load's
    result shows the landing commit carrying the gap was made, exit 0 or a result carrying bronze's
    `table` (the stage runs only after that commit). A plan with nothing below the log start, or a
    load refused before its commit, lost nothing."""
    result = ti.xcom_pull(task_ids="load", key="result")
    gaps = result.get("offsetGaps") if isinstance(result, dict) else None
    if not gaps or not (result.get("exit") == 0 or "table" in result):
        return None
    detail = {"partitions": [{"partition": g["partition"], "fromOffset": g["committed"], "toOffset": g["logStart"] - 1}
                             for g in gaps], "reason": reason}
    return {"run_id": run_id(ti), "product_id": product, "release_id": release, "task": "load", "event": "offset_gap",
            "table_name": result.get("landing"), "detail": json.dumps(detail, separators=(",", ":"))}


def rows(ti, product: str, release: str) -> list[dict]:
    """One task-level row per task of the run that ran: success is passed, failed is failed, and
    fetch skipped is passed (exit 10, unchanged). A task that never ran (skipped, upstream failed)
    has no row. Nothing here raises on an odd result: it only goes into detail."""
    out = []
    for crumb in ti.get_task_breadcrumbs(ti.dag_id, ti.run_id):
        task, state = crumb.get("task_id"), str(crumb.get("state"))
        if task not in (*TASKS, *RECORDED_AS) or task in ("resolve", "record"):
            continue
        event = {"success": "passed", "failed": "failed"}.get(state)
        result = ti.xcom_pull(task_ids=task, key="result")
        result = result if isinstance(result, dict) else {}
        if task == "fetch" and state == "skipped":
            event, result = "passed", {"exit": 10, "unchanged": True}
        if event is None:
            continue
        duration = crumb.get("duration")
        out.append({"run_id": run_id(ti), "product_id": product, "release_id": release, "task": RECORDED_AS.get(task, task),
                    "event": event, "rows_written": _int(result.get("rows")), "snapshot_after": _int(result.get("snapshotAfter")),
                    "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
                    "detail": json.dumps(result, separators=(",", ":"), default=str) if result else f"state {state}"})
    return out


class Record(BaseOperator):
    """The run record, as svc.runs, on every outcome; it fails only when it cannot write."""

    def __init__(self, *, product: str, kafka: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.product, self.kafka = product, kafka

    def execute(self, context) -> None:
        ti = context["ti"]
        try:
            pin = pinned(ti)
        except AirflowFailException as e:  # resolve failed: no release to record against
            raise AirflowSkipException(f"nothing to record: {e}") from None
        todo = rows(ti, self.product, pin["release"])
        reason = data_loss_reason(context["dag_run"], self.kafka)  # resolve passed the same conf
        if reason is not None and (gap := offset_gap(ti, self.product, pin["release"], reason)):
            todo.append(gap)
        try:
            k2runs = load_k2runs(pin["releasePath"])
            conn = k2runs.connect()
            for row in todo:
                k2runs.record(conn, **row)
        except Exception as e:  # noqa: BLE001 - any failure to write is this task's failure
            raise AirflowException(f"run record not written for {run_id(ti)}: {type(e).__name__}: {e}") from None
        self.log.info("recorded %d rows: %s", len(todo), ", ".join(f"{r['task']} {r['event']}" for r in todo))


def check_entry(i: int, e: object) -> dict:
    """A dags.json entry as dags.schema.json gives it, or a ValueError naming it."""
    if not isinstance(e, dict) or set(e) != ENTRY_KEYS:
        raise ValueError(f"dags.json entry {i}: keys {sorted(e) if isinstance(e, dict) else e!r}, expected {sorted(ENTRY_KEYS)}")
    tasks = e["tasks"]
    bad = [k for k, ok in (("dagId", DAG_ID.fullmatch(str(e["dagId"]))), ("product", PRODUCT.fullmatch(str(e["product"]))),
                           ("schedule", CRON.fullmatch(str(e["schedule"]))),
                           ("status", e["status"] in ("active", "paused", "deprecated", "retired")),
                           ("pipeline", e["pipeline"] == f"compiled/products/{e['product']}/pipeline.json"),
                           ("tasks", isinstance(tasks, list) and tasks[:1] == ["resolve"] and "record" in tasks
                            and tasks == [t for t in TASKS if t in tasks] and len(set(tasks)) == len(tasks)
                            and ("expire" not in tasks or "build" in tasks)))
           if not ok]
    if bad:
        raise ValueError(f"dags.json entry {i} ({e.get('dagId')!r}): bad {', '.join(bad)}")
    return e


def refresh_targets(entries: list[dict], docs: dict[str, dict]) -> dict[str, tuple[str, list[str]]]:
    """{product: (the DAG id a build degraded by one of its rules starts, the rules)}, or a ValueError
    naming the product: a target is another product of this dags.json whose own pipeline names no
    refresh target (one hop, no loop), and the rules a non-empty list of names."""
    dag_of, out = {e["product"]: e["dagId"] for e in entries}, {}
    for product, doc in docs.items():
        if (value := doc.get(REFRESH)) is None:
            continue
        target, rules = (value.get("product"), value.get("rules")) if isinstance(value, dict) else (None, None)
        if not (isinstance(rules, list) and rules and all(isinstance(r, str) and r for r in rules)):
            raise ValueError(f"{product}: refresh {value!r} is not {{product, rules}} with at least one rule name")
        if target == product or target not in dag_of:
            raise ValueError(f"{product}: refresh target {target!r} is not another product of this release's dags.json")
        if REFRESH in docs[target]:
            raise ValueError(f"{product}: refresh target {target} names its own refresh target "
                             f"{docs[target][REFRESH]!r}; a refresh is one hop, so a target triggers nothing")
        out[product] = (dag_of[target], rules)
    return out


def make_dag(e: dict, doc: dict, refresh: tuple[str, list[str]] | None) -> DAG:
    timeout = doc["schedule"].get("timeoutMinutes")
    kafka = (doc.get("connector") or {}).get("kind") == "kafka"
    with DAG(e["dagId"], schedule=e["schedule"], start_date=START, catchup=False, max_active_runs=1,
             dagrun_timeout=timedelta(minutes=timeout) if timeout else None,
             # Created paused, whatever its status: a DAG a release adds must not start under the deploy
             # that adds it (a Kafka product's first load reads its whole retained backlog, and a first
             # publish mid-verify fails the drift check). The operator unpauses it after
             # `make release-verify`; Airflow applies this at creation only, so an existing DAG keeps its state.
             is_paused_upon_creation=True, tags=[e["product"]]) as dag:
        product, tasks = e["product"], e["tasks"]
        ops = {"resolve": Resolve(task_id="resolve", product=product, kafka=kafka)}
        for t in tasks:
            if t in ("fetch", "build"):
                # build: no retry (its failures are deterministic), and none_failed, so it runs after
                # an unchanged fetch's skips; none_failed_min_one_success would skip it there, its
                # one direct upstream being skipped publish.
                ops[t] = PinnedOneShot(task_id=t, kind="fetch" if t == "fetch" else "dbt-runner", product=product,
                                       pool=POOLS[t], **({"retries": 0, "trigger_rule": TriggerRule.NONE_FAILED}
                                                         if t == "build" else  # a hung fetch is killed (on_kill)
                                                         {"execution_timeout": timedelta(minutes=timeout) if timeout
                                                          else FETCH_TIMEOUT}))
            elif t in POOLS:
                ops[t] = SparkTask(task_id=t, product=product, pool=POOLS[t], kafka=kafka)
        if kafka and "maintain" in tasks:  # W1-21: compaction of the landing and bronze, after maintain
            ops["compact"] = SparkTask(task_id="compact", product=product, pool=POOLS["compact"], kafka=kafka)
        ops["record"] = Record(task_id="record", product=product, kafka=kafka)
        last = ops["resolve"]
        for t in (t for t in DATA if t in tasks):
            last = last >> ops[t]
        last >> ops["record"]
        if "maintain" in tasks:
            ops["maintain"].trigger_rule = TriggerRule.ALL_DONE
            end = last >> ops["maintain"]
            if "compact" in ops:
                end = end >> ops["compact"]
            end >> ops["record"]
        if refresh:  # all_success: a degraded build publishes, so build succeeded
            if "build" not in ops:
                raise ValueError(f"{product}: a refresh target without a build task")
            ops["build"] >> Refresh(target=refresh[0], rules=refresh[1])
        if "expire" in ops:
            ops["expire"].as_teardown()
        ops["record"].as_teardown()
    return dag


def build(release: Path) -> dict[str, DAG]:
    entries = read_json(release / "compiled" / "airflow" / "dags.json")["dags"]
    if not isinstance(entries, list):
        raise ValueError("dags.json: dags is not a list")
    checked = [check_entry(i, e) for i, e in enumerate(entries)]  # every entry before any DAG
    docs = {e["product"]: read_json(release / e["pipeline"]) for e in checked}
    refresh = refresh_targets(checked, docs)
    return {e["dagId"]: make_dag(e, docs[e["product"]], refresh.get(e["product"])) for e in checked}


_parents = Path(__file__).resolve().parents  # a checkout mount (a release from before the run code) is shallower: no DAG
_release = _parents[4] if len(_parents) > 4 else None
if _release is not None and (_release / "manifest.json").is_file():
    globals().update(build(_release))

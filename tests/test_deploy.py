"""scripts/deploy.py (ADR-058) against a fake `docker` on PATH and releases compiled for real.

The fake plays compose and the engine: containers in a JSON state file, `compose up` recreating
one service on the image the override pins, `compose run tools k2tools ...` running the real
docker/tools/k2tools.py over the test's releases root (so the pointers really move), `exec` and
`kill` logged. Trino is the two functions deploy reaches it through, replaced in process: the
canary of the release the trino pointer names is admitted, every other refused; views and the tag
live in a dict. Every action, from either side, goes to one ordered timeline, so a test asserts
order: the view loses a dropped column before the pointer moves (review finding H2).

Each release is the k2gen good fixture set compiled by k2gen itself, with one change. The root
suite has neither k2gen nor bcrypt, so the module reruns itself in platform/k2gen's locked
environment with the bcrypt mk/release.mk pins. The proof on a real stack is
scripts/verify/deploy_rehearsal.py (`make deploy-rehearsal`).
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

try:
    import bcrypt  # noqa: F401
    import k2gen  # noqa: F401
except ImportError:
    if os.environ.get("K2_DEPLOY_TESTS_INNER"):  # the rerun itself lacks them: fail, never recurse
        raise
    def test_deploy_cases_in_the_k2gen_environment() -> None:
        # `python -m pytest`, not the `pytest` script: under `uv run --with`, the script runs the project's
        # own interpreter, which has no bcrypt; the overlay's python has both.
        p = subprocess.run(["uv", "run", "--project", "platform/k2gen", "--locked", "-q", "--with", "bcrypt==5.0.0",
                            "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(Path(__file__).relative_to(ROOT))],
                           cwd=ROOT, capture_output=True, text=True, env={**os.environ, "K2_DEPLOY_TESTS_INNER": "1"})
        assert p.returncode == 0, p.stdout[-8000:] + p.stderr[-2000:]
else:
    import yaml
    from k2gen import model
    from k2gen import release as k2release
    from k2gen.emit.lake import create_view, view_update

    sys.path.insert(0, str(ROOT / "platform/k2gen/tests"))
    import test_release
    from test_release import write_set  # the good fixture set as `k2gen docset` writes it

    def load(name: str, path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # dataclasses look their module up there
        spec.loader.exec_module(mod)
        return mod

    deploy = load("deploy", ROOT / "scripts/deploy.py")

    A = "alpine@sha256:" + "a" * 64
    B = "alpine@sha256:" + "b" * 64
    TRINO = "trinodb/trino@sha256:" + "c" * 64
    LK = "quay.io/lakekeeper/catalog@sha256:" + "d" * 64
    LK2 = "quay.io/lakekeeper/catalog@sha256:" + "e" * 64
    TOOLS = "tools:0123456789ab"
    VIEW = "sensor_net.station_readings.silver_readings"
    SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
    GOOD = ROOT / "platform/k2gen/tests/fixtures/v2/good"
    # The good set without ops-insight's daily-load (nothing reads it): the release after it ADDS a product,
    # whose builder and publisher the rules loaded before the swap do not know.
    DAILY_LOAD, NEW_VIEW = "contracts/ops-insight/daily-load", "ops_insight.daily_load.gold_daily_load"
    WITHOUT_DAILY_LOAD = {
        "tests/access/expectations.yaml": ["  - principal: svc.load-dashboard\n    table: ops_insight.daily_load.gold_daily_load\n"
                                           "    via: [sql]\n    visible: [load_date, station_id, readings, mean_value]\n"
                                           "    mustAppear: {minRows: 1}\n",
                                           "  - {principal: svc.load-dashboard, table: ops_insight.daily_load__base.gold_daily_load, denied: true}\n"],
        "contracts/access/principals.yaml": [f"  - id: {p}_daily_load\n    kind: service\n    owner: insight-team\n" for p in ("dbt", "pub")]}

    def compose_doc(standin: str = "alpine:3.20", lakekeeper: str = "quay.io/lakekeeper/catalog:v0.13.9") -> dict[str, Any]:
        return {"services": {
            "trino": {"image": "trinodb/trino:484", "restart": "unless-stopped"},
            "standin": {"image": standin, "restart": "unless-stopped"},
            "lakekeeper": {"image": lakekeeper, "restart": "unless-stopped"},
            "lake-init": {"image": "alpine:3.20", "restart": "no"},
            "clickhouse": {"image": "clickhouse/clickhouse-server:24.3", "profiles": ["hot-gold"], "restart": "unless-stopped"},
            "tools": {"image": "tools:local", "profiles": ["tools"], "restart": "no"},
        }}

    FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time, uuid
A, E = sys.argv[1:], os.environ
S = json.loads(open(E["FAKE_STATE"]).read())
def save(): open(E["FAKE_STATE"], "w").write(json.dumps(S))
def log(*what):
    with open(E["FAKE_LOG"], "a") as f: f.write(json.dumps([time.time_ns(), *what]) + "\n")
def inspect(c):
    return {"Id": c["id"], "Created": c["created"], "Image": c["image"], "Mounts": [{"Destination": "/k2/releases"}],
            "Config": {"Labels": {"com.docker.compose.project": "k2", "com.docker.compose.service": c["service"]}},
            "State": {"Status": c["status"], "Running": c["status"] == "running", "Restarting": False, "ExitCode": 0}}
if A[:2] == ["ps", "-aq"]:
    print("\n".join(c["id"] for c in S["containers"])); sys.exit(0)
if A[0] == "inspect":
    print(json.dumps([inspect(c) for c in S["containers"] if c["id"] in A[1:]])); sys.exit(0)
if A[:2] == ["image", "inspect"]:
    ref = A[-1]
    if ref not in S["images"]: sys.exit(1)
    print(S["images"][ref]); sys.exit(0)
if A[0] == "pull":
    S["images"][A[-1]] = "sha256:" + A[-1][-64:]; save(); log("pull", A[-1]); sys.exit(0)
if A[0] == "run":  # the dbt docs one-shot, by plain docker run
    log("run", *A[1:]); sys.exit(S.get("run_rc", 0))
if A[0] == "kill":
    log("kill", A[2]); sys.exit(0)
if A[0] == "exec":
    if A[2] == "sh":  # the landing listing: the answer the state holds, as the MinIO shell gives it
        print(S.get("mc_out", "")); sys.exit(S.get("mc_rc", 0))
    if A[2] == "readlink":
        print("\n".join(os.readlink(p.replace("/k2/releases", E["FAKE_ROOT"])) for p in A[3:])); sys.exit(0)
    if A[2] == "airflow":  # Airflow 3.3.2's CLI: piped -o json is one line, every value a string
        F, argv = S["airflow"], A[3:]
        prompts = argv[:2] in (["dags", "pause"], ["dags", "unpause"]) and argv[-1:] != ["-y"]  # the real CLI would ask on a pipe
        argv = [a for a in argv if a != "-y"]
        log("airflow", *argv)
        if prompts:
            sys.exit(1)
        if argv == ["dags", "list", "-o", "json"]:
            print(json.dumps([{"dag_id": d, "fileloc": "/x/factory.py", "is_paused": str(p)} for d, p in sorted(F["paused"].items())]))
        elif argv[:2] in (["dags", "pause"], ["dags", "unpause"]) and len(argv) == 3:
            if argv[2] in F["paused"]:
                F["paused"][argv[2]] = argv[1] == "pause"
        elif argv[:2] == ["dags", "list-runs"] and argv[3:] == ["--state", "running", "-o", "json"]:
            # a paused DAG's run stays running, its later tasks unscheduled, after its task is done
            print(json.dumps([{"dag_id": argv[2], "run_id": "scheduled__1", "state": "running"}] if argv[2] in F["busy"] else []))
        elif argv[:2] == ["tasks", "states-for-dag-run"] and argv[3:] == ["scheduled__1", "-o", "json"]:
            n = F["busy"][argv[2]]  # its build runs for n more polls; -1: for ever
            F["busy"][argv[2]] = n - 1 if n > 0 else n
            print(json.dumps([{"dag_id": argv[2], "task_id": "resolve", "state": "success"},
                              {"dag_id": argv[2], "task_id": "build", "state": "running" if n else "success"}]))
        else:
            sys.exit(f"fake airflow: no such command {argv}")
        save(); sys.exit(0)
    log("exec", *A[2:4]); sys.exit(0)
if A[:2] == ["network", "inspect"]:
    sys.exit(0 if A[-1] in S.get("networks", {}) else 1)
if A[:2] == ["network", "create"]:
    labels = dict(A[i + 1].split("=", 1) for i, a in enumerate(A) if a == "--label")
    S.setdefault("networks", {})[A[-1]] = {"driver": A[A.index("--driver") + 1], "labels": labels}
    save(); log("network", *A[1:]); sys.exit(0)
if A[:2] == ["network", "ls"]:  # --filter label=com.docker.compose.project=k2 --format <its version label>
    print("\n".join(n["labels"].get("com.docker.compose.version", "") for n in S.get("networks", {}).values())); sys.exit(0)
assert A[0] == "compose", A
files = [A[i + 1] for i, a in enumerate(A) if a == "-f"]
pinned = json.loads(open(files[1]).read())["services"]
rest = A[A.index(files[1]) + 1:]
if rest[0] in ("--profile", "rm", "up"):
    log("compose", *rest)  # the exact command, for the tests that assert it
while rest[0] in ("--profile", "--all-resources"):
    rest = rest[2:] if rest[0] == "--profile" else rest[1:]
if rest[0] == "config":
    if "json" in rest:  # rendered: the World's k2-api port, and its networks
        print(json.dumps(S["rendered"]))
    sys.exit(0)
if rest[0] == "rm":
    S["containers"] = [c for c in S["containers"] if c["service"] != rest[-1]]
    save(); sys.exit(0)
if rest[0] == "run":
    i = rest.index("tools")
    if rest[i + 1] != "k2tools":  # another entry in tools: the catalog site's python3
        log("tools-run", *rest[i + 1:]); sys.exit(S.get("tools_rc", 0))
    argv = rest[i + 2:]
    if argv[0] in ("swap", "pointers"):
        log("tools", *argv)
        out = subprocess.run([sys.executable, E["K2TOOLS"], *argv], env={**E, "K2_RELEASES_MOUNT": E["FAKE_ROOT"]},
                             capture_output=True, text=True)
        sys.stdout.write(out.stdout); sys.stderr.write(out.stderr); sys.exit(out.returncode)
    if argv[0] == "table":
        print(json.dumps(S["tables"].get(".".join(argv[-3:])))); sys.exit(0)
    log("tools", *argv); sys.exit(0)
if rest[0] == "up":
    svc = rest[-1]
    S["containers"] = [c for c in S["containers"] if c["service"] != svc]
    oneshot = svc in S.get("oneshots", [])
    S["containers"].append({"id": uuid.uuid4().hex, "service": svc, "created": f"{time.time_ns()}",
                            "image": S["images"][pinned[svc]["image"]], "status": "exited" if oneshot else "running"})
    save(); log("up", svc, pinned[svc]["image"], E.get("K2_LAKE_INIT_RELEASE", "")); sys.exit(0)
sys.exit(f"fake docker: no answer for {A}")
'''

    class World:
        """A releases root, a secrets directory, the fake docker's state, and Trino in process."""

        def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch):
            self.tmp, self.root, self.secrets = tmp, tmp / "releases", tmp / "secrets"
            self.state, self.log = tmp / "state.json", tmp / "timeline.jsonl"
            (tmp / "bin").mkdir()
            (tmp / "bin/docker").write_text(FAKE_DOCKER)
            (tmp / "bin/docker").chmod(0o755)
            monkeypatch.setenv("PATH", f"{tmp / 'bin'}:{os.environ['PATH']}")
            for k, v in {"FAKE_STATE": self.state, "FAKE_LOG": self.log, "FAKE_ROOT": self.root,
                         "K2TOOLS": ROOT / "docker/tools/k2tools.py", "K2_BACKUP_DIR": tmp / "backup"}.items():
                monkeypatch.setenv(k, str(v))
            images = {r: "sha256:" + r[-64:] for r in (A, B, TRINO, LK, LK2)} | {TOOLS: "sha256:0123456789ab" + "0" * 52}
            # k2-api on a loopback port of this test: /v1/release names the release its pointer names,
            # or another (api_says), or answers 503 (api_says = "down")
            self.api_says: str | None = None
            world = self

            class Api(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    try:
                        rid = world.api_says or os.readlink(world.root / "pointers/k2-api").removeprefix("../")
                    except OSError:
                        rid = "down"
                    body = json.dumps({"release": rid}).encode()
                    self.send_response(503 if rid == "down" else 200)
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *a: Any) -> None:
                    pass

            self.api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
            threading.Thread(target=self.api.serve_forever, daemon=True).start()
            port = {"host_ip": "127.0.0.1", "target": 8095, "published": str(self.api.server_address[1]), "protocol": "tcp"}
            self.write({"containers": [], "images": images, "tables": {}, "oneshots": ["lake-init"],
                        "rendered": {"services": {"k2-api": {"ports": [port]}}, "networks": {}}})
            monkeypatch.setattr(deploy, "API_WAIT_S", 0)
            for f in ("tls/ca.pem", "trino/password.db", "tls/trino.pem", "principals/pub_station_readings.password",
                      "principals/svc.platform.password", *(f"principals/{p}.password" for p in (
                          "dbt_station_readings", "dbt_daily_load", "pub_daily_load", "svc.runs"))):  # the good set's builds
                (self.secrets / f).parent.mkdir(parents=True, exist_ok=True)
                (self.secrets / f).write_text("svc.platform:$2y$10$" + "x" * 53 + "\n" if f.endswith(".db") else f"fixture {f}\n")
            self.views: dict[str, list[str]] = {}
            self.tag = {"station_id": "varchar", "read_at": "timestamp(6) with time zone", "value": "decimal(12,3)",
                        "region": "varchar", "operator_note": "varchar"}
            monkeypatch.setattr(deploy, "admitted", self.admitted)
            monkeypatch.setattr(deploy, "sql", self.sql)
            # S20's suites run on throwaway lakes and the live Trino; here they answer as told
            self.suite: list[str] = []
            self.s20: Any = lambda rel: (["drift clean"], [])
            monkeypatch.setattr(deploy, "release_suite", lambda ctx, rel: self.suite.append(rel.id) or ["suite passed"])
            self.real_s20 = deploy.s20_checks
            monkeypatch.setattr(deploy, "s20_checks", lambda ctx, rel: self.s20(rel))
            monkeypatch.setattr(deploy, "secrets_health", lambda ctx: None)  # its own test below
            self.fence_rc = 0  # scripts/verify/catalog-fence.sh, as verify runs it
            monkeypatch.setattr(deploy, "catalog_fence", lambda ctx: subprocess.CompletedProcess(
                [], self.fence_rc, f"catalog-fence: 3 PASS, {self.fence_rc} FAIL\n", ""))
            self.down = False  # Trino gives no answer until a compose up recreates it
            # each release's own emitter runs in its tools image; here, k2gen's in process
            self.shaped: list[str] = []
            monkeypatch.setattr(deploy, "statements", lambda ctx, rel, v, old, new: self.shaped.append(rel.id) or view_update(v, old, new))
            monkeypatch.setattr(deploy, "REFRESH_S", 0)
            monkeypatch.setattr(deploy, "DRAIN_POLL_S", 0)
            self.ctx = deploy.Ctx(root=self.root, secrets=self.secrets, home=ROOT, project="k2", trino="https://localhost:1",
                                  operator="tester", faults=set(), tmp=tmp, say=lambda line: None)
            self.n = 0

        def read(self) -> dict[str, Any]:
            return json.loads(self.state.read_text())

        def write(self, s: dict[str, Any]) -> None:
            self.state.write_text(json.dumps(s))

        def event(self, *what: Any) -> None:
            with open(self.log, "a") as f:
                f.write(json.dumps([time.time_ns(), *what]) + "\n")

        def timeline(self) -> list[list[Any]]:
            return [json.loads(x)[1:] for x in self.log.read_text().splitlines()] if self.log.exists() else []

        def release(self, compose: dict[str, Any] | None = None, over: dict[str, bytes] | None = None,
                    images: dict[str, str] | None = None, lake_init: bool = True, without_daily_load: bool = False) -> Any:
            """A sealed release of the good set with `over` laid on it, compiled against the newest one;
            lake_init: it carries docker/lake/init-lake.sh, as every release since review R6 does;
            without_daily_load: the set without that product, its two access rows and its principals."""
            self.n += 1
            over = ({"docker/lake/init-lake.sh": b"#!/bin/bash\n"} if lake_init else {}) | (over or {})
            if without_daily_load:
                test_release.GOOD = self.tmp / f"good{self.n}"
                shutil.copytree(GOOD, test_release.GOOD)
                shutil.rmtree(test_release.GOOD / DAILY_LOAD)
                for path, cut in WITHOUT_DAILY_LOAD.items():
                    text = (test_release.GOOD / path).read_text()
                    assert all(c in text for c in cut), path
                    (test_release.GOOD / path).write_text(re.sub("|".join(map(re.escape, cut)), "", text))
            try:
                set_dir = write_set(self.tmp / f"set{self.n}", compose or compose_doc(), over)
            finally:
                test_release.GOOD = GOOD
            refs = {"trino": TRINO, "standin": A, "lakekeeper": LK, "lake-init": A, "tools": TOOLS} | (images or {})
            (self.tmp / f"images{self.n}.json").write_text(json.dumps(refs))
            kept = sorted((p for p in self.root.glob("v*")), key=lambda p: p.stat().st_mtime) if self.root.exists() else []
            rid = k2release.release(set_dir, kept[-1] if kept else None, self.tmp / f"images{self.n}.json", self.root,
                                    set_dir / model.COMPOSE)
            return deploy.open_release(self.ctx, rid)

        def live(self, rel: Any) -> None:
            """`rel` deployed and good: pointers, its containers, a done record, its view published."""
            (self.root / "pointers").mkdir(exist_ok=True)
            for p in deploy.POINTERS:
                (self.root / "pointers" / p).unlink(missing_ok=True)
                (self.root / "pointers" / p).symlink_to(f"../{rel.id}")
            s = self.read()
            s["containers"] = [{"id": f"c-{svc}", "service": svc, "created": "0", "image": s["images"][ref], "status": "running"}
                               for svc, ref in rel.manifest.images.items() if svc not in ("tools", "lake-init")]
            self.write(s)
            deploy.record(self.ctx, rel, None, "done", "ok", set())
            v = deploy.views_of(rel)[VIEW]
            self.views[VIEW] = [c for c in v.columns if c in self.tag]
            self.stored = deploy.view_body(create_view(v, self.views[VIEW])).upper()  # stored reformatted

        def admitted(self, ctx: Any, user: str) -> bool | None:
            live = deploy.k2release.read(self.root / "pointers" / "trino")
            return user == model.canary_user(live.manifest.set_sha256)

        def knows(self, user: str) -> bool:
            """Trino's `queries` rules: a principal no rule of the loaded release lets execute is refused
            every statement, as the real one refuses a product's publisher under a release without it."""
            try:
                loaded = deploy.k2release.read(self.root / "pointers" / "trino")
            except (ValueError, OSError):
                return True  # no release loaded yet
            rules = json.loads((loaded.root / "compiled/trino/rules.json").read_text())
            return any(re.fullmatch(r["user"], user) for r in rules["queries"] if "execute" in r["allow"])

        def sql(self, ctx: Any, user: str, statement: str) -> dict[str, Any]:
            if self.down and "trino" not in self.ups():
                raise deploy.k2tools.Unreachable("Connection refused")
            if not self.knows(user):
                raise deploy.k2tools.TrinoError("PERMISSION_DENIED", "Access Denied: Cannot execute query")
            mine = "'silver_readings'" in statement  # VIEW's: no other view is published here
            if statement.startswith("SELECT column_name"):
                return {"columns": [["column_name", "varchar"]], "rows": [[c] for c in self.views.get(VIEW, [])] if mine else []}
            if statement.startswith("SELECT * FROM"):
                return {"columns": [[c, t] for c, t in self.tag.items()], "rows": []}
            if statement.startswith("SELECT view_definition"):
                return {"columns": [["view_definition", "varchar"]], "rows": [[self.stored]] if VIEW in self.views and mine else []}
            if statement.startswith("CREATE OR REPLACE VIEW"):
                self.stored = statement.split(" SECURITY DEFINER AS ", 1)[1].replace(", ", "\n, ")  # Trino reformats
                cols = self.stored.split("SELECT ", 1)[1].split(" FROM ")[0]
                self.views[VIEW] = [c.strip().strip('"') for c in cols.replace("\n", "").split(",")]
                self.event("view", self.views[VIEW])
                return {"columns": [], "rows": []}
            self.event("sql", user, statement[:40])
            return {"columns": [], "rows": []}

        def deploy(self, rel: Any, *faults: str, allow: set[str] | None = None) -> int:
            self.ctx.faults, self.ctx.allow_stateful = set(faults), allow or set()
            with deploy.lock(self.root):
                return deploy.deploy(self.ctx, rel.id, plan_only=False)

        def ups(self) -> list[str]:
            return [e[1] for e in self.timeline() if e[0] == "up"]

        def scheduler(self, paused: dict[str, bool], busy: dict[str, int] | None = None) -> None:
            """Airflow's scheduler running, with these DAGs (True: paused) and these runs' builds running."""
            s = self.read()
            s["containers"].append({"id": "c-airflow-scheduler", "service": "airflow-scheduler", "created": "0",
                                    "image": "x", "status": "running"})
            self.write(s | {"airflow": {"paused": paused, "busy": busy or {}}})

        def paused(self) -> dict[str, bool]:
            return dict(self.read()["airflow"]["paused"])

        def airflow_calls(self) -> list[list[str]]:
            return [e[1:] for e in self.timeline() if e[0] == "airflow"]

        def image_of(self, svc: str) -> str:
            return next(c["image"] for c in self.read()["containers"] if c["service"] == svc)

    EXPECT = "tests/access/expectations.yaml"

    def policy(minutes: int) -> dict[str, bytes]:
        """The delayed reader's delay tightened: a major version, and an expectation that asserts it."""
        return {SILVER: (GOOD / SILVER).read_bytes().replace(b"minutes: 20}", f"minutes: {minutes}}}".encode())
                .replace(b"\nversion: 1.0.0\n", b"\nversion: 2.0.0\n"),
                EXPECT: (GOOD / EXPECT).read_bytes().replace(b"minRowAgeMinutes: 20", f"minRowAgeMinutes: {minutes}".encode())
                } | bumped_product("2.0.0")

    def bumped_product(version: str, reader_version: str = "1.0.1") -> dict[str, bytes]:
        """station-readings and its silver port at `version`, and daily-load's input port acknowledging it."""
        path = "contracts/sensor-net/station-readings/product.odps.yaml"
        reader = "contracts/ops-insight/daily-load/product.odps.yaml"
        port = f"  - name: silver_readings\n    version: {version}".encode()
        text = (GOOD / path).read_bytes().replace(b"\nversion: 1.0.0\n", f"\nversion: {version}\n".encode(), 1)
        return {path: text.replace(b"  - name: silver_readings\n    version: 1.0.0", port),
                reader: (GOOD / reader).read_bytes().replace(b"  - name: silver_readings\n    version: 1.0.0", port)
                .replace(b"\nversion: 1.0.0\n", f"\nversion: {reader_version}\n".encode(), 1)}

    def dropped_note() -> dict[str, bytes]:
        """operator_note gone from the silver contract, its mask, its model and its expectations: a major."""
        contract = ((GOOD / SILVER).read_bytes()
                    .replace(b"      - name: operator_note\n        physicalType: varchar\n        description: Free text the made-up operator attached.\n", b"")
                    .replace(b"        mask: [operator_note]\n", b"").replace(b"\nversion: 1.0.0\n", b"\nversion: 2.0.0\n"))
        sql = "contracts/sensor-net/station-readings/models/silver_readings.sql"
        unit = "contracts/sensor-net/station-readings/unit_tests.yml"
        expect = EXPECT
        e = (GOOD / expect).read_text().replace("[station_id, read_at, value, region, operator_note]", "[station_id, read_at, value, region]")
        return {SILVER: contract, sql: (GOOD / sql).read_bytes().replace(b"  region,\n  operator_note\n", b"  region\n"),
                unit: (GOOD / unit).read_bytes().replace(b"value: 1.5, region: north, operator_note: synthetic}", b"value: 1.5, region: north}"),
                expect: e.replace("\n    masked: [operator_note]", "").encode()} | bumped_product("2.0.0")

    @pytest.fixture
    def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
        w = World(tmp_path, monkeypatch)
        yield w
        w.api.shutdown()

    def records(w: World) -> list[dict[str, Any]]:
        return deploy.history(w.root)

    # --- the plan ---------------------------------------------------------------------

    def test_standing_is_the_newest_done_and_every_attempt_since() -> None:
        r = [{"release": "a", "from": None, "step": "done", "outcome": "ok"},
             {"release": "b", "from": "a", "step": "swap", "outcome": "ok"},
             {"release": "b", "from": "a", "step": "verify", "outcome": "failed"},
             {"release": "a", "from": "b", "step": "rollback-done", "outcome": "ok"},
             {"release": "c", "from": "a", "step": "canary", "outcome": "ok"}]
        assert deploy.standing(r) == (["a", "a"], ["c", "a"])
        assert deploy.standing(r[:4]) == (["a", "a"], [])
        assert deploy.standing([]) == ([], [])
        planned = r[:4] + [{"release": "c", "from": "a", "step": s, "outcome": "ok"} for s in ("preflight", "images", "plan")]
        assert deploy.standing(planned) == (["a", "a"], [])  # PLAN=1 moved nothing: no unfinished attempt

    def test_make_rollback_resumes_an_interrupted_rollback_and_undoes_an_interrupted_deploy(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=policy(60))
        assert world.deploy(r2) == 0
        deploy.record(world.ctx, r1, r2.id, "rollback-swap", "ok", set())  # a make rollback to r1, interrupted
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}"
        deploy.record(world.ctx, r2, r1.id, "swap", "ok", set())  # a deploy of r2, interrupted
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}"

    def test_a_rules_only_release_recreates_nothing(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=policy(60))
        p = deploy.make_plan(world.ctx, r2, [r1])
        assert (p.recreate, p.oneshots, p.trino_restart, p.ddl) == ([], [], False, [])
        assert world.deploy(r2) == 0
        assert world.ups() == [] and os.readlink(world.root / "pointers/trino") == f"../{r2.id}"
        assert [r["step"] for r in records(world)][-2:] == ["done", "site"]

    def test_one_image_changed_recreates_only_its_service(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r3 = world.release(compose=compose_doc(standin="alpine:3"), images={"standin": B})
        assert deploy.make_plan(world.ctx, r3, [r1]).recreate == ["standin"]
        assert world.deploy(r3) == 0
        assert world.ups() == ["standin"] and world.image_of("standin") == "sha256:" + "b" * 64

    def test_a_forced_mismatch_restores_the_previous_release_images_included_and_exits_1(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r3 = world.release(compose=compose_doc(standin="alpine:3"), images={"standin": B})
        assert world.deploy(r3, "verify-mismatch") == 1
        assert world.ups() == ["standin", "standin"]
        assert world.image_of("standin") == "sha256:" + "a" * 64  # R-1's image, not only its configuration
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}"
        steps = [(r["step"], r["outcome"]) for r in records(world)]
        assert ("verify", "failed") in steps and steps[-2:] == [("rollback-done", "ok"), ("rollback-site", "ok")]
        assert deploy.standing(records(world))[0][-1] == r1.id

    def test_rollback_goes_back_to_the_previous_good_release(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r3 = world.release(compose=compose_doc(standin="alpine:3"), images={"standin": B})
        assert world.deploy(r3) == 0
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}" and world.image_of("standin") == "sha256:" + "a" * 64

    def test_a_rerun_after_an_unfinished_attempt_recreates_what_the_attempt_moved(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r3 = world.release(compose=compose_doc(standin="alpine:3"), images={"standin": B})
        deploy.record(world.ctx, r3, r1.id, "recreate", "ok", set())  # an attempt at r3 that never reached done
        r2 = world.release(over=policy(60))  # compiled after r3: the standin is A again in r2
        p = deploy.make_plan(world.ctx, r2, [r1, r3])
        assert p.recreate == ["standin"], "r2 equals r1's standin, but the attempt at r3 may have moved it"

    def test_a_changed_stateful_service_is_refused_unless_allowed(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r5 = world.release(compose=compose_doc(lakekeeper="quay.io/lakekeeper/catalog:v0.14.0"), images={"lakekeeper": LK2})
        with pytest.raises(deploy.Refused, match="ALLOW_STATEFUL=lakekeeper"):  # main() turns it into exit 2
            world.deploy(r5)
        assert world.ups() == [] and records(world)[-1]["step"] == "preflight" and records(world)[-1]["outcome"] == "failed"
        assert world.deploy(r5, allow={"lakekeeper"}) == 0 and world.ups() == ["lakekeeper"]

    def test_profiled_services_not_running_are_never_started_and_one_shots_run_to_their_exit(world: World) -> None:
        r1 = world.release()
        p = deploy.make_plan(world.ctx, r1, [None])
        assert "clickhouse" not in p.recreate and any(s.startswith("clickhouse: profile hot-gold") for s in p.skipped)
        assert "tools" not in p.recreate and p.oneshots == ["lake-init"]
        assert p.recreate[0] == "trino" and set(p.recreate) == {"trino", "standin"}  # lakekeeper held: never recreated

    # --- the views around the rules (review finding H2) ------------------------------

    def test_a_dropped_masked_column_leaves_the_view_before_the_pointer_moves(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        assert "operator_note" in world.views[VIEW]
        r2 = world.release(over=dropped_note())
        assert [v.relation for v in deploy.make_plan(world.ctx, r2, [r1]).views] == [VIEW]
        assert world.deploy(r2) == 0
        t = world.timeline()
        narrowed = next(i for i, e in enumerate(t) if e[0] == "view" and "operator_note" not in e[1])
        swapped = next(i for i, e in enumerate(t) if e[:2] == ["tools", "swap"])
        assert narrowed < swapped, t
        assert world.views[VIEW] == ["station_id", "read_at", "value", "region"]

    def test_a_column_coming_back_joins_the_view_only_after_its_rules_are_loaded(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=dropped_note())
        assert world.deploy(r2) == 0
        world.log.unlink()
        loaded = []
        real = world.admitted
        world.admitted = lambda ctx, user: (loaded.append(user) or real(ctx, user))  # type: ignore[method-assign]
        deploy.admitted = world.admitted
        assert world.deploy(r1) == 0  # forward to r1's contract: operator_note, masked again
        t = world.timeline()
        swapped = next(i for i, e in enumerate(t) if e[:2] == ["tools", "swap"])
        widened = next(i for i, e in enumerate(t) if e[0] == "view" and "operator_note" in e[1])
        assert swapped < widened and loaded, t
        assert all(e[0] != "view" or "operator_note" not in e[1] for e in t[:swapped])

    def test_a_view_shape_the_source_rules_refuse_is_narrowed_in_the_source_shape(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=dropped_note())
        real = world.sql

        def old_rules(ctx: Any, user: str, statement: str) -> dict[str, Any]:  # the target's shape needs a grant r1 lacks
            if statement.startswith("CREATE OR REPLACE VIEW") and os.readlink(world.root / "pointers/trino") == f"../{r1.id}" \
                    and "target-shape" in statement:
                raise deploy.k2tools.TrinoError("PERMISSION_DENIED", "Access Denied: Cannot create view")
            return real(ctx, user, statement)

        deploy.sql = old_rules
        shapes, asked = {r1.id: "source-shape", r2.id: "target-shape"}, []
        deploy.statements = lambda ctx, rel, v, old, new: asked.append(rel.id) or tuple(  # type: ignore[assignment]
            x and x.replace("SELECT ", f"SELECT /* {shapes[rel.id]} */ ", 1) for x in view_update(v, old, new))
        assert world.deploy(r2) == 0
        assert asked[:2] == [r2.id, r1.id], asked  # the target's shape refused before the swap: the source's asked for
        t = world.timeline()
        swapped = next(i for i, e in enumerate(t) if e[:2] == ["tools", "swap"])
        narrowed = [i for i, e in enumerate(t) if e[0] == "view" and "operator_note" not in e[1]]
        assert narrowed and narrowed[0] < swapped, t  # still before the rules change
        assert "target-shape" in world.stored, "after the rules load, the target's shape"

    # --- refusals, the canary, the log --------------------------------------------------

    def test_a_second_deploy_is_refused_while_one_holds_the_lock(world: World) -> None:
        with deploy.lock(world.root):
            with pytest.raises(deploy.Refused, match="another deploy holds"):
                with deploy.lock(world.root):
                    pass

    def test_a_release_whose_backup_set_drops_a_restore_table_storage_still_holds_is_refused(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1 = world.release()
        old = deploy.backup_entries(r1)
        assert {("table", "k2.station_readings.landing_files"), ("landing", "k2-landing", "sensor-net/station-readings/")} <= old
        assert ("table", "k2.station_readings.bronze_readings") not in old  # rebuild mode: nothing to lose
        s = world.read()
        s["tables"]["k2.station_readings.landing_files"] = {"columns": [], "partitioning": []}  # still in the catalog
        world.write(s)
        assert deploy.dropped_storage(world.ctx, r1, r1) == []
        real = deploy.backup_entries
        monkeypatch.setattr(deploy, "backup_entries", lambda rel: real(rel) if rel is r1 else set())
        held = deploy.dropped_storage(world.ctx, r1, world.release())
        assert held == ["landing s3://k2-landing/sensor-net/station-readings/ (MinIO not running, so not proven empty)",
                        "table k2.station_readings.landing_files"], held  # audit_checks is gone from the catalog: allowed
        monkeypatch.setattr(deploy, "required_secrets", lambda ctx, p: [])
        with pytest.raises(deploy.Refused, match="make drop TARGET"):
            deploy.preflight(world.ctx, deploy.make_plan(world.ctx, world.release(), [r1]))

    @pytest.mark.parametrize("rc, out, held", [(0, "", False), (0, "held", True), (3, "", True), (1, "", True)])
    def test_a_dropped_landing_prefix_is_allowed_only_when_minio_lists_it_empty(
            world: World, monkeypatch: pytest.MonkeyPatch, rc: int, out: str, held: bool) -> None:
        r1 = world.release()
        s = world.read()
        s["containers"].append({"id": "c-minio", "service": "minio", "created": "0", "image": "x", "status": "running"})
        s |= {"mc_rc": rc, "mc_out": out}
        world.write(s)
        real = deploy.backup_entries
        monkeypatch.setattr(deploy, "backup_entries", lambda rel: real(rel) if rel is r1 else
                            {e for e in real(r1) if e[0] != "landing"})
        found = deploy.dropped_storage(world.ctx, r1, world.release())
        assert bool(found) is held and all(f.startswith("landing s3://k2-landing/") for f in found), found

    def test_the_canary_line_is_written_in_place_and_the_others_pruned(world: World) -> None:
        db = world.secrets / "trino/password.db"
        inode = db.stat().st_ino
        deploy.canary_write(world.ctx, "k2_release_aaaaaaaaaaaa")
        deploy.canary_write(world.ctx, "k2_release_bbbbbbbbbbbb")
        deploy.canary_write(world.ctx, "k2_release_bbbbbbbbbbbb")  # a rerun replaces its own line
        users = [x.split(":")[0] for x in db.read_text().splitlines() if x.strip()]  # a removed line is a blank slot
        assert users == ["svc.platform", "k2_release_aaaaaaaaaaaa", "k2_release_bbbbbbbbbbbb"] and db.stat().st_ino == inode
        assert all(len(x) == deploy.ps.SLOT - 1 for x in db.read_text().splitlines()) and not deploy.ps.problems(db.read_text())
        assert oct((world.secrets / "canary/k2_release_bbbbbbbbbbbb.password").stat().st_mode & 0o777) == "0o600"
        assert deploy.canary_prune(world.ctx, "k2_release_bbbbbbbbbbbb") == ["k2_release_aaaaaaaaaaaa"]
        assert [x.split(":")[0] for x in db.read_text().splitlines() if x.strip()] == ["svc.platform", "k2_release_bbbbbbbbbbbb"]
        assert not (world.secrets / "canary/k2_release_aaaaaaaaaaaa.password").exists() and db.stat().st_ino == inode

    def test_every_record_meets_its_schema_and_holds_checksums_never_content(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        assert world.deploy(world.release(over=policy(60))) == 0
        lines = (world.root / model.DEPLOY_LOG).read_text().splitlines()
        assert len(lines) > 10 and all(model.schema_errors("deploy-record", json.loads(x)) == [] for x in lines)
        text = "\n".join(lines)
        assert "fixture" not in text and "$2y$" not in text
        last = json.loads(lines[-1])["secrets"]
        db = (world.secrets / "trino/password.db").stat()
        assert last["trino/password.db"] == {"mode": f"{db.st_mode & 0o777:04o}", "size": db.st_size}
        assert not any(len(str(v)) == 64 for r in lines for v in json.loads(r)["secrets"].values())  # no digest

    def test_an_unsealed_or_altered_release_is_refused(world: World) -> None:
        r1 = world.release()
        (r1.root / "manifest.json").chmod(0o644)
        with pytest.raises(deploy.Refused, match="not sealed"):
            deploy.open_release(world.ctx, r1.id)
        (r1.root / "manifest.json").chmod(0o444)
        target = r1.root / model.VIEWS
        target.chmod(0o644)
        target.write_text("{}")
        target.chmod(0o444)
        with pytest.raises(deploy.Refused, match="files differ"):
            deploy.open_release(world.ctx, r1.id)

    def test_the_release_suite_runs_at_preflight_and_a_drift_found_at_verify_rolls_back_with_its_reason(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=policy(60))
        world.s20 = lambda rel: ([], ["catalog drift check exit 1: DRIFT view governance.x: not in the release"]) \
            if rel.id == r2.id else (["drift clean"], [])  # a view planted while r2 was live
        assert world.deploy(r2) == 1 and world.suite == [r2.id]
        failed = [r for r in records(world) if r["outcome"] == "failed"]
        assert [r["step"] for r in failed] == ["verify"] and "DRIFT view governance.x" in failed[0]["reason"]
        assert all(model.schema_errors("deploy-record", r) == [] for r in records(world))
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}"

    def test_a_release_failing_its_release_time_suite_is_refused_before_anything_moves(world: World,
                                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
        r1 = world.release()
        world.live(r1)
        r2 = world.release(over=policy(60))

        def failing(ctx: Any, rel: Any) -> list[str]:
            raise deploy.Refused("the release fails its access suite at release time (make access-verify exit 1)")

        monkeypatch.setattr(deploy, "release_suite", failing)
        with pytest.raises(deploy.Refused, match="access suite"):
            world.deploy(r2)
        assert world.ups() == [] and [e for e in world.timeline() if e[:2] == ["tools", "swap"]] == []
        assert records(world)[-1]["step"] == "preflight" and "access suite" in records(world)[-1]["reason"]

    @pytest.mark.parametrize("exit_, pending, live_runs, wrong", [
        (0, [], True, False), (0, ["governance.access-canary"], False, False), (1, [], True, True), (2, [], True, True)])
    def test_live_access_verify_runs_only_once_every_product_is_published(world: World, monkeypatch: pytest.MonkeyPatch,
                                                                          exit_: int, pending: list[str], live_runs: bool,
                                                                          wrong: bool) -> None:
        monkeypatch.undo()  # the real s20_checks, with its two commands answered here
        r1 = world.release()
        calls: list[list[str]] = []
        monkeypatch.setattr(deploy, "drift", lambda ctx, rel: {"exit": exit_, "doc": {"pending": pending}, "text": "x"})
        monkeypatch.setattr(deploy, "run", lambda *a, **k: calls.append(list(a)) or subprocess.CompletedProcess(a, 0, "", ""))
        monkeypatch.setenv("K2_SPARK_S3_SECRET_KEY", "a-secret-of-this-test")
        lines, bad = deploy.s20_checks(world.ctx, r1)
        live = [c for c in calls if "/opt/k2/scripts/access_verify.py" in c and "live" in c]
        assert bool(live) is live_runs and bool(bad) is wrong, (lines, bad)
        for c in live:  # a tools one-shot; each engine's pair by -e name only, never its value on the command line
            assert c[:2] == ["docker", "compose"] and "tools" in c and "a-secret-of-this-test" not in " ".join(c)
            assert ["-e", "K2_SPARK_S3_SECRET_KEY"] == c[c.index("K2_SPARK_S3_SECRET_KEY") - 1:c.index("K2_SPARK_S3_SECRET_KEY") + 1]
        if pending:
            assert any(x.startswith("SKIP access-verify LIVE=1") and "governance.access-canary" in x for x in lines)

    def test_live_access_verify_mounts_each_operator_key_file_by_name_and_nothing_of_api(world: World,
                                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.undo()
        r1 = world.release()
        for f in ("api-keys/p.b.key", "api-keys/p.a.key", "api/keys.json", "api/p.old.key"):
            (world.secrets / f).parent.mkdir(parents=True, exist_ok=True)
            (world.secrets / f).write_text("fixture\n")
        calls: list[list[str]] = []
        monkeypatch.setattr(deploy, "run", lambda *a, **k: calls.append(list(a)) or subprocess.CompletedProcess(a, 0, "", ""))
        deploy.access_live(world.ctx, r1, world.ctx.tmp / "access-live.json")
        (c,) = calls
        mounts = [c[i + 1] for i, x in enumerate(c) if x == "-v"]
        # the runner reads K2_SECRETS=/run/k2-secrets, so its default <secrets>/api-keys is where the files land
        assert c[c.index(f"{model.SECRETS_ENV}=/run/k2-secrets") - 1] == "-e"
        assert [m for m in mounts if ":/run/k2-secrets/api" in m] == [f"{world.secrets}/api-keys/{n}:/run/k2-secrets/api-keys/{n}:ro"
                                                     for n in ("p.a.key", "p.b.key")]

    GUARDED = ('SELECT "id", "note" FROM "g"."a__base"."e" FOR VERSION AS OF \'audited\' WHERE EXISTS (SELECT 1 FROM '
               '"g"."a__base"."e$refs" WHERE "name" = \'audited\' AND "type" = \'TAG\')')
    STORED = ('SELECT\n  "id"\n, "note"\nFROM\n  "g"."a__base"."e" FOR VERSION AS OF \'audited\'\nWHERE (EXISTS (SELECT 1\n'
              'FROM\n  "g"."a__base"."e$refs"\nWHERE (("name" = \'audited\') AND ("type" = \'TAG\'))\n))\n')  # as Trino 484 stored it

    @pytest.mark.parametrize("a, b, same", [
        (GUARDED, STORED, True),  # Trino's parentheses around each comparison, the AND and the EXISTS
        ("SELECT 1 WHERE a = 1 AND (b = 1 OR c = 1)", "SELECT 1 WHERE (a = 1 AND b = 1) OR c = 1", False),
        ('SELECT CAST(NULL AS varchar) AS "note"', 'SELECT "note"', False),  # a column aliased past its mask
        ("SELECT 1 WHERE x IN (1, 2)", "SELECT 1 WHERE x IN (1, 2)", True),
        (GUARDED.split(" WHERE EXISTS")[0], GUARDED, False),  # the old shape is not the guarded one
        ("SELECT f((a)) FROM t", "SELECT f(a) FROM t", True),  # a redundant pair inside a call goes
        ("SELECT f(a, b) FROM t", "SELECT f a, b FROM t", False),  # the call's own parentheses stay
    ])
    def test_stored_and_generated_view_sql_compare_as_trino_formats_them(a: str, b: str, same: bool) -> None:
        assert (deploy.tokens(a) == deploy.tokens(b)) is same

    @pytest.mark.parametrize("env_rc, status_rc, refused", [(0, 0, None), (1, 0, "check-env"), (0, 1, "principals-status")])
    def test_preflight_refuses_a_loose_env_file_or_a_bad_password_db_line(world: World, monkeypatch: pytest.MonkeyPatch,
                                                                           env_rc: int, status_rc: int, refused: str | None) -> None:
        monkeypatch.undo()
        answers = {"check-env.py": env_rc, "principals-status": status_rc}
        monkeypatch.setattr(deploy, "run", lambda *a, **k: subprocess.CompletedProcess(
            a, next(rc for key, rc in answers.items() if any(key in str(x) for x in a)), "", "STALE p.gone"))
        if refused is None:
            deploy.secrets_health(world.ctx)
        else:
            with pytest.raises(deploy.Refused, match=refused):
                deploy.secrets_health(world.ctx)

    # --- the DDL comparison --------------------------------------------------------------

    def test_an_emitted_spark_table_is_read_as_the_catalog_spells_it(world: World) -> None:
        r1 = world.release()
        text = (r1.root / model.ddl_path("spark", "k2", "station_readings", "landing_files")).read_text()
        want = deploy.expected_table(text, "spark")
        assert want["columns"][0][1:] in (["string", False], ["string", True]) and want["partitioning"]
        assert deploy.table_difference("t", want, json.loads(json.dumps(want))) is None
        changed = json.loads(json.dumps(want))
        changed["columns"][0][1] = "long"
        why = deploy.table_difference("t", want, changed)
        assert why and f"column {want['columns'][0][0]} is long" in why

    def test_the_run_record_ddl_is_read_with_its_partition(world: World) -> None:
        want = deploy.expected_table(model.RUN_RECORD_SQL.read_text(), "trino")
        names = [c[0] for c in want["columns"]]
        assert names[:2] == ["run_id", "product_id"] and len(names) == 17
        assert dict((c[0], c[1]) for c in want["columns"])["recorded_at"] == "timestamptz"
        assert want["partitioning"] == [["day", "recorded_at"]]
        other = {**want, "partitioning": [["month", "recorded_at"]]}
        assert "partitioned by" in (deploy.table_difference("r", want, other) or "")

    def test_the_tools_image_is_the_one_compose_builds_and_its_base_is_pinned() -> None:
        doc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["tools"]
        assert doc["build"]["dockerfile"] == "docker/tools/Dockerfile" and doc["profiles"] == ["tools"]
        dockerfile = (ROOT / "docker/tools/Dockerfile").read_text()
        assert all("@sha256:" in line for line in dockerfile.splitlines() if line.startswith(("FROM", "COPY --from")))
        assert "\nUSER " in dockerfile and "USER root" not in dockerfile

    # --- review R1 of PR #250 ---------------------------------------------------------

    MIGRATE = {"prefect-db": TRINO}

    def migrate_doc(image: str) -> dict[str, Any]:
        d = compose_doc()
        d["services"] |= {"airflow-migrate": {"image": image, "restart": "no"},
                          "prefect-db": {"image": "postgres:16", "restart": "unless-stopped"}}
        return d

    def test_a_migrating_one_shot_runs_only_after_its_database_is_dumped_and_a_failed_dump_refuses(world: World) -> None:
        world.write(world.read() | {"oneshots": ["lake-init", "airflow-migrate"], "mc_out": "-- PostgreSQL database dump complete"})
        r1 = world.release(compose=migrate_doc("alpine:3.20"), images=MIGRATE | {"airflow-migrate": A})
        world.live(r1)
        r2 = world.release(compose=migrate_doc("alpine:3"), images=MIGRATE | {"airflow-migrate": B})
        assert deploy.make_plan(world.ctx, r2, [r1]).oneshots == ["airflow-migrate"]
        world.write(world.read() | {"mc_rc": 1})  # pg_dump fails: through the real scripts/backup-lake.sh --dump
        with pytest.raises(deploy.Refused, match="dump of database airflow"):
            world.deploy(r2)
        assert world.ups() == [] and records(world)[-1]["step"] == "backup"
        world.write(world.read() | {"mc_rc": 0})
        assert world.deploy(r2) == 0 and world.ups() == ["airflow-migrate"]
        steps = [r["step"] for r in records(world)]
        assert len(steps) - steps[::-1].index("backup") < len(steps) - steps[::-1].index("ddl")  # the dump first
        dump = next(r for r in reversed(records(world)) if r["step"] == "backup")["backups"]["airflow"]
        assert Path(dump["path"]).parent == world.tmp / "backup" / "deploy" / r2.id  # never the releases root, which every service mounts
        assert world.root not in Path(dump["path"]).parents
        assert hashlib.sha256(Path(dump["path"]).read_bytes()).hexdigest() == dump["sha256"]
        p = deploy.make_plan(world.ctx, r2, [r2])
        p.oneshots, found = ["airflow-migrate"], {}
        assert deploy.migration_dumps(world.ctx, p, found)[0].startswith("SKIP dump of database airflow") and found == {}
        p.sources = [None]  # no previous release: the dump is taken
        deploy.migration_dumps(world.ctx, p, found)
        assert set(found["backups"]) == {"airflow"}

    @pytest.mark.parametrize("down", [False, True])
    def test_a_rollback_narrows_before_the_swap_or_with_trino_down_right_after_it_comes_back(world: World, down: bool) -> None:
        t = world.release()  # operator_note, masked by t's rules alone
        g = world.release(over=dropped_note())  # compiled second: a version never goes backwards
        world.live(g)
        assert world.deploy(t) == 0 and "operator_note" in world.views[VIEW]
        world.log.unlink()
        world.down = down
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        tl = world.timeline()
        swapped = next(i for i, e in enumerate(tl) if e[:2] == ["tools", "swap"])
        narrowed = next(i for i, e in enumerate(tl) if e[0] == "view" and "operator_note" not in e[1])
        if down:
            assert swapped < next(i for i, e in enumerate(tl) if e[:2] == ["up", "trino"]) < narrowed, tl
        else:
            assert narrowed < swapped and ["up", "trino"] not in [e[:2] for e in tl], tl
        assert world.views[VIEW] == ["station_id", "read_at", "value", "region"]

    def test_a_view_not_synced_after_a_rollback_swap_fails_the_rollback_naming_it(world: World) -> None:
        t = world.release()
        g = world.release(over=dropped_note())
        world.live(g)
        assert world.deploy(t) == 0
        real, said = world.sql, []

        def refusing(ctx: Any, user: str, statement: str) -> dict[str, Any]:
            if statement.startswith("CREATE OR REPLACE VIEW"):
                raise deploy.k2tools.TrinoError("GENERIC_INTERNAL_ERROR", "the catalog refused the commit")
            return real(ctx, user, statement)

        deploy.sql, world.down, world.ctx.say = refusing, True, said.append
        world.log.unlink()
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == deploy.EXIT_ROLLBACK_FAILED
        assert any("ROLLBACK FAILED" in x and f"view {VIEW} not synced" in x for x in said), said

    def test_a_deploy_is_refused_when_its_rollback_target_lost_an_image_and_status_says_so(world: World) -> None:
        r1 = world.release(compose=compose_doc(standin="alpine:3"), images={"standin": B})
        world.live(r1)
        r2 = world.release()
        s = world.read()
        del s["images"][B]  # docker image prune -a
        world.write(s)
        with pytest.raises(deploy.Refused, match=f"rollback target {r1.id} names images .*standin {B}.*docker pull"):
            world.deploy(r2)
        assert world.ups() == [] and [e for e in world.timeline() if e[:2] == ["tools", "swap"]] == []
        said: list[str] = []
        world.ctx.say = said.append
        deploy.status(world.ctx)
        assert any(x.strip().startswith(f"rollback images of {r1.id}: MISSING standin {B}") for x in said), said

    def test_verify_fails_on_no_container_a_missing_service_or_a_failed_fence(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        assert any(x.startswith("catalog-fence: 3 PASS") for x in deploy.verify(world.ctx, r1, None))
        world.fence_rc = 1
        with pytest.raises(deploy.Failed, match="catalog fence exit 1"):
            deploy.verify(world.ctx, r1, None)
        world.fence_rc = 0
        s = world.read()
        world.write(s | {"containers": [c for c in s["containers"] if c["service"] != "standin"]})
        with pytest.raises(deploy.Failed, match="not running: standin"):
            deploy.verify(world.ctx, r1, None)
        world.write(s | {"containers": []})  # a project name that matches nothing
        with pytest.raises(deploy.Failed, match="no container of compose project"):
            deploy.verify(world.ctx, r1, None)

    def test_an_empty_project_is_refused_and_an_unset_one_is_the_checkout_directory(monkeypatch: pytest.MonkeyPatch,
                                                                                    tmp_path: Path) -> None:
        monkeypatch.setenv("K2_RELEASES", str(tmp_path / "releases"))
        monkeypatch.setenv("K2_COMPOSE_PROJECT", "")
        assert deploy.main(["status"]) == deploy.EXIT_REFUSED
        monkeypatch.delenv("K2_COMPOSE_PROJECT")
        monkeypatch.setenv("K2_HOME", str(tmp_path / "K2.Market-data_x"))
        assert deploy.ctx_from_env(tmp_path).project == "k2market-data_x"

    def test_rollback_takes_allow_stateful_from_make_to_the_plan(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1 = world.release()
        world.live(r1)
        r5 = world.release(compose=compose_doc(lakekeeper="quay.io/lakekeeper/catalog:v0.14.0"), images={"lakekeeper": LK2})
        assert world.deploy(r5, allow={"lakekeeper"}) == 0
        world.ctx.allow_stateful = set()
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == deploy.EXIT_ROLLBACK_FAILED  # the plan refuses lakekeeper
        world.ctx.allow_stateful = {"lakekeeper"}
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0 and world.image_of("lakekeeper") == "sha256:" + "d" * 64
        seen: list[set[str]] = []
        monkeypatch.setenv("K2_RELEASES", str(world.root))
        monkeypatch.setenv("K2_COMPOSE_PROJECT", "k2")
        monkeypatch.setattr(deploy, "rollback", lambda ctx: seen.append(ctx.allow_stateful) or 0)
        assert deploy.main(["rollback", "--allow-stateful", "lakekeeper"]) == 0 and seen == [{"lakekeeper"}]
        made = subprocess.run(["make", "-s", "-f", "mk/release.mk", "rollback", "DEPLOY=echo", "ALLOW_STATEFUL=lakekeeper"],
                              cwd=ROOT, capture_output=True, text=True)
        assert made.stdout.strip() == "rollback --allow-stateful lakekeeper", made.stdout + made.stderr

    @pytest.mark.parametrize("image", [
        "ghcr.io/acme/minio:RELEASE.1@sha256:" + "a" * 64,  # a tag and a digest: the form the mirror's pin has
        "ghcr.io/acme/minio:RELEASE.1", "ghcr.io/acme/minio@sha256:" + "a" * 64, "ghcr.io/acme/minio",
        "localhost:5000/acme/minio:RELEASE.1@sha256:" + "a" * 64,  # a registry port is not a tag
    ])
    def test_a_held_service_keeps_the_digest_recorded_for_its_repository(image: str, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = image.split("@")[0].removesuffix(":RELEASE.1")
        recorded = ["docker.io/other/minio@sha256:" + "c" * 64, f"{repo}@sha256:" + "b" * 64]
        monkeypatch.setattr(deploy, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(recorded), ""))
        assert deploy.held_ref("sha256:" + "e" * 64, image, image) == f"{repo}@sha256:" + "b" * 64
        monkeypatch.setattr(deploy, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(recorded[:1]), ""))
        with pytest.raises(deploy.Refused, match="no registry digest"):
            deploy.held_ref("sha256:" + "e" * 64, image, image)

    # --- review R1 of phases 002+003: a running service the target puts behind a profile -----

    # v3.0.0-alpha.3.2's three stubs as its compose declared them (the CI checkout fetches no tag to
    # `git show`), beside this branch's own three stanzas, read from docker-compose.yml.
    A32_STUBS = yaml.safe_load("""
    k2-api:
      profiles: [api]
      image: alpine:3.20
      command: [echo, "SKIP k2-api: built in phase 003"]
      networks: [k2-net]
      volumes: [{type: bind, source: "${K2_RELEASES:-${HOME}/k2-releases}", target: /k2/releases, read_only: true}]
      deploy: {resources: {limits: {cpus: '0.5', memory: 512M}}}
      restart: "no"
    fetch:
      profiles: [fetch]
      image: alpine:3.20
      command: [echo, "SKIP fetch: built in phase 002"]
      networks: [k2-net]
      deploy: {resources: {limits: {cpus: '0.5', memory: 512M}}}
      restart: "no"
    dbt-runner:
      profiles: [build]
      image: alpine:3.20
      command: [echo, "SKIP dbt-runner: built in phase 002"]
      networks: [k2-catalog]
      deploy: {replicas: 2, resources: {limits: {cpus: '0.5', memory: 768M}}}
      restart: "no"
    """)
    BUILT = {s: f"{s}:{c * 12}" for s, c in (("k2-api", "1"), ("fetch", "2"), ("dbt-runner", "3"))}  # local builds

    def real_pair(world: World) -> tuple[Any, Any]:
        """(alpha.3.2 live, its three stubs not running; this branch's release): the stanzas as each tag has them."""
        now = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
        old, new = compose_doc(), compose_doc()
        old["services"] |= A32_STUBS
        new["services"] |= {s: now[s] for s in A32_STUBS}
        r_old = world.release(compose=old, images={s: A for s in A32_STUBS})
        r_new = world.release(compose=new, images=BUILT)
        world.live(r_old)
        s = world.read()
        s["containers"] = [c for c in s["containers"] if c["service"] not in A32_STUBS]  # a profiled stub never runs
        s["images"] |= {ref: "sha256:" + ref[-12:] + "0" * 52 for ref in BUILT.values()}
        world.write(s)
        for f in ("api/keys.json", "principals/svc.api.password", "principals/svc.catalog.password",
                  "principals/svc.runs.password", "alertmanager/slack-webhook-url"):
            (world.secrets / f).parent.mkdir(parents=True, exist_ok=True)
            (world.secrets / f).write_text("fixture\n")
        return r_old, r_new

    def composed(world: World) -> list[list[str]]:
        return [e[1:] for e in world.timeline() if e[0] == "compose"]

    def test_k2_api_starts_on_the_way_in_and_is_removed_on_a_rollback_to_its_stub(world: World) -> None:
        r_old, r_new = real_pair(world)
        p = deploy.make_plan(world.ctx, r_new, [r_old])
        assert "k2-api" in p.recreate and p.remove == [] and not {"fetch", "dbt-runner"} & {*p.recreate, *p.oneshots}
        assert world.deploy(r_new) == 0
        assert ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "k2-api"] in composed(world)
        assert world.image_of("k2-api") == "sha256:" + "1" * 12 + "0" * 52
        back = deploy.make_plan(world.ctx, r_old, [r_new])
        assert back.remove == ["k2-api"] and "k2-api" not in {*back.recreate, *back.oneshots}, back
        world.log.unlink()
        world.api_says = "down"  # alpha.3.2's k2-api is a profiled stub: verify must not ask it
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0  # was exit 3: verify found the branch's k2-api on alpha.3.2
        assert composed(world) == [["--profile", "api", "rm", "--stop", "--force", "k2-api"]], composed(world)
        assert "k2-api" not in {c["service"] for c in world.read()["containers"]}
        steps = [(r["step"], r["outcome"]) for r in records(world)]
        assert steps.index(("rollback-swap", "ok")) < steps.index(("rollback-remove-k2-api", "ok")) < steps.index(("rollback-recreate", "ok"))
        assert steps[-2] == ("rollback-done", "ok") and os.readlink(world.root / "pointers/k2-api") == f"../{r_old.id}"

    def test_a_rollback_across_a_migrating_one_shot_image_verifies(world: World) -> None:
        """alpha.3.2 to this branch moves k2-airflow's image, so airflow-migrate reruns on the new one; a
        rollback never reruns it, and its exited container keeps that image (exit 3 before)."""
        world.write(world.read() | {"oneshots": ["lake-init", "airflow-migrate"], "mc_out": "-- PostgreSQL database dump complete"})
        r1 = world.release(compose=migrate_doc("alpine:3.20"), images=MIGRATE | {"airflow-migrate": A})
        world.live(r1)
        r2 = world.release(compose=migrate_doc("alpine:3"), images=MIGRATE | {"airflow-migrate": B})
        assert world.deploy(r2) == 0 and world.image_of("airflow-migrate") == "sha256:" + "b" * 64
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        assert world.image_of("airflow-migrate") == "sha256:" + "b" * 64 and records(world)[-2]["step"] == "rollback-done"

    @pytest.mark.parametrize("says, ok", [(None, True), ("v0.0.0-other-0123456789ab", False), ("down", False)])
    def test_verify_asks_k2_api_which_release_it_serves(world: World, says: str | None, ok: bool) -> None:
        r_old, r_new = real_pair(world)
        assert world.deploy(r_new) == 0
        world.api_says = says
        if ok:
            assert any(x.startswith("k2-api: GET http://127.0.0.1:") and x.endswith(f"/v1/release names {r_new.id}")
                       for x in deploy.verify(world.ctx, r_new, None))
        else:
            with pytest.raises(deploy.Failed, match="k2-api serves v0.0.0-other" if says != "down" else "k2-api did not answer: .*503"):
                deploy.verify(world.ctx, r_new, None)
        assert "k2-api" in deploy.POINTERS  # and the readlink check maps service k2-api to its pointer by name

    def test_every_declared_network_exists_after_a_deploy_and_a_rollback_with_compose_labels(world: World) -> None:
        r1 = world.release()
        world.live(r1)
        s = world.read()
        s["networks"] = {"k2_k2-catalog": {"driver": "bridge", "labels": {}},  # made by hand at the cutover, adopted
                         "k2_k2-net": {"driver": "bridge", "labels": {"com.docker.compose.version": "5.0.0"}}}  # by compose
        s["rendered"]["networks"] = {k: {"name": f"k2_{k}", "driver": "bridge", "ipam": {}} for k in ("k2-net", "k2-egress")}
        world.write(s)
        assert world.deploy(world.release(over=policy(60))) == 0
        made = [e[1:] for e in world.timeline() if e[0] == "network"]
        assert made == [["create", "--driver", "bridge", "--label", "com.docker.compose.project=k2", "--label",
                         "com.docker.compose.network=k2-egress", "--label", "com.docker.compose.version=5.0.0", "k2_k2-egress"]]
        assert world.read()["networks"]["k2_k2-egress"]["labels"] == {"com.docker.compose.project": "k2",
                                                                     "com.docker.compose.network": "k2-egress",
                                                                     "com.docker.compose.version": "5.0.0"}
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        assert len([e for e in world.timeline() if e[0] == "network"]) == 1  # it exists: never created twice
        s = world.read()  # no network of the project carries compose's version: the label is left off, never guessed
        s["networks"] = {}
        s["rendered"]["networks"] = {"k2-net": {"name": "k2_k2-net", "driver": "bridge", "ipam": {}}}
        world.write(s)
        assert deploy.ensure_networks(world.ctx, r1) == ["network k2_k2-net created: no service compose starts uses it yet"]
        assert world.read()["networks"]["k2_k2-net"]["labels"] == {"com.docker.compose.project": "k2", "com.docker.compose.network": "k2-net"}
        s = world.read()
        s["rendered"]["networks"]["odd"] = {"name": "k2_odd", "driver": "bridge", "internal": True}
        world.write(s)
        with pytest.raises(deploy.Failed, match="network odd declares"):
            deploy.ensure_networks(world.ctx, r1)

    def test_a_running_service_profiled_in_both_is_recreated_not_removed(world: World) -> None:
        r1 = world.release(compose=compose_doc(), images={"clickhouse": A})
        world.live(r1)  # clickhouse runs: someone started hot-gold on purpose
        r2 = world.release(compose=compose_doc(), images={"clickhouse": B})
        p = deploy.make_plan(world.ctx, r2, [r1])
        assert p.remove == [] and "clickhouse" in p.recreate

    # --- review R3: every secret file the target mounts, before anything changes ---------

    def test_preflight_names_every_missing_secret_file_of_the_target_and_its_make_command(world: World) -> None:
        now = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
        doc = compose_doc()
        doc["services"] |= {s: now[s] for s in ("trino", "tools", "k2-api", "airflow-scheduler", "alertmanager", "dbt-runner")}
        r1 = world.release(compose=doc)
        p = deploy.make_plan(world.ctx, r1, [None])
        builds = sorted(deploy.run_principals(r1))
        assert "svc.runs" in builds and any(x.startswith("dbt_") for x in builds) and any(x.startswith("pub_") for x in builds)
        assert deploy.secret_binds(r1) >= {"principals/svc.catalog.password", "principals/svc.api.password", "api",
                                           "principals/svc.runs.password", "trino", "tls/trino.pem", "alertmanager"}
        (world.secrets / "principals/dbt_daily_load.password").unlink()  # a build's: the operator mounts it at run time
        missing = deploy.required_secrets(world.ctx, p)
        assert missing == ["alertmanager/slack-webhook-url", "api/keys.json", "principals/dbt_daily_load.password",
                           "principals/svc.api.password", "principals/svc.catalog.password"], missing
        with pytest.raises(deploy.Refused) as e:
            world.deploy(r1)
        assert "principals/svc.catalog.password (make password PRINCIPAL=svc.catalog TO_FILE=1)" in str(e.value)
        assert "api/keys.json (make api-key)" in str(e.value) and "alertmanager/slack-webhook-url (make alerts-install-webhook)" in str(e.value)
        assert "fixture" not in str(e.value) and world.ups() == [] and records(world)[-1]["step"] == "preflight"
        for n in missing:
            (world.secrets / n).parent.mkdir(parents=True, exist_ok=True)
            (world.secrets / n).write_text("fixture\n")
        assert deploy.required_secrets(world.ctx, p) == []

    def test_preflight_refuses_while_a_principal_the_live_suite_logs_in_as_has_no_password_file(world: World) -> None:
        """svc.operator's rows run on live (its host-held login is the platform operator's), so its file is
        asked before anything moves, not failed by the live suite after the swap; a partner's rows are skipped
        there (access_verify.OWNER_HELD), so its file is never asked."""
        principals = "contracts/access/principals.yaml"
        rows = ('  - {principal: svc.operator, sql: "SELECT 1", expect: ok}\n'
                '  - {principal: svc.partner-dashboard, sql: "SELECT 1", expect: denied}\n')
        declared = "".join(f"  - id: {x}\n    kind: service\n    owner: platform-team\n" for x in ("svc.operator", "svc.partner-dashboard"))
        world.live(world.release())
        r1 = world.release(over={EXPECT: (GOOD / EXPECT).read_bytes() + rows.encode(),
                                 principals: (GOOD / principals).read_bytes() + declared.encode()})
        live = deploy.live_principals(r1)
        assert "svc.operator" in live and "svc.partner-dashboard" not in live, live
        with pytest.raises(deploy.Refused) as e:
            world.deploy(r1)
        assert "principals/svc.operator.password (make password PRINCIPAL=svc.operator TO_FILE=1)" in str(e.value)
        assert "partner" not in str(e.value) and world.ups() == [] and records(world)[-1]["step"] == "preflight"
        (world.secrets / "principals/svc.operator.password").write_text("fixture\n")
        assert world.deploy(r1) == 0

    # --- review R6: lake-init runs the deployed release's own script and policies --------

    def test_lake_init_runs_the_target_release_script_and_an_older_release_skips_it(world: World) -> None:
        lake = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["lake-init"]
        assert not any(str(v).startswith("./") for v in lake["volumes"]), "never the checkout's docker/lake"
        entry = lake["entrypoint"][-1]
        assert entry == f"/k2/releases/${{K2_LAKE_INIT_RELEASE:-pointers/trino}}/{deploy.LAKE_INIT}", entry
        world.live(world.release())
        r1 = world.release(images={"lake-init": B})  # lake-init's image moves: it runs, from r1's own copy
        assert world.deploy(r1) == 0
        assert ["up", "lake-init", B, r1.id] in [e[:4] for e in world.timeline()]
        assert (world.root / entry.replace("${K2_LAKE_INIT_RELEASE:-pointers/trino}", r1.id).removeprefix("/k2/releases/")).is_file()
        doc = compose_doc(standin="alpine:3")
        doc["services"]["lake-init"]["environment"] = {"MINIO_ROOT_USER": "${MINIO_ROOT_USER}"}  # its old shape
        old = world.release(compose=doc, images={"standin": B, "lake-init": B}, lake_init=False)
        p = deploy.make_plan(world.ctx, old, [r1])
        assert "lake-init" not in p.oneshots and any(x.startswith(f"lake-init: {old.id} carries no {deploy.LAKE_INIT}") for x in p.skipped)
        world.log.unlink()
        assert world.deploy(old) == 0 and world.ups() == ["standin"]

    def test_preflight_refuses_when_a_storage_key_the_live_suite_signs_with_is_not_in_the_environment(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = "".join(f"  - {{storageKey: {{engine: {e}, target: k2-lake/warehouse/k2, operation: read}}, expect: allowed}}\n"
                       for e in ("spark", "backup"))
        r1 = world.release(over={EXPECT: (GOOD / EXPECT).read_bytes() + rows.encode()})
        assert deploy.live_key_names(r1) == ["K2_BACKUP_S3_ACCESS_KEY", "K2_BACKUP_S3_SECRET_KEY",
                                             "K2_SPARK_S3_ACCESS_KEY", "K2_SPARK_S3_SECRET_KEY"]
        for n in deploy.live_key_names(r1):
            monkeypatch.setenv(n, "a-value-of-this-test")
        monkeypatch.delenv("K2_BACKUP_S3_SECRET_KEY")
        with pytest.raises(deploy.Refused, match=r"sign with K2_BACKUP_S3_SECRET_KEY, not in deploy's environment.*set -a") as e:
            world.deploy(r1)
        assert "a-value-of-this-test" not in str(e.value) and world.ups() == []
        assert deploy.live_key_names(world.release()) == []  # no storage-key row: nothing asked

    # --- the sites after each deploy and rollback (W3-12) ------------------------------

    DBT_RUNNER = "dbt-runner:" + "3" * 12

    def site_release(world: World, standin: str = "alpine:3.20") -> Any:
        doc = compose_doc(standin=standin) | {"volumes": {"k2-site": {}}}
        return world.release(compose=doc, over={"platform/catalog-site/k2site.py": b"print('k2site: wrote /site/catalog/current')\n"},
                             images={"dbt-runner": DBT_RUNNER} | ({"standin": B} if standin != "alpine:3.20" else {}))

    def test_the_sites_run_after_done_on_a_deploy_and_a_rollback_and_a_failure_never_fails_either(world: World) -> None:
        s = world.read()
        world.write(s | {"images": s["images"] | {DBT_RUNNER: "sha256:" + "3" * 12 + "0" * 52}})
        r1 = site_release(world)
        world.live(r1)
        s = world.read()  # dbt-runner is profiled: Airflow starts it per run, never compose
        world.write(s | {"containers": [c for c in s["containers"] if c["service"] != "dbt-runner"]})
        r2 = site_release(world, standin="alpine:3")
        assert deploy.DBT_PROJECT in r2.manifest.files and deploy.CATALOG_SITE in r2.manifest.files
        assert world.deploy(r2) == 0
        t = world.timeline()
        where = f"/k2/releases/{r2.id}"
        assert ["tools-run", "python3", f"{where}/{deploy.CATALOG_SITE}", "--release", where, "--out", "/site/catalog"] in t
        assert ["run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--pids-limit", "256", "--cpus", "0.5", "--memory", "768m",
                "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{world.root}:/k2/releases:ro", "-v", "k2_k2-site:/site",
                DBT_RUNNER, "k2-dbt", "docs", "--release", r2.id, "--out", "/site/dbt-docs"] in t
        steps = [(r["step"], r["outcome"]) for r in records(world)]
        assert steps[-2:] == [("done", "ok"), ("site", "ok")] and deploy.standing(records(world))[1] == []
        world.write(world.read() | {"run_rc": 1})  # the dbt docs fail: the rollback still ends at exit 0
        said: list[str] = []
        world.ctx.say = said.append
        with deploy.lock(world.root):
            assert deploy.rollback(world.ctx) == 0
        last = records(world)[-1]
        assert (last["step"], last["outcome"]) == ("rollback-site", "failed") and "dbt docs:" in last["reason"]
        assert any(x.startswith("deploy: site: failed") and "stays live and verified" in x for x in said), said
        assert deploy.standing(records(world)) == ([r1.id, r2.id, r1.id], [])  # a site moves no release
        assert deploy.sites(world.ctx, world.release()) == ["SKIP sites: " + world.release().id
                                                            + "'s compose has no k2-site volume (a release before phase 003)"]
        made = subprocess.run(["make", "-s", "-f", "mk/run.mk", "site", "DEPLOY=echo"], cwd=ROOT, capture_output=True, text=True)
        assert made.stdout.strip() == "site", made.stdout + made.stderr

    # --- the drain (W1-6, ADR-058) ------------------------------------------------------

    CODE = "docker/airflow/dags/factory.py"  # in a release at platform/docker/airflow/dags/factory.py
    SR, DL = "station_readings", "daily_load"  # the good set's two DAGs

    def stand_in(tmp: Path, rid: str, dags: dict[str, list[str]], code: str) -> Any:
        """A release as drain_need reads one: its manifest's files and its dags.json."""
        (tmp / rid / "compiled/airflow").mkdir(parents=True)
        (tmp / rid / model.DAGS).write_text(json.dumps({"dags": [{"dagId": d, "tasks": t} for d, t in dags.items()]}))
        files = {model.DAGS: hashlib.sha256(json.dumps(dags).encode()).hexdigest(),
                 f"platform/{CODE}": code, "compiled/trino/rules.json": rid}  # the rules move every time: never a drain
        return type("R", (), {"id": rid, "root": tmp / rid, "manifest": type("M", (), {"files": files})})()

    def test_drain_need_for_each_kind_of_change(tmp_path: Path) -> None:
        old = stand_in(tmp_path, "v1-a", {"a": ["resolve", "build"], "b": ["resolve", "build"], "c": ["resolve"]}, "h1")
        new = stand_in(tmp_path, "v1-b", {"a": ["resolve", "fetch", "build"], "c": ["resolve"], "d": ["resolve"]}, "h1")
        same = stand_in(tmp_path, "v1-c", {"a": ["resolve", "fetch", "build"], "c": ["resolve"], "d": ["resolve"]}, "h1")
        moved = stand_in(tmp_path, "v1-d", {"a": ["resolve", "build"], "b": ["resolve", "build"], "c": ["resolve"]}, "h2")

        def plan(target: Any, *sources: Any, recreate: tuple[str, ...] = ()) -> Any:
            return deploy.Plan(target, list(sources), list(recreate), [], False, [], [], [])

        fresh = "; new in this release, with no run to drain, never paused or unpaused by deploy: d"
        assert deploy.drain_need(plan(new, old)) == ({"a", "b"}, "the task list of a, b changes or goes" + fresh)  # d: no run
        # A release adding DAGs and moving the factory (phase 004's first): every DAG Airflow has, the new named apart
        assert deploy.drain_need(plan(new, moved)) == (None, "the DAG code moved since v1-d" + fresh)
        assert deploy.drain_need(plan(new, old, recreate=("trino",))) == (None, "Trino restarts" + fresh)
        assert deploy.drain_need(plan(same, new)) == (set(), "no Trino restart and no DAG's task list changes")
        assert deploy.new_dags(plan(new, None)) == []  # a first deploy: its drain is every DAG Airflow has
        assert deploy.drain_need(plan(same, new, recreate=("standin", "trino"))) == (None, "Trino restarts")
        assert deploy.drain_need(plan(same, new, moved))[0] is None  # any source the code moved from: all
        assert deploy.drain_need(plan(new, None))[0] is None  # no release before: its DAGs are unknown

    def drained_world(world: World, paused: dict[str, bool], busy: dict[str, int] | None = None) -> tuple[Any, Any]:
        """r1 live with the scheduler running; r2 moves the DAG code, so every DAG drains."""
        r1 = world.release(over={CODE: b"# one\n"})
        world.live(r1)
        world.scheduler(paused, busy)
        return r1, world.release(over={CODE: b"# two\n"})

    def test_a_drain_pauses_only_the_running_dags_waits_for_their_tasks_and_unpauses_exactly_those(world: World) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: True}, busy={SR: 1})  # an operator paused daily_load
        said: list[str] = []
        world.ctx.say = said.append
        assert world.deploy(r2) == 0
        runs = [[*p, "--state", "running", "-o", "json"] for p in (["dags", "list-runs", DL], ["dags", "list-runs", SR])]
        poll = [*runs, ["tasks", "states-for-dag-run", SR, "scheduled__1", "-o", "json"]]
        assert world.airflow_calls() == [["dags", "list", "-o", "json"], ["dags", "pause", SR], *poll, *poll,
                                         ["dags", "unpause", SR]]  # daily_load's run is asked, its pause kept
        assert world.paused() == {SR: False, DL: True} and not (world.root / deploy.DRAIN_STATE).exists()
        t = [e[:3] for e in world.timeline()]
        assert t.index(["airflow", "dags", "pause"]) < t.index(["tools", "swap", r2.id]) < t.index(["airflow", "dags", "unpause"])
        assert any("drain: the DAG code moved since" in x for x in said) and any(x.startswith("  drained daily_load, station_readings") for x in said)
        steps = [r["step"] for r in records(world)]
        assert steps[steps.index("drain") - 1: steps.index("drain") + 2] == ["backup", "drain", "ddl"]

    def test_a_failed_verify_rolls_back_then_unpauses_the_drained_dags(world: World) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: True})
        assert world.deploy(r2, "verify-mismatch") == 1
        assert world.paused() == {SR: False, DL: True} and not (world.root / deploy.DRAIN_STATE).exists()
        t = [e[:3] for e in world.timeline()]
        back = max(i for i, e in enumerate(t) if e[:2] == ["tools", "swap"])  # the rollback's swap, to r1
        assert t[back][2] == r1.id and back < t.index(["airflow", "dags", "unpause"])
        assert world.airflow_calls()[-1] == ["dags", "unpause", SR]

    def test_tasks_still_running_at_the_bound_refuse_before_the_swap_unpause_and_the_override_goes_on(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: False}, busy={SR: -1})
        monkeypatch.setattr(deploy, "DRAIN_WAIT_S", 0)
        with pytest.raises(deploy.Refused, match=r"still running after 0 s: station_readings\.build \(run scheduled__1, running\)"):
            world.deploy(r2)
        assert world.paused() == {SR: False, DL: False} and world.airflow_calls()[-2:] == [["dags", "unpause", DL], ["dags", "unpause", SR]]
        assert [e for e in world.timeline() if e[:2] == ["tools", "swap"]] == [] and world.ups() == []
        assert (records(world)[-1]["step"], records(world)[-1]["outcome"]) == ("drain", "failed")
        assert deploy.standing(records(world))[1] == []  # a refusal moved nothing: no unfinished attempt
        world.ctx.allow_running = True
        assert world.deploy(r2) == 0 and world.paused() == {SR: False, DL: False}
        steps = [r["step"] for r in records(world)]
        assert steps[steps.index("allow-running") + 1] == "drain" and steps[-2:] == ["done", "site"]

    def test_the_paused_set_is_written_before_the_first_pause_and_a_rerun_after_a_kill_unpauses_it(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: True})
        real, seen = deploy.airflow, []

        def killed(ctx: Any, cid: str, *argv: str) -> Any:
            out = real(ctx, cid, *argv)
            if argv[:2] == ("dags", "pause"):
                seen.append(deploy.paused_by_deploy(ctx))
                raise KeyboardInterrupt  # the process dies right after Airflow paused it: no except Exception runs
            return out

        monkeypatch.setattr(deploy, "airflow", killed)
        with pytest.raises(KeyboardInterrupt):
            world.deploy(r2)
        assert seen == [{SR}] and world.paused() == {SR: True, DL: True}
        monkeypatch.setattr(deploy, "airflow", real)
        assert world.deploy(r2) == 0  # the rerun finds SR paused, so pauses nothing, and unpauses it at the end
        assert world.paused() == {SR: False, DL: True} and ["dags", "pause", SR] not in world.airflow_calls()[2:]

    @pytest.mark.parametrize("earlier", [None, "ddl", "canary", "swap"])
    def test_a_ddl_failure_unpauses_only_when_no_attempt_went_past_the_one_shots(
            world: World, monkeypatch: pytest.MonkeyPatch, earlier: str | None) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: True})
        if earlier:  # an earlier attempt at r2, killed after this step (a kill after swap records only canary)
            deploy.record(world.ctx, r2, r1.id, earlier, "ok", set())

        def failing(ctx: Any, p: Any) -> list[str]:
            raise deploy.Failed("lake-init: the one-shot exited 1")

        monkeypatch.setattr(deploy, "ddl", failing)
        with pytest.raises(deploy.Failed, match="lake-init"):
            world.deploy(r2)
        held = earlier in ("canary", "swap")  # past the one-shots: the pointers may name r2 already
        assert world.paused()[SR] is held and (world.root / deploy.DRAIN_STATE).exists() is held
        assert (["dags", "unpause", SR] in world.airflow_calls()) is not held

    def held_world(world: World, monkeypatch: pytest.MonkeyPatch, held: dict[str, str]) -> tuple[Any, Any, list[tuple[str, str]]]:
        """drained_world, the releases standing in for ones with the holds table (every release has DAGS), and
        Trino answering k2runs' HELD statement with `held` (product -> reason); each read as (user, statement)."""
        monkeypatch.setattr(deploy, "HOLDS_DDL", model.DAGS)
        r1, r2 = drained_world(world, {SR: False, DL: False})
        asked: list[tuple[str, str]] = []
        real = world.sql

        def sql(ctx: Any, user: str, statement: str) -> dict[str, Any]:
            if "run_records.holds" not in statement:
                return real(ctx, user, statement)
            asked.append((user, statement))
            product = re.search(r"h\.product_id = '([^']+)'", statement)[1]  # type: ignore[index]
            row = [product, "operator", held.get(product), "rob", "2026-11-28 01:02:03 UTC", None, None]
            return {"columns": [], "rows": [row] if product in held else []}

        monkeypatch.setattr(deploy, "sql", sql)
        return r1, r2, asked

    def test_a_drained_dag_whose_product_is_held_stays_paused_and_the_deploy_says_so(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        """W1-12, and audit LOW 40: an operator holds a product while deploy has its DAG paused (a hold
        taken mid-drain, or after a deploy died there); done unpauses every other DAG deploy paused."""
        r1, r2, asked = held_world(world, monkeypatch, {"sensor-net.station-readings": "re-staging bronze"})
        said: list[str] = []
        world.ctx.say = said.append
        assert world.deploy(r2) == 0
        assert world.paused() == {SR: True, DL: False} and ["dags", "unpause", SR] not in world.airflow_calls(), [x for x in said if "paused" in x]
        assert not (world.root / deploy.DRAIN_STATE).exists()  # the hold is the operator's now: never unpaused by a later deploy
        assert "  unpaused daily_load: the DAGs deploy paused" in said
        assert ("  kept paused, held: station_readings (operator hold by rob at 2026-11-28 01:02:03 UTC: re-staging bronze); "
                "each stays paused until its hold is released") in said
        assert {u for u, _ in asked} == {"svc.platform"}  # SELECT alone, by the emitted rules: deploy never ends a hold
        bound = deploy.k2runs.HELD.replace("?", "'sensor-net.station-readings'")
        assert any(s == bound for _, s in asked), asked  # k2runs' own statement, its one parameter bound

    def test_holds_that_cannot_be_read_keep_every_dag_deploy_paused(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1, r2, _ = held_world(world, monkeypatch, {})
        real = deploy.sql

        def refused(ctx: Any, user: str, statement: str) -> dict[str, Any]:
            if "run_records.holds" in statement:
                raise deploy.k2tools.TrinoError("TABLE_NOT_FOUND", "Table 'governance_build.run_records.holds' does not exist")
            return real(ctx, user, statement)

        monkeypatch.setattr(deploy, "sql", refused)
        said: list[str] = []
        world.ctx.say = said.append
        # Review D7: live and verified, never rolled back, but not a clean exit 0: exit 4, the DAGs and why on the
        # deploy's last line, and in make release-status; they wait for a check of the holds, or the next done.
        assert world.deploy(r2) == deploy.EXIT_PAUSED
        assert world.paused() == {SR: True, DL: True} and deploy.paused_by_deploy(world.ctx) == {SR, DL}
        assert any(x.startswith("  kept paused daily_load, station_readings: the holds could not be read as svc.platform") for x in said)
        why = "the holds could not be read as svc.platform (TrinoError"
        assert said[-1].startswith(f"deploy: {r2.id} is live and verified, but 2 DAG(s) deploy paused are left paused: "
                                   f"daily_load, station_readings ({why}") and said[-1].endswith(f"its holds: {deploy.UNPAUSE}"), said[-1]
        assert records(world)[-2]["step"] == "done" and [r["step"] for r in records(world) if r["step"].startswith("rollback")] == []
        assert "live and verified, but DAGs left paused" in deploy.MEANING[deploy.EXIT_PAUSED]
        status: list[str] = []
        world.ctx.say = status.append
        deploy.status(world.ctx)
        line = next(x for x in status if "DAGs a deploy paused" in x)
        assert line.startswith("  DAGs a deploy paused and has not yet unpaused: daily_load, station_readings (left paused at ")
        assert why in line and deploy.UNPAUSE in line
        monkeypatch.setattr(deploy, "sql", real)  # the holds readable again: the next done unpauses them and exits 0
        world.ctx.say = said.append
        assert world.deploy(world.release(over={CODE: b"# three\n"})) == deploy.EXIT_OK
        assert world.paused() == {SR: False, DL: False} and not (world.root / deploy.DRAIN_STATE).exists()

    def test_a_release_without_the_holds_table_unpauses_without_reading_them(world: World) -> None:
        r1, r2 = drained_world(world, {SR: False, DL: False})
        assert deploy.HOLDS_DDL == "compiled/lake/ddl/trino/governance.run_records.holds.sql"  # what k2gen emits (I-7)
        assert deploy.HOLDS_DDL not in r2.manifest.files
        assert world.deploy(r2) == 0 and world.paused() == {SR: False, DL: False}
        assert not [e for e in world.timeline() if e[0] == "sql" and "holds" in e[2]]

    def test_the_holds_ddl_is_read_as_the_catalog_spells_it() -> None:
        """The table deploy's ddl step creates from the release (needs.ddl names it, the file is k2gen's
        copy of platform/runs/holds.sql) compares equal to what the catalog prints for it."""
        want = deploy.expected_table(model.HOLDS_SQL.read_text(), "trino")
        assert [c[0] for c in want["columns"]] == list(deploy.k2runs.HOLD_COLUMNS) and want["partitioning"] == []
        assert {c[1] for c in want["columns"]} == {"string", "timestamptz"} and not any(c[2] for c in want["columns"])

    def test_no_drain_no_scheduler_and_no_dags_are_recorded_and_the_deploy_goes_on(world: World) -> None:
        r1 = world.release(over={CODE: b"# one\n"})
        world.live(r1)
        said: list[str] = []
        world.ctx.say = said.append
        assert world.deploy(world.release(over={CODE: b"# two\n"})) == 0
        assert any(x.startswith("  no scheduler: airflow-scheduler is not running") for x in said), said
        world.scheduler({})
        assert world.deploy(world.release(over={CODE: b"# three\n"})) == 0
        assert any(x.startswith("  no DAGs: Airflow has none to drain") for x in said) and world.airflow_calls() == [["dags", "list", "-o", "json"]]
        assert world.deploy(world.release(over={CODE: b"# three\n", **policy(60)})) == 0
        assert any(x.startswith("  no drain: no Trino restart and no DAG's task list changes") for x in said)
        assert len(world.airflow_calls()) == 1  # no change that drains: Airflow not asked
        assert all(r["outcome"] == "ok" for r in records(world) if r["step"] == "drain")

    # --- a release that adds a product (the first live deploy of alpha.4-rc.1, 2026-11-27) ----------

    def test_a_view_new_in_the_target_is_never_read_as_its_publisher_before_its_rules_load(world: World) -> None:
        r1 = world.release(without_daily_load=True)
        world.live(r1)
        r2 = world.release()
        said: list[str] = []
        world.ctx.say = said.append
        assert NEW_VIEW in [v.relation for v in deploy.make_plan(world.ctx, r2, [r1]).views]
        real, asked = world.sql, []
        deploy.sql = lambda ctx, user, statement: asked.append((user, os.readlink(world.root / "pointers/trino"))) or real(ctx, user, statement)
        assert world.deploy(r2) == 0, [x for x in said if "FAILED" in x]  # was views-narrow FAILED: PERMISSION_DENIED
        assert ("pub_daily_load", f"../{r1.id}") not in asked, asked  # never as its publisher under r1's rules
        assert ("pub_daily_load", f"../{r2.id}") in asked  # views-widen asks, once r2's rules are loaded
        assert any(x.startswith(f"  {NEW_VIEW}: not in {r1.id}, so the rules loaded now do not know its publisher") for x in said)
        assert f"  views new in this release: {NEW_VIEW} (created after its rules load, at its first tested publish)" in said
        assert not any(NEW_VIEW in x for x in said if x.startswith("  views to re-sync"))

    def test_a_view_only_an_interrupted_attempt_declares_is_narrowed_only_if_the_loaded_rules_know_its_owner(world: World) -> None:
        r1 = world.release(without_daily_load=True)
        world.live(r1)
        r2, r3 = world.release(), world.release(over=policy(60))
        p = deploy.make_plan(world.ctx, r3, [r1, r2])  # an attempt at r2 died before its swap: r1's rules hold
        p.views = [deploy.views_of(r3)[NEW_VIEW]]
        lines = deploy.sync_views(world.ctx, p, "narrow")
        assert lines[0].startswith(f"{NEW_VIEW}: the rules loaded now refuse its publisher pub_daily_load"), lines
        p.sources = [r2]  # every release it moves from declares the view: a refusal is a real failure, never explained away
        with pytest.raises(deploy.Failed, match="PERMISSION_DENIED"):
            deploy.sync_views(world.ctx, p, "narrow")

    def test_a_rollback_across_a_new_products_ddl_verifies_with_its_leftovers_left_and_exits_1(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1 = world.release(without_daily_load=True)
        world.live(r1)
        r2 = world.release()

        def drift(ctx: Any, rel: Any) -> dict[str, Any]:  # catalog_drift.py over a catalog holding r2's namespaces
            if f"{model.RELEASES_MOUNT}/{r2.id}" in deploy.left_dirs(ctx, rel):
                return {"exit": 0, "doc": {"left": [f"LEFT namespace ops_insight.daily_load: declared by {r2.id}"]}, "text": ""}
            return {"exit": 1, "doc": {}, "text": "DRIFT namespace ops_insight.daily_load: the release declares no such namespace"}

        monkeypatch.setattr(deploy, "drift", drift)
        monkeypatch.setattr(deploy, "access_live", lambda ctx, rel, out: subprocess.CompletedProcess([], 0, "", ""))
        monkeypatch.setattr(deploy, "identity", lambda ctx: ["identity.sh: stood in"])
        world.s20 = lambda rel: world.real_s20(world.ctx, rel)
        said: list[str] = []
        world.ctx.say = said.append
        assert world.deploy(r2, "verify-mismatch") == deploy.EXIT_FAILED, [x for x in said if "FAILED" in x]  # was 3
        assert os.readlink(world.root / "pointers/trino") == f"../{r1.id}"
        said.clear()
        with deploy.lock(world.root):
            assert deploy.verify_live(world.ctx) == 0  # make release-verify after the rollback: the same set, from the log
        assert any(x.strip().startswith(f"LEFT namespace ops_insight.daily_load: declared by {r2.id}") for x in said), said

    def test_the_drift_check_and_the_live_suites_drift_row_are_given_what_the_log_says_was_left(
            world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        r1 = world.release(without_daily_load=True)
        world.live(r1)
        r2 = world.release()
        deploy.record(world.ctx, r2, r1.id, "ddl", "ok", set())  # an attempt at r2 got past its ddl
        calls: list[list[str]] = []
        monkeypatch.setattr(deploy, "run", lambda *a, **k: calls.append(list(a)) or subprocess.CompletedProcess(a, 0, "", ""))
        deploy.drift(world.ctx, r1)
        deploy.access_live(world.ctx, r1, world.tmp / "live.json")
        drift_cmd, live_cmd = calls
        i = drift_cmd.index("--left-by")
        assert drift_cmd[i:i + 2] == ["--left-by", f"{model.RELEASES_MOUNT}/{r2.id}"] and drift_cmd.count("--left-by") == 1
        assert f"{deploy.drift_lib.LEFT_ENV}={model.RELEASES_MOUNT}/{r2.id}" in live_cmd

    def test_left_by_is_each_release_past_its_drain_since_the_live_one_first_appears() -> None:
        def r(rel: str, step: str, outcome: str = "ok", frm: str | None = None) -> dict[str, Any]:
            return {"release": rel, "from": frm, "step": step, "outcome": outcome}

        log = [r("a", "done"), r("old", "ddl"),  # before b first appears: a forward deploy over it is drift again
               r("b", "preflight"), r("b", "done"),
               r("c", "preflight"), r("c", "plan"),  # PLAN=1 moved nothing
               r("d", "preflight"), r("d", "drain", "failed"),  # refused at its drain: no ddl ran
               r("e", "drain"),  # killed inside its ddl: its drain is recorded, its ddl not
               r("a", "swap"), r("a", "recreate"),  # older than b, deployed again: its own leftovers are b's to drop
               r("f", "ddl"), r("f", "views-narrow", "failed"), r("b", "rollback-verify", frm="f"), r("b", "rollback-done", frm="f")]
        assert deploy.left_by(log, "b") == ["e", "f"]  # every release-verify after the rollback
        assert deploy.left_by(log[:-1], "b") == ["e", "f"]  # the rollback's own verify
        assert deploy.left_by(log, "f") == []  # a forward deploy of f: b and a are older, and drift is drift
        assert deploy.left_by(log, "never-deployed") == []

    def test_under_a_release_without_a_product_no_rule_reaches_its_relations(world: World) -> None:
        """What makes a LEFT line safe (catalog_drift.NOTHING_READS): the old release's rules name no
        relation, schema or query of the product, so every principal is denied it by default."""
        r1, r2 = world.release(without_daily_load=True), world.release()
        rules = json.loads((r1.root / "compiled/trino/rules.json").read_text())
        names = [(c, s, "gold_daily_load") for c in ("ops_insight", "ops_insight_build") for s in ("daily_load", "daily_load__base")]
        assert [(n, x) for n in names for x in rules["tables"] if x.get("privileges")
                and all(re.fullmatch(x.get(k, ".*"), v) for k, v in zip(("catalog", "schema", "table"), n))] == []
        assert [x for x in rules["schemas"] for c, s, _ in names
                if all(re.fullmatch(x.get(k, ".*"), v) for k, v in (("catalog", c), ("schema", s)))] == []
        assert not any(re.fullmatch(q["user"], u) for q in rules["queries"] for u in ("pub_daily_load", "dbt_daily_load"))
        left = deploy.drift_lib.left_by(r2.root)  # and what --left-by reads of the release that added it
        assert {("ops_insight", "daily_load"), ("ops_insight", "daily_load__base")} <= left["namespaces"]
        assert ("ops_insight", "daily_load__base", "gold_daily_load") in left["tables"]
        assert left["views"][("ops_insight", "daily_load", "gold_daily_load")] == "pub_daily_load"

    def test_the_drift_check_waits_for_a_running_builds_own_relations_and_for_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
        """scripts/chaos/deploy-mid-run.sh, 2026-11-27: a deploy beside a running build saw dbt's
        `silver_rates__dbt_tmp` as drift and rolled back. It now runs the check again; any other
        difference, or one that stays, still fails."""
        tmp = "DRIFT view k2.ns__base.silver_rates__dbt_tmp: the release declares no such view (it claims owner None)"
        bak = "DRIFT table k2.ns__base.gold__dbt_backup: in k2, and the release declares no such table"
        other = "DRIFT view k2.ns__base.extra: the release declares no such view (it claims owner None)"
        assert deploy.in_flight([tmp, bak])
        assert not deploy.in_flight([]) and not deploy.in_flight([tmp, other])
        # "pending" ends s20_checks after its drift half: the live suite is not this test's
        answers = [{"exit": 1, "doc": {"differences": [tmp]}, "text": tmp}, {"exit": 0, "doc": {"pending": ["p"]}, "text": ""}]
        monkeypatch.setattr(deploy, "drift", lambda ctx, rel: answers.pop(0))
        monkeypatch.setattr(deploy.time, "sleep", lambda s: None)
        monkeypatch.setattr(deploy, "BUILD_WAIT", 20)
        lines, wrong = deploy.s20_checks(None, None)  # type: ignore[arg-type]
        assert not answers and not wrong and any("waited 10 s" in x for x in lines), (lines, wrong)
        stuck = {"exit": 1, "doc": {"differences": [tmp], "pending": ["p"]}, "text": tmp}
        monkeypatch.setattr(deploy, "drift", lambda ctx, rel: stuck)
        lines, wrong = deploy.s20_checks(None, None)  # type: ignore[arg-type]
        assert any("drift check exit 1" in w for w in wrong) and any("waited 20 s" in x for x in lines), (lines, wrong)

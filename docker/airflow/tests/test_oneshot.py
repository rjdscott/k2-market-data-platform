"""
The one-shot operator (docker/airflow/dags/oneshot.py) against the release's oneshots.json, with
the docker CLI faked: the exact argument list per kind, every refusal before a run, the container
name, stale removal, on_kill, exit codes, no value in argv, and no import but stdlib and Airflow.
The kinds are k2gen's own golden (platform/k2gen/tests/golden/pipeline/compiled/oneshots.json).

    make test-airflow     # inside the Airflow image, no Docker daemon and no network
"""

from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "docker/airflow/dags/oneshot.py"
GOLDEN = REPO / "platform/k2gen/tests/golden/pipeline"
sys.path.insert(0, str(SOURCE.parent))
import oneshot  # noqa: E402

RID = "v3.0.0-demo-0123456789ab"
RELEASE_PATH = f"/k2/releases/{RID}"
SECRET = "s3cret-value-never-in-argv"
HOST = {"K2_RELEASES": "/home/owner/k2-releases", "K2_SECRETS": "/home/owner/k2-secrets",
        "K2_COMPOSE_PROJECT": "k2proj", "K2_TRINO_HOST": "trino", "K2_TRINO_INTERNAL_PORT": "8443",
        "K2_RUNS_USER": "svc.runs", "K2_CATALOG_URI": "http://lakekeeper:8181/catalog", "K2_RELEASE": "stale"}
RELEASE_MOUNT = ["--mount", f"type=bind,source=/home/owner/k2-releases/{RID},target=/k2/releases/{RID},readonly"]
DBT = {"kind": "dbt-runner", "command": ["k2-dbt", "build", "--release", RID, "--product", "fx.ecb_fx",
                                         "--run-id", "ecb_fx/manual__1"],
       "env": {"K2_TRINO_USER": "dbt_ecb_fx", "K2_PUB_USER": "pub_ecb_fx"},
       "principals": ["dbt_ecb_fx", "pub_ecb_fx", "svc.runs"]}
FETCH = {"kind": "fetch", "command": ["k2-fetch", "--spool", "/spool/ecb_fx/manual__1"]}


def secret_mount(source: str, target: str) -> list[str]:
    return ["--mount", f"type=bind,source=/home/owner/k2-secrets/{source},target=/run/k2-secrets/{target},readonly"]


class Run:
    """One execute with docker faked; `calls` is every docker command line, in order."""

    def __init__(self, kinds: dict | None = None, lines: tuple[str, ...] = (), code: int = 0,
                 rm: subprocess.CompletedProcess | None = None, env: dict | None = None):
        self.kinds = kinds if kinds is not None else oneshot.load_oneshots(str(GOLDEN))
        self.lines, self.code, self.env = lines, code, {**HOST, **(env or {})}
        self.rm = rm or subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []
        self.pushed: dict = {}
        self.popen_env: dict = {}
        self.log = mock.MagicMock()

    def run(self, argv, **_):
        self.calls.append(argv)
        return self.rm

    def popen(self, argv, env, **_):
        self.calls.append(argv)
        self.popen_env = env
        return SimpleNamespace(stdout=iter(f"{line}\n" for line in self.lines), wait=lambda: self.code)

    def __call__(self, dag_id: str = "ecb_fx", try_number: int = 1, **kwargs):
        args = {"task_id": "t", "release_path": RELEASE_PATH, **kwargs}
        self.op = oneshot.OneShotOperator(**args)
        ti = SimpleNamespace(dag_id=dag_id, try_number=try_number, xcom_push=lambda key, value: self.pushed.update({key: value}))
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(oneshot, "load_oneshots", return_value=self.kinds), \
                mock.patch.object(oneshot.subprocess, "run", side_effect=self.run), \
                mock.patch.object(oneshot.subprocess, "Popen", side_effect=self.popen), \
                mock.patch.object(oneshot.OneShotOperator, "log", self.log):
            return self.op.execute({"ti": ti})

    @property
    def docker_run(self) -> list[str]:
        return next(c for c in self.calls if c[:2] == ["docker", "run"])


class ArgumentList(unittest.TestCase):
    def test_fetch_runs_with_exactly_what_its_kind_names(self):
        run = Run()
        run(**FETCH)
        self.assertEqual(run.docker_run, [
            "docker", "run", "--rm", "--name", "k2-fetch-ecb_fx-1", "--pull", "never", "--read-only", "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", "0.5", "--memory", "512M", "--network", "k2proj_k2-net", "-e", "K2_RELEASE", *RELEASE_MOUNT,
            "fetch:4008294739ab", "k2-fetch", "--spool", "/spool/ecb_fx/manual__1"])
        self.assertNotIn("--privileged", run.docker_run)
        self.assertEqual(run.popen_env["K2_RELEASE"], RID)  # the run's release, never the scheduler's

    def test_dbt_runner_runs_with_exactly_what_its_kind_names(self):
        run = Run(lines=('{"exit": 0, "expire": []}',))
        run(try_number=2, **DBT)
        self.assertEqual(run.docker_run, [
            "docker", "run", "--rm", "--name", "k2-dbt-runner-ecb_fx-2", "--pull", "never", "--read-only", "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", "0.5", "--memory", "768M", "--network", "k2proj_k2-catalog",
            "-e", "K2_TRINO_HOST", "-e", "K2_TRINO_INTERNAL_PORT", "-e", "K2_TRINO_USER", "-e", "K2_PUB_USER",
            "-e", "K2_RUNS_USER", "-e", "K2_CATALOG_URI", "-e", "K2_RELEASE", *RELEASE_MOUNT,
            *secret_mount("principals/dbt_ecb_fx.password", "builder.password"),
            *secret_mount("principals/pub_ecb_fx.password", "publisher.password"),
            *secret_mount("principals/svc.runs.password", "runs.password"),
            *secret_mount("tls/ca.pem", "tls/ca.pem"),
            "dbt-runner:5a6b7c8d9e0f", *DBT["command"]])
        self.assertEqual({k: run.popen_env[k] for k in ("K2_TRINO_USER", "K2_PUB_USER", "K2_TRINO_HOST", "K2_RELEASE")},
                         {"K2_TRINO_USER": "dbt_ecb_fx", "K2_PUB_USER": "pub_ecb_fx", "K2_TRINO_HOST": "trino",
                          "K2_RELEASE": RID})

    def test_no_env_value_is_in_argv_or_the_log_only_in_the_docker_cli_environment(self):
        run = Run(env={"K2_CATALOG_URI": SECRET}, lines=('{"exit": 0, "expire": []}',))
        run(**{**DBT, "env": {**DBT["env"], "K2_TRINO_USER": SECRET + "-user"}})
        self.assertFalse([a for c in run.calls for a in c if SECRET in a])
        self.assertNotIn(SECRET, repr(run.log.mock_calls))
        self.assertEqual((run.popen_env["K2_CATALOG_URI"], run.popen_env["K2_TRINO_USER"]), (SECRET, SECRET + "-user"))
        for flag, value in zip(run.docker_run, run.docker_run[1:]):
            if flag == "-e":
                self.assertRegex(value, r"^[A-Z][A-Z0-9_]*$")  # a name, never NAME=value

    def test_the_golden_is_read_from_the_release_path(self):
        self.assertEqual(sorted(oneshot.load_oneshots(str(GOLDEN))), ["dbt-runner", "fetch", "tools"])


class Refusals(unittest.TestCase):
    """Each refused before anything runs: no rm, no run."""

    def refused(self, kinds: dict | None = None, host: dict | None = None, **kwargs):
        """`host` is added to the scheduler's environment; the rest are the operator's arguments."""
        run = Run(kinds=kinds, env=host)
        with self.assertRaises(AirflowFailException) as caught:
            run(**{**FETCH, **kwargs})
        self.assertEqual(run.calls, [])
        return str(caught.exception)

    def golden(self, kind: str, **changes) -> dict:
        kinds = copy.deepcopy(oneshot.load_oneshots(str(GOLDEN)))
        kinds[kind].update(changes)
        return kinds

    def test_an_env_name_the_kind_does_not_list(self):
        self.assertIn("AWS_SECRET_ACCESS_KEY", self.refused(env={"AWS_SECRET_ACCESS_KEY": "x"}))

    def test_a_listed_env_name_with_no_value(self):
        self.assertIn("K2_CATALOG_URI", self.refused(host={"K2_CATALOG_URI": ""}, **DBT))

    def test_a_mount_the_kind_does_not_list(self):
        self.refused(principals=["svc.runs"])  # fetch lists no secret
        self.refused(**{**DBT, "principals": [*DBT["principals"], "svc.tools"]})
        self.refused(**{**DBT, "principals": ["dbt_ecb_fx", "dbt_other", "pub_ecb_fx", "svc.runs"]})

    def test_a_listed_password_mount_no_principal_fills(self):
        self.refused(**{**DBT, "principals": ["dbt_ecb_fx", "pub_ecb_fx"]})

    def test_a_kind_off_the_schema(self):
        self.refused(kinds=self.golden("fetch", volumes=["/var/run/docker.sock:/var/run/docker.sock"]))
        self.refused(kinds=self.golden("fetch", image="--privileged"))
        self.refused(kinds=self.golden("fetch", networks=["host"]))
        self.refused(kinds=self.golden("fetch", networks=["k2-net", "k2-catalog"]))
        self.refused(kinds=self.golden("fetch", secrets=["/run/k2-secrets/tls/ca.pem"]))
        self.refused(kinds=self.golden("fetch", env=["K2_RELEASE=x"]))
        self.refused(kinds=self.golden("fetch", cpus="1 --privileged"))
        self.refused(kinds=self.golden("tools", secrets=["/run/k2-secrets/../../../var/run/docker.sock"]), kind="tools")
        self.refused(kind="spark")

    def test_a_release_path_that_is_not_one_release(self):
        self.refused(release_path="/k2/releases/pointers")
        self.refused(release_path="/k2/releases/pointers/airflow")
        self.refused(release_path="/etc")

    def test_a_host_path_or_project_the_daemon_cannot_take(self):
        self.refused(host={"K2_RELEASES": "relative/path"})
        self.refused(host={"K2_RELEASES": "/a,source=/var/run/docker.sock"})
        self.refused(host={"K2_COMPOSE_PROJECT": ""})


class Names(unittest.TestCase):
    def test_a_name_is_docker_safe_and_deterministic(self):
        name = oneshot.container_name("dbt-runner", "fx ecb/fx:daily", 3)
        self.assertEqual(name, "k2-dbt-runner-fx-ecb-fx-daily-3")
        self.assertRegex(name, r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")
        self.assertEqual(name, oneshot.container_name("dbt-runner", "fx ecb/fx:daily", 3))


class Lifecycle(unittest.TestCase):
    def test_every_earlier_try_and_this_one_are_removed_before_the_run(self):
        missing = subprocess.CompletedProcess([], 1, "", "Error response from daemon: No such container: k2-fetch-ecb_fx-3\n")
        run = Run(rm=missing)
        run(try_number=3, **FETCH)
        self.assertEqual(run.calls[0], ["docker", "rm", "-f", "k2-fetch-ecb_fx-1", "k2-fetch-ecb_fx-2", "k2-fetch-ecb_fx-3"])
        self.assertEqual(run.calls[1][:2], ["docker", "run"])

    def test_a_removal_that_fails_otherwise_stops_the_run(self):
        run = Run(rm=subprocess.CompletedProcess([], 1, "", "permission denied while trying to connect\n"))
        with self.assertRaises(AirflowException):
            run(**FETCH)
        self.assertEqual(len(run.calls), 1)

    def test_on_kill_kills_this_try_by_name_and_tolerates_it_gone(self):
        run = Run()
        run(try_number=2, **FETCH)
        gone = subprocess.CompletedProcess([], 1, "", "Error response from daemon: No such container: k2-fetch-ecb_fx-2\n")
        with mock.patch.object(oneshot.subprocess, "run", return_value=gone) as kill, \
                mock.patch.object(oneshot.OneShotOperator, "log", mock.MagicMock()):
            run.op.on_kill()
        self.assertEqual(kill.call_args.args[0], ["docker", "kill", "k2-fetch-ecb_fx-2"])

    def test_on_kill_before_a_run_does_nothing(self):
        op = oneshot.OneShotOperator(task_id="t", release_path=RELEASE_PATH, **FETCH)
        with mock.patch.object(oneshot.subprocess, "run") as kill:
            op.on_kill()
        kill.assert_not_called()


class ExitCodes(unittest.TestCase):
    def test_fetch_exit_codes_follow_the_interface(self):
        for code in (0, 30):
            Run(code=code)(**FETCH)  # 30: the bytes land and bronze fails the batch
        with self.assertRaises(AirflowSkipException):
            Run(code=10)(**FETCH)
        with self.assertRaises(AirflowFailException):
            Run(code=20)(**FETCH)
        for code in (1, 125, 137):
            with self.assertRaises(AirflowException) as caught:
                Run(code=code)(**FETCH)
            self.assertIs(type(caught.exception), AirflowException)  # retried, not failed outright

    def test_dbt_pushes_its_last_stdout_line_on_a_pass_and_on_a_failure(self):
        line = '{"exit": 0, "expire": [{"table": "fx.ecb_fx.rates", "ids": [7]}]}'
        run = Run(lines=("dbt says hello", line, ""))
        run(**DBT)
        self.assertEqual(run.pushed["result"], {"exit": 0, "expire": [{"table": "fx.ecb_fx.rates", "ids": [7]}]})
        run = Run(lines=('{"exit": 1, "expire": [{"table": "fx.ecb_fx.rates", "ids": [8]}]}',), code=1)
        with self.assertRaises(AirflowException):
            run(**DBT)
        self.assertEqual(run.pushed["result"]["expire"][0]["ids"], [8])

    def test_dbt_without_its_xcom_line_fails_and_pushes_nothing(self):
        for lines, code in ((("no json",), 0), (('{"exit": 0}',), 2)):
            run = Run(lines=lines, code=code)
            with self.assertRaises(AirflowException):
                run(**DBT)
            self.assertEqual(run.pushed, {})


class NoProductCode(unittest.TestCase):
    """ADR-059's Outcome of 2026-11-27: the scheduler holds the socket and runs no product code."""

    ALLOWED = {"__future__", "json", "os", "re", "shlex", "subprocess", "pathlib", "airflow"}

    def test_it_imports_only_the_standard_library_and_airflow_and_loads_nothing_dynamically(self):
        tree = ast.parse(SOURCE.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "no relative import")
                imported.append(node.module)
            elif isinstance(node, ast.Name):
                self.assertNotIn(node.id, {"exec", "eval", "compile", "__import__", "importlib", "runpy"})
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"import_module", "exec_module", "load_module", "__import__"})
        self.assertEqual({m.split(".")[0] for m in imported} - self.ALLOWED, set())
        self.assertIn("airflow.sdk", imported)


if __name__ == "__main__":
    unittest.main()

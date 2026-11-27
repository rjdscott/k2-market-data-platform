"""
The one-shot operator: one `docker run` of one container kind, with exactly what the run's
release names for that kind in `compiled/oneshots.json` (contracts/schema/oneshots.schema.json,
A13) and nothing else. The run contracts it follows are frozen in
docs/development/phase-001-interfaces.md, "Run-time contracts".

The scheduler holds the Docker socket, so it is inside the catalog's trust boundary (ADR-059's
Outcome of 2026-11-27). What keeps that tolerable is that it runs no product code: parsers run in
`fetch`, models in `dbt-runner`, and neither gets the socket. This module therefore imports only
the standard library and Airflow (docker/airflow/tests/test_oneshot.py holds it to that), and
refuses anything off the release's shape before it starts a container.

Every run, whatever the kind: `--rm`, `--pull never` (images arrive with the deploy, never at run
time), a read-only root with a small `/tmp`, every capability dropped, no new privileges; never
`--privileged`, never the socket, never a mount the release does not list. Environment values go
by name only (`-e NAME`): docker copies each from its own environment, so no value is in argv or
in the logged command line.

The scheduler's own environment gives the host side of each mount and the network prefix:
`K2_RELEASES` and `K2_SECRETS` are host paths (the daemon resolves a bind source on the host, not in
this container), `K2_COMPOSE_PROJECT` the compose project whose `<project>_k2-net` and
`<project>_k2-catalog` the networks are.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from airflow.sdk import BaseOperator
from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException

RELEASES_MOUNT = "/k2/releases"  # k2gen.model.RELEASES_MOUNT
SECRETS_MOUNT = "/run/k2-secrets"  # k2gen.model.SECRETS_MOUNT
TMPFS = "/tmp:rw,noexec,nosuid,size=64m"  # counted against the kind's memory limit

KEYS = {"image", "cpus", "memory", "networks", "env", "secrets"}
NETWORKS = {"k2-net", "k2-catalog"}
# manifest.schema.json#/$defs/imageRef: a registry image by digest, or a local build by image id
IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}"
                   r"|(k2-airflow|k2-capture|k2-spark-iceberg|tools|fetch|dbt-runner|k2-api):[0-9a-f]{12}")
CPUS = re.compile(r"[0-9]+(\.[0-9]+)?")
MEMORY = re.compile(r"[0-9]+[MG]")
ENV = re.compile(r"[A-Z][A-Z0-9_]*")
SECRET = re.compile(r"/run/k2-secrets/[a-z0-9._/-]+")
PASSWORD = re.compile(r"/run/k2-secrets/[a-z0-9_-]+\.password")
PRINCIPAL = re.compile(r"(dbt|pub)_[a-z0-9_]+|svc\.[a-z0-9_-]+")
RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HOST_PATH = re.compile(r"/[^,]*")  # absolute, and no comma: --mount is comma-separated

# Exit codes with a meaning other than failure; any other non-zero exit fails the task.
SKIP = {"fetch": {10}}  # unchanged: nothing new to land
SUCCEED = {"fetch": {0, 30}}  # 30: the parse failed, the bytes still land and bronze fails the batch
NO_RETRY = {"fetch": {20}}  # refused under ADR-036 part 3: the same request is refused again
XCOM_LINE = {"dbt-runner"}  # its last stdout line is {"exit": <n>, "expire": [...]}


def load_oneshots(release_path: str) -> dict:
    return json.loads((Path(release_path) / "compiled" / "oneshots.json").read_text())["oneshots"]


def container_name(kind: str, dag_id: str, try_number: int) -> str:
    """`k2-<kind>-<dagId>-<try>` in Docker's name charset: the same for a retry or a kill to find."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", f"k2-{kind}-{dag_id}-{try_number}")


def password_mount(principal: str) -> str:
    """k2gen.model.password_mount: `builder` for dbt_*, `publisher` for pub_*, `<name>` for svc.<name>."""
    role = {"dbt_": "builder", "pub_": "publisher"}.get(principal[:4], principal.removeprefix("svc."))
    return f"{SECRETS_MOUNT}/{role}.password"


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or len(set(value)) != len(value):
        raise AirflowFailException(f"not a list of distinct strings: {value!r}")
    return value


def check_kind(kind: str, spec: object) -> dict:
    """The kind as oneshots.schema.json gives it, or a refusal naming every problem."""
    if not isinstance(spec, dict) or set(spec) != KEYS:
        raise AirflowFailException(f"oneshots.json {kind}: refused: keys {sorted(spec) if isinstance(spec, dict) else spec!r}, "
                                   f"expected {sorted(KEYS)}")
    bad = [f"{k} {spec[k]!r}" for k, pattern in (("image", IMAGE), ("cpus", CPUS), ("memory", MEMORY))
           if not isinstance(spec[k], str) or not pattern.fullmatch(spec[k])]
    networks, env, secrets = _strings(spec["networks"]), _strings(spec["env"]), _strings(spec["secrets"])
    if not networks or not set(networks) <= NETWORKS:
        bad.append(f"networks {networks}")
    bad += [f"env {e!r}" for e in env if not ENV.fullmatch(e)]
    bad += [f"secret {s!r}" for s in secrets if not SECRET.fullmatch(s) or ".." in s.split("/")]
    if kind == "fetch" and (networks != ["k2-net"] or secrets):
        bad.append("fetch is egress only: k2-net and no secret (A8)")
    if bad:
        raise AirflowFailException(f"oneshots.json {kind}: refused: {'; '.join(bad)}")
    return spec


def _host_path(name: str) -> str:
    value = os.environ.get(name, "")
    if not HOST_PATH.fullmatch(value):
        raise AirflowFailException(f"{name} must be an absolute host path without a comma in the scheduler's environment")
    return value.rstrip("/")


def _tolerate_missing(argv: list[str], log) -> None:
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    errors = [line for line in done.stderr.splitlines() if line.strip() and "No such container" not in line]
    if done.returncode != 0 and errors:
        raise AirflowException(f"{shlex.join(argv)} exited {done.returncode}: {' '.join(errors)}")
    log.info("%s: exit %d", shlex.join(argv), done.returncode)


class OneShotOperator(BaseOperator):
    """Runs one one-shot container of `kind` from the release at `release_path` (`/k2/releases/<id>`,
    from the `resolve` task's XCom). `command` follows the image; `env` gives values for names the
    kind lists (the rest come from the scheduler's environment; `K2_RELEASE` is the release id);
    `principals` names whose password files fill the kind's `<role>.password` mounts."""

    template_fields = ("release_path", "command", "env")

    def __init__(self, *, kind: str, release_path: str, command: list[str], env: dict[str, str] | None = None,
                 principals: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.kind, self.release_path, self.command = kind, release_path, command
        self.env, self.principals = env or {}, principals or []
        self._name: str | None = None

    def docker_run(self, name: str) -> tuple[list[str], dict[str, str]]:
        """The argument list and the docker CLI's environment, or a refusal before anything runs."""
        match = re.fullmatch(re.escape(RELEASES_MOUNT) + r"/(.+)", self.release_path)
        if not match or not RELEASE.fullmatch(match[1]) or match[1] == "pointers":
            raise AirflowFailException(f"release_path {self.release_path!r} is not {RELEASES_MOUNT}/<release id>")
        release = match[1]
        kinds = load_oneshots(self.release_path)
        if self.kind not in kinds:
            raise AirflowFailException(f"{self.kind}: not a one-shot kind of release {release} ({sorted(kinds)})")
        spec = check_kind(self.kind, kinds[self.kind])

        values = {**os.environ, **self.env, "K2_RELEASE": release}
        if extra := sorted(set(self.env) - set(spec["env"])):
            raise AirflowFailException(f"{self.kind}: refused env {extra}: oneshots.json lists only {spec['env']}")
        if unset := [e for e in spec["env"] if not values.get(e)]:
            raise AirflowFailException(f"{self.kind}: no value for {unset}, in the task's env or the scheduler's")

        mounts = [(_host_path("K2_RELEASES") + "/" + release, f"{RELEASES_MOUNT}/{release}")]
        by_role = {}
        for p in self.principals:
            if not PRINCIPAL.fullmatch(p) or password_mount(p) not in spec["secrets"] or password_mount(p) in by_role:
                raise AirflowFailException(f"{self.kind}: refused principal {p!r}: its mount {password_mount(p)} "
                                           f"is not one of {spec['secrets']} or is filled twice")
            by_role[password_mount(p)] = f"principals/{p}.password"  # k2gen.model.password_file
        for target in spec["secrets"]:
            if PASSWORD.fullmatch(target) and target not in by_role:
                raise AirflowFailException(f"{self.kind}: {target} is listed and no principal fills it")
            relative = by_role.get(target, target.removeprefix(SECRETS_MOUNT + "/"))
            mounts.append((f"{_host_path('K2_SECRETS')}/{relative}", target))

        project = os.environ.get("K2_COMPOSE_PROJECT", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
            raise AirflowFailException("K2_COMPOSE_PROJECT must name the compose project in the scheduler's environment")

        argv = ["docker", "run", "--rm", "--name", name, "--pull", "never",
                "--read-only", "--tmpfs", TMPFS, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", spec["cpus"], "--memory", spec["memory"]]
        for network in spec["networks"]:
            argv += ["--network", f"{project}_{network}"]
        for e in spec["env"]:
            argv += ["-e", e]
        for source, target in mounts:
            argv += ["--mount", f"type=bind,source={source},target={target},readonly"]
        return [*argv, spec["image"], *self.command], {**os.environ, **{e: values[e] for e in spec["env"]}}

    def execute(self, context) -> None:
        ti = context["ti"]
        name = container_name(self.kind, ti.dag_id, ti.try_number)
        argv, env = self.docker_run(name)
        # A crashed earlier try, or this try's name left by a crashed run, must not run beside this one.
        _tolerate_missing(["docker", "rm", "-f", *(container_name(self.kind, ti.dag_id, t)
                                                   for t in range(1, ti.try_number + 1))], self.log)
        self._name = name
        self.log.info("one-shot: %s", shlex.join(argv))
        # stdin is the task supervisor's request socket: never hand it on. stderr goes to the
        # task's own stderr, which the supervisor logs; stdout is read here for the last line.
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                text=True, errors="replace")
        last = ""
        for line in proc.stdout:
            self.log.info("%s", line.rstrip("\n"))
            last = line.strip() or last
        code = proc.wait()

        if self.kind in XCOM_LINE:
            try:
                result = json.loads(last)
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get("exit") == code:
                ti.xcom_push(key="result", value=result)  # on a failure too: expire reads its ids
            elif code == 0:
                raise AirflowException(f"{name} exited 0 and its last stdout line is not its XCom: {last!r}")
        if code in SKIP.get(self.kind, set()):
            raise AirflowSkipException(f"{name} exited {code}")
        if code in NO_RETRY.get(self.kind, set()):
            raise AirflowFailException(f"{name} exited {code}")
        if code not in SUCCEED.get(self.kind, {0}):
            raise AirflowException(f"{name} exited {code}")
        if code:
            self.log.warning("%s exited %d", name, code)

    def on_kill(self) -> None:
        if self._name:  # docker kill, then --rm removes it
            _tolerate_missing(["docker", "kill", self._name], self.log)

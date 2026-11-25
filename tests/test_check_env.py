"""scripts/check-env.py, the guard `make up` and scripts/dev-up.sh run before
`docker compose up`. check() on dicts; then the script end to end on scratch .env
files, through the `docker compose config --environment` it reads, for the lines
compose resolves differently from a naive parse."""

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
guard = importlib.import_module("check-env")

EXAMPLE = (ROOT / ".env.example").read_text()
NAMES = [n for n, _ in guard.SECRET.findall(EXAMPLE)]
USER = "k2-minio-root"


def filled(**overrides):
    """Every secret distinct and real-looking, MinIO user as .env.example ships it."""
    env = {n: f"filled-{i}-value" for i, n in enumerate(NAMES)}
    return {**env, "MINIO_ROOT_USER": USER, **overrides}


def test_the_shipped_example_does_not_pair_minio_user_and_password():
    user = re.search(r"^MINIO_ROOT_USER=(.*)$", EXAMPLE, re.M).group(1)
    password = re.search(r"^MINIO_ROOT_PASSWORD=(.*)$", EXAMPLE, re.M).group(1)
    assert user == USER and user != password


def test_the_example_as_is_fails_on_every_secret():
    env = dict(line.split("=", 1) for line in EXAMPLE.splitlines() if re.match(r"^[A-Z0-9_]+=", line))
    problems = guard.check(env, EXAMPLE)
    assert "MINIO_ROOT_PASSWORD" in NAMES and "LAKEKEEPER_ENCRYPTION_KEY" in NAMES
    assert len(problems) == len(NAMES) and all(any(p.startswith(n + " ") for p in problems) for n in NAMES)


def test_every_secret_filled_passes():
    assert guard.check(filled(), EXAMPLE) == []


def test_equal_pair_placeholder_empty_and_unset_each_fail_by_name():
    assert guard.check(filled(MINIO_ROOT_PASSWORD=USER), EXAMPLE) == [
        "MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD; set the password to `openssl rand -hex 24`"
    ]
    env = filled(GRAFANA_PASSWORD="change-me-in-production", AIRFLOW_JWT_SECRET="")
    del env["CLICKHOUSE_PASSWORD"]
    named = [p.split()[0] for p in guard.check(env, EXAMPLE)]
    assert named == ["CLICKHOUSE_PASSWORD", "AIRFLOW_JWT_SECRET", "GRAFANA_PASSWORD"]


# ── end to end, through compose ─────────────────────────────────────────────

def compose_has_environment():
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "compose", "config", "--help"], capture_output=True, text=True)
    return "--environment" in r.stdout


needs_compose = pytest.mark.skipif(not compose_has_environment(), reason="needs docker compose config --environment")


def env_text(**overrides):
    return "".join(f"{n}={v}\n" for n, v in filled(**overrides).items())


def run(tmp_path, text, **shell):
    f = tmp_path / ".env"
    f.write_bytes(text.encode())
    # Only what reaches the Docker engine, plus the case's own shell variables: a
    # developer shell with .env sourced must not decide the result.
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME") or k.startswith("DOCKER_")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check-env.py"), "--env-file", str(f)],
                       capture_output=True, text=True, env={**base, **shell})
    out = r.stdout + r.stderr
    assert "canary" not in out, "a value was printed"
    return r.returncode, out


@needs_compose
def test_a_filled_file_passes_through_compose(tmp_path):
    rc, out = run(tmp_path, env_text())
    assert rc == 0 and "PASS" in out


@needs_compose
@pytest.mark.parametrize("case, line, fails", [
    ("inline comment", "MINIO_ROOT_PASSWORD=change-me-in-production # canary note", "MINIO_ROOT_PASSWORD is"),
    ("export", f"export MINIO_ROOT_PASSWORD={USER}", "MINIO_ROOT_USER equals"),
    ("spaces around =", f"MINIO_ROOT_PASSWORD = {USER}", "MINIO_ROOT_USER equals"),
    ("trailing space", f"MINIO_ROOT_PASSWORD={USER}   ", "MINIO_ROOT_USER equals"),
])
def test_lines_compose_reads_differently_still_fail(tmp_path, case, line, fails):
    rc, out = run(tmp_path, env_text() + line + "\n")
    assert rc == 1 and fails in out, case


@needs_compose
def test_crlf_endings_still_fail(tmp_path):
    rc, out = run(tmp_path, env_text(GRAFANA_PASSWORD="change-me-in-production").replace("\n", "\r\n"))
    assert rc == 1 and "GRAFANA_PASSWORD is" in out


@needs_compose
def test_a_value_exported_in_the_shell_overrides_the_file(tmp_path):
    rc, out = run(tmp_path, env_text(), MINIO_ROOT_PASSWORD=USER)
    assert rc == 1 and "MINIO_ROOT_USER equals" in out
    rc, _ = run(tmp_path, env_text(MINIO_ROOT_PASSWORD=USER), MINIO_ROOT_PASSWORD="canary-from-shell")
    assert rc == 0

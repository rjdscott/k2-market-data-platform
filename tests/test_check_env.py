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
    """Every secret distinct, long and real-looking, users as .env.example ships them
    (the database role keeps its historical name, ADR-034)."""
    env = {n: f"filled-{i}-value-7f3a9c2e" for i, n in enumerate(NAMES)}
    keys = {f"K2_{e}_S3_ACCESS_KEY": f"k2-{e.lower()}-0a1b2c3d" for e in guard.ENGINES}
    return {**env, **keys, "MINIO_ROOT_USER": USER, "PREFECT_DB_USER": "prefect", **overrides}


def test_the_shipped_example_does_not_pair_minio_user_and_password():
    user = re.search(r"^MINIO_ROOT_USER=(.*)$", EXAMPLE, re.M).group(1)
    password = re.search(r"^MINIO_ROOT_PASSWORD=(.*)$", EXAMPLE, re.M).group(1)
    assert user == USER and user != password


def test_the_example_as_is_fails_on_every_secret():
    env = dict(line.split("=", 1) for line in EXAMPLE.splitlines() if re.match(r"^[A-Z0-9_]+=", line))
    problems = guard.check(env, EXAMPLE)
    assert "MINIO_ROOT_PASSWORD" in NAMES and "LAKEKEEPER_ENCRYPTION_KEY" in NAMES
    access = [f"K2_{e}_S3_ACCESS_KEY" for e in guard.ENGINES]
    assert len(problems) == len(NAMES) + len(access)
    assert all(any(p.startswith(n + " ") for p in problems) for n in NAMES + access)


def test_every_engine_has_a_pair_in_the_example_and_the_table():
    assert guard.ENGINES == ["BACKUP", "MAINTENANCE", "SPARK", "TOOLS", "TRINO"]
    for e in guard.ENGINES:
        assert f"K2_{e}_S3_SECRET_KEY" in NAMES and guard.CREDENTIALS[f"K2_{e}_S3_SECRET_KEY"] == f"$K2_{e}_S3_ACCESS_KEY"
        assert re.search(rf"^K2_{e}_S3_ACCESS_KEY=k2-{e.lower()}-placeholder$", EXAMPLE, re.M)


@pytest.mark.parametrize("overrides, says", [
    ({"K2_SPARK_S3_ACCESS_KEY": ""}, "K2_SPARK_S3_ACCESS_KEY is unset"),
    ({"K2_TOOLS_S3_ACCESS_KEY": "k2-tools-placeholder"}, "K2_TOOLS_S3_ACCESS_KEY is unset, empty or still the .env.example placeholder"),
    ({"K2_TRINO_S3_ACCESS_KEY": USER}, "K2_TRINO_S3_ACCESS_KEY equals MINIO_ROOT_USER"),
    ({"K2_BACKUP_S3_SECRET_KEY": "root-password-canary-long"}, "K2_BACKUP_S3_SECRET_KEY equals MINIO_ROOT_PASSWORD"),
    ({"K2_TRINO_S3_ACCESS_KEY": "k2-maintenance-0a1b2c3d"}, "K2_TRINO_S3_ACCESS_KEY is also K2_MAINTENANCE_S3_ACCESS_KEY's"),
])
def test_a_storage_pair_unset_root_or_shared_is_refused_by_name(overrides, says):
    env = filled(MINIO_ROOT_PASSWORD="root-password-canary-long", **overrides)
    problems = guard.check(env, EXAMPLE)
    assert len(problems) == 1 and says in problems[0], problems
    assert "canary" not in problems[0] and "0a1b2c3d" not in problems[0] and USER not in problems[0]


def test_a_secret_unset_is_named_once():
    env = filled()
    del env["K2_SPARK_S3_SECRET_KEY"]
    assert [p.split()[0] for p in guard.check(env, EXAMPLE)] == ["K2_SPARK_S3_SECRET_KEY"]


def test_every_secret_filled_passes():
    assert guard.check(filled(), EXAMPLE) == []


def test_the_table_names_every_secret_compose_and_the_example_pass():
    compose = (ROOT / "docker-compose.yml").read_text()
    in_compose = set(re.findall(r"\$\{([A-Z0-9_]*(?:PASSWORD|SECRET|(?<!ACCESS_)KEY|TOKEN))\b", compose))
    assert in_compose and in_compose | set(NAMES) <= guard.CREDENTIALS.keys()
    # every $NAME user the table points at is a variable compose really reads
    users = {u[1:] for u in guard.CREDENTIALS.values() if u and u.startswith("$")}
    assert users <= set(re.findall(r"\$\{([A-Z0-9_]+)", compose))


def test_equal_pair_placeholder_empty_and_unset_each_fail_by_name():
    assert guard.check(filled(MINIO_ROOT_PASSWORD=USER), EXAMPLE) == [
        "MINIO_ROOT_PASSWORD equals MINIO_ROOT_USER; set it to `openssl rand -hex 24`"
    ]
    env = filled(GRAFANA_PASSWORD="change-me-in-production", AIRFLOW_JWT_SECRET="")
    del env["CLICKHOUSE_PASSWORD"]
    named = [p.split()[0] for p in guard.check(env, EXAMPLE)]
    assert named == ["CLICKHOUSE_PASSWORD", "AIRFLOW_JWT_SECRET", "GRAFANA_PASSWORD"]


@pytest.mark.parametrize("name, value, why", [
    # the audit's probes: each passed before
    ("PREFECT_DB_PASSWORD", "prefect", "equals PREFECT_DB_USER"),
    ("PREFECT_DB_PASSWORD", "PREFECT", "equals PREFECT_DB_USER"),
    ("CLICKHOUSE_PASSWORD", "x", "shorter than 16"),
    ("GRAFANA_PASSWORD", "admin", "equals its user name `admin`"),
    ("CLICKHOUSE_PASSWORD", "default", "equals its user name `default`"),
    ("K2_QUANT_PASSWORD", "Quant", "equals its user name `quant`"),
    ("AIRFLOW_DB_PASSWORD", "airflow", "equals its user name `airflow`"),
    ("MINIO_ROOT_PASSWORD", "minioadmin", "known default"),
    ("AIRFLOW_JWT_SECRET", "password", "known default"),
    ("LAKEKEEPER_ENCRYPTION_KEY", "k2-minio-root", "known default or a value .env.example ships"),
    ("AIRFLOW_API_SECRET_KEY", "fifteen-chars!!", "shorter than 16"),
])
def test_every_credential_refuses_its_user_a_default_or_a_short_value(name, value, why):
    problems = guard.check(filled(**{name: value}), EXAMPLE)
    assert len(problems) == 1 and problems[0].startswith(name + " ") and why in problems[0], problems


def test_no_rule_prints_the_value():
    env = filled(PREFECT_DB_USER="canary-user-name", PREFECT_DB_PASSWORD="canary-user-name",
                 CLICKHOUSE_PASSWORD="canary", GRAFANA_PASSWORD="canary-is-sixteen-plus")
    problems = guard.check(env, EXAMPLE + "SOMETHING=canary-is-sixteen-plus\n")
    assert len(problems) == 3 and not any("canary" in p for p in problems)


def test_sixteen_characters_pass():
    assert guard.check(filled(AIRFLOW_API_SECRET_KEY="sixteen-chars!!!"), EXAMPLE) == []


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
    f.chmod(0o600)
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
    ("export", f"export MINIO_ROOT_PASSWORD={USER}", "MINIO_ROOT_PASSWORD equals"),
    ("spaces around =", f"MINIO_ROOT_PASSWORD = {USER}", "MINIO_ROOT_PASSWORD equals"),
    ("trailing space", f"MINIO_ROOT_PASSWORD={USER}   ", "MINIO_ROOT_PASSWORD equals"),
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
    assert rc == 1 and "MINIO_ROOT_PASSWORD equals" in out
    rc, _ = run(tmp_path, env_text(MINIO_ROOT_PASSWORD=USER), MINIO_ROOT_PASSWORD="canary-from-the-shell")
    assert rc == 0


@pytest.mark.parametrize("mode, fails", [(0o600, False), (0o400, False), (0o640, True), (0o604, True), (0o620, True), (0o664, True)])
def test_a_file_open_to_its_group_or_others_fails_with_the_fix(tmp_path, mode, fails):
    f = tmp_path / ".env"
    f.write_text("A_SECRET=canary-mode-value\n")
    f.chmod(mode)
    problem = guard.mode_problem(f)
    assert (problem is not None) is fails
    assert not fails or f"chmod 600 {f}" in problem and "canary" not in problem
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check-env.py"), "--mode-only", "--env-file", str(f)],
                       capture_output=True, text=True)
    assert r.returncode == (1 if fails else 0) and "canary" not in r.stdout + r.stderr
    assert guard.mode_problem(tmp_path / "absent") is None

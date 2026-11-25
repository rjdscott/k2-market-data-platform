"""scripts/check-env.sh, the guard `make up` and scripts/dev-up.sh run before
`docker compose up`: run on scratch .env files against the real .env.example."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
SECRET = re.compile(r"^([A-Z0-9_]*(?:PASSWORD|SECRET|KEY))=", re.M)


def run(tmp_path, text):
    env = tmp_path / ".env"
    env.write_text(text)
    r = subprocess.run(["bash", str(ROOT / "scripts/check-env.sh"), str(env), str(EXAMPLE)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def filled(**overrides):
    """.env.example with every secret set to a distinct value, then overrides."""
    text = EXAMPLE.read_text()
    for i, name in enumerate(SECRET.findall(text)):
        text = re.sub(rf"^{name}=.*$", f"{name}=filled-{i}-value", text, flags=re.M)
    for name, value in overrides.items():
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
    return text


def test_the_shipped_example_does_not_pair_minio_user_and_password():
    text = EXAMPLE.read_text()
    user = re.search(r"^MINIO_ROOT_USER=(.*)$", text, re.M).group(1)
    password = re.search(r"^MINIO_ROOT_PASSWORD=(.*)$", text, re.M).group(1)
    assert user and user != password


def test_a_copy_of_the_example_is_refused_naming_every_placeholder(tmp_path):
    rc, out = run(tmp_path, EXAMPLE.read_text())
    assert rc == 1
    names = SECRET.findall(EXAMPLE.read_text())
    assert "MINIO_ROOT_PASSWORD" in names and "LAKEKEEPER_ENCRYPTION_KEY" in names
    for name in names:
        assert f"{name} is empty or still" in out


def test_every_secret_filled_passes(tmp_path):
    rc, out = run(tmp_path, filled())
    assert rc == 0 and "PASS" in out


def test_minio_user_equal_to_password_is_refused_without_printing_it(tmp_path):
    rc, out = run(tmp_path, filled(MINIO_ROOT_USER="s3cr3t-same", MINIO_ROOT_PASSWORD='"s3cr3t-same"'))
    assert rc == 1
    assert "MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD" in out
    assert "s3cr3t-same" not in out


def test_one_placeholder_or_empty_secret_is_refused_by_name_only(tmp_path):
    rc, out = run(tmp_path, filled(GRAFANA_PASSWORD="change-me-in-production", AIRFLOW_JWT_SECRET=""))
    assert rc == 1
    assert "GRAFANA_PASSWORD is empty or still" in out and "AIRFLOW_JWT_SECRET is empty or still" in out
    assert "CLICKHOUSE_PASSWORD" not in out
    assert "change-me-in-production" not in out


def test_the_last_assignment_wins_as_compose_reads_it(tmp_path):
    rc, _ = run(tmp_path, filled() + "\nGRAFANA_PASSWORD=change-me-in-production\n")
    assert rc == 1
    rc, _ = run(tmp_path, EXAMPLE.read_text() + "\n" + filled())
    assert rc == 0

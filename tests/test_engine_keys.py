"""scripts/engine-keys.py on scratch env files: writes only what is missing or a placeholder, never
a value to its output, keeps the file and its backup at mode 600, a second run writes nothing,
--rotate replaces one engine's pair alone, and the result passes check-env.py's scoped-key checks."""

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
keys = importlib.import_module("engine-keys")
guard = importlib.import_module("check-env")
EXAMPLE = (ROOT / ".env.example").read_text()
NAMES = [n for e in keys.ENGINES for n in keys.pair_names(e)] + ["K2_RUN_UID", "K2_RUN_GID"]


def run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "scripts/engine-keys.py"), "--env-file", str(path), *args],
                          capture_output=True, text=True)


def scratch(tmp_path: Path, text: str = EXAMPLE) -> Path:
    f = tmp_path / ".env"
    f.write_text(text)
    f.chmod(0o600)
    return f


def written(out: str) -> list[str]:
    return re.findall(r"^engine-keys: wrote (\S+)$", out, re.M)


def test_fourteen_names_on_the_example_then_none(tmp_path):
    assert keys.ENGINES == ["backup", "maintenance", "restore", "spark", "tools", "trino"]
    f = scratch(tmp_path)
    first = run(f)
    assert first.returncode == 0 and sorted(written(first.stdout)) == sorted(NAMES) and len(NAMES) == 14
    got = keys.values(f.read_text())
    for e in keys.ENGINES:
        ak, sk = keys.pair_names(e)
        assert re.fullmatch(rf"k2-{e}-[0-9a-f]{{8}}", got[ak]) and len(got[sk]) == 40
        assert got[ak] not in first.stdout + first.stderr and got[sk] not in first.stdout + first.stderr
    assert got["K2_RUN_UID"] == str(os.getuid()) and got["K2_RUN_GID"] == str(os.getgid())
    assert len({got[keys.pair_names(e)[0]] for e in keys.ENGINES}) == 6
    assert f.stat().st_mode & 0o777 == 0o600
    backups = list(tmp_path.glob(".env.bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == EXAMPLE and backups[0].stat().st_mode & 0o777 == 0o600
    # Every other line as it was, in its place.
    assert [ln for ln in f.read_text().splitlines() if not any(ln.startswith(n + "=") for n in NAMES)] == \
        [ln for ln in EXAMPLE.splitlines() if not any(ln.startswith(n + "=") for n in NAMES)]
    second = run(f)
    assert second.returncode == 0 and written(second.stdout) == [] and "nothing to write" in second.stdout
    assert keys.values(f.read_text()) == got and len(list(tmp_path.glob(".env.bak-*"))) == 1
    assert not list(tmp_path.glob(".env.engine-keys-*"))  # no temporary file left


def test_a_real_value_is_kept_and_a_missing_name_appended(tmp_path):
    text = EXAMPLE.replace("K2_TRINO_S3_ACCESS_KEY=k2-trino-placeholder", "K2_TRINO_S3_ACCESS_KEY=k2-trino-ownvalue1")
    text = text.replace("K2_TRINO_S3_SECRET_KEY=change-me-in-production", "K2_TRINO_S3_SECRET_KEY=a-real-secret-0123456789")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("K2_TOOLS_S3_SECRET_KEY=")) + "\n"
    f = scratch(tmp_path, text)
    out = run(f).stdout
    assert "K2_TRINO_S3_ACCESS_KEY" not in written(out) and "K2_TOOLS_S3_SECRET_KEY" in written(out)
    got = keys.values(f.read_text())
    assert got["K2_TRINO_S3_ACCESS_KEY"] == "k2-trino-ownvalue1" and got["K2_TRINO_S3_SECRET_KEY"] == "a-real-secret-0123456789"
    assert f.read_text().count("K2_TOOLS_S3_SECRET_KEY=") == 1 and len(got["K2_TOOLS_S3_SECRET_KEY"]) == 40


def test_rotate_replaces_one_engines_secret_and_keeps_its_user(tmp_path):
    # The same access key, so lake-init's `mc admin user add` sets that user's secret and the old
    # secret stops working; a new key would leave the old user, policy and secret all valid.
    f = scratch(tmp_path)
    run(f)
    before = keys.values(f.read_text())
    out = run(f, "--rotate", "TRINO")
    assert out.returncode == 0 and written(out.stdout) == ["K2_TRINO_S3_SECRET_KEY"]
    after = keys.values(f.read_text())
    assert {n for n in after if after[n] != before.get(n)} == {"K2_TRINO_S3_SECRET_KEY"}
    assert len(after["K2_TRINO_S3_SECRET_KEY"]) == 40
    assert run(f, "--rotate", "fetch").returncode == 2  # fetch has no key and never gets one


def test_a_pair_left_on_the_root_pair_gets_its_own_and_nothing_else_moves(tmp_path):
    # The release before phase 002 shipped Trino on MinIO's root pair (its .env.example); lake-init
    # refuses that pair, so it counts as unset, both names of it, whichever half is the root's.
    f = scratch(tmp_path)
    run(f)
    root_pw = "a-root-password-0123456789"
    f.write_text(keys.rewrite(f.read_text(), {"MINIO_ROOT_PASSWORD": root_pw, "K2_TRINO_S3_ACCESS_KEY": "k2-minio-root",
                                              "K2_TRINO_S3_SECRET_KEY": root_pw}))
    before = keys.values(f.read_text())
    out = run(f)
    assert out.returncode == 0 and sorted(written(out.stdout)) == ["K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"]
    assert root_pw not in out.stdout + out.stderr and "k2-minio-root" not in out.stdout + out.stderr
    after = keys.values(f.read_text())
    assert {n for n in after if after[n] != before.get(n)} == {"K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"}
    assert re.fullmatch(r"k2-trino-[0-9a-f]{8}", after["K2_TRINO_S3_ACCESS_KEY"]) and after["K2_TRINO_S3_SECRET_KEY"] != root_pw
    assert guard.scoped_problems(after, EXAMPLE) == []
    second = run(f)
    assert written(second.stdout) == [] and keys.values(f.read_text()) == after


def test_either_half_on_the_root_pair_replaces_both():
    root = {"MINIO_ROOT_USER": "k2-minio-root", "MINIO_ROOT_PASSWORD": "a-root-password-0123456789"}
    ids = {"K2_RUN_UID": "1", "K2_RUN_GID": "1"}
    pairs = {n: f"own-{n}-0123456789" for e in keys.ENGINES for n in keys.pair_names(e)}
    for half, value in (("K2_SPARK_S3_ACCESS_KEY", "k2-minio-root"), ("K2_SPARK_S3_SECRET_KEY", "a-root-password-0123456789"),
                        ("K2_SPARK_S3_ACCESS_KEY", "a-root-password-0123456789"), ("K2_SPARK_S3_SECRET_KEY", "k2-minio-root")):
        got = keys.plan({**root, **ids, **pairs, half: value}, {}, set(), 1, 1)
        assert sorted(got) == ["K2_SPARK_S3_ACCESS_KEY", "K2_SPARK_S3_SECRET_KEY"], half
    # An empty root password matches no empty secret: that one is unset, and unset alone.
    assert keys.plan({**ids, **pairs, "MINIO_ROOT_USER": "k2-minio-root"}, {}, set(), 1, 1) == {}


def test_a_new_access_key_never_takes_another_engines_or_the_roots(monkeypatch):
    draws = iter(["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(keys.secrets, "token_hex", lambda n: next(draws))
    current = {"MINIO_ROOT_USER": "k2-spark-aaaaaaaa", "K2_TOOLS_S3_ACCESS_KEY": "k2-tools-x",
               **{n: "x" * 40 for e in keys.ENGINES for n in keys.pair_names(e)[1:]}}
    current |= {"K2_BACKUP_S3_ACCESS_KEY": "b", "K2_MAINTENANCE_S3_ACCESS_KEY": "m", "K2_TRINO_S3_ACCESS_KEY": "t", "K2_RESTORE_S3_ACCESS_KEY": "r",
                "K2_RUN_UID": "1", "K2_RUN_GID": "1"}
    assert keys.plan(current, {}, set(), 1, 1) == {"K2_SPARK_S3_ACCESS_KEY": "k2-spark-bbbbbbbb"}


def test_the_written_file_passes_check_envs_scoped_key_checks(tmp_path):
    f = scratch(tmp_path)
    run(f)
    env = keys.values(f.read_text()) | {"MINIO_ROOT_PASSWORD": "a-root-password-0123456789"}
    assert guard.scoped_problems(env, EXAMPLE) == []
    assert not [p for p in guard.check(env, EXAMPLE) if "_S3_" in p]


def test_a_missing_file_is_refused(tmp_path):
    out = run(tmp_path / ".env")
    assert out.returncode == 2 and "cp .env.example" in out.stderr and not (tmp_path / ".env").exists()


# --- A rotation reaches MinIO only when lake-init reruns (audit 2026-11-27, HIGH 8) ------------
# A deploy runs lake-init only when its release changes lake-init; a rotation changes .env alone.


def test_each_engines_holders_are_the_services_compose_gives_its_secret():
    assert keys.HOLDERS == {"backup": [], "maintenance": ["spark-iceberg"], "restore": [], "spark": ["spark-iceberg"],
                            "tools": [], "trino": ["trino"]}


def test_a_rotation_names_the_lake_init_rerun_and_the_services_to_recreate(tmp_path):
    f = scratch(tmp_path)
    run(f)
    out = run(f, "--rotate", "trino,maintenance")
    assert out.returncode == 0
    assert 'make lake-init RECREATE="spark-iceberg trino"' in out.stdout
    assert "until then MinIO keeps the old secret" in out.stdout
    assert "make lake-init" in run(f, "--rotate", "backup").stdout  # no holder to recreate, the rerun still


FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$LOG"
case "$1" in
  compose) for a; do [ "$prev" = -f ] && last=$a; prev=$a; done
           echo "env K2_LAKE_INIT_RELEASE=$K2_LAKE_INIT_RELEASE K2_STORAGE_PLAN=$K2_STORAGE_PLAN" >> "$LOG"
           echo "pin $(cat "$last")" >> "$LOG" ;;
  wait) echo "$WAIT_CODE" ;;
esac
"""


def make_lake_init(tmp_path, wait_code="0", recreate="trino"):
    """`make lake-init` with a fake docker on PATH, over a scratch releases root."""
    rid = "v3.0.0-alpha.4-0123456789ab"
    (tmp_path / "releases" / rid).mkdir(parents=True)
    (tmp_path / "releases" / rid / "manifest.json").write_text(
        '{"images": {"lake-init": "minio@sha256:aa", "trino": "trinodb/trino@sha256:bb", "minio": "minio@sha256:aa"}}')
    (tmp_path / "releases" / "pointers").mkdir()
    (tmp_path / "releases" / "pointers" / "trino").symlink_to(Path("..") / rid)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "docker").write_text(FAKE_DOCKER)
    (tmp_path / "bin" / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "K2_RELEASES": str(tmp_path / "releases"),
           "LOG": str(tmp_path / "log"), "WAIT_CODE": wait_code, "RECREATE": recreate}
    env.pop("K2_COMPOSE_PROJECT", None)
    out = subprocess.run(["make", "-s", "-C", str(ROOT), "lake-init"], env=env, capture_output=True, text=True)
    return rid, out, (tmp_path / "log").read_text().splitlines() if (tmp_path / "log").exists() else []


def test_make_lake_init_runs_deploys_invocation_on_the_live_release_then_recreates(tmp_path):
    rid, out, log = make_lake_init(tmp_path)
    assert out.returncode == 0, out.stderr
    up = re.search(r'"(up)", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", s\)',
                   (ROOT / "scripts/deploy.py").read_text())
    assert up, "deploy's one-shot invocation moved: hold make lake-init to it again"
    flags = "up -d --no-deps --no-build --pull never --force-recreate"
    compose = f"-f {tmp_path}/releases/{rid}/compose/docker-compose.yml"
    assert log[0].startswith("docker compose --project-directory ") and compose in log[0]
    assert log[0].endswith(f"{flags} lake-init")
    assert log[1] == f"env K2_LAKE_INIT_RELEASE={rid} K2_STORAGE_PLAN=/k2/releases/{rid}/compiled/lake/storage-plan.json"
    assert log[2] == 'pin {"services": {"lake-init": {"image": "minio@sha256:aa"}, "trino": {"image": "trinodb/trino@sha256:bb"}}}'
    assert log[3] == "docker wait k2-lake-init"
    assert log[4].endswith(f"{flags} trino") and compose in log[4]
    assert f"lake-init: release {rid}, exit 0" in out.stdout


def test_a_failed_lake_init_fails_the_target_and_recreates_nothing(tmp_path):
    _, out, log = make_lake_init(tmp_path, wait_code="1")
    assert out.returncode != 0 and log[-1] == "docker wait k2-lake-init"


def test_no_live_release_is_refused(tmp_path):
    env = {**os.environ, "K2_RELEASES": str(tmp_path)}
    out = subprocess.run(["make", "-s", "-C", str(ROOT), "lake-init"], env=env, capture_output=True, text=True)
    assert out.returncode != 0 and "no release at pointers/trino" in out.stderr

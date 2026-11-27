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


def test_twelve_names_on_the_example_then_none(tmp_path):
    assert keys.ENGINES == ["backup", "maintenance", "spark", "tools", "trino"]
    f = scratch(tmp_path)
    first = run(f)
    assert first.returncode == 0 and sorted(written(first.stdout)) == sorted(NAMES) and len(NAMES) == 12
    got = keys.values(f.read_text())
    for e in keys.ENGINES:
        ak, sk = keys.pair_names(e)
        assert re.fullmatch(rf"k2-{e}-[0-9a-f]{{8}}", got[ak]) and len(got[sk]) == 40
        assert got[ak] not in first.stdout + first.stderr and got[sk] not in first.stdout + first.stderr
    assert got["K2_RUN_UID"] == str(os.getuid()) and got["K2_RUN_GID"] == str(os.getgid())
    assert len({got[keys.pair_names(e)[0]] for e in keys.ENGINES}) == 5
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


def test_rotate_replaces_one_engines_pair_alone(tmp_path):
    f = scratch(tmp_path)
    run(f)
    before = keys.values(f.read_text())
    out = run(f, "--rotate", "TRINO")
    assert out.returncode == 0 and sorted(written(out.stdout)) == ["K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"]
    after = keys.values(f.read_text())
    assert {n for n in after if after[n] != before.get(n)} == {"K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"}
    assert run(f, "--rotate", "fetch").returncode == 2  # fetch has no key and never gets one


def test_a_new_access_key_never_takes_another_engines_or_the_roots(monkeypatch):
    draws = iter(["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(keys.secrets, "token_hex", lambda n: next(draws))
    current = {"MINIO_ROOT_USER": "k2-spark-aaaaaaaa", "K2_TOOLS_S3_ACCESS_KEY": "k2-tools-x",
               **{n: "x" * 40 for e in keys.ENGINES for n in keys.pair_names(e)[1:]}}
    current |= {"K2_BACKUP_S3_ACCESS_KEY": "b", "K2_MAINTENANCE_S3_ACCESS_KEY": "m", "K2_TRINO_S3_ACCESS_KEY": "t",
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

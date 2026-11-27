"""scripts/backup-lake.sh against fakes on PATH. `docker` plays three parts:
`inspect` finds k2-minio's networks, `exec` is pg_dump, and `run` is either the
catalog's curl container (handed to the fake `curl`) or the mc container, mirroring a scratch "bucket" directory into the bind-mounted backup
the way mc RELEASE.2025-08-13 does (verified 2026-11-26 in the image the script
pins): a missing file is copied, a differing one is refused with a JSON error
line and exit 0, and only `--overwrite` or `--remove` would replace or delete.
So the fake honours those two flags, and the additive tests fail if the script
ever passes them. `curl` is the catalog, with a credential in the table's
config block, as a catalog may return, which the script must never print.

The script ships with no table to copy until phase 002 (the old lake ingest's
tables were dropped): the fixture's copy of it is given four synthetic tables, so
the copy path keeps its tests, and test_shipped_with_no_tables runs it as shipped."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
USER = "dummy-access-key-7f3a9c"
PASSWORD = "dummy-secret-value-0b8e4d2a6c"
TABLES = ["landing.events", "landing.checks", "ref.dim_one", "ref.dim_two"]

FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$LOG"
case "$1" in
  inspect) echo "${NETS:-k2-test-net }" ;;
  exec)
    [ -n "${DUMP_BROKEN:-}" ] && { echo "-- PostgreSQL database dump"; exit 0; }
    printf -- '-- PostgreSQL database dump\nCREATE TABLE t ();\n-- PostgreSQL database dump complete\n\n\\unrestrict x\n' ;;
  run)
    if [[ " $* " == *" --entrypoint curl "* ]]; then
      while [[ $1 != ghcr.io/rjdscott/minio@* ]]; do shift; done
      shift; exec curl "$@"
    fi
    echo "fake docker: $*" >&2   # a docker that echoes its command line, as some errors do
    [ -n "${MC_DOWN:-}" ] && { echo "mc: MinIO did not answer" >&2; exit 1; }
    [[ "${MC_HOST_k2:-}" == *":$WANT_PASSWORD@minio:9000" ]] || { echo "credential not in the environment" >&2; exit 1; }
    host=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -v) host=${2%:/backup}; shift 2 ;;
        ghcr.io/rjdscott/minio@*) shift; break ;;   # <image> <mc args>
        *) shift ;;
      esac
    done
    cmd=$1; shift
    overwrite=0 remove=0
    while [[ $1 == --* ]]; do
      case $1 in --overwrite) overwrite=1 ;; --remove) remove=1 ;; esac
      shift
    done
    src=$BUCKET/${1#k2/} dst=$host/${2#/backup/}
    (cd "$src" && find . -type f | sort) | while read -r f; do
      f=${f#./}
      if [ "$cmd" = diff ]; then
        [ -e "$dst/$f" ] || echo "< $1/$f"
      elif [ ! -e "$dst/$f" ] || { [ "$overwrite" = 1 ] && ! cmp -s "$src/$f" "$dst/$f"; }; then
        mkdir -p "$(dirname "$dst/$f")" && cp "$src/$f" "$dst/$f"
        echo "{\"status\":\"success\",\"source\":\"$f\"}"
      elif ! cmp -s "$src/$f" "$dst/$f"; then
        echo "{\"status\":\"error\",\"error\":{\"message\":\"Overwrite not allowed for $f\"}}"
      fi
    done
    if [ "$cmd" = mirror ] && [ "$remove" = 1 ]; then
      (cd "$dst" && find . -type f) | while read -r f; do [ -e "$src/$f" ] || rm "$dst/$f"; done
    fi ;;
esac
exit 0
"""
FAKE_CURL = r"""#!/usr/bin/env bash
[ -n "${CATALOG_DOWN:-}" ] && { echo "curl: (7) Failed to connect" >&2; exit 7; }
case "$*" in
  *"config?warehouse=k2"*) echo '{"defaults": {"prefix": "p1"}}' ;;
  *) url=${!#} && t=${url##*/namespaces/} && ns=${t%%/*} name=${t##*/}
     loc=s3://k2-lake/warehouse/k2/u-$ns-$name
     echo "{\"metadata-location\": \"$loc/metadata/00002-b.metadata.json\", \"metadata\": {\"location\": \"$loc\"},
            \"config\": {\"s3.access-key-id\": \"$WANT_USER\", \"s3.secret-access-key\": \"$WANT_PASSWORD\"}}" ;;
esac
"""


@pytest.fixture
def stack(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shipped = (ROOT / "scripts/backup-lake.sh").read_text()
    assert shipped.count('\nTABLES=""\n') == 1
    (repo / "scripts/backup-lake.sh").write_text(shipped.replace('\nTABLES=""\n', f'\nTABLES="{" ".join(TABLES)}"\n'))
    (repo / ".env").write_text(f"MINIO_ROOT_USER={USER}\nMINIO_ROOT_PASSWORD={PASSWORD}\nOTHER_PASSWORD=x\n")
    bucket = tmp_path / "bucket"
    for t in TABLES:
        ns, name = t.split(".")
        table = bucket / f"k2-lake/warehouse/k2/u-{ns}-{name}"
        for f, body in (("data/p=1/a.parquet", "rows-a"), ("metadata/00001-a.metadata.json", "{}"),
                        ("metadata/00002-b.metadata.json", "{\"v\": 2}"), ("metadata/snap-1.avro", "m")):
            (table / f).parent.mkdir(parents=True, exist_ok=True)
            (table / f).write_text(body)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        (bin_ / tool).write_text(body)
        (bin_ / tool).chmod(0o755)
    return tmp_path


def run(stack, *args, **env):
    env = {"K2_BACKUP_DIR": str(stack / "k2-backup"), "K2_BACKUP_MIN_FREE_GIB": "0", **env}
    out = subprocess.run(
        ["bash", str(stack / "repo/scripts/backup-lake.sh"), *args], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": f"{stack / 'bin'}:{os.environ['PATH']}", "LOG": str(stack / "docker.log"),
             "BUCKET": str(stack / "bucket"), "WANT_USER": USER, "WANT_PASSWORD": PASSWORD, **env},
    )
    return out


def status(stack):
    return json.loads((stack / "k2-backup/STATUS.json").read_text())


def files(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*")) if p.is_file()}


def docker_calls(stack) -> str:
    log = stack / "docker.log"
    return log.read_text() if log.exists() else ""


@pytest.mark.parametrize("target", ["repo/backup", "repo", "link-into-repo/backup"])
def test_refuses_a_target_inside_the_checkout(stack, target):
    (stack / "link-into-repo").symlink_to(stack / "repo")
    out = run(stack, K2_BACKUP_DIR=str(stack / target))
    assert out.returncode == 1
    assert "inside the checkout" in out.stderr
    assert not (stack / "repo/backup").exists() and not (stack / "repo/lake").exists()
    assert docker_calls(stack) == ""


def test_runs_on_the_catalogs_network(stack):
    # Lakekeeper has no host port: the catalog and MinIO are reached from k2-catalog,
    # whichever of k2-minio's networks docker lists first.
    out = run(stack, NETS="p_k2-net p_k2-catalog ")
    assert out.returncode == 0, out.stderr
    runs = [line for line in docker_calls(stack).splitlines() if line.startswith("run ")]
    assert any("--entrypoint curl" in r for r in runs) and any("--entrypoint mc" in r for r in runs)
    assert runs and all("--network p_k2-catalog " in r for r in runs)


def test_refuses_below_the_free_space_floor(stack):
    out = run(stack, K2_BACKUP_MIN_FREE_GIB=str(10**9))
    assert out.returncode == 1
    assert "under the floor" in out.stderr
    assert "run --rm" not in docker_calls(stack) and "exec k2-prefect-db" not in docker_calls(stack)
    assert not list((stack / "k2-backup/lake").iterdir())
    assert status(stack)["status"] == "failed"


def differing_copy(stack):
    f = stack / "k2-backup/lake/landing.events/data/p=1/a.parquet"
    f.parent.mkdir(parents=True)
    f.write_text("other bytes")


@pytest.mark.parametrize("name, env, setup, rc", [
    ("success", {}, None, 0),
    ("catalog down", {"CATALOG_DOWN": "1"}, None, 1),
    ("mc container fails", {"MC_DOWN": "1"}, None, 1),
    ("an object differs from its copy", {}, differing_copy, 1),
    ("incomplete dump", {"DUMP_BROKEN": "1"}, None, 1),
    ("dry run", {}, None, 0),
])
def test_never_prints_the_credential(stack, name, env, setup, rc):
    if setup:
        setup(stack)
    out = run(stack, *(["--dry-run"] if name == "dry run" else []), **env)
    assert out.returncode == rc, (name, out.stderr)
    written = (stack / "k2-backup/STATUS.json").read_text() if (stack / "k2-backup/STATUS.json").exists() else ""
    for secret in (USER, PASSWORD):
        assert secret not in out.stdout + out.stderr, name
        assert secret not in docker_calls(stack), f"{name}: on a docker command line"
        assert secret not in written, name


def test_additive_only(stack):
    assert run(stack).returncode == 0
    table = stack / "bucket/k2-lake/warehouse/k2/u-landing-events"
    (table / "metadata/snap-1.avro").unlink()               # expired upstream: the copy keeps it
    (table / "data/p=1/a.parquet").write_text("rewritten")  # a key whose bytes changed: refused
    (table / "data/p=1/new.parquet").write_text("new")      # new: copied
    before = status(stack)

    out = run(stack)
    assert out.returncode == 1
    assert "differ from the copy" in out.stderr
    copy = stack / "k2-backup/lake/landing.events"
    assert (copy / "metadata/snap-1.avro").read_text() == "m"
    assert (copy / "data/p=1/a.parquet").read_text() == "rows-a"
    assert (copy / "data/p=1/new.parquet").read_text() == "new"
    after = status(stack)
    assert after["status"] == "failed" and after["exit_status"] == 1 and "differ" in after["error"]
    # A failed run's copy may be incomplete: the restore point stays the last good one.
    assert after["restore_point"] == before["restore_point"]
    assert after["last_success_utc"] == before["last_success_utc"]


def test_status_shape(stack):
    out = run(stack)
    assert out.returncode == 0, out.stderr
    s = status(stack)
    assert s["status"] == "ok" and s["exit_status"] == 0 and s["error"] is None
    assert s["last_success_utc"] == s["finished_utc"]
    assert s["backup_dir"] == str((stack / "k2-backup").resolve())
    point = s["restore_point"]
    assert sorted(point["tables"]) == sorted(TABLES)
    for t, row in point["tables"].items():
        ns, name = t.split(".")
        assert row["location"] == f"s3://k2-lake/warehouse/k2/u-{ns}-{name}"
        assert row["metadata_location"] == row["location"] + "/metadata/00002-b.metadata.json"
        assert row["objects_total"] == row["objects_this_run"] == 4
        assert row["bytes_total"] == row["bytes_this_run"] == sum(len(b) for b in ("rows-a", "{}", '{"v": 2}', "m"))
    assert point["objects_total"] == 16 and point["bytes_this_run"] == point["bytes_total"]
    for db in ("lakekeeper", "airflow"):
        dump = stack / "k2-backup" / point["dumps"][db]["file"]
        assert dump.stat().st_size == point["dumps"][db]["bytes"] > 0
    assert point["dumps"]["lakekeeper"]["file"].endswith("Z.sql.gz")
    assert not list((stack / "k2-backup/catalog").glob("*.partial"))


def test_dump_rotation_keeps_the_newest_n(stack):
    catalog = stack / "k2-backup/catalog"
    catalog.mkdir(parents=True)
    for day in range(1, 21):
        (catalog / f"202601{day:02d}T043000Z.sql.gz").write_text("old")
        (catalog / f"202601{day:02d}T043000Z.airflow.sql.gz").write_text("old")
    assert run(stack, K2_BACKUP_KEEP="3").returncode == 0
    newest = Path(status(stack)["restore_point"]["dumps"]["lakekeeper"]["file"]).name
    assert sorted(p.name for p in catalog.glob("*Z.sql.gz")) == ["20260119T043000Z.sql.gz", "20260120T043000Z.sql.gz",
                                                                 newest]
    assert len(list(catalog.glob("*Z.airflow.sql.gz"))) == 3


def test_rerun_is_idempotent(stack):
    assert run(stack).returncode == 0
    copy = files(stack / "k2-backup/lake")
    first = status(stack)["restore_point"]
    out = run(stack)
    assert out.returncode == 0, out.stderr
    second = status(stack)["restore_point"]
    assert files(stack / "k2-backup/lake") == copy
    assert second["objects_this_run"] == second["bytes_this_run"] == 0
    assert (second["objects_total"], second["bytes_total"]) == (first["objects_total"], first["bytes_total"])


def test_dry_run_lists_and_writes_nothing(stack):
    out = run(stack, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "< k2/k2-lake/warehouse/k2/u-landing-events/data/p=1/a.parquet" in out.stdout
    assert not (stack / "k2-backup/STATUS.json").exists()
    assert set(files(stack / "k2-backup")) <= {".lock"}
    assert "exec k2-prefect-db" not in docker_calls(stack)


def test_shipped_with_no_tables(stack):
    # As shipped: a visible SKIP for the table copy, both dumps still taken, and
    # neither MinIO nor the catalog asked, so an outage of either costs no dump.
    shutil.copy(ROOT / "scripts/backup-lake.sh", stack / "repo/scripts/backup-lake.sh")
    out = run(stack, CATALOG_DOWN="1", MC_DOWN="1")
    assert out.returncode == 0, out.stderr
    assert "SKIP the lake table copy" in out.stdout and "phase 002" in out.stdout
    calls = docker_calls(stack)
    assert "inspect" not in calls and "run --rm" not in calls
    point = status(stack)["restore_point"]
    assert point["tables"] == {} and point["objects_total"] == 0
    assert sorted(point["dumps"]) == ["airflow", "lakekeeper"]
    dry = run(stack, "--dry-run")
    assert dry.returncode == 0 and "SKIP the lake table copy" in dry.stdout, dry.stderr

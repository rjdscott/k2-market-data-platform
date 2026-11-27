"""The backup (W1-22): scripts/backup_lake.py's plan, copy and verify against fakes, and
scripts/backup-lake.sh, the host-side starter, against a fake `docker` on PATH. No Docker, no network.

The module's manifests are real Avro written by fastavro (deflate, Iceberg's default codec), so its
small decoder is checked against a real writer. The starter's fake `docker` plays three parts:
`inspect` finds k2-minio's networks, `exec` is pg_dump, and `run` is the tools one-shot, which
records its command line and environment and writes the run directory the module would."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import fastavro
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("backup_lake", ROOT / "scripts/backup_lake.py")
bl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bl)

LOC = "s3://k2-lake/warehouse/k2/u-landing-files"
MANIFEST_LIST_SCHEMA = {"type": "record", "name": "manifest_file", "fields": [
    {"name": "manifest_path", "type": "string"}, {"name": "manifest_length", "type": "long"},
    {"name": "content", "type": "int"}, {"name": "added_snapshot_id", "type": ["null", "long"]},
    {"name": "partitions", "type": ["null", {"type": "array", "items": {"type": "record", "name": "r508", "fields": [
        {"name": "contains_null", "type": "boolean"}, {"name": "lower_bound", "type": ["null", "bytes"]}]}}]},
]}
MANIFEST_SCHEMA = {"type": "record", "name": "manifest_entry", "fields": [
    {"name": "status", "type": "int"}, {"name": "snapshot_id", "type": ["null", "long"]},
    {"name": "data_file", "type": {"type": "record", "name": "r2", "fields": [
        {"name": "content", "type": "int"}, {"name": "file_path", "type": "string"},
        {"name": "file_format", "type": "string"}, {"name": "file_size_in_bytes", "type": "long"},
        {"name": "value_counts", "type": ["null", {"type": "array", "logicalType": "map", "items": {
            "type": "record", "name": "k119_v120", "fields": [{"name": "key", "type": "int"}, {"name": "value", "type": "long"}]}}]},
        {"name": "properties", "type": {"type": "map", "values": "string"}},
        {"name": "key_metadata", "type": ["null", {"type": "fixed", "name": "f4", "size": 4}]},
        {"name": "nan", "type": ["null", "double"]}, {"name": "ratio", "type": "float"},
        {"name": "fmt", "type": {"type": "enum", "name": "e1", "symbols": ["PARQUET", "AVRO"]}}]}},
]}


def avro(schema, records) -> bytes:
    buf = io.BytesIO()
    fastavro.writer(buf, fastavro.parse_schema(schema), records, codec="deflate", sync_interval=64)
    return buf.getvalue()


def entry(status, path, size):
    return {"status": status, "snapshot_id": 7, "data_file": {
        "content": 0, "file_path": path, "file_format": "PARQUET", "file_size_in_bytes": size,
        "value_counts": [{"key": 1, "value": 3}], "properties": {"a": "b"}, "key_metadata": b"\x00\x01\x02\x03",
        "nan": None, "ratio": 0.5, "fmt": "PARQUET"}}


@pytest.fixture
def lake():
    """One restore table with two snapshots (main at 2, tag `audited` at 1), a landing prefix, and
    the objects they name: {uri: bytes}."""
    objs = {}

    def put(uri, body):
        objs[uri] = body
        return len(body)

    for n in ("a", "b", "old"):
        put(f"{LOC}/data/{n}.parquet", f"rows-{n}".encode())
    m1 = put(f"{LOC}/metadata/m1.avro", avro(MANIFEST_SCHEMA, [entry(1, f"{LOC}/data/old.parquet", 9)]))
    m2 = put(f"{LOC}/metadata/m2.avro", avro(MANIFEST_SCHEMA, [
        entry(1, f"{LOC}/data/a.parquet", 6), entry(0, f"{LOC}/data/b.parquet", 6),
        entry(2, f"{LOC}/data/old.parquet", 9)]))  # deleted in snapshot 2: not part of it
    for sid, ms in ((1, [(f"{LOC}/metadata/m1.avro", m1)]), (2, [(f"{LOC}/metadata/m2.avro", m2)])):
        put(f"{LOC}/metadata/snap-{sid}.avro", avro(MANIFEST_LIST_SCHEMA, [
            {"manifest_path": p, "manifest_length": n, "content": 0, "added_snapshot_id": sid,
             "partitions": [{"contains_null": False, "lower_bound": None}]} for p, n in ms]))
    put(f"{LOC}/metadata/stats-2.puffin", b"puffin")
    meta = {"format-version": 2, "location": LOC, "current-snapshot-id": 2,
            "refs": {"main": {"snapshot-id": 2, "type": "branch"}, "audited": {"snapshot-id": 1, "type": "tag"}},
            "snapshots": [{"snapshot-id": i, "manifest-list": f"{LOC}/metadata/snap-{i}.avro"} for i in (1, 2)],
            "statistics": [{"snapshot-id": 2, "statistics-path": f"{LOC}/metadata/stats-2.puffin", "file-size-in-bytes": 6}]}
    put(f"{LOC}/metadata/00002-b.metadata.json", json.dumps(meta).encode())
    put("s3://k2-landing/fx-reference/ecb-fx-rates/2026/f1.csv", b"one")
    put("s3://k2-landing/fx-reference/ecb-fx-rates/2026/f2.csv", b"two!")
    return objs


BACKUP_SET = {"tag": "v0.0.0-t.1", "entries": [
    {"kind": "landing", "bucket": "k2-landing", "prefix": "fx-reference/ecb-fx-rates/"},
    {"kind": "table", "table": "k2.ecb_fx_rates.landing_files", "mode": "restore", "ref": "main"},
    {"kind": "table", "table": "fx_reference.ecb_fx_rates__base.silver_rates", "mode": "rebuild", "ref": "audited"},
    {"kind": "lakekeeper-db"}, {"kind": "releases"}]}


def fakes(objs, locations=None):
    locations = locations or {"k2.ecb_fx_rates.landing_files": (LOC, f"{LOC}/metadata/00002-b.metadata.json")}
    asked = []

    def locate(t):
        asked.append(t)
        return locations[t]

    def ls(bucket, prefix):
        return [(u.split("/", 3)[3], len(b)) for u, b in sorted(objs.items()) if u.startswith(f"s3://{bucket}/{prefix}")]

    return locate, (lambda uri: objs[uri]), ls, asked


def test_avro_reader_reads_what_fastavro_writes(lake):
    rows = bl.avro_records(lake[f"{LOC}/metadata/m2.avro"])
    assert [r["status"] for r in rows] == [1, 0, 2]
    assert rows[0]["data_file"]["file_path"] == f"{LOC}/data/a.parquet"
    assert rows[0]["data_file"]["properties"] == {"a": "b"} and rows[0]["data_file"]["key_metadata"] == b"\x00\x01\x02\x03"
    assert rows[0]["data_file"]["value_counts"] == [{"key": 1, "value": 3}] and rows[0]["data_file"]["fmt"] == "PARQUET"
    with pytest.raises(bl.Failed):
        bl.avro_records(b"PAR1....")


def test_plan_copies_the_snapshot_at_its_ref_and_not_a_rebuild_table(lake):
    locate, read, ls, asked = fakes(lake)
    p = bl.plan(BACKUP_SET, locate, read, ls)
    assert asked == ["k2.ecb_fx_rates.landing_files"]   # the rebuild table is never even looked up
    assert p["rebuild"] == ["fx_reference.ecb_fx_rates__base.silver_rates"]
    (t,) = p["tables"]
    assert t["snapshot_id"] == 2 and t["ref"] == "main"
    assert [u.rsplit("/", 1)[1] for u, _ in t["files"]] == [
        "00002-b.metadata.json", "snap-2.avro", "m2.avro", "a.parquet", "b.parquet", "stats-2.puffin"]
    assert dict(t["files"])[f"{LOC}/data/a.parquet"] == 6
    (g,) = p["landings"]
    assert [u for u, _ in g["files"]] == ["s3://k2-landing/fx-reference/ecb-fx-rates/2026/f1.csv",
                                          "s3://k2-landing/fx-reference/ecb-fx-rates/2026/f2.csv"]
    # The same table at its tag: the older snapshot, whose file the newer one deleted.
    tagged = {**BACKUP_SET, "entries": [{**BACKUP_SET["entries"][1], "ref": "audited"}]}
    (t,) = bl.plan(tagged, locate, read, ls)["tables"]
    assert t["snapshot_id"] == 1
    assert [u.rsplit("/", 1)[1] for u, _ in t["files"]] == ["00002-b.metadata.json", "snap-1.avro", "m1.avro", "old.parquet"]


def test_a_table_with_no_snapshot_copies_its_metadata_file_alone(lake):
    meta = json.loads(lake[f"{LOC}/metadata/00002-b.metadata.json"])
    meta.update(refs={}, snapshots=[], **{"current-snapshot-id": -1})
    lake[f"{LOC}/metadata/00002-b.metadata.json"] = json.dumps(meta).encode()
    (t,) = bl.plan(BACKUP_SET, *fakes(lake)[:3])["tables"]
    assert t["snapshot_id"] is None and [u for u, _ in t["files"]] == [f"{LOC}/metadata/00002-b.metadata.json"]


@pytest.mark.parametrize("where", ["s3://k2-lake/warehouse/k2/other/metadata/x.json", f"{LOC}/../escape/x.json",
                                   "file:///etc/passwd"])
def test_plan_refuses_a_path_outside_the_tables_location(lake, where):
    lake[where] = lake[f"{LOC}/metadata/00002-b.metadata.json"]
    locate, read, ls, _ = fakes(lake, {"k2.ecb_fx_rates.landing_files": (LOC, where)})
    with pytest.raises(bl.Failed):
        bl.plan(BACKUP_SET, locate, read, ls)


@pytest.fixture
def releases(tmp_path):
    r = tmp_path / "releases"
    (r / "rel-1/compiled/lake").mkdir(parents=True)
    (r / "rel-1/compiled/lake/backup-set.json").write_text(json.dumps(BACKUP_SET))
    (r / "rel-1/secret.txt").write_text("x")
    (r / "rel-1/secret.txt").chmod(0o600)
    (r / "pointers").mkdir()
    (r / "pointers/trino").symlink_to("../rel-1")
    (r / "deploy-log.jsonl").write_text("{}\n")
    return r


def copy_run(monkeypatch, objs, d, releases, stamp, fetch=None, *dumps):
    locate, read, ls, _ = fakes(objs)
    monkeypatch.setattr(bl, "locate", locate)
    monkeypatch.setattr(bl, "ls", ls)
    monkeypatch.setattr(bl, "RELEASES", releases)
    monkeypatch.setattr(bl, "get", fetch or (lambda uri, out=None: out.write_bytes(objs[uri]) and b"" if out else objs[uri]))
    args = ["copy", "--release", str(releases / "rel-1"), "--dir", str(d), "--run", stamp]
    for x in dumps:
        args += ["--dump", x]
    return bl.main(args)


def test_copy_writes_a_complete_run_and_its_manifest(monkeypatch, lake, tmp_path, releases):
    d = tmp_path / "backup"
    (d / "catalog").mkdir(parents=True)
    (d / "catalog/s.sql.gz").write_bytes(b"dump")
    assert copy_run(monkeypatch, lake, d, releases, "20261127T043000Z", None, "lakekeeper=catalog/s.sql.gz") == 0
    run = d / "runs/20261127T043000Z"
    m = json.loads((run / "manifest.json").read_text())
    assert m["release"] == "rel-1" and m["tag"] == "v0.0.0-t.1" and m["rebuild"] == ["fx_reference.ecb_fx_rates__base.silver_rates"]
    (t,) = m["tables"]
    assert (t["table"], t["ref"], t["snapshot_id"], t["objects"]) == ("k2.ecb_fx_rates.landing_files", "main", 2, 6)
    assert t["metadata_location"] == f"{LOC}/metadata/00002-b.metadata.json"
    assert t["bytes"] == sum(t["files"].values()) == sum(len(lake[f"s3://{k[len('objects/'):]}"]) for k in t["files"])
    assert (run / "objects/k2-lake/warehouse/k2/u-landing-files/data/a.parquet").read_bytes() == b"rows-a"
    (g,) = m["landings"]
    assert (g["prefix"], g["objects"], g["bytes"]) == ("fx-reference/ecb-fx-rates/", 2, 7)
    assert m["dumps"]["lakekeeper"] == {"file": "catalog/s.sql.gz", "bytes": 4, "sha256": bl.sha256(d / "catalog/s.sql.gz")}
    assert m["releases"]["sha256"] == bl.sha256(run / "releases.tar")
    with tarfile.open(run / "releases.tar") as tar:   # links stay links, modes are kept
        assert tar.getmember("k2-releases/pointers/trino").issym()
        assert tar.getmember("k2-releases/pointers/trino").linkname == "../rel-1"
        assert tar.getmember("k2-releases/rel-1/secret.txt").mode == 0o600
        assert "k2-releases/deploy-log.jsonl" in tar.getnames()
    assert [p.name for p in (d / "runs").iterdir()] == ["20261127T043000Z"]

    # The next run links what the last one holds at a known size and fetches the rest: what is new,
    # and the metadata file and manifest list, whose sizes no metadata states.
    lake["s3://k2-landing/fx-reference/ecb-fx-rates/2026/f3.csv"] = b"three"
    assert copy_run(monkeypatch, lake, d, releases, "20261128T043000Z") == 0
    m2 = json.loads((d / "runs/20261128T043000Z/manifest.json").read_text())
    unsized = [f"{LOC}/metadata/00002-b.metadata.json", f"{LOC}/metadata/snap-2.avro"]
    assert m2["objects_copied"] == 3 and m2["bytes_copied"] == len(b"three") + sum(len(lake[u]) for u in unsized)
    a = "objects/k2-lake/warehouse/k2/u-landing-files/data/a.parquet"
    assert os.path.samefile(run / a, d / "runs/20261128T043000Z" / a)


def test_a_failed_copy_leaves_nothing_that_looks_complete(monkeypatch, lake, tmp_path, releases):
    d = tmp_path / "backup"

    def broken(uri, out=None):
        if uri.endswith("b.parquet") and out:
            raise bl.Failed(f"{uri}: storage answered HTTP 503")
        return out.write_bytes(lake[uri]) and b"" if out else lake[uri]

    assert copy_run(monkeypatch, lake, d, releases, "20261127T043000Z", broken) == 1
    assert bl.complete_runs(d / "runs") == []
    assert [p.name for p in (d / "runs").iterdir()] == [".20261127T043000Z.partial"]
    lines, bad = bl.check(d, BACKUP_SET)
    assert bad == [f"no complete backup run under {d / 'runs'}"]
    # The next run clears the stale partial and completes.
    assert copy_run(monkeypatch, lake, d, releases, "20261128T043000Z") == 0
    assert [p.name for p in (d / "runs").iterdir()] == ["20261128T043000Z"]


def test_a_short_copy_fails(monkeypatch, lake, tmp_path, releases):
    def short(uri, out=None):
        return out.write_bytes(lake[uri][:-1]) and b"" if out else lake[uri]

    assert copy_run(monkeypatch, lake, tmp_path / "backup", releases, "20261127T043000Z", short) == 1
    assert bl.complete_runs(tmp_path / "backup/runs") == []


@pytest.fixture
def done(monkeypatch, lake, tmp_path, releases):
    d = tmp_path / "backup"
    (d / "catalog").mkdir(parents=True)
    (d / "catalog/s.sql.gz").write_bytes(b"dump")
    assert copy_run(monkeypatch, lake, d, releases, "20261127T043000Z", None, "lakekeeper=catalog/s.sql.gz") == 0
    return d


def test_verify_lists_each_snapshot_and_landing(done, capsys):
    (done / "set.json").write_text(json.dumps(BACKUP_SET))
    assert bl.main(["verify", "--dir", str(done), "--backup-set", str(done / "set.json")]) == 0
    out = capsys.readouterr().out
    assert "table k2.ecb_fx_rates.landing_files at main: snapshot 2, 6 files" in out
    assert "landing s3://k2-landing/fx-reference/ecb-fx-rates/: 2 objects, 7 bytes" in out
    assert "backup-verify: OK: 0 problem(s)" in out


DATA = "objects/k2-lake/warehouse/k2/u-landing-files/data/a.parquet"


@pytest.mark.parametrize("name, damage, says", [
    ("a missing file", lambda d, r: (r / DATA).unlink(), "is missing"),
    ("a size mismatch", lambda d, r: (r / DATA).write_bytes(b"rows-a and more"), "the manifest says 6"),
    ("a changed dump", lambda d, r: (d / "catalog/s.sql.gz").write_bytes(b"pmud"), "lakekeeper: catalog/s.sql.gz"),
    ("a changed releases tar", lambda d, r: (r / "releases.tar").write_bytes(b"x"), "releases: releases.tar"),
    ("a restore table the run lacks", None, "k2.ecb_fx_rates.audit_checks: a restore table of the live backup set"),
])
def test_verify_catches(done, name, damage, says):
    live = json.loads(json.dumps(BACKUP_SET))
    if damage:
        damage(done, done / "runs/20261127T043000Z")
    else:
        live["entries"].append({"kind": "table", "table": "k2.ecb_fx_rates.audit_checks", "mode": "restore", "ref": "main"})
    _, bad = bl.check(done, live)
    assert any(says in b for b in bad), (name, bad)


# ---- the starter, scripts/backup-lake.sh ------------------------------------------------------

ACCESS, SECRET = "backup-a1b2c3", "dummy-backup-secret-9f8e7d"
ROOT_USER, ROOT_PASSWORD = "dummy-root-user-4c4c", "dummy-root-secret-5d5d"
IMAGE = "ghcr.io/example/tools@sha256:" + "ab" * 32

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, sys
A, E = sys.argv[1:], os.environ
with open(E["LOG"], "a") as f:
    f.write(json.dumps({"argv": A, "env": {k: v for k, v in E.items() if k.startswith(("K2_BACKUP_S3", "MINIO"))}}) + "\n")
if A[0] == "inspect":
    print(E.get("NETS", "p_k2-net p_k2-catalog "))
elif A[0] == "exec":
    if E.get("DUMP_BROKEN"):
        print("-- PostgreSQL database dump")
    else:
        print("-- PostgreSQL database dump\nCREATE TABLE t ();\n-- PostgreSQL database dump complete\n")
elif A[0] == "run":
    if E.get("COPY_FAIL"):
        sys.exit("backup-lake: FAIL: s3://k2-lake/x: storage answered HTTP 503")
    if not (E.get("K2_BACKUP_S3_ACCESS_KEY") == E["WANT_ACCESS"] and E.get("K2_BACKUP_S3_SECRET_KEY") == E["WANT_SECRET"]):
        sys.exit("the backup key is not in the environment")
    if "--dry-run" in A:
        sys.exit(0)
    host = next(a.split(":")[0] for a in A if a.endswith(":/backup"))
    run = A[A.index("--run") + 1]
    dumps = dict(A[i + 1].split("=", 1) for i, a in enumerate(A) if a == "--dump")
    d = os.path.join(host, "runs", run)
    os.makedirs(d)
    tables = [{"table": "k2.ecb_fx_rates.landing_files", "ref": "main", "location": "s3://k2-lake/w/t",
               "metadata_location": "s3://k2-lake/w/t/metadata/1.metadata.json", "snapshot_id": 2, "objects": 3, "bytes": 30, "files": {}}]
    json.dump({"release": "rel-1", "tables": tables, "landings": [{"objects": 2, "bytes": 7}], "objects_copied": 5,
               "bytes_copied": 37, "dumps": {n: {"file": f} for n, f in dumps.items()}}, open(os.path.join(d, "manifest.json"), "w"))
"""


@pytest.fixture
def stack(tmp_path, releases):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for f in ("backup-lake.sh", "backup_lake.py"):
        shutil.copy(ROOT / "scripts" / f, repo / "scripts" / f)
    (repo / ".env").write_text(f"K2_BACKUP_S3_ACCESS_KEY={ACCESS}\nK2_BACKUP_S3_SECRET_KEY={SECRET}\n"
                               f"MINIO_ROOT_USER={ROOT_USER}\nMINIO_ROOT_PASSWORD={ROOT_PASSWORD}\nOTHER_PASSWORD=x\n")
    (releases / "rel-1/manifest.json").write_text(json.dumps({"images": {"tools": IMAGE}}))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/docker").write_text(FAKE_DOCKER)
    (tmp_path / "bin/docker").chmod(0o755)
    return tmp_path


def run(stack, *args, **env):
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("K2_", "MINIO"))}
    return subprocess.run(
        ["bash", str(stack / "repo/scripts/backup-lake.sh"), *args], capture_output=True, text=True, timeout=60,
        env={**inherited, "PATH": f"{stack / 'bin'}:{os.environ['PATH']}", "LOG": str(stack / "docker.log"),
             "WANT_ACCESS": ACCESS, "WANT_SECRET": SECRET, "K2_BACKUP_DIR": str(stack / "k2-backup"),
             "K2_RELEASES": str(stack / "releases"), "K2_BACKUP_MIN_FREE_GIB": "0", **env})


def calls(stack):
    log = stack / "docker.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def status(stack):
    return json.loads((stack / "k2-backup/STATUS.json").read_text())


def test_never_reads_the_root_pair():
    for f in ("scripts/backup-lake.sh", "scripts/backup_lake.py"):
        assert "MINIO_ROOT" not in (ROOT / f).read_text(), f


def test_the_copy_runs_in_the_live_releases_tools_image_with_the_backup_key(stack):
    out = run(stack)
    assert out.returncode == 0, out.stderr
    (r,) = [c for c in calls(stack) if c["argv"][0] == "run"]
    a = r["argv"]
    assert a[a.index("--network") + 1] == "p_k2-catalog"
    assert a[a.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "--read-only" in a and IMAGE in a
    mounts = [a[i + 1] for i, x in enumerate(a) if x == "-v"]
    assert mounts == [f"{stack / 'releases'}:/k2/releases:ro", f"{stack / 'repo/scripts'}:/opt/k2/scripts:ro",
                      f"{stack / 'k2-backup'}:/backup"]   # the backup directory is the one writable mount
    assert a[a.index("--release") + 1] == "/k2/releases/rel-1"
    assert a[a.index("-e") + 1] == "K2_BACKUP_S3_ACCESS_KEY"     # by name: the value is in the environment
    for c in calls(stack):
        assert not any(k.startswith("MINIO") for k in c["env"]), c
    for s in (ACCESS, SECRET, ROOT_USER, ROOT_PASSWORD):
        assert s not in json.dumps([c["argv"] for c in calls(stack)]) and s not in out.stdout + out.stderr
        assert s not in (stack / "k2-backup/STATUS.json").read_text()


def test_status_shape_and_the_health_marker(stack):
    assert run(stack).returncode == 0
    s = status(stack)
    # make health (5) reads these three: keep them.
    assert s["status"] == "ok" and s["error"] is None and s["last_success_utc"] == s["finished_utc"]
    p = s["restore_point"]
    assert p["run"].startswith("runs/") and p["manifest"] == p["run"] + "/manifest.json" and p["release"] == "rel-1"
    assert p["tables"]["k2.ecb_fx_rates.landing_files"]["snapshot_id"] == 2
    assert (p["objects_total"], p["bytes_total"], p["objects_copied"]) == (5, 37, 5)
    for db in ("lakekeeper", "airflow"):
        assert (stack / "k2-backup" / p["dumps"][db]["file"]).stat().st_size > 0


@pytest.mark.parametrize("name, env, says", [
    ("the copy fails", {"COPY_FAIL": "1"}, "the lake copy did not complete"),
    ("an incomplete dump", {"DUMP_BROKEN": "1"}, "incomplete"),
    ("no live release", {"K2_RELEASES": "/nonexistent"}, "no live release"),
])
def test_a_failed_run_keeps_the_last_restore_point(stack, name, env, says):
    assert run(stack).returncode == 0
    before = status(stack)
    out = run(stack, **env)
    assert out.returncode == 1 and says in out.stderr, (name, out.stderr)
    after = status(stack)
    assert after["status"] == "failed" and says in after["error"]
    assert after["restore_point"] == before["restore_point"] and after["last_success_utc"] == before["last_success_utc"]


def test_dumps_are_taken_before_the_copy_and_kept_when_it_fails(stack):
    out = run(stack, COPY_FAIL="1")
    assert out.returncode == 1
    kinds = [c["argv"][0] for c in calls(stack)]
    assert kinds.index("exec") < kinds.index("run")
    assert len(list((stack / "k2-backup/catalog").glob("*Z.sql.gz"))) == 1


@pytest.mark.parametrize("target", ["repo/backup", "repo"])
def test_refuses_a_target_inside_the_checkout(stack, target):
    out = run(stack, K2_BACKUP_DIR=str(stack / target))
    assert out.returncode == 1 and "inside the checkout" in out.stderr
    assert calls(stack) == []


def test_refuses_below_the_free_space_floor(stack):
    out = run(stack, K2_BACKUP_MIN_FREE_GIB=str(10**9))
    assert out.returncode == 1 and "under the floor" in out.stderr
    assert calls(stack) == [] and status(stack)["status"] == "failed"


def test_rotation_keeps_the_newest_n(stack):
    catalog, runs = stack / "k2-backup/catalog", stack / "k2-backup/runs"
    catalog.mkdir(parents=True)
    runs.mkdir()
    for day in range(1, 21):
        (catalog / f"202601{day:02d}T043000Z.sql.gz").write_text("old")
        (catalog / f"202601{day:02d}T043000Z.airflow.sql.gz").write_text("old")
        (runs / f"202601{day:02d}T043000Z").mkdir()
    assert run(stack, K2_BACKUP_KEEP="3").returncode == 0
    newest = Path(status(stack)["restore_point"]["run"]).name
    assert sorted(p.name for p in runs.iterdir()) == ["20260119T043000Z", "20260120T043000Z", newest]
    assert len(list(catalog.glob("*Z.sql.gz"))) == 3 and len(list(catalog.glob("*Z.airflow.sql.gz"))) == 3


def test_dry_run_copies_and_dumps_nothing(stack):
    out = run(stack, "--dry-run")
    assert out.returncode == 0, out.stderr
    (r,) = [c for c in calls(stack) if c["argv"][0] == "run"]
    assert "--dry-run" in r["argv"] and "--dump" not in r["argv"]
    assert not [c for c in calls(stack) if c["argv"][0] == "exec"]
    assert not (stack / "k2-backup/STATUS.json").exists()


def test_verify_mode_reads_the_live_backup_set(stack, done):
    shutil.copytree(done, stack / "k2-backup")
    out = run(stack, "--verify")
    assert out.returncode == 0, out.stderr
    assert "snapshot 2" in out.stdout and "landing s3://k2-landing/fx-reference/ecb-fx-rates/" in out.stdout
    assert calls(stack) == []
    live = json.loads((stack / "releases/rel-1/compiled/lake/backup-set.json").read_text())
    live["entries"].append({"kind": "table", "table": "k2.x.y", "mode": "restore", "ref": "main"})
    (stack / "releases/rel-1/compiled/lake/backup-set.json").write_text(json.dumps(live))
    out = run(stack, "--verify")
    assert out.returncode == 1 and "k2.x.y: a restore table" in out.stderr

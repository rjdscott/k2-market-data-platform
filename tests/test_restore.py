"""scripts/restore.py (make restore, make restore-drill): every refusal and decision with fakes at the
storage, the catalog, Trino and docker. The whole path on a throwaway lake is
scripts/verify/restore-path.sh."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import restore as r  # noqa: E402

spec = importlib.util.spec_from_file_location("catalog_drift_cli", ROOT / "scripts/verify/catalog_drift.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

T, REB, PLAT = "k2.p.bronze", "dom.p__base.silver", "governance.run_records.holds"
LOC = "s3://k2-lake/warehouse/k2/p/bronze"
META = f"{LOC}/metadata/00002.gz.metadata.json"
PRODUCTS = ["dom.p", "dom.q"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_run(backup: Path, stamp: str, *, snapshots=(1, 2)) -> Path:
    """One complete run as backup_lake.py copy writes it: a restore table (its metadata file and one
    data file), a platform table, a landing object, and the catalog dump."""
    run = backup / "runs" / stamp
    meta = json.dumps({"location": LOC, "snapshots": [{"snapshot-id": s} for s in snapshots]}).encode()
    files = {"objects/k2-lake/warehouse/k2/p/bronze/metadata/00002.gz.metadata.json": gzip.compress(meta),
             "objects/k2-lake/warehouse/k2/p/bronze/data/a.parquet": b"rows",
             "objects/k2-lake/warehouse/governance/run_records/holds/metadata/1.metadata.json": b"{}",
             "objects/k2-landing/dom/p/2026/f.csv": b"a,b\n"}
    for rel, data in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(data)
    (backup / "catalog").mkdir(parents=True, exist_ok=True)
    dump = gzip.compress(b"-- PostgreSQL database dump complete\n")
    (backup / "catalog" / f"{stamp}.sql.gz").write_bytes(dump)
    size = {k: len(v) for k, v in files.items()}
    tables = [{"table": T, "ref": "main", "location": LOC, "metadata_location": META, "snapshot_id": 2, "objects": 2, "bytes": 0,
               "files": {k: n for k, n in size.items() if "/p/bronze/" in k}},
              {"table": PLAT, "ref": "main", "location": "s3://k2-lake/warehouse/governance/run_records/holds",
               "metadata_location": "s3://k2-lake/warehouse/governance/run_records/holds/metadata/1.metadata.json",
               "snapshot_id": 7, "objects": 1, "bytes": 2, "files": {k: n for k, n in size.items() if "/holds/" in k}}]
    manifest = {"run": stamp, "release": "rel-1", "tag": "v0", "tables": tables, "rebuild": [REB],
                "landings": [{"bucket": "k2-landing", "prefix": "dom/p/", "objects": 1, "bytes": 4,
                              "files": {k: n for k, n in size.items() if "k2-landing" in k}}],
                "dumps": {"lakekeeper": {"file": f"catalog/{stamp}.sql.gz", "bytes": len(dump), "sha256": sha(dump)}}}
    (run / "manifest.json").write_text(json.dumps(manifest))
    return run


BACKUP_SET = {"tag": "v0", "entries": [
    {"kind": "landing", "bucket": "k2-landing", "prefix": "dom/p/"},
    {"kind": "table", "mode": "restore", "ref": "main", "table": T},
    {"kind": "table", "mode": "restore", "ref": "main", "table": PLAT},
    {"kind": "table", "mode": "rebuild", "ref": "audited", "table": REB},
    {"kind": "lakekeeper-db"}]}


# ---- 1. the run ------------------------------------------------------------------------------------

def test_the_newest_complete_run_is_chosen(tmp_path):
    write_run(tmp_path, "20261128T010000Z")
    run = write_run(tmp_path, "20261128T020000Z")
    (tmp_path / "runs" / ".20261128T030000Z.partial").mkdir()  # a copy that never finished is never a run
    assert r.choose_run(tmp_path, BACKUP_SET, None)[0] == run
    assert r.choose_run(tmp_path, BACKUP_SET, run.name)[0] == run


def test_a_run_older_than_the_newest_is_refused(tmp_path):
    write_run(tmp_path, "20261128T010000Z")
    write_run(tmp_path, "20261128T020000Z")
    with pytest.raises(r.Refused, match="older than the newest complete run 20261128T020000Z"):
        r.choose_run(tmp_path, BACKUP_SET, "20261128T010000Z")
    with pytest.raises(r.Refused, match="not a complete run"):
        r.choose_run(tmp_path, BACKUP_SET, ".20261128T030000Z.partial")


def test_an_incomplete_run_is_refused(tmp_path):
    run = write_run(tmp_path, "20261128T020000Z")
    (run / "objects/k2-lake/warehouse/k2/p/bronze/data/a.parquet").unlink()
    with pytest.raises(r.Refused, match="is incomplete: .*a.parquet is missing"):
        r.choose_run(tmp_path, BACKUP_SET, None)
    with pytest.raises(r.Refused, match="no complete backup run"):
        r.choose_run(tmp_path / "empty", BACKUP_SET, None)


def test_a_run_whose_tables_are_not_the_live_sets_is_refused(tmp_path):
    write_run(tmp_path, "20261128T020000Z")
    moved = {"tag": "v1", "entries": [e | {"mode": "rebuild"} if e.get("table") == PLAT else e for e in BACKUP_SET["entries"]]
             + [{"kind": "table", "mode": "rebuild", "ref": "audited", "table": "dom.p__base.gold"}]}
    with pytest.raises(r.Refused, match="does not hold what the live backup set names"):
        r.choose_run(tmp_path, moved, None)
    m = json.loads((tmp_path / "runs/20261128T020000Z/manifest.json").read_text())
    assert r.set_problems(moved, m) == [f"{PLAT}: the live backup set says rebuild, the run restore",
                                        "dom.p__base.gold: in the live backup set, not in the run"]
    assert r.set_problems({"entries": [e for e in BACKUP_SET["entries"] if e["kind"] != "landing"]}, m) == \
        ["landing s3://k2-landing/dom/p/: in the run, not in the live backup set"]


# ---- storage --------------------------------------------------------------------------------------

def test_put_back_puts_what_is_gone_and_never_overwrites(tmp_path):
    run = write_run(tmp_path, "20261128T020000Z")
    files = json.loads((run / "manifest.json").read_text())["tables"][0]["files"]
    store, puts = {"s3://k2-lake/warehouse/k2/p/bronze/data/a.parquet": 4}, []
    assert r.put_back(files, run, store.get, lambda u, d: puts.append((u, d))) == (1, 1)
    assert puts == [(META, (run / "objects/k2-lake/warehouse/k2/p/bronze/metadata/00002.gz.metadata.json").read_bytes())]
    store["s3://k2-lake/warehouse/k2/p/bronze/data/a.parquet"] = 9  # another object of that key
    puts.clear()
    with pytest.raises(r.Failed, match="never overwritten"):
        r.put_back(files, run, store.get, lambda u, d: puts.append(u))
    assert META not in [u for u in puts if "a.parquet" in u]


# ---- the catalog ----------------------------------------------------------------------------------

class FakeLake:
    def __init__(self, tables=None):
        self.tables, self.calls = dict(tables or {}), []

    def load(self, t):
        return self.tables.get(t)

    def register(self, t, m):
        self.calls.append(("register", t, m))
        self.tables[t] = entry(m, sid=2)

    def drop(self, t):
        self.calls.append(("drop", t))
        self.tables.pop(t)

    def create(self, t, meta):
        self.calls.append(("create", t, meta["current-schema-id"]))
        self.tables[t] = entry(f"{meta['location']}/metadata/new.json", sid=None)


def entry(metadata, sid=2, location=LOC):
    snaps = [] if sid is None else [{"snapshot-id": sid, "manifest-list": f"{location}/metadata/snap-{sid}.avro"}]
    return {"metadata-location": metadata, "metadata": {"location": location, "current-snapshot-id": sid if sid else -1,
            "refs": {"main": {"snapshot-id": sid}} if sid else {}, "snapshots": snaps, "current-schema-id": 0,
            "schemas": [{"schema-id": 0, "fields": []}], "default-spec-id": 0, "partition-specs": [{"spec-id": 0, "fields": []}],
            "default-sort-order-id": 0, "sort-orders": [{"order-id": 0, "fields": []}], "properties": {"p": "v"}}}


def table_of(run):
    return json.loads((run / "manifest.json").read_text())["tables"][0]


@pytest.mark.parametrize("catalog, store, want, calls", [
    (None, set(), "registered at the backup's metadata", [("register", T, META)]),
    (entry(META), set(), "in the catalog at the backup's metadata", []),
    # the dump is older than the copy: its snapshot is in the backup's metadata, so forward to the backup's
    (entry(f"{LOC}/metadata/00001.metadata.json", sid=1), {"all"}, "re-pointed", [("drop", T), ("register", T, META)]),
    # newer than the backup and whole: a commit after the copy, kept
    (entry(f"{LOC}/metadata/00003.metadata.json", sid=3), {"all"}, "newer than the backup and whole: kept", []),
    # newer, but its files are gone: the backup's
    (entry(f"{LOC}/metadata/00003.metadata.json", sid=3), set(), "re-pointed", [("drop", T), ("register", T, META)]),
])
def test_restore_table_decides_from_the_catalog_and_the_store(tmp_path, catalog, store, want, calls):
    run = write_run(tmp_path, "20261128T020000Z")
    lake = FakeLake({T: catalog} if catalog else {})
    # The backup's own files are there at their sizes (put back); `store` says whether the catalog's
    # entry is readable: its metadata file and its snapshot's manifest list.
    sizes = {("s3://" + k.removeprefix("objects/")): n for k, n in table_of(run)["files"].items()}
    line = r.restore_table(table_of(run), run, lake, lambda u: sizes[u] if u in sizes else (1 if store else None), lambda u, d: None)
    assert want in line and lake.calls == calls


def test_a_restore_table_at_another_location_is_another_table(tmp_path):
    run = write_run(tmp_path, "20261128T020000Z")
    other = entry(META, location="s3://k2-lake/warehouse/k2/p/bronze-0f3a")
    assert r.lake_problems([table_of(run)], {T: other}.get) == [
        f"{T}: the catalog's is at s3://k2-lake/warehouse/k2/p/bronze-0f3a, the backup's at {LOC}"]
    assert r.lake_problems([table_of(run)], {T: entry(META)}.get) == []


def test_a_rebuild_table_is_recreated_only_when_its_files_are_gone():
    whole = FakeLake({REB: entry(f"{LOC}/metadata/9.json")})
    assert r.rebuild_table(REB, whole, lambda u: 1) == ("whole: kept", False) and whole.calls == []
    gone = FakeLake({REB: entry(f"{LOC}/metadata/9.json")})
    line, made = r.rebuild_table(REB, gone, lambda u: None)
    assert made and "dropped without purge, recreated empty" in line and gone.calls == [("drop", REB), ("create", REB, 0)]
    assert r.rebuild_table(REB, FakeLake(), lambda u: None) == ("absent: its first build, or the deploy's DDL, makes it", False)


def test_create_sends_the_old_tables_shape():
    sent = []
    lake = r.Lake("http://lakekeeper:8181/catalog")
    lake._base["dom"] = "http://lakekeeper:8181/catalog/v1/wh"
    r.cd.call, real = (lambda m, u, b=None: sent.append((m, u, b)) or (200, {})), r.cd.call
    try:
        lake.create(REB, entry("x")["metadata"])
    finally:
        r.cd.call = real
    [(method, url, body)] = sent
    assert method == "POST" and url.endswith("/namespaces/p__base/tables")
    assert body["name"] == "silver" and body["schema"]["schema-id"] == 0 and body["properties"] == {"p": "v"}
    assert body["partition-spec"]["spec-id"] == 0 and body["write-order"]["order-id"] == 0


# ---- the in-network lake phases -------------------------------------------------------------------

def release(tmp_path):
    rel = tmp_path / "releases" / "rel-1"
    (rel / "compiled/airflow").mkdir(parents=True)
    (rel / "compiled/lake").mkdir(parents=True)
    (rel / "compiled/airflow/dags.json").write_text(json.dumps({"dags": [{"product": p, "dagId": p.split(".")[1]} for p in PRODUCTS]}))
    (rel / "compiled/lake/backup-set.json").write_text(json.dumps(BACKUP_SET))
    (rel / "manifest.json").write_text(json.dumps({"images": {"tools": "tools@sha256:0"}}))
    (tmp_path / "releases" / "pointers").mkdir()
    (tmp_path / "releases" / "pointers" / "trino").symlink_to("../rel-1")
    return rel


def test_the_rest_phase_refuses_unless_every_product_is_held(tmp_path):
    rel, run = release(tmp_path), write_run(tmp_path / "b", "20261128T020000Z")
    lake, puts = FakeLake(), []
    a = argparse.Namespace(release=str(rel), run_dir=str(run), phase="rest")
    with pytest.raises(r.Refused, match="not every product is held.*dom.q"):
        r.run_lake(a, lake, held={"dom.p": {"kind": "restore"}}.get, head_=lambda u: None, put_=lambda u, d: puts.append(u))
    assert puts == [] and lake.calls == []  # nothing put back, nothing registered
    out = r.run_lake(a, lake, held=lambda p: {"kind": "restore"}, head_=lambda u: None, put_=lambda u, d: puts.append(u))
    assert [c[:2] for c in lake.calls] == [("register", T)] and out["recreated"] == []
    assert "s3://k2-landing/dom/p/2026/f.csv" in puts and not any("/holds/" in u for u in puts)  # the platform tables are phase 1's


def test_the_platform_phase_puts_the_holds_back_first_and_refuses_another_table(tmp_path):
    rel, run = release(tmp_path), write_run(tmp_path / "b", "20261128T020000Z")
    a = argparse.Namespace(release=str(rel), run_dir=str(run), phase="platform")
    lake, puts = FakeLake(), []
    out = r.run_lake(a, lake, held=None, head_=lambda u: None, put_=lambda u, d: puts.append(u))
    assert [c[:2] for c in lake.calls] == [("register", PLAT)] and all("/holds/" in u for u in puts) and out["lines"]
    planted = FakeLake({T: entry(META, location="s3://k2-lake/warehouse/k2/p/bronze-0f3a")})
    with pytest.raises(r.Refused, match="not the ones the backup holds"):
        r.run_lake(a, planted, held=None, head_=lambda u: None, put_=lambda u, d: puts.append(u))
    assert planted.calls == []


# ---- the host side ----------------------------------------------------------------------------------

class Docker:
    """docker and the in-network subcommands, recorded; each answers as the step would."""

    def __init__(self, drift_bad=(), drift_rebuild=(), refuse=None, recreated=()):
        self.calls, self.drift_bad, self.drift_rebuild, self.refuse, self.recreated = [], list(drift_bad), list(drift_rebuild), refuse, list(recreated)

    def __call__(self, cmd, **kw):
        def done(out="", rc=0, err=""):
            return subprocess.CompletedProcess(cmd, rc, out, err)
        if "airflow" in cmd:
            self.calls.append(f"{cmd[cmd.index('dags') + 1]} {cmd[cmd.index('dags') + 2]}")
            return done()
        if "psql" in " ".join(cmd):
            return done("3\n")  # three warehouses: the catalog is there
        if "restore.py" in " ".join(cmd):
            i = next(k for k, x in enumerate(cmd) if x.endswith("restore.py"))
            sub = cmd[i + 1:i + 4]
            step = " ".join(sub[:3]) if sub[0] == "lake" else " ".join(sub[:2])
            self.calls.append(step)
            if self.refuse == step:
                return done(rc=2, err="restore: REFUSED: no")
            if sub[0] == "lake":
                return done(json.dumps({"lines": ["table x: ok"], "recreated": self.recreated if "rest" in sub else []}))
            if sub[0] == "drift":
                return done(json.dumps({"bad": self.drift_bad, "rebuild": self.drift_rebuild, "pending": [], "left": [], "skipped": []}))
            return done(json.dumps({"lines": [f"{sub[1]} dom.p"]}))
        raise AssertionError(cmd)


def stack(tmp_path, docker, **kw):
    release(tmp_path)
    write_run(tmp_path / "b", "20261128T020000Z")
    (tmp_path / "s/principals").mkdir(parents=True)
    (tmp_path / "s/principals/svc.operator.password").write_text("x")
    return r.Stack(releases=tmp_path / "releases", backup=tmp_path / "b", secrets=tmp_path / "s", network="n_k2-catalog",
                   tools_image="tools@sha256:0", minio_image="minio", sh=docker, **kw)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("K2_RESTORE_S3_ACCESS_KEY", "k2-restore-0a1b2c3d")
    monkeypatch.setenv("K2_RESTORE_S3_SECRET_KEY", "s" * 40)


def test_the_order_dags_paused_holds_before_the_tables_released_after_the_tags(tmp_path):
    d = Docker()
    assert r.restore(stack(tmp_path, d), None, say=lambda s: None) == r.OK
    assert d.calls == ["pause p", "pause q", "lake --phase platform", "holds hold", "lake --phase rest", "drift --release",
                       "holds release", "unpause p", "unpause q"]


def test_a_drift_difference_keeps_every_hold_and_every_dag_paused(tmp_path):
    d = Docker(drift_bad=["DRIFT tag dom.p__base.silver audited = 5: the newest audited run record says 4"])
    said = []
    assert r.restore(stack(tmp_path, d), None, say=said.append) == r.FAILED
    assert "holds release" not in d.calls and not any(c.startswith("unpause") for c in d.calls)
    assert any("every hold kept, every DAG left paused" in s for s in said)


def test_a_refused_lake_check_unpauses_and_holds_nothing(tmp_path):
    d = Docker(refuse="lake --phase platform")
    with pytest.raises(r.Refused):
        r.restore(stack(tmp_path, d), None, say=lambda s: None)
    assert d.calls == ["pause p", "pause q", "lake --phase platform", "unpause p", "unpause q"]


def test_no_restore_key_refuses_before_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("K2_RESTORE_S3_SECRET_KEY")
    d = Docker()
    with pytest.raises(r.Failed, match="K2_RESTORE_S3_SECRET_KEY not set"):
        r.restore(stack(tmp_path, d), None, say=lambda s: None)
    assert d.calls == []


def test_the_live_holds_run_on_the_host_as_svc_operator_and_the_lake_in_network_with_the_restore_key(tmp_path):
    st = stack(tmp_path, Docker())
    cmd = r.docker_run(st, "rel-1", "lake", "--phase", "rest")
    assert cmd[:3] == ["docker", "run", "--rm"] and cmd[cmd.index("--network") + 1] == "n_k2-catalog"
    assert ["-e", r.KEY[0]] == cmd[cmd.index(r.KEY[0]) - 1:cmd.index(r.KEY[0]) + 1]  # by name, never the value
    assert "s" * 40 not in " ".join(cmd) and not any("operator" in x for x in cmd) and not any("MINIO_ROOT" in x for x in cmd)
    seen = []
    st.sh = lambda c, **kw: seen.append(c) or subprocess.CompletedProcess(c, 0, json.dumps({"lines": []}), "")
    r.holds_step(st, tmp_path / "releases/rel-1", "hold", "20261128T020000Z", lambda s: None)
    [c] = seen
    assert c[0] == sys.executable and c[c.index("--trino") + 1] == "localhost:8443"
    assert c[c.index("--password-file") + 1] == str(tmp_path / "s/principals/svc.operator.password")


def test_differences_on_a_recreated_table_are_its_rebuild_not_a_failure():
    found = {"differences": ["DRIFT tag dom.p__base.silver: no 'audited' tag",
                             "DRIFT view dom.p.silver: its base dom.p__base.silver has no audited tag to read",
                             "DRIFT tag dom.p__base.silver_x: no 'audited' tag"]}
    bad, left = r.classify(found, ["dom.p__base.silver"])
    assert bad == ["DRIFT tag dom.p__base.silver_x: no 'audited' tag"] and len(left) == 2


def test_the_drift_cli_holds_each_tag_to_the_run_records_with_runs(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cd, "expected", lambda release: {})
    monkeypatch.setattr(cd, "runs_reader", lambda: "the run-records reader")
    monkeypatch.setattr(cd, "drift", lambda uri, want, newest_audited=None, left=None: seen.update(n=newest_audited)
                        or {"differences": [], "pending": [], "skipped": [], "left": []})
    assert cd.main(["--release", str(tmp_path), "--runs"]) == 0 and seen["n"] == "the run-records reader"
    assert cd.main(["--release", str(tmp_path)]) == 0 and seen["n"] is None


def test_the_drill_refuses_without_the_live_release_named(tmp_path):
    d = Docker()
    with pytest.raises(r.Refused, match="CONFIRM=rel-1"):
        r.drill(stack(tmp_path, d), "yes", say=lambda s: None)
    assert d.calls == []

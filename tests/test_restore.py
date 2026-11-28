"""scripts/restore.py (make restore, make restore-drill): every refusal, decision and ordering, with
fakes at the storage, the catalog, Trino, Airflow and docker. A recording fake pins the order of the
steps, so a reordering fails here. The whole path on a throwaway lake is scripts/verify/restore-path.sh
(which has no Airflow: the pause, drain and unpause are proven here only)."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import fastavro
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import restore as r  # noqa: E402

spec = importlib.util.spec_from_file_location("catalog_drift_cli", ROOT / "scripts/verify/catalog_drift.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

T, REB, PLAT = "k2.ecb_fx_rates.bronze_daily", "fx_reference.ecb_fx_rates__base.silver_rates", "governance.run_records.holds"
LOC = "s3://k2-lake/warehouse/k2/ecb/bronze"
META = f"{LOC}/metadata/00002.gz.metadata.json"
STAMP = "20261128T020000Z"
# The release's products and their inputs, as the compiled pipelines have them (phases 004 and 005)
GRAPH = {"reference-data.calendars": [], "fx-reference.ecb-fx-rates": ["reference-data.calendars.target_days"],
         "reference-data.currency-master": [], "market-capture.kraken-instruments": [],
         "reference-data.security-master": ["market-capture.kraken-instruments.silver_listing", "reference-data.currency-master.currency"],
         "market-capture.kraken-trades": ["reference-data.security-master.venue_symbol"],
         "market-analytics.canonical-trades": ["market-capture.kraken-trades.silver_trade"],
         "market-analytics.candles": ["market-analytics.canonical-trades.trades"], "governance.quality-results": []}
KIND = {"fx-reference.ecb-fx-rates": "https-file", "reference-data.currency-master": "https-file",
        "market-capture.kraken-instruments": "public-api", "market-capture.kraken-trades": "kafka"}
BRONZE = {"fx-reference.ecb-fx-rates": "lake.ecb_fx_rates.bronze_daily", "reference-data.currency-master": "lake.currency_master.bronze_currencies",
          "market-capture.kraken-trades": "lake.kraken_trades.bronze_trade"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dag(p: str) -> str:
    return p.split(".")[1].replace("-", "_")


def write_run(backup: Path, stamp: str, *, snapshots=(1, 2)) -> Path:
    """One complete run as backup_lake.py copy writes it: a restore table (its metadata file and one
    data file), a platform table, a landing object, and the catalog dump."""
    run = backup / "runs" / stamp
    meta = json.dumps({"location": LOC, "snapshots": [{"snapshot-id": s} for s in snapshots]}).encode()
    files = {"objects/k2-lake/warehouse/k2/ecb/bronze/metadata/00002.gz.metadata.json": gzip.compress(meta),
             "objects/k2-lake/warehouse/k2/ecb/bronze/data/a.parquet": b"rows",
             "objects/k2-lake/warehouse/governance/run_records/holds/metadata/1.metadata.json": b"{}",
             "objects/k2-landing/fx/ecb/2026/f.xml": b"<a/>"}
    for rel, data in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(data)
    (backup / "catalog").mkdir(parents=True, exist_ok=True)
    dump = gzip.compress(b"-- PostgreSQL database dump complete\n")
    (backup / "catalog" / f"{stamp}.sql.gz").write_bytes(dump)
    size = {k: len(v) for k, v in files.items()}
    tables = [{"table": T, "ref": "main", "location": LOC, "metadata_location": META, "snapshot_id": 2, "objects": 2, "bytes": 0,
               "files": {k: n for k, n in size.items() if "/ecb/bronze/" in k}},
              {"table": PLAT, "ref": "main", "location": "s3://k2-lake/warehouse/governance/run_records/holds",
               "metadata_location": "s3://k2-lake/warehouse/governance/run_records/holds/metadata/1.metadata.json",
               "snapshot_id": 7, "objects": 1, "bytes": 2, "files": {k: n for k, n in size.items() if "/holds/" in k}}]
    manifest = {"run": stamp, "release": "rel-1", "tag": "v0", "tables": tables, "rebuild": [REB],
                "landings": [{"bucket": "k2-landing", "prefix": "fx/ecb/", "objects": 1, "bytes": 4,
                              "files": {k: n for k, n in size.items() if "k2-landing" in k}}],
                "dumps": {"lakekeeper": {"file": f"catalog/{stamp}.sql.gz", "bytes": len(dump), "sha256": sha(dump)}}}
    (run / "manifest.json").write_text(json.dumps(manifest))
    return run


BACKUP_SET = {"tag": "v0", "entries": [
    {"kind": "landing", "bucket": "k2-landing", "prefix": "fx/ecb/"},
    {"kind": "table", "mode": "restore", "ref": "main", "table": T},
    {"kind": "table", "mode": "restore", "ref": "main", "table": PLAT},
    {"kind": "table", "mode": "rebuild", "ref": "audited", "table": REB},
    {"kind": "lakekeeper-db"}]}


def release(tmp_path):
    rel = tmp_path / "releases" / "rel-1"
    (rel / "compiled/airflow").mkdir(parents=True)
    (rel / "compiled/lake").mkdir(parents=True)
    (rel / "compiled/airflow/dags.json").write_text(json.dumps({"dags": [{"product": p, "dagId": dag(p)} for p in GRAPH]}))
    for p, ins in GRAPH.items():
        (rel / "compiled/products" / p).mkdir(parents=True)
        doc = {"product": p, "dagId": dag(p), "inputs": [{"contractId": c} for c in ins], "build": {"engine": "dbt"}}
        if p in KIND:
            doc["connector"] = {"kind": KIND[p]}
        if p in BRONZE:
            doc["bronze"] = [{"table": BRONZE[p]}]
        (rel / "compiled/products" / p / "pipeline.json").write_text(json.dumps(doc))
    (rel / "compiled/lake/backup-set.json").write_text(json.dumps(BACKUP_SET))
    (rel / "manifest.json").write_text(json.dumps({"images": {"tools": "tools@sha256:0"}}))
    (tmp_path / "releases" / "pointers").mkdir()
    (tmp_path / "releases" / "pointers" / "trino").symlink_to("../rel-1")
    return rel


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for n in (*r.KEY, *r.READ_KEY):
        monkeypatch.setenv(n, n.lower() + "-0a1b2c3d4e")
    monkeypatch.setenv("MINIO_ROOT_USER", "root")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "root-password")


# ---- 1. the run ------------------------------------------------------------------------------------

def test_the_newest_complete_run_is_chosen(tmp_path):
    write_run(tmp_path, "20261128T010000Z")
    run = write_run(tmp_path, STAMP)
    (tmp_path / "runs" / ".20261128T030000Z.partial").mkdir()  # a copy that never finished is never a run
    assert r.choose_run(tmp_path, BACKUP_SET, None)[0] == run
    assert r.choose_run(tmp_path, BACKUP_SET, run.name)[0] == run


def test_a_run_older_than_the_newest_is_refused(tmp_path):
    write_run(tmp_path, "20261128T010000Z")
    write_run(tmp_path, STAMP)
    with pytest.raises(r.Refused, match=f"older than the newest complete run {STAMP}"):
        r.choose_run(tmp_path, BACKUP_SET, "20261128T010000Z")
    with pytest.raises(r.Refused, match="not a complete run"):
        r.choose_run(tmp_path, BACKUP_SET, ".20261128T030000Z.partial")


def test_an_incomplete_run_is_refused(tmp_path):
    run = write_run(tmp_path, STAMP)
    (run / "objects/k2-lake/warehouse/k2/ecb/bronze/data/a.parquet").unlink()
    with pytest.raises(r.Refused, match="is incomplete: .*a.parquet is missing"):
        r.choose_run(tmp_path, BACKUP_SET, None)
    with pytest.raises(r.Refused, match="no complete backup run"):
        r.choose_run(tmp_path / "empty", BACKUP_SET, None)


def test_a_run_whose_tables_are_not_the_live_sets_is_refused(tmp_path):
    write_run(tmp_path, STAMP)
    moved = {"tag": "v1", "entries": [e | {"mode": "rebuild"} if e.get("table") == PLAT else e for e in BACKUP_SET["entries"]]
             + [{"kind": "table", "mode": "rebuild", "ref": "audited", "table": "dom.p__base.gold"}]}
    with pytest.raises(r.Refused, match="does not hold what the live backup set names"):
        r.choose_run(tmp_path, moved, None)
    m = json.loads((tmp_path / "runs" / STAMP / "manifest.json").read_text())
    assert r.set_problems(moved, m) == [f"{PLAT}: the live backup set says rebuild, the run restore",
                                        "dom.p__base.gold: in the live backup set, not in the run"]
    assert r.set_problems({"entries": [e for e in BACKUP_SET["entries"] if e["kind"] != "landing"]}, m) == \
        ["landing s3://k2-landing/fx/ecb/: in the run, not in the live backup set"]


# ---- storage and wholeness ---------------------------------------------------------------------------

class FakeStore:
    """Objects by uri: their sizes, and the bytes of those a test reads. Every list and get is counted."""

    def __init__(self, sizes=None, blobs=None):
        self.sizes, self.blobs, self.puts, self.lists, self.gets = dict(sizes or {}), dict(blobs or {}), [], [], []

    def list(self, prefix):
        prefix = prefix.rstrip("/") + "/"
        self.lists.append(prefix)
        return {u: n for u, n in self.sizes.items() if u.startswith(prefix)}

    def get(self, uri):
        self.gets.append(uri)
        if uri not in self.blobs or uri not in self.sizes:
            raise r.Failed(f"{uri}: 404")
        return self.blobs[uri]

    def put(self, uri, data):
        self.puts.append(uri)
        self.sizes[uri] = len(data)


def test_put_back_puts_what_is_gone_and_never_overwrites(tmp_path):
    run = write_run(tmp_path, STAMP)
    files = json.loads((run / "manifest.json").read_text())["tables"][0]["files"]
    store = FakeStore({f"{LOC}/data/a.parquet": 4})
    assert r.put_back(files, run, store.list(LOC), store.put) == (1, 1) and store.puts == [META]
    store = FakeStore({f"{LOC}/data/a.parquet": 9})  # another object at that key
    with pytest.raises(r.Failed, match="never overwritten"):
        r.put_back(files, run, store.list(LOC), store.put)
    assert store.puts == [] or store.puts == [META]


def avro(fields, records):
    buf = io.BytesIO()
    fastavro.writer(buf, fastavro.parse_schema({"type": "record", "name": "r", "fields": fields}), records, codec="deflate")
    return buf.getvalue()


def table_in_store(main_data=b"main rows", tag_data=b"tagged rows"):
    """A table with main and an audited tag on two snapshots, each its manifest list, manifest and one
    data file; returns (catalog entry, store)."""
    entry_fields = [{"name": "status", "type": "int"}, {"name": "data_file", "type": {"type": "record", "name": "df", "fields": [
        {"name": "file_path", "type": "string"}, {"name": "file_size_in_bytes", "type": "long"}]}}]
    list_fields = [{"name": "manifest_path", "type": "string"}, {"name": "manifest_length", "type": "long"}]
    blobs, snaps = {}, []
    for sid, data in ((2, main_data), (1, tag_data)):
        d, m, ml = f"{LOC}/data/{sid}.parquet", f"{LOC}/metadata/m{sid}.avro", f"{LOC}/metadata/snap-{sid}.avro"
        blobs[d] = data
        blobs[m] = avro(entry_fields, [{"status": 1, "data_file": {"file_path": d, "file_size_in_bytes": len(data)}}])
        blobs[ml] = avro(list_fields, [{"manifest_path": m, "manifest_length": len(blobs[m])}])
        snaps.append({"snapshot-id": sid, "manifest-list": ml})
    blobs[META] = b"{}"
    entry = {"metadata-location": META, "metadata": {"location": LOC, "current-snapshot-id": 2, "snapshots": snaps,
             "refs": {"main": {"snapshot-id": 2, "type": "branch"}, "audited": {"snapshot-id": 1, "type": "tag"}}}}
    return entry, FakeStore({u: len(b) for u, b in blobs.items()}, blobs)


@pytest.mark.parametrize("lose, is_whole", [
    (None, True),
    (f"{LOC}/data/2.parquet", False),       # main's data file: a lost data prefix keeps the metadata
    (f"{LOC}/data/1.parquet", False),       # the audited tag's, which readers read
    (f"{LOC}/metadata/m2.avro", False),     # a manifest
    (f"{LOC}/metadata/snap-1.avro", False), # the tag's manifest list
    (META, False),
])
def test_whole_needs_every_file_of_main_and_the_audited_tag_from_one_listing(lose, is_whole):
    entry, store = table_in_store()
    if lose:
        del store.sizes[lose]
    assert r.whole(entry, store) is is_whole
    assert store.lists == [LOC + "/"]  # one listing of the table's location, never a HEAD per file
    assert not any("/data/" in u for u in store.gets)  # data files are checked against the listing, not read


def test_a_data_file_of_another_size_is_not_whole():
    entry, store = table_in_store()
    store.sizes[f"{LOC}/data/2.parquet"] = 3
    assert r.whole(entry, store) is False


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


@pytest.mark.parametrize("catalog, readable, want", [
    (None, True, "register"),
    (entry(META), True, "same"),
    # the dump is older than the copy: its snapshot is in the backup's metadata, so forward to the backup's
    (entry(f"{LOC}/metadata/00001.metadata.json", sid=1), True, "re-point"),
    # newer than the backup and whole: a commit after the copy, kept
    (entry(f"{LOC}/metadata/00003.metadata.json", sid=3), True, "keep-newer"),
    # newer, but not whole: the backup's
    (entry(f"{LOC}/metadata/00003.metadata.json", sid=3), False, "re-point"),
])
def test_a_restore_tables_action_is_decided_from_the_catalog_and_the_store(tmp_path, catalog, readable, want):
    run = write_run(tmp_path, STAMP)
    assert r.decide(table_of(run), catalog, r.backup_meta(table_of(run), run), lambda c: readable) == want


def test_restore_table_decides_before_it_puts_back_then_registers(tmp_path):
    run = write_run(tmp_path, STAMP)
    lake, store = FakeLake(), FakeStore()
    action, line = r.restore_table(table_of(run), run, lake, store)
    assert action == "register" and lake.calls == [("register", T, META)] and len(store.puts) == 2
    lake = FakeLake({T: entry(f"{LOC}/metadata/00001.metadata.json", sid=1)})
    assert r.restore_table(table_of(run), run, lake, FakeStore())[0] == "re-point" and lake.calls == [("drop", T), ("register", T, META)]


def test_a_restore_table_at_another_location_is_another_table(tmp_path):
    run = write_run(tmp_path, STAMP)
    other = entry(META, location="s3://k2-lake/warehouse/k2/ecb/bronze-0f3a")
    assert r.lake_problems([table_of(run)], {T: other}.get) == [
        f"{T}: the catalog's is at s3://k2-lake/warehouse/k2/ecb/bronze-0f3a, the backup's at {LOC}"]
    assert r.lake_problems([table_of(run)], {T: entry(META)}.get) == []


def test_a_rebuild_table_is_recreated_only_when_it_is_not_whole():
    meta_file = f"{LOC}/metadata/9.json"
    whole_store = FakeStore({meta_file: 2})
    kept = FakeLake({REB: entry(meta_file, sid=None)})
    assert r.rebuild_table(REB, kept, whole_store) == "keep" and kept.calls == []
    gone = FakeLake({REB: entry(meta_file)})
    assert r.rebuild_table(REB, gone, FakeStore()) == "recreate" and gone.calls == [("drop", REB), ("create", REB, 0)]
    assert r.rebuild_table(REB, FakeLake(), FakeStore()) == "absent"


def test_create_sends_the_old_tables_shape(monkeypatch):
    sent = []
    lake = r.Lake("http://lakekeeper:8181/catalog")
    lake._base["fx_reference"] = "http://lakekeeper:8181/catalog/v1/wh"
    monkeypatch.setattr(r.cd, "call", lambda m, u, b=None: sent.append((m, u, b)) or (200, {}))
    lake.create(REB, entry("x")["metadata"])
    [(method, url, body)] = sent
    assert method == "POST" and url.endswith("/namespaces/ecb_fx_rates__base/tables")
    assert body["name"] == "silver_rates" and body["schema"]["schema-id"] == 0 and body["properties"] == {"p": "v"}
    assert body["partition-spec"]["spec-id"] == 0 and body["write-order"]["order-id"] == 0


# ---- R3: what a recreated table leaves held ------------------------------------------------------------

def test_a_recreated_table_leaves_its_product_and_everything_downstream_in_order(tmp_path):
    rel = release(tmp_path)
    order, own = r.affected(rel, ["k2.kraken_trades.bronze_trade"])
    assert order == ["market-capture.kraken-trades", "market-analytics.canonical-trades", "market-analytics.candles"]
    assert own == {"market-capture.kraken-trades": ["k2.kraken_trades.bronze_trade"]}
    order, _ = r.affected(rel, ["market_analytics.canonical_trades__base.trades", "governance.quality_results__base.test_results",
                                "k2.currency_master.bronze_currencies"])
    assert order == ["reference-data.currency-master", "market-analytics.canonical-trades", "reference-data.security-master",
                     "market-analytics.candles", "market-capture.kraken-trades", "governance.quality-results"] or \
        (order.index("reference-data.currency-master") < order.index("reference-data.security-master")
         < order.index("market-capture.kraken-trades") and order.index("market-analytics.canonical-trades")
         < order.index("market-analytics.candles") and order[-1] == "governance.quality-results")
    assert r.affected(rel, ["governance.run_records.run_record"]) == ([], {})  # a platform table is no product's


def test_a_left_products_hold_reason_is_the_one_make_rebuild_reads():
    import rebuild as rb
    order = ["market-capture.kraken-trades", "market-analytics.canonical-trades"]
    left = {"run": STAMP, "recreated": [], "unpause": True}
    reason = r.hold_reason(STAMP, order[1], order, left)
    assert "make rebuild PRODUCT=market-analytics.canonical-trades, after market-capture.kraken-trades" in reason
    assert rb.awaiting({"kind": "restore", "reason": reason}) == left
    assert rb.awaiting({"kind": "restore", "reason": r.hold_reason(STAMP, order[0], order, None)}) is None
    with pytest.raises(r.Failed, match="over 1000"):
        r.hold_reason(STAMP, order[0], order, {"recreated": ["x" * 1000]})


class FakeHolds:
    """k2runs' writes and rebuild.open_holds over one list of rows."""

    def __init__(self, monkeypatch, open_=None):
        import k2runs
        import rebuild as rb
        self.open, self.log = {p: list(hs) for p, hs in (open_ or {}).items()}, []
        monkeypatch.setattr(rb, "open_holds", lambda c, p: self.open.get(p, []))
        monkeypatch.setattr(k2runs, "hold", self.hold)
        monkeypatch.setattr(k2runs, "release", self.release)

    def hold(self, conn, p, reason, by, kind="operator"):
        self.log.append(("hold", p, kind, reason))
        self.open.setdefault(p, []).append({"kind": kind, "reason": reason, "held_by": by, "held_at": "t"})

    def release(self, conn, p, by, kind="operator"):
        self.log.append(("release", p, kind))
        self.open[p] = [h for h in self.open.get(p, []) if h["kind"] != kind]


def holds_args(rel, verb, plan=None, keep=None):
    return argparse.Namespace(verb=verb, release=str(rel), run=STAMP, plan=json.dumps(plan or {}), keep=json.dumps(keep or []),
                              trino="trino:8443", password_file="p", ca="c")


def test_the_holds_mark_the_left_products_and_their_release_keeps_them(tmp_path, monkeypatch):
    rel, holds = release(tmp_path), FakeHolds(monkeypatch)
    plan = {"market-capture.kraken-trades": {"run": STAMP, "recreated": ["k2.kraken_trades.bronze_trade"], "unpause": True},
            "market-analytics.canonical-trades": {"run": STAMP, "recreated": [], "unpause": False}}
    r.run_holds(holds_args(rel, "hold", plan), conn=object())
    reasons = {p: reason for _, p, _, reason in holds.log}
    assert set(reasons) == set(GRAPH) and all(k == "restore" for _, _, k, _ in holds.log)
    assert all(("k2.restore.rebuild=" in reasons[p]) == (p in plan) for p in GRAPH)
    holds.log.clear()
    r.run_holds(holds_args(rel, "release", keep=["market-capture.kraken-trades"]), conn=object())
    assert sorted(p for _, p, _ in holds.log) == sorted(set(GRAPH) - set(plan))  # canonical's mark keeps it too
    holds.log.clear()
    r.run_holds(holds_args(rel, "release"), conn=object())  # a later restore never ends what this one left for rebuild
    assert holds.log == []


def test_the_holds_refuse_an_open_rebuild_lock_before_any_hold(tmp_path, monkeypatch):
    rel = release(tmp_path)
    holds = FakeHolds(monkeypatch, {"market-analytics.candles": [{"kind": "rebuild", "reason": "make rebuild", "held_by": "x", "held_at": "t"}]})
    with pytest.raises(r.Refused, match="market-analytics.candles has a rebuild lock open"):
        r.run_holds(holds_args(rel, "hold"), conn=object())
    assert holds.log == []


# ---- the in-network phases -----------------------------------------------------------------------------

def lake_args(rel, run, phase, fresh=False):
    return argparse.Namespace(release=str(rel), run_dir=str(run), phase=phase, fresh=fresh)


def open_(*kinds):
    return [{"kind": k, "reason": "", "held_by": "x", "held_at": "t"} for k in kinds]


def test_the_rest_phase_needs_a_restore_hold_on_every_product_and_no_rebuild_lock(tmp_path):
    rel, run = release(tmp_path), write_run(tmp_path / "b", STAMP)
    lake, store = FakeLake(), FakeStore()
    a = lake_args(rel, run, "rest")
    for bad, why in ((lambda p: open_("operator") if p.endswith("candles") else open_("restore"), "candles has no restore hold"),
                     (lambda p: open_("restore", "rebuild") if p.endswith("candles") else open_("restore"), "candles has a rebuild lock")):
        with pytest.raises(r.Refused, match=why):
            r.run_lake(a, lake, store, holds=bad)
        assert store.puts == [] and lake.calls == []  # nothing put back, nothing registered
    out = r.run_lake(a, lake, store, holds=lambda p: open_("restore"))
    assert [c[:2] for c in lake.calls] == [("register", T)] and out["changed"] == [T] and out["recreated"] == []
    assert "s3://k2-landing/fx/ecb/2026/f.xml" in store.puts and not any("/holds/" in u for u in store.puts)


def test_the_platform_phase_puts_the_holds_back_and_refuses_another_table(tmp_path):
    rel, run = release(tmp_path), write_run(tmp_path / "b", STAMP)
    lake, store = FakeLake(), FakeStore()
    out = r.run_lake(lake_args(rel, run, "platform"), lake, store)
    assert [c[:2] for c in lake.calls] == [("register", PLAT)] and all("/holds/" in u for u in store.puts) and out["lines"]
    planted = FakeLake({T: entry(META, location="s3://k2-lake/warehouse/k2/ecb/bronze-0f3a")})
    with pytest.raises(r.Refused, match="not the ones the backup holds"):
        r.run_lake(lake_args(rel, run, "platform"), planted, FakeStore())
    assert planted.calls == []


def test_the_check_plans_without_writing(tmp_path):
    rel, run = release(tmp_path), write_run(tmp_path / "b", STAMP)
    lake = FakeLake({REB: entry(f"{LOC}/../silver/metadata/1.json")})
    store = FakeStore()
    out = r.run_lake(lake_args(rel, run, "check"), lake, store)
    assert out["problems"] == [] and out["changes"] == {T: "register", REB: "recreate"} and out["stale"] == []
    assert lake.calls == [] and store.puts == []


def test_freshness_names_a_commit_a_landing_and_a_run_record_since_the_run(tmp_path):
    run = write_run(tmp_path, STAMP)
    m = json.loads((run / "manifest.json").read_text())
    same = FakeLake({t["table"]: {"metadata-location": t["metadata_location"], "metadata": {"location": t["location"]}} for t in m["tables"]})
    landed = FakeStore({"s3://k2-landing/fx/ecb/2026/f.xml": 4})
    before = datetime(2026, 11, 28, 1, 59, tzinfo=timezone.utc)
    assert r.freshness(m, STAMP, same, landed, before) == []
    same.tables[T] = {"metadata-location": f"{LOC}/metadata/00003.metadata.json", "metadata": {"location": LOC}}
    landed.sizes["s3://k2-landing/fx/ecb/2026/g.xml"] = 9
    stale = r.freshness(m, STAMP, same, landed, datetime(2026, 11, 28, 2, 0, 1, tzinfo=timezone.utc))
    assert len(stale) == 3 and T in stale[0] and "1 object(s) since the run" in stale[1] and "after the run began" in stale[2]


# ---- the host side: the recording fake -----------------------------------------------------------------

class Rec:
    """Airflow (scripts/rebuild.py's Live) and every docker call, recorded as one ordered list of step
    names; each in-network step answers from `answers`."""

    def __init__(self, paused=(), lost="present", running=(), answers=None):
        self.calls, self.was_paused, self.lost, self.alive = [], set(paused), lost, set(running)
        self.answers = {"check": {"problems": [], "changes": {}, "locations": {T: LOC}, "refs_lost": [], "stale": []},
                        "platform": {"lines": ["table holds: ok"], "changed": [], "recreated": []},
                        "rest": {"lines": [], "changed": [], "recreated": []},
                        "drift": {"bad": [], "rebuild": [], "pending": [], "left": [], "skipped": []}, **(answers or {})}
        self.argv, self.t = {}, 0.0

    # rebuild.Live's interface
    def paused(self, d):
        return d in self.was_paused

    def pause(self, d):
        self.calls.append(f"pause {d}")
        self.was_paused.add(d)

    def unpause(self, d):
        self.calls.append(f"unpause {d}")

    def busy(self, d):
        self.calls.append(f"drain {d}")
        return []

    def running(self, name):
        return name in self.alive

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t

    def __call__(self, cmd, **kw):
        def done(out="", rc=0, err=""):
            return subprocess.CompletedProcess(cmd, rc, out, err)
        line = " ".join(cmd)
        if "restore.py" in line:
            i = next(k for k, x in enumerate(cmd) if x.endswith("restore.py"))
            sub = cmd[i + 1]
            step = (cmd[i + 3] + (" fresh" if "--fresh" in cmd else "")) if sub == "lake" else f"{sub} {cmd[i + 2]}" if sub == "holds" else sub
            self.calls.append(step)
            self.argv[step] = cmd
            answer = self.answers[step.split()[0] if sub == "lake" else sub if sub == "drift" else "holds"] if sub != "holds" else {"lines": []}
            if isinstance(answer, int):
                return done(rc=answer, err="restore: REFUSED: no")
            return done(json.dumps(answer))
        if "SELECT count(*) FROM warehouse" in line:
            self.calls.append("catalog?")
            return {"present": done("3\n"), "lost": done("0\n"), "gone": done(rc=2, err='psql: error: FATAL:  database "lakekeeper" does not exist'),
                    "restarting": done(rc=2, err="psql: error: FATAL:  the database system is starting up"),
                    "busy": done(rc=2, err="psql: error: FATAL:  sorry, too many clients already")}[self.lost]
        if "--if-exists" in line or cmd[1] in ("stop", "start") or "ON_ERROR_STOP" in line:
            self.calls.append("replay")
            return done()
        if "healthcheck" in line:
            return done()
        if "dropdb" in line:
            self.calls.append("damage db")
            return done()
        if " mc" in line or "--entrypoint mc" in line:
            self.calls.append(f"damage {cmd[-1]}")
            return done()
        raise AssertionError(cmd)


def stack(tmp_path, rec, airflow=True):
    release(tmp_path)
    write_run(tmp_path / "b", STAMP)
    (tmp_path / "s/principals").mkdir(parents=True)
    (tmp_path / "s/principals/svc.operator.password").write_text("x")
    return r.Stack(releases=tmp_path / "releases", backup=tmp_path / "b", secrets=tmp_path / "s", network="n_k2-catalog",
                   tools_image="tools@sha256:0", minio_image="minio", io=rec if airflow else None, sh=rec)


DAGS = sorted(dag(p) for p in GRAPH)


def test_restore_order_pause_drain_catalog_check_platform_holds_rest_drift_release_unpause(tmp_path):
    rec = Rec(paused={"calendars"})
    assert r.restore(stack(tmp_path, rec), None, say=lambda s: None) == r.OK
    unpaused = [d for d in DAGS if d != "calendars"]
    assert rec.calls == [*(f"pause {d}" for d in unpaused), *(f"drain {d}" for d in DAGS), "catalog?", "check", "platform",
                         "holds hold", "rest", "drift", "holds release", *(f"unpause {d}" for d in unpaused)]


def test_a_dag_paused_before_the_restore_is_never_unpaused(tmp_path):
    rec = Rec(paused=set(DAGS) - {"candles"})
    assert r.restore(stack(tmp_path, rec), None, say=lambda s: None) == r.OK
    assert [c for c in rec.calls if c.startswith(("pause", "unpause"))] == ["pause candles", "unpause candles"]


def test_a_product_left_for_rebuild_and_all_downstream_stay_held_and_paused(tmp_path):
    changed = {"k2.kraken_trades.bronze_trade": "recreate", T: "same"}
    rec = Rec(answers={"check": {"problems": [], "changes": changed, "locations": {}, "refs_lost": [], "stale": []},
                       "rest": {"lines": [], "changed": ["k2.kraken_trades.bronze_trade"], "recreated": ["k2.kraken_trades.bronze_trade"]}})
    said = []
    assert r.restore(stack(tmp_path, rec), None, say=said.append) == r.OK
    left = ["market-capture.kraken-trades", "market-analytics.canonical-trades", "market-analytics.candles"]
    cmd = rec.argv["holds hold"]
    plan = json.loads(cmd[cmd.index("--plan") + 1])
    assert list(plan) == left and plan[left[0]]["recreated"] == ["k2.kraken_trades.bronze_trade"] and plan[left[0]]["unpause"]
    cmd = rec.argv["holds release"]
    assert json.loads(cmd[cmd.index("--keep") + 1]) == left
    assert not any(c in rec.calls for c in ("unpause kraken_trades", "unpause canonical_trades", "unpause candles"))
    assert "unpause calendars" in rec.calls
    rebuilds = [s for s in said if s.startswith("restore: rebuild ")]
    assert [s.split("PRODUCT=")[1].split()[0] for s in rebuilds] == left


def test_a_table_changed_that_the_check_did_not_plan_fails_with_every_hold_kept(tmp_path):
    rec = Rec(answers={"rest": {"lines": [], "changed": [REB], "recreated": [REB]}})
    assert r.restore(stack(tmp_path, rec), None, say=lambda s: None) == r.FAILED
    assert "holds release" not in rec.calls and not any(c.startswith("unpause") for c in rec.calls)


def test_a_drift_difference_keeps_every_hold_and_every_dag_paused(tmp_path):
    rec = Rec(answers={"drift": {"bad": [f"DRIFT tag {REB} audited = 5: the newest audited run record says 4"], "rebuild": [],
                                 "pending": [], "left": [], "skipped": []}})
    said = []
    assert r.restore(stack(tmp_path, rec), None, say=said.append) == r.FAILED
    assert "holds release" not in rec.calls and not any(c.startswith("unpause") for c in rec.calls)
    assert any("every hold kept, every DAG left paused" in s for s in said)


@pytest.mark.parametrize("answer", ["restarting", "busy"])
def test_a_catalog_answer_that_is_not_definite_replaces_nothing(tmp_path, answer):
    rec = Rec(lost=answer)
    with pytest.raises(r.Failed, match="cannot tell whether the catalog database is lost"):
        r.restore(stack(tmp_path, rec), None, say=lambda s: None)
    assert "replay" not in rec.calls and "check" not in rec.calls and not any(c.startswith("unpause") for c in rec.calls)


@pytest.mark.parametrize("answer, lost", [("present", False), ("lost", True), ("gone", True)])
def test_the_catalog_is_lost_only_on_a_definite_answer(tmp_path, answer, lost):
    rec = Rec(lost=answer)
    assert r.catalog_lost(stack(tmp_path, rec)) is lost


def test_a_refusal_before_any_write_unpauses_what_this_restore_paused(tmp_path):
    rec = Rec(paused={"calendars"}, answers={"check": {"problems": ["planted"], "changes": {}, "locations": {}, "refs_lost": [], "stale": []}})
    with pytest.raises(r.Refused, match="planted"):
        r.restore(stack(tmp_path, rec), None, say=lambda s: None)
    assert [c for c in rec.calls if c.startswith("unpause")] == [f"unpause {d}" for d in DAGS if d != "calendars"]
    assert "platform" not in rec.calls and "holds hold" not in rec.calls


def test_a_refusal_after_a_catalog_replay_unpauses_nothing(tmp_path):
    rec = Rec(lost="lost", answers={"check": {"problems": ["planted"], "changes": {}, "locations": {}, "refs_lost": [], "stale": []}})
    said = []
    with pytest.raises(r.Refused):
        r.restore(stack(tmp_path, rec), None, say=said.append)
    assert "replay" in rec.calls and not any(c.startswith("unpause") for c in rec.calls)
    assert any("nothing is unpaused" in s for s in said)


def test_a_running_make_rebuild_refuses_before_anything(tmp_path):
    rec = Rec(running={"k2-dbt-runner-candles-rebuild"})
    with pytest.raises(r.Refused, match="make rebuild runs"):
        r.restore(stack(tmp_path, rec), None, say=lambda s: None)
    assert rec.calls == []


def test_no_storage_key_refuses_before_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("K2_RESTORE_S3_SECRET_KEY")
    rec = Rec()
    with pytest.raises(r.Failed, match="K2_RESTORE_S3_SECRET_KEY not set"):
        r.restore(stack(tmp_path, rec), None, say=lambda s: None)
    assert rec.calls == []


def test_only_the_writing_phases_get_the_restore_key(tmp_path):
    rec = Rec()
    assert r.restore(stack(tmp_path, rec), None, say=lambda s: None) == r.OK
    for step, want in (("check", r.READ_KEY), ("platform", r.KEY), ("rest", r.KEY), ("drift", ()), ("holds hold", ())):
        cmd = rec.argv[step]
        named = tuple(cmd[i + 1] for i, x in enumerate(cmd) if x == "-e" and "_S3_" in cmd[i + 1] and cmd[i + 1] != "K2_S3_ENDPOINT")
        assert named == tuple(want), step
        assert not any(os_v in " ".join(cmd) for os_v in ("0a1b2c3d4e", "root-password"))  # by name, never a value
    host = rec.argv["holds hold"]
    assert host[0] == sys.executable and host[host.index("--trino") + 1] == "localhost:8443"


def test_differences_on_a_recreated_table_are_its_rebuild_not_a_failure():
    found = {"differences": [f"DRIFT tag {REB}: no 'audited' tag", f"DRIFT view x: its base {REB} has no audited tag to read",
                             f"DRIFT tag {REB}_x: no 'audited' tag"]}
    bad, left = r.classify(found, [REB])
    assert bad == [f"DRIFT tag {REB}_x: no 'audited' tag"] and len(left) == 2


def test_the_drift_cli_holds_each_tag_to_the_run_records_with_runs(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cd, "expected", lambda release: {})
    monkeypatch.setattr(cd, "runs_reader", lambda: "the run-records reader")
    monkeypatch.setattr(cd, "drift", lambda uri, want, newest_audited=None, left=None: seen.update(n=newest_audited)
                        or {"differences": [], "pending": [], "skipped": [], "left": []})
    assert cd.main(["--release", str(tmp_path), "--runs"]) == 0 and seen["n"] == "the run-records reader"
    assert cd.main(["--release", str(tmp_path)]) == 0 and seen["n"] is None


# ---- R1: the drill checks everything before it damages anything -----------------------------------------

DRILL_CHECK = {"problems": [], "changes": {}, "refs_lost": ["k2.ecb_fx_rates.bronze_daily@audit"], "stale": [],
               "locations": {T: LOC, PLAT: "s3://k2-lake/warehouse/governance/run_records/holds",
                             "k2.kraken_trades.bronze_trade": "s3://k2-lake/warehouse/k2/kt/bronze",
                             "k2.currency_master.bronze_currencies": "s3://k2-lake/warehouse/k2/cm/bronze", REB: "s3://k2-lake/warehouse/fx/s"}}


def drill_stack(tmp_path, rec):
    st = stack(tmp_path, rec)
    m = st.backup / "runs" / STAMP / "manifest.json"
    doc = json.loads(m.read_text())
    doc["rebuild"] += ["k2.kraken_trades.bronze_trade", "k2.currency_master.bronze_currencies"]
    m.write_text(json.dumps(doc))
    rel = st.releases / "rel-1" / "compiled/lake/backup-set.json"
    s = json.loads(rel.read_text())
    s["entries"] += [{"kind": "table", "mode": "rebuild", "ref": "main", "table": t} for t in doc["rebuild"][1:]]
    rel.write_text(json.dumps(s))
    return st


@pytest.mark.parametrize("why, answers", [
    ("stale", {"check": {**DRILL_CHECK, "stale": [f"{T}: the catalog's metadata is newer"]}}),
    ("problems", {"check": {**DRILL_CHECK, "problems": ["planted"]}}),
])
def test_the_drill_refuses_a_run_that_cannot_put_back_what_it_destroys_before_any_pause(tmp_path, why, answers):
    rec = Rec(answers=answers)
    with pytest.raises(r.Refused, match="cannot put back"):
        r.drill(drill_stack(tmp_path, rec), "rel-1", say=lambda s: None)
    assert rec.calls == ["check fresh"]


def test_the_drill_refuses_an_incomplete_run_before_any_call(tmp_path):
    rec = Rec(answers={"check": DRILL_CHECK})
    st = drill_stack(tmp_path, rec)
    (st.backup / "runs" / STAMP / "objects/k2-landing/fx/ecb/2026/f.xml").unlink()
    with pytest.raises(r.Refused, match="incomplete"):
        r.drill(st, "rel-1", say=lambda s: None)
    assert rec.calls == []


def test_the_drill_states_what_it_destroys_and_refuses_without_confirm(tmp_path):
    rec = Rec(answers={"check": DRILL_CHECK})
    with pytest.raises(r.Refused) as e:
        r.drill(drill_stack(tmp_path, rec), "yes", say=lambda s: None)
    said = str(e.value)
    assert "CONFIRM=rel-1" in said and "rebuild table(s) k2.kraken_trades.bronze_trade (not in the run" in said
    assert "left as they are: k2.currency_master.bronze_currencies file product bronze" in said
    assert "lost: k2.ecb_fx_rates.bronze_daily@audit" in said
    assert rec.calls == ["check fresh"]


def test_the_drill_order_checks_pause_drain_holds_damage_restore_drift_selective_unpause(tmp_path):
    rec = Rec(paused={"calendars"}, lost="gone", answers={"check": DRILL_CHECK})
    assert r.drill(drill_stack(tmp_path, rec), "rel-1", say=lambda s: None) == r.OK
    unpaused = [d for d in DAGS if d != "calendars"]
    damage = [c for c in rec.calls if c.startswith("damage")]
    assert rec.calls == ["check fresh", *(f"pause {d}" for d in unpaused), *(f"drain {d}" for d in DAGS), "check fresh",
                         "holds hold", *damage, "catalog?", "replay", "replay", "replay", "replay", "check", "platform", "holds hold",
                         "rest", "drift", "holds release", *(f"unpause {d}" for d in unpaused)]
    # by location: each restore table's, each landing, the destroyed rebuild table's; never a file product's bronze
    assert damage == ["damage db", "damage k2/k2-lake/warehouse/k2/ecb/bronze/", "damage k2/k2-lake/warehouse/governance/run_records/holds/",
                      "damage k2/k2-lake/warehouse/k2/kt/bronze/", "damage k2/k2-landing/fx/ecb/"]


def test_the_drill_refuses_when_something_lands_between_its_check_and_the_drain(tmp_path):
    rec = Rec(answers={"check": DRILL_CHECK})
    st = drill_stack(tmp_path, rec)
    calls = {"n": 0}
    real = rec.__call__

    def second_is_stale(cmd, **kw):
        if "--fresh" in cmd:
            calls["n"] += 1
            if calls["n"] == 2:
                rec.answers["check"] = {**DRILL_CHECK, "stale": ["a run record after the run"]}
        return real(cmd, **kw)
    st.sh = second_is_stale
    with pytest.raises(r.Refused, match="between the check and the drain"):
        r.drill(st, "rel-1", say=lambda s: None)
    assert not any(c.startswith(("damage", "holds")) for c in rec.calls)
    assert [c for c in rec.calls if c.startswith("unpause")] == [f"unpause {d}" for d in DAGS]

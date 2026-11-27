"""docker/lake/connector.py and docker/lake/expire.py: the Spark connector's land, load, audit
and publish tasks and the expire task, their pure cores and their flows over fakes.

No Spark and no network: `Store` fakes the landing bucket's conditional PUT, `FakeLake` the few
Iceberg facts the flows read (refs, ancestry, a snapshot's batch, the platform tables) and
records every check's SQL. The shells over Spark and S3 are proven on a throwaway lake by
scripts/verify/connector-wap.sh (W1-12). Values are synthetic.
"""

import ast
import hashlib
import json
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import connector as C  # docker/lake is on sys.path (tests/conftest.py)
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _k2gen_model():
    """The names the connector repeats, executed from k2gen's own model.py, so the two cannot
    drift. The release carries no k2gen and this runner has no jsonschema, so only these
    top-level definitions are run, not the module."""
    want = {"LAKE", "LANDING_BUCKET", "LANDING_FILES", "AUDIT_CHECKS", "KAFKA_LANDING", "LINEAGE_COLUMN",
            "AUDIT_BRANCH", "SNAPSHOT_SUMMARY_KEYS", "fence", "lake_relation", "landing_prefix", "landing_key",
            "PlatformColumn", "PlatformTable", "_RUN", "PLATFORM_TABLES"}
    tree = ast.parse((ROOT / "platform/k2gen/k2gen/model.py").read_text())
    body = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in want)
            or (isinstance(n, ast.Assign) and {getattr(t, "id", None) for t in n.targets} & want)
            or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) in want)]
    ns = {"dataclass": dataclass, "Mapping": Mapping, "__name__": "k2gen_model_subset"}
    exec(compile(ast.Module(body, []), "model.py", "exec"), ns)  # noqa: S102 - the repository's own file
    return types.SimpleNamespace(**ns)


model = _k2gen_model()

GOLDEN = ROOT / "platform/k2gen/tests/golden/pipeline/compiled/products/sensor-net.station-readings/pipeline.json"
PRODUCT = "sensor-net.station-readings"
RELEASE = "v3.0.0-alpha.4-rc.1-0123456789ab"
RUN = "station_readings/scheduled__2026-11-27T15:15:00+00:00"
RAW = b"station_id,read_at,value_text\ns-1,2026-11-27T15:00:00Z,1.5\n"
SHA = hashlib.sha256(RAW).hexdigest()
ROWS = [{"station_id": "s-1", "read_at": "2026-11-27T15:00:00Z", "value_text": "1.5",
         "region": "north", "operator_note": None}]
TABLE = "lake.station_readings.bronze_readings"
LOG = "lake.station_readings.landing_files"
CHECKS = "lake.station_readings.audit_checks"


class Store:
    """The landing bucket: put_if_absent refuses an existing key, as If-None-Match: * does."""

    def __init__(self):
        self.objects, self.puts = {}, []

    def put_if_absent(self, key, data):
        self.puts.append(key)
        if key in self.objects:
            return False
        self.objects[key] = data
        return True

    def get(self, key):
        return self.objects[key]


class FakeLake:
    """main and audit as snapshot ids, each snapshot's batch, the rows per snapshot; the
    platform tables as lists; every check's SQL recorded with its arguments."""

    def __init__(self):
        self.next_id, self.history = 100, []  # history: main's ancestry
        self.refs_, self.batch, self.rows = {}, {}, {}
        self.tables = {LOG: [], CHECKS: []}
        self.sql, self.answers, self.calls = [], {}, []

    def _commit(self, parent_rows, new_rows, batch):
        self.next_id += 1
        self.rows[self.next_id] = parent_rows + new_rows
        self.batch[self.next_id] = batch
        return self.next_id

    def publish_main(self, rows, batch):  # a published batch, as a test's starting state
        sid = self._commit(self.rows.get(self.refs_.get("main"), []), rows, batch)
        self.refs_["main"] = self.refs_["audit"] = sid
        self.history.append(sid)

    def refs(self, table):
        return dict(self.refs_)

    def published(self, table, snapshot):
        return snapshot in self.history

    def batch_of(self, table, snapshot):
        return self.batch.get(snapshot)

    def has_object(self, log, key):
        return any(r["object_key"] == key for r in self.tables[log])

    def published_rows(self, table, sha):
        return sum(r[model.LINEAGE_COLUMN] == sha for r in self.rows.get(self.refs_.get("main"), []))

    def create_branch(self, table):
        self.calls.append(("create_branch", self.refs_.get("main")))
        self.refs_["audit"] = self.refs_.get("main")

    def reset_branch(self, table, snapshot):
        self.calls.append(("reset_branch", snapshot))
        self.refs_["audit"] = snapshot

    def append(self, table, records, props):
        self.tables[table].extend(records)

    def append_rows(self, table, lines, sha, props):
        rows = [{**json.loads(line), model.LINEAGE_COLUMN: sha} for line in lines]
        self.refs_["audit"] = self._commit(self.rows.get(self.refs_.get("audit"), []), rows, props["k2.batch-id"])

    def query(self, text, args):
        self.sql.append((text, args))
        return [(self.answers.get(text.split(" ", 2)[1], 0),)]

    def checks_of(self, checks, table, batch, snapshot):
        return [(r["check_name"], r["passed"], r["checked_at"]) for r in self.tables[checks]
                if r["table_name"] == table and r["batch_id"] == batch and r["snapshot_id"] == snapshot]

    def fast_forward(self, table):
        self.calls.append(("fast_forward", self.refs_["audit"]))
        self.refs_["main"] = self.refs_["audit"]
        self.history.append(self.refs_["main"])


@pytest.fixture
def pipeline():
    return json.loads(GOLDEN.read_text())


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """A run's spool entry as fetch.py leaves it after exit 0: raw, fetch.json, rows.jsonl."""
    monkeypatch.setattr(C, "SPOOL", tmp_path)

    def make(raw=RAW, rows=ROWS, run=RUN, sha=None):
        entry = tmp_path / run
        entry.mkdir(parents=True)
        (entry / "raw.csv").write_bytes(raw)
        record = {"url": "https://data.example.org/readings/2026-11-27.csv",
                  "finalUrl": "https://data.example.org/readings/2026-11-27.csv",
                  "sha256": sha or hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                  "fetchedAt": "2026-11-27T23:59:58.000001+00:00", "httpStatus": 200,
                  "address": "93.184.215.14"}
        (entry / "fetch.json").write_text(json.dumps(record))
        if rows is not None:
            (entry / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return entry
    return make


def ctx(pipeline, run=RUN):
    return C.Run(release=RELEASE, product=PRODUCT, run_id=run, pipeline=pipeline)


# --- W1-9: land ---------------------------------------------------------------------------


def test_the_landing_key_is_k2gens(pipeline, spool):
    spool()
    record = json.loads((C.SPOOL / RUN / "fetch.json").read_text())
    key = C.object_key(pipeline, record, SHA)
    assert f"s3://{C.LANDING_BUCKET}/{key}" == model.landing_key("sensor-net", "station-readings", "2026-11-27", SHA, "csv")
    assert (C.LANDING_BUCKET, C.LINEAGE, C.AUDIT, C.SUMMARY_KEYS) == (
        model.LANDING_BUCKET, model.LINEAGE_COLUMN, model.AUDIT_BRANCH, model.SNAPSHOT_SUMMARY_KEYS)
    assert tuple(C.LANDING_COLUMNS) == tuple(c.name for c in model.PLATFORM_TABLES[model.LANDING_FILES].columns)
    assert tuple(C.CHECK_COLUMNS) == tuple(c.name for c in model.PLATFORM_TABLES[model.AUDIT_CHECKS].columns)


def test_the_landing_day_is_the_fetch_days_in_utc(pipeline):
    record = {"fetchedAt": "2026-11-28T09:30:00+10:00"}
    assert C.object_key(pipeline, record, SHA).startswith("sensor-net/station-readings/2026-11-27/")


def test_a_sha_mismatch_is_refused_and_nothing_is_put(pipeline, spool):
    spool(sha="0" * 64)
    store, lake = Store(), FakeLake()
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_REFUSED
    assert store.puts == [] and lake.tables[LOG] == []


def test_a_size_mismatch_is_refused(pipeline, spool):
    entry = spool()
    record = json.loads((entry / "fetch.json").read_text())
    with pytest.raises(C.Refused, match="bytes"):
        C.verify({**record, "bytes": 1}, RAW)


def test_a_landing_is_one_conditional_put_and_one_landing_files_row(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    key = C.object_key(pipeline, json.loads((C.SPOOL / RUN / "fetch.json").read_text()), SHA)
    assert store.objects == {key: RAW}
    [row] = lake.tables[LOG]
    assert list(row) == list(C.LANDING_COLUMNS)
    assert (row["object_key"], row["sha256"], row["bytes"], row["parsed"], row["run_id"], row["release_id"]) == (
        f"s3://k2-landing/{key}", SHA, len(RAW), True, RUN, RELEASE)
    assert row["batch_id"] == RUN and row["http_status"] == 200 and row["fetched_at"].tzinfo is not None


def test_the_same_bytes_under_the_same_key_land_once(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK  # a retry of the task
    assert len(store.objects) == 1 and len(lake.tables[LOG]) == 1


def test_different_bytes_under_a_landed_key_are_refused():
    store = Store()
    store.objects["k"] = b"tampered"
    with pytest.raises(C.Refused, match="different bytes"):
        C.land(store, "k", RAW)
    assert store.objects["k"] == b"tampered"


def test_a_parse_failed_fetch_still_lands(pipeline, spool):
    spool(rows=None)  # fetch exit 30: no rows.jsonl
    store, lake = Store(), FakeLake()
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert len(store.objects) == 1 and lake.tables[LOG][0]["parsed"] is False


def test_a_relation_is_validated_and_quoted():
    assert C.rel("lake.station_readings.bronze_readings") == "`lake`.`station_readings`.`bronze_readings`"
    for bad in ("lake.x.y`; DROP TABLE t; --", "lake.x", "lake.x.y.z", "lake.X.y", "lake.x.current_user()",
                "lake.x.y z"):
        with pytest.raises(ValueError):
            C.rel(bad)


def test_arguments_are_plain_names(pipeline, spool):
    for argv in (["load", "--release", "../x", "--product", PRODUCT, "--run-id", RUN],
                 ["load", "--release", RELEASE, "--product", "../x", "--run-id", RUN],
                 ["load", "--release", RELEASE, "--product", PRODUCT, "--run-id", "station_readings/../x"],
                 ["load", "--release", RELEASE, "--product", PRODUCT, "--run-id", "other_dag/r1"]):
        with pytest.raises(SystemExit):
            C.parse_args(argv, lambda product: pipeline)

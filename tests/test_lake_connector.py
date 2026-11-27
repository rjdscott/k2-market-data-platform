"""docker/lake/connector.py and docker/lake/expire.py: the Spark connector's land, load, audit
and publish tasks and the expire task, their pure cores and their flows over fakes.

No Spark and no network beyond loopback: `Store` fakes the landing bucket's conditional PUT,
`FakeLake` the few Iceberg facts the flows read (refs, ancestry, a snapshot's batch, the platform
tables) and records every check's SQL, and the `catalog` fixture serves Lakekeeper's REST API on a
loopback port, checking each commit's requirements against the fake's refs. The shells over Spark,
S3 and the catalog run on a throwaway lake in scripts/verify/run-path.sh. Values are synthetic.
"""

import ast
import copy
import hashlib
import http.server
import importlib.util
import json
import os
import re
import threading
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import connector as C  # docker/lake is on sys.path (tests/conftest.py)
import expire as E
import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fetch", ROOT / "docker" / "fetch" / "fetch.py")
FETCH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FETCH)  # its audit(), so the two sides of audits.json agree


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
        self.next_id = 100
        self.refs_, self.batch, self.rows, self.parent = {}, {}, {}, {}
        self.tables = {LOG: [], CHECKS: []}
        self.sql, self.answers, self.calls = [], {}, []

    def _commit(self, parent_rows, new_rows, batch, parent=None):
        self.next_id += 1
        self.rows[self.next_id] = parent_rows + new_rows
        self.batch[self.next_id], self.parent[self.next_id] = batch, parent
        return self.next_id

    def publish_main(self, rows, batch):  # a published batch, as a test's starting state
        main = self.refs_.get("main")
        self.refs_["main"] = self.refs_["audit"] = self._commit(self.rows.get(main, []), rows, batch, main)

    def refs(self, table):
        return dict(self.refs_)

    def published(self, table, snapshot):
        s = self.refs_.get("main")
        while s is not None and s != snapshot:
            s = self.parent[s]
        return s is not None

    def parents(self, table):
        return {s: (self.parent[s], self.batch[s]) for s in self.batch}

    def catalog(self, table):
        return C.Catalog(self.catalog_uri, "k2")

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
        audit = self.refs_.get("audit")
        self.refs_["audit"] = self._commit(self.rows.get(audit, []), rows, props["k2.batch-id"], audit)

    def columns(self, table):
        return [*ROWS[0], model.LINEAGE_COLUMN]

    def count(self, check, text, args):
        """A check's problem count: as set in `answers`; else row_count counts the batch's rows at
        the snapshot the SQL names, and every other check finds nothing."""
        self.sql.append((check, text, args))
        if check in self.answers:
            return self.answers[check]
        if check == "row_count":
            snapshot = int(re.search(r"VERSION AS OF (\d+) ", text)[1])
            return sum(r[model.LINEAGE_COLUMN] == args["sha"] for r in self.rows[snapshot])
        return 0

    def checks_of(self, checks, table, batch, snapshot):
        return [(r["check_name"], r["passed"], r["checked_at"]) for r in self.tables[checks]
                if r["table_name"] == table and r["batch_id"] == batch and r["snapshot_id"] == snapshot]


PREFIX = "0d6a1c2e-5b7f-4c1a-9e3d-2f8b6a4c0e11"  # a warehouse id, as Lakekeeper's config gives it
TABLE_PATH = f"/catalog/v1/{PREFIX}/namespaces/station_readings/tables/bronze_readings"


@pytest.fixture
def catalog(monkeypatch):
    """Lakekeeper's REST API on a loopback port: the warehouse's config, a table's metadata (its
    refs, from `state.lake`) and the table commit, which applies its set-snapshot-ref updates only
    if every assert-ref-snapshot-id holds (a null one: no such ref), else 409, as the Iceberg REST
    spec says. `before_commit` moves a ref between the task's read and the commit; `force` makes
    the catalog answer 409 with that message."""
    for var in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(var, raising=False)
    state = types.SimpleNamespace(lake=None, requests=[], before_commit=None, force=None)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, doc):
            body = json.dumps(doc).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def metadata(self):
            return {"metadata": {"refs": {r: {"snapshot-id": s, "type": "branch"}
                                          for r, s in state.lake.refs_.items() if s is not None}}}

        def do_GET(self):
            state.requests.append(("GET", self.path, None))
            if self.path == "/catalog/v1/config?warehouse=k2":
                return self.send(200, {"defaults": {}, "overrides": {"prefix": PREFIX}})
            return self.send(200, self.metadata()) if self.path == TABLE_PATH else self.send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(("POST", self.path, body))
            if self.path != TABLE_PATH:
                return self.send(404, {})
            if state.before_commit:
                state.before_commit()
            refs = state.lake.refs_
            failed = [r for r in body["requirements"]
                      if r["type"] != "assert-ref-snapshot-id" or refs.get(r["ref"]) != r["snapshot-id"]]
            if state.force or failed:
                return self.send(409, {"error": {"message": state.force or f"Requirement failed: {failed}",
                                                 "type": "CommitFailedException", "code": 409}})
            for u in body["updates"]:
                assert (u["action"], u["type"]) == ("set-snapshot-ref", "branch")
                refs[u["ref-name"]] = u["snapshot-id"]
            self.send(200, self.metadata())

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.uri = f"http://127.0.0.1:{server.server_address[1]}/catalog"
    yield state
    server.shutdown()
    server.server_close()


def commits(catalog):
    """`(path, body)` of every commit the catalog was sent."""
    return [(path, body) for method, path, body in catalog.requests if method == "POST"]


@pytest.fixture
def pipeline():
    return json.loads(GOLDEN.read_text())


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """A run's spool entry as fetch.py leaves it after exit 0: raw, fetch.json, rows.jsonl."""
    monkeypatch.setattr(C, "SPOOL", tmp_path)

    def make(raw=RAW, rows=ROWS, run=RUN, sha=None, audits=None):
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
        if audits is not None:
            (entry / "audits.json").write_text(json.dumps({"audits": audits}))
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


def test_arguments_are_plain_names_and_a_bad_one_exits_1_not_refused(pipeline, spool):
    for argv in (["load", "--release", "../x", "--product", PRODUCT, "--run-id", RUN],
                 ["load", "--release", RELEASE, "--product", "../x", "--run-id", RUN],
                 ["load", "--release", RELEASE, "--product", PRODUCT, "--run-id", "station_readings/../x"],
                 ["load", "--release", RELEASE, "--product", PRODUCT, "--run-id", "other_dag/r1"],
                 ["load", "--release", RELEASE, "--product", PRODUCT],  # argparse's own: a missing argument
                 ["land", "--release", RELEASE, "--product", PRODUCT, "--run-id", RUN],
                 ["audit", "--release", RELEASE, "--product", PRODUCT, "--run-id", RUN, "--accept-data-loss"]):
        with pytest.raises(SystemExit) as e:
            C.parse_args(argv, lambda product: pipeline)
        assert e.value.code == C.EXIT_ERROR, argv  # 2 is refused, which the DAG does not retry (MED 28)


# --- W1-10: load onto `audit`, and the batch's audits ----------------------------------------

OTHER = "station_readings/scheduled__2026-11-27T14:15:00+00:00"


def staged(lake):
    """The rows on `audit` that `main` does not hold."""
    return lake.rows.get(lake.refs_.get("audit"), [])[len(lake.rows.get(lake.refs_.get("main"), [])):]


def test_load_stages_the_rows_on_audit_tagged_with_the_batch(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    code, result = C.load(ctx(pipeline), store, lake)
    assert code == C.EXIT_OK and result["rows"] == 1
    assert ("create_branch", None) in lake.calls and lake.refs_.get("main") is None
    head = lake.refs_["audit"]
    assert lake.batch[head] == RUN and staged(lake) == [{**ROWS[0], model.LINEAGE_COLUMN: SHA}]


def test_a_retry_of_load_stages_nothing_twice(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    head = lake.refs_["audit"]
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert lake.refs_["audit"] == head and len(staged(lake)) == 1


def test_an_unpublished_batch_on_audit_blocks_the_next_but_not_its_landing(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    lake.publish_main([], "station_readings/first")
    lake.refs_["audit"] = lake._commit([], [{"station_id": "s-9"}], OTHER)  # failed its audit, kept
    code, _ = C.load(ctx(pipeline), store, lake)
    assert code == C.EXIT_REFUSED
    assert len(store.objects) == 1 and len(lake.tables[LOG]) == 1  # landed and logged all the same
    assert lake.batch[lake.refs_["audit"]] == OTHER  # nothing appended behind the failed batch


def test_the_same_bytes_already_published_load_no_row(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    lake.publish_main([{**ROWS[0], model.LINEAGE_COLUMN: SHA}], OTHER)
    head = lake.refs_["audit"]
    code, result = C.load(ctx(pipeline), store, lake)
    assert code == C.EXIT_OK and result["rows"] == 0 and lake.refs_["audit"] == head


def test_an_audit_branch_behind_main_is_reset_to_main_before_staging(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    lake.publish_main([], OTHER)
    behind = lake.refs_["audit"]
    lake.publish_main([], "maintenance")  # main moved on (compaction), audit left behind
    lake.refs_["audit"] = behind
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert ("reset_branch", lake.refs_["main"]) in lake.calls and len(staged(lake)) == 1


def test_a_parse_failed_batch_lands_loads_nothing_and_fails_its_audit(pipeline, spool):
    spool(rows=None)
    store, lake = Store(), FakeLake()
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert lake.refs_.get("audit") is None and len(store.objects) == 1
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    [row] = lake.tables[CHECKS]
    assert (row["check_name"], row["passed"], row["batch_id"], row["table_name"]) == ("parses", False, RUN, TABLE)
    assert list(row) == list(C.CHECK_COLUMNS) and lake.sql == []


def test_a_batch_that_passes_records_every_check_at_the_audit_head(pipeline, spool):
    spool()
    pipeline["bronze"][0]["audits"] = []  # parser audits are refused below
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    rows = lake.tables[CHECKS]
    assert [r["check_name"] for r in rows] == C.check_names(pipeline["bronze"][0])
    assert {r["snapshot_id"] for r in rows} == {lake.refs_["audit"]} and all(r["passed"] for r in rows)


def test_a_row_count_short_of_the_parsers_fails_the_audit(pipeline, spool):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    lake.answers["row_count"] = 0
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    [failed] = [r for r in lake.tables[CHECKS] if not r["passed"]]
    assert failed["check_name"] == "row_count" and "parser wrote 1" in failed["detail"]


def test_a_row_whose_columns_are_not_the_tables_fails_parses(pipeline, spool):
    spool(rows=[{**ROWS[0], "extra": 1}])
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    assert lake.tables[CHECKS][0]["check_name"] == "parses" and not lake.tables[CHECKS][0]["passed"]


def audited(pipeline, spool, audits):
    spool(audits=audits)
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    code, _ = C.audit(ctx(pipeline), store, lake)
    return code, {r["check_name"]: r for r in lake.tables[CHECKS]}


def test_the_golden_products_parser_audit_passes_when_fetch_ran_it_clean(pipeline, spool):
    module = types.SimpleNamespace(value_parses=lambda rows: [f"row {i}" for i, r in enumerate(rows)
                                                               if not r["value_text"].replace(".", "").isdigit()])
    entry = FETCH.audit(module, "value_parses", "".join(json.dumps(r) + "\n" for r in ROWS))
    code, rows = audited(pipeline, spool, [entry])
    assert code == C.EXIT_OK and rows["value_parses"]["passed"]
    assert list(rows) == C.check_names(pipeline["bronze"][0])


@pytest.mark.parametrize("audits", [None, []])  # no audits.json; one without the declared audit
def test_a_parser_audit_with_no_result_fails_closed(pipeline, spool, audits):
    code, rows = audited(pipeline, spool, audits)
    assert code == C.EXIT_FAILED and not rows["value_parses"]["passed"]
    assert "no result" in rows["value_parses"]["detail"]


def test_a_failed_parser_audit_fails_the_batch_with_its_detail(pipeline, spool):
    code, rows = audited(pipeline, spool, [{"name": "value_parses", "passed": False, "failures": 2,
                                            "detail": "row 0: x; row 3: y"}])
    assert code == C.EXIT_FAILED and not rows["value_parses"]["passed"]
    assert rows["value_parses"]["detail"] == "2 failing rows: row 0: x; row 3: y"


@pytest.mark.parametrize("entry", [
    {"name": "value_parses", "passed": True, "failures": 1, "detail": ""},  # passed, yet a failure
    {"name": "value_parses", "passed": "yes", "failures": 0, "detail": ""},
    {"name": "value_parses", "passed": True, "failures": "0", "detail": ""},
])
def test_a_malformed_parser_audit_entry_fails_closed(pipeline, spool, entry):
    code, rows = audited(pipeline, spool, [entry])
    assert code == C.EXIT_FAILED and not rows["value_parses"]["passed"]


def test_an_undeclared_parser_audit_is_ignored_and_logged(pipeline, spool, capsys):
    clean = {"name": "value_parses", "passed": True, "failures": 0, "detail": ""}
    code, rows = audited(pipeline, spool, [clean, {**clean, "name": "extra_check", "passed": False}])
    assert code == C.EXIT_OK and "extra_check" not in rows
    assert "extra_check" in capsys.readouterr().out


# --- A spool entry is this run's own files, never a link (audit 2026-11-27, HIGH 6) -----------
# fetch mounts only its DAG's spool directory, but Spark mounts the whole volume: a parser that
# leaves a link in its own entry must not make this product land and stage another's files.

OTHER = "other_dag/scheduled__2026-11-27T15:15:00+00:00"
SECRET_ROWS = [{**ROWS[0], "station_id": "another-products-row"}]


def other_products_entry(spool):
    """Another DAG's run, spooled whole: the target a planted link points at."""
    return spool(run=OTHER, rows=SECRET_ROWS)


def refused_load(pipeline, capsys):
    store, lake = Store(), FakeLake()
    code, _ = C.load(ctx(pipeline), store, lake)
    err = capsys.readouterr().err
    assert code == C.EXIT_REFUSED and store.puts == [] and lake.tables[LOG] == [] and lake.rows == {}
    assert "another-products-row" not in err  # names the entry, never what its target holds
    return err


def test_a_symlinked_raw_file_is_refused_and_nothing_lands(pipeline, spool, capsys):
    other = other_products_entry(spool)
    entry = spool()
    (entry / "raw.csv").unlink()
    (entry / "raw.csv").symlink_to(Path("../..") / OTHER / "raw.csv")
    assert (entry / "raw.csv").read_bytes() == (other / "raw.csv").read_bytes()  # the link does resolve
    assert "raw.csv" in refused_load(pipeline, capsys)


@pytest.mark.parametrize("level", ["run", "dag"])
def test_a_symlinked_run_or_dag_directory_is_refused(pipeline, spool, capsys, tmp_path, level):
    other_products_entry(spool)
    dag, run = RUN.split("/")
    if level == "run":
        (tmp_path / dag).mkdir()
        (tmp_path / dag / run).symlink_to(Path("..") / OTHER)
    else:
        (tmp_path / dag).symlink_to(OTHER.split("/")[0])
    assert (C.SPOOL / RUN / "rows.jsonl").exists()  # it resolves to the other product's entry
    refused_load(pipeline, capsys)


def test_a_hard_linked_rows_file_is_refused(pipeline, spool, capsys):
    other = other_products_entry(spool)
    entry = spool()
    (entry / "rows.jsonl").unlink()
    (entry / "rows.jsonl").hardlink_to(other / "rows.jsonl")
    assert "rows.jsonl" in refused_load(pipeline, capsys)


def test_a_fifo_is_refused_without_blocking(pipeline, spool, capsys):
    entry = spool()
    (entry / "rows.jsonl").unlink()
    os.mkfifo(entry / "rows.jsonl")  # opened blocking, a read would wait for a writer forever
    assert "rows.jsonl" in refused_load(pipeline, capsys)


def test_a_symlinked_audits_file_is_refused_at_audit(pipeline, spool):
    other = spool(run=OTHER, audits=[{"name": "value_parses", "passed": True, "failures": 0, "detail": ""}])
    entry = spool()
    (entry / "audits.json").symlink_to(Path("../..") / OTHER / "audits.json")
    assert (other / "audits.json").exists()
    store, lake = Store(), FakeLake()
    assert C.load(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    with pytest.raises(C.Refused, match="audits.json"):  # main() exits 2 on Refused
        C.audit(ctx(pipeline), store, lake)
    assert lake.tables[CHECKS] == []


def test_fetchs_own_files_have_one_link_each(tmp_path):
    FETCH.write_new(tmp_path / "rows.jsonl", "{}\n")
    assert (tmp_path / "rows.jsonl").stat().st_nlink == 1 and [p.name for p in tmp_path.iterdir()] == ["rows.jsonl"]


def test_the_connector_imports_no_product_code():
    text = (ROOT / "docker/lake/connector.py").read_text()
    assert "importlib" not in text and "domains/" not in text and "exec_module" not in text


def test_each_declared_check_renders_batch_scoped_sql(pipeline):
    bronze = pipeline["bronze"][0]
    src = ("`lake`.`station_readings`.`bronze_readings` VERSION AS OF 7 "
           "WHERE `source_file_sha256` = :sha")
    got = {n: (text, args, want) for n, text, args, want in C.check_sql(bronze, 7, SHA, 3)}
    assert got == {
        "row_count": (f"SELECT count(*) FROM {src}", {"sha": SHA}, 3),
        "keys_unique": (f"SELECT count(*) FROM (SELECT 1 FROM {src} "
                        "GROUP BY `station_id`, `read_at`, `source_file_sha256` HAVING count(*) > 1)", {"sha": SHA}, 0),
        "in_domain:station_id": (f"SELECT count(*) FROM {src} AND `station_id` IS NOT NULL "
                                 "AND NOT (CAST(`station_id` AS STRING) RLIKE :pattern)",
                                 {"sha": SHA, "pattern": "^(?:^s-[0-9]+$)$"}, 0),
        "values_unchanged": (
            "SELECT count(*) FROM `lake`.`station_readings`.`bronze_readings` VERSION AS OF 7 AS b "
            "JOIN `lake`.`station_readings`.`bronze_readings` VERSION AS OF 7 AS o "
            "ON b.`station_id` = o.`station_id` AND b.`read_at` = o.`read_at` "
            "WHERE b.`source_file_sha256` = :sha AND o.`source_file_sha256` <> :sha "
            "AND NOT (b.`value_text` <=> o.`value_text` AND b.`region` <=> o.`region` "
            "AND b.`operator_note` <=> o.`operator_note`)", {"sha": SHA}, 0),
    }
    assert all(":sha" in text and "VERSION AS OF 7" in text for text, _, _ in got.values())


@pytest.mark.parametrize("field,value", [
    ("key", ["station_id`) OR 1=1 --"]),
    ("values", ["current_user()"]),
    ("patterns", {"x` = 1 OR `y": "^a$"}),
    ("table", "lake.station_readings.bronze_readings; DROP TABLE x"),
])
def test_an_identifier_injection_in_the_pipeline_is_refused(pipeline, field, value):
    bronze = copy.deepcopy(pipeline["bronze"][0])
    bronze[field] = value
    with pytest.raises(ValueError):
        C.check_sql(bronze, 7, SHA, 1)


def test_a_snapshot_must_be_an_integer(pipeline):
    for bad in ("7 OR 1=1", True, None):
        with pytest.raises(ValueError):
            C.check_sql(pipeline["bronze"][0], bad, SHA, 1)


def test_a_check_value_is_bound_never_spliced(pipeline):
    bronze = copy.deepcopy(pipeline["bronze"][0])
    bronze["patterns"] = {"station_id": "x') OR ('1'='1"}
    [(_, text, args, _)] = [c for c in C.check_sql(bronze, 7, SHA, 1) if c[0].startswith("in_domain")]
    assert "OR ('1'" not in text and args["pattern"] == "^(?:x') OR ('1'='1)$"


# --- W1-11, W1-4: publish by one catalog commit requiring both refs, and expire ----------------


def passed_batch(pipeline, spool, catalog):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    catalog.lake, lake.catalog_uri = lake, catalog.uri
    lake.publish_main([], OTHER)
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    return store, lake


def test_publish_is_one_commit_requiring_main_and_audit_as_read(pipeline, spool, catalog):
    store, lake = passed_batch(pipeline, spool, catalog)
    main, head = lake.refs_["main"], lake.refs_["audit"]
    assert lake.parent[head] == main
    code, result = C.publish(ctx(pipeline), store, lake)
    assert code == C.EXIT_OK and lake.refs_["main"] == head
    assert commits(catalog) == [(TABLE_PATH, {
        "identifier": {"namespace": ["station_readings"], "name": "bronze_readings"},
        "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": main},
                         {"type": "assert-ref-snapshot-id", "ref": "audit", "snapshot-id": head}],
        "updates": [{"action": "set-snapshot-ref", "ref-name": "main", "type": "branch", "snapshot-id": head}]})]
    assert (result["snapshotBefore"], result["snapshotAfter"], result["table"]) == (main, head, TABLE)
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK  # a retry: nothing left to move
    assert len(commits(catalog)) == 1


def test_the_first_publish_of_a_table_requires_it_to_have_no_main(pipeline, spool, catalog):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    catalog.lake, lake.catalog_uri = lake, catalog.uri
    base = lake._commit([], [], None)  # the branch's own snapshot on a table with no main
    lake.refs_["audit"] = lake._commit([], [{**ROWS[0], model.LINEAGE_COLUMN: SHA}], RUN, base)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK and lake.refs_["main"] == lake.refs_["audit"]
    [(_, body)] = commits(catalog)
    assert body["requirements"][0] == {"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": None}


def test_publish_refuses_when_main_moved_since_staging(pipeline, spool, catalog, capsys):
    store, lake = passed_batch(pipeline, spool, catalog)
    head = lake.refs_["audit"]
    lake.refs_["main"] = moved = lake._commit([], [], "maintenance", lake.refs_["main"])  # a compaction on main
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_REFUSED
    assert commits(catalog) == [] and lake.refs_ == {"main": moved, "audit": head}
    assert "main moved since staging" in capsys.readouterr().err


def test_publish_refuses_an_audit_head_that_does_not_descend_from_main(pipeline, spool, catalog, capsys):
    store, lake = passed_batch(pipeline, spool, catalog)
    main = lake.refs_["main"]
    elsewhere = lake._commit([], [], "elsewhere")  # another lineage, which main never held
    lake.refs_["audit"] = lake._commit([], [{**ROWS[0], model.LINEAGE_COLUMN: SHA}], RUN, elsewhere)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK  # the batch's own checks pass at the new head
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_REFUSED
    assert commits(catalog) == [] and lake.refs_["main"] == main
    assert "no fast-forward" in capsys.readouterr().err


@pytest.mark.parametrize("ref", ["main", "audit"])
def test_a_ref_moved_between_the_read_and_the_commit_publishes_nothing_and_is_named(pipeline, spool, catalog,
                                                                                      capsys, ref):
    store, lake = passed_batch(pipeline, spool, catalog)
    main, head = lake.refs_["main"], lake.refs_["audit"]
    later = lake._commit([], [], "a concurrent writer", lake.refs_[ref])
    catalog.before_commit = lambda: lake.refs_.update({ref: later})
    code, result = C.publish(ctx(pipeline), store, lake)
    assert code == C.EXIT_REFUSED and result["snapshotAfter"] == main and len(commits(catalog)) == 1
    assert lake.refs_ == {"main": main, "audit": head, ref: later}  # the commit changed nothing
    err = capsys.readouterr().err
    assert "HTTP 409" in err and f"{ref} (read {main if ref == 'main' else head}, now {later})" in err
    assert ("audit (read" in err) == (ref == "audit") and ("main (read" in err) == (ref == "main")


def test_a_409_with_neither_ref_moved_is_a_clear_failure_a_retry_decides(pipeline, spool, catalog, capsys):
    store, lake = passed_batch(pipeline, spool, catalog)
    main = lake.refs_["main"]
    catalog.force = "Requirement failed: a concurrent commit"
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_ERROR and lake.refs_["main"] == main
    err = capsys.readouterr().err
    assert "HTTP 409" in err and "a concurrent commit" in err and "read back unmoved" in err and "nothing published" in err
    catalog.force = None
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK  # the retry the DAG makes of exit 1


def test_publish_refuses_a_batch_with_a_failed_check(pipeline, spool, catalog):
    store, lake = passed_batch(pipeline, spool, catalog)
    main = lake.refs_["main"]
    lake.tables[CHECKS][2]["passed"] = False
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    assert lake.refs_["main"] == main and commits(catalog) == []


def test_publish_refuses_a_branch_that_moved_after_its_audit(pipeline, spool, catalog):
    store, lake = passed_batch(pipeline, spool, catalog)
    head = lake.refs_["audit"]
    lake.refs_["audit"] = lake._commit(staged(lake), [{"station_id": "s-2"}], RUN, head)  # written after the audit
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    assert commits(catalog) == []


def test_publish_refuses_a_parse_failed_batch(pipeline, spool):
    spool(rows=None)
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    C.audit(ctx(pipeline), store, lake)
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED


def test_publish_of_bytes_already_published_moves_nothing(pipeline, spool, catalog):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    catalog.lake, lake.catalog_uri = lake, catalog.uri
    lake.publish_main([{**ROWS[0], model.LINEAGE_COLUMN: SHA}], OTHER)
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert catalog.requests == []


def test_the_newest_audit_of_the_snapshot_decides():
    names = ["parses", "row_count"]
    old, new = "2026-11-27T10:00:00", "2026-11-27T11:00:00"
    assert C.unaudited([("parses", False, old), ("parses", True, new), ("row_count", True, new)], names) is None
    assert "row_count" in C.unaudited([("parses", True, old), ("row_count", True, old),
                                       ("parses", True, new), ("row_count", False, new)], names)
    assert "lacks" in C.unaudited([("parses", True, new)], names)
    assert "no audit" in C.unaudited([], names)


def test_a_string_literal_is_one_token():
    assert C.string_literal("a'b\\c") == "'a\\'b\\\\c'"
    with pytest.raises(ValueError):
        C.string_literal("a\nb")


OUT = "sensor_net_build.station_readings__base.silver_readings"


def test_expire_renders_by_id_with_older_than_the_epoch():
    assert E.statement(OUT, [11, 12]) == (
        "CALL `sensor_net_build`.`system`.`expire_snapshots`("
        "table => 'station_readings__base.silver_readings', snapshot_ids => array(11L, 12L), "
        "older_than => TIMESTAMP '1970-01-01 00:00:00')")


def test_expire_takes_only_the_products_outputs_and_integer_ids(pipeline):
    assert E.plan(pipeline, [{"table": OUT, "ids": [11, 12]}]) == [(OUT, [11, 12])]
    for bad in ([{"table": "other_build.x__base.y", "ids": [1]}],
                [{"table": TABLE, "ids": [1]}],
                [{"table": OUT, "ids": ["1); DROP TABLE x; --"]}],
                [{"table": OUT, "ids": [True]}],
                [{"table": OUT, "ids": []}],
                [{"table": OUT, "ids": [1, 1]}]):
        with pytest.raises(ValueError):
            E.plan(pipeline, bad)


def test_expire_fails_unless_the_deleted_manifest_lists_cover_the_ids(pipeline):
    answers = []

    def spark_sql(text):
        answers.append(text)
        return {"deleted_manifest_lists_count": 1, "deleted_data_files_count": 0}

    code, result = E.expire(pipeline, [{"table": OUT, "ids": [11, 12]}], spark_sql)
    assert code == E.EXIT_ERROR and result[0]["deletedManifestLists"] == 1 and len(answers) == 1
    assert E.expire(pipeline, [{"table": OUT, "ids": [11]}], spark_sql)[0] == E.EXIT_OK
    assert E.expire(pipeline, [], spark_sql) == (E.EXIT_OK, [])


def test_expire_runs_with_the_maintenance_key():
    assert E.KEY_ENV == ("K2_MAINTENANCE_S3_ACCESS_KEY", "K2_MAINTENANCE_S3_SECRET_KEY")
    policy = json.loads((ROOT / "docker/lake/minio/policies/maintenance.json").read_text())
    actions = {a for s in policy["Statement"] for a in s["Action"]}
    assert "s3:DeleteObject" in actions and "s3:PutObject" not in actions

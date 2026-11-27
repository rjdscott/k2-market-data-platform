"""docker/lake/connector.py and docker/lake/expire.py: the Spark connector's land, load, audit
and publish tasks and the expire task, their pure cores and their flows over fakes.

No Spark and no network: `Store` fakes the landing bucket's conditional PUT, `FakeLake` the few
Iceberg facts the flows read (refs, ancestry, a snapshot's batch, the platform tables) and
records every check's SQL. The shells over Spark and S3 are proven on a throwaway lake by
scripts/verify/connector-wap.sh (W1-12). Values are synthetic.
"""

import ast
import copy
import hashlib
import json
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import connector as C  # docker/lake is on sys.path (tests/conftest.py)
import expire as E
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


def test_a_parser_audit_the_spark_task_cannot_run_fails_closed(pipeline, spool):
    spool()
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    [failed] = [r for r in lake.tables[CHECKS] if not r["passed"]]
    assert failed["check_name"] == "value_parses"


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


# --- W1-11: publish by fast-forward, and expire ----------------------------------------------


def passed_batch(pipeline, spool):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    lake.publish_main([], OTHER)
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    return store, lake


def test_publish_fast_forwards_main_when_every_check_passed(pipeline, spool):
    store, lake = passed_batch(pipeline, spool)
    head = lake.refs_["audit"]
    code, result = C.publish(ctx(pipeline), store, lake)
    assert code == C.EXIT_OK and lake.calls[-1] == ("fast_forward", head) and lake.refs_["main"] == head
    assert (result["snapshotAfter"], result["table"]) == (head, TABLE)
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK  # a retry: nothing left to move
    assert [c for c in lake.calls if c[0] == "fast_forward"] == [("fast_forward", head)]


def test_publish_refuses_a_batch_with_a_failed_check(pipeline, spool):
    store, lake = passed_batch(pipeline, spool)
    main = lake.refs_["main"]
    lake.tables[CHECKS][2]["passed"] = False
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    assert lake.refs_["main"] == main and not any(c[0] == "fast_forward" for c in lake.calls)


def test_publish_refuses_a_branch_that_moved_after_its_audit(pipeline, spool):
    store, lake = passed_batch(pipeline, spool)
    lake.refs_["audit"] = lake._commit(staged(lake), [{"station_id": "s-2"}], RUN)  # written after the audit
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED
    assert not any(c[0] == "fast_forward" for c in lake.calls)


def test_publish_refuses_a_parse_failed_batch(pipeline, spool):
    spool(rows=None)
    store, lake = Store(), FakeLake()
    C.load(ctx(pipeline), store, lake)
    C.audit(ctx(pipeline), store, lake)
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_FAILED


def test_publish_of_bytes_already_published_moves_nothing(pipeline, spool):
    spool()
    pipeline["bronze"][0]["audits"] = []
    store, lake = Store(), FakeLake()
    lake.publish_main([{**ROWS[0], model.LINEAGE_COLUMN: SHA}], OTHER)
    C.load(ctx(pipeline), store, lake)
    assert C.audit(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert C.publish(ctx(pipeline), store, lake)[0] == C.EXIT_OK
    assert not any(c[0] == "fast_forward" for c in lake.calls)


def test_the_newest_audit_of_the_snapshot_decides():
    names = ["parses", "row_count"]
    old, new = "2026-11-27T10:00:00", "2026-11-27T11:00:00"
    assert C.unaudited([("parses", False, old), ("parses", True, new), ("row_count", True, new)], names) is None
    assert "row_count" in C.unaudited([("parses", True, old), ("row_count", True, old),
                                       ("parses", True, new), ("row_count", False, new)], names)
    assert "lacks" in C.unaudited([("parses", True, new)], names)
    assert "no audit" in C.unaudited([], names)


def test_the_fast_forward_names_only_validated_identifiers():
    assert C.fast_forward_sql(TABLE) == (
        "CALL `lake`.`system`.`fast_forward`(table => 'station_readings.bronze_readings', "
        "branch => 'main', to => 'audit')")
    with pytest.raises(ValueError):
        C.fast_forward_sql("lake.x.y') --")


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

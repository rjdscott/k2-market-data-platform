"""docker/lake/maintain.py: the snapshot plan and the landing expiry, over fakes (no Spark, no
network). The shells over Spark and S3 are proven on a throwaway lake later. Values are synthetic."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import maintain as M
import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "platform/k2gen/tests/golden/pipeline/compiled/products/sensor-net.station-readings/pipeline.json"
PIPELINE = json.loads(GOLDEN.read_text())
AT = datetime(2026, 11, 27, 3, 0, tzinfo=timezone.utc)
SHA_OLD, SHA_FAILED, SHA_NEW, SHA_UNLOGGED = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
PREFIX = "sensor-net/station-readings/"
BRONZE = "lake.station_readings.bronze_readings"
RETENTION = {
    "k2.station_readings.bronze_readings": 30,
    "k2.station_readings.audit_checks": 7,
    "k2.station_readings.landing_files": 7,
    "sensor_net.station_readings__base.silver_readings": 90,
}


def key(day, sha, ext="csv"):
    return f"{PREFIX}{day}/{sha}.{ext}"


class FakeStore:
    def __init__(self, keys, deny=False):
        self.keys, self.deleted, self.deny = list(keys), [], deny

    def list(self, prefix):
        return iter([k for k in self.keys if k.startswith(prefix)])

    def delete(self, k):
        if self.deny:
            raise M.Denied(f"AccessDenied; {M.GRANT}")
        self.deleted.append(k)


class AnalysisException(Exception):
    """pyspark.errors.AnalysisException as Spark 3.5 raises it, its error class by getErrorClass()."""

    def __init__(self, error_class, message):
        super().__init__(f"[{error_class}] {message}")
        self.error_class = error_class

    def getErrorClass(self):  # noqa: N802 - pyspark's name
        return self.error_class


def not_found(table):
    parts = table.split(".") + ["refs"]
    return AnalysisException("TABLE_OR_VIEW_NOT_FOUND", f"The table or view {'.'.join(f'`{p}`' for p in parts)} cannot "
                             f"be found. To tolerate the error on drop use DROP VIEW IF EXISTS or DROP TABLE IF EXISTS.;\n"
                             f"'UnresolvedRelation [{', '.join(parts)}], [], false")


class FakeLake:
    def __init__(self, published=(SHA_OLD, SHA_NEW), logged=(SHA_OLD, SHA_FAILED, SHA_NEW), drop_ref=None, fail=None,
                 empty=()):
        self.published, self.logged, self.drop_ref = set(published), set(logged), drop_ref
        self.fail, self.empty = fail or {}, set(empty)  # fail: table -> the exception its read raises
        self.sql, self.expired = [], False

    def refs(self, table):
        if table in self.fail:
            raise self.fail[table]
        if table in self.empty:
            return {}
        refs = {"main": 1, "audit": 2, "audited": 3}
        if self.expired and self.drop_ref:
            del refs[self.drop_ref]
        return refs

    def snapshot_count(self, table):
        return 0 if table in self.empty else 2 if self.expired else 5

    def run(self, text):
        self.sql.append(text)
        self.expired = True

    def has_object(self, log, uri):
        return any(uri.endswith(f"/{s}.csv") for s in self.logged)

    def published_rows(self, table, sha):
        return 3 if sha in self.published else 0


def test_plan_passes_each_tables_days_from_retention_json():
    plan = {r: (o, n) for r, o, n in M.snapshot_plan(PIPELINE, RETENTION, AT)}
    assert plan[BRONZE] == ("2026-10-28 03:00:00", 1)
    assert plan["lake.station_readings.audit_checks"] == ("2026-11-20 03:00:00", 1)
    assert plan["sensor_net_build.station_readings__base.silver_readings"][0] == "2026-08-29 03:00:00"
    assert len(plan) == 4  # bronze, checks, log, the one build output; nothing of another product


def test_expiry_statement_and_counts():
    lake = FakeLake()
    code, expired, unbuilt = M.expire_snapshots(PIPELINE, RETENTION, lake, AT)
    assert code == 0 and expired[BRONZE] == 3 and unbuilt == []
    assert ("CALL `lake`.`system`.`expire_snapshots`(table => 'station_readings.bronze_readings', "
            "older_than => TIMESTAMP '2026-10-28 03:00:00', retain_last => 1)") in lake.sql


@pytest.mark.parametrize("ref", ["audit", "audited"])
def test_a_ref_missing_after_expiry_fails(ref, capsys):
    code, _, _ = M.expire_snapshots(PIPELINE, RETENTION, FakeLake(drop_ref=ref), AT)
    assert code == 1 and ref in capsys.readouterr().err


OUTPUT = "sensor_net_build.station_readings__base.silver_readings"


def test_a_build_output_not_built_yet_is_skipped_and_named(capsys):
    """The live failure of 2026-11-27: before a product's first good build its outputs do not
    exist, and reading their refs raised TABLE_OR_VIEW_NOT_FOUND. Nothing to expire: skipped."""
    lake = FakeLake(fail={OUTPUT: not_found(OUTPUT)})
    code, expired, unbuilt = M.expire_snapshots(PIPELINE, RETENTION, lake, AT)
    assert (code, unbuilt) == (0, [OUTPUT]) and OUTPUT not in expired and len(expired) == 3
    assert f"maintain: {OUTPUT}: not built yet" in capsys.readouterr().out
    assert not [s for s in lake.sql if "silver_readings" in s]  # no CALL on a table that is not there


@pytest.mark.parametrize("table, error", [
    (OUTPUT, AnalysisException("INSUFFICIENT_PERMISSIONS", "no read on the table")),  # there, not readable
    (OUTPUT, RuntimeError("org.apache.iceberg.exceptions.ForbiddenException: Forbidden")),
    (BRONZE, not_found(BRONZE)),  # made at deploy, never by a build: gone is a broken deploy, not "not built"
])
def test_a_table_that_cannot_be_read_or_should_exist_is_still_an_error(table, error):
    with pytest.raises(type(error)):
        M.expire_snapshots(PIPELINE, RETENTION, FakeLake(fail={table: error}), AT)


def test_a_table_with_no_snapshot_yet_has_no_ref_to_lose():
    code, expired, _ = M.expire_snapshots(PIPELINE, RETENTION, FakeLake(empty={BRONZE}), AT)
    assert code == 0 and expired[BRONZE] == 0


def test_a_table_without_retention_is_refused():
    with pytest.raises(ValueError, match="retention.json"):
        M.snapshot_plan(PIPELINE, {k: v for k, v in RETENTION.items() if "bronze" not in k}, AT)


def test_landing_window_and_published_rule(capsys):
    old, edge, ok13 = key("2026-11-12", SHA_OLD), key("2026-11-13", SHA_OLD, "xml"), key("2026-11-14", SHA_NEW)
    store = FakeStore([old, edge, ok13, key("2026-11-01", SHA_FAILED), key("2026-11-01", SHA_UNLOGGED),
                       f"{PREFIX}2026-11-01/not-a-sha.csv", f"{PREFIX}2026-13-45/{SHA_OLD}.csv", f"{PREFIX}stray.csv"])
    code, deleted, kept = M.expire_landings(PIPELINE, store, FakeLake(logged=(SHA_OLD, SHA_FAILED, SHA_NEW)), AT)
    # 15 days old (11-12) goes; exactly 14 days (11-13) and 13 days (11-14) stay
    assert store.deleted == [old]
    assert (code, deleted) == (0, 1)
    assert kept == {"recent": 2, "unpublished": 1, "unlogged": 1, "malformed": 3}
    assert "kept until an operator clears it" in capsys.readouterr().out


def test_an_old_landing_of_an_unpublished_batch_is_kept():
    store = FakeStore([key("2026-10-01", SHA_FAILED)])
    _, deleted, kept = M.expire_landings(PIPELINE, store, FakeLake(), AT)
    assert store.deleted == [] and deleted == 0 and kept == {"unpublished": 1}


def test_only_the_products_own_prefix_is_deleted():
    store = FakeStore([f"other/product/2026-01-01/{SHA_OLD}.csv", key("2026-01-01", SHA_OLD)])
    M.expire_landings(PIPELINE, store, FakeLake(), AT)
    assert store.deleted == [key("2026-01-01", SHA_OLD)]
    assert M.parse_key(PREFIX, f"other/x/2026-01-01/{SHA_OLD}.csv") is None


@pytest.mark.parametrize("prefix", ["", "sensor-net/", "sensor-net/other/", "../", "sensor-net/station-readings/../x/", "sensor-net/station-readings"])
def test_a_prefix_escape_is_refused(prefix):
    bad = copy.deepcopy(PIPELINE)
    bad["landing"]["prefix"] = prefix
    store = FakeStore([key("2026-01-01", SHA_OLD)])
    with pytest.raises(ValueError, match="is not k2-landing"):
        M.expire_landings(bad, store, FakeLake(), AT)
    assert store.deleted == []


def test_access_denied_on_delete_is_exit_1_naming_the_grant(capsys):
    code, deleted, _ = M.expire_landings(PIPELINE, FakeStore([key("2026-01-01", SHA_OLD)], deny=True), FakeLake(), AT)
    err = capsys.readouterr().err
    assert code == 1 and deleted == 0
    assert "s3:DeleteObject on arn:aws:s3:::k2-landing/*" in err and "s3:ListBucket" in err


# --- A Kafka product: resume points kept, continuity, compaction, orphans ---------------------

import re  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from datetime import timedelta  # noqa: E402

import kafka as K  # noqa: E402
from test_lake_kafka import PIPELINE as KAFKA  # noqa: E402 - the frozen Kafka pipeline (I-1)
from test_lake_kafka import TOPIC  # noqa: E402

import offsets as O  # noqa: E402

LANDING, KBRONZE, KCHECKS = KAFKA["landing"]["table"], KAFKA["bronze"][0]["table"], KAFKA["bronze"][0]["checks"]
KRETENTION = {M.retention_key(r): 7 for r in (LANDING, KBRONZE, KCHECKS)}
DAY = timedelta(days=1)
COMPACTION = {O.JOB: M.JOB_COMPACTION}
EXPIRE = re.compile(r"CALL `(\w+)`\.`system`\.`expire_snapshots`\(table => '([\w.]+)', "
                    r"older_than => TIMESTAMP '([\d :-]+)', retain_last => (\d+)\)")


def connector(ends, batch="b"):
    return {O.JOB: K.JOB_CONNECTOR, O.KAFKA_OFFSETS: O.encode({TOPIC: ends}), "k2.batch-id": batch}


class Iceberg:
    """Tables as Iceberg keeps them: snapshots `{id: (parent, committed_at, summary)}` and refs.
    run() applies expire_snapshots as Iceberg 1.8.1's RemoveSnapshots does: each branch keeps its
    head and walks its ancestors while fewer than retain_last are kept or they are newer than
    older_than; an unreferenced snapshot stays only if newer. W0-4c proves the real one."""

    def __init__(self):
        self.snaps, self.heads, self.sql, self.compacted = {}, {}, [], []
        self.files, self.rows = {}, []

    def commit(self, table, ts, summary, ref="main"):
        snaps, heads = self.snaps.setdefault(table, {}), self.heads.setdefault(table, {})
        sid = max(snaps, default=0) + 1
        snaps[sid] = (heads.get(ref, heads.get("main")), ts, summary)
        heads[ref] = sid
        return sid

    def _walk(self, table, sid):
        while sid in self.snaps[table]:
            yield sid
            sid = self.snaps[table][sid][0]

    def refs(self, table):
        return dict(self.heads.get(table, {}))

    def snapshot_count(self, table):
        return len(self.snaps.get(table, {}))

    def lineage(self, table):
        return [self.snaps[table][s][2] for s in self._walk(table, self.heads[table].get("main"))]

    def history(self, table):
        return [(self.snaps[table][s][1], self.snaps[table][s][2]) for s in self._walk(table, self.heads[table]["main"])]

    def summaries(self, table):
        return [(ts, s) for _, ts, s in self.snaps[table].values()]

    def published(self, table, snapshot):
        return snapshot in set(self._walk(table, self.heads[table].get("main")))

    def batch_of(self, table, snapshot):
        return self.snaps[table][snapshot][2].get("k2.batch-id")

    def offset_counts(self, table):
        return self.rows

    def file_counts(self, table):
        return self.files.get(table, (0, 0, 0))

    def compact(self, table, props, rewrite_deletes):
        self.compacted.append((table, rewrite_deletes))
        self.commit(table, AT, dict(props))
        return {"rewrittenDataFilesCount": 60, "addedDataFilesCount": 1}

    def remove_orphans(self, table, text):
        self.sql.append(text)
        return 0

    def run(self, text):
        self.sql.append(text)
        catalog, table, older, n = EXPIRE.fullmatch(text).groups()
        table, n = f"{catalog}.{table}", int(n)
        older = datetime.strptime(older, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        snaps, keep, reachable = self.snaps[table], set(), set()
        for head in self.heads[table].values():
            for i, sid in enumerate(self._walk(table, head)):
                reachable.add(sid)
                if i == 0 or i < n or snaps[sid][1] >= older:
                    keep.add(sid)
                else:
                    break
            reachable |= set(self._walk(table, head))
        keep |= {s for s, (_, ts, _) in snaps.items() if s not in reachable and ts >= older}
        self.snaps[table] = {s: v for s, v in snaps.items() if s in keep}


def resumable():
    """Landing and bronze each: two connector snapshots, then two compaction snapshots, all older
    than the 7 days of retention; checks one snapshot."""
    lake = Iceberg()
    for table in (LANDING, KBRONZE):
        lake.commit(table, AT - 30 * DAY, connector({0: 10}))
        lake.commit(table, AT - 20 * DAY, connector({0: 20}))
        lake.commit(table, AT - 15 * DAY, COMPACTION)
        lake.commit(table, AT - 10 * DAY, COMPACTION)
    lake.commit(KCHECKS, AT - 20 * DAY, {})
    lake.rows = [(0, 20, 20, 0, 19)]
    return lake


def resume_point(lake, table):
    return K.plan(lake.history(table), {TOPIC: {0: 0}}, {TOPIC: {0: 50}}, 100).starts


def test_newest_connector_snapshot_survives_expiry_behind_two_compaction_snapshots():
    lake = resumable()
    code, expired, _ = M.expire_snapshots(KAFKA, KRETENTION, lake, AT)
    assert code == 0 and expired == {KBRONZE: 1, KCHECKS: 0, LANDING: 1}  # only the older connector snapshot
    for table in (LANDING, KBRONZE):
        assert K.newest(lake.history(table))[O.KAFKA_OFFSETS] == O.encode({TOPIC: {0: 20}})
        assert resume_point(lake, table) == {TOPIC: {0: 20}}
    assert any(f"'{LANDING.partition('.')[2]}', older_than => TIMESTAMP '2026-11-20 03:00:00', retain_last => 3)" in s
               for s in lake.sql)


def test_retain_last_1_would_lose_the_resume_point():
    """The rule the test above holds, broken: the fake expires what Iceberg would, and the next
    run would land again from the log start."""
    lake = resumable()
    lake.run(M.statement(LANDING, "2026-11-20 03:00:00", 1))
    assert K.newest(lake.history(LANDING)) is None and resume_point(lake, LANDING) == {TOPIC: {0: 0}}


@pytest.mark.parametrize("lineage, depth", [
    ([], 1), ([COMPACTION], 1), ([connector({0: 1})], 1),
    ([COMPACTION, COMPACTION, connector({0: 1}), connector({0: 0})], 3),
    ([{O.JOB: K.JOB_CONNECTOR}, connector({0: 1})], 2),  # a connector commit without offsets is no resume point
])
def test_connector_depth(lineage, depth):
    assert M.connector_depth(lineage) == depth


class NoStore:
    def __getattr__(self, attr):
        raise AssertionError(f"a Kafka landing's rows are never expired: store.{attr} called")


def test_a_kafka_landing_is_never_expired_and_maintenance_never_compacts():
    lake = resumable()
    code, result = M.maintain(KAFKA, KRETENTION, lake, NoStore(), AT)
    assert code == 0 and "landingsDeleted" not in result and lake.compacted == []
    assert all("expire_snapshots" in s or "remove_orphan_files" in s for s in lake.sql)
    assert M.tables(KAFKA) == [KBRONZE, KCHECKS, LANDING]  # the landing table, never a landing log
    assert result["maintaining"]["product"] == KAFKA["product"]


def gap(partition, committed, log_start):
    return {**connector({partition: log_start + 5}), K.KAFKA_GAPS: json.dumps({TOPIC: {str(partition): [committed, log_start]}})}


@pytest.mark.parametrize("rows, gaps, passed, says", [
    ([(0, 20, 20, 0, 19), (1, 5, 5, 3, 7)], [], True, "25 rows over 2 partitions, each unbroken"),
    ([(0, 19, 19, 0, 19)], [], False, "0..19 span 20 records but 19 rows are present (missing)"),
    ([(0, 21, 20, 0, 19)], [], False, "1 rows repeat an offset"),
    ([(0, 15, 15, 0, 19)], [gap(0, 5, 10)], True, "but for 5 offsets of accepted losses"),  # 5..9 lost, accepted
    ([(0, 14, 14, 0, 19)], [gap(0, 5, 10)], False, "missing"),  # one more than the accepted loss
    ([(0, 15, 15, 0, 19)], [gap(1, 5, 10)], False, "missing"),  # accepted on another partition
])
def test_landing_continuity(rows, gaps, passed, says, capsys):
    lake = resumable()
    lake.rows = rows
    for g in gaps:
        lake.commit(LANDING, AT - 25 * DAY, g)
    code, result = M.maintain(KAFKA, KRETENTION, lake, NoStore(), AT)
    [check] = result["checks"]
    assert (check["check"], check["table"], check["passed"]) == ("offset_continuity", LANDING, passed)
    assert says in check["detail"] and code == (0 if passed else M.EXIT_FAILED)
    if not passed:
        assert "FAILED offset_continuity" in capsys.readouterr().err


def pending(lake):
    """A batch staged on bronze's audit branch and not published: a waiting or failed batch."""
    lake.heads[KBRONZE]["audit"] = lake.heads[KBRONZE]["main"]
    lake.commit(KBRONZE, AT - DAY, connector({0: 30}, batch="kafka_fixture/scheduled__waiting"), ref="audit")


def test_audit_ahead_of_main_compaction_skipped_reason_recorded():
    lake = resumable()
    lake.files = {LANDING: (100, 1, 0), KBRONZE: (100, 1, 0)}
    pending(lake)
    code, result = M.compact(KAFKA, lake, AT)
    assert code == 0 and lake.compacted == [] and result["compacted"] == {}
    assert set(result["skipped"]) == {LANDING, KBRONZE}
    assert "holds batch kafka_fixture/scheduled__waiting, unpublished" in result["skipped"][KBRONZE]


def test_audit_level_with_main_compacts_and_the_resume_walk_passes_the_compaction():
    lake = resumable()
    lake.files = {LANDING: (100, 1, 0), KBRONZE: (100, 1, 2)}
    lake.heads[KBRONZE]["audit"] = lake.heads[KBRONZE]["main"]  # published: audit is main
    code, result = M.compact(KAFKA, lake, AT)
    assert code == 0 and lake.compacted == [(LANDING, False), (KBRONZE, True)] and result["skipped"] == {}
    for table in (LANDING, KBRONZE):
        newest = lake.snaps[table][lake.heads[table]["main"]][2]
        assert newest[O.JOB] != K.JOB_CONNECTOR and O.KAFKA_OFFSETS not in newest
        assert resume_point(lake, table) == {TOPIC: {0: 20}}
    assert result["maintaining"]["product"] == KAFKA["product"]


@pytest.mark.parametrize("files, partitions, deletes, compacted", [
    (M.COMPACT_MIN_FILES, 1, 0, False),  # one under the threshold beyond one file per partition
    (M.COMPACT_MIN_FILES + 1, 1, 0, True),
    (3, 3, 0, False),  # one file per partition: nothing to bin
    (3, 3, 1, True),  # a delete file: its rewrite is due whatever the count
])
def test_compaction_threshold(files, partitions, deletes, compacted):
    lake = resumable()
    lake.files = {LANDING: (files, partitions, deletes)}
    _, result = M.compact(KAFKA, lake, AT)
    assert (LANDING in result["compacted"]) is compacted and (LANDING in result["skipped"]) is not compacted
    if not compacted:
        assert f"under {M.COMPACT_MIN_FILES}" in result["skipped"][LANDING]


def test_orphans_are_removed_only_past_the_24_hour_floor_and_never_for_an_unbuilt_output():
    assert M.orphan_statement(LANDING, AT) == (
        "CALL `lake`.`system`.`remove_orphan_files`(table => 'sample_trades.landing', "
        "older_than => TIMESTAMP '2026-11-26 03:00:00', file_list_view => 'k2_orphan_listing')")
    lake = FakeLake(fail={OUTPUT: not_found(OUTPUT)})
    lake.removed = []
    lake.remove_orphans = lambda table, text: lake.removed.append(table) or 0
    code, result = M.maintain(PIPELINE, RETENTION, lake, FakeStore([]), AT)
    assert code == 0 and OUTPUT not in lake.removed and len(lake.removed) == 3
    assert result["orphansRemoved"] == dict.fromkeys(lake.removed, 0) and result["landingsDeleted"] == 0


@pytest.mark.parametrize("compact, key", [(False, "K2_MAINTENANCE_S3_ACCESS_KEY"), (True, "K2_SPARK_S3_ACCESS_KEY")])
def test_each_pass_signs_with_its_own_key(compact, key, tmp_path, monkeypatch, capsys):
    root = tmp_path / "r1" / "compiled"
    (root / "products" / KAFKA["product"]).mkdir(parents=True)
    (root / "lake").mkdir()
    (root / "products" / KAFKA["product"] / "pipeline.json").write_text(json.dumps(KAFKA))
    (root / "lake" / "retention.json").write_text(json.dumps(KRETENTION))
    seen = {}
    spark = types.SimpleNamespace(stop=lambda: None)
    monkeypatch.setitem(sys.modules, "spark_conf", types.SimpleNamespace(
        lake_session=lambda app, key, catalogs: seen.update(key=key, catalogs=catalogs) or spark))
    monkeypatch.setattr(M, "RELEASES", tmp_path)
    monkeypatch.setattr(M, "MaintainLake", lambda s: resumable())
    code = M.main(["--release", "r1", "--product", KAFKA["product"], "--run-id", f"{KAFKA['dagId']}/manual__1",
                   *(["--compact"] if compact else [])])
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and seen == {"key": M.PASS_KEYS["compact" if compact else "maintain"], "catalogs": ["lake"]}
    assert seen["key"][0] == key and result["maintaining"]["product"] == KAFKA["product"]
    assert ("compacted" in result) is compact and ("expired" in result) is not compact


def test_compact_is_refused_for_a_file_product(tmp_path, monkeypatch):
    root = tmp_path / "r1" / "compiled"
    (root / "products" / PIPELINE["product"]).mkdir(parents=True)
    (root / "products" / PIPELINE["product"] / "pipeline.json").write_text(json.dumps(PIPELINE))
    monkeypatch.setattr(M, "RELEASES", tmp_path)
    with pytest.raises(SystemExit):
        M.main(["--release", "r1", "--product", PIPELINE["product"], "--run-id", f"{PIPELINE['dagId']}/manual__1",
                "--compact"])

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

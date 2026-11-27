"""k2runs against a fake connection: no network, no Trino."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import k2runs  # noqa: E402

BASE = dict(run_id="d/r1", product_id="governance.access-canary", release_id="v1.0.0-0123456789ab",
            task="build", event="started")


class Cur:
    def __init__(self, rows=(), fail=0):
        self.calls, self.rows, self.fail = [], list(rows), fail

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.fail:
            self.fail -= 1
            raise ConnectionError("secret-pw refused")

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur


def test_record_binds_every_column_in_order():
    cur = Cur()
    when = datetime(2026, 11, 27, 10, 0, tzinfo=timezone(timedelta(hours=11)))
    k2runs.record(Conn(cur), **BASE, started_at=when, inputs=[{"a": 1}], detail="x" * 2000)
    (sql, params), = cur.calls
    assert sql.count("?") == len(k2runs.COLUMNS) == len(params) and "d/r1" not in sql
    p = dict(zip(k2runs.COLUMNS, params))
    assert p["run_id"] == "d/r1" and p["table_name"] is None and p["snapshot_before"] is None
    assert p["started_at"].utcoffset() == timedelta(0) and p["started_at"].hour == 23
    assert p["recorded_at"].utcoffset() == timedelta(0)
    assert p["inputs"] == '[{"a":1}]' and len(p["detail"]) == 1000


@pytest.mark.parametrize("bad", [dict(event="done"), dict(task="nope"), dict(run_id=""), dict(extra=1)])
def test_record_refuses_before_any_call(bad):
    cur = Cur()
    with pytest.raises(k2runs.RunRecordError):
        k2runs.record(Conn(cur), **(BASE | bad))
    assert cur.calls == []


def test_naive_timestamp_refused():
    with pytest.raises(k2runs.RunRecordError):
        k2runs.record(Conn(Cur()), **BASE, started_at=datetime(2026, 1, 1))


def test_one_retry_then_error_without_secret():
    ok = Cur(fail=1)
    k2runs.record(Conn(ok), **BASE)
    assert len(ok.calls) == 2
    bad = Cur(fail=5)
    with pytest.raises(k2runs.RunRecordError) as e:
        k2runs.record(Conn(bad), **BASE)
    assert len(bad.calls) == 2 and "secret-pw" not in str(e.value) and "secret-pw" not in repr(e.value.__cause__)


def test_password_read_at_call_time_and_never_leaked(tmp_path, monkeypatch):
    import trino
    pw = tmp_path / "pw"
    seen = []
    monkeypatch.setattr(trino.dbapi, "connect", lambda **kw: seen.append(kw["auth"]._password) or "conn")
    monkeypatch.setenv("K2_TRINO_HOST", "h")
    monkeypatch.setenv("K2_TRINO_INTERNAL_PORT", "8443")
    monkeypatch.setenv("K2_RUNS_USER", "svc.runs")
    pw.write_text("first\n")
    k2runs.connect(password_file=str(pw), ca="ca.pem")
    pw.write_text("second\n")
    k2runs.connect(password_file=str(pw), ca="ca.pem")
    assert seen == ["first", "second"]
    with pytest.raises(FileNotFoundError) as e:
        k2runs.connect(password_file=str(tmp_path / "none"))
    assert "second" not in str(e.value)


def test_newest_audited_found_and_not_found():
    cur = Cur(rows=[(42,)])
    assert k2runs.newest_audited(Conn(cur), "g_build.x.t") == 42
    assert cur.calls[0][1] == ["g_build.x.t"] and "?" in cur.calls[0][0]
    assert k2runs.newest_audited(Conn(Cur()), "g_build.x.t") is None


def test_offset_gap_round_trips_and_an_unknown_event_is_still_refused():
    cur = Cur()
    gap = {"partitions": [{"partition": 3, "fromOffset": 10, "toOffset": 19}], "reason": "retention"}
    k2runs.record(Conn(cur), **(BASE | dict(task="load", event="offset_gap")), detail=json.dumps(gap))
    p = dict(zip(k2runs.COLUMNS, cur.calls[0][1]))
    assert p["event"] == "offset_gap" and json.loads(p["detail"]) == gap
    with pytest.raises(k2runs.RunRecordError):
        k2runs.record(Conn(Cur()), **(BASE | dict(event="gap")))


T0 = datetime(2026, 11, 28, 1, 0, tzinfo=timezone.utc)
A, B = "market-capture.kraken-trades.silver_trade", "market-capture.kraken-instruments.silver_listing"


def inp(cid, state="active", snapshot=5):
    return {"contractId": cid, "relation": "lake.x.y", "snapshot": snapshot, "state": state, "lagMinutes": 1}


def build(run="d/r2", minutes=0, inputs=None, detail=None, event="passed"):
    return dict(run_id=run, task="build", event=event, recorded_at=T0 + timedelta(minutes=minutes),
                inputs=None if inputs is None else json.dumps(inputs), detail=detail)


def test_state_no_records_and_all_inputs_good_is_complete():
    assert k2runs.state([]) == "complete"
    assert k2runs.state([build(inputs=[inp(A), inp(B)])]) == "complete"


def test_state_partial_names_paused_and_never_audited_inputs_sorted():
    assert k2runs.state([build(inputs=[inp(B, "paused"), inp(A, snapshot=None), inp("a.b.c")])]) == f"partial: {B}, {A}"


def test_state_degraded_from_the_detail_and_only_for_the_newest_run():
    bad = '{"degraded":true,"warned":["unresolved rows = 0"]}'
    assert k2runs.state([build(detail=bad)]) == "degraded"
    older = build("d/r1", -60, [inp(A, "paused")], detail=bad)
    assert k2runs.state([older, build(inputs=[inp(A)])]) == "complete"
    assert k2runs.state([build(detail="plain message"), build(detail='{"degraded":false}')]) == "complete"


def test_state_precedence_degraded_over_partial_and_other_tasks_ignored():
    rows = [build(inputs=[inp(A, "paused")], detail='{"degraded":true}'),
            dict(run_id="d/r3", task="audit", event="passed", recorded_at=T0 + timedelta(hours=1), inputs=None, detail=None)]
    assert k2runs.state(rows) == "degraded"


def test_state_reads_inputs_given_as_a_list():
    row = build() | {"inputs": [inp(A, "paused")]}
    assert k2runs.state([row]) == f"partial: {A}"


def test_offset_gaps_newest_first_and_bad_detail_skipped():
    def d(r):
        return json.dumps({"partitions": [{"partition": 0, "fromOffset": 1, "toOffset": 2}], "reason": r})

    rows = [dict(event="offset_gap", recorded_at=T0, detail=d("old")), dict(event="offset_gap", recorded_at=T0 + timedelta(hours=1), detail=d("new")),
            dict(event="offset_gap", recorded_at=T0, detail="not json"), dict(event="passed", recorded_at=T0, detail=d("x"))]
    assert [g["reason"] for g in k2runs.offset_gaps(rows)] == ["new", "old"]


P = "market-capture.kraken-trades"


def test_hold_binds_one_conditional_insert_and_refuses_bad_arguments_before_any_call():
    cur = Cur(rows=[(1,)])
    when = datetime(2026, 11, 28, 12, 0, tzinfo=timezone(timedelta(hours=11)))
    k2runs.hold(Conn(cur), P, "drill", "rob", at=when)
    (sql, params), = cur.calls
    assert sql.startswith(f"INSERT INTO {k2runs.HOLDS} ") and sql.count("?") == len(params) and P not in sql
    assert params[:4] == [P, "operator", "drill", "rob"] and params[5:] == [P, "operator"]
    assert params[4].utcoffset() == timedelta(0) and params[4].hour == 1
    for bad in (dict(reason=""), dict(reason="  "), dict(by=""), dict(kind="lock"), dict(product="")):
        cur = Cur(rows=[(1,)])
        with pytest.raises(k2runs.HoldError):
            k2runs.hold(Conn(cur), **(dict(product=P, reason="r", by="rob") | bad))
        assert cur.calls == []


def test_a_second_hold_of_one_kind_and_a_release_of_nothing_are_refused():
    with pytest.raises(k2runs.HoldError, match="held already"):
        k2runs.hold(Conn(Cur(rows=[(0,)])), P, "again", "rob", kind="rebuild")
    with pytest.raises(k2runs.HoldError, match="no open restore hold"):
        k2runs.release(Conn(Cur(rows=[(0,)])), P, "rob", kind="restore")


def test_release_appends_a_row_copied_from_the_open_hold_of_its_kind_only():
    cur = Cur(rows=[(1,)])
    k2runs.release(Conn(cur), P, "rob")
    (sql, params), = cur.calls
    # append-only: an INSERT of the hold's own columns, never an UPDATE or DELETE
    assert sql.startswith(f"INSERT INTO {k2runs.HOLDS} ") and "UPDATE" not in sql and "DELETE" not in sql
    assert "h.product_id, h.kind, h.reason, h.held_by, h.held_at" in sql
    assert params[1:] == ["rob", P, "operator"] and params[0].utcoffset() == timedelta(0)
    assert "h.kind = ?" in sql  # an operator's release never ends a restore's lock


def test_held_is_the_newest_open_hold_or_none():
    row = (P, "restore", "restore drill", "make restore", T0, None, None)
    cur = Cur(rows=[row])
    assert k2runs.held(Conn(cur), P) == dict(zip(k2runs.HOLD_COLUMNS, row))
    (sql, params), = cur.calls
    assert params == [P] and "ORDER BY h.held_at DESC LIMIT 1" in sql and "h.kind = ?" not in sql  # any kind holds it
    assert k2runs.held(Conn(Cur()), P) is None


def test_the_hold_columns_are_holds_sql_in_order():
    text = (Path(__file__).parent / "holds.sql").read_text()
    body = text[text.index("CREATE TABLE"):]
    assert [line.split()[0] for line in body.splitlines()[1:] if line.startswith("    ")] == list(k2runs.HOLD_COLUMNS)
    assert f"CREATE TABLE {k2runs.HOLDS} (" in text

"""k2runs against a fake connection: no network, no Trino."""
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

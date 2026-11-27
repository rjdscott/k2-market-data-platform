"""db.run, the one Trino client, against a stand-in for trino.dbapi.connect: the login is svc.api's
password, the session user is the one asked for, values are bound, errors are classified."""

import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests
import trino

from k2api import db

PASSWORD = "made-up-password-of-svc-api"
run = db.run  # the real one: conftest's autouse fixture replaces db.run with the fake


class Cursor:
    def __init__(self, conn: "Conn") -> None:
        self.conn = conn

    def execute(self, sql: str, params: list[Any]) -> None:
        self.conn.executed.append((sql, params))
        if self.conn.slow:  # the real client blocks here until a first row, the end or a cancel
            self.conn.cancel_seen.wait(5)
            raise trino.exceptions.TrinoUserError({"errorName": "USER_CANCELED", "errorType": "USER_ERROR"}, "q1")
        if self.conn.error:
            raise self.conn.error

    def fetchmany(self, size: int) -> list[list[Any]]:
        if self.conn.fetch_error:
            raise self.conn.fetch_error
        if not self.conn.batches:
            self.conn.finished = True
        return self.conn.batches.pop(0) if self.conn.batches else []

    def cancel(self) -> None:
        if self.conn.cancel_error:
            raise self.conn.cancel_error
        if not self.conn.finished:  # as the client: a finished query has no next URI to DELETE
            self.conn.cancelled = True
            self.conn.cancel_seen.set()


class Conn:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs, self.executed, self.closed, self.cancelled = kwargs, [], False, False
        self.batches: list[list[list[Any]]] = [[[1]], [[2]]]
        self.error: Exception | None = None
        self.fetch_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.slow, self.finished, self.cancel_seen = False, False, threading.Event()

    def cursor(self) -> Cursor:
        return Cursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Conn:
    (tmp_path / "api.password").write_text(PASSWORD + "\n")
    monkeypatch.setattr(db, "PASSWORD_FILE", str(tmp_path / "api.password"))
    made = Conn()

    def connect(**kwargs: Any) -> Conn:
        made.kwargs = kwargs
        return made
    monkeypatch.setattr(trino.dbapi, "connect", connect)
    return made


def test_a_query_logs_in_as_svc_api_and_runs_as_the_user_asked(conn: Conn) -> None:
    assert run("p.analyst-full", 'SELECT "a" FROM "x"."y"."z" WHERE "a" = ? LIMIT 2', ["it's"]) == [[1], [2]]
    auth = conn.kwargs.pop("auth")
    assert isinstance(auth, trino.auth.BasicAuthentication)
    assert (auth._username, auth._password) == ("svc.api", PASSWORD)
    assert conn.kwargs == {"host": "trino", "port": 8443, "user": "p.analyst-full", "http_scheme": "https",
                           "verify": "/run/k2-secrets/tls/ca.pem", "source": "k2-api", "timezone": "UTC",
                           "request_timeout": db.TIMEOUT_S, "legacy_prepared_statements": False}
    assert conn.executed == [('SELECT "a" FROM "x"."y"."z" WHERE "a" = ? LIMIT 2', ["it's"])]  # bound, as given
    assert conn.closed and not conn.cancelled


def test_the_trino_address_comes_from_the_environment(conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K2_TRINO_HOST", "trino.internal")
    monkeypatch.setenv("K2_TRINO_INTERNAL_PORT", "9443")
    run("svc.api", "SELECT 1", [])
    assert (conn.kwargs["host"], conn.kwargs["port"], conn.kwargs["user"]) == ("trino.internal", 9443, "svc.api")


def user_error(name: str) -> trino.exceptions.TrinoUserError:
    return trino.exceptions.TrinoUserError({"errorName": name, "errorType": "USER_ERROR", "message": f"{name} {PASSWORD}"}, "q1")


@pytest.mark.parametrize("error, raised", [
    (user_error("PERMISSION_DENIED"), db.Denied),
    (user_error("TABLE_NOT_FOUND"), db.Failed),
    (trino.exceptions.TrinoQueryError({"errorName": "GENERIC_INTERNAL_ERROR", "errorType": "INTERNAL_ERROR"}, "q1"), db.Unavailable),
    (trino.exceptions.HttpError(f"error 401: {PASSWORD}"), db.Unavailable),
    (trino.exceptions.TrinoConnectionError("failed"), db.Unavailable),
    (requests.exceptions.ConnectionError("refused"), db.Unavailable),
    (requests.exceptions.SSLError("certificate verify failed"), db.Unavailable),
    (ConnectionRefusedError(), db.Unavailable),
])
def test_errors_are_classified_and_carry_neither_password_nor_message(conn: Conn, error: Exception, raised: type) -> None:
    conn.error = error
    with pytest.raises(raised) as caught:
        run("p.analyst-full", "SELECT 1", [])
    assert PASSWORD not in str(caught.value) and caught.value.__cause__ is None and caught.value.__suppress_context__
    if raised is db.Failed:
        assert caught.value.name == "TABLE_NOT_FOUND"
    assert conn.closed


def test_a_query_with_no_row_by_its_deadline_is_cancelled_on_trino(conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
    # A filter on a non-key column of a large table: execute blocks with no batch to check a clock
    # between. The deadline cancels the query on Trino, which ends the wait.
    monkeypatch.setattr(db, "TIMEOUT_S", 0.2)
    conn.slow = True
    started = time.monotonic()
    with pytest.raises(db.Unavailable) as caught:
        run("p.analyst-full", "SELECT 1", [])
    assert str(caught.value) == "timeout" and conn.cancelled and conn.closed
    assert time.monotonic() - started < 2


def test_a_query_that_fails_midway_is_cancelled_not_left_running(conn: Conn) -> None:
    # Closing the connection alone does not cancel (the client only closes its HTTP session), so a
    # read timeout would leave the query running on Trino until its client timeout.
    conn.fetch_error = requests.exceptions.ReadTimeout("read timed out")
    with pytest.raises(db.Unavailable):
        run("p.analyst-full", "SELECT 1", [])
    assert conn.cancelled and conn.closed


def test_a_cancel_that_fails_does_not_hide_the_answer(conn: Conn) -> None:
    conn.error, conn.cancel_error = user_error("PERMISSION_DENIED"), trino.exceptions.TrinoConnectionError("gone")
    with pytest.raises(db.Denied):
        run("p.analyst-full", "SELECT 1", [])
    assert conn.closed

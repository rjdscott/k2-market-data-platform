"""The one Trino client: every statement k2-api sends goes through run().

k2-api has one Trino identity, svc.api, and one credential, its password file. A caller's query
logs in as svc.api and names the caller as the session user (`X-Trino-User`); Trino runs it as
that principal only if the release's rules.json lets svc.api impersonate it, then applies that
principal's rules, so Trino, not k2-api, decides what the caller sees (ADR-059, ADR-060).
k2-api's own reads, the tested snapshot id from a base's `t$refs`, run as svc.api itself.

Values are always bound: the client sends `EXECUTE IMMEDIATE '<statement>' USING <values>`, each
value a typed literal it escapes, so no value is ever part of a statement's text.

Every query ends on Trino by TIMEOUT_S. The client blocks in execute() until a first row and in
fetchmany() until the next, so a clock read between batches decides nothing for a query that
scans long before its first row; a timer cancels the query on Trino at the deadline instead, which
ends either wait. Any query that has not finished when run() returns, for whatever reason, is
cancelled too: closing the connection alone leaves it running until Trino's client timeout.
"""

import contextlib
import os
import threading
from typing import Any

import requests
import trino

API_USER = "svc.api"  # k2gen.emit.trino_rules API; model.REFS_READERS
PASSWORD_FILE = "/run/k2-secrets/api.password"  # k2gen.model.password_mount("svc.api")
CA_FILE = "/run/k2-secrets/tls/ca.pem"
TIMEOUT_S = 30  # the whole query, and each HTTP request of it
BATCH = 1000


class Denied(Exception):
    """Trino refused the user: PERMISSION_DENIED, an access rule or the impersonation rule."""


class Failed(Exception):
    """Trino answered with another query error; `name` is its errorName (TABLE_NOT_FOUND, ...)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class Unavailable(Exception):
    """No answer: Trino unreachable, the login refused, the TLS check failed, or the timeout."""


CLIENT_ERRORS = (trino.exceptions.Error, trino.exceptions.HttpError, requests.RequestException, OSError)


def cancel(cur: Any) -> None:
    """Cancel cur's query on Trino if it has not finished (the client sends nothing for one that
    has); a failure here is never the answer, so it is dropped."""
    with contextlib.suppress(*CLIENT_ERRORS):
        cur.cancel()


def run(user: str, sql: str, params: list[Any]) -> list[list[Any]]:
    """Every row of `sql` with `params` bound, run on Trino as `user`: svc.api, or a caller
    svc.api may become. No row is kept after it returns."""
    with open(PASSWORD_FILE) as f:
        password = f.read().strip()
    conn = trino.dbapi.connect(
        host=os.environ.get("K2_TRINO_HOST", "trino"), port=int(os.environ.get("K2_TRINO_INTERNAL_PORT", "8443")),
        user=user, auth=trino.auth.BasicAuthentication(API_USER, password), http_scheme="https", verify=CA_FILE,
        source="k2-api", timezone="UTC", request_timeout=TIMEOUT_S, legacy_prepared_statements=False)
    cur = conn.cursor()
    expired = threading.Event()
    # ponytail: one timer thread per query; Trino's query_max_execution_time as a session property
    # is the server-side bound, once rules.json lets svc.api and the p.* it becomes set it.
    timer = threading.Timer(TIMEOUT_S, lambda: (expired.set(), cancel(cur)))
    timer.start()
    try:
        cur.execute(sql, params)
        rows: list[list[Any]] = []
        while batch := cur.fetchmany(BATCH):
            rows += batch
        if expired.is_set():  # a cancelled query can end with no error and part of its rows
            raise Unavailable("timeout")
        return rows
    except trino.exceptions.TrinoUserError as e:
        if expired.is_set():
            raise Unavailable("timeout") from None
        if e.error_name == "PERMISSION_DENIED":
            raise Denied() from None
        raise Failed(e.error_name) from None
    except CLIENT_ERRORS as e:
        # the type alone: never a header or the password
        raise Unavailable("timeout" if expired.is_set() else type(e).__name__) from None
    finally:
        timer.cancel()
        cancel(cur)
        conn.close()

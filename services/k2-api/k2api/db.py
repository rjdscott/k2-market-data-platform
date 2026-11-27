"""The one Trino client: every statement k2-api sends goes through run().

k2-api has one Trino identity, svc.api, and one credential, its password file. A caller's query
logs in as svc.api and names the caller as the session user (`X-Trino-User`); Trino runs it as
that principal only if the release's rules.json lets svc.api impersonate it, then applies that
principal's rules, so Trino, not k2-api, decides what the caller sees (ADR-059, ADR-060).
k2-api's own reads, the tested snapshot id from a base's `t$refs`, run as svc.api itself.

Values are always bound: the client sends `EXECUTE IMMEDIATE '<statement>' USING <values>`, each
value a typed literal it escapes, so no value is ever part of a statement's text.
"""

import os
import time
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


def run(user: str, sql: str, params: list[Any]) -> list[list[Any]]:
    """Every row of `sql` with `params` bound, run on Trino as `user`: svc.api, or a caller
    svc.api may become. No row is kept after it returns."""
    with open(PASSWORD_FILE) as f:
        password = f.read().strip()
    conn = trino.dbapi.connect(
        host=os.environ.get("K2_TRINO_HOST", "trino"), port=int(os.environ.get("K2_TRINO_INTERNAL_PORT", "8443")),
        user=user, auth=trino.auth.BasicAuthentication(API_USER, password), http_scheme="https", verify=CA_FILE,
        source="k2-api", timezone="UTC", request_timeout=TIMEOUT_S, legacy_prepared_statements=False)
    deadline = time.monotonic() + TIMEOUT_S
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows: list[list[Any]] = []
        while batch := cur.fetchmany(BATCH):
            rows += batch
            if time.monotonic() > deadline:
                cur.cancel()
                raise Unavailable("timeout")
        return rows
    except trino.exceptions.TrinoUserError as e:
        if e.error_name == "PERMISSION_DENIED":
            raise Denied() from None
        raise Failed(e.error_name) from None
    except (trino.exceptions.Error, trino.exceptions.HttpError, requests.RequestException, OSError) as e:
        raise Unavailable(type(e).__name__) from None  # the type alone: never a header or the password
    finally:
        conn.close()

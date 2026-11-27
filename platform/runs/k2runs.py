"""The one run-records client (phase 001 interface A6, contracts/schema/run-record.schema.json).

record() appends one row to governance_build.run_records.run_record as svc.runs; newest_audited()
reads the newest `audited` row of a table. Dependency: trino only. The password is read from its
file at each connect() and never held, logged or put in an error message.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

TABLE = "governance_build.run_records.run_record"
TASKS = ("fetch", "load", "audit", "publish", "build", "expire", "record", "maintain")
EVENTS = ("started", "passed", "failed", "rolled_back", "audited", "reconciled", "expired")
# The columns of platform/runs/run_record.sql, in order; every one is written, absent ones as null.
COLUMNS = ("run_id", "product_id", "release_id", "task", "event", "table_name", "snapshot_before",
           "snapshot_after", "inputs", "rows_written", "bytes_written", "started_at", "recorded_at",
           "duration_s", "detail", "dbt_invocation_id", "openlineage_run_id")
REQUIRED = ("run_id", "product_id", "release_id", "task", "event")
INSERT = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
NEWEST = (f"SELECT snapshot_after FROM {TABLE} WHERE table_name = ? AND event = 'audited' "
          "AND snapshot_after IS NOT NULL ORDER BY recorded_at DESC LIMIT 1")


class RunRecordError(Exception):
    """A refused row or a failed write; its message never holds the password."""


def connect(host: str | None = None, port: int | None = None, user: str | None = None,
            password_file: str = "/run/k2-secrets/runs.password",
            ca: str = "/run/k2-secrets/tls/ca.pem") -> Any:
    """An HTTPS connection as svc.runs; defaults are the wrapper's environment (A-interfaces)."""
    import trino  # imported here so the tests need no network and the module no import cost

    with open(password_file) as f:
        password = f.read().strip()
    return trino.dbapi.connect(
        host=host or os.environ["K2_TRINO_HOST"], port=port or int(os.environ["K2_TRINO_INTERNAL_PORT"]),
        user=user or os.environ["K2_RUNS_USER"], http_scheme="https", verify=ca,
        auth=trino.auth.BasicAuthentication(user or os.environ["K2_RUNS_USER"], password))


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise RunRecordError("a timestamp must carry a time zone")
    return value.astimezone(timezone.utc)


def record(conn: Any, **row: Any) -> None:
    """Append one run-record row. The task and event are validated before any network call."""
    if unknown := set(row) - set(COLUMNS):
        raise RunRecordError(f"not run-record columns: {sorted(unknown)}")
    if missing := [c for c in REQUIRED if not row.get(c)]:
        raise RunRecordError(f"missing: {missing}")
    if row["task"] not in TASKS:
        raise RunRecordError(f"task {row['task']!r} is not one of {TASKS}")
    if row["event"] not in EVENTS:
        raise RunRecordError(f"event {row['event']!r} is not one of {EVENTS}")
    row = {c: row.get(c) for c in COLUMNS} | {"recorded_at": row.get("recorded_at") or datetime.now(timezone.utc)}
    if row["inputs"] is not None and not isinstance(row["inputs"], str):
        row["inputs"] = json.dumps(row["inputs"], separators=(",", ":"))
    if row["detail"] is not None:
        row["detail"] = row["detail"][:1000]
    for c in ("started_at", "recorded_at"):
        row[c] = _utc(row[c])
    params = [row[c] for c in COLUMNS]
    for attempt in (1, 2):  # one bounded retry, on a connection error only
        try:
            cur = conn.cursor()
            cur.execute(INSERT, params)
            cur.fetchall()  # the trino client runs the statement as the result is drawn
            return
        except (ConnectionError, TimeoutError) as e:
            if attempt == 2:
                raise RunRecordError(f"writing {row['run_id']}: {type(e).__name__}") from None


def newest_audited(conn: Any, table: str) -> int | None:
    """The snapshot id of the newest `audited` row for `table` (<catalog>.<ns>.<table>), or None.

    From the run records, not `t$refs`: this is the history the drift check compares the tag with
    (A6), and svc.runs reads it. The wrapper reads the tag itself for its inputs."""
    cur = conn.cursor()
    cur.execute(NEWEST, [table])
    rows = cur.fetchall()
    return int(rows[0][0]) if rows else None

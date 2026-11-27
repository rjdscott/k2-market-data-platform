"""The one run-records client (phase 001 interface A6, contracts/schema/run-record.schema.json).

record() appends one row to governance_build.run_records.run_record as svc.runs; newest_audited()
reads the newest `audited` row of a table. hold(), release() and held() write and read the holds
(platform/runs/holds.sql, step I-7), also as svc.runs. Dependency: trino only. The password is read from its
file at each connect() and never held, logged or put in an error message.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

TABLE = "governance_build.run_records.run_record"
TASKS = ("fetch", "load", "audit", "publish", "build", "expire", "record", "maintain")
EVENTS = ("started", "passed", "failed", "rolled_back", "audited", "reconciled", "expired",
          "offset_gap")
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


HOLDS = "governance_build.run_records.holds"
HOLD_KINDS = ("operator", "rebuild", "restore")  # make hold; make rebuild's and make restore's per-product lock
# The columns of platform/runs/holds.sql, in order.
HOLD_COLUMNS = ("product_id", "kind", "reason", "held_by", "held_at", "released_at", "released_by")
# An open hold: a hold row (released_at null) that no release row of its product, kind and held_at follows.
_OPEN = (f"h.released_at IS NULL AND NOT EXISTS (SELECT 1 FROM {HOLDS} r WHERE r.product_id = h.product_id "
         "AND r.kind = h.kind AND r.held_at = h.held_at AND r.released_at IS NOT NULL)")
# Each write is one statement that inserts only when the state allows it, so a hold never opens
# twice and a release never ends what is not held; the row count says which.
# ponytail: one statement, not a mutex: two writers in the same instant can both pass NOT EXISTS
# (Iceberg appends do not conflict). One operator per host; add a compare-and-set on the table's
# snapshot if two writers ever hold at once.
HOLD = (f"INSERT INTO {HOLDS} ({', '.join(HOLD_COLUMNS)}) SELECT ?, ?, ?, ?, ?, CAST(NULL AS timestamp(6) with time zone), "
        f"CAST(NULL AS varchar) WHERE NOT EXISTS (SELECT 1 FROM {HOLDS} h WHERE h.product_id = ? AND h.kind = ? AND {_OPEN})")
RELEASE = (f"INSERT INTO {HOLDS} ({', '.join(HOLD_COLUMNS)}) SELECT h.product_id, h.kind, h.reason, h.held_by, h.held_at, ?, ? "
           f"FROM {HOLDS} h WHERE h.product_id = ? AND h.kind = ? AND {_OPEN}")
HELD = (f"SELECT {', '.join(HOLD_COLUMNS)} FROM {HOLDS} h WHERE h.product_id = ? AND {_OPEN} "
        "ORDER BY h.held_at DESC LIMIT 1")


class HoldError(RunRecordError):
    """A refused hold or release: a bad argument, already held, or not held."""


def _written(cur: Any) -> int:
    """The row count Trino returns for an INSERT, as its one row's one column."""
    rows = cur.fetchall()
    return int(rows[0][0]) if rows else 0


def _hold_args(product: str, kind: str, by: str) -> None:
    if not product or not by:
        raise HoldError("a hold names its product and who acts")
    if kind not in HOLD_KINDS:
        raise HoldError(f"kind {kind!r} is not one of {HOLD_KINDS}")


def hold(conn: Any, product: str, reason: str, by: str, kind: str = "operator", at: datetime | None = None) -> None:
    """Open a hold of `kind` on `product` (<domain>.<product>). Refused, before any call, without a
    reason; refused by the table when one of that kind is open already."""
    _hold_args(product, kind, by)
    if not reason or not reason.strip():
        raise HoldError("a hold carries its reason")
    at = _utc(at) or datetime.now(timezone.utc)
    cur = conn.cursor()
    cur.execute(HOLD, [product, kind, reason[:1000], by, at, product, kind])
    if _written(cur) != 1:
        raise HoldError(f"{product} is held already ({kind}); release it first")


def release(conn: Any, product: str, by: str, kind: str = "operator", at: datetime | None = None) -> None:
    """End the open hold of `kind` on `product` by appending its release row. An operator's release
    never ends a rebuild's or a restore's lock, nor they an operator's hold."""
    _hold_args(product, kind, by)
    at = _utc(at) or datetime.now(timezone.utc)
    cur = conn.cursor()
    cur.execute(RELEASE, [at, by, product, kind])
    if _written(cur) != 1:
        raise HoldError(f"{product} has no open {kind} hold to release")


def held(conn: Any, product: str) -> dict[str, Any] | None:
    """The newest open hold on `product`, any kind, as a dict of HOLD_COLUMNS; None when it is free.
    A DAG's resolve fails fast on anything but None, naming the hold."""
    cur = conn.cursor()
    cur.execute(HELD, [product])
    rows = cur.fetchall()
    return dict(zip(HOLD_COLUMNS, rows[0])) if rows else None


def _json(value: Any) -> Any:
    """A JSON text column as its value; anything that is not JSON as None."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def state(records: list[dict[str, Any]]) -> str:
    """A product's state from its run records (dicts keyed by COLUMNS, any order, any runs): exactly
    one of `complete`, `partial: <contract ids, sorted, ", " separated>` or `degraded`.

    The newest build is the run_id of the newest `build` row; only that run's build rows count.
    degraded: a build row's detail is JSON with `"degraded": true` (the wrapper's word for a failed
    `warn` test such as "unresolved rows = 0"). partial: a row's `inputs` (JSON text or list) holds
    an input whose state is `paused` or whose snapshot is null (never audited). When both hold,
    degraded wins: the output has known-bad rows, which matters more to a reader than a late input.
    No build rows: `complete`, since no input is known missing; a caller shows "not built" itself.
    ponytail: an input over its maxLagMinutes is not partial, the record has no limit to compare;
    deprecated and retired inputs read their last audited snapshot and are not partial."""
    builds = [r for r in records if r.get("task") == "build"]
    if not builds:
        return "complete"
    newest = max(builds, key=lambda r: r["recorded_at"])["run_id"]
    rows = [r for r in builds if r["run_id"] == newest]
    if any(isinstance(d := _json(r.get("detail")), dict) and d.get("degraded") is True for r in rows):
        return "degraded"
    missing = {i["contractId"] for r in rows for i in _json(r.get("inputs")) or []
               if i.get("state") == "paused" or i.get("snapshot") is None}
    return f"partial: {', '.join(sorted(missing))}" if missing else "complete"


def offset_gaps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The product's `offset_gap` rows, newest first: {at, partitions, reason}; a detail that is not
    the schema's offsetGapDetail is skipped."""
    gaps = []
    for r in sorted((r for r in records if r.get("event") == "offset_gap"), key=lambda r: r["recorded_at"], reverse=True):
        d = _json(r.get("detail"))
        if isinstance(d, dict) and "partitions" in d and "reason" in d:
            gaps.append({"at": r["recorded_at"], "partitions": d["partitions"], "reason": d["reason"]})
    return gaps

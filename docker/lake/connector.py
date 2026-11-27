#!/usr/bin/env python3
"""The Spark connector for files: land, load, audit and publish one fetched batch (ADR-036;
docs/development/phase-001-interfaces.md, "The fetch container" and the bronze names of A5).

    connector.py load    --release <id> --product <domain>.<product> --run-id <dagId>/<runId>
    connector.py audit   --release <id> --product <domain>.<product> --run-id <dagId>/<runId>
    connector.py publish --release <id> --product <domain>.<product> --run-id <dagId>/<runId>

--release defaults to $K2_RELEASE. Each reads the product's compiled/products/<product>/pipeline.json
from /k2/releases/<id>/ and the run's spool entry /spool/<dagId>/<runId>/ that fetch.py left
(raw.<format>, fetch.json, and rows.jsonl unless the parse failed). The batch id is the run id.

load     recomputes the raw file's sha256 and size and refuses a mismatch with fetch.json, nothing
         landed; writes the bytes by conditional PUT (If-None-Match: *) to
         s3://k2-landing/<domain>/<product>/<fetch day, UTC>/<sha256>.<ext> (k2gen model.landing_key),
         so a landed object is never overwritten (the same bytes there: already landed; other
         bytes: refused); adds one row to lake.<fence>.landing_files. A file whose parse failed
         lands too: the landing is the record of what arrived, and nothing is staged. Then it
         appends rows.jsonl to the bronze table's one fixed branch `audit`, every row's
         source_file_sha256 set and the commit tagged k2.batch-id. While `audit` holds another
         batch `main` does not, this one is refused (bronze publishes in order: a failed batch
         blocks the next, never its landing). A retry stages nothing twice, and bytes already
         published stage no row.
audit    checks the batch at `audit`'s head, scoped to its rows (source_file_sha256 = its sha256),
         never the whole branch: parses (rows.jsonl there, each row exactly the table's columns),
         non_empty, row_count (the parser's count), keys_unique, in_domain:<column> per declared
         pattern, values_unchanged (no published key with other values), and each declared
         parser audit as fetch recorded it in audits.json (product code runs in the fetch
         container, never here; no entry fails closed); one audit_checks row each, at that snapshot.
publish  fast-forwards `main` to `audit` (Iceberg's fast_forward, which refuses anything else),
         only if the batch's newest audit at `audit`'s current head passed every check; a batch
         that staged nothing (the same bytes again) moves nothing.

Exits: 0 done (audit: every check passed); 1 a task error; 2 refused (a spool file that is a link at
any level, not a regular file, or has a second link; a sha256 or size mismatch, other bytes under the
key, `audit` holding another unpublished batch); 3 the batch failed its audit
(publish: its audit at `audit`'s head is missing or failed, main not moved).
The last line on stdout is the task's result as JSON, {"exit": <n>, ...}, for the DAG's record
task, which writes the run record (this task writes none).

The landing key is the Spark key, K2_SPARK_S3_ACCESS_KEY and K2_SPARK_S3_SECRET_KEY (A8): get and
put on the landing bucket, no delete. Nothing here prints a key.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RELEASES = Path("/k2/releases")  # k2gen model.RELEASES_MOUNT, read-only
SPOOL = Path("/spool")
# k2gen model's names, repeated because a release carries no k2gen; tests/test_lake_connector.py
# holds each equal to model.py's.
LANDING_BUCKET = "k2-landing"
LINEAGE = "source_file_sha256"  # model.LINEAGE_COLUMN
AUDIT = "audit"  # model.AUDIT_BRANCH
SUMMARY_KEYS = ("k2.run-id", "k2.release", "k2.batch-id", "k2.source-sha256")  # model.SNAPSHOT_SUMMARY_KEYS
LANDING_COLUMNS = ("object_key", "sha256", "bytes", "url", "final_url", "http_status", "fetched_at",
                   "landed_at", "parsed", "batch_id", "run_id", "release_id")  # model.PLATFORM_TABLES
CHECK_COLUMNS = ("table_name", "batch_id", "snapshot_id", "check_name", "passed", "detail", "checked_at",
                 "run_id", "release_id")
EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_FAILED = 0, 1, 2, 3
S3_ENDPOINT = os.environ.get("K2_S3_ENDPOINT", "http://minio:9000")  # as spark_conf.py reads them
S3_REGION = os.environ.get("K2_S3_REGION", "local-01")
KEY_ENV = ("K2_SPARK_S3_ACCESS_KEY", "K2_SPARK_S3_SECRET_KEY")
RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # as docker/fetch/fetch.py
PRODUCT_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z][a-z0-9]*(-[a-z0-9]+)*")
RUN_NAME = re.compile(r"[A-Za-z0-9_~:+-][A-Za-z0-9_.~:+-]*")
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")  # model.IDENTIFIER within MAX_IDENTIFIER
SHA256 = re.compile(r"[0-9a-f]{64}")


class Refused(Exception):
    """The batch is refused: exit 2. Nothing is landed or staged past the refusal."""


# --- The pure core: names, keys, decisions --------------------------------------------------


def name(part: str) -> str:
    """One identifier, held to NAME and backtick-quoted: the only way a name reaches Spark SQL."""
    if not NAME.fullmatch(part):
        raise ValueError(f"{part!r} is not a plain identifier")
    return f"`{part}`"


def rel(relation: str) -> str:
    """`catalog.namespace.table` from pipeline.json, each part through name()."""
    parts = relation.split(".")
    if len(parts) != 3:
        raise ValueError(f"{relation!r} is not catalog.namespace.table")
    return ".".join(name(p) for p in parts)


@dataclass(frozen=True)
class Run:
    release: str
    product: str
    run_id: str  # <dagId>/<runId>: the batch id too
    pipeline: dict

    @property
    def entry(self) -> Path:
        return SPOOL / self.run_id

    @property
    def raw(self) -> Path:
        return self.entry / f"raw.{self.pipeline['connector']['format']}"

    @property
    def bronze(self) -> dict:
        # ponytail: one bronze table per file product, since rows.jsonl is one list of rows; a
        # parser that feeds two tables needs the rows tagged by table first.
        [bronze] = self.pipeline["bronze"]
        return bronze

    def read(self, file: str) -> bytes:
        """One file of the run's entry, opened one level at a time from SPOOL without following a
        link: refused unless /spool/<dagId>/<runId>/<file> is a regular file with one link. fetch
        mounts only its DAG's directory, Spark the whole volume, so a parser's symbolic or hard
        link would otherwise land another product's files as this one's (audit 2026-11-27, HIGH 6).
        FileNotFoundError when it is absent."""
        why = f"spool entry {self.entry / file} is not a regular file with one link inside {self.entry}/"
        fds = [os.open(SPOOL, os.O_RDONLY | os.O_DIRECTORY)]
        try:
            for part, flag in [*((p, os.O_DIRECTORY) for p in self.run_id.split("/")), (file, os.O_NONBLOCK)]:
                try:  # O_NONBLOCK: a FIFO opens at once, to be refused below, instead of waiting
                    fds.append(os.open(part, os.O_RDONLY | os.O_NOFOLLOW | flag, dir_fd=fds[-1]))
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENXIO):
                        raise Refused(f"{why}; nothing read") from None
                    raise
            st = os.fstat(fds[-1])
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise Refused(f"{why}; nothing read")
            with open(fds[-1], "rb", closefd=False) as f:
                return f.read()
        finally:
            for fd in fds:
                os.close(fd)

    def audits(self) -> dict:
        """audits.json's entries by name, as fetch wrote them; none when it wrote no file."""
        try:
            text = self.read("audits.json")
        except FileNotFoundError:
            return {}
        try:
            return {a["name"]: a for a in json.loads(text)["audits"]}
        except (ValueError, KeyError, TypeError) as e:
            print(f"audit: audits.json does not read ({type(e).__name__}); every parser audit fails")
            return {}

    def lines(self) -> list[str] | None:
        """rows.jsonl's lines, or None when fetch's parse failed (exit 30)."""
        try:
            return self.read("rows.jsonl").decode("utf-8").splitlines()
        except FileNotFoundError:
            return None

    def spooled(self) -> tuple[dict, bytes, str]:
        """fetch.json, the raw bytes and their verified sha256."""
        record = json.loads(self.read("fetch.json"))
        data = self.read(self.raw.name)
        return record, data, verify(record, data)


def verify(record: dict, data: bytes) -> str:
    """The bytes' sha256, refused unless it and their size are what fetch.json recorded."""
    sha = hashlib.sha256(data).hexdigest()
    if record.get("sha256") != sha:
        raise Refused(f"the spooled file hashes to {sha}, fetch.json records {record.get('sha256')!r}")
    if record.get("bytes") != len(data):
        raise Refused(f"the spooled file is {len(data)} bytes, fetch.json records {record.get('bytes')!r}")
    return sha


def object_key(pipeline: dict, record: dict, sha: str) -> str:
    """The key in the landing bucket, `<prefix><YYYY-MM-DD>/<sha256>.<ext>`, the day being the
    fetch's in UTC (so a retry of the run lands on the same key)."""
    landing = pipeline["landing"]
    if landing["bucket"] != LANDING_BUCKET or not SHA256.fullmatch(sha):
        raise ValueError(f"landing {landing['bucket']!r} or sha256 {sha!r} is not the connector's")
    day = datetime.fromisoformat(record["fetchedAt"]).astimezone(timezone.utc).date().isoformat()
    return f"{landing['prefix']}{day}/{sha}.{pipeline['connector']['format']}"


def land(store, key: str, data: bytes) -> bool:
    """Writes `data` at `key` only if the key is free: True if written, False if those very
    bytes are there already; other bytes there are refused and left as they are."""
    if store.put_if_absent(key, data):
        return True
    if hashlib.sha256(store.get(key)).digest() != hashlib.sha256(data).digest():
        raise Refused(f"s3://{LANDING_BUCKET}/{key} holds different bytes; a landed object is never replaced")
    return False


def now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{value!r} is not a snapshot id")
    return value


def business_key(bronze: dict) -> list[str]:
    """The key without the lineage column: what a row is across files."""
    return [c for c in bronze["key"] if c != LINEAGE]


def check_names(bronze: dict) -> list[str]:
    """Every check a batch must pass, in the order they run: what publish requires."""
    values = ["values_unchanged"] if bronze["values"] and business_key(bronze) else []
    return ["parses", "non_empty", "row_count", "keys_unique",
            *(f"in_domain:{c}" for c in sorted(bronze["patterns"])), *values, *bronze["audits"]]


def check_sql(bronze: dict, snapshot, sha: str, parsed: int) -> list[tuple[str, str, dict, int]]:
    """`(check, sql, args, wanted)` for each declared check that runs in SQL: the query returns
    one count, which must equal `wanted`. Every query reads the batch's rows alone (lineage =
    the batch's sha256) at the one snapshot audited, never the whole branch (SPIKE-RUNCOST F12).
    Names come from the pipeline through name() and rel(); values only as bound parameters."""
    t, lin = rel(bronze["table"]), name(LINEAGE)
    at = f"VERSION AS OF {snapshot_id(snapshot)}"
    src = f"{t} {at} WHERE {lin} = :sha"
    keys = ", ".join(name(c) for c in bronze["key"])
    out = [("row_count", f"SELECT count(*) FROM {src}", {"sha": sha}, parsed),
           ("keys_unique", f"SELECT count(*) FROM (SELECT 1 FROM {src} GROUP BY {keys} HAVING count(*) > 1)",
            {"sha": sha}, 0)]
    for column in sorted(bronze["patterns"]):
        c = name(column)
        out.append((f"in_domain:{column}",
                    f"SELECT count(*) FROM {src} AND {c} IS NOT NULL AND NOT (CAST({c} AS STRING) RLIKE :pattern)",
                    {"sha": sha, "pattern": f"^(?:{bronze['patterns'][column]})$"}, 0))
    if "values_unchanged" in check_names(bronze):
        # ponytail: the other side, `o`, is every published row sharing a key, which Spark reads by
        # scanning the table at the snapshot; partition bronze on the key's date when it grows.
        on = " AND ".join(f"b.{name(c)} = o.{name(c)}" for c in business_key(bronze))
        same = " AND ".join(f"b.{name(c)} <=> o.{name(c)}" for c in bronze["values"])
        out.append(("values_unchanged",
                    f"SELECT count(*) FROM {t} {at} AS b JOIN {t} {at} AS o ON {on} "
                    f"WHERE b.{lin} = :sha AND o.{lin} <> :sha AND NOT ({same})", {"sha": sha}, 0))
    return out


def shape_problems(lines: list[str], columns: list[str]) -> list[str]:
    """Rows that are not exactly the table's columns (the lineage column aside): Spark's JSON
    reader would drop an extra field and null a missing one without a word."""
    want, problems = sorted(c for c in columns if c != LINEAGE), []
    for n, line in enumerate(lines):
        row = json.loads(line)
        if not isinstance(row, dict) or sorted(row) != want:
            problems.append(f"row {n} has columns {sorted(row) if isinstance(row, dict) else type(row).__name__}")
    return problems


def pending_batch(lake, table: str, refs: dict) -> str | None:
    """The batch `audit` holds that `main` does not, or None."""
    head = refs.get(AUDIT)
    if head is None or lake.published(table, head):
        return None
    return lake.batch_of(table, head) or "an untagged commit"


# --- The tasks: pure flows over a store and a lake --------------------------------------------


def load(run: Run, store, lake) -> tuple[int, dict]:
    """Lands the spooled file and logs it in landing_files, then stages its rows on `audit`."""
    try:
        record, data, sha = run.spooled()
        lines = run.lines()
    except Refused as e:
        print(f"load refused: {e}; nothing landed", file=sys.stderr)
        return EXIT_REFUSED, {}
    parsed = lines is not None
    key = object_key(run.pipeline, record, sha)
    uri = f"s3://{LANDING_BUCKET}/{key}"
    try:
        wrote = land(store, key, data)
    except Refused as e:
        print(f"load refused: {e}", file=sys.stderr)
        return EXIT_REFUSED, {"objectKey": uri}
    log = run.pipeline["landing"]["log"]
    if not lake.has_object(log, uri):  # a retry of this task logs the object once
        row = {"object_key": uri, "sha256": sha, "bytes": len(data), "url": record["url"],
               "final_url": record["finalUrl"], "http_status": record["httpStatus"],
               "fetched_at": datetime.fromisoformat(record["fetchedAt"]), "landed_at": now(), "parsed": parsed,
               "batch_id": run.run_id, "run_id": run.run_id, "release_id": run.release}
        lake.append(log, [row], summary(run, sha))
    print(f"load: {'landed' if wrote else 'already landed'} {uri}, {len(data)} bytes, parsed {parsed}")
    landed = {"objectKey": uri, "bytes": len(data), "rows": 0}
    if not parsed:
        print("load: the parse failed, so nothing is staged; the audit fails the batch")
        return EXIT_OK, landed
    return stage(run, lake, lines, sha, landed)


def stage(run: Run, lake, lines: list[str], sha: str, result: dict) -> tuple[int, dict]:
    """Appends the batch's rows to `audit` unless another batch waits there unpublished (it
    blocks this one: bronze publishes in order), this batch is staged already (a retry), or its
    bytes are published already (the same file again)."""
    table = run.bronze["table"]
    refs = lake.refs(table)
    result = {**result, "table": table, "snapshotBefore": refs.get(AUDIT)}
    pending = pending_batch(lake, table, refs)
    if pending == run.run_id:
        print(f"load: batch {run.run_id} is on {AUDIT} already (snapshot {refs[AUDIT]}); nothing staged")
        return EXIT_OK, result
    if pending is not None:
        print(f"load refused: {AUDIT} of {table} holds batch {pending}, unpublished; this batch waits "
              f"behind it (its file is landed)", file=sys.stderr)
        return EXIT_REFUSED, result
    if lake.published_rows(table, sha):
        print(f"load: {sha} is published in {table} already; nothing staged")
        return EXIT_OK, result
    if AUDIT not in refs:
        lake.create_branch(table)
    elif refs[AUDIT] != refs.get("main"):  # behind main, which maintenance moved on
        lake.reset_branch(table, refs["main"])
    if lines:
        lake.append_rows(table, lines, sha, summary(run, sha))
    after = lake.refs(table).get(AUDIT)
    print(f"load: {len(lines)} rows staged on {AUDIT} of {table}, snapshot {after}")
    return EXIT_OK, {**result, "rows": len(lines), "snapshotAfter": after}


def audit(run: Run, store, lake) -> tuple[int, dict]:
    """Runs every check of the batch at `audit`'s head and records each in audit_checks."""
    _, _, sha = run.spooled()
    bronze, lines = run.bronze, run.lines()
    table = bronze["table"]
    refs = lake.refs(table)
    pending = pending_batch(lake, table, refs)
    if pending not in (None, run.run_id):
        print(f"audit refused: {AUDIT} of {table} holds batch {pending}, not this one", file=sys.stderr)
        return EXIT_REFUSED, {"table": table}
    head = refs.get(AUDIT, refs.get("main"))
    results = audit_results(bronze, lines, lake, head, sha, run.audits())
    at = now()
    rows = [{"table_name": table, "batch_id": run.run_id, "snapshot_id": head, "check_name": check,
             "passed": passed, "detail": detail[:1000], "checked_at": at, "run_id": run.run_id,
             "release_id": run.release} for check, passed, detail in results]
    lake.append(bronze["checks"], rows, summary(run, sha))
    for check, passed, detail in results:
        print(f"  {'pass' if passed else 'FAIL'}  {check:<24} {detail}")
    failed = [c for c, passed, _ in results if not passed]
    print(f"audit: batch {run.run_id} at {AUDIT} snapshot {head}: "
          f"{'FAILED ' + ', '.join(failed) + '; main not moved' if failed else 'passed'}")
    return (EXIT_FAILED if failed else EXIT_OK), {"table": table, "snapshot": head, "failed": failed}


def unaudited(found: list, expected: list[str]) -> str | None:
    """Why the batch's newest audit at this snapshot does not license its publish, or None
    (from #239). `found` is `(check, passed, checked_at)` per audit_checks row of the batch at
    the snapshot; the newest checked_at is its latest audit, which must pass every expected check."""
    if not found:
        return "no audit of this batch at this snapshot is recorded"
    newest = max(at for _, _, at in found)
    last = {check: passed for check, passed, at in found if at == newest}
    missing = [c for c in expected if c not in last]
    failed = sorted(c for c, passed in last.items() if not passed)
    if missing or failed:
        return f"its newest audit, at {newest}, failed {failed or 'nothing'} and lacks {missing or 'nothing'}"
    return None


def publish(run: Run, store, lake) -> tuple[int, dict]:
    """Fast-forwards `main` to `audit`'s head, only when the batch's newest audit at that very
    head passed every check; a branch written after its audit is not published unaudited."""
    bronze = run.bronze
    table = bronze["table"]
    refs = lake.refs(table)
    head = refs.get(AUDIT, refs.get("main"))
    result = {"table": table, "snapshotBefore": refs.get("main"), "snapshotAfter": refs.get("main")}
    why = unaudited(lake.checks_of(bronze["checks"], table, run.run_id, head), check_names(bronze))
    if why:
        print(f"publish refused at {AUDIT} snapshot {head}: {why}; main {refs.get('main')} not moved", file=sys.stderr)
        return EXIT_FAILED, result
    pending = pending_batch(lake, table, refs)
    if pending is None:
        print(f"publish: batch {run.run_id} staged nothing unpublished; main {refs.get('main')} stays")
        return EXIT_OK, result
    if pending != run.run_id:
        print(f"publish refused: {AUDIT} holds batch {pending}, not this one", file=sys.stderr)
        return EXIT_REFUSED, result
    lake.fast_forward(table)  # Iceberg refuses one that is not a fast-forward
    after = lake.refs(table).get("main")
    if after != head:
        raise RuntimeError(f"main is {after} after the fast-forward, not {AUDIT}'s {head}")
    print(f"publish: main {refs.get('main')} -> {after}, batch {run.run_id}")
    return EXIT_OK, {**result, "snapshotAfter": after}


def string_literal(value: str) -> str:
    """A Spark SQL string literal, for the procedure arguments a parameter cannot carry: every
    backslash and quote escaped, no control character, so it is always one token."""
    if any(c < " " for c in value):
        raise ValueError(f"{value!r}: a control character in a SQL literal")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def procedure_table(relation: str) -> tuple[str, str]:
    """`catalog.ns.table` as a procedure's quoted catalog and its `table =>` literal."""
    rel(relation)
    catalog, ns, table = relation.split(".")
    return name(catalog), string_literal(f"{ns}.{table}")


def fast_forward_sql(table: str) -> str:
    catalog, ident = procedure_table(table)
    return (f"CALL {catalog}.`system`.`fast_forward`(table => {ident}, branch => {string_literal('main')}, "
            f"to => {string_literal(AUDIT)})")


def parser_audits(declared: list[str], found: dict) -> list[tuple[str, bool, str]]:
    """One result per declared parser audit, from fetch's audits.json: passed only if fetch
    recorded it passed with no failure; a declared audit with no entry fails closed. The
    functions are product code and ran in the fetch container; nothing here imports them."""
    for extra in sorted(set(found) - set(declared)):
        print(f"audit: audits.json holds {extra!r}, which the pipeline does not declare; ignored")
    out = []
    for check in declared:
        a = found.get(check)
        if a is None:
            out.append((check, False, "not run: no result for it in fetch's audits.json"))
            continue
        passed, failures, detail = a.get("passed"), a.get("failures"), str(a.get("detail") or "")
        counted = isinstance(failures, int) and not isinstance(failures, bool)
        if passed is True and counted and failures == 0:
            out.append((check, True, ""))
        elif counted and failures > 0:
            out.append((check, False, f"{failures} failing rows: {detail}"))
        else:
            out.append((check, False, detail or f"audits.json records passed {passed!r}, failures {failures!r}"))
    return out


def audit_results(bronze: dict, lines: list[str] | None, lake, head, sha: str,
                  audits: dict) -> list[tuple[str, bool, str]]:
    """`(check, passed, detail)` in check_names order; a batch that did not parse has one row."""
    if lines is None:
        return [("parses", False, "fetch could not parse the file; its bytes are landed")]
    problems = shape_problems(lines, lake.columns(bronze["table"]))
    if problems:
        return [("parses", False, f"{len(problems)} rows: " + "; ".join(problems[:5]))]
    out = [("parses", True, f"{len(lines)} rows"),
           ("non_empty", bool(lines), "" if lines else "the file holds no rows")]
    if head is None:
        out += [(c, False, f"no snapshot on {AUDIT} to check") for c in check_names(bronze)[2:]]
        return out
    for check, text, args, wanted in check_sql(bronze, head, sha, len(lines)):
        got = lake.count(check, text, args)
        detail = (f"{got} rows on {AUDIT}, the parser wrote {wanted}" if check == "row_count"
                  else f"{got} rows fail it" if got else "")
        out.append((check, got == wanted, detail))
    return out + parser_audits(bronze["audits"], audits)


def summary(run: Run, sha: str) -> dict:
    """The snapshot summary every commit of the batch carries (model.SNAPSHOT_SUMMARY_KEYS)."""
    return dict(zip(SUMMARY_KEYS, (run.run_id, run.release, run.run_id, sha)))


# --- The shells: S3 by the standard library, the lake through Spark ---------------------------


def sign(method: str, path: str, headers: dict, payload_sha: str, access: str, secret: str) -> str:
    """The AWS Signature Version 4 `Authorization` value for a request with no query string
    (from #239's docker/lake/file_source.py). `headers` are those sent, host and x-amz-date among them."""
    hdrs = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    stamp = hdrs["x-amz-date"]
    names = ";".join(sorted(hdrs))
    canonical = "\n".join([method, path, "", *(f"{k}:{hdrs[k]}" for k in sorted(hdrs)), "", names, payload_sha])
    scope = f"{stamp[:8]}/{S3_REGION}/s3/aws4_request"
    key = ("AWS4" + secret).encode()
    for part in (stamp[:8], S3_REGION, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={names}, Signature={signature}"


class Landing:
    """The landing bucket over MinIO's S3 API, path-style and signed, standard library only:
    the Spark image has no boto3, and its Java S3 client is not reachable from Python."""

    def __init__(self, bucket: str = LANDING_BUCKET):
        self.bucket = bucket
        self._access, self._secret = (os.environ[k] for k in KEY_ENV)

    def _call(self, method: str, key: str, body: bytes = b"", headers: dict | None = None):
        path = f"/{self.bucket}/{urllib.parse.quote(key)}"
        payload = hashlib.sha256(body).hexdigest()
        hdrs = {**(headers or {}), "host": urllib.parse.urlsplit(S3_ENDPOINT).netloc,
                "x-amz-date": now().strftime("%Y%m%dT%H%M%SZ"), "x-amz-content-sha256": payload}
        hdrs["authorization"] = sign(method, path, hdrs, payload, self._access, self._secret)
        request = urllib.request.Request(S3_ENDPOINT + path, data=body if method == "PUT" else None,
                                         method=method, headers=hdrs)
        return urllib.request.urlopen(request, timeout=60)  # noqa: S310 - the configured object store

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            with self._call("PUT", key, data, {"if-none-match": "*"}):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 412:  # Precondition Failed: the key exists, nothing was written
                return False
            raise

    def get(self, key: str) -> bytes:
        with self._call("GET", key) as response:
            return response.read()


class SparkLake:
    """The lake facts the flows read and the commits they make, each one statement over
    identifiers from rel() and values bound as parameters (Spark 3.5 named markers)."""

    def __init__(self, spark):
        self.spark = spark

    def _sql(self, text: str, **args):
        return [tuple(r) for r in self.spark.sql(text, args or None).collect()]

    def has_object(self, log: str, uri: str) -> bool:
        return bool(self._sql(f"SELECT 1 FROM {rel(log)} WHERE `object_key` = :uri LIMIT 1", uri=uri))

    def append(self, table: str, records: list, props: dict) -> None:
        schema = self.spark.table(rel(table)).schema
        frame = self.spark.createDataFrame([tuple(r[f.name] for f in schema) for r in records], schema=schema)
        self._write(frame, rel(table), props)

    @staticmethod
    def _write(frame, target: str, props: dict) -> None:
        writer = frame.writeTo(target)
        for k, v in props.items():
            writer = writer.option(f"snapshot-property.{k}", v)
        writer.append()

    def refs(self, table: str) -> dict:
        return dict(self._sql(f"SELECT `name`, `snapshot_id` FROM {rel(table)}.`refs`"))

    def published(self, table: str, snapshot: int) -> bool:
        """Whether `snapshot` is main's or one of its ancestors."""
        return bool(self._sql(f"SELECT 1 FROM {rel(table)}.`history` WHERE `snapshot_id` = :s "
                              "AND `is_current_ancestor` LIMIT 1", s=snapshot_id(snapshot)))

    def batch_of(self, table: str, snapshot: int) -> str | None:
        found = self._sql(f"SELECT `summary`['k2.batch-id'] FROM {rel(table)}.`snapshots` "
                          "WHERE `snapshot_id` = :s", s=snapshot_id(snapshot))
        return found[0][0] if found else None

    def published_rows(self, table: str, sha: str) -> int:
        return self._sql(f"SELECT count(*) FROM {rel(table)} WHERE {name(LINEAGE)} = :sha", sha=sha)[0][0]

    def columns(self, table: str) -> list[str]:
        return [f.name for f in self.spark.table(rel(table)).schema.fields]

    def create_branch(self, table: str) -> None:
        self.spark.sql(f"ALTER TABLE {rel(table)} CREATE BRANCH IF NOT EXISTS {name(AUDIT)}")

    def reset_branch(self, table: str, snapshot: int) -> None:
        self.spark.sql(f"ALTER TABLE {rel(table)} REPLACE BRANCH {name(AUDIT)} AS OF VERSION {snapshot_id(snapshot)}")

    def append_rows(self, table: str, lines: list[str], sha: str, props: dict) -> None:
        """rows.jsonl onto `audit`, read by Spark's JSON reader against the table's own types
        (FAILFAST: a value that does not read as its column's type fails the task, nothing
        written), with the lineage column set to the batch's sha256."""
        from pyspark.sql import functions as F
        from pyspark.sql.types import StructType

        schema = self.spark.table(rel(table)).schema
        body = StructType([f for f in schema.fields if f.name != LINEAGE])
        # ponytail: the rows pass through the driver, bounded by fetch's 64 MiB cap.
        frame = (self.spark.read.schema(body).option("mode", "FAILFAST")
                 .json(self.spark.sparkContext.parallelize(lines, 1))
                 .withColumn(LINEAGE, F.lit(sha))
                 .select(*[F.col(f.name).cast(f.dataType) for f in schema.fields]))
        self._write(frame, f"{rel(table)}.{name('branch_' + AUDIT)}", props)

    def count(self, check: str, text: str, args: dict) -> int:
        return self._sql(text, **args)[0][0]

    def checks_of(self, checks: str, table: str, batch: str, snapshot) -> list:
        return self._sql(f"SELECT `check_name`, `passed`, `checked_at` FROM {rel(checks)} WHERE `table_name` = :t "
                         "AND `batch_id` = :b AND `snapshot_id` <=> :s", t=table, b=batch, s=snapshot)

    def fast_forward(self, table: str) -> None:
        self.spark.sql(fast_forward_sql(table))


def parse_args(argv: list[str] | None, read_pipeline) -> tuple[str, Run]:
    ap = argparse.ArgumentParser(prog="connector.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=sorted(TASKS))
    ap.add_argument("--release", default=os.environ.get("K2_RELEASE"))
    ap.add_argument("--product", required=True)
    ap.add_argument("--run-id", required=True, help="<dagId>/<runId>")
    a = ap.parse_args(argv)
    dag_id, _, run_name = a.run_id.partition("/")
    if not (a.release and RELEASE_ID.fullmatch(a.release) and PRODUCT_ID.fullmatch(a.product)
            and RUN_NAME.fullmatch(run_name)):
        ap.error("--release, --product or --run-id is not a plain name")
    pipeline = read_pipeline(RELEASES / a.release / "compiled" / "products" / a.product / "pipeline.json")
    if (pipeline.get("connector") or {}).get("kind") != "https-file" or dag_id != pipeline["dagId"]:
        ap.error(f"{a.product} has no https-file connector, or the run is not of DAG {pipeline['dagId']}")
    return a.task, Run(release=a.release, product=a.product, run_id=a.run_id, pipeline=pipeline)


TASKS = {"load": load, "audit": audit, "publish": publish}


def main(argv: list[str] | None = None) -> int:
    task, run = parse_args(argv, lambda p: json.loads(p.read_text()))
    from spark_conf import lake_session

    spark = lake_session(f"k2-connector-{task}-{run.pipeline['fence']}")
    code, result = EXIT_ERROR, {}
    try:
        code, result = TASKS[task](run, Landing(), SparkLake(spark))
    except Refused as e:
        print(f"{task} refused: {e}", file=sys.stderr)
        code = EXIT_REFUSED
    finally:
        spark.stop()
        print(json.dumps({"exit": code, "task": task, "batchId": run.run_id, **result}))
    return code


if __name__ == "__main__":
    sys.exit(main())

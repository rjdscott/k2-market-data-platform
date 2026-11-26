#!/usr/bin/env python3
"""
A file source into the lake (ADR-036): fetch, load, audit, publish, one
subcommand per task of the DAG docker/airflow/dags/factory.py builds for it.

    python3 file_source.py fetch   --table bronze.<name>
    python3 file_source.py load    --table bronze.<name> --object <key>
    python3 file_source.py audit   --table bronze.<name> --object <key>
    python3 file_source.py publish --table bronze.<name> --object <key>

The sources are generated/file_sources.json, which `make generate` writes from
each contract's `fileSource` custom property and its columns.

fetch    downloads the URL with the standard library, takes its sha256, and lands
         it in bucket k2-landing at <domain>/<product>/<fetch date>/<sha256>.<ext>
         with a conditional PUT (`If-None-Match: *`): the store refuses a write
         to a key that exists, so the same bytes on the same day write nothing,
         and no write through this path replaces a landed file. Prints the key
         last; the DAG hands it to the other three. No Spark, no lock.
load     reads the object back, refuses it if its bytes no longer hash to its
         key, records it in bronze.source_files, parses it with
         sources/<parser>.py, and appends the rows `main` does not already hold,
         by key and value, to branch wap_<sha256[:16]>, which it first resets to
         `main`. Loading the same file again adds nothing. A file that does not
         parse is a failed `parses` row in audit.checks and exit 1.
audit    parses the file again, reads the branch, runs GENERIC and the product's
         own checks, and files every result in audit.checks in one commit, scope
         "<table> <sha256> <branch snapshot id>". Exit 1 on any failure: the DAG
         stops there, the branch stays for inspection and `main` does not move.
publish  fast-forwards `main` to the branch and drops the branch, only if the
         newest audit of this file at the branch's current snapshot passed every
         check: a branch loaded again after its audit is not published unaudited.

load, audit and publish take the writer lock (lock.py) before starting Spark,
waiting at most LOCK_WAIT_S; still held then, they exit 75 and the DAG retries.
`--namespace-map bronze=scratch,audit=scratch` renames every table this job
touches, as apply_ddl.py's flag does, for a rehearsal that writes no real table.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import sources
from lock import LOCK_PATH, acquire_lock

import offsets as O

CONFIG = Path(__file__).resolve().parent / "generated" / "file_sources.json"
BUCKET = os.environ.get("K2_LANDING_BUCKET", "k2-landing")
# The same endpoint and region the lake catalog's S3FileIO uses (spark_conf.py).
S3_ENDPOINT = os.environ.get("K2_S3_ENDPOINT", "http://minio:9000")
S3_REGION = os.environ.get("K2_S3_REGION", "local-01")
SOURCE_FILES = "bronze.source_files"
CHECKS = "audit.checks"
LINEAGE = "source_file_sha256"  # every file-source row carries the sha256 of the file it came from
JOB = "file_source"  # the k2.job of every commit this writes, and audit.checks.job
SHA_PROPERTY = "k2.source-file-sha256"
# The checks every file source runs, in order; a product adds its own after them.
# platform/k2gen/k2gen/cli.py refuses a product audit with one of these names.
GENERIC = ("parses", "non_empty", "keys_unique", "in_domain", "values_unchanged", "staged")
MAX_BYTES = 64 * 2**20  # a download larger than this is refused, not landed
LOCK_WAIT_S = 600  # two ingest cycles; maintenance holds the lock longer, and the DAG retries
EXIT_LOCK_HELD = 75  # os.EX_TEMPFAIL, as ingest.py
DETAIL_ITEMS = 5  # problems quoted in a check's detail; the rest are counted


# ── landing ───────────────────────────────────────────────────────────────


def sign(method: str, path: str, headers: dict, payload_sha: str, access: str, secret: str,
         region: str, service: str = "s3") -> str:
    """The AWS Signature Version 4 `Authorization` value for a request with no query
    string. `headers` are the ones sent and signed, `host` and `x-amz-date` among them."""
    hdrs = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    stamp = hdrs["x-amz-date"]
    names = ";".join(sorted(hdrs))
    canonical = "\n".join([method, path, "", *(f"{k}:{hdrs[k]}" for k in sorted(hdrs)), "", names, payload_sha])
    scope = f"{stamp[:8]}/{region}/{service}/aws4_request"
    key = ("AWS4" + secret).encode()
    for part in (stamp[:8], region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={names}, Signature={signature}"


class Landing:
    """The landing bucket over MinIO's S3 API: path-style, signed, standard library only.

    The credentials are the lake's own (MINIO_ROOT_USER, MINIO_ROOT_PASSWORD), from the
    Spark container's environment; nothing here prints them."""

    def __init__(self, bucket: str = BUCKET):
        self.bucket = bucket
        self._access = os.environ["MINIO_ROOT_USER"]
        self._secret = os.environ["MINIO_ROOT_PASSWORD"]

    def _call(self, method: str, key: str, body: bytes = b"", headers: dict | None = None):
        path = f"/{self.bucket}/{urllib.parse.quote(key)}"
        payload = hashlib.sha256(body).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        hdrs = {**(headers or {}), "host": urllib.parse.urlsplit(S3_ENDPOINT).netloc,
                "x-amz-date": stamp, "x-amz-content-sha256": payload}
        hdrs["authorization"] = sign(method, path, hdrs, payload, self._access, self._secret, S3_REGION)
        request = urllib.request.Request(S3_ENDPOINT + path, data=body if method == "PUT" else None,
                                         method=method, headers=hdrs)
        return urllib.request.urlopen(request, timeout=60)  # noqa: S310 - the configured object store

    def put_if_absent(self, key: str, data: bytes, meta: dict) -> bool:
        """True if written; False if the key already exists, in which case nothing is."""
        headers = {"if-none-match": "*", **{f"x-amz-meta-{k}": v for k, v in meta.items()}}
        try:
            with self._call("PUT", key, data, headers):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 412:  # Precondition Failed: the key exists
                return False
            raise

    def get(self, key: str) -> tuple:
        """The object's bytes, and its user metadata without the x-amz-meta- prefix."""
        with self._call("GET", key) as response:
            meta = {k.lower()[len("x-amz-meta-"):]: v for k, v in response.headers.items()
                    if k.lower().startswith("x-amz-meta-")}
            return response.read(), meta


def fetch(spec: dict, landing, now: datetime | None = None, opener=urllib.request.urlopen) -> str:
    """Download the source and land it; the landed key."""
    now = now or datetime.now(timezone.utc)
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "k2-file-source/1"})
    with opener(request, timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise SystemExit(f"{spec['url']}: larger than {MAX_BYTES} bytes, refused and not landed")
    sha = hashlib.sha256(data).hexdigest()
    key = f"{spec['domain']}/{spec['product']}/{now:%Y-%m-%d}/{sha}.{spec['format']}"
    written = landing.put_if_absent(key, data, {"source-url": spec["url"], "fetched-at": now.isoformat(timespec="seconds")})
    print(("landed" if written else "already landed, nothing written:") + f" s3://{landing.bucket}/{key}, {len(data)} bytes")
    return key


def object_sha(spec: dict, key: str) -> str:
    """The sha256 a landed key of this source names; SystemExit for any other key."""
    shape = (rf"{re.escape(spec['domain'])}/{re.escape(spec['product'])}/[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}/"
             rf"([0-9a-f]{{64}})\.{re.escape(spec['format'])}")
    match = re.fullmatch(shape, key)
    if not match:
        raise SystemExit(f"{key!r} is not a landed file of {spec['domain']}/{spec['product']}")
    return match.group(1)


def read_landed(spec: dict, key: str, landing) -> tuple:
    """`(sha256, bytes, metadata)` of a landed file whose bytes still hash to its key."""
    sha = object_sha(spec, key)
    data, meta = landing.get(key)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise SystemExit(f"s3://{landing.bucket}/{key} hashes to {actual}, not to its key: "
                         "the landed file was changed after landing; nothing is read from it")
    return sha, data, meta


# ── parse and audit: pure ─────────────────────────────────────────────────


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def parse(spec: dict, data: bytes) -> tuple:
    """`(rows, None)`, or `(None, why)` when the file does not read as the table's rows."""
    try:
        rows = sources.parser(spec["parser"]).parse(data)
    except Exception as exc:  # noqa: BLE001 - any failure to read the file is the finding
        return None, f"{type(exc).__name__}: {exc}"
    want = sorted(c for c in spec["columns"] if c != LINEAGE)
    for n, row in enumerate(rows):
        if sorted(row) != want:
            return None, f"row {n} has columns {sorted(row)}, the table {want}"
        for column in spec["decimal"]:
            try:
                ok = row[column] is None or _decimal(row[column]).is_finite()
            except InvalidOperation:
                ok = False
            if not ok:
                return None, f"row {n}: {column} {row[column]!r} is not a decimal number"
    return rows, None


def key_of(spec: dict, row: dict) -> tuple:
    return tuple(row[c] for c in spec["key"])


def value_of(spec: dict, row: dict) -> tuple:
    """The row's non-key values, a number as a Decimal: 137.20 and 137.2 are one value."""
    return tuple(_decimal(row[c]) if c in spec["decimal"] else row[c] for c in spec["values"])


def to_stage(spec: dict, rows: list, published: list) -> list:
    """The file's rows `main` does not already hold with the same key and value."""
    have = {(key_of(spec, r), value_of(spec, r)) for r in published}
    return [r for r in rows if (key_of(spec, r), value_of(spec, r)) not in have]


def _show(values: tuple) -> str:
    return ", ".join("null" if v is None else str(v) for v in values)


def _result(name: str, problems: list) -> tuple:
    detail = "; ".join(problems[:DETAIL_ITEMS])
    if len(problems) > DETAIL_ITEMS:
        detail += f"; and {len(problems) - DETAIL_ITEMS} more"
    return name, not problems, len(problems), detail


def checks(spec: dict, rows: list, branch: list, sha: str, product_audits: list) -> list:
    """`(check, passed, observed, detail)` for GENERIC in order, then each product audit.

    `rows` are the file's, parsed; `branch` the table's rows on the write-audit-publish
    branch, which is what `main` would hold after a publish; `product_audits` are
    `(name, function)` pairs from the source's parser module.
    """
    keys = Counter(key_of(spec, r) for r in rows)
    patterns = {c: re.compile(p) for c, p in spec["patterns"].items()}
    theirs: dict = {}  # key -> values the rows of other files hold for it
    for r in branch:
        if r[LINEAGE] != sha:
            theirs.setdefault(key_of(spec, r), set()).add(value_of(spec, r))
    staged = {(key_of(spec, r), value_of(spec, r)) for r in branch}
    out = [
        ("parses", True, len(rows), ""),
        ("non_empty", bool(rows), len(rows), "" if rows else "the file holds no rows"),
        _result("keys_unique", [f"key {_show(k)} appears {n} times" for k, n in keys.items() if n > 1]),
        _result("in_domain", [f"{c} {r[c]!r} does not match {p.pattern}" for r in rows for c, p in patterns.items()
                              if r[c] is not None and not p.fullmatch(str(r[c]))]),
        _result("values_unchanged", [
            f"key {_show(key_of(spec, r))}: published {' or '.join(sorted(map(_show, theirs[key_of(spec, r)])))}, "
            f"this file {_show(value_of(spec, r))}"
            for r in rows if key_of(spec, r) in theirs and value_of(spec, r) not in theirs[key_of(spec, r)]]),
        _result("staged", [f"key {_show(key_of(spec, r))} with {_show(value_of(spec, r))} is not on the branch"
                           for r in rows if (key_of(spec, r), value_of(spec, r)) not in staged]),
    ]
    return out + [_result(name, list(audit(rows))) for name, audit in product_audits]


def unaudited(found: list, expected: list) -> str | None:
    """Why the newest audit of a file does not license its publish, or None if it does.

    `found` is `(check, passed, run_ts)` for every audit row of the file at the branch's
    current snapshot; the newest run_ts is its latest audit, and that one must have passed
    each expected check."""
    if not found:
        return "no audit of this file at this branch snapshot is recorded"
    newest = max(ts for _, _, ts in found)
    last = {name: passed for name, passed, ts in found if ts == newest}
    missing = [c for c in expected if c not in last]
    failed = sorted(c for c, passed in last.items() if not passed)
    if missing or failed:
        return f"its newest audit, at {newest}, failed {failed or 'nothing'} and lacks {missing or 'nothing'}"
    return None


def branch_of(sha: str) -> str:
    return f"wap_{sha[:16]}"


def scope_of(table: str, sha: str, head) -> str:
    """An audit row's scope: the table, the file, and the branch snapshot it checked ("none"
    for a file that did not parse, which has no branch)."""
    return f"{table} {sha} {'none' if head is None else head}"


# ── load, audit, publish: Spark ───────────────────────────────────────────


def _rows(spark, table: str, columns: list) -> list:
    # ponytail: the whole table into the driver, which a reference source of a few hundred
    # thousand rows fits at a 512m heap; filter by the file's keys in Spark when one does not.
    return [r.asDict() for r in spark.table(table).select(*columns).collect()]


def _append(spark, table: str, records: list, sha: str) -> None:
    schema = spark.table(table).schema
    frame = spark.createDataFrame([tuple(r[f.name] for f in schema) for r in records], schema=schema)
    frame.writeTo(table).option(f"snapshot-property.{O.JOB}", JOB).option(f"snapshot-property.{SHA_PROPERTY}", sha).append()


def _file_checks(spark, names: dict, sha: str, head, results: list) -> bool:
    from catalog import write_audit_rows
    from pyspark.sql import Row

    run_ts = datetime.now(timezone.utc)
    rows = [Row(run_ts=run_ts, job=JOB, check_name=name, scope=scope_of(names["logical"], sha, head), passed=passed,
                observed=int(observed), detail=detail) for name, passed, observed, detail in results]
    for name, passed, observed, detail in results:
        print(f"  {'pass' if passed else 'FAIL'}  {name:<18} {observed:>8}  {detail}")
    return write_audit_rows(spark, rows, {SHA_PROPERTY: sha}, job=JOB, table=names["checks"])


def _ref(spark, table: str, name: str):
    found = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = '{name}'").collect()
    return found[0][0] if found else None


def load(spark, spec: dict, names: dict, key: str, landing) -> int:
    sha, data, meta = read_landed(spec, key, landing)
    path = f"s3://{landing.bucket}/{key}"
    if not spark.sql(f"SELECT 1 FROM {names['files']} WHERE landing_path = '{path}' LIMIT 1").collect():
        landed = {"domain": spec["domain"], "product": spec["product"], "url": meta["source-url"],
                  "fetched_at": datetime.fromisoformat(meta["fetched-at"]), "sha256": sha,
                  "landing_path": path, "byte_size": len(data)}
        _append(spark, names["files"], [landed], sha)
    rows, why = parse(spec, data)
    if why:
        _file_checks(spark, names, sha, None, [("parses", False, 0, why)])
        print(f"load: {path} does not parse; nothing staged")
        return 1
    table, branch = names["table"], branch_of(sha)
    new = to_stage(spec, rows, _rows(spark, table, spec["columns"]))
    spark.sql(f"ALTER TABLE {table} CREATE OR REPLACE BRANCH {branch}")
    if new:
        _append(spark, f"{table}.branch_{branch}", [{**r, LINEAGE: sha} for r in new], sha)
    print(f"load: {len(rows)} rows in {path}, {len(new)} not on main, staged on branch {branch} "
          f"(head {_ref(spark, table, branch)}); main {_ref(spark, table, 'main')}")
    return 0


def audit(spark, spec: dict, names: dict, key: str, landing) -> int:
    sha, data, _ = read_landed(spec, key, landing)
    table, branch = names["table"], branch_of(sha)
    head = _ref(spark, table, branch)  # the lock is held: nothing moves the branch past this
    rows, why = parse(spec, data)
    if why:
        results = [("parses", False, 0, why)]
    else:
        staged = _rows(spark, f"{table}.branch_{branch}", spec["columns"])
        module = sources.parser(spec["parser"])
        results = checks(spec, rows, staged, sha, [(a, getattr(module, a)) for a in spec["audits"]])
    print(f"audit of {key}, branch {branch} (head {head}), main {_ref(spark, table, 'main')}:")
    if not _file_checks(spark, names, sha, head, results):
        print("audit: the results could NOT be recorded, so nothing may publish this file")
        return 1
    failed = [r[0] for r in results if not r[1]]
    print(f"audit: {'FAILED ' + ', '.join(failed) + '; main not moved, branch ' + branch + ' kept' if failed else 'passed'}")
    return 1 if failed else 0


def publish(spark, spec: dict, names: dict, key: str, landing) -> int:
    sha = object_sha(spec, key)
    table, branch = names["table"], branch_of(sha)
    head = _ref(spark, table, branch)
    found = spark.sql(f"SELECT check_name, passed, run_ts FROM {names['checks']} "
                      f"WHERE job = '{JOB}' AND scope = '{scope_of(names['logical'], sha, head)}'").collect()
    why = "the file has no branch" if head is None else unaudited(
        [(r["check_name"], r["passed"], r["run_ts"]) for r in found], [*GENERIC, *spec["audits"]])
    if why:
        print(f"publish refused at branch {branch} head {head}: {why}; main {_ref(spark, table, 'main')} not moved")
        return 1
    before = _ref(spark, table, "main")
    catalog, ident = table.split(".", 1)
    spark.sql(f"CALL {catalog}.system.fast_forward('{ident}', 'main', '{branch}')")
    spark.sql(f"ALTER TABLE {table} DROP BRANCH {branch}")
    print(f"publish: main {before} -> {_ref(spark, table, 'main')}, branch {branch} dropped")
    return 0


STEPS = {"load": load, "audit": audit, "publish": publish}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("fetch", *STEPS))
    ap.add_argument("--table", required=True, help="the file source's bronze table, as file_sources.json keys it")
    ap.add_argument("--object", default="", help="the landed key fetch printed (load, audit, publish)")
    ap.add_argument("--config", type=Path, default=CONFIG, help="default generated/file_sources.json")
    ap.add_argument("--namespace-map", default="", help="bronze=scratch,audit=scratch: rehearse on scratch tables")
    args = ap.parse_args(argv)

    specs = json.loads(args.config.read_text())
    if args.table not in specs:
        raise SystemExit(f"{args.table} is not a file source in {args.config}")
    spec, landing = specs[args.table], Landing()
    if args.step == "fetch":
        print(fetch(spec, landing))  # the last line: the DAG's fetch task returns it
        return 0
    object_sha(spec, args.object)  # a bad key fails before the lock and the JVM
    lock = acquire_lock(LOCK_PATH, wait_s=LOCK_WAIT_S)
    if lock is None:
        print(f"another lake writer held {LOCK_PATH} for {LOCK_WAIT_S}s; exit {EXIT_LOCK_HELD}, retry later",
              file=sys.stderr)
        return EXIT_LOCK_HELD
    from spark_conf import CATALOG, lake_session

    mapping = dict(pair.split("=", 1) for pair in args.namespace_map.split(",") if pair)

    def lake(name: str) -> str:
        ns, table = name.split(".")
        return f"{CATALOG}.{mapping.get(ns, ns)}.{table}"

    names = {"logical": args.table, "table": lake(args.table), "files": lake(SOURCE_FILES), "checks": lake(CHECKS)}
    spark = lake_session(f"k2-file-source-{args.step}")
    try:
        return STEPS[args.step](spark, spec, names, args.object, landing)
    finally:
        spark.stop()
        lock.close()


if __name__ == "__main__":
    sys.exit(main())

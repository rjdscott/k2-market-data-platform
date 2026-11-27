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
         lands too: the landing is the record of what arrived.

Exits: 0 done; 1 a task error; 2 refused (a sha256 or size mismatch, other bytes under the key).
The last line on stdout is the task's result as JSON, {"exit": <n>, ...}, for the DAG's record
task, which writes the run record (this task writes none).

The landing key is the Spark key, K2_SPARK_S3_ACCESS_KEY and K2_SPARK_S3_SECRET_KEY (A8): get and
put on the landing bucket, no delete. Nothing here prints a key.
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
EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
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


# --- The tasks: pure flows over a store and a lake --------------------------------------------


def load(run: Run, store, lake) -> tuple[int, dict]:
    """Lands the spooled file and logs it in landing_files."""
    record = json.loads((run.entry / "fetch.json").read_text())
    data = run.raw.read_bytes()
    try:
        sha = verify(record, data)
    except Refused as e:
        print(f"load refused: {e}; nothing landed", file=sys.stderr)
        return EXIT_REFUSED, {}
    parsed = (run.entry / "rows.jsonl").exists()
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
    return EXIT_OK, {"objectKey": uri, "bytes": len(data)}


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
        writer = frame.writeTo(rel(table))
        for k, v in props.items():
            writer = writer.option(f"snapshot-property.{k}", v)
        writer.append()


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


TASKS = {"load": load}


def main(argv: list[str] | None = None) -> int:
    task, run = parse_args(argv, lambda p: json.loads(p.read_text()))
    from spark_conf import lake_session

    spark = lake_session(f"k2-connector-{task}-{run.pipeline['fence']}")
    code, result = EXIT_ERROR, {}
    try:
        code, result = TASKS[task](run, Landing(), SparkLake(spark))
    finally:
        spark.stop()
        print(json.dumps({"exit": code, "task": task, "batchId": run.run_id, **result}))
    return code


if __name__ == "__main__":
    sys.exit(main())

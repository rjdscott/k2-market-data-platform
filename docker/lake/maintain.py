#!/usr/bin/env python3
"""Maintain, a Spark task: the daily expiry of a product's snapshots and old landings
(docs/development/phase-001-interfaces.md, "Expire": "daily maintenance expires by
compiled/lake/retention.json"; the owner's O6 in docs/research/2026-11-26-decisions-addendum.md).

    maintain.py --release <id> --product <domain>.<product> --run-id <dagId>/<runId>

1. Snapshots. For each table of the product (its bronze tables, their landing log and checks
   tables, its build outputs) retention.json gives the days, keyed `<warehouse>.<ns>.<table>`
   (a `<x>_build` catalog is warehouse `<x>`, `lake` is `k2`):

       CALL <catalog>.system.expire_snapshots(table => '<ns>.<table>',
                                              older_than => TIMESTAMP '<now UTC - days>', retain_last => 1)

   The table's refs (`main`, `audit`, `audited`) are read before and after; one that vanished
   fails the task, though Iceberg keeps a referenced snapshot. A build output that does not exist
   yet (Spark's TABLE_OR_VIEW_NOT_FOUND: no good build so far) is skipped as `not built yet`.
2. Landings. Objects under the product's own prefix `<domain>/<product>/` in k2-landing whose key's
   day, `<prefix><YYYY-MM-DD>/<sha256>.<ext>`, is more than LANDING_DAYS before today (UTC) are
   deleted, by the day in the key and never by mtime. Kept, and counted by reason: a key that does
   not parse (`malformed`), a file not in landing_files (`unlogged`), a file whose batch has no
   rows published on `main` (`unpublished`: a failed or waiting batch's file is evidence until an
   operator clears it), and one inside the window (`recent`).

It runs with the maintenance key, K2_MAINTENANCE_S3_ACCESS_KEY and K2_MAINTENANCE_S3_SECRET_KEY.
Deleting a landing needs s3:ListBucket on k2-landing and s3:DeleteObject on k2-landing/*, which the
key's policy must carry; without it the task exits 1 and says so.

Exits: 0 done; 1 an error (a ref vanished, a refused delete, a missing retention entry, any other).
The last line on stdout is the result as JSON, {"exit": <n>, "task": "maintain", "expired": {...},
"notBuilt": [...], "landingsDeleted": <n>, "landingsKept": {...}}, for the DAG's record task.
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
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from connector import (
    LANDING_BUCKET,
    PRODUCT_ID,
    RELEASE_ID,
    RELEASES,
    RUN_NAME,
    S3_ENDPOINT,
    S3_REGION,
    SparkLake,
    name,
    now,
    procedure_table,
    rel,
)

EXIT_OK, EXIT_ERROR = 0, 1
KEY_ENV = ("K2_MAINTENANCE_S3_ACCESS_KEY", "K2_MAINTENANCE_S3_SECRET_KEY")
LANDING_DAYS = 14  # O6, the owner's decision of 2026-11-26 (decisions-addendum): a rolling 14 days per product
RETAIN_LAST = 1  # Iceberg's own default, stated: the newest snapshot of a table is never expired
GRANT = ("the maintenance key lacks s3:ListBucket on arn:aws:s3:::k2-landing and s3:DeleteObject on "
         "arn:aws:s3:::k2-landing/*")
KEY_PART = re.compile(r"(\d{4}-\d{2}-\d{2})/([0-9a-f]{64})\.[a-z0-9]+")
NOT_FOUND = "TABLE_OR_VIEW_NOT_FOUND"  # Spark's error class for a relation that is not there


class Denied(Exception):
    """The store refused a call: the key lacks a grant."""


# --- The pure core -------------------------------------------------------------------------


def retention_key(relation: str) -> str:
    """`<warehouse>.<ns>.<table>`: `lake` is warehouse k2, a `<x>_build` catalog is warehouse `<x>`."""
    rel(relation)
    catalog, ns, table = relation.split(".")
    warehouse = "k2" if catalog == "lake" else catalog.removesuffix("_build")
    return f"{warehouse}.{ns}.{table}"


def tables(pipeline: dict) -> list[str]:
    """The relations whose snapshots maintain expires: the product's own, no other's."""
    out = []
    for b in pipeline.get("bronze", []):
        out += [b["table"], b["checks"]]
    if pipeline.get("landing"):
        out.append(pipeline["landing"]["log"])
    out += (pipeline.get("build") or {}).get("outputs", [])
    return list(dict.fromkeys(out))


def snapshot_plan(pipeline: dict, retention: dict, at: datetime) -> list[tuple[str, str, int]]:
    """`(relation, older_than, retain_last)` per table; a table without a retention entry is an error."""
    plan = []
    for relation in tables(pipeline):
        days = retention.get(retention_key(relation))
        if type(days) is not int or days < 1:
            raise ValueError(f"{relation}: retention.json has no whole number of days at {retention_key(relation)!r}")
        plan.append((relation, (at - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"), RETAIN_LAST))
    return plan


def statement(relation: str, older_than: str, retain_last: int) -> str:
    catalog, table = procedure_table(relation)
    if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", older_than) or type(retain_last) is not int:
        raise ValueError("older_than or retain_last is not what the plan makes")
    return (f"CALL {catalog}.{name('system')}.{name('expire_snapshots')}(table => {table}, "
            f"older_than => TIMESTAMP '{older_than}', retain_last => {retain_last})")


def landing_prefix(pipeline: dict) -> str:
    """The product's prefix, refused unless it is exactly `<domain>/<product>/` of this product."""
    landing = pipeline["landing"]
    want = f"{pipeline['domain']}/{pipeline['product'].partition('.')[2]}/"
    if landing["bucket"] != LANDING_BUCKET or landing["prefix"] != want or not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+/", want):
        raise ValueError(f"landing {landing['bucket']!r}/{landing['prefix']!r} is not {LANDING_BUCKET}/{want!r}")
    return want


def parse_key(prefix: str, key: str) -> tuple[date, str] | None:
    """(day, sha256) of a landing key under `prefix`, or None if it is not `<prefix><day>/<sha>.<ext>`."""
    if not key.startswith(prefix):
        return None
    m = KEY_PART.fullmatch(key[len(prefix):])
    try:
        return (date.fromisoformat(m.group(1)), m.group(2)) if m else None
    except ValueError:
        return None


# --- The flows, over a store and a lake -------------------------------------------------------


def not_found(e: Exception) -> bool:
    """Spark's AnalysisException for a relation that does not exist, by its error class (pyspark
    3.5's getErrorClass; the pure core imports no pyspark)."""
    get = getattr(e, "getErrorClass", None)
    return callable(get) and get() == NOT_FOUND


def expire_snapshots(pipeline: dict, retention: dict, lake, at: datetime) -> tuple[int, dict, list[str]]:
    """(exit, snapshots expired per table, the build outputs not built yet). A build output exists
    only after the product's first good build, so one that is not there has nothing to expire;
    every other table is made at deploy, and any other error reading a table is raised."""
    code, expired, unbuilt = EXIT_OK, {}, []
    outputs = set((pipeline.get("build") or {}).get("outputs", []))
    for relation, older_than, retain_last in snapshot_plan(pipeline, retention, at):
        try:
            before_refs = lake.refs(relation)
        except Exception as e:
            if relation not in outputs or not not_found(e):
                raise
            print(f"maintain: {relation}: not built yet")
            unbuilt.append(relation)
            continue
        before = lake.snapshot_count(relation)
        lake.run(statement(relation, older_than, retain_last))
        gone = set(before_refs) - set(lake.refs(relation))
        expired[relation] = before - lake.snapshot_count(relation)
        if gone:
            print(f"maintain FAILED: {relation}: refs {sorted(gone)} vanished in expiry", file=sys.stderr)
            code = EXIT_ERROR
        print(f"maintain: {relation}: {expired[relation]} snapshots expired (older than {older_than}, keep {retain_last})")
    return code, expired, unbuilt


def expire_landings(pipeline: dict, store, lake, at: datetime) -> tuple[int, int, dict]:
    """Deletes the product's landings past LANDING_DAYS whose batch is published; (exit, deleted, kept by reason)."""
    prefix, kept, deleted = landing_prefix(pipeline), {}, 0
    cutoff = at.astimezone(timezone.utc).date() - timedelta(days=LANDING_DAYS)
    table, log = pipeline["bronze"][0]["table"], pipeline["landing"]["log"]
    for key in store.list(prefix):
        parsed = parse_key(prefix, key)
        if parsed is None:
            reason = "malformed"
            print(f"maintain: {key} is not <prefix><day>/<sha256>.<ext>; kept")
        elif parsed[0] >= cutoff:
            reason = "recent"
        elif not lake.has_object(log, f"s3://{LANDING_BUCKET}/{key}"):
            reason = "unlogged"
        elif lake.published_rows(table, parsed[1]) < 1:
            reason = "unpublished"
            print(f"maintain: {key} has no published rows; kept until an operator clears it")
        else:
            try:
                store.delete(key)
            except Denied as e:
                print(f"maintain FAILED: delete of {key} refused: {e}", file=sys.stderr)
                return EXIT_ERROR, deleted, kept
            deleted += 1
            continue
        kept[reason] = kept.get(reason, 0) + 1
    return EXIT_OK, deleted, kept


# --- The shells: S3 and Spark ---------------------------------------------------------------


class Store:
    """The landing bucket over MinIO's S3 API, signed with the maintenance key, standard library only
    (connector.sign has no query string, which a listing needs)."""

    def __init__(self, bucket: str = LANDING_BUCKET):
        self.bucket = bucket
        self._access, self._secret = (os.environ[k] for k in KEY_ENV)

    def _call(self, method: str, key: str = "", query: dict | None = None):
        path = f"/{self.bucket}/{urllib.parse.quote(key)}" if key else f"/{self.bucket}"
        q = urllib.parse.urlencode(sorted((query or {}).items()), quote_via=lambda s, *a, **k: urllib.parse.quote(s, safe="-_.~"))
        payload = hashlib.sha256(b"").hexdigest()
        hdrs = {"host": urllib.parse.urlsplit(S3_ENDPOINT).netloc, "x-amz-date": now().strftime("%Y%m%dT%H%M%SZ"),
                "x-amz-content-sha256": payload}
        names = ";".join(sorted(hdrs))
        canonical = "\n".join([method, path, q, *(f"{k}:{hdrs[k]}" for k in sorted(hdrs)), "", names, payload])
        stamp = hdrs["x-amz-date"]
        scope = f"{stamp[:8]}/{S3_REGION}/s3/aws4_request"
        sk = ("AWS4" + self._secret).encode()
        for part in (stamp[:8], S3_REGION, "s3", "aws4_request"):
            sk = hmac.new(sk, part.encode(), hashlib.sha256).digest()
        text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        hdrs["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self._access}/{scope}, SignedHeaders={names}, "
                                 f"Signature={hmac.new(sk, text.encode(), hashlib.sha256).hexdigest()}")
        request = urllib.request.Request(S3_ENDPOINT + path + (f"?{q}" if q else ""), method=method, headers=hdrs)
        try:
            return urllib.request.urlopen(request, timeout=60)  # noqa: S310 - the configured object store
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise Denied(f"{method} {path}: AccessDenied; {GRANT}") from e
            raise

    def list(self, prefix: str):
        token = None
        while True:
            query = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            with self._call("GET", query=query) as response:
                root = ET.fromstring(response.read())
            ns = {"s": root.tag.partition("}")[0].lstrip("{")}
            yield from (e.text for e in root.findall("s:Contents/s:Key", ns))
            token = root.findtext("s:NextContinuationToken", namespaces=ns)
            if root.findtext("s:IsTruncated", namespaces=ns) != "true":
                return

    def delete(self, key: str) -> None:
        with self._call("DELETE", key):
            pass


class MaintainLake(SparkLake):
    def run(self, text: str) -> None:
        self.spark.sql(text).collect()

    def snapshot_count(self, table: str) -> int:
        return self._sql(f"SELECT count(*) FROM {rel(table)}.`snapshots`")[0][0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maintain.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default=os.environ.get("K2_RELEASE"))
    ap.add_argument("--product", required=True)
    ap.add_argument("--run-id", required=True, help="<dagId>/<runId>")
    a = ap.parse_args(argv)
    dag_id, _, run_name = a.run_id.partition("/")
    if not (a.release and RELEASE_ID.fullmatch(a.release) and PRODUCT_ID.fullmatch(a.product)
            and RUN_NAME.fullmatch(run_name)):
        ap.error("--release, --product or --run-id is not a plain name")
    root = RELEASES / a.release / "compiled"
    pipeline = json.loads((root / "products" / a.product / "pipeline.json").read_text())
    if dag_id != pipeline["dagId"]:
        ap.error(f"the run is not of DAG {pipeline['dagId']}")
    retention = json.loads((root / "lake" / "retention.json").read_text())
    at = now()
    snapshot_plan(pipeline, retention, at)  # refused before Spark starts
    if pipeline.get("landing"):
        landing_prefix(pipeline)
    from spark_conf import lake_session

    spark = lake_session(f"k2-maintain-{pipeline['fence']}", key=KEY_ENV,
                         catalogs=sorted({r.split(".")[0] for r in tables(pipeline)}))
    code, result = EXIT_ERROR, {}
    try:
        lake = MaintainLake(spark)
        code, expired, unbuilt = expire_snapshots(pipeline, retention, lake, at)
        result.update(expired=expired, notBuilt=unbuilt)
        if pipeline.get("landing") and pipeline.get("bronze"):
            lcode, deleted, kept = expire_landings(pipeline, Store(), lake, at)
            code = max(code, lcode)
            result.update(landingsDeleted=deleted, landingsKept=kept)
    except Exception as e:  # noqa: BLE001 - the last line must be the result
        print(f"maintain FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        code = EXIT_ERROR
    finally:
        spark.stop()
        print(json.dumps({"exit": code, "task": "maintain", **result}))
    return code


if __name__ == "__main__":
    sys.exit(main())

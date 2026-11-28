#!/usr/bin/env python3
"""Maintain, a Spark task: the daily expiry of a product's snapshots and old landings
(docs/development/phase-001-interfaces.md, "Expire": "daily maintenance expires by
compiled/lake/retention.json"; the owner's O6 in docs/research/2026-11-26-decisions-addendum.md),
and a Kafka product's compaction pass.

    maintain.py [--compact] --release <id> --product <domain>.<product> --run-id <dagId>/<runId>

Without --compact, with the maintenance key (PASS_KEYS), in this order:

1. Continuity, a Kafka product only. The landing holds every record of every range it planned,
   so per partition its offsets are unbroken over the whole table: max - min + 1 equals the
   distinct offsets plus those an accepted loss (`k2.kafka-gaps`, read from every snapshot the
   landing still holds) records inside the span (offsets.offset_gaps), and no offset twice. A
   break is a failed check, `offset_continuity`, in the result and exit 3, never silent.
2. Snapshots. For each table of the product (its bronze tables, their landing log, or a Kafka
   product's landing table, and checks tables, its build outputs) retention.json gives the days,
   keyed `<warehouse>.<ns>.<table>` (a `<x>_build` catalog is warehouse `<x>`, `lake` is `k2`):

       CALL <catalog>.system.expire_snapshots(table => '<ns>.<table>',
                                              older_than => TIMESTAMP '<now UTC - days>', retain_last => <n>)

   n is 1 but for a Kafka product's landing and bronze: there it is the depth of main's newest
   connector snapshot (the newest carrying `k2.kafka-offsets`, kafka.newest's resume point) in
   main's lineage, head first, so neither it nor a compaction snapshot the resume walk passes on
   the way to it is ever expired; without it the next run lands from the log start, or stages
   from offset 0, again. The table's refs (`main`, `audit`, `audited`) are read before and after;
   one that vanished fails the task, though Iceberg keeps a referenced snapshot. A build output
   that does not exist yet (Spark's TABLE_OR_VIEW_NOT_FOUND: no good build so far) is skipped as
   `not built yet`.
3. Orphans. Per table but those not built yet, files under its location that no snapshot holds and
   last modified more than ORPHAN_FLOOR (24 hours) ago are deleted (remove_orphan_files, over a
   listing made with the maintenance key: Iceberg 1.8.1 lists through Hadoop's FileSystem, which
   this image has none of for s3://). Never younger: a write in flight is not an orphan.
4. Landings, a file product only (a Kafka product's landing rows are never expired, owner
   2026-11-28). Objects under the product's own prefix `<domain>/<product>/` in k2-landing whose
   key's day, `<prefix><YYYY-MM-DD>/<sha256>.<ext>`, is more than LANDING_DAYS before today (UTC)
   are deleted, by the day in the key and never by mtime. Kept, and counted by reason: a key that
   does not parse (`malformed`), a file not in landing_files (`unlogged`), a file with a logged
   observation whose batch has no rows published on `main` (`unpublished`: a public API's object
   is every same-day observation of its bytes, each its own batch; a failed or waiting batch's file
   is evidence until an operator clears it), and one inside the window (`recent`).

With --compact, a Kafka product only, with the Spark key: its landing and bronze compacted
(rewrite_data_files, and the position deletes rewritten where a table has any), each commit's
summary carrying `k2.job` `maintenance`, never `connector`, so the resume walk passes over it. The
whole pass is skipped, the reason in the result, while bronze's `audit` holds a batch `main` does
not (a waiting or failed batch): its publish requires `main` to be the snapshot it was staged on
(connector.publish), which a compaction of `main` would move. A table under COMPACT_MIN_FILES small
files beyond one per partition is skipped too, so a five-minute cadence rewrites nothing needlessly.
The Spark key writes warehouse k2, where both tables live; the maintenance key writes nothing.

The maintenance key is K2_MAINTENANCE_S3_ACCESS_KEY and K2_MAINTENANCE_S3_SECRET_KEY: get, list and
delete in the warehouses, list and delete in k2-landing, no put. Deleting a landing needs
s3:ListBucket on k2-landing and s3:DeleteObject on k2-landing/*, which the key's policy must carry;
without it the task exits 1 and says so.

Exits: 0 done; 1 an error (a ref vanished, a refused delete, a missing retention entry, any other);
3 a failed check. The last line on stdout is the result as JSON, for the DAG's record task:
{"exit": <n>, "task": "maintain", "maintaining": {"product", "from", "to"}, "checks": [...],
"expired": {...}, "notBuilt": [...], "orphansRemoved": {...}, "landingsDeleted": <n>,
"landingsKept": {...}}, or with --compact {..., "compacted": {...}, "skipped": {...}}.
`maintaining` names the product and the window the pass ran in, for a freshness alert to skip.
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

import connector as C
import kafka
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
    string_literal,
)

import offsets as O

EXIT_OK, EXIT_ERROR, EXIT_FAILED = 0, 1, 3
KEY_ENV = ("K2_MAINTENANCE_S3_ACCESS_KEY", "K2_MAINTENANCE_S3_SECRET_KEY")
PASS_KEYS = {"maintain": KEY_ENV, "compact": C.KEY_ENV}  # compaction writes data files: the Spark key's put
LANDING_DAYS = 14  # O6, the owner's decision of 2026-11-26 (decisions-addendum): a rolling 14 days per product
RETAIN_LAST = 1  # Iceberg's own default, stated: the newest snapshot of a table is never expired
ORPHAN_FLOOR = timedelta(hours=24)  # plan 004: a file younger than this may be a write in flight
ORPHAN_VIEW = "k2_orphan_listing"  # remove_orphan_files' file_list_view: (file_path, last_modified)
JOB_COMPACTION = O.JOB_MAINTENANCE  # k2.job on a compaction commit: not kafka.JOB_CONNECTOR
SMALL_FILE_BYTES = 64 * 1024 * 1024
# ponytail: 48 is four hours of five-minute runs at one file each. Tune it from the soak's files per
# run and its delete-file and planning times (W0-12), or per partition if one table's days diverge.
COMPACT_MIN_FILES = 48
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


def is_kafka(pipeline: dict) -> bool:
    return (pipeline.get("connector") or {}).get("kind") == "kafka"


def tables(pipeline: dict) -> list[str]:
    """The relations whose snapshots maintain expires: the product's own, no other's."""
    out = []
    for b in pipeline.get("bronze", []):
        out += [b["table"], b["checks"]]
    if landing := pipeline.get("landing"):
        out.append(landing["table"] if is_kafka(pipeline) else landing["log"])
    out += (pipeline.get("build") or {}).get("outputs", [])
    return list(dict.fromkeys(out))


def resume_tables(pipeline: dict) -> list[str]:
    """A Kafka product's landing and bronze: the tables whose newest connector snapshot is a resume point."""
    return [pipeline["landing"]["table"], *(b["table"] for b in pipeline["bronze"])] if is_kafka(pipeline) else []


def connector_depth(lineage: list) -> int:
    """retain_last for a resume table: how many snapshots of main's lineage (`[summary]`, head
    first) reach back to and include the newest connector snapshot, by kafka.newest's own test, so
    every snapshot the resume walk passes and the one it stops at are kept. None there: RETAIN_LAST."""
    for depth, summary in enumerate(lineage, 1):
        if kafka.newest([(0, summary)]) is not None:
            return max(depth, RETAIN_LAST)
    return RETAIN_LAST


def continuity(topic: str, found: list, history: list) -> tuple[bool, str]:
    """Whole-table offset continuity over a Kafka landing. `found` is `[(partition, rows, offsets,
    first, last)]` over the table, `history` `[(committed_at, summary)]` over every snapshot it
    holds. A partition passes when its distinct offsets plus the accepted losses inside its span
    fill it (offsets.offset_gaps) and no offset is there twice. Losses never overlap: each starts
    at the end offset the one before it resumed from."""
    lost = kafka.accepted_losses(history, topic)

    def inside(p, lo, hi):
        return sum(max(0, min(last, hi) - max(first, lo) + 1) for first, last in lost.get(p, []))

    failures = [f"{f['scope']}: {f['detail']}" for f in O.offset_gaps(
        [(topic, p, d + inside(p, lo, hi), lo, hi) for p, _, d, lo, hi in found])]
    failures += [f"{topic}/{p}: {n - d} rows repeat an offset" for p, n, d, _, _ in found if n != d]
    if failures:
        return False, "; ".join(failures)
    accepted = sum(inside(p, lo, hi) for p, _, _, lo, hi in found)
    return True, (f"{sum(n for _, n, *_ in found)} rows over {len(found)} partitions, each unbroken"
                  + (f" but for {accepted} offsets of accepted losses" if accepted else ""))


def orphan_statement(relation: str, at: datetime) -> str:
    """remove_orphan_files over ORPHAN_VIEW, older than ORPHAN_FLOOR before `at` and never younger."""
    catalog, table = procedure_table(relation)
    older = (at.astimezone(timezone.utc) - ORPHAN_FLOOR).strftime("%Y-%m-%d %H:%M:%S")
    return (f"CALL {catalog}.{name('system')}.{name('remove_orphan_files')}(table => {table}, "
            f"older_than => TIMESTAMP '{older}', file_list_view => {string_literal(ORPHAN_VIEW)})")


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
    resume = resume_tables(pipeline)
    for relation, older_than, retain_last in snapshot_plan(pipeline, retention, at):
        try:
            before_refs = lake.refs(relation)
        except Exception as e:
            if relation not in outputs or not not_found(e):
                raise
            print(f"maintain: {relation}: not built yet")
            unbuilt.append(relation)
            continue
        if relation in resume:
            retain_last = connector_depth(lake.lineage(relation))
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
    """Deletes the product's landings past LANDING_DAYS whose batches are all published; (exit, deleted,
    kept by reason). A public API's object is one key for every observation of the same bytes that day
    (A, B, A: two rows in the log), each its own batch at its fetched_at (connector.py), so it goes only
    once every logged observation has rows published; a file's same bytes again stage nothing, so its
    sha256 alone is the batch."""
    prefix, kept, deleted = landing_prefix(pipeline), {}, 0
    cutoff = at.astimezone(timezone.utc).date() - timedelta(days=LANDING_DAYS)
    table, log = pipeline["bronze"][0]["table"], pipeline["landing"]["log"]
    api = pipeline["connector"]["kind"] == "public-api"  # connector.Run.api
    for key in store.list(prefix):
        parsed = parse_key(prefix, key)
        if parsed is None:
            reason = "malformed"
            print(f"maintain: {key} is not <prefix><day>/<sha256>.<ext>; kept")
        elif parsed[0] >= cutoff:
            reason = "recent"
        elif not (seen := lake.observations(log, f"s3://{LANDING_BUCKET}/{key}")):
            reason = "unlogged"
        elif any(lake.published_rows(table, parsed[1], fetched if api else None) < 1 for fetched in seen):
            reason = "unpublished"
            print(f"maintain: {key} has an observation with no published rows; kept until an operator clears it")
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


def check_continuity(pipeline: dict, lake) -> dict:
    """The landing's offset_continuity check, as the result lists it."""
    landing = pipeline["landing"]["table"]
    passed, detail = continuity(pipeline["connector"]["topic"], lake.offset_counts(landing), lake.summaries(landing))
    print(f"maintain: {'pass' if passed else 'FAILED'} offset_continuity {landing}: {detail}",
          file=sys.stdout if passed else sys.stderr)
    return {"check": "offset_continuity", "table": landing, "passed": passed, "detail": detail}


def maintain(pipeline: dict, retention: dict, lake, store, at: datetime) -> tuple[int, dict]:
    """The maintenance key's pass, steps 1 to 4 of the module's docstring: (exit, result)."""
    code, result = EXIT_OK, {"maintaining": {"product": pipeline["product"], "from": at.isoformat()}}
    if is_kafka(pipeline):  # before expiry, which may take a snapshot recording an accepted loss
        result["checks"] = [check_continuity(pipeline, lake)]
        code = EXIT_OK if result["checks"][0]["passed"] else EXIT_FAILED
    ecode, expired, unbuilt = expire_snapshots(pipeline, retention, lake, at)
    code = max(code, ecode)
    result.update(expired=expired, notBuilt=unbuilt, orphansRemoved={
        r: lake.remove_orphans(r, orphan_statement(r, at)) for r in tables(pipeline) if r not in unbuilt})
    if pipeline.get("landing") and pipeline.get("bronze") and not is_kafka(pipeline):
        lcode, deleted, kept = expire_landings(pipeline, store, lake, at)
        code = max(code, lcode)
        result.update(landingsDeleted=deleted, landingsKept=kept)
    return code, result


def compact(pipeline: dict, lake, at: datetime) -> tuple[int, dict]:
    """The Spark key's pass over a Kafka product's landing and bronze: (exit, result)."""
    [bronze] = pipeline["bronze"]
    work = [pipeline["landing"]["table"], bronze["table"]]
    result = {"maintaining": {"product": pipeline["product"], "from": at.isoformat()}, "compacted": {}, "skipped": {}}
    pending = C.pending_batch(lake, bronze["table"], lake.refs(bronze["table"]))
    if pending is not None:
        why = (f"{C.AUDIT} of {bronze['table']} holds batch {pending}, unpublished: its publish requires main to "
               "be the snapshot it was staged on, which a compaction would move")
        print(f"maintain: compaction skipped: {why}")
        result["skipped"] = dict.fromkeys(work, why)
        return EXIT_OK, result
    props = {O.JOB: JOB_COMPACTION}
    for relation in work:
        files, partitions, deletes = lake.file_counts(relation)
        if files - partitions < COMPACT_MIN_FILES and not deletes:
            result["skipped"][relation] = (f"{files - partitions} small files beyond one per partition, "
                                           f"under {COMPACT_MIN_FILES}")
            continue
        result["compacted"][relation] = lake.compact(relation, props, rewrite_deletes=deletes > 0)
        print(f"maintain: {relation} compacted: {result['compacted'][relation]}")
    return EXIT_OK, result


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

    def listing(self, prefix: str):
        """`(key, last modified)` of every object under `prefix`."""
        token = None
        while True:
            query = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            with self._call("GET", query=query) as response:
                root = ET.fromstring(response.read())
            ns = {"s": root.tag.partition("}")[0].lstrip("{")}
            yield from ((e.findtext("s:Key", namespaces=ns),
                         datetime.fromisoformat(e.findtext("s:LastModified", namespaces=ns).replace("Z", "+00:00")))
                        for e in root.findall("s:Contents", ns))
            token = root.findtext("s:NextContinuationToken", namespaces=ns)
            if root.findtext("s:IsTruncated", namespaces=ns) != "true":
                return

    def list(self, prefix: str):
        return (key for key, _ in self.listing(prefix))

    def delete(self, key: str) -> None:
        with self._call("DELETE", key):
            pass


class MaintainLake(SparkLake):
    def run(self, text: str) -> None:
        self.spark.sql(text).collect()

    def observations(self, log: str, uri: str) -> list:
        """`[fetched_at]` of each row the landing log holds for `uri`: one per observation."""
        return [r[0] for r in self._sql(f"SELECT `fetched_at` FROM {rel(log)} WHERE `object_key` = :uri", uri=uri)]

    def snapshot_count(self, table: str) -> int:
        return self._sql(f"SELECT count(*) FROM {rel(table)}.`snapshots`")[0][0]

    def lineage(self, table: str) -> list:
        """`[summary]` of main's lineage, head first; main read before the snapshots, as catalog.main_snapshots."""
        head = self.refs(table).get("main")
        rows = self._sql(f"SELECT `snapshot_id`, `parent_id`, `summary` FROM {rel(table)}.`snapshots`")
        summaries = {s: dict(m or {}) for s, _, m in rows}
        return [summaries[s] for s in O.main_lineage({s: p for s, p, _ in rows}, head)]

    def summaries(self, table: str) -> list:
        """`[(committed_at, summary)]` over every snapshot the table holds, main's or not."""
        return [(ts, dict(m or {})) for ts, m in self._sql(f"SELECT `committed_at`, `summary` FROM {rel(table)}.`snapshots`")]

    def offset_counts(self, table: str) -> list:
        return self._sql("SELECT `kafka_partition`, count(*), count(DISTINCT `kafka_offset`), min(`kafka_offset`), "
                         f"max(`kafka_offset`) FROM {rel(table)} GROUP BY `kafka_partition`")

    def remove_orphans(self, table: str, text: str) -> int:
        """Runs `text` over a listing of the table's location made with the maintenance key."""
        # ponytail: the listing passes through the driver; past a few million files per table, list
        # per directory into a DataFrame instead.
        [location] = [r[1] for r in self._sql(f"DESCRIBE TABLE EXTENDED {rel(table)}") if r[0] == "Location"]
        bucket, _, prefix = location.removeprefix("s3://").partition("/")
        files = [(f"s3://{bucket}/{key}", at) for key, at in Store(bucket).listing(prefix.rstrip("/") + "/")]
        self.spark.createDataFrame(files, "file_path string, last_modified timestamp").createOrReplaceTempView(ORPHAN_VIEW)
        removed = len(self.spark.sql(text).collect())
        print(f"maintain: {table}: {removed} orphan files removed of {len(files)} listed")
        return removed

    def file_counts(self, table: str) -> tuple[int, int, int]:
        """(data files under SMALL_FILE_BYTES, the partitions they are in, delete files) on main."""
        part = "`partition`" if "partition" in self.spark.table(f"{rel(table)}.`data_files`").columns else "1"
        [(files, partitions)] = self._sql(f"SELECT count(*), count(DISTINCT {part}) FROM {rel(table)}.`data_files` "
                                          "WHERE `file_size_in_bytes` < :b", b=SMALL_FILE_BYTES)
        return files, partitions, self._sql(f"SELECT count(*) FROM {rel(table)}.`delete_files`")[0][0]

    def compact(self, table: str, props: dict, rewrite_deletes: bool) -> dict:
        """Iceberg's actions, not the procedures: only an action takes snapshot properties."""
        jvm, js = self.spark._jvm, self.spark._jsparkSession
        target = jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(js, rel(table))
        actions = jvm.org.apache.iceberg.spark.actions.SparkActions.get(js)
        steps = [(actions.rewriteDataFiles(target), ("rewrittenDataFilesCount", "addedDataFilesCount"))]
        if rewrite_deletes:
            steps.append((actions.rewritePositionDeletes(target), ("rewrittenDeleteFilesCount", "addedDeleteFilesCount")))
        out = {}
        for step, counts in steps:
            for k, v in props.items():
                step = step.snapshotProperty(k, v)
            r = step.execute()
            out.update({c: getattr(r, c)() for c in counts})
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maintain.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default=os.environ.get("K2_RELEASE"))
    ap.add_argument("--product", required=True)
    ap.add_argument("--run-id", required=True, help="<dagId>/<runId>")
    ap.add_argument("--compact", action="store_true", help="a kafka product's compaction pass, with the Spark key")
    a = ap.parse_args(argv)
    dag_id, _, run_name = a.run_id.partition("/")
    if not (a.release and RELEASE_ID.fullmatch(a.release) and PRODUCT_ID.fullmatch(a.product)
            and RUN_NAME.fullmatch(run_name)):
        ap.error("--release, --product or --run-id is not a plain name")
    root = RELEASES / a.release / "compiled"
    pipeline = json.loads((root / "products" / a.product / "pipeline.json").read_text())
    if dag_id != pipeline["dagId"]:
        ap.error(f"the run is not of DAG {pipeline['dagId']}")
    if a.compact and not is_kafka(pipeline):
        ap.error("--compact is for a kafka product")
    retention = json.loads((root / "lake" / "retention.json").read_text())
    at = now()
    if not a.compact:  # refused before Spark starts
        snapshot_plan(pipeline, retention, at)
        if pipeline.get("landing") and not is_kafka(pipeline):
            landing_prefix(pipeline)
    from spark_conf import lake_session

    work = resume_tables(pipeline) if a.compact else tables(pipeline)
    spark = lake_session(f"k2-{'compact' if a.compact else 'maintain'}-{pipeline['fence']}",
                         key=PASS_KEYS["compact" if a.compact else "maintain"],
                         catalogs=sorted({r.split(".")[0] for r in work}))
    code, result = EXIT_ERROR, {}
    try:
        lake = MaintainLake(spark)
        code, result = (compact(pipeline, lake, at) if a.compact else
                        maintain(pipeline, retention, lake, None if is_kafka(pipeline) else Store(), at))
    except Exception as e:  # noqa: BLE001 - the last line must be the result
        print(f"maintain FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        code = EXIT_ERROR
    finally:
        spark.stop()
        window = {"product": pipeline["product"], "from": at.isoformat(), "to": now().isoformat()}
        print(json.dumps({"exit": code, "task": "maintain", **result, "maintaining": window}))
    return code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Expire, a Spark task: the snapshots a run rolled back, by id (docs/development/
phase-001-interfaces.md, "Expire"; the release spine spikes, (b)).

    expire.py --release <id> --product <domain>.<product> --run-id <dagId>/<runId> --expire '<json>'

--expire is the dbt wrapper's XCom list, `[{"table": "<relation>", "ids": [<snapshot id>, ...]}]`;
each table must be one of the product's build outputs in its compiled pipeline.json. Per table:

    CALL <catalog>.system.expire_snapshots(table => '<ns>.<table>', snapshot_ids => array(<id>L, ...),
                                           older_than => TIMESTAMP '1970-01-01 00:00:00')

older_than the epoch, so the table's own maximum snapshot age takes nothing else; and the task
fails unless the procedure's deleted_manifest_lists_count is at least the number of ids, since a
key that cannot delete reports success with zeros and leaves the files orphaned.

It runs with the maintenance key, K2_MAINTENANCE_S3_ACCESS_KEY and K2_MAINTENANCE_S3_SECRET_KEY
(get and delete in the warehouses, no put). Exits: 0 expired (or nothing to expire); 1 a short
deleted count, a table or id not the product's, or any other error. The last line on stdout is
the result as JSON, {"exit": <n>, "task": "expire", "expired": [...]}, for the DAG's record task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from connector import PRODUCT_ID, RELEASE_ID, RELEASES, RUN_NAME, name, procedure_table, snapshot_id

EXIT_OK, EXIT_ERROR = 0, 1
KEY_ENV = ("K2_MAINTENANCE_S3_ACCESS_KEY", "K2_MAINTENANCE_S3_SECRET_KEY")
EPOCH = "TIMESTAMP '1970-01-01 00:00:00'"  # the session zone is UTC (spark_conf.py)


def plan(pipeline: dict, entries: list) -> list[tuple[str, list[int]]]:
    """`(relation, ids)` per entry, refused unless the relation is a build output of this
    product and the ids are distinct integers."""
    outputs = set((pipeline.get("build") or {}).get("outputs", []))
    out = []
    for entry in entries:
        table, ids = entry["table"], [snapshot_id(i) for i in entry["ids"]]
        if table not in outputs:
            raise ValueError(f"{table!r} is not a build output of {pipeline['product']}")
        if not ids or len(set(ids)) != len(ids):
            raise ValueError(f"{table}: ids {ids} are empty or repeated")
        out.append((table, ids))
    return out


def statement(relation: str, ids: list[int]) -> str:
    catalog, table = procedure_table(relation)
    array = ", ".join(f"{snapshot_id(i)}L" for i in ids)
    return (f"CALL {catalog}.{name('system')}.{name('expire_snapshots')}(table => {table}, "
            f"snapshot_ids => array({array}), older_than => {EPOCH})")


def expire(pipeline: dict, entries: list, run_sql) -> tuple[int, list[dict]]:
    """Runs each table's statement through `run_sql` (text -> the procedure's one row as a dict)
    and holds its deleted manifest lists to the ids given."""
    code, done = EXIT_OK, []
    for relation, ids in plan(pipeline, entries):
        row = run_sql(statement(relation, ids))
        deleted = row["deleted_manifest_lists_count"]
        done.append({"table": relation, "ids": ids, "deletedManifestLists": deleted})
        if deleted < len(ids):
            print(f"expire FAILED: {relation}: {deleted} manifest lists deleted for {len(ids)} ids; "
                  "the key could not delete, or a snapshot was not there", file=sys.stderr)
            code = EXIT_ERROR
        else:
            print(f"expire: {relation}: {len(ids)} snapshots, {deleted} manifest lists deleted")
    return code, done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="expire.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default=os.environ.get("K2_RELEASE"))
    ap.add_argument("--product", required=True)
    ap.add_argument("--run-id", required=True, help="<dagId>/<runId>")
    ap.add_argument("--expire", default="[]", help="the wrapper's expire list, JSON")
    a = ap.parse_args(argv)
    dag_id, _, run_name = a.run_id.partition("/")
    if not (a.release and RELEASE_ID.fullmatch(a.release) and PRODUCT_ID.fullmatch(a.product)
            and RUN_NAME.fullmatch(run_name)):
        ap.error("--release, --product or --run-id is not a plain name")
    pipeline = json.loads((RELEASES / a.release / "compiled" / "products" / a.product / "pipeline.json").read_text())
    if dag_id != pipeline["dagId"]:
        ap.error(f"the run is not of DAG {pipeline['dagId']}")
    entries = json.loads(a.expire)
    work = plan(pipeline, entries)  # refused before Spark starts
    if not work:
        print(json.dumps({"exit": EXIT_OK, "task": "expire", "expired": []}))
        return EXIT_OK
    from spark_conf import lake_session

    # The session holds a catalog per relation's catalog (lake over k2, <domain>_build over the
    # domain's warehouse) and signs with the maintenance key, not Spark's (spark_conf.py, W1-3).
    # ponytail: the maintenance pair and Spark's both sit in the Spark container's environment, so
    # any process there sees both; the scheduler never holds a storage key. Ceiling: a product that
    # runs code in Spark; then expire moves to its own maintenance container.
    spark = lake_session(f"k2-expire-{pipeline['fence']}", key=KEY_ENV,
                         catalogs=sorted({r.split(".")[0] for r, _ in work}))
    code, done = EXIT_ERROR, []
    try:
        code, done = expire(pipeline, entries, lambda text: spark.sql(text).collect()[0].asDict())
    finally:
        spark.stop()
        print(json.dumps({"exit": code, "task": "expire", "expired": done}))
    return code


if __name__ == "__main__":
    sys.exit(main())

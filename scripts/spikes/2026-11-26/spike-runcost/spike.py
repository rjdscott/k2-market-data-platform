"""Run-cost spike: the fixed cost of one small branch-write-audit-publish on the lake.

    T0=<epoch> python3 spike.py run <table>        one timed run, prints a JSON line
    python3 spike.py setup                          namespace + table a (1 seed snapshot) + table b
    python3 spike.py grow <table> <n> <max_s>       append 1,000-row snapshots to main until n or max_s
    python3 spike.py teardown                       drop both tables and the namespace, list what is left

Writes only lake.spike_runcost_20261126.*; never takes the writer lock (lock.py).
The branch pattern is docker/lake/file_source.py's (feat/file-source-pattern): CREATE OR
REPLACE BRANCH, append to <table>.branch_<b>, audit the branch, fast_forward, DROP BRANCH.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, "/home/iceberg/lake")
from spark_conf import CATALOG, lake_session  # noqa: E402

NS = f"{CATALOG}.spike_runcost_20261126"
BRANCH = "wap_spike"
SCHEMA = "id bigint, batch_id bigint, ts timestamp, symbol string, price decimal(18,8), qty decimal(18,8)"


def batch_rows():
    """1,000 synthetic rows built in Python, as a connector would hand them over."""
    batch = time.time_ns() // 1_000_000
    now = datetime.now(timezone.utc)
    rows = [(batch * 1000 + i, batch, now, f"SYN{i % 10}", Decimal(100 + i) / 7, Decimal(i + 1) / 3)
            for i in range(1000)]
    return batch, rows


def run(spark, table, t, t0):
    t["session"] = time.time() - t0
    T = f"{NS}.{table}"

    def step(name, fn):
        s = time.perf_counter()
        out = fn()
        t[name] = time.perf_counter() - s
        return out

    step("create_branch", lambda: spark.sql(f"ALTER TABLE {T} CREATE OR REPLACE BRANCH {BRANCH}"))
    batch, rows = batch_rows()
    step("append_branch", lambda: spark.createDataFrame(rows, SCHEMA).writeTo(f"{T}.branch_{BRANCH}")
         .option("snapshot-property.k2.job", "spike_runcost").append())
    n = step("audit_count", lambda: spark.sql(
        f"SELECT count(*) FROM {T}.branch_{BRANCH} WHERE batch_id = {batch}").collect()[0][0])
    dup = step("audit_unique", lambda: spark.sql(
        f"SELECT count(*) FROM (SELECT id FROM {T}.branch_{BRANCH} GROUP BY id HAVING count(*) > 1)").collect()[0][0])
    assert n == 1000 and dup == 0, (n, dup)
    ident = T.split(".", 1)[1]
    step("fast_forward", lambda: spark.sql(f"CALL {CATALOG}.system.fast_forward('{ident}', 'main', '{BRANCH}')"))
    step("drop_branch", lambda: spark.sql(f"ALTER TABLE {T} DROP BRANCH {BRANCH}"))
    t["snapshots_after"] = spark.sql(f"SELECT count(*) FROM {T}.snapshots").collect()[0][0]


def main():
    t0 = float(os.environ.get("T0", time.time()))
    mode = sys.argv[1]
    spark = lake_session(f"spike-runcost-{mode}", driver_memory=os.environ["K2_LAKE_DRIVER_MEMORY"])
    spark.sql(f"SHOW NAMESPACES IN {CATALOG}").collect()  # the REST catalog is loaded lazily; load it now
    t = {"mode": mode, "app": spark.sparkContext.applicationId}
    try:
        if mode == "run":
            t["table"] = sys.argv[2]
            run(spark, sys.argv[2], t, t0)
        elif mode == "setup":
            spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {NS}")
            for name in ("a", "b"):
                spark.sql(f"CREATE TABLE IF NOT EXISTS {NS}.{name} ({SCHEMA}) USING iceberg")
            _, rows = batch_rows()
            spark.createDataFrame(rows, SCHEMA).writeTo(f"{NS}.a").append()  # a branch needs a snapshot to branch from
        elif mode == "grow":
            table, want, max_s = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
            T, s = f"{NS}.{table}", time.time()
            have = spark.sql(f"SELECT count(*) FROM {T}.snapshots").collect()[0][0]
            while have < want and time.time() - s < max_s:
                _, rows = batch_rows()
                spark.createDataFrame(rows, SCHEMA).writeTo(T).append()
                have += 1
            t["snapshots"] = spark.sql(f"SELECT count(*) FROM {T}.snapshots").collect()[0][0]
            t["grow_s"] = time.time() - s
        elif mode == "teardown":
            for name in ("a", "b"):
                spark.sql(f"DROP TABLE IF EXISTS {NS}.{name}")  # no PURGE: Lakekeeper refuses it (spark_conf.py)
            spark.sql(f"DROP NAMESPACE IF EXISTS {NS}")
            t["namespaces_left"] = [r[0] for r in spark.sql(f"SHOW NAMESPACES IN {CATALOG}").collect()]
    finally:
        s = time.perf_counter()
        spark.stop()
        t["stop"] = time.perf_counter() - s
        t["total"] = time.time() - t0
        print("RESULT " + json.dumps(t, default=str), flush=True)


if __name__ == "__main__":
    main()

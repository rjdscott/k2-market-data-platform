"""Runs inside a throwaway container of the live Spark image (k2-spark-iceberg, Iceberg 1.8.1).

    python3 - '<plan json>' < expire.py

One Spark catalog per scoped MinIO key (`p0`..`p4`, keys from SPIKE_AK_<CAT>/SPIKE_SK_<CAT>), all over
the same Lakekeeper warehouse; then the plan's steps in order. Prints one `RESULT {json}` line per step.
"""

import json
import os
import sys

from pyspark.sql import SparkSession

plan = json.loads(sys.argv[1])
ns, tbl = plan["namespace"], plan["table"]
b = (SparkSession.builder.master("local[1]").appName("spike-n04plat-b")
     .config("spark.driver.memory", "512m")
     .config("spark.ui.enabled", "false")
     .config("spark.eventLog.enabled", "false")
     .config("spark.sql.session.timeZone", "UTC")
     .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"))
for cat in plan["catalogs"]:
    c = f"spark.sql.catalog.{cat}"
    b = (b.config(c, "org.apache.iceberg.spark.SparkCatalog")
         .config(f"{c}.type", "rest")
         .config(f"{c}.uri", plan["uri"])
         .config(f"{c}.warehouse", plan["warehouse"])
         .config(f"{c}.cache-enabled", "false")
         .config(f"{c}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
         .config(f"{c}.s3.endpoint", "http://minio:9000")
         .config(f"{c}.s3.path-style-access", "true")
         .config(f"{c}.s3.region", "local-01")
         .config(f"{c}.s3.access-key-id", os.environ[f"SPIKE_AK_{cat.upper()}"])
         .config(f"{c}.s3.secret-access-key", os.environ[f"SPIKE_SK_{cat.upper()}"]))
spark = b.getOrCreate()
for step in plan["steps"]:
    cat = step["catalog"]
    if step["kind"] == "expire":
        older = ", older_than => TIMESTAMP '1970-01-01 00:00:00'" if step.get("older_than_epoch") else ""
        sql = (f"CALL {cat}.system.expire_snapshots(table => '{ns}.{tbl}', "
               f"snapshot_ids => array({step['id']}L){older})")  # procedure arguments take literals only
    else:  # max_age_1ms
        sql = f"ALTER TABLE {cat}.{ns}.{tbl} SET TBLPROPERTIES ('history.expire.max-snapshot-age-ms' = '1')"
    out = {"step": step}
    try:
        out["rows"] = [r.asDict() for r in spark.sql(sql).collect()]
        out["ok"] = True
    except Exception as e:  # the finding is the failure mode, so keep its first line
        out["ok"] = False
        lines = str(e).strip().splitlines()
        out["error"] = next((ln.strip(": \t") for ln in lines[1:] if "Exception" in ln), lines[0])[:400]
    # Listed through p4, the full key: a metadata-table scan reads the table's metadata file itself.
    out["snapshots"] = [r[0] for r in spark.sql(f"SELECT snapshot_id FROM p4.{ns}.{tbl}.snapshots").collect()]
    print("RESULT " + json.dumps(out, default=str), flush=True)
spark.stop()

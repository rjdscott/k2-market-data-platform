"""The run-path scripts' two steps in the Spark container (scripts/lib/run-path-lib.sh), on the
release's own session builder, fed on stdin: `python3 - /k2/releases/<id> <product> <command>`.

    facts  landing_files and audit_checks of the product, as JSON on the last line (no Trino rule
           reads them: they are Spark's tables)
    clear  the operator's clearing step after a failed batch: `audit` reset to main, the statement
           the connector's own SparkLake.reset_branch runs when maintenance moved main on
"""
import json
import sys
from pathlib import Path

release, product, command = sys.argv[1:4]
sys.path.insert(0, release + "/platform/docker/lake")
from spark_conf import lake_session  # noqa: E402

p = json.loads((Path(release) / "compiled/products" / product / "pipeline.json").read_text())
spark = lake_session(f"run-path-{command}")
if command == "facts":
    def q(sql):
        return [list(r) for r in spark.sql(sql).collect()]

    print(json.dumps({"landing": q(f"SELECT object_key, sha256, batch_id, parsed FROM {p['landing']['log']} ORDER BY landed_at"),
                      "checks": q(f"SELECT batch_id, check_name, passed, snapshot_id FROM {p['bronze'][0]['checks']} ORDER BY checked_at")},
                     default=str))
elif command == "clear":
    table = p["bronze"][0]["table"]
    main = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'").collect()[0][0]
    statement = f"ALTER TABLE {table} REPLACE BRANCH audit AS OF VERSION {main}"
    spark.sql(statement)
    print(statement)
else:
    sys.exit(f"run_path_spark.py: no command {command}")
spark.stop()

#!/usr/bin/env python3
"""
K2 v3 — the one Spark session builder for the Iceberg lake.

Every v3 Spark job (raw ingest, bronze build, maintenance) gets its session from
`lake_session()`, so the `lake` catalog config lives here and nowhere else. v2's
`docker/offload/offload_generic.py` keeps building its own `k2` hadoop-catalog
session until Phase D retires that path — the two coexist deliberately.

Smoke test (the runnable check for this file):

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
"""

import os
import sys

from pyspark.sql import SparkSession

# Fixed for this stack — one host, one catalog, one bucket. Only the credentials
# vary, and those come from the environment.
CATALOG = "lake"
CATALOG_URI = "http://lakekeeper:8181/catalog"
WAREHOUSE = "k2"
S3_ENDPOINT = "http://minio:9000"
S3_REGION = "local-01"


def lake_session(app_name: str) -> SparkSession:
    """Spark session wired to the Lakekeeper REST catalog over MinIO."""
    access_key = os.environ["MINIO_ROOT_USER"]
    secret_key = os.environ["MINIO_ROOT_PASSWORD"]

    return (
        SparkSession.builder.appName(app_name)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(f"spark.sql.catalog.{CATALOG}", "org.apache.iceberg.spark.SparkCatalog")
        .config(f"spark.sql.catalog.{CATALOG}.type", "rest")
        .config(f"spark.sql.catalog.{CATALOG}.uri", CATALOG_URI)
        .config(f"spark.sql.catalog.{CATALOG}.warehouse", WAREHOUSE)
        .config(f"spark.sql.catalog.{CATALOG}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config(f"spark.sql.catalog.{CATALOG}.s3.endpoint", S3_ENDPOINT)
        .config(f"spark.sql.catalog.{CATALOG}.s3.path-style-access", "true")
        .config(f"spark.sql.catalog.{CATALOG}.s3.access-key-id", access_key)
        .config(f"spark.sql.catalog.{CATALOG}.s3.secret-access-key", secret_key)
        .config(f"spark.sql.catalog.{CATALOG}.s3.region", S3_REGION)
        .config("spark.sql.defaultCatalog", CATALOG)
        .getOrCreate()
    )


def _smoke() -> None:
    """Round-trip one row through the catalog: create, append, read, drop.

    Asserts the snapshot summary carries the write's `snapshot-property.*` — that
    is the mechanism ADR-018 relies on to store Kafka offsets atomically with the
    data commit, so it is the part worth proving before Phase C builds on it.
    """
    spark = lake_session("k2-lake-smoke")
    # No PURGE on the drops: Lakekeeper 0.13.3 answers a purge-drop with
    # `BadRequestException: Table does not exist ... at location <metadata.json>`
    # (verified 2026-08-26). It expires dropped tables through its own task queue,
    # so a plain DROP is both what works and what we want.
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.audit")
    spark.sql(f"DROP TABLE IF EXISTS {CATALOG}.audit.smoke")
    spark.sql(f"CREATE TABLE {CATALOG}.audit.smoke (id bigint, note string) USING iceberg")

    (
        spark.createDataFrame([(1, "smoke")], "id bigint, note string")
        .writeTo(f"{CATALOG}.audit.smoke")
        .option("snapshot-property.k2.smoke", "1")
        .append()
    )

    count = spark.sql(f"SELECT count(*) FROM {CATALOG}.audit.smoke").collect()[0][0]
    summary = spark.sql(
        f"SELECT summary FROM {CATALOG}.audit.smoke.snapshots "
        "ORDER BY committed_at DESC LIMIT 1"
    ).collect()[0][0]

    print(f"count={count}")
    print(f"summary={summary}")

    assert count == 1, f"expected 1 row, got {count}"
    assert summary.get("k2.smoke") == "1", f"snapshot summary lost k2.smoke: {summary}"

    spark.sql(f"DROP TABLE {CATALOG}.audit.smoke")
    print("✓ lake smoke passed")
    spark.stop()


if __name__ == "__main__":
    if "--smoke" not in sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    _smoke()

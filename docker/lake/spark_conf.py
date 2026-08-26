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

# Every endpoint, region, path-style flag and catalog URI is read from the
# environment with today's single-host value as the default. That is not
# speculative config: it is the requirement from
# docs/research/2026-08-26-v3-requirements-clarification.md Q9 — moving this
# lake to S3 + Glue/Lakekeeper-on-ECS must be a change to the environment, not a
# change to these files. Defaults keep `python3 spark_conf.py --smoke` working
# with no environment at all.
#
# The defaults must match LK / WAREHOUSE / BUCKET in docker/lake/init-lake.sh,
# which reads the same four names with the same defaults. That script creates
# what this one connects to; override one side only and every job points at a
# catalog nothing bootstrapped.
CATALOG = os.environ.get("K2_LAKE_CATALOG", "lake")
CATALOG_URI = os.environ.get("K2_LAKE_CATALOG_URI", "http://lakekeeper:8181/catalog")
WAREHOUSE = os.environ.get("K2_LAKE_WAREHOUSE", "k2")
S3_ENDPOINT = os.environ.get("K2_S3_ENDPOINT", "http://minio:9000")
S3_REGION = os.environ.get("K2_S3_REGION", "local-01")
# Path-style on MinIO, virtual-hosted on real S3. String, not bool: it goes
# straight into a Spark conf value.
S3_PATH_STYLE = os.environ.get("K2_S3_PATH_STYLE", "true")

# Redpanda's schema registry. Stage 2 fetches Avro schemas by id from here.
SCHEMA_REGISTRY_URL = os.environ.get("K2_SCHEMA_REGISTRY_URL", "http://redpanda:8081")
KAFKA_BROKERS = os.environ.get("K2_BROKERS", "redpanda:9092")

# Driver heap, pinned rather than inherited. `spark-iceberg` is capped at 2 CPU
# and 4 GiB, and during the v2/v3 parallel window TWO drivers can be alive in it
# at once: v2's iceberg-offload every 15 minutes and v3's lake ingest every 5.
# They no longer start on the same minute (see the cron in
# docker/lake/flows/deploy_lake.py), but a slow offload still overlaps the next
# ingest, so both heaps have to fit together.
#
# 1 g each is what fits. A driver JVM costs its heap plus roughly 400-600 MB of
# metaspace, code cache, GC structures, thread stacks and direct buffers, and
# each run carries a Python driver process on top: 2 x (1024 + ~550) MB plus
# ~400 MB of Python lands near 3.5 GiB of the 4 GiB cap. 2 g each does not fit.
#
# It equals today's image default — verified in the running container, where
# `Runtime.getRuntime().maxMemory()` reports 1024 MiB with nothing set — so this
# changes no behaviour. It makes the number a contract instead of an inherited
# coincidence, next to the cron that is the other half of the same constraint.
# Both are needed: the cron stops the two jobs starting together, this stops a
# future image default raising the heap inside a container that cannot hold two
# of them. docker/offload/ is deliberately untouched; it retires with Phase D.
DRIVER_MEMORY = os.environ.get("K2_LAKE_DRIVER_MEMORY", "1g")


def lake_session(app_name: str) -> SparkSession:
    """Spark session wired to the Lakekeeper REST catalog over MinIO."""
    access_key = os.environ["MINIO_ROOT_USER"]
    secret_key = os.environ["MINIO_ROOT_PASSWORD"]

    return (
        SparkSession.builder.appName(app_name)
        # Read by pyspark BEFORE it launches the gateway JVM, so this really is
        # the heap and not just a reported value: with 1536m set here the driver
        # reports maxMemory() == 1536 MiB in this image (verified 2026-08-26).
        .config("spark.driver.memory", DRIVER_MEMORY)
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
        .config(f"spark.sql.catalog.{CATALOG}.s3.path-style-access", S3_PATH_STYLE)
        .config(f"spark.sql.catalog.{CATALOG}.s3.access-key-id", access_key)
        .config(f"spark.sql.catalog.{CATALOG}.s3.secret-access-key", secret_key)
        .config(f"spark.sql.catalog.{CATALOG}.s3.region", S3_REGION)
        .config("spark.sql.defaultCatalog", CATALOG)
        # Pinned, not inherited from the image's TZ. Two places read it and
        # disagree if it moves: maintenance.expire() builds `older_than =>
        # TIMESTAMP '...'` from a UTC datetime and Spark interprets it in the
        # session zone, while the compaction predicate uses
        # `timestamp_seconds(<epoch>)`, which is absolute. One non-UTC session
        # zone and expiry silently shifts by the offset while compaction does
        # not. metrics._epoch reads `k2.max-kafka-ts` as UTC-naive for the same
        # reason. The image happens to be Etc/UTC today; this makes it a
        # contract instead of a coincidence.
        .config("spark.sql.session.timeZone", "UTC")
        # LAST_WIN, not the default EXCEPTION. `map_from_entries` over Kafka
        # headers throws on a duplicate key, and it throws on the *archive*
        # write — so one foreign producer sending two headers of the same name
        # would block raw.messages at that offset forever, which is precisely
        # what lake.sql promises cannot happen. capture sets exactly one header
        # (services/capture-rust/src/sink.rs); keeping the last of a duplicate
        # pair loses nothing we wrote and costs nothing we did.
        .config("spark.sql.mapKeyDedupPolicy", "LAST_WIN")
        .getOrCreate()
    )


def _smoke() -> None:
    """Round-trip one row through the catalog: create, append, read, drop.

    Asserts the snapshot summary carries the write's `snapshot-property.*` — that
    is the mechanism ADR-018 relies on to store Kafka offsets atomically with the
    data commit, so it is the part worth proving before Phase C builds on it.
    """
    spark = lake_session("k2-lake-smoke")
    # try/finally so a failed assert still drops the table and stops the session.
    # Without it the first failure leaves lake.audit.smoke behind, and the NEXT
    # run's CREATE TABLE fails on the leftover instead of on the real problem —
    # a debugging dead end on the very path you only walk when something is wrong.
    try:
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

        print("✓ lake smoke passed")
    finally:
        try:
            spark.sql(f"DROP TABLE IF EXISTS {CATALOG}.audit.smoke")
        finally:
            spark.stop()


if __name__ == "__main__":
    if "--smoke" not in sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    _smoke()

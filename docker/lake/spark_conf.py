#!/usr/bin/env python3
"""
K2 v3 — the one Spark session builder for the Iceberg lake.

Every v3 Spark job (raw ingest, bronze build, maintenance) gets its session from
`lake_session()`, so the `lake` catalog config lives here and nowhere else. It
is the only catalog on the stack: v2's `k2` hadoop catalog and the offload that
built its own session went in Phase D.

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
# and 4 GiB, and that budget is NOT empty before a job starts: the base image
# runs a standalone Master, a Worker, the History Server, a Thrift Server and
# Jupyter for the whole life of the container. Idle, with no driver at all,
# that is 633 MiB — `docker stats --no-stream k2-spark-iceberg`, 2026-08-26
# (635.4 MiB on a re-read 2026-08-27). Every sum below starts from it.
#
# Two drivers can be alive in the container at once, and the cron does not
# prevent it. `lake-ingest-5min` is `1-59/5` and `lake-maintenance-daily` is
# `0 3 * * *`, so the two never start on the same minute — but 03:00 and 03:01
# are one minute apart and a compaction run outlives that by a wide margin.
# An operator's `docker exec` during an incident is the other way to get two.
#
# 768m each is what fits. A driver JVM costs its heap plus roughly 400-600 MB
# of metaspace, code cache, GC structures, thread stacks and direct buffers,
# and each run carries a Python driver process on top:
#
#     2 x (768 + ~550) + ~400 Python + 633 baseline  = ~3.58 GiB of the 4 GiB
#     2 x (1024 + ~550) + ~400 Python + 633 baseline = ~4.08 GiB — over the cap
#
# The second line is the image's own default, and it is the arithmetic this
# setting exists to fix: inheriting 1 g puts two drivers plus the always-on
# JVMs past 4 GiB, and the failure mode is an OOM-kill of whichever driver
# asks for the last page — which reads as a random ingest failure rather than
# as a memory problem.
#
# **Measured, 2026-08-27.** The estimate above held. Four hand-run ingests
# against the live 41.5 M-record backlog, sampled every 2 s with `docker stats`
# and `ps -o rss=,cmd= -A` in the container:
#
#     bound 1,000   77,542 rows   17 s   peak driver RSS 1,122 MiB
#     bound 50,000  2,721,812     92 s   peak driver RSS 1,243 MiB  <- the number
#     bound 50,000  1,770,914     57 s   peak driver RSS 1,227 MiB
#     bound 50,000  1,564,334     49 s   peak driver RSS 1,221 MiB
#
# Peak container memory was 2.13 GiB of 4 GiB at the worst of those, over a
# 633 MiB idle baseline. 1,243 MiB is 768m of heap plus ~475 MiB of metaspace,
# code cache, GC structures, thread stacks and direct buffers — inside the
# ~550 MiB the arithmetic below assumed, so two concurrent drivers still fit.
#
# The number that matters is not its size but its FLATNESS: 35x the batch moved
# it 11%. That is what "nothing payload-bearing is cached" buys, and it is why
# `--max-offsets-per-partition` can be raised for a backlog drain without
# re-deriving this figure. Peak RSS here is a function of the heap setting, not
# of how much arrived.
#
# Revisit trigger: raise it back to `1g` if `lake-ingest-5min` fails with an
# OOM-kill or `java.lang.OutOfMemoryError`. It did exactly that once, on
# 2026-08-26, and the cause was a `persist(DISK_ONLY)` on a DataFrame carrying
# 5.2 MB payload rows rather than an undersized heap — read
# docker/lake/README.md, "What one run may read, and why nothing is cached",
# before touching this value.
#
# The other consumer is the `lake-ddl` one-shot, which gets its own container
# with a 1 GiB limit — too small for 768m plus JVM overhead, so
# docker-compose.yml sets `K2_LAKE_DRIVER_MEMORY: 512m` for that service only.
DRIVER_MEMORY = os.environ.get("K2_LAKE_DRIVER_MEMORY", "768m")

# maintenance.py gets a bigger heap: rewriting raw.messages file groups whose
# rows are up to 5 MB each OOM'd at 768m twice on 2026-08-26 (22:16Z with five
# concurrent groups, 22:28Z with one). It can afford it because it holds the
# ingest lock for its whole run, so it is never in the container with an ingest
# driver: 2g + ~550 MiB JVM overhead + the 633 MiB idle baseline is ~3.2 GiB of
# the 4 GiB cap.
MAINTENANCE_DRIVER_MEMORY = os.environ.get("K2_LAKE_MAINTENANCE_DRIVER_MEMORY", "2g")


def lake_session(app_name: str, driver_memory: str = DRIVER_MEMORY) -> SparkSession:
    """Spark session wired to the Lakekeeper REST catalog over MinIO."""
    access_key = os.environ["MINIO_ROOT_USER"]
    secret_key = os.environ["MINIO_ROOT_PASSWORD"]

    return (
        SparkSession.builder.appName(app_name)
        # Read by pyspark BEFORE it launches the gateway JVM, so this really is
        # the heap and not just a reported value: with 1536m set here the driver
        # reports maxMemory() == 1536 MiB in this image (verified 2026-08-26).
        .config("spark.driver.memory", driver_memory)
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
        # 256 rows per columnar cache batch, not the default 10,000. A backstop,
        # deliberately: no lake job caches a payload-bearing DataFrame any more
        # (docker/lake/ingest.py says why at each site), so on today's code this
        # setting is never reached.
        #
        # It is here because the failure it bounds was not obvious from the code
        # that caused it. `persist(DISK_ONLY)` reads as "spill to disk"; Spark
        # actually builds an in-memory columnar batch first and writes it out
        # whole, so the heap high-water mark is batchSize x row size regardless
        # of the storage level. At 10,000 rows of Coinbase level2 frames — up to
        # 5.2 MB each — that is tens of gigabytes against a 768m heap, and it is
        # exactly how the first cold start died. At 256 the same mistake costs
        # ~1.3 GB: still too much, but it fails on a batch boundary with a
        # readable trace instead of taking the JVM out mid-append.
        #
        # NOT touched, for the record: `spark.sql.files.maxRecordsPerFile` and
        # the tables' `write.target-file-size-bytes`. Neither was implicated —
        # they govern how the WRITER rolls output files, which was streaming
        # correctly the whole time, and shrinking them would trade a memory
        # problem this does not have for a small-file problem compaction would
        # then have to clean up nightly.
        .config("spark.sql.inMemoryColumnarStorage.batchSize", "256")
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


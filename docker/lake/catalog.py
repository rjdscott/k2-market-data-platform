#!/usr/bin/env python3
"""
Catalog-side helpers shared by every lake writer: snapshot bookkeeping, the
registry fetch, and the one way an audit row gets filed from a running job.

Split out of ingest.py when bronze.py (the per-venue decode) needed the same
five functions and importing ingest.py for them would have been a cycle. Every
name here is still re-exported from ingest.py, so the chaos scripts and
scripts/lake-verify.sh that import them from there keep working.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import offsets as O
from spark_conf import CATALOG, KAFKA_BROKERS, SCHEMA_REGISTRY_URL

CHECKS_TABLE = f"{CATALOG}.audit.checks"


def snapshot_history(spark, table: str) -> list:
    """`[(committed_at, summary)]` for one table, newest last. Empty if unwritten."""
    rows = spark.sql(
        f"SELECT committed_at, summary FROM {table}.snapshots"
    ).collect()
    return [(r["committed_at"], r["summary"] or {}) for r in rows]


def current_snapshot_id(spark, table: str):
    """The snapshot the `main` branch points at — the authoritative pointer.

    Not `ORDER BY committed_at DESC LIMIT 1`: `<table>.snapshots` lists every
    snapshot in the metadata, and the newest by commit time is not necessarily
    the current one after a rollback, a cherry-pick or a branch write.
    `<table>.refs` is where Iceberg records which one is live.
    """
    rows = spark.sql(
        f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'"
    ).collect()
    return rows[0][0] if rows else None


def broker_offsets(spark, topic_list: list, at_timestamp_ms: int = 0) -> tuple:
    """`(earliest, latest, until)` as the broker reports them, per partition.

    Each is `{topic: {partition: offset}}`; `until` is None unless
    `at_timestamp_ms` is given, in which case it holds the offset of the first
    record at or after that instant (Kafka's -1 where there is none).

    Over the Kafka AdminClient already on the Spark classpath
    (kafka-clients-3.4.1.jar, pulled in by spark-sql-kafka-0-10), through the
    driver's JVM gateway — no new Python dependency for three metadata calls.

    **The offsets a run will consume are decided from this, before Spark reads a
    byte.** That is what lets `bounded_offsets` pin `endingOffsets` instead of
    resolving `latest` inside the read, and a pinned range is what removed the
    `persist(DISK_ONLY)` that killed the driver — see docker/lake/offsets.py.

    This used to be a `--partitions` flag. See docker/lake/offsets.py for why a
    number on a command line is the wrong place for it.
    """
    jvm = spark._jvm
    admin_pkg = jvm.org.apache.kafka.clients.admin
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", KAFKA_BROKERS)
    admin = admin_pkg.AdminClient.create(props)
    try:
        names = jvm.java.util.ArrayList()
        for topic in topic_list:
            names.add(topic)
        described = admin.describeTopics(names).all().get()
        counts = {topic: described.get(topic).partitions().size() for topic in topic_list}

        def list_offsets(spec) -> dict:
            """One `listOffsets` round trip for every partition, under one spec."""
            request = jvm.java.util.HashMap()
            for topic, count in counts.items():
                for partition in range(count):
                    request.put(jvm.org.apache.kafka.common.TopicPartition(topic, partition), spec())
            answer = admin.listOffsets(request).all().get()
            return {
                topic: {
                    partition: answer.get(
                        jvm.org.apache.kafka.common.TopicPartition(topic, partition)
                    ).offset()
                    for partition in range(count)
                }
                for topic, count in counts.items()
            }

        return (
            list_offsets(admin_pkg.OffsetSpec.earliest),
            list_offsets(admin_pkg.OffsetSpec.latest),
            list_offsets(lambda: admin_pkg.OffsetSpec.forTimestamp(at_timestamp_ms))
            if at_timestamp_ms
            else None,
        )
    finally:
        admin.close()


def added_records(spark, table: str) -> int:
    """Rows the table's current snapshot added, from Iceberg's own summary.

    Not `df.count()`. A count on the DataFrame that was just written is a second
    full evaluation of it — for stage 1 a second read of every Kafka record, for
    stage 2 a second Avro decode of the whole range — and the reflex fix for
    that (cache it first) is what put gigabytes of 5.2 MB payload rows into the
    driver heap. Iceberg already counted the rows while committing them; the
    number is a metadata read away and it describes the commit rather than the
    plan that produced it.
    """
    snapshot_id = current_snapshot_id(spark, table)
    if snapshot_id is None:
        return 0
    rows = spark.sql(
        f"SELECT summary FROM {table}.snapshots WHERE snapshot_id = {snapshot_id}"
    ).collect()
    return int((rows[0][0] or {}).get("added-records", 0)) if rows else 0


class UnresolvableSchema(Exception):
    """The registry does not serve this schema id."""


def fetch_schema(schema_id: int) -> str:
    """The registered Avro schema for `schema_id`, as a JSON string.

    By id, not by subject: a payload names its own writer schema and that is the
    only schema that can decode it. Resolving the subject's *latest* version
    instead would decode last week's records against this week's schema and
    succeed at it, which is the silent-corruption path Avro's id exists to close.
    """
    url = "{}/schemas/ids/{}".format(SCHEMA_REGISTRY_URL.rstrip("/"), schema_id)
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed internal host
            return json.load(response)["schema"]
    except urllib.error.HTTPError as exc:
        # 404 is a real state, not a bug: a record framed with an id this
        # registry has never held. Raising it as its own type is what lets
        # stage 2 skip that id and file an audit row instead of dying on every
        # cycle for as long as the record stays in the archive — which, since
        # raw.messages is never expired, is forever.
        raise UnresolvableSchema(f"schema id {schema_id}: {exc}") from exc


def write_audit_rows(spark, rows: list, properties: dict) -> bool:
    """This run's findings into `audit.checks`, in ONE commit. True if it landed.

    Same table as the nightly audit, `job='ingest'`, so "what did the pipeline
    find and when" stays one query.

    Three properties ride on the commit and all three are load-bearing.
    `k2.job=ingest` is what keeps this snapshot out of
    `k2_lake_audit_failures_total`: that gauge is the nightly audit's count, and
    an ingest row landing as the current snapshot used to zero a firing
    `LakeAuditFailed` with no audit having passed. `k2.audit-failures` is the
    same property `maintenance.run_audits` writes, and it is why this is one
    commit rather than one per row — per row the count in the newest summary is
    always 1, and a gauge reading it would report "at least one" while claiming
    to be a count. `properties` carries the per-finding count that the gauge for
    THIS finding is read from (`k2.unresolvable-schema-ids`, `k2.offset-gaps`):
    two ingest-side findings now share this table, and one shared count would
    have each of them setting the other's gauge — the case
    docker/lake/offsets.py names next to those two constants.

    **Returning rather than raising is the point.** The schema-id path treats a
    failed write as best-effort — a finding that cannot be recorded must not
    become a second failure on top of the one it was reporting — while
    `_accept_data_loss` treats it as fatal, because there the record is what
    licenses the skip.
    """
    failures = sum(1 for r in rows if not r["passed"])
    writer = (
        spark.createDataFrame(rows)
        .writeTo(CHECKS_TABLE)
        .option(f"snapshot-property.{O.JOB}", "ingest")
        .option(f"snapshot-property.{O.AUDIT_FAILURES}", str(failures))
    )
    for name, value in properties.items():
        writer = writer.option(f"snapshot-property.{name}", value)
    try:
        writer.append()
    except Exception as exc:  # noqa: BLE001 - the finding already printed above
        print(f"could not write {len(rows)} audit row(s) ({exc})")
        return False
    return True

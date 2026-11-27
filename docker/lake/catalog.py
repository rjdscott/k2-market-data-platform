#!/usr/bin/env python3
"""
Catalog-side helpers for a Spark job on the lake: snapshot bookkeeping, the
broker's offsets and the registry fetch.

Split out of the old ingest job so its stages could share them without an
import cycle. That pipeline is retired and archived in legacy/v3-lake-ingest/
(ADR-056); these helpers stay with offsets.py for the Kafka connector of the
plan's phase 004, and books.py imports two of them. The audit-row writer went
with the audit table it wrote to.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import offsets as O
from spark_conf import KAFKA_BROKERS, SCHEMA_REGISTRY_URL


def main_snapshots(spark, table: str) -> list:
    """The `<table>.snapshots` rows on `main`'s lineage, unordered. Empty if unwritten.

    One refs read and one snapshot scan. `main` is read first, so a commit
    landing between the two adds a snapshot the walk ignores instead of a head
    it cannot find.
    """
    head = current_snapshot_id(spark, table)
    rows = spark.sql(
        f"SELECT snapshot_id, parent_id, committed_at, summary FROM {table}.snapshots"
    ).collect()
    on_main = set(O.main_lineage({r["snapshot_id"]: r["parent_id"] for r in rows}, head))
    return [r for r in rows if r["snapshot_id"] in on_main]


def snapshot_history(spark, table: str) -> list:
    """`[(committed_at, summary)]` for the snapshots on `main`'s lineage. Empty if unwritten.

    Not every row of `<table>.snapshots`: once a table has a branch, that lists
    unpublished commits too, and `latest_summary` over them resumes from a
    position `main` never reached (`offsets.main_lineage`).
    """
    return [(r["committed_at"], r["summary"] or {}) for r in main_snapshots(spark, table)]


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


def broker_offsets(spark, topic_list: list, at_timestamp_ms: int = 0, brokers: str | None = None) -> tuple:
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
    props.put("bootstrap.servers", brokers or KAFKA_BROKERS)
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


def fetch_schema(schema_id: int, registry: str | None = None) -> str:
    """The registered Avro schema for `schema_id`, as a JSON string.

    By id, not by subject: a payload names its own writer schema and that is the
    only schema that can decode it. Resolving the subject's *latest* version
    instead would decode last week's records against this week's schema and
    succeed at it, which is the silent-corruption path Avro's id exists to close.
    """
    url = "{}/schemas/ids/{}".format((registry or SCHEMA_REGISTRY_URL).rstrip("/"), schema_id)
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed internal host
            return json.load(response)["schema"]
    except urllib.error.HTTPError as exc:
        # 404 is a real state, not a bug: a record framed with an id this
        # registry has never held. Raising it as its own type is what let the
        # old decode stage skip that id and record it, instead of dying on
        # every cycle for as long as the record stayed in its never-expired
        # archive.
        raise UnresolvableSchema(f"schema id {schema_id}: {exc}") from exc

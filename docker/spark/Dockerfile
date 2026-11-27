# K2 Spark image: the v3 Redpanda-to-lake batch ingest and maintenance.
#
# Base: the official Apache Spark image, since 2026-11-26 (plan 000, step N0-6).
# Until then it was tabulario/spark-iceberg:3.5.5_1.8.1, a demo image on Debian 11
# with a source-built Python 3.10.16 (upstream 3.10 ended 2026-10) and OpenSSL
# 1.1.1w (ended 2023-09): nothing patches either. This one is Ubuntu 22.04 LTS,
# whose Python 3.10 and OpenSSL 3.0 packages the distribution still patches
# (the image's python3.10 is 3.10.12-1~22.04.18, built 2026-08-31), with Temurin
# Java 17 and Spark 3.5.9 built for Hadoop 3.3.4. Spark 3.5.5 to 3.5.9 is one patch line, so
# Scala 2.12 and the Iceberg 1.8.1 that Lakekeeper is verified against stay.
#
# Pinned by digest, not by tag alone: an official image tag is rebuilt in place
# whenever its OS layer is patched, so the tag alone would change the image under
# every job. The tag is kept alongside for readability. Refresh with
#   docker buildx imagetools inspect spark:3.5.9-scala2.12-java17-python3-ubuntu
# (2026-11-26, OCI image index digest); the whole procedure is
# docs/runbooks/upgrade-a-pinned-component.md.
FROM spark:3.5.9-scala2.12-java17-python3-ubuntu@sha256:8901b8427694267ed719f27aa1498815dbd3573efb3cde447246fbff29d07272

# The base ends as USER spark (uid 185); root for the build steps, spark again below.
USER root

# Python: docker/lake/instruments.py and bars.py read their YAML with PyYAML, and
# nothing else under docker/lake imports a package outside the standard library
# and pyspark. pyspark is the
# distribution's own, on PYTHONPATH below, not a second copy from PyPI. The
# tabulario base also carried Jupyter, pandas, pyiceberg and the rest of its
# sample-notebook packages; no lake job imports any of them.
RUN pip install --no-cache-dir --only-binary=:all: PyYAML==6.0.4

# ─────────────────────────────────────────────────────────────────────────────
# JARs: baked at build time, not resolved from Maven at job runtime
# ─────────────────────────────────────────────────────────────────────────────
# Iceberg: the Spark 3.5 / Scala 2.12 runtime (SparkCatalog, the SQL extensions)
# and the AWS bundle (S3FileIO, which the REST catalog's tables are read and
# written through). The tabulario base shipped these two plus Azure and GCP
# bundles this stack has no use for.
#
# Kafka source and Avro codec: the ingest reads Redpanda by offset range and
# decodes Confluent-framed Avro. kafka-clients 3.4.1 and commons-pool2 2.11.1 are
# not free choices: they are what spark-sql-kafka-0-10_2.12:3.5.9 declares in its
# POM (`kafka.version` / `commons-pool2.version` in spark-parent_2.12:3.5.9).
#
# sha256 provenance (2026-11-26): Maven Central publishes a .sha256 for the two
# Iceberg jars and kafka-clients, and each matched. For the other four it
# publishes a .sha1 only, so each jar was downloaded once, its published .sha1
# verified, and the sha256 computed from that same byte stream. The two Iceberg
# jars, kafka-clients and commons-pool2 are byte-identical to the ones in the
# tabulario image. Reproduce with:
#   curl -fsSLO <url> && curl -fsSL <url>.sha1 && sha1sum <jar> && sha256sum <jar>
ARG MAVEN=https://repo1.maven.org/maven2
RUN set -eu; cd "$SPARK_HOME/jars"; \
    for spec in \
      "org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/1.8.1/iceberg-spark-runtime-3.5_2.12-1.8.1.jar a42f6e3f89943ddc67d35738f822396970d0c83d5688c7614099a810aeabf9e0" \
      "org/apache/iceberg/iceberg-aws-bundle/1.8.1/iceberg-aws-bundle-1.8.1.jar 31aeb0880fbdcf9b240326cc0a90a108159c3420a6c0e38bc70d3ade7ea97dd1" \
      "org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.9/spark-sql-kafka-0-10_2.12-3.5.9.jar fed393dcc3ef70611fe3d4f35ae78a8ea7c55301fd4cdd66c5dc97fcfaa5a00f" \
      "org/apache/spark/spark-token-provider-kafka-0-10_2.12/3.5.9/spark-token-provider-kafka-0-10_2.12-3.5.9.jar e82612e6070438c5ea48b08c74f367a6d214a390bdf6dcf3ff316dca592ddda5" \
      "org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar 9d0a9058c8ede79db6e54ae378ed16fe8d1bead2894635a4aa6aa0b7981668c6" \
      "org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar ea0505ee7515e58b1ac0e686e4d1a5d9f7d808e251a61bc371aa0595b9963f83" \
      "org/apache/spark/spark-avro_2.12/3.5.9/spark-avro_2.12-3.5.9.jar 9dda7512c1052bcd3774cbe247572cd481d4481eb8a90b7c4865b23a12bbecf8" \
    ; do \
      set -- $spec; \
      curl -fsSLO "$MAVEN/$1"; \
      echo "$2  $(basename $1)" | sha256sum -c -; \
    done

# ─────────────────────────────────────────────────────────────────────────────
# spark-defaults.conf: what the stack took from the tabulario base, written out
# ─────────────────────────────────────────────────────────────────────────────
# The base has no conf directory at all. Every line here was in the tabulario
# file the jobs ran under, minus its `demo` REST catalog and the defaultCatalog
# that pointed at it (http://rest:8181, which does not resolve in this stack).
# The `lake` catalog is not here: docker/lake/spark_conf.py configures it per
# session, from the environment.
#
# Event logs: every Spark job writes one to /home/iceberg/spark-events, in the
# container's writable layer. The five-minute ingest alone left about 13 GiB a
# day there before the cleaner was on (P2 audit, 2026-11-26). The History Server
# (the CMD below) runs the cleaner: two days kept for debugging, checked hourly,
# compressed.
#
# Redaction: docker/lake/spark_conf.py hands the MinIO key to the catalog as
# `spark.sql.catalog.lake.s3.access-key-id` and `...s3.secret-access-key`, and
# pyspark also puts every session conf on the driver's command line as `--conf`,
# which the environment page shows as `sun.java.command`. Spark 3.5.9's default
# regex, (?i)secret|password|token|access[.]?key, misses the hyphen in
# `access-key-id`, so the job UI (4040), the History Server (18080) and every event
# log served that value in clear. This one adds `credential` and every separator
# spelling of access key. Spark blanks a property's value when the regex matches
# its name OR its value, and applies it when the event log is written (the
# environment, job and stage events), when the job UI serves it and when the
# History Server serves a log, an old one included. Verified 2026-11-26 with fake
# keys through spark_conf.py: no occurrence in either environment endpoint or the
# decompressed event log; with the default regex, five in the event log. No
# `spark.redaction.string.regex`: the key appears in no plan or SQL text.
# tests/test_spark_redaction.py fails when a sensitive name in spark_conf.py
# would slip past it.
RUN mkdir -p "$SPARK_HOME/conf" /home/iceberg/spark-events \
 && printf '%s\n' \
      'spark.sql.extensions                   org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions' \
      'spark.sql.catalogImplementation        in-memory' \
      'spark.eventLog.enabled                 true' \
      'spark.eventLog.dir                     /home/iceberg/spark-events' \
      'spark.eventLog.compress                true' \
      'spark.history.fs.logDirectory          /home/iceberg/spark-events' \
      'spark.history.fs.cleaner.enabled       true' \
      'spark.history.fs.cleaner.maxAge        2d' \
      'spark.history.fs.cleaner.interval      1h' \
      'spark.redaction.regex                  (?i)secret|password|token|credential|access[-._]?key' \
      >"$SPARK_HOME/conf/spark-defaults.conf" \
 && chown -R spark:spark /home/iceberg

# PATH: spark-sql, spark-submit and the sbin scripts by name, as the old base had
# them. PYTHONPATH: the distribution's pyspark and its py4j, which is how
# `python3 /home/iceberg/lake/<script>.py` imports pyspark at all.
# /spool, the mount point of fetch's spool volume (read-only here), owned as docker/fetch/Dockerfile
# owns it (10002, mode 755): Docker gives an empty named volume the owner of the directory it is
# first mounted on, so with both images agreeing, fetch can write whichever service mounts it first.
RUN mkdir /spool && chown 10002:10002 /spool

ENV PATH=/opt/spark/bin:/opt/spark/sbin:$PATH \
    PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip

# The build-time check: a py4j bump in a later Spark renames the zip above, and
# a wrong PYTHONPATH must fail here, not in the first scheduled ingest.
RUN python3 -c "import pyspark, py4j, yaml; assert pyspark.__version__ == '3.5.9', pyspark.__version__"

# Not root: the jobs need nothing root has. The lake scripts are read from the
# /home/iceberg/lake mount, the registry and bar thresholds from
# /home/iceberg/config (both readable by any user), and everything a job writes
# goes to /tmp (the writer lock, Spark's scratch) or /home/iceberg/spark-events,
# which spark owns. `docker exec` runs as this user unless told otherwise.
WORKDIR /home/iceberg
USER spark

# What the container runs between jobs: the History Server, in the foreground,
# on its default port 18080 inside the container. It is the only long-running
# process anything depends on, because it runs the event-log cleaner above; its
# REST API is also where a failed stage is read after the fact (the 2026-11-25
# note in docker/lake/spark_conf.py). The jobs themselves are local-mode drivers
# started by `docker exec`, so the tabulario base's standalone Master and Worker,
# Thrift server and Jupyter are not started: nothing in the stack used them.
#
# tini as PID 1, because every job leaves an orphan: the PySpark gateway JVM
# outlives its Python driver by a moment, is reparented to PID 1, and a Java PID 1
# never reaps it. A one-shot that sets its own entrypoint replaces all of this,
# CMD included.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/spark/bin/spark-class", "org.apache.spark.deploy.history.HistoryServer"]

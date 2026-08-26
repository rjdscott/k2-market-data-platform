# K2 Spark image — v2 ClickHouse→Iceberg offload + v3 Redpanda→lake batch ingest.
#
# Base bumped 3.5.0_1.4.2 → 3.5.5_1.8.1 for Phase B (ADR-018): Iceberg 1.8.1 is
# the client Lakekeeper 0.13.3 is verified against, and it brings
# iceberg-aws-bundle (S3FileIO) which the REST catalog needs.
# 3.5.6_1.9.0 does not exist on Docker Hub — 3.5.5_1.8.1 is the newest tag.
#
# Pinned by digest, not by tag alone: a tag is mutable and this image is not
# ours, so an upstream repush would silently change the Spark and Iceberg jars
# under every job. The tag is kept alongside for readability. Refresh with:
#   docker buildx imagetools inspect tabulario/spark-iceberg:3.5.5_1.8.1
# (2026-08-26 — OCI image index digest)
FROM tabulario/spark-iceberg:3.5.5_1.8.1@sha256:790a72e744e0d09f161e1b4c7c2f79625cc598cf0476da4703e62f1f4e023460

# ─────────────────────────────────────────────────────────────────────────────
# Neutralise the base image's `demo` catalog
# ─────────────────────────────────────────────────────────────────────────────
# The image ships a spark-defaults.conf that declares a REST catalog `demo` at
# http://rest:8181 AND makes it spark.sql.defaultCatalog. `rest` does not resolve
# in this stack, so any session that touches the default catalog dies with
# UnknownHostException. Strip both settings so each job's own catalog stands
# alone — v2's `k2` (hadoop, docker/offload/offload_generic.py) and v3's `lake`
# (REST, docker/lake/spark_conf.py). Everything else in that file (eventLog,
# the Iceberg extensions, catalogImplementation) is kept.
RUN sed -i '/spark\.sql\.catalog\.demo/d; /spark\.sql\.defaultCatalog/d' \
      "$SPARK_HOME/conf/spark-defaults.conf" \
 && ! grep -q 'demo' "$SPARK_HOME/conf/spark-defaults.conf"

# ─────────────────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
# v2 ONLY: watermark_pg.py tracks offload state in PostgreSQL. Phase D moves
# exactly-once into the Iceberg snapshot summary and the watermark table goes
# away — this line and the ClickHouse JDBC driver below go with it.
RUN pip3 install --no-cache-dir psycopg2-binary==2.9.9

# ─────────────────────────────────────────────────────────────────────────────
# JARs — baked at build time, not resolved from Maven at job runtime
# ─────────────────────────────────────────────────────────────────────────────
# v2 ONLY (see above): ClickHouse JDBC, `-all` classifier so httpclient5 etc.
# are shaded in and no spark.jars.packages resolution is needed.
# Pinned to 0.4.6 (2026-02-18) — the version ADR-015's 24.3 downgrade was tested
# against. Upgrading requires an integration test.
#
# sha256 pinned by the same method as the five jars below (2026-08-26): Maven
# Central publishes only a .sha1 sidecar for this artifact, so the jar was
# downloaded once, its published .sha1 verified against the bytes, and the
# sha256 computed from that same byte stream. Both checks agreed —
#   published/computed sha1: 6efc73bb044a64add5555d676a8074b8389632e6
# Reproduce with:
#   curl -fsSLO <url> && curl -fsSL <url>.sha1 && sha1sum <jar> && sha256sum <jar>
RUN curl -fsSL \
    "https://repo1.maven.org/maven2/com/clickhouse/clickhouse-jdbc/0.4.6/clickhouse-jdbc-0.4.6-all.jar" \
    -o "$SPARK_HOME/jars/clickhouse-jdbc-0.4.6-all.jar" \
 && echo "ccd7dc983d7ed4ee1e1a4ad12a91f5912718ae15f4b6078efd66862f758ff7e9  $SPARK_HOME/jars/clickhouse-jdbc-0.4.6-all.jar" \
    | sha256sum -c -

# v3: Kafka source + Avro codec. The base image has neither, and Phase C reads
# Redpanda by offset range and decodes Confluent-framed Avro.
#
# Versions are not free choices: kafka-clients 3.4.1 and commons-pool2 2.11.1 are
# what spark-sql-kafka-0-10_2.12:3.5.5 declares in its POM
# (`kafka.version` / `commons-pool2.version` in spark-parent_2.12:3.5.5).
#
# sha256 provenance: Maven Central publishes .sha256 only for kafka-clients.
# For the other four it publishes .sha1 only, so each jar was downloaded once,
# its published .sha1 verified, and the sha256 computed from that same byte
# stream and pinned here (2026-08-26). Reproduce with:
#   curl -fsSLO <url> && curl -fsSL <url>.sha1 && sha1sum <jar> && sha256sum <jar>
ARG MAVEN=https://repo1.maven.org/maven2
RUN set -eu; cd "$SPARK_HOME/jars"; \
    for spec in \
      "org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.5/spark-sql-kafka-0-10_2.12-3.5.5.jar 26e2ada52ed32d9310960749da074f3bfa9c20354c282303d1bd87b78856814c" \
      "org/apache/spark/spark-token-provider-kafka-0-10_2.12/3.5.5/spark-token-provider-kafka-0-10_2.12-3.5.5.jar 475f1ba3d06afef4ab0ddb9e27f0490c07c1f5870212c7f178e7dfbbc433ec1d" \
      "org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar 9d0a9058c8ede79db6e54ae378ed16fe8d1bead2894635a4aa6aa0b7981668c6" \
      "org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar ea0505ee7515e58b1ac0e686e4d1a5d9f7d808e251a61bc371aa0595b9963f83" \
      "org/apache/spark/spark-avro_2.12/3.5.5/spark-avro_2.12-3.5.5.jar 16815c7ae6e68eb8f097c3987737689a20fb9cd8718f1c6cce72b76008a94d06" \
    ; do \
      set -- $spec; \
      curl -fsSLO "$MAVEN/$1"; \
      echo "$2  $(basename $1)" | sha256sum -c -; \
    done

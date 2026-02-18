FROM tabulario/spark-iceberg:3.5.0_1.4.2

# ─────────────────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
# psycopg2-binary: watermark_pg.py connects to PostgreSQL to track offload state.
# Using binary wheel to avoid libpq-dev build dependency in the container.
RUN pip3 install psycopg2-binary==2.9.9

# ─────────────────────────────────────────────────────────────────────────────
# JARs — baked at build time, not downloaded at job runtime
# ─────────────────────────────────────────────────────────────────────────────
# The tabulario/spark-iceberg base image already includes the Iceberg runtime
# JARs for Iceberg 1.4.2. We only need to add the ClickHouse JDBC driver.
#
# Using the -all classifier (fat jar) so there are no transitive dependency gaps
# (httpclient5 etc. are shaded in). This removes the need for spark.jars.packages
# which would otherwise hit Maven Central on every job run.
#
# Decision 2026-02-18: pin ClickHouse JDBC to 0.4.6 — same version that was
# previously resolved via spark.jars.packages. Upgrade requires integration test.
RUN curl -fsSL \
    "https://repo1.maven.org/maven2/com/clickhouse/clickhouse-jdbc/0.4.6/clickhouse-jdbc-0.4.6-all.jar" \
    -o "$SPARK_HOME/jars/clickhouse-jdbc-0.4.6-all.jar"

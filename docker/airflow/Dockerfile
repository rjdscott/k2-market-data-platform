# Airflow 3 for the lake schedules (ADR-034): api-server, scheduler, dag-processor
# and the airflow-migrate one-shot all run this one image.
#
# 3.3.2, not 3.4.0: astronomer-cosmos 1.16.0 added Airflow 3.4 to its test
# matrix as a pre-release (3.4.0b1, cosmos PR #3049), so 3.3 is the newest
# minor it supports outside beta. Python 3.12 is the image's default and the
# Python the Prefect worker already runs. ADR-034 records how this was chosen.
#
# The slim image, because the full one carries some fifty providers K2 does
# not use. It already ships docker-ce-cli (the scheduler dispatches every lake
# job as `docker exec k2-spark-iceberg ...`) and psql (the migrate one-shot
# creates the `airflow` role and database). The RUN line fails the build if
# either goes missing from a later tag.
FROM apache/airflow:slim-3.3.2-python3.12

# postgres: the metadata DB driver. statsd: metrics to the statsd exporter.
# The constraints file is the one Airflow publishes for this exact release and
# Python, so every transitive dependency is the set Airflow tested.
RUN pip install --no-cache-dir "apache-airflow[postgres,statsd]==3.3.2" \
      --constraint=https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt \
    && docker --version && psql --version

COPY docker/airflow/init-db.sh /opt/k2/init-db.sh

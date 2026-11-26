# Airflow 3 for the lake schedules (ADR-034): api-server, scheduler, dag-processor
# and the airflow-migrate one-shot all run this one image.
#
# 3.3.2, not 3.4.0: astronomer-cosmos 1.16.0 added Airflow 3.4 to its test
# matrix as a pre-release (3.4.0b1, cosmos PR #3049), so 3.3 is the newest
# minor it supports outside beta. Python 3.12 is the image's default and the
# Python the retired scheduler's worker ran. ADR-034 records how this was chosen.
#
# The slim image, because the full one carries some fifty providers K2 does
# not use. It already ships docker-ce-cli (the scheduler dispatches every lake
# job as `docker exec k2-spark-iceberg ...`) and psql (the migrate one-shot
# creates the `airflow` role and database). The RUN line fails the build if
# either goes missing from a later tag.
FROM apache/airflow:slim-3.3.2-python3.12

# postgres: the metadata DB driver. statsd: metrics to the statsd exporter.
# astronomer-cosmos rendered the retired dbt_marts DAG from the dbt project (archived
# with the old lake ingest in legacy/v3-lake-ingest/airflow/dags/); no DAG uses
# cosmos today. 1.16.0 (2026-11-03) is the newest release
# on 2026-11-26; its compatibility policy lists Airflow 3.3 and dbt Core 1.12.
# The constraints file is the one Airflow publishes for this exact release and
# Python, so every transitive dependency is the set Airflow tested; it pins
# every one of cosmos's own dependencies too (aenum, deprecation, msgpack,
# openlineage-integration-common, virtualenv and the rest).
RUN pip install --no-cache-dir "apache-airflow[postgres,statsd]==3.3.2" "astronomer-cosmos==1.16.0" \
      --constraint=https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt \
    && docker --version && psql --version

# dbt in a virtualenv of its own, from dbt/uv.lock: dbt-core's pins (protobuf,
# networkx, jsonschema and more) are not Airflow's, and one environment for both
# is the clash the plan names. Cosmos calls /opt/k2/dbt-venv/bin/dbt as a
# subprocess. The packages come from dbt/package-lock.yml at build time, never at
# task time, into a directory outside the project, which the services mount
# read-only at /opt/k2/dbt (docker-compose.yml, dbt_project.yml
# packages-install-path). uv 0.13.10 (2026-11-24) is the newest release.
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /usr/local/bin/uv
USER root
COPY dbt/pyproject.toml dbt/uv.lock /opt/k2/dbt-env/
RUN UV_PROJECT_ENVIRONMENT=/opt/k2/dbt-venv UV_PYTHON=/usr/python/bin/python3.12 UV_PYTHON_DOWNLOADS=never \
      uv sync --locked --no-dev --no-cache --project /opt/k2/dbt-env \
    && /opt/k2/dbt-venv/bin/dbt --version
COPY dbt/dbt_project.yml dbt/packages.yml dbt/package-lock.yml /tmp/dbt-deps/
# The lock the packages came from is kept beside uv.lock, so a test can hold the image to
# the checkout's dbt/ (docker/airflow/tests/test_dbt_image.py).
RUN cd /tmp/dbt-deps && K2_DBT_PACKAGES=/opt/k2/dbt_packages DBT_SEND_ANONYMOUS_USAGE_STATS=false \
      /opt/k2/dbt-venv/bin/dbt deps --log-path /tmp/dbt-deps/logs \
    && cp package-lock.yml /opt/k2/dbt-env/ \
    && rm -rf /tmp/dbt-deps && ls /opt/k2/dbt_packages/dbt_expectations /opt/k2/dbt_packages/dbt_date >/dev/null
USER airflow

COPY docker/airflow/init-db.sh /opt/k2/init-db.sh

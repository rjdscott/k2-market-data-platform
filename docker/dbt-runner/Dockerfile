# The dbt-runner one-shot (A7, A13; docs/development/phase-001-interfaces.md, "The dbt wrapper"):
# `k2-dbt build --release <id> --product <domain>.<product> --run-id <dagId>/<runId>` runs the
# wrapper, docker/dbt-runner/k2_dbt.py, which runs dbt Core on the release's compiled/dbt/ tree
# as the builder and publishes as the publisher. The Airflow one-shot operator starts it with a
# read-only root, a 64m noexec /tmp, every capability dropped and `--user` the host user who owns
# the mode-600 password files (docker/airflow/dags/oneshot.py).
#
# Holds: Python 3.12, dbt-core and dbt-trino from dbt/uv.lock (the trino client among their
# locked dependencies, which the run-records client k2runs needs; k2runs itself is read from the
# release's platform/runs/), dbt/profiles.yml, and the wrapper. No dbt packages: the compiled
# tree names none, and nothing is fetched at run time. Built from the repository root as context
# (Dockerfile.dockerignore) and tagged dbt-runner:<image id 12> by `make release`.
# python 3.12.16 and uv 0.13.10 are the pins docker/tools/Dockerfile carries.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64 AS venv

COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv
COPY dbt/pyproject.toml dbt/uv.lock /opt/k2/dbt-env/
# Bytecode compiled here: the root is read-only at run time, so it is never written then.
RUN UV_PROJECT_ENVIRONMENT=/opt/k2/dbt-venv UV_PYTHON=/usr/local/bin/python3.12 UV_PYTHON_DOWNLOADS=never \
      uv sync --locked --no-dev --no-cache --compile-bytecode --project /opt/k2/dbt-env

# The run image: the same base, so the venv's interpreter link holds; uv stays in the stage above.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

COPY --from=venv /opt/k2/dbt-venv /opt/k2/dbt-venv
COPY dbt/profiles.yml /opt/k2/dbt/profiles.yml
COPY docker/dbt-runner/ /opt/k2/dbt-runner/
RUN printf '#!/bin/sh\nexec /opt/k2/dbt-venv/bin/python /opt/k2/dbt-runner/k2_dbt.py "$@"\n' > /usr/local/bin/k2-dbt \
    && chmod 0755 /usr/local/bin/k2-dbt \
    && /opt/k2/dbt-venv/bin/dbt --version

# Every path dbt writes is on the /tmp tmpfs (target, logs, HOME), so --read-only holds; the
# wrapper may name a target path of its own. No usage stats: nothing leaves for dbt Labs.
ENV PATH=/opt/k2/dbt-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DBT_PROFILES_DIR=/opt/k2/dbt \
    DBT_TARGET_PATH=/tmp/dbt/target \
    DBT_LOG_PATH=/tmp/dbt/logs \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

# Non-root. The operator runs it as the host user (--user) to read the password files; this uid
# is for any other run, and every file here is world-readable for either.
USER 10003:10003
WORKDIR /tmp

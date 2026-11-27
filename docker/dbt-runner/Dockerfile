# The dbt-runner one-shot (A7, A13; docs/development/phase-001-interfaces.md, "The dbt wrapper"):
# `k2-dbt build --release <id> --product <domain>.<product> --run-id <dagId>/<runId>` runs the
# wrapper, docker/dbt-runner/k2_dbt.py, which runs dbt Core on the release's compiled/dbt/ tree
# as the builder and publishes as the publisher. The Airflow one-shot operator starts it with a
# read-only root, a 64m noexec /tmp, every capability dropped and `--user` the host user who owns
# the mode-600 password files (docker/airflow/dags/oneshot.py).
#
# Holds: Python 3.12, dbt-core and dbt-trino from dbt/uv.lock (the trino client among their
# locked dependencies, which the wrapper and the run-records client k2runs need; k2runs itself is
# read from the release's platform/runs/), dbt/profiles.yml, the wrapper, and what it imports:
# k2gen's source with contracts/schema, as docker/tools/Dockerfile lays them out, and the catalog
# drift check beside the wrapper. No dbt packages: the compiled tree names none, and nothing is
# fetched at run time. Built from the repository root as context
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
# k2gen (the release's model, emit/lake.view_update, the drift check's expected()): its source in a
# subset of the tree, since model.REPO is three directories above its module and model.load reads
# contracts/schema there, on the venv's path by a .pth file. Its runtime dependencies are dbt's
# lock's, and the build fails unless each is the version platform/k2gen/uv.lock pins.
COPY platform/k2gen/k2gen /opt/k2/platform/k2gen/k2gen
COPY platform/k2gen/uv.lock /opt/k2/platform/k2gen/uv.lock
COPY contracts/schema /opt/k2/contracts/schema
COPY docker/dbt-runner/ /opt/k2/dbt-runner/
COPY scripts/verify/catalog_drift.py /opt/k2/dbt-runner/catalog_drift.py
RUN echo /opt/k2/platform/k2gen > /opt/k2/dbt-venv/lib/python3.12/site-packages/k2gen.pth \
    && /opt/k2/dbt-venv/bin/python -c "import importlib.metadata as m, tomllib; \
lock = {p['name']: p['version'] for p in tomllib.load(open('/opt/k2/platform/k2gen/uv.lock', 'rb'))['package']}; \
bad = [(n, m.version(n), lock[n]) for n in ('jsonschema', 'pyyaml', 'referencing', 'attrs', 'jsonschema-specifications', \
'rpds-py', 'typing-extensions') if m.version(n) != lock[n]]; assert not bad, bad" \
    && printf '#!/bin/sh\nexec /opt/k2/dbt-venv/bin/python /opt/k2/dbt-runner/k2_dbt.py "$@"\n' > /usr/local/bin/k2-dbt \
    && chmod 0755 /usr/local/bin/k2-dbt \
    && /opt/k2/dbt-venv/bin/dbt --version \
    && k2-dbt --help >/dev/null

# dbt's profiles directory, target and log paths are the wrapper's flags (k2_dbt.py dbt_argv:
# /opt/k2/dbt and /tmp/k2-dbt), and its environment the wrapper's allow-list (dbt_env), so
# --read-only holds: everything dbt writes is on the /tmp tmpfs. No usage stats: nothing leaves
# for dbt Labs, for a dbt run by hand here too.
ENV PATH=/opt/k2/dbt-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

# Non-root. The operator runs it as the host user (--user) to read the password files; this uid
# is for any other run, and every file here is world-readable for either.
USER 10003:10003
WORKDIR /tmp

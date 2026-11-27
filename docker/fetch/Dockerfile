# The fetch one-shot (ADR-036; docs/development/phase-001-interfaces.md, "The fetch container"):
# one https-file fetch into the run's spool, /spool/<dagId>/<runId>/, then the product's parser,
# read from the release at /k2/releases/<id>. The Airflow one-shot operator runs it as
# `k2-fetch <args>` with a read-only root, a 64m noexec /tmp, every capability dropped and the
# compose project's k2-spool volume at /spool (docker/airflow/dags/oneshot.py).
#
# Standard library only: nothing is installed. It holds no secret and no storage key, and reaches
# only k2-net (A8). Built from the repository root as context (Dockerfile.dockerignore keeps it to
# fetch.py) and tagged fetch:<image id 12> by `make release` (manifest.schema.json).
# python 3.12.16 is the base docker/tools/Dockerfile pins, the newest 3.12 patch on 2026-11-27;
# its ca-certificates are the trust store the fetch verifies publishers against.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

COPY docker/fetch/fetch.py /opt/k2/fetch.py
# fetch.py has no shebang line; the command name is the operator's, `k2-fetch`.
# /spool is created owned by the image's user: a named volume first mounted there copies that
# ownership, so the operator needs no --user for fetch.
RUN printf '#!/bin/sh\nexec python3 /opt/k2/fetch.py "$@"\n' > /usr/local/bin/k2-fetch \
    && chmod 0755 /usr/local/bin/k2-fetch \
    && mkdir /spool && chown 10002:10002 /spool

# A read-only root: no bytecode written beside the release's parser, HOME on the tmpfs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# Non-root, a uid of its own (tools is 10001): only fetch writes the spool.
USER 10002:10002
WORKDIR /tmp

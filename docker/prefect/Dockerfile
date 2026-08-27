FROM prefecthq/prefect:3-python3.12

# ─────────────────────────────────────────────────────────────────────────────
# System dependencies
# ─────────────────────────────────────────────────────────────────────────────
# docker-cli: the worker dispatches lake jobs via `docker exec k2-spark-iceberg`.
# (Debian 13 splits the CLI out of docker.io; we only need the client.)
# Installing here (build time) instead of the command: entrypoint (runtime) avoids
# a ~60s apt-get run on every container restart.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
# prometheus-client: docker/lake/metrics.py serves the lake gauges in the
# lake-metrics service, which reuses this image.
# psycopg2-binary was dropped in Phase D: the only thing that imported it was
# the v2 offload's PostgreSQL watermark module, deleted with the rest of
# docker/offload/. Nothing in docker/lake/ talks to PostgreSQL.
RUN pip install --no-cache-dir \
    prometheus-client==0.21.1

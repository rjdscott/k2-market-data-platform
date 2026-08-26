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
# psycopg2-binary: no longer imported by anything in this repo — it went with
# docker/offload/watermark_pg.py in Phase D — but tests/ still install it via
# `make test-python`, so it is dropped here and nowhere else.
RUN pip install --no-cache-dir \
    prometheus-client==0.21.1

FROM prefecthq/prefect:3-python3.12

# ─────────────────────────────────────────────────────────────────────────────
# System dependencies
# ─────────────────────────────────────────────────────────────────────────────
# docker.io: the worker orchestrates offload jobs via `docker exec k2-spark-iceberg`.
# Installing here (build time) instead of the command: entrypoint (runtime) avoids
# a ~60s apt-get run on every container restart.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
# psycopg2-binary: flow imports watermark_pg for watermark checks in the worker.
# prometheus-client: metrics.py publishes offload metrics to Prometheus.
RUN pip install --no-cache-dir \
    psycopg2-binary==2.9.9 \
    prometheus-client==0.21.1

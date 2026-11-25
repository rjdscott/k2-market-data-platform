# The lake-metrics exporter (docker/lake/metrics.py), on its own pinned image.
#
# It ran on the Prefect worker image until ADR-034, only because that image
# already carried prometheus-client. Its own image means retiring Prefect does
# not take the lake alerts with it. Stdlib plus one package: metrics.py reads
# the catalog over urllib, deliberately not PyIceberg (see its docstring).
#
# The script itself is bind-mounted from docker/lake/ (docker-compose.yml), the
# same file the Spark container sees, so this image changes only when the
# Python or the client does.
FROM python:3.15.1-slim-trixie

RUN pip install --no-cache-dir prometheus-client==0.26.0

# It reads a REST API, stats one filesystem and serves :8000. None needs root.
USER nobody

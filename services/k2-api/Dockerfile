# k2-api (phase 003; services/k2-api/README.md): read-only REST over the data products of the
# release the k2-api pointer names, on :8095, which compose publishes on 127.0.0.1 only. Every data
# read runs on Trino as the caller, logged in as svc.api with the password file compose mounts; it
# holds no storage key and is never on k2-catalog (docker-compose.yml, service k2-api).
#
# Holds: Python 3.12, the dependencies services/k2-api/uv.lock locks (no dev group), the k2api
# package, and the frozen openapi.yaml beside it, served as /v1/openapi.json. Built from the
# repository root as context (Dockerfile.dockerignore) and tagged k2-api:<image id 12> by
# `make release`. python 3.12.16 and uv 0.13.10 are the pins docker/tools/Dockerfile carries.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64 AS venv

COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv
COPY services/k2-api/pyproject.toml services/k2-api/uv.lock /opt/k2/api-env/
# Bytecode compiled here: the root is read-only at run time, so it is never written then.
RUN UV_PROJECT_ENVIRONMENT=/opt/k2/api-venv UV_PYTHON=/usr/local/bin/python3.12 UV_PYTHON_DOWNLOADS=never \
      uv sync --locked --no-dev --no-cache --compile-bytecode --project /opt/k2/api-env

# The run image: the same base, so the venv's interpreter link holds; uv stays in the stage above.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

COPY --from=venv /opt/k2/api-venv /opt/k2/api-venv
COPY services/k2-api/openapi.yaml /opt/k2/api/openapi.yaml
COPY services/k2-api/k2api /opt/k2/api/k2api
WORKDIR /opt/k2/api
# The app imports, and reads the frozen document beside its package, before the image exists.
RUN /opt/k2/api-venv/bin/python -m compileall -q k2api \
    && /opt/k2/api-venv/bin/python -c "import k2api.app as a; assert a.SPEC['openapi'] == '3.1.0'"

ENV PATH=/opt/k2/api-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# Non-root, a uid of its own (tools 10001, fetch 10002, dbt-runner 10003). Compose runs it as the
# host user (`user: ${K2_RUN_UID}:0`) to read the mode-600 password and key files; every file here
# is world-readable for either. No HEALTHCHECK: compose asks /healthz.
USER 10004:10004
EXPOSE 8095
CMD ["uvicorn", "k2api.app:app", "--host", "0.0.0.0", "--port", "8095"]

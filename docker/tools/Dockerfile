# The tools one-shot (A13, ADR-058): the one container that mounts the releases root read-write.
# scripts/deploy.py runs it to swap the release pointers on the VM side of Docker Desktop's file
# sharing (spike c), to compile a release with the generator of the tag being released, and to
# reach the catalog on its own network. Built from the repository at the tag by `make release`,
# from the repository root as context, and tagged tools:<image id 12> (manifest.schema.json).
#
# Holds: a shell, Python 3.12, git, k2gen installed from platform/k2gen/uv.lock with the files it
# reads from the tree beside it (model.REPO: contracts/schema, platform/runs, dbt/macros), and
# k2tools.py, whose `sql` is the minimal Trino client. No Trino CLI: it needs a JVM.
# python 3.12.16 is the newest 3.12 patch on 2026-11-27; uv 0.13.10 is the one the Airflow image
# pins. git is Debian bookworm's own build: a security update that replaces it fails this build
# loudly at the pin, and the pin is moved in the same change.
FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 \
    && rm -rf /var/lib/apt/lists/*

# k2gen resolves model.REPO three directories above its own module, so it is installed editable
# inside this subset of the tree, and nothing else of the repository is copied in.
COPY platform/k2gen /opt/k2/platform/k2gen
COPY platform/runs /opt/k2/platform/runs
COPY contracts/schema /opt/k2/contracts/schema
COPY dbt/macros /opt/k2/dbt/macros
RUN UV_PROJECT_ENVIRONMENT=/opt/k2/venv UV_PYTHON=/usr/local/bin/python3.12 UV_PYTHON_DOWNLOADS=never \
      uv sync --locked --no-dev --no-cache --project /opt/k2/platform/k2gen \
    && /opt/k2/venv/bin/k2gen --help >/dev/null
# The trino client, for the catalog site's facts (the release's platform/catalog-site/k2site.py, run
# here after each deploy as svc.catalog): 0.340.0 and its dependencies at the versions
# services/k2-api/uv.lock locks for it, each named, --no-deps so nothing unnamed comes in, and
# `uv pip check` fails the build if the set is incomplete. None of them is one of k2gen's.
RUN UV_PYTHON_DOWNLOADS=never uv pip install --python=/opt/k2/venv/bin/python --no-cache --no-deps --only-binary=:all: \
      trino==0.340.0 requests==2.34.2 urllib3==2.8.0 certifi==2026.11.20 charset-normalizer==3.5.3 idna==3.20 \
      lz4==4.4.5 orjson==3.13.0 zstandard==0.25.0 python-dateutil==4.0.0 tzdata==2026.6 pytz==2026.6 tzlocal==5.4.5 \
    && uv pip check --python=/opt/k2/venv/bin/python \
    && /opt/k2/venv/bin/python -c "import trino, requests; assert trino.__version__ == '0.340.0'"
COPY docker/tools/k2tools.py /opt/k2/bin/k2tools
# /site: the k2-site volume's mount point, mode 1777 as k2-api's /srv/k2-site, so the volume's root
# is the same whichever image mounts it first, empty (docker copies the mount point's mode in).
# Deploy runs this image as the host user, whose uid no image knows: 1777 lets it write, and the
# sticky bit keeps one writer from renaming another's entry. The volume holds public pages only.
RUN chmod 0755 /opt/k2/bin/k2tools && mkdir -m 1777 /site

# The repository is mounted read-only by another uid when a release is compiled: git refuses a
# tree it does not own unless told it is safe. Nothing here writes to git.
ENV PATH=/opt/k2/venv/bin:/opt/k2/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=* \
    HOME=/tmp

# Non-root. Deploy runs it as the host user (--user), so the same works on a Linux engine
# without Docker Desktop's ownership mapping.
USER 10001:10001
WORKDIR /tmp

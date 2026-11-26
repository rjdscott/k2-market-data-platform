"""The one-time cutover to releases, rehearsed as a migration (`make deploy-rehearsal CUTOVER=1`), from
TODAY's stack shape on compose project k2-rehearsal: the procedure of
docs/runbooks/first-cutover-to-releases.md, then its rollback to today's shape. Imported by
deploy_rehearsal.py, whose helpers it uses (R).

Today's shape, as main runs before step S22: fixed container names, one shared network, Trino on plain
HTTP reading the checkout's docker/trino/etc, Lakekeeper on the shared network with a host port, a
stand-in capture. The cutover keeps the project name (renaming it would recreate every container, the
captures and Redpanda among them), joins MinIO and Postgres to the catalog's network without a
restart, compiles a release whose held services keep the images they run, and deploys it allowing
Lakekeeper alone of the stateful services, which must move networks and lose its host port.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from typing import Any

TODAY = """\
# Today's shape (main before step S22), for the cutover rehearsal: fixed names, one network, Trino on
# HTTP from the checkout's etc, Lakekeeper with a host port.
networks:
  k2-net: {}
volumes:
  pg: {}
  s3: {}
x-small: &small
  deploy: {resources: {limits: {cpus: '0.5', memory: 256M}}}
services:
  prefect-db:
    container_name: k2r-prefect-db
    image: postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b
    environment: {POSTGRES_USER: lk, POSTGRES_PASSWORD: "${PG_PASSWORD}", POSTGRES_DB: lakekeeper}
    networks: [k2-net]
    volumes: ["pg:/var/lib/postgresql/data"]
    healthcheck: {test: ["CMD", "pg_isready", "-U", "lk"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  minio:
    container_name: k2r-minio
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: ["server", "/data"]
    environment: {MINIO_ROOT_USER: "${MINIO_ROOT_USER}", MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"}
    networks: [k2-net]
    volumes: ["s3:/data"]
    healthcheck: {test: ["CMD", "curl", "-fs", "http://localhost:9000/minio/health/live"], interval: 2s, retries: 30}
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '0.5', memory: 512M}}}
  lakekeeper-migrate:
    container_name: k2r-lakekeeper-migrate
    image: quay.io/lakekeeper/catalog:v0.13.9
    command: ["migrate"]
    environment: &lk
      LAKEKEEPER__PG_DATABASE_URL_READ: "postgres://lk:${PG_PASSWORD}@prefect-db:5432/lakekeeper"
      LAKEKEEPER__PG_DATABASE_URL_WRITE: "postgres://lk:${PG_PASSWORD}@prefect-db:5432/lakekeeper"
      LAKEKEEPER__PG_ENCRYPTION_KEY: "${LAKEKEEPER_KEY}"
      LAKEKEEPER__BASE_URI: "http://lakekeeper:8181"
    networks: [k2-net]
    depends_on: {prefect-db: {condition: service_healthy}}
    restart: "no"
    <<: *small
  lakekeeper:
    container_name: k2r-lakekeeper
    image: quay.io/lakekeeper/catalog:v0.13.9
    command: ["serve"]
    environment: *lk
    networks: [k2-net]
    ports: ["127.0.0.1:${K2_LAKEKEEPER_PORT}:8181"]
    depends_on: {lakekeeper-migrate: {condition: service_completed_successfully}}
    healthcheck: {test: ["CMD", "/home/nonroot/lakekeeper", "healthcheck"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  trino:
    container_name: k2r-trino
    image: trinodb/trino:484
    networks: [k2-net]
    ports: ["127.0.0.1:${K2_TRINO_HTTP_PORT}:8080"]
    volumes: ["./docker/trino/etc:/etc/trino:ro"]
    environment: {MINIO_ROOT_USER: "${MINIO_ROOT_USER}", MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"}
    healthcheck: {test: ["CMD", "/usr/lib/trino/bin/health-check"], interval: 3s, retries: 60}
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '2.0', memory: 4G}}}
  capture-binance:
    container_name: k2r-capture-binance
    image: alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
    command: ["sleep", "infinity"]
    networks: [k2-net]
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '0.1', memory: 32M}}}
"""
NAMED = ("prefect-db", "minio", "lakekeeper-migrate", "lakekeeper", "trino", "prometheus", "standin", "capture-binance")


def release_shape(R: Any) -> str:
    """The release-shaped compose with today's fixed container names kept, so compose, under the same
    project, recognises each running container as its own and recreates it in place."""
    text = R.COMPOSE.replace("STANDIN_IMAGE", R.ALPINE_A)
    for svc in NAMED:
        text = re.sub(rf"^  {re.escape(svc)}:(.*)$", rf"  {svc}:\1\n    container_name: k2r-{svc}", text, count=1, flags=re.M)
    return text


def http(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> int | str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}), timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        return f"refused ({type(e).__name__})"


def started(R: Any, names: tuple[str, ...]) -> dict[str, str]:
    return {n: R.sh("docker", "inspect", "-f", "{{.State.StartedAt}}", f"k2r-{n}", check=False).stdout.strip() for n in names}


def rehearse(R: Any) -> int:
    lk_port, http_port = R.free_port(), R.free_port()
    R.DOTENV.update({"K2_LAKEKEEPER_PORT": lk_port, "K2_TRINO_HTTP_PORT": http_port})
    R.scratch_repo(TODAY)
    for d in ("docker/trino", "docker/lake"):  # today's Trino etc, read from the checkout
        R.sh("cp", "-r", str(R.REPO / d), str(R.CLONE / "docker"))
    R.dotenv()
    R.DOTENV.update({"K2_LAKEKEEPER_PORT": lk_port, "K2_TRINO_HTTP_PORT": http_port})
    (R.CLONE / ".env").write_text("".join(f"{k}={v}\n" for k, v in R.DOTENV.items()))
    R.sh("git", "-C", str(R.CLONE), "add", "-A")
    R.sh("git", "-C", str(R.CLONE), "commit", "-q", "-m", "today: the stack as main runs it before S22")
    today_commit = R.sh("git", "-C", str(R.CLONE), "rev-parse", "HEAD").stdout.strip()

    # Today's stack, running, with its lake
    R.compose("up", "-d", "--wait", "prefect-db", "minio", "lakekeeper", "capture-binance")
    warehouses = [w.strip() for w in (R.CLONE / "docker/lake/generated/warehouses.txt").read_text().splitlines()
                  if w.strip() and not w.startswith("#")]
    c = R.compose("ps", "-q", "minio").stdout.strip()
    R.sh("docker", "exec", c, "sh", "-c", 'MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc mb --ignore-existing l/lake')
    tools_today = R.sh("docker", "build", "-q", "-f", str(R.CLONE / "docker/tools/Dockerfile"), str(R.CLONE)).stdout.strip()
    print(R.sh("docker", "run", "--rm", "-i", "--network", f"{R.PROJECT}_k2-net", "--memory", "128m", "-e", "MINIO_ROOT_USER",
               "-e", "MINIO_ROOT_PASSWORD", tools_today, "python3", "-", "http://lakekeeper:8181", "k2", *warehouses,
               input=R.LAKE_SETUP, env=R.env()).stdout.strip().replace("\n", "; "))
    R.compose("up", "-d", "--wait", "trino")
    q_http = lambda: http(f"http://127.0.0.1:{http_port}/v1/statement", {"X-Trino-User": "today"}, b"SELECT 1")  # noqa: E731
    today_ok = (q_http(), http(f"http://127.0.0.1:{lk_port}/health"))
    R.check("today's shape runs: Trino answers on HTTP, Lakekeeper on its host port", today_ok == (200, 200), str(today_ok))
    held = started(R, ("prefect-db", "minio", "capture-binance"))

    # 1-2. secrets by the real tools; the .env lines the release shape reads
    R.real_secrets()
    # 3. the release-shaped compose at a tag; the release, its held services keeping what they run
    (R.CLONE / "docker-compose.yml").write_text(release_shape(R))
    t0 = time.monotonic()
    rid = R.cut("v0.0.1-cutover.1", "the cutover: release-shaped compose, Trino over HTTPS from the pointer")
    rel = R.make("release-status").stderr
    images = json.loads((R.ROOT / rid / "manifest.json").read_text())["images"]
    R.check("make release: the services deploy never recreates keep the images they run",
            all(images[s] for s in ("prefect-db", "minio", "lakekeeper", "capture-binance")), f"{rid} in {time.monotonic() - t0:.0f} s")
    # 4. the catalog's network made, MinIO and Postgres joined to it in place, no restart
    net = f"{R.PROJECT}_k2-catalog"
    R.sh("docker", "network", "create", "--label", f"com.docker.compose.project={R.PROJECT}",
         "--label", "com.docker.compose.network=k2-catalog", net)
    for svc in ("minio", "prefect-db"):
        R.sh("docker", "network", "connect", "--alias", svc, net, f"k2r-{svc}")
    # 5. the plan, then the first deploy, Lakekeeper the one stateful service allowed to move
    plan = R.make("deploy", f"RELEASE={rid}", "PLAN=1", check=False)
    print("\n".join(x for x in plan.stderr.splitlines() if x.startswith("  ")))
    t0 = time.monotonic()
    out = R.make("deploy", f"RELEASE={rid}", "ALLOW_STATEFUL=lakekeeper", check=False)
    took = time.monotonic() - t0
    down = dict(re.findall(r"^  (\S+) recreated, healthy in (\d+\.\d) s", out.stderr, re.M))
    R.check("the cutover deploy converges, exit 0", R.code(out) == 0, f"{took:.0f} s; down while recreated: {down}")
    if R.code(out) != 0:
        print(out.stderr[-5000:])
        return R.summary()
    after = started(R, ("prefect-db", "minio", "capture-binance"))
    R.check("MinIO, Postgres and the capture were never restarted", after == held, str({k: (held[k] == after[k]) for k in held}))
    ports = R.sh("docker", "port", "k2r-lakekeeper", check=False).stdout.strip()
    nets = R.sh("docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", "k2r-lakekeeper").stdout
    R.check("Lakekeeper is fenced: no host port, on the catalog's network only",
            not ports and list(json.loads(nets)) == [net] and isinstance(http(f"http://127.0.0.1:{lk_port}/health"), str),
            f"ports {ports or 'none'}, networks {list(json.loads(nets))}")
    R.check("Trino: plain HTTP gone, HTTPS by name with a password", isinstance(q_http(), str)
            and R.q("svc.catalog", "SELECT 1")["rows"] == [[1]])
    verify = R.make("release-verify", check=False)
    R.check("release-verify after the cutover", R.code(verify) == 0,
            " ".join(x.strip() for x in verify.stderr.splitlines() if "drift" in x or "identity" in x or "SKIP" in x)[:300])

    # 6. the rollback of the whole cutover to today's shape
    t0 = time.monotonic()
    R.sh("git", "-C", str(R.CLONE), "checkout", "-q", today_commit)
    gone = [s for s in ("prometheus", "standin") if R.sh("docker", "rm", "-f", f"k2r-{s}", check=False).returncode == 0]
    R.compose("up", "-d", "--no-deps", "--wait", "lakekeeper", "trino")
    for svc in ("minio", "prefect-db"):
        R.sh("docker", "network", "disconnect", net, f"k2r-{svc}")
    R.sh("docker", "network", "rm", net)
    os.rename(R.ROOT, R.BASE / "releases.rolled-back")  # nothing mounts it now; the next cutover is a first deploy
    back = (q_http(), http(f"http://127.0.0.1:{lk_port}/health"))
    R.check("the cutover rolled back: Trino on HTTP and Lakekeeper on its host port again", back == (200, 200),
            f"{back} in {time.monotonic() - t0:.0f} s; removed the release-only {gone}")
    R.check("and MinIO, Postgres and the capture were still never restarted", started(R, ("prefect-db", "minio", "capture-binance")) == held)
    R.ROOT = R.BASE / "releases.rolled-back"
    return R.summary()


if __name__ == "__main__":
    raise SystemExit("run it through deploy_rehearsal.py: make deploy-rehearsal CUTOVER=1")

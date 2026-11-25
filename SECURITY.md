# Security

This is a personal portfolio project designed to run on a single host, for one maintainer
([ADR-028](docs/adr/ADR-028-non-goals-and-honest-limits.md) section 8). It is **not**
hardened for production use. What is protected, and what is not, plainly:

## What protects it

- **Every published port binds `127.0.0.1`**, in every compose profile, and no service uses
  `network_mode: host`; CI fails either (`scripts/check-budget.py`, the Compose job).
  Containers reach each other over the compose network by service name (`minio:9000`),
  never through a published port, and host tools use `localhost`. Until 2026-11-26 fifteen
  of the nineteen published ports listened on every interface (fourteen in the default
  profile), MinIO's S3 API and console among them, so any machine on the same network could
  reach them.
- **Passwords from `.env`.** Copy `.env.example` and replace every `change-me-in-production`
  value with `openssl rand -hex 24` (`LAKEKEEPER_ENCRYPTION_KEY`: `openssl rand -base64 32`).
  `make up` and `make dev-up` refuse to start while a `*_PASSWORD`, `*_SECRET` or `*_KEY`
  is empty or still its placeholder, or while `MINIO_ROOT_USER` equals
  `MINIO_ROOT_PASSWORD` (`scripts/check-env.py`). It compares the values compose itself
  resolves (`docker compose config --environment`: `.env` and the shell), names variables
  and never prints a value. A bare `docker compose up -d` skips that check.
- **Trino's startup log no longer prints the S3 access key.** Trino logs every catalog
  property and masks only the secret key; `docker/trino/etc/log.properties` silences that
  logger, and `make test-trino` starts the real `etc/` with a canary key and fails if the
  log holds it.

## What does not

- **Loopback stops other machines, not other users or processes on this host.** Anything
  running on the host, and any container on the compose network, reaches every service. On
  Docker Desktop so does any container on the same engine, through `host.docker.internal`: a
  throwaway container on the default bridge reached Lakekeeper's loopback-only `18181` that
  way on 2026-11-26.
- **On Docker Engine before 28.0.0, loopback publishing does not stop the LAN either:**
  "hosts within the same L2 segment (for example, hosts connected to the same network
  switch) can reach ports published to localhost" (Docker documentation,
  [Port publishing and mapping](https://docs.docker.com/engine/network/port-publishing/)).
  This host runs Engine 29.1.3 under Docker Desktop. On an older engine, upgrade; check 3
  below, from another machine, is the one that proves it either way.
- **Several services have no authentication at all:** Redpanda (Kafka API, admin API and
  schema registry), Redpanda Console, Prometheus (with `--web.enable-lifecycle`), the Spark
  UIs, and Lakekeeper (`authz-backend` `allow-all`). Trino takes its user from the
  `X-Trino-User` header without checking it, so its rules stop mistakes, not a liar
  ([ADR-044](docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)).
- **The live MinIO root credential was MinIO's own well-known default pair, and it is
  compromised.** MinIO printed its `Detected default credentials` warning at each start,
  which it prints only for its built-in default, and the API and console listened on every
  interface, so any machine on the local network could sign in as root without reading any
  log. Trino's log printed the access key as well. One credential is held by every engine:
  MinIO, `lake-init`, Spark, the six Trino catalogs and each Lakekeeper warehouse's stored
  storage credential. It is being replaced, root rotated and a scoped key per engine, in the
  design [docs/research/2026-11-26-storage-credentials-least-privilege.md](docs/research/2026-11-26-storage-credentials-least-privilege.md),
  which also finds that Lakekeeper signs Spark's S3 requests with its stored copy of the root
  pair for any caller. Until that is rolled out, do not edit
  the pair in `.env` on a stack that holds data: each Lakekeeper warehouse keeps its own
  stored copy, and the lake stops writing.
- No TLS between services or on any port.
- The compose files under `docs/research/2026-08-26-v3-spikes/` are dated research
  snapshots, outside the CI gate, and publish on every interface. Run one only on a trusted
  network, or bind its `ports:` to `127.0.0.1` in your copy first.
- Secrets live in plain text in `.env` and in each container's environment, which
  `docker inspect` shows to anyone with the Docker socket. The Airflow scheduler and the
  Prefect worker hold that socket.
- Container images are pinned but not rebuilt on a schedule; Dependabot and a Trivy scan in
  CI surface known CVEs but nothing auto-patches.

## Check the binding

After any change to a `ports:` entry and the `docker compose up -d` that applies it, from
the repo root on the host (Linux; on macOS use `lsof -nP -iTCP -sTCP:LISTEN` and
`ipconfig getifaddr en0`):

```bash
# 1. No compose port listens on every interface: prints PASS, or the listeners at fault.
ports=$(docker compose --profile '*' config --format json | python3 -c 'import json, sys; print("|".join(sorted({p["published"] for s in json.load(sys.stdin)["services"].values() for p in s.get("ports") or []})))')
ss -ltnH | awk '{print $4}' | grep -E "^(\*|0\.0\.0\.0|\[::\]):($ports)$" || echo PASS

# 2. MinIO answers on loopback (200) and refuses on the host's LAN address (000).
LAN_IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')   # no packet is sent
curl -s --max-time 3 -o /dev/null -w 'loopback %{http_code}\n' http://127.0.0.1:9000/minio/health/live
curl -s --max-time 3 -o /dev/null -w 'lan %{http_code}\n' "http://$LAN_IP:9000/minio/health/live"
```

3. Then from another machine on the same network, the proof that does not route through the
host's own loopback: `curl --max-time 3 http://<LAN_IP>:9000/minio/health/live` must fail
with `Connection refused` or time out, and so must `:9001`, `:3000` and `:8080`.

On 2026-11-26, before the change reached the running stack, check 1 listed fourteen `*:`
listeners and check 2 printed `lan 200` for MinIO, while Lakekeeper's port, already on
loopback, printed `000` from the LAN address. The same checks after the rollout are not yet
run. <!-- VERIFY -->

## Reporting

If you find something worth reporting, open a [private security advisory](https://github.com/rjdscott/k2-market-data-platform/security/advisories/new) or an issue. There is no response-time commitment.

Only the `main` branch is maintained. `legacy/v1/` is archived code with [known issues](legacy/v1/README.md#known-issues-left-as-is) and is not deployed anywhere.

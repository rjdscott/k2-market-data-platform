# Security

This is a personal portfolio project designed to run on a single host, for one maintainer
([ADR-028](docs/adr/ADR-028-non-goals-and-honest-limits.md) section 8). It is **not**
hardened for production use. What is protected, and what is not, plainly:

## What protects it

- **Every published port binds `127.0.0.1`**, in every compose profile, and CI fails a
  port that does not (`scripts/check-budget.py`, the Compose job). Containers reach each
  other over the compose network by service name (`minio:9000`), never through a published
  port, and host tools use `localhost`. Until 2026-11-26 fifteen of the nineteen published
  ports listened on every interface (fourteen in the default profile), MinIO's S3 API and console among them, so any machine
  on the same network could reach them.
- **Passwords from `.env`.** Copy `.env.example` and replace every `change-me-in-production`
  value with `openssl rand -hex 24` (`LAKEKEEPER_ENCRYPTION_KEY`: `openssl rand -base64 32`).
  `make up` and `make dev-up` refuse to start while a `*_PASSWORD`, `*_SECRET` or `*_KEY`
  is empty or still its placeholder, or while `MINIO_ROOT_USER` equals
  `MINIO_ROOT_PASSWORD` (`scripts/check-env.sh`, which names variables and never prints a
  value). A bare `docker compose up -d` skips that check.
- **Trino's startup log no longer prints the S3 access key.** Trino logs every catalog
  property and masks only the secret key; `docker/trino/etc/log.properties` silences that
  logger, and `make test-trino` starts the real `etc/` with a canary key and fails if the
  log holds it.

## What does not

- **Loopback stops other machines, not other users or processes on this host.** Anything
  running on the host, and any container on the compose network, reaches every service.
- **Several services have no authentication at all:** Redpanda (Kafka API, admin API and
  schema registry), Redpanda Console, Prometheus (with `--web.enable-lifecycle`), the Spark
  UIs, and Lakekeeper (`authz-backend` `allow-all`). Trino takes its user from the
  `X-Trino-User` header without checking it, so its rules stop mistakes, not a liar
  ([ADR-044](docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)).
- **One MinIO root credential is held by every engine:** MinIO, `lake-init`, Spark, the six
  Trino catalogs and each Lakekeeper warehouse's stored storage credential. A credential in
  use before 2026-11-26 was reachable from the local network and printed in Trino's log:
  treat it as exposed. Rotating it, and replacing it with a scoped key per consumer, is the
  next change; until then do not edit the pair in `.env` on a stack that holds data, because
  each Lakekeeper warehouse keeps its own stored copy of it.
- No TLS between services or on any port.
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
ports=$(docker compose --profile legacy config --format json | python3 -c 'import json, sys; print("|".join(sorted({p["published"] for s in json.load(sys.stdin)["services"].values() for p in s.get("ports") or []})))')
ss -ltnH | awk '{print $4}' | grep -E "^(\*|0\.0\.0\.0|\[::\]):($ports)$" || echo PASS

# 2. MinIO answers on loopback (200) and refuses on the host's LAN address (000).
LAN_IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')   # no packet is sent
curl -s --max-time 3 -o /dev/null -w 'loopback %{http_code}\n' http://127.0.0.1:9000/minio/health/live
curl -s --max-time 3 -o /dev/null -w 'lan %{http_code}\n' "http://$LAN_IP:9000/minio/health/live"
```

Then from another machine on the same network, the proof that does not route through the
host's own loopback: `curl --max-time 3 http://<LAN_IP>:9000/minio/health/live` must fail
with `Connection refused` or time out, and so must `:9001`, `:3000` and `:8080`.

On 2026-11-26, before the change reached the running stack, check 1 listed fourteen `*:`
listeners and check 2 printed `lan 200` for MinIO, while Lakekeeper's port, already on
loopback, printed `000` from the LAN address. The same checks after the rollout are not yet
run. <!-- VERIFY -->

## Reporting

If you find something worth reporting, open a [private security advisory](https://github.com/rjdscott/k2-market-data-platform/security/advisories/new) or an issue. There is no response-time commitment.

Only the `main` branch is maintained. `legacy/v1/` is archived code with [known issues](legacy/v1/README.md#known-issues-left-as-is) and is not deployed anywhere.

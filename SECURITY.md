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
  `make up` and `make dev-up` refuse to start while any password, secret or key compose
  passes to a service is empty, still its placeholder, equal to the user it is paired with,
  a known default, or shorter than 16 characters (`scripts/check-env.py`, one table of every
  credential and its user). It compares the values compose itself
  resolves (`docker compose config --environment`: `.env` and the shell), names variables
  and never prints a value. A bare `docker compose up -d` skips that check.
- **Trino's startup log no longer prints the S3 access key.** Trino logs every catalog
  property and masks only the secret key; the `log.properties` k2gen compiles into each
  release's Trino etc (`platform/k2gen/k2gen/emit/trino_etc.py`, pinned by its golden file)
  silences that logger. Until 2026-11-27 `make test-trino` also started the checkout's
  `docker/trino/etc` with a canary key and read the log for it; that etc and that check
  went when the etc moved into releases.
- **Trino logins are by password over HTTPS** under a local CA (ADR-060): plain HTTP is off,
  the CA and Trino's certificate come from `make tls` and each password from
  `make password PRINCIPAL=<id>`, all under `${K2_SECRETS}` (default `~/k2-secrets`), never
  the repository. Its rules are compiled default-deny for each release (ADR-059).
- **Lakekeeper is fenced by network, not by login.** Since the compose file of 2026-11-27 it
  is on the network `k2-catalog` alone, with no host port, and only the platform's own
  services join that network (ADR-059 part 7). `make catalog-fence` dials every probe row of
  `tests/access/expectations.yaml` and fails if the fence is open where it must be refused.

## What does not

- **Loopback stops other machines, not other users or processes on this host.** Anything
  running on the host, and any container on the compose network, reaches every service. On
  Docker Desktop so does any container on the same engine, through `host.docker.internal`: a
  throwaway container on the default bridge reached Lakekeeper's loopback-only `18181` that
  way on 2026-11-26. The compose file of 2026-11-27 removed that port: Lakekeeper is now
  reachable only from `k2-catalog`, so this path is closed for it, and still open for every
  service that publishes a port.
  <!-- VERIFY: after the live cutover, nothing listens on 18181 (ss -ltnH | grep ':18181$' prints nothing) and make catalog-fence passes -->
- **The MinIO root pair still reaches Trino.** Trino's S3 keys are `K2_TRINO_S3_*`, set to
  MinIO's root user and password until phase 002 gives it a scoped key.
- **On Docker Engine before 28.0.0, loopback publishing does not stop the LAN either:**
  "hosts within the same L2 segment (for example, hosts connected to the same network
  switch) can reach ports published to localhost" (Docker documentation,
  [Port publishing and mapping](https://docs.docker.com/engine/network/port-publishing/)).
  This host runs Engine 29.1.3 under Docker Desktop. On an older engine, upgrade; check 3
  below, from another machine, is the one that proves it either way.
- **Several services have no authentication at all:** Redpanda (Kafka API, admin API and
  schema registry), Redpanda Console, Prometheus (with `--web.enable-lifecycle`), the Spark
  UIs, and Lakekeeper (`authz-backend` `allow-all`; fenced by network, above). Until the
  2026-11-27 compose file Trino took its user from the `X-Trino-User` header without checking
  it ([ADR-044](docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)); it now asks
  for a password (ADR-060).
- **The live MinIO root credential was MinIO's own well-known default pair, and it is
  compromised.** MinIO printed its `Detected default credentials` warning at each start,
  which it prints only for its built-in default, and the API and console listened on every
  interface, so any machine on the local network could sign in as root without reading any
  log. Trino's log printed the access key as well. One credential is held by every engine:
  MinIO, `lake-init`, Spark, the six Trino catalogs and each Lakekeeper warehouse's stored
  storage credential. It is being replaced, root rotated and a scoped key per engine, in the
  follow-up design `docs/research/2026-11-26-storage-credentials-least-privilege.md`
  (branch `docs/storage-credentials-least-privilege`). Until that is rolled out, do not edit
  the pair in `.env` on a stack that holds data: each Lakekeeper warehouse keeps its own
  stored copy, and the lake stops writing.
- No TLS between services or on any port except Trino's (HTTPS only, ADR-060).
- The compose files under `docs/research/2026-08-26-v3-spikes/` are dated research
  snapshots, outside the CI gate, and publish on every interface. Run one only on a trusted
  network, or bind its `ports:` to `127.0.0.1` in your copy first.
- Secrets live in plain text in `.env` and in each container's environment, which
  `docker inspect` shows to anyone with the Docker socket. The Airflow scheduler and cAdvisor
  hold that socket (cAdvisor read-only, which does not narrow it). The local CA's key, Trino's
  `password.db` (bcrypt lines) and each service's password file are plain files under
  `${K2_SECRETS}`, protected only by the host user's file permissions.
- Container images are pinned but not rebuilt on a schedule; Dependabot and a Trivy scan in
  CI surface known CVEs but nothing auto-patches. Trivy scans the repository's lockfiles and
  fails CI on a CRITICAL with a fix available unless `.trivyignore.yaml` accepts it with a
  reason and an expiry date. The third-party images compose runs are scanned by nothing yet.

## Check the binding

After any change to a `ports:` entry and the `docker compose up -d` that applies it, from
the repo root on the host (Linux; on macOS use `lsof -nP -iTCP -sTCP:LISTEN` and
`ipconfig getifaddr en0`):

```bash
# 1. No compose port listens on every interface: prints PASS, or the listeners at fault.
ports=$(docker compose --profile '*' config --format json | python3 -c 'import json, sys; print("|".join(sorted({p["published"] for s in json.load(sys.stdin)["services"].values() for p in s.get("ports") or []})))')
ss -ltnH | awk '{print $4}' | grep -E "^(\*|0\.0\.0\.0|\[::\]):($ports)$" || echo PASS

# 2. MinIO publishes no port (W1-23): loopback and the host's LAN address both refuse (000).
LAN_IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')   # no packet is sent
curl -s --max-time 3 -o /dev/null -w 'loopback %{http_code}\n' http://127.0.0.1:9000/minio/health/live
curl -s --max-time 3 -o /dev/null -w 'lan %{http_code}\n' "http://$LAN_IP:9000/minio/health/live"
```

3. Then from another machine on the same network, the proof that does not route through the
host's own loopback: `curl --max-time 3 http://<LAN_IP>:9000/minio/health/live` must fail
with `Connection refused` or time out, and so must `:9001`, `:3000` and `:8080`.

On 2026-11-26, before the change reached the running stack, check 1 listed fourteen `*:`
listeners and check 2 printed `lan 200` for MinIO, while Lakekeeper's port, already on
loopback, printed `000` from the LAN address. After the rollout (2026-11-25 23:50 to 23:54
UTC), run 2026-11-26 at about 01:30 UTC: check 1 printed `PASS`, check 2 printed
`loopback 200` and `lan 000`, and the LAN address answered `000` on 9001, 3000, 8080, 8090 and
18181 as well. Check 3, from another machine, is not yet run. <!-- VERIFY: check 3 from a second machine -->
Since the compose file of 2026-11-27, 18181 is not published at all (Lakekeeper is fenced
onto `k2-catalog`) and Trino's 8088 is replaced by HTTPS on 8443. Since W1-23 neither are
MinIO's 9000 and 9001: the lake's objects are reached from a container on its networks, and the
host reaches them through Trino alone, so check 2 prints `loopback 000` too.
<!-- VERIFY: after the deploy that recreates minio, check 2 prints loopback 000 and lan 000, and make catalog-fence passes its host rows -->

## Reporting

If you find something worth reporting, open a [private security advisory](https://github.com/rjdscott/k2-market-data-platform/security/advisories/new) or an issue. There is no response-time commitment.

Only the `main` branch is maintained. `legacy/v1/` is archived code with [known issues](legacy/v1/README.md#known-issues-left-as-is) and is not deployed anywhere.

# Runbook: First cutover of the live stack to releases

Use this once, for phase 001 step S22: moving the running stack from reading the git checkout to
running releases (Trino over HTTPS from the release pointer, Lakekeeper fenced onto its own network),
and, if it goes wrong, moving it back to today's shape. Every later change is an ordinary
[deploy](./deploy-a-release.md). Why: [ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md),
[ADR-059](../adr/ADR-059-access-rules-compiled-per-release-default-deny.md) part 7 (the catalog fence),
[ADR-060](../adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md) (identity).

**Run every command from the live checkout with `set -a && . ./.env && set +a` loaded.**

> **Target state: where these commands ran.** On 2026-11-27, as a migration, on the rehearsal
> (`make deploy-rehearsal CUTOVER=1`, [`scripts/verify/deploy_cutover.py`](../../scripts/verify/deploy_cutover.py)):
> compose project `k2-rehearsal` started in TODAY's shape (fixed container names `k2r-*`, one shared
> network, Trino on plain HTTP from the checkout's `docker/trino/etc`, Lakekeeper with a host port, a
> stand-in capture), then this procedure, then its rollback. Never on the live stack. Below, `k2-` is
> the live container prefix and `$P` the live project name; the outputs are the rehearsal's.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | The cutover, from the release to verified | <15 min | MEASURE_CUTOVER |
| 2 | Each service's outage during it | | MEASURE_DOWN |
| 3 | The rollback to today's shape | <10 min | MEASURE_BACK |

---

## Before you start

**The compose project name stays as it is.** The live containers belong to the project named after
the checkout's directory, and carry fixed `container_name`s. A container's project is a label set when
it is created, so renaming the project (A1's `-p k2`) would recreate every container, the captures,
Redpanda and MinIO among them, or leave the old ones running beside new ones of the same name, which
`docker` refuses. Deploy is told the existing name instead (`K2_COMPOSE_PROJECT`), and compose, under
that name, recognises each running container as its own and recreates only what the plan names, in
place.

**What the cutover recreates, and what it never touches.**

| Service | At the cutover | Why |
|---|---|---|
| captures, `redpanda`, `minio`, `prefect-db` | never restarted | deploy never recreates them (A1); MinIO and Postgres join the catalog's network in place (`docker network connect`) |
| `lakekeeper` | recreated once, `ALLOW_STATEFUL=lakekeeper` | it leaves `k2-net` and loses its host port: both are set only when a container is created |
| `trino` | recreated | the etc through the pointer, HTTPS on 8443, the secrets mounts, the shared secret |
| every other default-profile service | recreated once, each in seconds | its image is now pinned by digest from the manifest, a new definition to compose; one-shots rerun |
| profiled services not running (`clickhouse`, the stubs) | never started | |

**Compose shape S22 writes** (release shape, kept in the tag): every service that reads a pointer
mounts `${K2_RELEASES}` read-only at `/k2/releases` (the `x-k2-releases-ro` anchor); Trino runs
`/usr/lib/trino/bin/launcher run --etc-dir=/k2/releases/pointers/trino/compiled/trino/etc`, mounts
`${K2_SECRETS}/tls/trino.pem` as a FILE at `/run/k2-secrets/tls/trino.pem` (never the `tls/`
directory, which holds `ca.key`) and `${K2_SECRETS}/trino` read-only at `/run/k2-secrets/trino`,
takes `K2_TRINO_SHARED_SECRET`, `K2_TRINO_S3_ACCESS_KEY` and `K2_TRINO_S3_SECRET_KEY`, publishes
`127.0.0.1:${K2_TRINO_PORT:-8443}:8443`, and has its own HTTPS health check (the image's
`health-check` reads `/etc/trino/config.properties`, absent here, and so probes HTTP 8080):
`bash -c 'i=$(curl -fsk https://localhost:8443/v1/info) && [[ $i == *"\"starting\":false"* ]]'`.
Lakekeeper is on `k2-catalog` only, with no `ports:`. MinIO and Postgres are on both networks. Keep
every `container_name`. The rehearsal's release-shaped file is `COMPOSE` in
[`deploy_rehearsal.py`](../../scripts/verify/deploy_rehearsal.py), with today's names kept by
`deploy_cutover.release_shape`.

---

## 1. The cutover

**Symptom**, phase 001's branch is merged and its release tag approved; the stack runs today's shape.

**Detection**, manual.

**Expected behaviour**, the steps below in order; each is safe to rerun. Nothing before step 7 changes
what runs.

**Recovery**

```bash
# 0. preconditions: memory for a throwaway lake beside the stack, the stack healthy
awk '/MemAvailable/{print $2/1048576}' /proc/meminfo        # 10 or more
make health
# 1. a copy of what nothing can rebuild (docs/runbooks/lake-backup-and-restore.md)
make backup
# 2. the project name the live containers carry: deploy must use it, never rename it
P=$(docker inspect k2-trino --format '{{index .Config.Labels "com.docker.compose.project"}}'); echo "$P"
# 3. secrets, outside the checkout (ADR-060): the CA and Trino's certificate, each service's password
make tls
for id in svc.runs svc.catalog svc.platform svc.api dbt_access_canary pub_access_canary; do
  make password PRINCIPAL=$id TO_FILE=1; done
# 4. .env: K2_COMPOSE_PROJECT=$P, K2_HOME=<this checkout's absolute path>, K2_TRINO_PORT=8443,
#    K2_TRINO_SHARED_SECRET=$(openssl rand -hex 32), K2_TRINO_S3_ACCESS_KEY and K2_TRINO_S3_SECRET_KEY
#    (MinIO's root pair until phase 002's scoped keys); then load it again
set -a && . ./.env && set +a
# 5. the checkout at the release's tag (deploy refuses a checkout whose emitter differs), the release
git checkout <tag>
make release TAG=<tag>          # captures, Redpanda, MinIO, Postgres, Lakekeeper keep the images they run
# 6. the catalog's network, and MinIO and Postgres joined to it in place: no restart
docker network create --label com.docker.compose.project=$P --label com.docker.compose.network=k2-catalog ${P}_k2-catalog
docker network connect --alias minio ${P}_k2-catalog k2-minio
docker network connect --alias prefect-db ${P}_k2-catalog k2-prefect-db
# 7. the plan, then the first deploy: Lakekeeper the one stateful service allowed to move
make deploy RELEASE=<release id> PLAN=1
make deploy RELEASE=<release id> ALLOW_STATEFUL=lakekeeper
make release-verify
```

CUTOVER_OUTPUT

**Measured**, MEASURE_CUTOVER_TEXT

**Not covered by the rehearsal**: the live services the rehearsal has no copy of (Airflow, Spark,
Grafana, cAdvisor, Alertmanager, Redpanda Console, the one-shots), each recreated once by the same
`up -d --no-deps`; and the live canary. Until phase 002 publishes the access canary, release-verify's
live suite is a visible SKIP (`not yet published`); once it is published, its readers are people,
whose passwords `make password` never files, while the live suite reads them from
`${K2_SECRETS}/principals/p.canary-*.password`: decide how those files are made before then.

---

## 2. Rolling the cutover back to today's shape

**Symptom**, the cutover deploy exited 3 (a first deploy has no release to go back to), or verified
but something the rehearsal did not cover is wrong.

**Detection**, the deploy's output; `make release-verify`; `make health`.

**Expected behaviour**, today's compose recreates what the cutover recreated, MinIO and Postgres leave
the catalog's network in place, and the releases root is moved aside, since nothing mounts it any
more and the next cutover must start as a first deploy. Captures, Redpanda, MinIO and Postgres are
never restarted.

**Recovery**

```bash
git checkout <the commit before the cutover>
docker compose -p $P up -d --no-deps --wait lakekeeper trino   # and each other service the cutover's plan recreated
docker network disconnect ${P}_k2-catalog k2-minio
docker network disconnect ${P}_k2-catalog k2-prefect-db
docker network rm ${P}_k2-catalog
mv "${K2_RELEASES:-$HOME/k2-releases}" "${K2_RELEASES:-$HOME/k2-releases}.rolled-back-$(date -u +%F)"
make health
```

BACK_OUTPUT

**Measured**, MEASURE_BACK_TEXT

---

## Failure modes / incidents

- None on the live stack yet.

---

**Last verified:** 2026-11-27 as a migration on the rehearsal, branch `step/s19b-deploy-integration`;
not yet on the live stack.

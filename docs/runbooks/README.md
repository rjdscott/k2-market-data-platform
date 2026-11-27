# Runbooks

Incident procedures for the running stack: the capture tier, Redpanda, the Iceberg catalog and
object store, Airflow and the host under them. The ClickHouse hot tier is paused behind the compose
profile `hot-gold` ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)): its sections and runbooks apply only while that profile
runs, and [pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md) is how it is paused and started.
The v3 lake ingest pipeline is retired and archived in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/) with its runbooks
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)): no live crypto data reaches the lake
until [phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan, and the captures keep writing to Redpanda meanwhile. What the lake
receives since [phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md)
is the file products, one DAG each: the ISO 4217 currency list, the TARGET calendar and the ECB
reference rates. Runbooks record
*how*; ADRs record *why* (`../adr/`). Every Prometheus alert annotation points at one of
these; start from the alert that fired, or from the "when to use" column below. Day-to-day
operation (start, stop, health, consoles, recreating one service, rolling out a change) is
not a runbook: it is the [operator guide](../operations/README.md).

Load secrets before running any command here: `set -a && . ./.env && set +a`

## Conventions

- One task per file, `<slug>.md`. Updating an existing runbook beats a near-duplicate.
- Shape: **symptom → detection (name the alert) → expected behaviour → recovery commands
  → measured MTTR**. [`template.md`](./template.md) is that shape, empty.
- Exact copy-pasteable commands, never paraphrases. Verify them before writing them down;
  a runbook nobody ran is fiction.
- MTTR is measured by inducing the failure, not estimated.
- No decision rationale here, link the ADR.
- A PR that invalidates a runbook's steps updates it and its index row in the same PR.
- Every alert in `docker/prometheus/rules/` names a runbook as a `runbook:` annotation,
  and that path must resolve. Two gates in `scripts/check-docs.sh`, because one was not
  enough: **(d)** the paths present resolve, **(d2)** every `- alert:` block has one.
  Only (d) existed until 2026-09-03, and the six ClickHouse rules passed it by carrying
  no annotation at all — the last `**Runbook:**`-inside-the-description holdout was
  converted then, so the annotation form is now the only form.
- Write it with the `/runbook` skill.

## Index

### Infrastructure (written for the v2 stack)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [failure-recovery.md](./failure-recovery.md) | The infrastructure failures tested on 2026-02-19: broker restart, ClickHouse restart, capture crash, MinIO down, network partition; the batch-job failure (§4) retired with the v3 lake ingest. Plus §7, ClickHouse resource pressure: diagnosed, **not induced**, no MTTR. Plus §8, a host or Docker VM killed: when it happened, and recording the loss window. The ClickHouse sections need the `hot-gold` profile running (ADR-057), and the six ClickHouse alerts are removed while it is paused | `CaptureDown`, `CaptureProduceErrors` |
| [redpanda.md](./redpanda.md) | Topic, partition, consumer-group or schema-registry problems; broker health | `CaptureProduceErrors` |

The six runbooks for the v2 ClickHouse→Iceberg offload were archived with the code they
described; they are kept unmodified in
[`legacy/v2-offload/runbooks/`](../../legacy/v2-offload/README.md) and describe a path that
no longer exists, do not follow them against this stack.

### v3 capture tier (Phase C: written ahead of the code; see the note below)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [capture-down.md](./capture-down.md) | A `k2-capture` container is down or crash-looping, or is running but failing to produce to Redpanda | `CaptureDown`, `CaptureProduceErrors` |
| [capture-produce-stalled.md](./capture-produce-stalled.md) | Capture is receiving from the exchange but producing nothing to Redpanda, before the queue fills and starts dropping | `CaptureProduceStalled` |
| [capture-feed-stale.md](./capture-feed-stale.md) | Container up and scrapeable but a stream has gone silent; or exchange→receive p99 has stepped up | `CaptureFeedStale`, `CaptureIngressLatencyHigh` |
| [capture-sequence-gaps.md](./capture-sequence-gaps.md) | Exchange sequence continuity broke (messages lost), or the book keeps being resynced | `CaptureSequenceGaps`, `CaptureResyncStorm` |
| [capture-checksum-failure.md](./capture-checksum-failure.md) | Kraken CRC32 mismatch, book thinner than top-20, or a venue quoting finer than 8 dp | `CaptureChecksumFailure`, `CaptureBookDepthDegraded`, `CapturePrecisionLoss` |

> **These four carry no measured MTTR yet, and say so on every row.** The capture tier
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) is built and running; what has not
> happened is a fault injection. Commands run against it are marked ✅. The Phase C
> chaos run
> (`make chaos`) induces each failure, waits for the alert, and fills in the
> **Measured** rows and the **Last verified** stamp. Until then they are procedures,
> not measurements, which is exactly the distinction this directory's conventions
> exist to protect.

### Orchestration (Airflow 3)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [airflow-scheduling.md](./airflow-scheduling.md) | How a product's DAG runs (the DAG folder through the release pointer, the factory, pools `lake_spark`, `fetch`, `dbt`, one-shots); the scheduler stopped (§1, drilled on 2026-11-25), a task failed, per task (§2), a DAG missing, paused or stale (§3), triggering and reading a product's run and its run record (§4). The product sections written from the code at this release, **not yet run on the live stack**; the v3 lake's lock and catch-up sections retired with its DAGs | `AirflowSchedulerHeartbeatLost`, `AirflowTaskFailed` |

### File products (phase 002)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [connector-failure.md](./connector-failure.md) | A file product's `fetch` failed or was skipped: exit 10 (unchanged, not a failure), 20 (refused: scheme, host, redirect, address, size), 30 (the parse failed, the bytes kept), 1 (network, TLS, HTTP status, the 300 s deadline); the one-shot never started (image, network `k2-egress`, the spool helper); never widening `allowHosts` without a contract change. Written from the code at this release, **not yet run on the live stack**; the exits unit-tested (`make test-connectors`) | `AirflowTaskFailed` (`task_id="fetch"`); no alert specific to a product yet |
| [file-source-audit-failed.md](./file-source-audit-failed.md) | A landed file failed its bronze audit, or a later file's `load` waits behind it: reading `audit_checks` and `landing_files` in Spark (no Trino principal may), a bad file versus a wrong check, resetting branch `audit` to `main` as the Spark key, rerun and verify. Written from the code at this release, **not yet run on the live stack** | `AirflowTaskFailed` (`task_id="audit"` or `"load"`); no alert specific to an audit yet |
| [onboard-a-file-product.md](./onboard-a-file-product.md) | Adding a file product from nothing: its documents, parser and synthetic fixtures, the checks and tests, the pull request, tag, release, its two principals' passwords, deploy, the first run, its run records and catalog page, with the three products of phase 002 as the worked example. The unit steps run; **the live steps not yet run** | none, manual |

### Host / operations

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [fresh-install.md](./fresh-install.md) | Bringing the stack up from a fresh clone or after a Docker wipe: prerequisites, the measured timeline, what `docker compose ps` should show, and the capture image build race. Since phase 002: `make engine-keys`, lake-init from the release's own script, the secrets list, MinIO without host ports; **not yet run on the live stack** | none, manual check |
| [docker-desktop-disk.md](./docker-desktop-disk.md) | Host `/` filling while `docker system df` reports far less: reclaiming space from a bloated `Docker.raw` with `fstrim`. Read the warning before touching the disk-image-size slider. Section 2: the VM disk the volumes share filling from inside | section 1 none, manual check; section 2 `DockerDiskUsageHigh`, `DockerDiskUsageCritical` |
| [lake-backup-and-restore.md](./lake-backup-and-restore.md) | The host copy of what nothing can rebuild, and its restore: the live release's backup set (`compiled/lake/backup-set.json`: restore tables at their ref, landing prefixes, the releases root, the catalog and Airflow dumps) copied in its tools image with the read-only backup key to `runs/<UTC>/` with a `manifest.json`; `make backup`, `make backup-install`, `make backup-verify`, `make health` step (5); what it does not protect against (same disk, writable by anything running as you); the copies of the four retired tables stay under `lake/`. Written from the code at this release, **not yet run on the live stack**; **the restore is not yet drilled: phase 004's drill** | none: `make health` step (5), manual check |
| [docker-vm-oom-kill.md](./docker-vm-oom-kill.md) | Every container gone at once and `systemctl --user status docker-desktop` says `Result: oom-kill`: the host's out-of-memory killer ended the Docker VM. Confirm the cause, start Docker Desktop, start what did not come back, record the capture outage; then size the VM against host RAM. One occurrence, 2026-11-25: 10 min 13 s from the kill to `make health` PASS, capture off 343 s | none: no container can see host memory, manual check |
| [pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md) | Pausing the ClickHouse hot tier behind the compose profile `hot-gold`, or starting it again (`docker compose --profile hot-gold up -d clickhouse`, never a bare `up -d`): the two 7-day clocks (committed offsets, topic retention) and the hole a resume after them leaves, the consumer-group and lag checks after a resume, and why no reload from the lake can fill that hole any more. Read-only steps run 2026-11-26; the stop, remove and start rendered with `--dry-run` only | none: ADR-057 removes the ClickHouse alerts with the pause, manual check |
| [drop-and-rebuild.md](./drop-and-rebuild.md) | Deleting a lake table, view or namespace, or a MinIO bucket: `make drop` takes a full `make backup` first (since phase 002), then copies it to `$K2_BACKUP_DIR/drop/<date>/`, compares count and bytes, then purges it through the catalog or removes the bucket; `DRY_RUN=1` lists only. What it refuses, a drop that stopped, and restoring a table or bucket from its copy (drilled 2026-11-26 on scratch objects) | none, manual check |
| [upgrade-a-pinned-component.md](./upgrade-a-pinned-component.md) | Moving a base image pinned by digest, or a jar baked in with its sha256, to a new release: find the tag, resolve the digest, the jar versions a Spark release names and their sha256, every place a pin lives, a build and a read-only proof under a tag no service runs, the rollback. Worked example: the Spark rebase onto the official image, 2026-11-26; the build and proof measured, **the rollback not yet run** | none, manual check (Dependabot's monthly docker pull requests) |
| [where-an-alert-goes.md](./where-an-alert-goes.md) | Following an alert from its rule to Slack; installing or rotating the Slack webhook (`make alerts-install-webhook`, stdin only); testing delivery (`make alert-test`, and `make alert-test-standin` for the whole path with nothing live touched); silencing; what the not-installed state looks like; no messages arriving; Alertmanager down. Run 2026-11-26 on throwaway copies of the service; live since 2026-11-27 only `make alert-test` (delivery PASS) | `AlertmanagerDown`, `AlertmanagerNotificationsFailing` |
| [create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md) | Giving a declared principal a Trino password (`make password`, `TO_FILE=1` for a platform service's password file) or a k2-api key (`make api-key`, `TO_FILE=1` for the access suite's key file under `api-keys/`), printed once with only its hash kept under `${K2_SECRETS}`; rotating one (`ROTATE=1`); who has which (`make principals-status`); a login refused because `password.db` was renamed over. Run 2026-11-26 with temporary secrets on a throwaway Trino 484, **not yet on the live stack** (step S22 mounts the secrets) | none, manual check |
| [verify-access-after-a-release.md](./verify-access-after-a-release.md) | Proving a release keeps its access promises: `make access-verify RELEASE=<dir>` (every hand-written row of `tests/access/expectations.yaml` on a throwaway lake, 27 s), `make access-verify LIVE=1` (the canary's and the platform's rows on the stack), reading a failing row, the catalog drift check and the tested-data marker, rolling back (`make rollback`, step S19), and `make test-access`, the proof that each check can fail. Run 2026-11-26 on throwaway lakes; live since 2026-11-27 only `make release-verify` (exit 0, the live suite a SKIP). Granting and revoking: [grant-and-revoke-access.md](./grant-and-revoke-access.md). Since phase 002 `LIVE=1` runs in a tools one-shot with the storage-key rows (the five pairs from `.env`) and the REST rows (`${K2_SECRETS}/api-keys/<principal>.key`, `make api-key TO_FILE=1`); **not yet run on the live stack** | none, manual check (step S19's `release-verify` runs it) |
| [tls-and-passwords.md](./tls-and-passwords.md) | Setting up Trino's identity from nothing (`make tls-init`, a password file per service), rotating a service's password, reissuing Trino's server certificate (`make tls-rotate`, served within a minute, no restart), replacing the local CA (`make tls-init FORCE=1`, a minute of refused connections), and what an expired or wrong certificate looks like from the CLI, curl, Python and dbt; `make tls-status` exits 1 within 30 days of an expiry. Run 2026-11-26 on throwaway lakes and temporary secrets, **not yet on the live stack** (step S22) | none, manual check (`make tls-status`) |
| [deploy-a-release.md](./deploy-a-release.md) | Putting an approved tag live: `make release TAG=...` compiles a sealed release, `make release-diff` and `PLAN=1` show what a deploy will do, `make deploy` moves the pointers from the tools one-shot and recreates only what changed, `make release-verify` and `make release-status` ask every part what it runs; a refused deploy (exit 2), one that rolled itself back (exit 1), one interrupted. Run 2026-11-27 on the rehearsal `make deploy-rehearsal`, live only as the first deploy, the cutover of 2026-11-27 (step S22 moved it onto releases). Since phase 002 a deploy also drains the DAGs a change affects (`ALLOW_RUNNING=1`), removes the services a release puts behind a profile, creates missing networks, asks k2-api `GET /v1/release`, runs the live access suite in a tools one-shot (the `.env` loaded, for the five storage-key pairs), and regenerates the catalog site and dbt docs (`make site`, never failing a deploy); `ALLOW_STATEFUL=minio` for the one deploy that removes MinIO's host ports. Those steps **not yet run on the live stack** | none, manual check |
| [roll-back-a-release.md](./roll-back-a-release.md) | Going back to the previous good release (`make rollback`), images included; an interrupted rollback; a rollback that failed (exit 3); a first deploy with nothing to go back to. Run 2026-11-27 on the rehearsal, **not yet on the live stack**; a rollback to a release before phase 002 (§6): what it removes and what it leaves in place | none, manual check |
| [first-cutover-to-releases.md](./first-cutover-to-releases.md) | The one-time move of the live stack onto releases (phase 001 step S22): keep the compose project name, join MinIO and Postgres to `k2-catalog` in place, `make release` keeping what deploy never recreates, `make deploy ALLOW_STATEFUL=lakekeeper`; which services are recreated and for how long; and the rollback of the whole cutover to today's shape. Rehearsed 2026-11-27 as a migration from today's shape on `k2-rehearsal`, **not yet on the live stack**. Since phase 002: `make engine-keys`, lake-init from the release's own script, the secrets list, MinIO without host ports; **not yet run on the live stack** | none, manual check |

### Archived with the v3 lake ingest pipeline

The runbooks of the v3 lake tier and its hourly dbt marts retired with them on 2026-11-26 and are
kept unchanged in
[`legacy/v3-lake-ingest/docs/runbooks/`](../../legacy/v3-lake-ingest/docs/runbooks/); their
alerts were removed and their commands read tables, DAGs and scripts that no longer exist, so do
not follow them against this stack. A short page at each old path says what replaces it:

| Runbook (archived) | What it covered | What replaces it |
|---------|-------------|------------------|
| [lake-ingest-lag.md](./lake-ingest-lag.md) | The lake ingest behind Redpanda | the connector-failure runbook of phase 002, the Kafka runbooks of phase 004 |
| [lake-ingest-batch-audit.md](./lake-ingest-batch-audit.md) | An ingest batch that did not match the offsets it read | the Kafka connector's exactly-once resume, phase 004 |
| [lake-audit-failed.md](./lake-audit-failed.md) | The nightly lake audit failed | the failed-audit runbook of phase 002 |
| [lake-recovery.md](./lake-recovery.md) | Lakekeeper or MinIO down, an ingest killed mid-run, a replay, ClickHouse rebuilt from the lake | the backup, restore and rebuild-from-empty runbook of phase 004 |
| [lake-disk-usage-high.md](./lake-disk-usage-high.md) | The two lake disk alerts | the lake metrics and alerts of phase 004; meanwhile `DockerDiskUsageHigh` and `DockerDiskUsageCritical` ([docker-desktop-disk.md](./docker-desktop-disk.md) section 2) and `df -h /` |
| [dbt-marts.md](./dbt-marts.md) | The hourly dbt marts not rebuilt | the dbt runner and its run records, phase 002 |
| [clickhouse-rebuild-from-lake.md](./clickhouse-rebuild-from-lake.md) | ClickHouse `gold` reloaded from the lake's gold | nothing: the lake's gold was dropped, and a resume reads Redpanda only, within 7 days ([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)) |
| [grant-and-revoke-access.md](./grant-and-revoke-access.md) | Giving a principal a role on a product and taking it away: the documents to change (`principals.yaml`, the product's `grants.yaml`, an expectations row), `make password` and `make principal-remove`, release and deploy, verifying the grant and the denial, what a wrong grant's check codes mean, and what cannot be done (no edit on the stack). Documents and checks run 2026-11-27 in a scratch clone; **no grant deployed live yet** | none, manual |

The phases are those of the
[unified platform plan](../plans/2026-11-26-unified-product-platform/README.md).

## Triage

```bash
# What is actually firing?
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alertname: .labels.alertname, state, since: .activeAt}'

# Is anything down?
make ps

# Is data still moving? Records each venue's capture had acknowledged by Redpanda in the last
# 5 minutes; a venue at 0 while its feed is live is CaptureProduceStalled's case.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange) (increase(k2_capture_records_delivered_total[5m]))' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

Then:

- **A capture** (down, stale, gaps, produce errors) → the `capture-*` runbooks above, by the
  alert that fired
- **Broker-level** (topics, partitions, schema registry) → [redpanda.md](./redpanda.md)
- **A file product's DAG** (`AirflowTaskFailed` on `fetch`, `load` or `audit`) →
  [connector-failure.md](./connector-failure.md) for `fetch`,
  [file-source-audit-failed.md](./file-source-audit-failed.md) for `load` and `audit`; any other task
  → [airflow-scheduling.md](./airflow-scheduling.md)
- **Hot tier affected** (no trades arriving, ClickHouse down), only while the `hot-gold`
  profile runs: ClickHouse is paused (ADR-057), and a stopped one is the paused state, not an
  incident → [failure-recovery.md](./failure-recovery.md)
- **Catalog or object store** (`lakekeeper` or `minio` unhealthy in `make health` step (1)) →
  [failure-recovery.md](./failure-recovery.md) §5 for MinIO; for either, `docker logs --tail 100
  k2-<service>` first

Until phase 004 the lake receives no crypto data and the hot tier is paused, so **Redpanda holds
the only copy** of what the captures record: 48 h or 2 GiB per partition on the `raw.*` topics, 7 days on
`trades.*` and `book.*` ([`docker/redpanda/init.sh`](../../docker/redpanda/init.sh)). A capture or
broker failure is therefore the one that loses data, and it goes first. When phase 004's first
product runs it starts at the start of the log; nothing older is recovered.

## Escalation

| Condition | Action |
|-----------|--------|
| Recovery not achieved within the runbook's MTTR + 30 min | Escalate to the platform owner |
| Root cause is a code bug | Open an issue, link the runbook section that failed |
| More than 3 occurrences in 24 h | Stop restarting; find the cause |
| Data loss suspected | Escalate immediately. With no crypto data in the lake and the hot tier paused there is no second store to reconcile against: bound the window from the capture metrics ([capture-sequence-gaps.md](./capture-sequence-gaps.md)) and record it, as the capture runbooks' "Record the gap" says, before taking any destructive action |

## Archived v2 Kotlin runbooks

The feed-handler crash procedure and the three `FeedHandler*` alert rules moved to
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/) when the Kotlin capture
tier retired ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). The measured MTTR is kept
as measured; the alerts and the `feed_handler_*` metric family no longer exist, so do not
follow it against this stack.

## v1 runbooks

The 14 archived v1 runbooks, Kafka, Spark Streaming, the v1 OHLCV pipeline, blue-green
deploys, checkpoint corruption, are kept for reference in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/). They describe an
architecture that no longer exists; do not follow them against the v2 stack.

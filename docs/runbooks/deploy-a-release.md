# Runbook: Deploy a release

Use this to put an approved tag live: compile it into a sealed release, see what deploying it will do,
deploy it, and check that every part runs it. It also covers a deploy that was refused (exit 2), one that
failed and rolled itself back (exit 1) and one that was interrupted. Going back on purpose, and a rollback
that failed, are [roll-back-a-release.md](./roll-back-a-release.md). Why it works this way:
[ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** The defaults:
`K2_RELEASES=$HOME/k2-releases` (created by the first `make release`, never removed or recreated),
`K2_SECRETS=$HOME/k2-secrets`, compose project `K2_COMPOSE_PROJECT` (unset: the checkout directory's
name, the project `make up` gives it; set but empty is refused; the live stack's `.env` sets it to the
project its containers carry, `k2-market-data-platform`, see
[first-cutover-to-releases.md](./first-cutover-to-releases.md)), Trino at
`https://localhost:${K2_TRINO_PORT:-8443}` by name, with `${K2_SECRETS}/tls/ca.pem`. View statements come
from each release's own k2gen, in its tools image, so the checkout's version does not matter to them;
the scripts deploy calls (S20's checks, S13's `identity.sh`) are the checkout's. Memory: a deploy of a
new release starts a throwaway lake (about 4 GiB) beside the stack for its release-time suites.

**The `.env` must be loaded into the shell that runs `make deploy`, not only present.** Since phase 002
each engine has its own MinIO key pair (`K2_SPARK_S3_*`, `K2_TRINO_S3_*`, `K2_MAINTENANCE_S3_*`,
`K2_BACKUP_S3_*`, `K2_TOOLS_S3_*`, each an `_ACCESS_KEY` and a `_SECRET_KEY`, written by `make engine-keys`,
[first-cutover-to-releases.md](./first-cutover-to-releases.md)). The live access suite's storage-key rows
sign with them, and deploy passes them to it by name from its own environment, so preflight refuses a
deploy whose environment lacks any of the ten (section 5) rather than fail 21 rows after the swap.

> **Where these commands ran.** The live stack's first deploy ran on 2026-11-27 at 00:28 to 00:31 UTC,
> release `v3.0.0-alpha.3-rc.1-490b0533ff56`, recorded in
> [first-cutover-to-releases.md](./first-cutover-to-releases.md); the tag `v3.0.0-alpha.3.2` then named the
> release it ran. Later the same day it ran three deploys of this phase's releases, their output quoted in
> sections 3, 5 and 8 (host paths as `~`):
>
> - 08:33 UTC, `make deploy RELEASE=v3.0.0-alpha.4-rc.1-7d00791fc1fe ALLOW_STATEFUL=minio`: failed at
>   `views-narrow` and rolled itself back, exit 3
>   ([roll-back-a-release.md](./roll-back-a-release.md) section 3 quotes it);
> - 09:58 to 10:01 UTC, `make deploy RELEASE=v3.0.0-alpha.4-rc.2-e2dcf759de67 ALLOW_STATEFUL=minio`: exit 0
>   in 166 s, MinIO recreated without its host ports, lake-init from the release, `k2-egress` created,
>   k2-api in verify, the site step;
> - 10:50 to 10:51 UTC, `make deploy RELEASE=v3.0.0-alpha.4-rc.3-48ad71fba732`: exit 0 in 56 s, the first
>   drain that met real DAGs.
>
> After rc.3, `make release-verify` (section 4) exited 0 with the live access suite run, and `make site`
> (section 8) wrote both sites. The refusal for missing secret files (section 5) ran live before the
> password files existed. Run only on the rehearsal (`make deploy-rehearsal KEEP=1`,
> [`scripts/verify/deploy_rehearsal.py`](../../scripts/verify/deploy_rehearsal.py): compose project
> `k2-rehearsal` with its own networks and volumes under a scratch directory in `$HOME`, Trino 484 started
> from the release's compiled etc, a throwaway Lakekeeper, Postgres and MinIO, Spark, Prometheus, the tools
> one-shot and a stand-in, releases cut from the k2gen good fixture set at scratch tags), and quoted from it
> with its release ids: compiling a release (section 1), the plan and the diff (section 2), a rules-only
> deploy, a deploy that rolls itself back with exit 1 (section 6) and an interrupted one (section 7). Not
> yet run live: `remove-<service>`, the refusals for the env file's mode, `password.db`, the storage-key
> names and a drain still running, `ALLOW_RUNNING=1`, `make site-install`.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Compile a release from a tag | <2 min | 2.7 to 4.7 s a scratch tag, its two local builds cached (2026-11-27) |
| 2 | See what a deploy will do | <1 min | 0.8 s for `PLAN=1`, 0.1 s for the diff (2026-11-27) |
| 3 | Deploy, rules only | <1 min | 9.1 to 13.4 s; no container restarted; the new rules loaded 0.19 to 4.25 s after the swap (2026-11-27, rehearsal) |
| 3 | Deploy, one service's image | <2 min | 10.8 s, the stand-in alone recreated (2026-11-27, rehearsal) |
| 3 | Deploy, live, recreating MinIO and seven services | <5 min | 166 s, recreate 80.3 s of it (2026-11-27, live, rc.2) |
| 3 | Deploy, live, DAG code moved, nothing recreated | <2 min | 56 s, the drain 12.8 s of it (2026-11-27, live, rc.3) |
| 4 | Check what runs | <1 min | 1.4 to 1.7 s (2026-11-27, rehearsal); live, with the access suite and identity.sh: exit 0, not timed (2026-11-27) |
| 6 | A deploy that failed verification | <2 min | 16.1 to 19.0 s from the command to the previous release verified (2026-11-27) |
| 7 | A deploy that was interrupted | <2 min | 9.4 s for the rerun; killed after each of the 13 steps in turn, every rerun converged (2026-11-27) |
| 8 | The sites failed or are stale | <2 min | the `site` step 16.0 s and 18.6 s (2026-11-27, live, rc.2 and rc.3); a failed site not yet seen |

---

## 1. Compile a release from a tag

**Symptom**, a tag has been approved (an annotated `v*` tag on `main`) and is to go live.

**Detection**, manual: nothing runs a tag until it is deployed.

**Expected behaviour**, `make release` exports the tag's tree, builds the tools image and every local image
from it (reproducibly: one tree, one image id), pins every registry image by digest, then compiles the
tag's document set with the tag's own k2gen inside the tools image, against the release live now. The
release is sealed read-only and its id printed. Compiling a tag twice prints the same id and writes nothing.

**Recovery**

```bash
make release TAG=v0.0.1-rehearsal.2
```

```text
release: building k2-spark-iceberg for spark-iceberg from the tag
release: building tools for tools from the tag
  k2gen release: NO ONESHOT dbt-runner: --images names no image for it
  k2gen release: NO ONESHOT fetch: --images names no image for it
release: v0.0.1-rehearsal.6-4d9b7b32a3a4 (needs against v0.0.1-rehearsal.5-2a37b3160f57)
deploy: exit 0: done
v0.0.1-rehearsal.6-4d9b7b32a3a4
```

Run again, the same tag prints the same id and adds `k2gen release: v0.0.1-rehearsal.6-4d9b7b32a3a4 exists,
identical; nothing written`. The two `NO ONESHOT` lines were the compose stubs of a tag before phase 002;
a tag of this phase builds `fetch`, `dbt-runner` and `k2-api` from the tag too, with `tools`, `k2-airflow`,
`k2-capture` and `k2-spark-iceberg` (one `release: building <image> for <service> from the tag` line each,
`scripts/deploy.py`), so it prints neither.
`IMAGES=<file>` skips the builds and pins what the file names (`manifest.schema.json#/$defs/images`).

**Measured**, 2.7 to 4.7 s, 2026-11-27, on the rehearsal: the tools image and a one-line Spark stand-in built from the tag, both cache hits. A first build of the real local images (`k2-airflow`, `k2-capture`, `k2-spark-iceberg`) from a tag is not measured; it is minutes when the cache is cold.

---

## 2. See what a deploy will do

**Symptom**, before a deploy: which services will restart, which tables are created, which views move.

**Detection**, manual.

**Expected behaviour**, `make release-diff` prints k2gen's file, needs, image and storage-plan difference
from the release live now; `PLAN=1` runs preflight, the image check and the plan, then stops before any
change.

**Recovery**

```bash
make release-diff RELEASE=<release id>
make deploy RELEASE=<release id> PLAN=1
```

```text
## Release `v0.0.1-rehearsal.5-2a37b3160f57` to `v0.0.1-rehearsal.6-4d9b7b32a3a4`

### What deploying it needs

| Need | |
|---|---|
| Recreate | none |
| Trino restart | no |
| DAG drain (a DAG's tasks changed) | no |
| DDL | none |

Nothing beyond the pointer swap: every changed file is read through the pointer.
...
deploy: preflight ok (0.2 s)
deploy: images ok (0.5 s)
deploy: plan v0.0.1-rehearsal.5-2a37b3160f57 -> v0.0.1-rehearsal.6-4d9b7b32a3a4
  recreate, one at a time: nothing
  one-shots run: none
  Trino restart: no; DDL: none
  views to re-sync: none
deploy: plan ok (0.0 s)
deploy: PLAN=1: stopped before any change
deploy: exit 0: done
```

When the release declares a view that the release live now does not, the plan adds one line after
`views to re-sync`: `views new in this release: <relations> (created after its rules load, at its first
tested publish)`. Those views are not touched before the swap.

**Measured**, 0.8 s for `PLAN=1` and 0.1 s for `make release-diff`, 2026-11-27, on the rehearsal.

---

## 3. Deploy

**Symptom**, a release is compiled and its plan read.

**Detection**, manual.

**Expected behaviour**, the steps run in order, each printed with its time and recorded in
`$K2_RELEASES/deploy-log.jsonl` (a failed step with its `reason`): preflight, images, plan, backup, drain,
ddl, views-narrow, canary, swap, `remove-<service>` (one per service removed), recreate, rules-loaded,
ddl-late, views-widen, verify, done, site. Preflight checks the env file's mode
(`scripts/check-env.py --mode-only`), every `password.db` line (`make principals-status`), every secret
file the release's services and builds need, the ten storage-key names in its environment, then runs the
release-time suites on a throwaway lake from the release: every row of `tests/access/expectations.yaml`
(112 in the file at this commit) and the tested-data marker; a sealed release's pass is kept under
`$K2_RELEASES/verify/<id>.*.json` and not run again.
**Drain**: when Trino restarts, when the DAG code moves, or when a DAG's task list changes, deploy pauses
the affected DAGs that are not paused already (their names written first to
`$K2_RELEASES/deploy-paused.json`) and waits up to 300 s for their running tasks; it unpauses exactly
those once a release runs again (done, rolled back, or a failed `ddl`), never one an operator paused.
Tasks still running at 300 s refuse the deploy (section 5) unless `ALLOW_RUNNING=1`, which goes on under
them and records step `allow-running`; a run keeps the release it started on either way, since every
task reads the release its `resolve` task pinned. With no scheduler running, or no DAG in it, the step
records why and the deploy goes on.
**Before a release that adds a product**, its builder's and publisher's password files must exist
(`make password PRINCIPAL=dbt_<fence> TO_FILE=1` and `make password PRINCIPAL=pub_<fence> TO_FILE=1`,
[create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md)); preflight refuses the deploy,
naming each one that is missing and its command, before anything changes (section 5).
**ddl** runs the one-shots first, each recreated fresh: `lake-init` runs the target release's own
`platform/docker/lake/init-lake.sh` (`K2_LAKE_INIT_RELEASE`, `K2_STORAGE_PLAN`), so a new engine's MinIO
user and policy exist before anything uses its key; a target that carries no such script (a release
before phase 002) skips lake-init and says so in the plan.
**Recreate** first creates every network the release's compose declares and lacks, with compose's labels
(`network <name> created: no service compose starts uses it yet`): `k2-egress`, fetch's network, has no
long-running member. A service in a source release's default set that the target puts behind a profile
is stopped and removed (`remove-<service>`, `<service> stopped and removed: behind profile <p> in <id>`).
**Site**, after done: the release's catalog site and dbt docs written into the `k2-site` volume k2-api
serves (section 8); a failure there never fails or rolls back the deploy. Views lose dropped columns before the rules move (in the target's shape when the old rules admit
it, else the old one; a view the release being moved from does not declare is skipped, since the old rules
cannot read it and its publisher has not created it: it is left to widen once the target's rules are loaded) and take the target's shape and new columns once its rules are loaded. Only the services in the plan are recreated, one at a
time, each waited healthy; a rules-only release recreates none. The last line is the exit code and what it
means. `make deploy RELEASE=<tag>` compiles the tag first when no release of that id exists.
Before a one-shot that migrates a database (`airflow-migrate`, `lakekeeper-migrate`), `backup` dumps that
database to `${K2_BACKUP_DIR:-~/k2-backup}/deploy/<id>/`, outside the releases root every service mounts, (`scripts/backup-lake.sh --dump`) and records its path and sha256;
it prints `SKIP dump` when every release the deploy moves from pins the same image. A rollback never runs a
migration backwards: restoring the dump is section 5 of [roll-back-a-release.md](./roll-back-a-release.md).
Verify also fails when no container of the project, or not every default-profile long-running service of
the release, is running, and when the catalog fence's probes fail (`scripts/verify/catalog-fence.sh`).
The fence's structure line passes only when every member of `k2-catalog` is a service of
`tests/access/catalog-fence-allow.txt` or one of the platform's own one-shots that Airflow starts with
`docker run` (a dbt build mid-run, for instance): no compose labels, labels `com.k2.project=<project>` and
`com.k2.oneshot=<kind>` with a kind on that list (set by `docker/airflow/dags/oneshot.py`), the name
`k2-<kind>-<dagId>-<try>`, and the image the live release's `compiled/oneshots.json` gives the kind. When
no live release can be read the image goes unchecked, and the PASS line says so (`<n> one-shots admitted,
images unchecked: no live release readable`, else `images as the live release gives them`); any other
member fails the line by name.
It asks k2-api which release it serves, `GET http://127.0.0.1:8095/v1/release` (the port the release's
compose publishes, `K2_API_PORT`, default 8095), up to five tries 2 s apart, and fails unless the answer is
the target's id; and it runs the live access suite in a tools one-shot (section 4).

**The one deploy that recreates MinIO.** A release of this phase changes MinIO's compose service: its two
host ports (`127.0.0.1:9000` and `127.0.0.1:9001`, the console) are gone, so nothing outside a container
reaches the object store. MinIO is a service deploy never recreates (`redpanda`, `minio`, `prefect-db`,
`lakekeeper` and the captures), so that deploy is refused (section 5) until it is run as
`make deploy RELEASE=<release id> ALLOW_STATEFUL=minio`, which recreates MinIO once, first in the order,
on its kept `minio-data` volume. Every reader and writer of the lake loses the object store while it
restarts. Later deploys leave MinIO's definition alone and need no flag.

**Recovery**

```bash
make deploy RELEASE=<release id>                          # an ordinary deploy
make deploy RELEASE=<release id> ALLOW_STATEFUL=minio     # once: the first release of phase 002 or later
```

The live stack's first deploy of this phase's releases to go through, 2026-11-27, 09:58 to 10:01 UTC
(trimmed: `...` stands for lines left out, never for part of a line):

```text
  access-verify and the tested-data marker passed earlier for v3.0.0-alpha.4-rc.2-e2dcf759de67 (v3.0.0-alpha.4-rc.2-e2dcf759de67.access-release.json, v3.0.0-alpha.4-rc.2-e2dcf759de67.marker.json)
deploy: preflight ok (3.1 s)
deploy: images ok (1.5 s)
deploy: plan v3.0.0-alpha.3.2-bae18019daef -> v3.0.0-alpha.4-rc.2-e2dcf759de67 (also undoing attempts v3.0.0-alpha.4-rc.1-7d00791fc1fe)
  recreate, one at a time: minio, trino, airflow-api-server, airflow-dag-processor, airflow-scheduler, k2-api, prometheus, spark-iceberg
  one-shots run: airflow-migrate, lake-init
  Trino restart: no; DDL: k2.currency_master.audit_checks, k2.currency_master.bronze_currencies, k2.currency_master.landing_files, k2.ecb_fx_rates.audit_checks, k2.ecb_fx_rates.bronze_daily, k2.ecb_fx_rates.landing_files
  views to re-sync: fx_reference.ecb_fx_rates.gold_eur_rates_daily, fx_reference.ecb_fx_rates.silver_rates, governance.quality_results.test_results, reference_data.calendars.target_days, reference_data.currency_master.currency
  not recreated: dbt-runner: profile build, not running; its next start uses the release
  not recreated: fetch: profile fetch, not running; its next start uses the release
  not recreated: tools: a one-shot this deploy runs itself, from the release
  drain: Trino restarts
deploy: plan ok (0.0 s)
  database airflow dumped before airflow-migrate: ~/k2-backup/deploy/v3.0.0-alpha.4-rc.2-e2dcf759de67/airflow-2026-11-27T095847.120305Z.sql.gz sha256 7e85688c74ce9edf41b849acfd36aafdabbc0556df6ffb0b5a92c38b46f23085
deploy: backup ok (0.2 s)
  no DAGs: Airflow has none to drain (Trino restarts)
deploy: drain ok (1.5 s)
  one-shot airflow-migrate exited 0 in 20.2 s
  one-shot lake-init exited 0 in 7.3 s
  every namespace exists
  k2.currency_master.audit_checks: created after the recreate, by spark
  ...
deploy: ddl ok (36.0 s)
  fx_reference.ecb_fx_rates.gold_eur_rates_daily: the rules loaded now refuse its publisher pub_ecb_fx_rates (PERMISSION_DENIED: Access Denied: Cannot execute query), so they are a release without it and name no reader of it: nothing to narrow; views-widen syncs it
  ...
deploy: views-narrow ok (0.2 s)
  k2_release_5ccfa4e8747b: line written in place
deploy: canary ok (0.0 s)
  as the VM sees them: {"airflow": "../v3.0.0-alpha.4-rc.2-e2dcf759de67", "k2-api": "../v3.0.0-alpha.4-rc.2-e2dcf759de67", "prometheus": "../v3.0.0-alpha.4-rc.2-e2dcf759de67", "spark": "../v3.0.0-alpha.4-rc.2-e2dcf759de67", "trino": "../v3.0.0-alpha.4-rc.2-e2dcf759de67"}
  prometheus: SIGHUP, its rules reread through the pointer
deploy: swap ok (1.1 s)
  network k2-market-data-platform_k2-egress created: no service compose starts uses it yet
  minio recreated, healthy in 6.3 s
  trino recreated, healthy in 10.4 s
  ...
  k2-api recreated, healthy in 6.2 s
  prometheus recreated, healthy in 6.1 s
  spark-iceberg recreated, healthy in 0.1 s
deploy: recreate ok (80.3 s)
  k2_release_5ccfa4e8747b admitted and k2_release_2a341686bfd4 refused 81.17 s after the swap
deploy: rules-loaded ok (5.7 s)
  created k2.currency_master.audit_checks (spark)
  ...
deploy: ddl-late ok (9.8 s)
  fx_reference.ecb_fx_rates.gold_eur_rates_daily: not published yet; the wrapper creates it at its first tag
  ...
deploy: views-widen ok (1.4 s)
  spark-iceberg: pointer spark is ../v3.0.0-alpha.4-rc.2-e2dcf759de67
  ...
  canary k2_release_5ccfa4e8747b admitted, k2_release_2a341686bfd4 refused
  catalog drift check clean (0 differences, pending ['PENDING view fx_reference.ecb_fx_rates.gold_eur_rates_daily: its product has not published yet', ...], 0 skipped, 0 left by a release since)
  SKIP access-verify LIVE=1: not yet published, so no live row can read it: PENDING view fx_reference.ecb_fx_rates.gold_eur_rates_daily: its product has not published yet, ...; every row ran on a throwaway lake at release time (preflight); phase 002 publishes them
  k2-api: GET http://127.0.0.1:8095/v1/release names v3.0.0-alpha.4-rc.2-e2dcf759de67
  catalog-fence: 10 PASS, 0 FAIL
  images and health checked for 23 container(s)
deploy: verify ok (8.6 s)
  removed canary k2_release_2a341686bfd4
deploy: done ok (0.0 s)
  catalog site: k2site: wrote /site/catalog/current; facts read
  dbt docs: k2-dbt: wrote /site/dbt-docs/tree-fcg23xn0
deploy: site ok (16.0 s)
deploy: v3.0.0-alpha.4-rc.2-e2dcf759de67 is live and verified
deploy: exit 0: done
```

`(also undoing attempts ...)` in the plan is the rc.1 attempt of 08:33 UTC, which failed and whose rollback
exited 3: the plan of this deploy covers what that attempt moved. The `views-narrow` lines are the fix of
that failure: a view the release being moved from does not declare is left for `views-widen`. Every
product was still unpublished, so the live suite was a visible SKIP and the drift check listed them as
`PENDING`; `rules-loaded` holds one more refresh period (5 s) after the canary is admitted, for the groups'
own timer.

A deploy whose DAG code moved, with the five product DAGs present, 2026-11-27, 10:50 to 10:51 UTC: the
drain pauses the DAGs that were not paused, waits for their running tasks, and unpauses exactly those at
the end (the other two had been paused by the operator and stayed paused):

```text
deploy: plan v3.0.0-alpha.4-rc.2-e2dcf759de67 -> v3.0.0-alpha.4-rc.3-48ad71fba732
  recreate, one at a time: nothing
  one-shots run: none
  Trino restart: no; DDL: none
  views to re-sync: none
  not recreated: dbt-runner: profile build, not running; its next start uses the release
  not recreated: fetch: profile fetch, not running; its next start uses the release
  drain: the DAG code moved since v3.0.0-alpha.4-rc.2-e2dcf759de67
deploy: plan ok (0.0 s)
  no one-shot migrates a database
deploy: backup ok (0.0 s)
  drained access_canary, calendars, currency_master, ecb_fx_rates, quality_results (the DAG code moved since v3.0.0-alpha.4-rc.2-e2dcf759de67) in 11 s; paused access_canary, calendars, ecb_fx_rates
deploy: drain ok (12.8 s)
...
deploy: swap ok (1.1 s)
  nothing to recreate
deploy: recreate ok (0.1 s)
  k2_release_498401369b41 admitted and k2_release_5ccfa4e8747b refused 0.41 s after the swap
deploy: rules-loaded ok (5.1 s)
...
  catalog drift check clean (0 differences, pending ['PENDING view governance.quality_results.test_results: its product has not published yet', ...], 4 skipped, 0 left by a release since)
  ...
  catalog-fence: 10 PASS, 0 FAIL
  images and health checked for 23 container(s)
deploy: verify ok (8.6 s)
  removed canary k2_release_5ccfa4e8747b
deploy: done ok (0.0 s)
  catalog site: k2site: wrote /site/catalog/current; facts read
  dbt docs: k2-dbt: wrote /site/dbt-docs/tree-6q37it9h
deploy: site ok (18.6 s)
deploy: v3.0.0-alpha.4-rc.3-48ad71fba732 is live and verified
  unpaused access_canary, calendars, ecb_fx_rates: the DAGs deploy paused
deploy: exit 0: done
```

Lines not yet seen live, quoted from `scripts/deploy.py` with its placeholders:

```text
  <service> stopped and removed: behind profile <p> in <release id>
deploy: remove-<service> ok (...)
  LEFT <kind> <name>: declared by <release id>, a release deployed or attempted since the one that is live; ...
```

Once every product the drift check names has published, verify runs the live suite instead of the SKIP,
as `make release-verify` did after rc.3 (section 4).

**Measured**, live, 2026-11-27: 166 s from the command to `exit 0` for rc.2 (eight services recreated
one at a time, 80.3 s; DDL 36.0 s; verify 8.6 s; site 16.0 s), and 56 s for rc.3 (nothing recreated; the
drain 12.8 s; site 18.6 s). On the rehearsal, the same day, from the first to the last record of `deploy-log.jsonl`: a first deploy 33.2 s (Trino started from the compiled etc in 10.9 s, three Spark tables created after it); a rules-only release 9.1 to 13.4 s with no container restarted (every `State.StartedAt` unchanged), its rules loaded 0.19 to 4.25 s after the swap and a 60-minute delay seen by a reader polling every 50 ms 3.84 s after the swap; a release changing one service's image 10.8 s, that service alone recreated.

---

## 4. Check what runs

**Symptom**, any time: after a deploy, after a host restart, when something looks off.

**Detection**, manual. `make release-verify` exits non-zero on any mismatch and names it.

**Expected behaviour**, `release-verify` asks every part: each pointer as the VM and each container that
mounts the root see it, the files under it against the manifest, the release's Trino canary admitted and
the previous one refused, every container's image id against the manifest's, every health check, the
catalog drift check from inside the tools one-shot (`scripts/verify/catalog_drift.py --release`), the access
suite on the live stack (`make access-verify LIVE=1`, run in a tools one-shot so its storage-key rows reach
MinIO, which has no host port: [verify-access-after-a-release.md](./verify-access-after-a-release.md)
section 2; a visible `SKIP` while the drift check reports a product not yet published), k2-api's
`GET /v1/release`, which must name the release, and S13's `scripts/verify/identity.sh` (only in `make release-verify`, not in a
deploy's own verify step, since it writes and removes a probe line). Results land in
`$K2_RELEASES/verify/<id>.{drift,access-live}.json`. `release-status` is
read-only: the good release, the pointers as the host sees them, the canary lines by name, the kept
releases and the last records.

**Recovery**

```bash
make release-verify
make release-status
```

`make release-verify` on the live stack after the rc.3 deploy, 2026-11-27 (exit 0; the lines that name
each check, `...` for those left out):

```text
  ...
  k2-api: pointer k2-api is ../v3.0.0-alpha.4-rc.3-48ad71fba732
  ...
  catalog drift check clean (0 differences, pending none, 6 skipped, 0 left by a release since)
  access-verify LIVE=1 passed: {'PASS': 82, 'FAIL': 0, 'SKIP': 30}
  k2-api: GET http://127.0.0.1:8095/v1/release names v3.0.0-alpha.4-rc.3-48ad71fba732
  catalog-fence: 10 PASS, 0 FAIL
  identity.sh: 18 PASS, exit 0
  ...
```

Every product had published by then, so no `PENDING` remained and the live suite ran rather than SKIP
([verify-access-after-a-release.md](./verify-access-after-a-release.md) section 2). `make release-status`
was not run live that day; its output below is the rehearsal's:

```text
releases root <home>/s19-rehearsal-3452690/releases; good release v0.0.1-rehearsal.6-4d9b7b32a3a4
  pointer trino -> ../v0.0.1-rehearsal.6-4d9b7b32a3a4 (as the host sees it)
  ...
  canary lines in trino/password.db: k2_release_7160b886ca37
  releases kept: v0.0.1-rehearsal.1-9b8473db7182, ..., v0.0.1-rehearsal.6-4d9b7b32a3a4
  2026-11-26T20:57:37.122137Z v0.0.1-rehearsal.5-2a37b3160f57 done ok (from v0.0.1-rehearsal.3-8d67e7c865fe, by <operator>)
```

`release-status` also prints `DAGs a deploy paused and has not yet unpaused: <dags or none>`.

**Measured**, 1.4 to 1.7 s for `release-verify`, 0.1 s for `release-status`, 2026-11-27, on the rehearsal,
before the live access suite and k2-api joined verify. Live, with them and identity.sh, 2026-11-27: exit 0,
not timed.

---

## 5. A deploy was refused (exit 2)

**Symptom**, `deploy: REFUSED: ...` and `deploy: exit 2: refused before any change`. `make` itself
reports any failed recipe as `Error 2`; the script's own code is its last line.

**Detection**, the command's output; the deploy log's last record is the refused step, `failed`.

**Expected behaviour**, nothing has moved: no pointer, no service, no view, no canary line. Read the
message, fix the cause, run the same command again.

A plan-only deploy on the live stack, 2026-11-27, before the password files and the API key file of the
release's products existed (trimmed at `...`):

```text
deploy: preflight FAILED after 1.7 s: secret files missing under ~/k2-secrets: api/keys.json (make api-key); principals/dbt_calendars.password (make password PRINCIPAL=dbt_calendars TO_FILE=1); ...
deploy: REFUSED: ...
deploy: exit 2: refused before any change
```

The commands it names, run once each (`make password PRINCIPAL=<id> TO_FILE=1` for every `dbt_<fence>`
and `pub_<fence>` it listed, `make api-key PRINCIPAL=p.analyst-a TO_FILE=1`), cleared it.

| Message names | Cause | Fix |
|---|---|---|
| `another deploy holds .../.deploy.lock (pid N)` | a deploy or `make release` is running | wait for it |
| `not sealed` or `files differ from its manifest` | the release directory was changed after it was compiled | compile the tag again under a new tag; never edit a release |
| `secret files missing under <dir>: <file> (<command>); ...; nothing changed` | a file the release's services mount, a build principal's password (each built product's `dbt_<fence>` and `pub_<fence>`, and `svc.runs`), or one the deploy's own SQL logs in with is absent | run the command printed beside each file: `make password PRINCIPAL=<id> TO_FILE=1` for `principals/<id>.password`, `make tls` for `tls/*`, `make api-key` for `api/keys.json`, `make alerts-install-webhook` for `alertmanager/*` ([create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md)) |
| `the live access suite's storage-key rows sign with K2_..._S3_..., not in deploy's environment: load the env file first (set -a; . ./.env; set +a), where make engine-keys writes them; nothing changed` | the shell running `make deploy` did not load `.env`, or `.env` lacks an engine's pair | `set -a && . ./.env && set +a`; a pair `.env` lacks: `make engine-keys` ([first-cutover-to-releases.md](./first-cutover-to-releases.md)) |
| `scripts/check-env.py --mode-only: check-env: FAIL <file> is mode 644, open to its group or others, ...` | `.env` is readable by others | `chmod 600 .env` |
| `make principals-status exit 1, a malformed or stale password.db line: ...` | `password.db` holds a line for a principal no release declares, or a malformed one | `make principals-status` names it with its fix, `STALE <id> in ...: ...; make principal-remove PRINCIPAL=<id>` |
| `drain: still running after 300 s: <tasks>; ... Nothing changed: deploy again once they finish, or with ALLOW_RUNNING=1 to go on under them` | a drained DAG's tasks ran past the bound; deploy unpaused what it paused | wait for the run, then deploy again; or `ALLOW_RUNNING=1` (a running run stays on the release it pinned) |
| `<n> GiB free at <root>, under the 2.0 GiB floor`, `... of memory available, under the 2.0 GiB floor` | the releases root's disk, or the host's memory, is short | free space or memory, then deploy again |
| `the release changes minio, which deploy never recreates (A1); recreate it by hand after review, or deploy with ALLOW_STATEFUL=minio` | a capture, Redpanda, MinIO, Postgres or Lakekeeper changed; for MinIO, the first release of phase 002 or later (its host ports removed) | review the change; `ALLOW_STATEFUL=<service>` recreates it with the deploy (section 3) |
| `exists and differs from the release's DDL` | a table's columns or partitioning in the catalog differ from the release's | never altered by deploy: `make drop TARGET=...` after a backup ([drop-and-rebuild.md](./drop-and-rebuild.md)), then deploy |
| `the release's backup set drops ..., which storage still holds` | a restore table or landing prefix leaves the backup set while its data is there | `make drop TARGET=...` first |
| `the release fails its access suite at release time` | a row of `tests/access/expectations.yaml` fails on the throwaway lake | read `$K2_RELEASES/verify/<id>.access-release.json` ([verify-access-after-a-release.md](./verify-access-after-a-release.md)); fix the documents or the row, tag again |
| `the release fails the tested-data marker check` | a view, its tag or its shape fails on the throwaway lake | read `$K2_RELEASES/verify/<id>.marker.json` |
| `local build ... is not on this host` | a local image the release names was removed | compile the tag again (`make release`) |
| `the rollback target <id> names images this host no longer has` | an image the good release pins, which a rollback would run, was removed | a registry image: `docker pull <ref>`; a local build cannot be rebuilt to its id: `make release TAG=<the good release's tag>`, deploy that, then this one. Never run `docker image prune -a` (or `docker system prune -a`, or Docker Desktop's clean-up) on this host: a kept release's images go with it. `make release-status` prints the same check |
| `the dump of database <db> before <db>-migrate failed` | `scripts/backup-lake.sh --dump` could not dump the database a one-shot migrates | is prefect-db running and healthy? Then deploy again |
| `K2_COMPOSE_PROJECT is set but empty` | `.env` or the shell sets it to nothing | set it to the project the running containers carry, or unset it |

---

## 6. A deploy failed and rolled back (exit 1)

**Symptom**, `deploy: <id> failed after the views began to move: ...`, then `deploy: rolling back to
<previous>`, the `rollback-<step>` lines, `deploy: rolled back; <previous> is live and verified` and
`deploy: exit 1`.

**Detection**, the command's output; in the log, a `failed` record, then `rollback-*` records ending in
`rollback-done ok`.

**Expected behaviour**, every part is back on the previous release, images included, verified by the same
checks as a deploy. The failed release stays compiled; fix the cause and deploy it again.

**Recovery**

```bash
K2_DEPLOY_FAULT=verify-mismatch make deploy RELEASE=<release id>   # the rehearsal's forced mismatch
make release-verify
```

```text
deploy: views-widen ok (0.0 s)
deploy: verify FAILED after 0.0 s: verify: K2_DEPLOY_FAULT=verify-mismatch, a mismatch forced for the rehearsal
deploy: v0.0.1-rehearsal.6-4d9b7b32a3a4 failed after the views began to move: verify: K2_DEPLOY_FAULT=verify-mismatch, ...
deploy: rolling back to v0.0.1-rehearsal.5-2a37b3160f57
deploy: plan v0.0.1-rehearsal.6-4d9b7b32a3a4 -> v0.0.1-rehearsal.5-2a37b3160f57
deploy: rollback-views-narrow ok (0.0 s)
deploy: rollback-canary ok (0.0 s)
deploy: rollback-swap ok (0.9 s)
deploy: rollback-recreate ok (0.0 s)
deploy: rollback-rules-loaded ok (5.1 s)
deploy: rollback-ddl-late ok (0.0 s)
deploy: rollback-views-widen ok (0.0 s)
deploy: rollback-verify ok (1.4 s)
deploy: rollback-done ok (0.0 s)
deploy: rolled back; v0.0.1-rehearsal.5-2a37b3160f57 is live and verified
deploy: exit 1: failed; back on the previous release, or nothing to undo
make: *** [mk/release.mk:18: deploy] Error 1
```

**Measured**, 16.1 s for the command above (nothing to recreate either way), and 19.0 s for the rehearsal's release whose stand-in changed image: the stand-in was recreated on the new image, then again on the previous one, and its running image id was the previous release's afterwards (2026-11-27). Exit 1 is the script's; `make` adds its own `Error 1` and exits 2.
On the live stack no deploy has yet rolled back with exit 1: the one that failed there (rc.1, 2026-11-27,
08:33 UTC) rolled back but its rollback's verify failed, exit 3, quoted in
[roll-back-a-release.md](./roll-back-a-release.md) section 3.

---

## 7. A deploy was interrupted

**Symptom**, the deploy's terminal closed, the host slept, or the process was killed: the output stops
without a `deploy: exit` line. `make release-status` prints `UNFINISHED attempt since it: <id>`.

**Detection**, `make release-status`; `make release-verify` may fail (a pointer or a service moved, the
other not).

**Expected behaviour**, the state is mixed until a rerun. Every step is idempotent and the plan of a rerun
includes what the interrupted attempt may have moved, so either command converges: run the same deploy
again, or go back with `make rollback` (which restores the good release after an unfinished attempt).

**Recovery**

```bash
make release-status
make deploy RELEASE=<the same release id>
make release-verify
```

```text
deploy: canary ok (0.1 s)
deploy: K2_DEPLOY_FAULT=kill:swap: killed after the step's work, before its record
make: *** [mk/release.mk:18: deploy] Error 137
releases root <home>/s19-rehearsal-3452690/releases; good release v0.0.1-rehearsal.5-2a37b3160f57; UNFINISHED attempt since it: v0.0.1-rehearsal.6-4d9b7b32a3a4, v0.0.1-rehearsal.5-2a37b3160f57
  pointer trino -> ../v0.0.1-rehearsal.6-4d9b7b32a3a4 (as the host sees it)
  canary lines in trino/password.db: k2_release_af0a1b537aff, k2_release_7160b886ca37
deploy: an earlier attempt at v0.0.1-rehearsal.6-4d9b7b32a3a4 did not finish; converging from v0.0.1-rehearsal.5-2a37b3160f57
...
deploy: v0.0.1-rehearsal.6-4d9b7b32a3a4 is live and verified
deploy: exit 0: done
```

`K2_DEPLOY_FAULT=kill:<step>` is the rehearsal's way of interrupting a deploy after a step's work and before its
record, the worst moment: the work is done and nothing says so.

**Measured**, 9.4 s for the rerun, 2026-11-27. The rehearsal killed a deploy after each of its 13 steps in turn and reran it: every rerun exited 0 and `release-verify` passed, and a reader polling every 50 ms through all of it (3,401 polls) never saw a masked value.

---

## 8. The sites failed, or are stale

**Symptom**, a deploy or rollback that reached done prints ``deploy: site: failed <reason>. <id> stays live
and verified; the site is the one before: `make site` reruns it``; or the catalog pages at
`http://127.0.0.1:8095/catalog/` describe run-time facts (newest publish, tests, freshness) older than a day.

**Detection**, the command's output, and the deploy log's `site` record, `failed` with its `reason`. No
alert covers it.

**Expected behaviour**, the release stays live and verified: the site step runs after done, so its failure
never fails or rolls back the deploy. It writes two things into the `k2-site` volume k2-api serves read-only:
the catalog site (the release's own `platform/catalog-site/k2site.py` in a tools one-shot as `svc.catalog`,
to `/site/catalog`, served at `/catalog/`) and the dbt docs (`k2-dbt docs` in the release's dbt-runner
image, no network and no secret, to `/site/dbt-docs`, served at `/dbt-docs/`). A release before phase 003
has no `k2-site` volume and prints `SKIP sites: ...`. The catalog's run-time facts move with every run, so
`make site-install` regenerates both daily at 05:15 UTC, after the 04:30 backup, through a systemd user
timer that loads `.env` as an operator does; `make site` runs it now, with `--strict` (the catalog's facts
must read).

**Recovery**

```bash
make site                                   # both sites for the live release, now
make site-install                           # once, from the main checkout: the daily 05:15 UTC timer
systemctl --user list-timers k2-site.timer  # NEXT is the next 05:15 UTC
journalctl --user -u k2-site --since yesterday
```

`make site` on the live stack, 2026-11-27, after the rc.3 deploy:

```text
catalog site: k2site: wrote /site/catalog/current; facts read
dbt docs: k2-dbt: wrote /site/dbt-docs/tree-<id>
site: <release id>
```

`make site-install` ends `Scheduled daily at 05:15 UTC. Deploy regenerates the site too; 'make site' runs
it now.` (`mk/run.mk`); it has not been run yet. The unit runs the checkout it was installed from, so
install it from the main checkout.

**Measured**, the deploy's `site` step: 16.0 s (rc.2) and 18.6 s (rc.3), live, 2026-11-27. `make site` not
timed; a failed site not yet seen.

---

## Failure modes / incidents

- **2026-11-27**, rehearsal findings, each fixed before this was written: a release sealed inside the tools
  container was still writable on the host (Docker Desktop keeps a container's `chmod` to itself; deploy
  now seals on the host too); every release rebuilt its local images with new ids (each tag's `git archive`
  file times, and BuildKit's provenance); Python 3.13 refused a CA certificate without keyUsage; a first
  deploy cannot create Spark or Trino tables before it has started them (now `ddl-late`).
- **2026-11-27, live, 08:33 UTC**, the first deploy of a release that added products
  (`v3.0.0-alpha.4-rc.1-7d00791fc1fe`) failed at `views-narrow`: it read a new product's view as its
  publisher under the old rules, which name neither (`PERMISSION_DENIED`). Its rollback exited 3
  ([roll-back-a-release.md](./roll-back-a-release.md) section 3). `views-narrow` now leaves such a view to
  `views-widen` (`9d7f6dd`); the next release, rc.2, deployed with exit 0.
- **2026-11-27, live**, `release-verify` failed the fence's structure line on a dbt build one-shot
  Airflow had started (`k2-dbt-runner-ecb_fx_rates-1=/`, no compose labels); the fence now admits the
  platform's own labelled one-shots and nothing else (`806e3ed`, section 3).

---

**Last verified:** 2026-11-27. On the live stack: section 3 (rc.2 with `ALLOW_STATEFUL=minio`, rc.3 with a real drain), section 4's `make release-verify`, section 5's missing-secret refusal, section 8's `make site` and the `site` step. On the rehearsal only, branch `step/s19-deploy`: sections 1, 2, 6 and 7, and `make release-status`. Not yet run anywhere live: `remove-<service>`, `ALLOW_RUNNING=1`, the refusals for the env file's mode, `password.db`, the storage-key names and a drain still running, a deploy that rolls itself back with exit 1, an interrupted deploy, `make site-install`.

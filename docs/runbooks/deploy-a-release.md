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

> **Where these commands ran.** The live stack runs a release: its first deploy ran on 2026-11-27 at
> 00:28 to 00:31 UTC, release `v3.0.0-alpha.3-rc.1-490b0533ff56`, recorded in
> [first-cutover-to-releases.md](./first-cutover-to-releases.md). Nothing else has run live: a second,
> ordinary deploy, a rules-only deploy, a failed deploy rolled back and an interrupted one were run on the
> rehearsal (`make deploy-rehearsal KEEP=1`, [`scripts/verify/deploy_rehearsal.py`](../../scripts/verify/deploy_rehearsal.py)),
> not yet live: compose project `k2-rehearsal` with its own networks and volumes under a scratch directory
> in `$HOME`, Trino 484 started from the release's compiled etc, a throwaway Lakekeeper, Postgres and MinIO,
> Spark, Prometheus, the tools one-shot and a stand-in, releases cut from the k2gen good fixture set at
> scratch tags. The outputs below are the rehearsal's, its release ids and scratch paths included, and the
> `SKIP access-verify` and `SKIP catalog drift check` lines in them are the rehearsal's: on the live stack
> the access suite and the drift check run, and the live suite is a visible SKIP until phase 002 publishes
> the access canary. Live, `make release-status` and `make release-verify` answer for the release above.
> The tag `v3.0.0-alpha.3.2` names itself the release the live stack runs; the deploys that moved it
> there are not recorded here.
>
> **What phase 002 and 003 added has not run live.** The `drain`, `remove-<service>` and `site` steps, the
> refusals for the env file's mode, `password.db`, the storage-key names and a drain still running, the
> networks created at recreate, lake-init running the target release's own script, k2-api in verify and the
> live access suite in a tools one-shot are written from `scripts/deploy.py` on this branch. Their lines
> below are that script's own messages, quoted, not a transcript; the first deploy of a release of this
> phase is their first live run.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Compile a release from a tag | <2 min | 2.7 to 4.7 s a scratch tag, its two local builds cached (2026-11-27) |
| 2 | See what a deploy will do | <1 min | 0.8 s for `PLAN=1`, 0.1 s for the diff (2026-11-27) |
| 3 | Deploy, rules only | <1 min | 9.1 to 13.4 s; no container restarted; the new rules loaded 0.19 to 4.25 s after the swap (2026-11-27) |
| 3 | Deploy, one service's image | <2 min | 10.8 s, the stand-in alone recreated (2026-11-27) |
| 4 | Check what runs | <1 min | 1.4 to 1.7 s (2026-11-27) |
| 6 | A deploy that failed verification | <2 min | 16.1 to 19.0 s from the command to the previous release verified (2026-11-27) |
| 7 | A deploy that was interrupted | <2 min | 9.4 s for the rerun; killed after each of the 13 steps in turn, every rerun converged (2026-11-27) |
| 8 | The sites failed or are stale | <2 min | not yet measured: the first deploy of a release of this phase times its `site` step |

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
make deploy RELEASE=<release id>
```

```text
deploy: preflight ok (0.2 s)
deploy: images ok (0.5 s)
deploy: plan v0.0.1-rehearsal.5-2a37b3160f57 -> v0.0.1-rehearsal.6-4d9b7b32a3a4
  recreate, one at a time: nothing
...
deploy: ddl ok (0.8 s)
deploy: views-narrow ok (0.0 s)
  k2_release_7160b886ca37: line written in place
deploy: canary ok (0.0 s)
  as the VM sees them: {"airflow": "../v0.0.1-rehearsal.6-4d9b7b32a3a4", ... "trino": "../v0.0.1-rehearsal.6-4d9b7b32a3a4"}
  prometheus: SIGHUP, its rules reread through the pointer
deploy: swap ok (0.9 s)
  nothing to recreate
deploy: recreate ok (0.0 s)
  k2_release_7160b886ca37 admitted and k2_release_af0a1b537aff refused 0.22 s after the swap
deploy: rules-loaded ok (5.1 s)
deploy: ddl-late ok (0.0 s)
deploy: views-widen ok (0.0 s)
  standin: pointer trino is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  prometheus: pointer prometheus is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  trino: pointer trino is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  canary k2_release_7160b886ca37 admitted, k2_release_af0a1b537aff refused
  SKIP access-verify: built by phase 001 step S20
  SKIP catalog drift check: scripts/verify/catalog_drift.py is built by phase 001 step S20
  images and health checked for 8 container(s)
deploy: verify ok (1.8 s)
  removed canary k2_release_af0a1b537aff
deploy: done ok (0.0 s)
deploy: v0.0.1-rehearsal.6-4d9b7b32a3a4 is live and verified
deploy: exit 0: done
```

`rules-loaded` holds one more refresh period (5 s) after the canary is admitted, for the groups' own timer.

The output above is the rehearsal's from before phase 002. The steps added since print these lines, quoted
from `scripts/deploy.py` with its placeholders, in the order they run (which ones appear depends on the plan):

```text
  drain: <why>                            # e.g. "no Trino restart and no DAG's task list changes"
deploy: plan ok (...)
  no drain: <why>                         # or: drained <dags> (<why>) in <n> s; paused <dags or none>
deploy: drain ok (...)
  one-shot lake-init exited 0 in <n> s
deploy: ddl ok (...)
  <service> stopped and removed: behind profile <p> in <release id>
deploy: remove-<service> ok (...)
  network <project>_k2-egress created: no service compose starts uses it yet
  <service> recreated, healthy in <n> s
deploy: recreate ok (...)
  catalog drift check clean (<n> differences, pending none, <n> skipped, <n> left by a release since)
  LEFT <kind> <name>: declared by <release id>, a release deployed or attempted since the one that is live; ...
  access-verify LIVE=1 passed: {'PASS': <n>, 'FAIL': 0, 'SKIP': <n>}
  k2-api: GET http://127.0.0.1:8095/v1/release names <release id>
deploy: verify ok (...)
deploy: done ok (...)
  catalog site: <the generator's last line>
  dbt docs: <k2-dbt docs' last line>
deploy: site ok (...)
deploy: <release id> is live and verified
  unpaused <dags>: the DAGs deploy paused
deploy: exit 0: done
```

While a product the drift check finds unpublished remains (a DAG not yet run), the live suite prints
`SKIP access-verify LIVE=1: not yet published, so no live row can read it: <products>; ...` instead.

**Measured**, 2026-11-27, on the rehearsal, from the first to the last record of `deploy-log.jsonl`: a first deploy 33.2 s (Trino started from the compiled etc in 10.9 s, three Spark tables created after it); a rules-only release 9.1 to 13.4 s with no container restarted (every `State.StartedAt` unchanged), its rules loaded 0.19 to 4.25 s after the swap and a 60-minute delay seen by a reader polling every 50 ms 3.84 s after the swap; a release changing one service's image 10.8 s, that service alone recreated.

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

```text
  standin: pointer trino is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  prometheus: pointer prometheus is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  trino: pointer trino is ../v0.0.1-rehearsal.6-4d9b7b32a3a4
  canary k2_release_7160b886ca37 admitted, k2_release_af0a1b537aff refused
  SKIP access-verify: built by phase 001 step S20
  SKIP catalog drift check: scripts/verify/catalog_drift.py is built by phase 001 step S20
  images and health checked for 8 container(s)
release-verify: v0.0.1-rehearsal.6-4d9b7b32a3a4 everywhere
deploy: exit 0: done
releases root <home>/s19-rehearsal-3452690/releases; good release v0.0.1-rehearsal.6-4d9b7b32a3a4
  pointer trino -> ../v0.0.1-rehearsal.6-4d9b7b32a3a4 (as the host sees it)
  ...
  canary lines in trino/password.db: k2_release_7160b886ca37
  releases kept: v0.0.1-rehearsal.1-9b8473db7182, ..., v0.0.1-rehearsal.6-4d9b7b32a3a4
  2026-11-26T20:57:37.122137Z v0.0.1-rehearsal.5-2a37b3160f57 done ok (from v0.0.1-rehearsal.3-8d67e7c865fe, by <operator>)
```

`release-status` also prints `DAGs a deploy paused and has not yet unpaused: <dags or none>`.

**Measured**, 1.4 to 1.7 s for `release-verify`, 0.1 s for `release-status`, 2026-11-27, on the rehearsal,
before the live access suite and k2-api joined verify; with them, not yet measured.

---

## 5. A deploy was refused (exit 2)

**Symptom**, `deploy: REFUSED: ...` and `deploy: exit 2: refused before any change`. `make` itself
reports any failed recipe as `Error 2`; the script's own code is its last line.

**Detection**, the command's output; the deploy log's last record is the refused step, `failed`.

**Expected behaviour**, nothing has moved: no pointer, no service, no view, no canary line. Read the
message, fix the cause, run the same command again.

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

`make site` prints one line per site, then `site: <release id>`, per `scripts/deploy.py`; `make
site-install` ends `Scheduled daily at 05:15 UTC. Deploy regenerates the site too; 'make site' runs it
now.` (`mk/run.mk`). The unit runs the checkout it was installed from, so install it from the main checkout.

**Measured**, not yet measured: the first deploy of a release of this phase times its `site` step.

---

## Failure modes / incidents

- **2026-11-27**, rehearsal findings, each fixed before this was written: a release sealed inside the tools
  container was still writable on the host (Docker Desktop keeps a container's `chmod` to itself; deploy
  now seals on the host too); every release rebuilt its local images with new ids (each tag's `git archive`
  file times, and BuildKit's provenance); Python 3.13 refused a CA certificate without keyUsage; a first
  deploy cannot create Spark or Trino tables before it has started them (now `ddl-late`).

---

**Last verified:** 2026-11-27 against the rehearsal, branch `step/s19-deploy`; on the live stack only the first deploy (the cutover, [first-cutover-to-releases.md](./first-cutover-to-releases.md), 00:28 to 00:31 UTC, release `v3.0.0-alpha.3-rc.1-490b0533ff56`); a second ordinary deploy, a rules-only deploy and a deploy that rolls itself back are not yet live. The steps and refusals phases 002 and 003 added (drain and `ALLOW_RUNNING`, `remove-<service>`, the networks, lake-init from the target, the env-mode, `password.db`, secret-file and storage-key refusals, `ALLOW_STATEFUL=minio`, k2-api and the live suite in verify, the site step, `make site`, `make site-install`): not yet: first run after the deploy of this release.

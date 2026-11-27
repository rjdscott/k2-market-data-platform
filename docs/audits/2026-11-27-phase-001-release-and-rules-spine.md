# Audit: phase 001, the release and rules spine, 2026-11-27

**Verdict:** not yet. The live stack runs release `v3.0.0-alpha.3-rc.1-490b0533ff56` and it
verifies: `make release-verify` exits 0 with every pointer, image and health check on it and
`identity.sh` 18 PASS, `make health` passes, the budget gate prints the plan's 16.80 CPU /
28.750 GiB, no generated file is tracked, Trino refuses plain HTTP and a statement with no
password, Lakekeeper has no host port and one network, and Alertmanager has sent 3 Slack
notifications with 0 failures. But six HIGH findings stand against the exit. The pinned MinIO
image can no longer be pulled from Docker Hub (HIGH 1), so the Trino job of CI, which carries
"writer A is denied on product B" and the access test, has never passed for this phase, and no
host without the cached image can start a throwaway lake or a fresh stack. The other five HIGH
come from the two independent reviews of pull request #250 and are being fixed now (HIGH 2 to 6).
Three lines of the plan's Verification block cannot run as written (LOW 12, 14, 15). Before the
tag: the six HIGH fixed, CI green on the pushed head, a live deploy and `make release-verify` of
the release cut from that head (the live one is `v3.0.0-alpha.3-rc.1`, 11 commits behind the
audited commit), and the runbooks that still describe the stack before the cutover brought up
to date (MED 9).

| | |
|---|---|
| **Commit** | `6f01477` (local `phase/001-release-and-rules-spine`; pull request #250's pushed head is `cb132a9`, 4 commits behind). Live: release `v3.0.0-alpha.3-rc.1-490b0533ff56` from tag `v3.0.0-alpha.3-rc.1` (`02c77d4`), deployed 2026-11-27 00:28 to 00:31 UTC |
| **Scope** | `docs/plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md`: its Exit, the Verification block line by line, the Scope bullets and the learning aids. Against them: the live stack (read-only), CI on pull request #250, the tree, the deploy log and the release-time records under `~/k2-releases/verify/`, the decided deviations and where each is written, the pullability of every pinned third-party image, and a sample of 12 claims from `CLAUDE.md`, `docs/runbooks/deploy-a-release.md`, `docs/runbooks/verify-access-after-a-release.md`, `docs/operations/quick-reference.md` and `docs/architecture/21-releases-and-deploy.md` |
| **Out of scope** | Rediscovering what reviews R1 and R2 of pull request #250 found (their HIGH rows are listed below as fix in progress; their MED and LOW rows are cited where a plan line depends on them); phase 002 and later; the P0, P2 and P3 audits' open rows |
| **Lens** | Claims against reality: the phase's exit, doc accuracy, what was proven live and what only on a rehearsal |
| **Method** | Live strictly read-only. Run from the live checkout with `.env` loaded in a subshell, printing no value: `make release-verify`, `make release-status`, `make health`, `make tls-status`, `make principals-status`, `python3 scripts/check-budget.py`; `docker inspect`, `docker port` and unauthenticated `curl` for ports and refusals; Alertmanager's `/metrics`. `scripts/verify/identity.sh` ran once, inside `make release-verify` (`scripts/deploy.py:1189`), not a second time, since it writes and removes a probe line in the live `password.db`. Not run: anything that deploys, rolls back or writes to Trino or the catalog, `amtool alert add` (it posts to Slack), and the throwaway suites (`make test-trino`, `make test-access`, `make access-verify RELEASE=...`, `make spine-e2e`, `make deploy-rehearsal`), for which CI's result and the records are cited. Run in the audit worktree: `make test-k2gen`, `pytest tests/test_principal_secret.py`, `make check-docs`. `docker manifest inspect` for each distinct third-party image reference, one at a time. Secrets: names and modes only |
| **Severities** | BLOCKER: a reader sees data they are denied, a secret exposed, data loss. HIGH: an exit criterion not met or a guard that does not guard. MED: a document claims what was not run, or a criterion met only in part. LOW: stated limits, cosmetics. "LOW (stated)": a limit the documents already state |
| **Findings** | 0 BLOCKER · 6 HIGH · 5 MED · 8 LOW |

---

## The Verification block, line by line

Run 2026-11-27 between 00:33 and 00:50 UTC.

| Plan line | How it was checked | Printed (trimmed) | Holds |
|---|---|---|---|
| 155 `make deploy RELEASE=v3.0.0-alpha.3 && make release-verify` | live deploy record (`make release-status`); `make release-verify` re-run | deploy of `v3.0.0-alpha.3-rc.1-490b0533ff56`, 12 records 00:28:47Z to 00:31:07Z, each `ok`; release-verify: `identity.sh: 18 PASS, exit 0`, `release-verify: v3.0.0-alpha.3-rc.1-490b0533ff56 everywhere`, exit 0 in 41.9 s | for the release candidate; tag `v3.0.0-alpha.3` not cut (LOW 13) |
| 156 `K2_DEPLOY_FAULT=verify-mismatch make deploy ...` | not run (deploys); `scripts/verify/deploy_rehearsal.py:24` "a forced mismatch restoring R-1, images included, exit 1" | rehearsal only, as `deploy-a-release.md:17-24` states | rehearsal only (LOW 13) |
| 157 `make release-verify` reports the previous release | not run (needs line 156) | rehearsal only | rehearsal only (LOW 13) |
| 158 `bash scripts/verify/rules-only-deploy.sh` | `ls scripts/verify/` | no such file | cannot run as written (LOW 12) |
| 159 `bash scripts/verify/grant-refresh.sh` | not run (throwaway); its usage is `<prefix> [n]` on a lake `prove.sh` staged | last measured 2026-11-26 (`verify-access-after-a-release.md:287-289`) | no runner reaches it at the audited head (MED 8) |
| 160 `git ls-files docs/products docker/lake/generated docker/lake/ddl/generated docker/trino/etc/rules.json \| wc -l` | run in the audit worktree | `0` (and `git ls-files docker/trino/etc` prints nothing) | yes |
| 161 `make test-k2gen` | run in the audit worktree at `6f01477` | `1361 passed, 2 skipped in 30.14s`; definer view, `env_var` and coverage cases in `platform/k2gen/tests/test_checks_sql.py:21,114` and `tests/fixtures/v2/bad/access/cases.yaml:153,187` | yes at `6f01477`; red in CI at `cb132a9` (MED 7) |
| 162 `make test-trino` | CI job 140234732701 on `cb132a9` | `docker: Error response from daemon: pull access denied for minio/minio, repository does not exist` (00:17:40Z), exit 125 | never passed for this phase (HIGH 1) |
| 163 `make access-verify` | CI: the step after `trino-rules-test.sh`, never reached; live preflight record `~/k2-releases/verify/v3.0.0-alpha.3-rc.1-490b0533ff56.access-release.json`; live: `make release-verify` | preflight 00:27:41Z, scope `release`, exit 0, `{'PASS': 63, 'FAIL': 0, 'SKIP': 3}` (the 3 network probe rows); live `SKIP access-verify LIVE=1: not yet published` | on a throwaway lake at deploy time; never in CI (HIGH 1); no live row (LOW 19) |
| 164 `bash scripts/verify/identity.sh` | inside `make release-verify` | `identity.sh: 18 PASS, exit 0`; by hand: `curl` plain HTTP to 8443 exits 1, a statement with no password gets `401` | yes |
| 165 `bash scripts/verify/resource-groups.sh` | not run (throwaway) | runs only inside `tests/access/prove.sh:83` | no runner reaches it at the audited head (MED 8) |
| 166 `bash scripts/verify/tested-data-marker.sh` | live preflight record `...marker.json` | 00:27:49Z, exit 0, 11 PASS, 0 FAIL | at deploy time, on a throwaway lake from the release |
| 167 `docker exec k2-alertmanager amtool ... alert add K2TestAlert` | not run (posts to Slack); its replacement is `make alert-test` (`where-an-alert-goes.md:130-144`) | | replaced (LOW 14) |
| 168 `curl -s 127.0.0.1:9093/metrics \| grep -E '...\{integration="slack"\}'` | run as written, and without the closing `\}` | as written: `alertmanager_notifications_total{integration="slack"} 3` only; without it, every `alertmanager_notifications_failed_total{integration="slack",reason=...}` is `0` | delivery yes (k2-alertmanager up since 2026-11-26T18:19:20Z); the line cannot show the failed counter (LOW 14) |
| 169 `gh run view <validation run id> --log \| grep 'k2gen diff'` | job 140234732735's log | nothing: the step logs only `k2gen release: NO PIPELINE market-analytics.hot-gold ...` and the release path | cannot run as written (LOW 15) |
| 170 `python3 scripts/check-budget.py` | live checkout | `bootstrap default 22 services 16.80 CPU 28.750 GiB`, `PASS`, exit 0 | yes |
| 171 `make test` | not run (heavy jobs on the host); CI on `cb132a9` | 3 of 14 checks fail: Trino, Contracts (k2gen), Python | no (HIGH 1, MED 7) |
| 172 `make check-docs` | run in the audit worktree with this file | exit 0 | yes |

## The Scope bullets

| Bullet | State | Evidence |
|---|---|---|
| The stack runs from releases | partly, decided | Trino runs `--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc` (`docker inspect k2-trino`); five pointers name the live release (`make release-status`); Airflow's two, Spark and Prometheus mount `/k2/releases` read-only (`docker inspect` Mounts) and read nothing through it (Prometheus `--config.file=/etc/prometheus/prometheus.yml`): decided, below |
| Generated files leave git | done | Verification line 160 prints `0` |
| The validation runner in CI | partly | `ci.yml:93-97` runs `ci-validate.sh --diff` (check, trial compile, diff to the step summary) and `make test-k2gen`; dbt job passes; the access test step (`ci.yml:141-143`) has never run to completion (HIGH 1) |
| Identity: every client moved | done for what exists | Trino CLI (I15), dbt-trino with the `trino` Python client under it (I16), Trino's client protocol (I01 to I13), the access test over HTTPS (preflight record): `identity.sh` 18 PASS. Airflow tasks: none exist (`docker/airflow/dags/` holds `.gitkeep`), so nothing to move until phase 002 |
| Principals, grants and policies | done, live part deferred | `contracts/access/roles.yaml`, `principals.yaml`, `grants.yaml` for both products, `tests/access/expectations.yaml`; release-time access-verify 63 PASS; the live run is a SKIP and the three `p.canary-*` people have no password (`make principals-status`) (LOW 19) |
| The tested-data marker | done (reader side) | preflight marker record 11 PASS; ADR-061 |
| `hot-gold` declared paused | done | `contracts/market-analytics/hot-gold/product.odps.yaml:8` `status: paused`, `:28-32` `serves` with `engine: clickhouse`, `status: paused` |
| Alertmanager | done | `docker-compose.yml:531` `prom/alertmanager:v0.35.1@sha256:...`, `cpus: '0.1'`, `memory: 64M`; Prometheus rules `AlertmanagerDown`, `AlertmanagerNotificationsFailing` loaded (`/api/v1/rules`); 3 sent, 0 failed |
| Runbooks: deploy and roll back | done, stale since the cutover | `deploy-a-release.md`, `roll-back-a-release.md`, `first-cutover-to-releases.md` (MED 9) |
| Runbooks: grant, revoke and verify access | partly | verify: `verify-access-after-a-release.md`; grant and revoke: one clause (MED 11) |
| Runbook: where an alert goes | done, stale since the cutover | `where-an-alert-goes.md` (MED 9) |
| Chapters 21 and 22 | done | `docs/architecture/21-releases-and-deploy.md` (3 Mermaid diagrams), `22-access-and-identity.md` (2); two wrong claims in 21 (LOW 16, 17) and one unbacked one (MED 10) |
| `CLAUDE.md` and `SECURITY.md` updated | done | `git diff origin/main...HEAD --stat`: `CLAUDE.md` 34 lines, `SECURITY.md` 38; the bind-mount paragraph names the releases root |

## The decided deviations, and where a reader finds them

| Deviation | Written in |
|---|---|
| Airflow, Spark and Prometheus mount the releases root, read nothing through a pointer until phase 002 | `CLAUDE.md:19-21`, `docker-compose.yml:84-86`, `:737`, `:783`; not in chapter 21, whose reading guide says the opposite (LOW 17) |
| Trino on the MinIO root pair until phase 002 | `first-cutover-to-releases.md:90-92`, `fresh-install.md:84`, `A1-technology-stack.md:19` |
| The compose project name kept (`K2_COMPOSE_PROJECT`) | `first-cutover-to-releases.md:30-36`; `deploy-a-release.md:11` still says the default is `k2` (R1 MED) |
| Folding the C1 rows into the main rules suite deferred to the hardening sprint | pull request #250's body only; `git grep` finds it in no file (LOW 18) |
| A fresh install of the release-shaped stack not run | `fresh-install.md:13`, `:103-109`, `first-cutover-to-releases.md:163-164` |

`CHANGELOG.md` `[Unreleased]` lists none of the five (LOW 18).

## Pinned third-party images

`docker manifest inspect <ref>`, one at a time, for every distinct reference in
`docker-compose.yml`, the live Dockerfiles (`docker/airflow`, `docker/spark`, `docker/tools`,
`services/capture-rust`) and `scripts/lib/throwaway-lake.sh` (19 references):

| Reference | Result |
|---|---|
| `minio/minio:RELEASE.2025-09-07T16-13-09Z` (`docker-compose.yml:323`, `:1030`) | **fails**: `denied: requested access to the resource is denied` / `unauthorized: authentication required`; `minio/minio:latest` the same. The host holds it cached as `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` |
| `docker.redpanda.com/redpandadata/redpanda:v25.3.4`, `.../console:v3.5.1`, `clickhouse/clickhouse-server:24.3-alpine`, `prom/prometheus:v3.2.0`, `grafana/grafana:11.5.0`, `ghcr.io/google/cadvisor:v0.61.1`, `prom/alertmanager:v0.35.1@sha256:4e3d...`, `postgres@sha256:fe07...`, `prom/statsd-exporter:v0.31.0`, `quay.io/lakekeeper/catalog:v0.13.9`, `trinodb/trino:484`, `alpine:3.20`, `apache/airflow:slim-3.3.2-python3.12`, `spark:3.5.9-scala2.12-java17-python3-ubuntu@sha256:8901...`, `python:3.12.16-slim-bookworm@sha256:44cf...`, `rust:1.100.0-bookworm`, `gcr.io/distroless/cc-debian12:nonroot@sha256:9dac...`, `ghcr.io/astral-sh/uv:0.9.25-python3.12-bookworm-slim@sha256:e47c...`, `ghcr.io/astral-sh/uv:0.13.10` | all resolve (exit 0, a manifest list or OCI index) |

## Doc truth sample

| # | Claim | Check | Result |
|---|---|---|---|
| 1 | `CLAUDE.md:13-14`: Trino serves HTTPS only on `127.0.0.1:${K2_TRINO_PORT:-8443}` | `docker port k2-trino`; `curl` plain HTTP to 8443; a statement with no password | `8443/tcp -> 127.0.0.1:8443`; curl exit 1; `401`. Holds |
| 2 | `CLAUDE.md:21-22`: Lakekeeper on `k2-catalog` alone, no host port | `docker inspect k2-lakekeeper` networks and PortBindings; `curl 127.0.0.1:18181` | `k2-market-data-platform_k2-catalog`, `{}`; refused. Holds |
| 3 | `CLAUDE.md:19-21`: Airflow, Spark and Prometheus mount the releases root, their pointer content arrives in phase 002 | `docker inspect` Mounts; Prometheus Args | `/k2/releases` read-only on all four; `--config.file=/etc/prometheus/prometheus.yml`. Holds |
| 4 | `CLAUDE.md:166`: `make test` is python, rust, clickhouse, trino, dbt, notebooks, k2gen, airflow | `Makefile:48` | the same eight. Holds; its trino part cannot pull MinIO off this host (HIGH 1) |
| 5 | `CLAUDE.md:17`: `make release TAG=<annotated tag>` | `platform/k2gen/k2gen/docset.py:46` | a lightweight tag is refused. Holds |
| 6 | `deploy-a-release.md:11`: compose project `k2` by default | `docker inspect k2-trino` project label; `scripts/deploy.py:157` | live label `k2-market-data-platform`; default `"k2"` matches no stack `make up` makes. Wrong (R1 MED) |
| 7 | `deploy-a-release.md:21-23`: the live stack does not read releases yet; do not run these commands on it | `make release-status` | five pointers on the live release since 00:31 UTC. Wrong (MED 9) |
| 8 | `deploy-a-release.md:32`: check what runs, 1.4 to 1.7 s | `time make release-verify` on live | 41.9 s, exit 0: within the <1 min target; the figure is the rehearsal's and excludes `identity.sh` |
| 9 | `verify-access-after-a-release.md:19-22`: the live Trino has no HTTPS or passwords; `make access-verify LIVE=1` exits 2 | checks 1 and `make release-verify` | HTTPS with passwords; the live suite is a SKIP inside an exit 0. Wrong (MED 9) |
| 10 | `quick-reference.md:170-173`: Trino runs from the release's etc, its Web UI is off | `docker inspect k2-trino` Args; `curl /ui/` | `["run","--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc"]`; `303` to `/ui/static/disabled.html`. Holds |
| 11 | `21-releases-and-deploy.md:138`: the deploy log records each secret file by sha256 only | keys of every record in `~/k2-releases/deploy-log.jsonl` | 17 records; each `secrets` entry holds `mode` and `size`, no hash. Wrong (LOW 16) |
| 12 | `21-releases-and-deploy.md:119`, `:210-211`: five pointers, k2-api's exists and nothing reads it | `ls ~/k2-releases/pointers`; `docker ps -a` | `airflow k2-api prometheus spark trino`; no k2-api, fetch or dbt-runner container. Holds |

Could not check: `quick-reference.md:180-195`'s `trino_cli svc.platform 'SHOW CATALOGS'` on the live
stack (the doc itself says "Not yet run on the live stack"): logging in as `svc.platform` was denied
to the auditor by this session's permission policy, and was not worked around.

## Findings

| # | Severity | file:line | Claim | Reality | Fix |
|---|---|---|---|---|---|
| 1 | HIGH | `docker-compose.yml:323`, `:1030`; `scripts/lib/throwaway-lake.sh:105` | The stack's images are pinned, so any host can start it and CI can run the rules suite (`CLAUDE.md` Tests; plan line 162) | `docker manifest inspect minio/minio:RELEASE.2025-09-07T16-13-09Z`: `denied: requested access to the resource is denied`. CI job 140234732701: `pull access denied for minio/minio, repository does not exist` at 00:17:40Z. The phase's Trino job failed in both its CI runs (45915356280, 45915842808), so "writer A denied on B" and the CI access test never passed; `make test`, `make test-access`, `make spine-e2e`, deploy's preflight lakes and a fresh install work only where the image is cached. The other 18 third-party references resolve | Fix in progress: mirror the cached image (`sha256:14cea493...`) to a registry the project controls and pin it by digest in compose and `throwaway-lake.sh`; replacing MinIO is a hardening-sprint ADR |
| 2 | HIGH | `scripts/deploy.py:1105-1108`, `1134-1136` (review R1) | A failed deploy rolls everything back | Migration one-shots run the target's schema migration before the rollback boundary, with no backup and no reverse | Fix in progress (R1 row 1) |
| 3 | HIGH | `scripts/deploy.py:770`, `696-701` (review R1) | Rollback always has a path | Every rollback starts by narrowing views through Trino; with Trino down under the target, neither the automatic rollback nor `make rollback` reaches the swap | Fix in progress (R1 row 2) |
| 4 | HIGH | `scripts/deploy.py:1129`; `docs/adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md:96-97` (review R1) | An image a kept release names is never pruned | Nothing implements it; preflight checks only the target's images, and a rebuild does not restore the id | Fix in progress (R1 row 3) |
| 5 | HIGH | `docker-compose.yml:480`, `:742` (review R1) | No `k2-net` service alone can reach the catalog fence | cAdvisor and the Airflow scheduler hold the Docker socket, one hop from `k2-catalog`, the secrets and Trino's environment | Fix in progress (R1 row 4) |
| 6 | HIGH | `scripts/verify/catalog-fence.sh:15-23`; `scripts/access_verify.py:359-360` (review R2) | The catalog fence is verified | Only a manual `make catalog-fence` probes it, its probes count a failed container as refused, and nothing checks Lakekeeper's ports or the network's members | Fix in progress (R2 row 1) |
| 7 | MED | pull request #250, run 45915842808 at `cb132a9` | The phase's pull request is green on the 13 required checks | Three fail: Trino (finding 1), Contracts (`test_emit_trino_rules.py:59`, a golden mismatch) and Python (`test_principal_secret.py:117`, `unknown switch 'J'`). The last two are fixed in unpushed commits `21929fd` and `1db2721`/`6aafd73`: at `6f01477`, `make test-k2gen` gives `1361 passed` and `test_principal_secret.py` `27 passed` | Push the branch; CI green on the pushed head before the tag |
| 8 | MED | plan lines 159, 165; `tests/access/prove.sh:54`, `:81-83`; pull request #250 body, "How it was proven" | `make spine-e2e` proved the phase, `grant-refresh.sh` and `resource-groups.sh` included | Neither script has a runner outside `prove.sh` (CI and deploy call neither: `git grep`), and `prove.sh` expects `'0 FAIL, 0 SKIP'` while the three probe rows always SKIP since `7f6220c` (2026-11-26T23:47Z): the live preflight record shows `'SKIP': 3`. So `make test-access` and spine-e2e's `test-access` stage stop at step 2, before both (review R2 MED). Their last measurement is 2026-11-26, before `7f6220c` | Match the probe SKIPs in `prove.sh` (R2's fix), rerun `make spine-e2e` at the pushed head and record its stage lines in the pull request |
| 9 | MED | `docs/runbooks/deploy-a-release.md:21-23`, `:344`; `roll-back-a-release.md:12-14`, `:163`; `verify-access-after-a-release.md:19-22`, `:28`, `:114`, `:301`; `where-an-alert-goes.md:15-16`, `:343-344` | The live stack does not read releases, its Trino has no HTTPS or passwords, `LIVE=1` exits 2, `k2-alertmanager` does not exist; each "not yet on the live stack" | Since 00:31 UTC the stack runs a release (`make release-status`), Trino answers HTTPS and 401 (sample 1), `make release-verify` exits 0 with the live suite a SKIP, and `k2-alertmanager` has run since 2026-11-26T18:19:20Z with 3 notifications sent, 0 failed. `CLAUDE.md` asks that a document about the running system be updated in the pull request that makes the change real | Rewrite each target-state block and "Last verified" line from the live cutover and this audit's runs |
| 10 | MED | `docs/architecture/21-releases-and-deploy.md:212-213`; `docs/adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md:196-197` | The releases root is in the backup set | The live release's `compiled/lake/backup-set.json` lists `{'kind': 'releases'}`, but nothing copies it: `scripts/backup-lake.sh` names neither `K2_RELEASES` nor `k2-releases` (0 matches), the timer runs only that script (`scripts/systemd/k2-backup.service:11`), and `~/k2-backup` holds `catalog`, `drop`, `lake`, `notes`. With HIGH 4, a lost root cannot be rebuilt to the same release id | State in chapter 21's limits that `make backup` copies no release until phase 002 implements the backup set, or copy the root now |
| 11 | MED | plan line 147; `docs/runbooks/create-or-rotate-a-principal.md:5-7` | Runbooks: grant, revoke and verify access | Verify is `verify-access-after-a-release.md`. Granting or revoking a role is one clause ("a pull request to that product's `grants.yaml`"): no section, no command sequence (tag, `make release`, `make deploy`), no measured time to effect; `git grep -i revoke docs/runbooks` finds only credential removal | Add a section: change `grants.yaml`, release, deploy, prove with `make access-verify LIVE=1`, the 5 s refresh measured in `verify-access-after-a-release.md:287-289` |
| 12 | LOW | plan lines 158, 175 | `bash scripts/verify/rules-only-deploy.sh` | No such script. What replaces it: `make deploy-rehearsal`'s check "R2 (rules only) deploys and restarts no container" (`scripts/verify/deploy_rehearsal.py:502-537`), and `grant-refresh.sh`'s start-time comparison; rehearsal only, as `deploy-a-release.md:30` states | Name the replacement in the plan's landed line |
| 13 | LOW (stated) | plan lines 155-157 | Deploy, forced mismatch and rollback of `v3.0.0-alpha.3` | The tag is not cut; the live deploy was the release candidate at `02c77d4`, 11 commits behind `6f01477`, and the review fixes change `scripts/deploy.py` again. The forced mismatch and the rollback ran on the rehearsal only, as `deploy-a-release.md:17-24` and `roll-back-a-release.md:163` state | Deploy the release of the final tag live and run `make release-verify` before the tag is announced |
| 14 | LOW | plan lines 167-168, 176-177 | The Slack check passes when the sent counter rose and the failed counter did not | As written, the grep's closing `\}` matches only the sent counter; every failed series carries a `reason` label. The `amtool` line is replaced by `make alert-test` (`where-an-alert-goes.md:130-144`) | `make alert-test`, or the grep without `\}` |
| 15 | LOW | plan line 169 | `gh run view <id> --log \| grep 'k2gen diff'` shows the diff | `scripts/verify/ci-validate.sh:41-44` writes the diff to `$GITHUB_STEP_SUMMARY`, not the log; job 140234732735's log has no such line | Read the run's summary page; correct the plan line |
| 16 | LOW | `docs/architecture/21-releases-and-deploy.md:138` | The deploy log records each secret file by sha256 only | Every `secrets` entry in the 17 records holds `mode` and `size`, as `ADR-058` Outcome part 4 says | Say "by mode and size" |
| 17 | LOW | `docs/architecture/21-releases-and-deploy.md:124-128`, `:205-214` | Each service resolves its pointer; moving one changes what a service reads | Only Trino and `lake-init` read through it (`docker-compose.yml:84-86`); the limits section omits the decided deviation | Add it to "The limits, today" |
| 18 | LOW | `CHANGELOG.md` `[Unreleased]`; pull request #250 body | The decided deviations are recorded | Four of five are in a runbook, `CLAUDE.md` or compose; folding the C1 rows into the main rules suite in the hardening sprint is only in the pull request body (`git grep` finds it nowhere), and the release notes list none of the five | A known-limits block in the `[Unreleased]` entry naming all five |
| 19 | LOW (stated) | plan lines 133-135; `first-cutover-to-releases.md:159-162` | `make access-verify` runs every row on a throwaway Trino and then live | No live row has run: `make release-verify` prints `SKIP access-verify LIVE=1: not yet published`, and `p.canary-delayed`, `p.canary-full` and `p.canary-scoped` have no password or file (`make principals-status`). The runbook states both | As stated: decide how the people's password files are made before phase 002 publishes the canary |

Counts: 0 BLOCKER · 6 HIGH · 5 MED · 8 LOW.

## Resolutions

Appended after the audit; the findings above stand as written.

- Finding 1: Resolved in `b20b1af`. The image is the project's unmodified mirror `ghcr.io/rjdscott/minio`, pinned by tag and digest; a throwaway lake started from it (`ONLY="access-verify" bash scripts/verify/spine-e2e.sh`, PASS in 29 s, 2026-11-27). Replacing MinIO is left to the hardening sprint.
- Finding 2: Resolved in `91b4513`. A deploy dumps a database before a one-shot migrates it and refuses when the dump fails; the restore is section 5 of `docs/runbooks/roll-back-a-release.md`. The dump's place moved out of the releases root in `fbe20fa`.
- Finding 3: Resolved in `91b4513`. With Trino down a rollback moves the pointers and recreates first, then narrows the views; both orders are tested and the rehearsal stops Trino before one rollback.
- Finding 4: Resolved in `91b4513`. Preflight refuses when an image the good release pins is gone, and `make release-status` prints the same check.
- Finding 5: Not fixed, stated. `docs/adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md`, Outcome of 2026-11-27 (`9fdeb80`): the two services that hold the Docker socket are inside the catalog's trust boundary; a read-only socket proxy for cAdvisor is a hardening item.
- Finding 6: Resolved in `c35ef39` and `91b4513`. A test holds `docker-compose.yml` to the allow-list on every pull request; `scripts/verify/catalog-fence.sh` checks the running stack's structure and probes, and every `release-verify` runs it. On the live stack, 2026-11-27 00:33 UTC: `catalog-fence: 6 PASS, 0 FAIL`.
- Finding 7: Resolved in `21929fd` and `6aafd73` (the golden and the test), and finding 1's commit for the Trino job.
- Finding 8: Resolved in `4d2673a`. `make spine-e2e` on the merged branch, 2026-11-27: `PASS test-access 114s`, all five stages PASS in 1995 s.
- Findings 9 and 11: Resolved in `ab77577`.
- Finding 10: Open, for the hardening sprint.
- Finding 16: Resolved in the commit that adds this section.
- Finding 18: Resolved in the commit that adds this section (`CHANGELOG.md`, 3.0.0-alpha.3, Decided).

## Not checked

- Anything that deploys, rolls back, or logs in to the live Trino as a principal (see the sample's
  last line); the throwaway suites and the rehearsals, whose results are cited from CI and records.
- Whether review R2's other MED rows (`catalog-fence.sh` exit handling, the CI step without
  pipefail) are in the fixes now in progress.
- Slack itself: `alertmanager_notifications_total` counts Slack's `ok`, not a message read in the
  channel.

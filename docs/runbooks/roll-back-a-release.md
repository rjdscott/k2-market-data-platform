# Runbook: Roll back a release

Use this to put the previous release back on purpose, to finish a rollback that was interrupted, and to
recover when a rollback itself failed (exit 3) or a first deploy failed with nothing to go back to.
A deploy that fails verification rolls itself back; that case is section 6 of
[deploy-a-release.md](./deploy-a-release.md). A healthy rollback across a release that added a product
exits 1, the deploy having failed and been undone, not 3: the tables and views that release created stay
and verify reports them as `LEFT` (section 3). Why rollback is a deploy in reverse:
[ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded**, with the same
`K2_RELEASES`, `K2_SECRETS`, `K2_COMPOSE_PROJECT` and `K2_TRINO_PORT` as the deploy
([deploy-a-release.md](./deploy-a-release.md)).

> **Where these commands ran.** On 2026-11-27, against the rehearsal (`make deploy-rehearsal KEEP=1`),
> compose project `k2-rehearsal`: a rollback has not been run on the live stack. The live stack has read
> releases since its first deploy (2026-11-27, 00:28 to 00:31 UTC, release
> `v3.0.0-alpha.3-rc.1-490b0533ff56`, [first-cutover-to-releases.md](./first-cutover-to-releases.md)), and
> that deploy was a first one: there is no earlier release to go back to, so `make rollback` has nothing
> to restore until a second release is deployed (section 4 is the case it would meet). Going back from the
> cutover itself, to the shape before releases, is section 2 of that runbook. The outputs below are the
> rehearsal's.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Go back to the previous good release | <2 min | 9.6 to 9.8 s, images included (2026-11-27) |
| 2 | A rollback was interrupted | <2 min | 8.3 s for the resumed rollback (2026-11-27) |
| 3 | A rollback failed (exit 3) | <15 min | not induced: the steps below are the same commands as 1 and 2 |
| 4 | A first deploy failed (exit 3, nothing to go back to) | <15 min | not induced on the rehearsal |
| 5 | A migration ran forward: restore the database's dump | <15 min | the restore commands ran on a throwaway Postgres from compose's pinned image (2026-11-27); not induced on a stack |
| 6 | A rollback to a release before phase 002 | <5 min | not yet measured: not run on the live stack |

---

## 1. Go back to the previous good release

**Symptom**, the release live now is wrong (a policy, a column, an image) though it verified.

**Detection**, manual, or a failing `make release-verify` (section 4 of the deploy runbook).

**Expected behaviour**, `make rollback` finds in `$K2_RELEASES/deploy-log.jsonl` the good release before
the current one and deploys it from the current one through the same steps, recorded as `rollback-<step>`:
views lose what the older contract lacks before the pointers move, the older canary's line is written, the
pointers move, the services that differ are recreated on the older compose file and image digests, the
older rules are proven loaded, views gain what the older contract has, every part is verified. After an
interrupted deploy it restores the good release instead, and after an interrupted rollback it resumes it
(section 2). A second `make rollback` goes back one more good release. When Trino does not answer, the
pointers move and the services are recreated first, Trino among them on the good release's etc and rules,
and the views are synced right after; a view that cannot be synced then fails the rollback (exit 3) and the
message names it. A release deployed with `ALLOW_STATEFUL=<service>` is rolled back with the same
`make rollback ALLOW_STATEFUL=<service>`. A service that runs from the current release's default set and
that the good release puts behind a profile is stopped and removed (`rollback-remove-<service>`), so verify
expects none of it. After `rollback-done` the sites are regenerated for the good release (`rollback-site`,
which never fails the rollback, [deploy-a-release.md](./deploy-a-release.md) section 8), and the DAGs a
deploy paused are unpaused. A rollback runs no drain and no one-shot (lake-init, the migrations). A rollback runs the good release's images: never run
`docker image prune -a` (or `docker system prune -a`, or Docker Desktop's clean-up) on this host; if one is
gone, `make release-status` names it with the fix (a registry image: `docker pull <ref>`; a local build:
`make release TAG=<its tag>` and deploy that release instead, since a rebuild has another image id).

**Recovery**

```bash
make release-status
make rollback
make release-verify
```

```text
deploy: rolling back to v0.0.1-rehearsal.5-2a37b3160f57
deploy: plan v0.0.1-rehearsal.6-4d9b7b32a3a4 -> v0.0.1-rehearsal.5-2a37b3160f57
  recreate, one at a time: nothing
  one-shots run: none
  Trino restart: no; DDL: none
  views to re-sync: none
deploy: rollback-views-narrow ok (0.0 s)
  k2_release_af0a1b537aff: line written in place
deploy: rollback-canary ok (0.0 s)
  prometheus: SIGHUP, its rules reread through the pointer
deploy: rollback-swap ok (0.8 s)
deploy: rollback-recreate ok (0.0 s)
  k2_release_af0a1b537aff admitted and k2_release_7160b886ca37 refused 1.76 s after the swap
deploy: rollback-rules-loaded ok (6.6 s)
deploy: rollback-ddl-late ok (0.0 s)
deploy: rollback-views-widen ok (0.0 s)
  canary k2_release_af0a1b537aff admitted, k2_release_7160b886ca37 refused
  images and health checked for 8 container(s)
deploy: rollback-verify ok (1.6 s)
  removed canary k2_release_7160b886ca37
deploy: rollback-done ok (0.0 s)
deploy: rolled back; v0.0.1-rehearsal.5-2a37b3160f57 is live and verified
deploy: exit 0: done
```

**Measured**, 9.6 s for the command above, 2026-11-27, on the rehearsal (nothing to recreate); 9.8 s for one whose stand-in changed image, which was recreated on the previous release's image id. `release-verify` afterwards: 1.4 s.

---

## 2. A rollback was interrupted

**Symptom**, the output of `make rollback` (or of a deploy's own rollback) stops without a `deploy: exit`
line; `make release-status` prints `UNFINISHED attempt since it`.

**Detection**, `make release-status`.

**Expected behaviour**, the good release is still the one before the interrupted rollback, and nothing
reached `rollback-done`. `make rollback` sees that the newest unfinished attempt was a rollback and resumes
it toward the same release, its plan covering whatever the interrupted run moved. (After an interrupted
forward deploy, `make rollback` instead restores the good release.)

**Recovery**

```bash
make release-status
make rollback
make release-verify
```

```text
deploy: rollback-swap ok (0.9 s)
deploy: K2_DEPLOY_FAULT=kill:rollback-recreate: killed after the step's work, before its record
make: *** [mk/release.mk:26: rollback] Error 137
releases root <home>/s19-rehearsal-3452690/releases; good release v0.0.1-rehearsal.6-4d9b7b32a3a4; UNFINISHED attempt since it: v0.0.1-rehearsal.5-2a37b3160f57, v0.0.1-rehearsal.6-4d9b7b32a3a4
  pointer trino -> ../v0.0.1-rehearsal.5-2a37b3160f57 (as the host sees it)
deploy: rolling back to v0.0.1-rehearsal.5-2a37b3160f57
deploy: plan v0.0.1-rehearsal.6-4d9b7b32a3a4 -> v0.0.1-rehearsal.5-2a37b3160f57
...
deploy: rolled back; v0.0.1-rehearsal.5-2a37b3160f57 is live and verified
deploy: exit 0: done
```

**Measured**, 8.3 s for the resumed `make rollback`, 2026-11-27, on the rehearsal; a deploy's own rollback killed after its swap, then `make rollback`, also converged on the previous release (`make deploy-rehearsal`).

---

## 3. A rollback failed (exit 3)

**Symptom**, `deploy: ROLLBACK FAILED: <cause>. The platform is between <failed> and <good>` and
`deploy: exit 3: the rollback failed too: page the owner`.

**Detection**, the command's output; the log's last record is a `rollback-<step>` record, `failed`.

**Expected behaviour**, nothing more happens on its own. The good release named in the message is still
the good one in the log.

**Recovery**, find the cause from the failed step's line (a service that would not become healthy:
`docker compose -p "${K2_COMPOSE_PROJECT:-$(basename "$PWD")}" logs --tail 50 <service>`; Trino not loading the rules:
`make release-status` for the canary lines), fix it, then converge on the good release; `make rollback`
does that after an unfinished attempt, and a deploy of the good release by id does it too:

```bash
make release-status
make rollback
make release-verify
```

---

A release's DDL is additive and nothing undoes it, so after a rollback across a release that added a
product its namespaces, tables and views are still in the catalog. The drift check run by the rollback's
verify and by `make release-verify` reports each as `LEFT <kind> <name>: declared by <release id>, a release
deployed or attempted since the one that is live; nothing reads it under the live rules: ...` and does not
fail on it; anything else undeclared in a namespace of the live release is still `DRIFT`
([verify-access-after-a-release.md](./verify-access-after-a-release.md) section 4). A rollback whose only
differences are `LEFT` lines exits 1, and `make release-verify` afterwards exits 0. The leftovers stay
until a release that declares them is deployed again or they are dropped with `make drop`
([drop-and-rebuild.md](./drop-and-rebuild.md)).

---

## 4. A first deploy failed (exit 3, nothing to go back to)

**Symptom**, `deploy: FAILED on a first deploy: there is no previous release to go back to; the pointers
name <id>` and `deploy: exit 3`.

**Detection**, the command's output.

**Expected behaviour**, the pointers name the failed release and the services the plan recreated run it;
no earlier release exists to return to.

**Recovery**, fix the cause and deploy the same release again (every step is idempotent), or a corrected
tag:

```bash
make deploy RELEASE=<release id>
```

---

## 5. A migration ran forward: restore the database's dump

**Symptom**, a rollback across a release whose `airflow-migrate` or `lakekeeper-migrate` one-shot ran
(its image changed) fails, or the good release's Airflow or Lakekeeper does not become healthy: the
database is at the failed release's schema, and no rollback runs a migration backwards.

**Detection**, the failed step's line (`rollback-recreate`, a service not healthy); the deploy log's
`backup` record of the failed release names the dump: `backups.<database>.path` and `.sha256`.

**Expected behaviour**, the dump was taken by `scripts/backup-lake.sh --dump` right before the
migration, so it holds the good release's schema. Restoring it loses what was written to that database
since the migration.

**Recovery**, stop the database's users (Airflow's services, or `lakekeeper`), restore the dump into a
recreated database of the same owner, then converge on the good release:

```bash
grep '"step": "backup"' "$K2_RELEASES/deploy-log.jsonl" | tail -n 1     # backups.<database>.path, .sha256
DB=airflow DUMP=<backups.airflow.path>                                   # or DB=lakekeeper
sha256sum "$DUMP"                                                        # equal to the record's sha256
P=${K2_COMPOSE_PROJECT:-$(basename "$PWD")}
docker compose -p "$P" stop airflow-api-server airflow-scheduler airflow-dag-processor   # or: lakekeeper
PG=$(docker ps -q --filter label=com.docker.compose.project="$P" --filter label=com.docker.compose.service=prefect-db)
OWNER=$(echo "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$DB'" |
  docker exec -i "$PG" sh -c 'psql -U "$POSTGRES_USER" -d postgres -tA')
docker exec "$PG" sh -c 'dropdb -U "$POSTGRES_USER" --force "$1" && createdb -U "$POSTGRES_USER" -O "$2" "$1"' sh "$DB" "$OWNER"
gunzip -c "$DUMP" | docker exec -i "$PG" sh -c 'psql -U "$POSTGRES_USER" -d "$1" -q -v ON_ERROR_STOP=1' sh "$DB"
make rollback
make release-verify
```

**Measured**, the `OWNER`, `dropdb`/`createdb` and `psql` lines, 2026-11-27, on a throwaway Postgres from
compose's pinned image, a database `airflow` owned by role `airflow` dumped by `backup-lake.sh --dump`, then
changed: the restore brought back its rows and owner and removed the table added after the dump. The
`stop` line and the whole sequence on a stack were not run.

---

## 6. A rollback to a release before phase 002

**Symptom**, the release of phase 002 or 003 is to be undone, back to one cut before it, for example
`v3.0.0-alpha.3.2`.

**Detection**, manual.

**Expected behaviour**, the rollback moves what deploy moves and nothing else. From `scripts/deploy.py`,
`docker/lake/init-lake.sh` and the two compose files:

| What | After the rollback |
|---|---|
| MinIO | the older compose publishes `127.0.0.1:9000` and `9001` again, a change to a service deploy never recreates. Without `ALLOW_STATEFUL=minio` the plan refuses it inside the rollback, so `make rollback` prints `deploy: ROLLBACK FAILED: the release changes minio, which deploy never recreates (A1); ...` and exits 3 with nothing changed yet; run it again with the flag. A deploy run with `ALLOW_STATEFUL=minio` rolls itself back with the same flag |
| k2-api | in the default set now, behind profile `api` in the older release: stopped and removed (`rollback-remove-k2-api`); nothing serves `/v1`, `/catalog/` or `/dbt-docs/` |
| lake-init | not run: the older release carries no `platform/docker/lake/init-lake.sh`, and the plan says so (`not recreated: lake-init: ... not run, its warehouses already exist`) |
| MinIO users and policies, one per engine | stay: lake-init creates and updates, never deletes, and nothing of the older release removes them. Its compose gives Spark the root pair and Trino `K2_TRINO_S3_*`, which `make engine-keys` made Trino's own scoped key |
| Remote signing on the warehouses | stays off: nothing turns it back on |
| Airflow pools `lake_spark`, `fetch`, `dbt` | stay, rows in Airflow's database that no migration removes; the older compose mounts the checkout's `docker/airflow/dags`, where the factory builds no DAG (it builds only from a release), so the product DAGs leave the scheduler and their run history stays in the database |
| Volumes `k2-spool`, `k2-site` | stay with their contents: no recreate removes a volume. The site step prints `SKIP sites: <id>'s compose has no k2-site volume (a release before phase 003)` |
| Network `k2-egress` | stays, with no member: deploy creates networks and never removes one |
| Lake tables this phase created (bronze, `landing_files`, `audit_checks`, the products' bases, the run records) | stay in the catalog; the older rules name none, so no reader reaches them through Trino |

**Recovery**

```bash
make release-status
make rollback ALLOW_STATEFUL=minio
make release-verify
```

Moving forward again is an ordinary deploy of the newer release, `ALLOW_STATEFUL=minio` included, since its
MinIO definition differs once more.

**Measured**, not yet measured: no rollback across phase 002 has run on the live stack.

---

## Failure modes / incidents

- None on a live stack yet: the live stack has deployed one release (the cutover), and no rollback has run there.

---

**Last verified:** 2026-11-27 against the rehearsal, branch `step/s19-deploy`; a rollback is not yet run on the live stack (it has one deployed release, the cutover's, so there is nothing to go back to yet). The removal of a profiled service, `rollback-site` and section 6: not yet: first run after the deploy of this release.

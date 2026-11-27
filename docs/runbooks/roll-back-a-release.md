# Runbook: Roll back a release

Use this to put the previous release back on purpose, to finish a rollback that was interrupted, and to
recover when a rollback itself failed (exit 3) or a first deploy failed with nothing to go back to.
A deploy that fails verification rolls itself back; that case is section 6 of
[deploy-a-release.md](./deploy-a-release.md). Why rollback is a deploy in reverse:
[ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded**, with the same
`K2_RELEASES`, `K2_SECRETS`, `K2_COMPOSE_PROJECT` and `K2_TRINO_PORT` as the deploy
([deploy-a-release.md](./deploy-a-release.md)).

> **Target state: where these commands ran.** On 2026-11-27, against the rehearsal
> (`make deploy-rehearsal KEEP=1`), compose project `k2-rehearsal`, never the live stack, which does not
> read releases until step S22. The outputs below are the rehearsal's.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Go back to the previous good release | <2 min | 9.6 to 9.8 s, images included (2026-11-27) |
| 2 | A rollback was interrupted | <2 min | 8.3 s for the resumed rollback (2026-11-27) |
| 3 | A rollback failed (exit 3) | <15 min | not induced: the steps below are the same commands as 1 and 2 |
| 4 | A first deploy failed (exit 3, nothing to go back to) | <15 min | not induced on the rehearsal |
| 5 | A migration ran forward: restore the database's dump | <15 min | the restore commands ran on a throwaway Postgres from compose's pinned image (2026-11-27); not induced on a stack |

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
`make rollback ALLOW_STATEFUL=<service>`. A rollback runs the good release's images: never run
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

## Failure modes / incidents

- None on a live stack yet.

---

**Last verified:** 2026-11-27 against the rehearsal, branch `step/s19-deploy`; not yet on the live stack.

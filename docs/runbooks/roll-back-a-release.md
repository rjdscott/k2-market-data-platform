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
(section 2). A second `make rollback` goes back one more good release.

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
`docker compose -p ${K2_COMPOSE_PROJECT:-k2} logs --tail 50 <service>`; Trino not loading the rules:
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

## Failure modes / incidents

- None on a live stack yet.

---

**Last verified:** 2026-11-27 against the rehearsal, branch `step/s19-deploy`; not yet on the live stack.

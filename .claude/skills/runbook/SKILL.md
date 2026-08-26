---
name: runbook
description: Create or update an operational runbook in docs/runbooks/ in the K2 shape — symptom, detection (the alert that fires), expected behaviour, recovery commands, measured MTTR, last verified. Use when the user says "write a runbook", "document how to recover X", after resolving an incident worth teaching, when a new Prometheus alert needs a destination, or when a change invalidates an existing runbook's steps.
---

# runbook — record how, not why

Runbooks live in `docs/runbooks/`. Read that directory's
`README.md` first: it holds the index, the triage flow and the escalation
table. Reference material (metrics definitions, SLOs, cost model) belongs in
`docs/operations/` proper, not in a runbook.

**ADRs record why. Runbooks record how.** No decision rationale here — link
the ADR instead.

## Workflow

1. **Confirm it's a runbook.** A repeatable operational task or an incident
   recovery → runbook. A decision → `/adr`. A number → `/benchmark-report`.
   Check the index: updating an existing runbook beats a near-duplicate.
2. **One task per file**: `docs/runbooks/<slug>.md`, imperative
   title. Note at the top that every command assumes
   `set -a && . ./.env && set +a` is loaded.
3. **Structure per scenario** — all five, in order (see `template.md`):
   - **Symptom** — what the operator actually sees.
   - **Detection** — the Prometheus alert name that fires. If none does, add
     the rule in `docker/prometheus/rules/*.yml` in the same PR, or say
     explicitly that detection is manual. Never invent an alert name.
   - **Expected behaviour** — what self-heals, and why. This is what lets an
     operator tell normal recovery from a real failure.
   - **Recovery** — numbered, exact, copy-pasteable commands with expected
     output where it isn't obvious.
   - **Measured** — MTTR from an actual induced failure, with the date. Not a
     target: a measurement.
4. **Verify every command against the running stack** before writing it down.
   Induce the failure if you can (`docker compose restart <svc>`,
   `kill -STOP`, `docker network disconnect`) and record what happened. A
   runbook nobody ran is fiction, and this repo's runbooks claim measured
   MTTRs — don't dilute that.
5. **Stamp it**: `**Last verified:** YYYY-MM-DD against <commit/stack state>`.
6. **Update the index** table in `docs/runbooks/README.md`: file,
   "when to use", triggering alert. Point the alert's annotation at the
   runbook too.

## Maintenance

- A PR that invalidates a runbook's steps updates the runbook and bumps
  **Last verified** in the same PR.
- New incidents are **appended** with their date, never used to overwrite the
  previous account. The runbook is the incident's permanent home.
- Retiring a runbook (the subsystem is gone): move it to the legacy tree with
  a note, don't silently delete — the index tells readers what happened.

## Hard rules

- Exact commands, never paraphrases ("restart the broker" ✗,
  `docker compose restart redpanda` ✓).
- Container names are real (`k2-clickhouse`, `k2-redpanda`, `k2-prefect-db`).
- Destructive steps carry a reconciliation check before them.

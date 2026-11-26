# Phase 004: hardening

**Depends on:** `003` (tag `v3.0.0-beta.5`); the phase audits of both plans, whose medium and low
findings were deferred here.
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md`. Drafts resolved,
each accepted, revised or withdrawn in the pull request that builds it: ADR-051 telemetry
(#225), ADR-052 backup and ADR-053 recoverable deletes and guards (#227). The upgrades carry
ADR-010 Outcome lines where limits move.
**Delivers:** every deferred finding resolved or given a dated trigger; the off-host backup copy
and its timer; the Iceberg and Lakekeeper upgrades with dated triggers; recoverable deletes;
telemetry; Lakekeeper off the Postgres superuser; the Postgres service renamed from
`prefect-db`; the Spark base image's support date recorded as a trigger.
**Exit:** no finding of a phase audit is open without a `Resolved in <commit>` line or a dated
trigger; `make backup-verify` lists a copy at the off-host destination setting; Lakekeeper's
database role is not a superuser; no compose service is named `prefect-db`; a dropped table is
recovered within the window ADR-053 states; tag `v3.0.0-beta.6`.

## Scope

- **Deferred findings.** Every medium and low finding from the phase audits of both plans, and
  the remaining gate holes from the #228 review, resolved or given a trigger that is a date, a
  metric or an event, never "if needed". Audits are dated snapshots: a resolution is an appended
  `Resolved in <commit>` line, never a rewritten finding.
- **The off-host backup copy and its timer**, writing to a destination given as a setting; the
  owner names the destination at the release gate (owner decision: the copy stays on this host
  for the lab).
- **Upgrades with dated triggers**: Iceberg 1.12 and Lakekeeper 0.15, each either done here with
  `make gates` after it, or recorded with the date or event that will start it. The Spark base
  image's Ubuntu 22.04 standard support ends in April 2027; that is its trigger.
- **Recoverable deletes and guards** (ADR-053): a dropped table can be restored within a stated
  window, and the guards refuse a destructive call outside the platform's own tools.
- **Telemetry** (ADR-051), as that ADR is accepted.
- **Least privilege at the catalog database**: Lakekeeper connects as its own role, not the
  Postgres superuser.
- **`prefect-db` renamed**: the service, its role and database, the `PREFECT_DB_*` variables and
  every file that names them, with the volume rebuilt, since lab data is disposable.
- **Small items from the open-items ledger**: the stale test count in `README.md`.

## Verification

```bash
python3 scripts/check-findings.py               # lists no open finding without a resolution or dated trigger
make backup && make backup-verify               # lists the off-host copy
docker exec k2-postgres psql -U postgres -tAc "SELECT rolsuper FROM pg_roles WHERE rolname = 'lakekeeper'"   # f
docker compose config --services | grep -cx prefect-db                  # 0
bash scripts/chaos/drop-and-recover.sh          # the dropped table restored within the stated window
make gates
make test
make check-docs
```

`check-findings.py` reads the findings tables under `docs/audits/` for the phase audits of both
plans.

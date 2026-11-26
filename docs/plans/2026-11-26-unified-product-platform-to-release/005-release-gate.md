# Phase 005: the release gate and `v3.0.0`

**Depends on:** `004` (tag `v3.0.0-beta.6`); the owner's answers on the off-host backup
destination, the `legacy/` layout and the lakehouse mode.
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md): the platform is
a lab until this gate, and production rules apply from the `v3.0.0` tag.
**Delivers:** the whole platform rebuilt from a fresh clone on an empty engine; the restore
drill again, stopping the live stack; production mode; the governance pack, a capability
traceability file, the compliance report, a golden path per connector kind and a README tour;
a `/benchmark-report` of the release; the tag `v3.0.0`.
**Exit:** `/release-check` passes from a fresh clone; the restore drill's `make product-verify`
passes for every product; a test alert is received in Slack; the off-host copy is listed at the
destination the owner named; `make drop` refuses in production mode; branch protection on `main`
shows the required checks; `make demo` passes as the release smoke test; tag `v3.0.0`.

## Scope

- **A fresh clone from empty**: `/release-check` clones the repository to a temporary directory,
  brings the whole stack up from nothing, waits for health, runs smoke queries against every
  tier, runs `make test`, and sweeps for forbidden strings and broken links. Its time is
  published.
- **The restore drill again**, this time stopping the live stack: restore from the backup set,
  then `make product-verify` for every product; recovery time and recovery point published
  through `/benchmark-report`.
- **Production mode**: data is no longer disposable; `make drop` and `make demo-reset` refuse;
  the backup and its off-host copy are checked daily.
- **Branch protection** is already on (F23); the gate checks that its required checks include
  every CI job the release relies on.
- **Documents for people who run or adopt the platform**: the governance pack; a capability
  traceability file, one row per capability with its evidence and the command that shows it;
  the compliance report for the release, committed as a dated snapshot; a golden path per
  connector kind; a README tour from first clone to first query.
- **`/benchmark-report`** of the release: every number quoted in the README and the
  architecture chapters cites it.
- **The owner's questions**: the `legacy/` layout and the lakehouse mode, asked and recorded.

**Learning aids and documents.** Runbook: release and recover.

## Verification

First `/release-check` on a fresh clone. Then:

```bash
make restore-drill                       # stops the live stack, restores, product-verify for every product
docker exec k2-alertmanager amtool --alertmanager.url=http://localhost:9093 alert add K2TestAlert severity=info
make backup-verify                       # the off-host copy at the named destination
make drop TARGET=k2.scratch DRY_RUN=1    # refused: production mode
gh api repos/{owner}/{repo}/branches/main/protection --jq '.required_status_checks.contexts'
make demo
make test
make check-docs
```

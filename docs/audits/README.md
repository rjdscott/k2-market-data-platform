# Audits

Point-in-time sweeps of one surface: what the repo claims, against what the
stack actually does. This is a public repo, so published claims are audited
rather than trusted — the most common finding is a doc that was true two
refactors ago.

One file per audit, `YYYY-MM-DD-<surface>.md`.

## Conventions

- **Verdict first**, then scope, the audited commit, method, and the findings
  table: `severity | file:line | claim | reality | fix`.
- Severities: `BLOCKER` (misleads a user into a broken state, or a live
  correctness bug) · `HIGH` (user-visible wrong claim or foot-gun) · `MED` ·
  `LOW`. Counts by severity go in the header so audits compare across dates.
- **Verify or drop.** Every finding carries a `file:line` or a command with its
  output. A finding that can't be demonstrated does not ship.
- **Never edited after publication.** Remediation is recorded by appending
  `Resolved in <commit>` lines below the table, and by publishing a *new* dated
  audit — the re-run is the gate (zero BLOCKER/HIGH), not an edit.
- Audits cover a surface. A single diff or PR gets `/code-review` instead.
- Use the `/audit` skill.

## Index

| Date | Surface | Verdict | Findings |
|------|---------|---------|----------|
| [2026-08-26](./2026-08-26-doc-accuracy.md) | Documentation vs the running stack | Headline numbers all slightly wrong; two documents walked a new user into a broken stack | 31 — 2 BLOCKER, 8 HIGH, 13 MED, 8 LOW · resolved in `535997a` |

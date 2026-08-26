---
name: audit
description: Run a point-in-time audit of one surface and publish it to docs/audits/YYYY-MM-DD-<surface>.md — scope, method, findings table (severity | file:line | claim | reality | fix), verdict. Use when the user asks for an audit, a doc-accuracy sweep, a security review, a coverage check, or "how healthy is X" across a whole surface. Not for reviewing a single diff or PR — use /code-review for that.
---

# audit — sweep a surface, publish what you can prove

Conventions in `docs/audits/README.md` — read it first. Template in
`template.md` beside this file.

An audit covers a **surface at a point in time** and leaves a fix list. The
most valuable audit in this repo is the doc-accuracy sweep: published claims
against what the stack actually does.

## Workflow

1. **Scope contract, before any digging.** Write down: paths in scope, what is
   explicitly out, the lens (doc accuracy / correctness / security / data
   coverage / resources), and the commit — `git rev-parse --short HEAD`.
   Confirm scope with the user if it isn't already pinned.
2. **Dig against reality, not against memory.** For every claim, find the
   thing that would prove or break it:
   - doc claims a number → the command or benchmark file that produced it
   - doc names an alert → `grep -r <AlertName> docker/prometheus/rules/`
   - doc names a path → `ls` it
   - doc describes behaviour → run it against the stack
   - doc links somewhere → check the link and the anchor
3. **Verify or drop.** A finding you can't demonstrate does not ship. Re-check
   every BLOCKER and HIGH adversarially before publishing — the most common
   audit failure is a confident finding that is itself wrong.
4. **Write** `docs/audits/YYYY-MM-DD-<surface>.md`:
   - **Verdict first** — one paragraph a reader can act on without scrolling.
   - **Scope / method / commit / date.**
   - **Findings table**: `severity | file:line | claim | reality | fix`.
     Severities: `BLOCKER` (misleads a user into a broken state, or a live
     correctness bug), `HIGH` (user-visible wrong claim or foot-gun),
     `MED`, `LOW`. Don't inflate; a table of LOWs reads as noise.
   - **Counts by severity**, so the next audit can compare.
5. **Register it** in the index table in `docs/audits/README.md`.
6. **Close the loop:** a fork worth deciding → `/adr`. Multi-phase remediation
   → `/plan`. Small remediation → work the table on one branch, PR title citing
   the audit date.
7. **Re-run the same audit after remediation** as the gate (zero BLOCKER/HIGH),
   and publish it as a new dated file. Audits compare across dates; that's the
   point of dating them.

## Hard rules

- Snapshots. Record the audited commit; **never edit a published finding.**
  Append `Resolved in <commit>` lines to the row's fix cell or a Resolutions
  section below the table.
- No praise sections, no padding, no "areas of strength". Findings and fixes.
- Every finding carries `file:line` or a command with its output. Prose-only
  findings are opinions.

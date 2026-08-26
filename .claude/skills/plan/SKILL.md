---
name: plan
description: Write a phase design document in docs/plans/YYYY-MM-DD-slug.md — context, decisions, target architecture (Mermaid), phases with exit criteria and verification commands, risks. Use when the user asks to plan multi-phase work, "write a plan", "break this into phases", or when work will span more than a week. Also use to append a "Phase X landed" line when a phase completes. Does NOT create progress logs, status tables or checkbox trackers — those are forbidden in this repo.
---

# plan — write a phase design document

One file: `docs/plans/YYYY-MM-DD-<slug>.md`. Conventions in
`docs/plans/README.md` — read it first.

**A plan here is a design document, not a tracker.** K2 forbids committed
progress logs, status tables, handoffs and phase-status files: they rot, and a
stale tracker is worse than none. Status lives in ADR statuses, git tags and
CI. If you feel the urge to add a checkbox table, don't.

## Workflow

1. **Ground it.** A plan executes decisions already made. Check
   `docs/decisions/` for what it builds on. If a major fork is still open,
   resolve it with `/adr` before planning around a guess. If the plan itself
   settles forks, list them under Decisions and open the ADRs as the phases land.
2. **Gather ground truth first.** Read the code and run the commands. A plan
   built on assumed file paths, image tags or API shapes fails in week two.
   Record what you verified in a **Ground truth** section with file:line
   citations, so implementation doesn't re-derive it.
3. **Write the document**, these sections in order:
   - **Context** — where the repo is, what's wrong, what the user decided.
   - **Decisions** — user-confirmed choices, one line each, marked as such.
   - **Ground truth** — verified facts with citations.
   - **Target architecture** — a Mermaid diagram plus the principles it encodes.
   - **Phase A…N** — one PR-sized, independently verifiable slice each,
     ordered by dependency then risk (riskiest assumptions surface earliest).
     Every phase ends with **Exit:** concrete criteria, and the exact
     verification commands. 3–8 phases; more means it's two plans.
   - **Verification (end-to-end)** — the commands that prove the whole thing.
   - **Risks / verify-first** — unknowns that must be spiked before code, each
     with its escape hatch.
4. **Register it** in the index table in `docs/plans/README.md`.
5. **Confirm with the user** before execution begins.

## When a phase completes

Append exactly one dated line to that phase's section — nothing else:

```
_Phase C landed 2026-09-14 — commit a1b2c3d, tag v3.0.0-capture._
```

No checkboxes, no progress log, no status column. If the phase's shape changed,
edit the phase description in place and say so in the landed line. Never
renumber phases.

## Hard rules

- Verification commands are exact and runnable, not paraphrases.
- Plans cite ADRs and benchmarks; they never re-litigate them.
- A phase without exit criteria is a wish. Write the criteria or drop the phase.
- Mid-plan decision between real alternatives → `/adr` before proceeding.

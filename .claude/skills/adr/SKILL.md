---
name: adr
description: Record an architecture decision in docs/decisions/ using the K2 ADR template (ADR-NNN-kebab-title.md, immutable once Accepted, Outcome appended on divergence). Use when a fork between technologies/patterns/schemas is being decided, a decision has just been made in conversation, an obvious option is being deliberately rejected, a prior ADR is being reversed, or the user says "write an ADR", "record this decision", "capture this choice". Also use when a plan or audit hits a decision fork.
---

# adr — record an architecture decision

One ADR per decision, in `docs/decisions/`. Read `docs/decisions/README.md`
first — it holds the conventions and the index you must update.

## Workflow

1. **Confirm it deserves an ADR.** The test is cost of reversal: **more than a
   day to unwind** → ADR. One sane option, or a cheap-to-reverse detail → say
   so and stop. A corpus padded with trivia is a corpus nobody reads.
2. **Number it.** `ls docs/decisions/ADR-*.md | tail -1` → next number, zero
   padded to 3. Never reuse, never renumber, including for rejected ADRs.
   Slug states the decision: `ADR-019-rust-capture-tier.md`, not
   `ADR-019-capture.md`.
3. **Draft from `template.md`** in this skill directory. Rules:
   - **Context** — forces and constraints only, no solutions. Include the
     numbers that made it a problem (with their source). A stranger must feel
     the tension.
   - **Decision** — one bold sentence: "We will X, because Y." Scope it
     ("for the lake only", "until the third exchange").
   - **Rationale** — why this over the alternatives. Measurements beat opinion.
   - **Alternatives Considered** — 2–4 real options, including the popular one
     you rejected, one line of why each lost.
   - **Consequences** — what gets easier, harder, committed, risked, and a
     **concrete revisit trigger**: a metric, a date, or an event. Never
     "if needed".
   - One to two pages. Cite plans, benchmarks and runbooks; don't restate them.
4. **Status line** in the header block:
   - Decided by the user in conversation → `Accepted`
   - Built already → `Accepted — Implemented`
   - Proposing → `Proposed`; the user flips it
   - Reversed → the *new* ADR references the old; edit the old one's status to
     `Superseded by [ADR-NNN](ADR-NNN-slug.md)`. **That line is the only
     permitted edit to an Accepted ADR.**
5. **Update the index table** in `docs/decisions/README.md` — the right table
   (design vs implementation decisions), with ADR number, title, status and a
   one-line Outcome cell. Keep the supersession chain readable.
6. **Report** the file path and the one-line decision. Land the ADR in the same
   PR as the work it governs.

## Outcome sections

When an implementation diverges from an accepted ADR, **append** a
`## Outcome (YYYY-MM)` section: what was actually built, why the original
reasoning failed, and what being wrong cost (CPU/GB/hours — with provenance).
Never rewrite the body to look prescient. ADR-008's Outcome is the reference
example: half the decision held, half was reversed, both are on the record.

## Hard rules

- One decision per ADR. Two decisions → two ADRs, cross-linked.
- Never edit the body of an Accepted ADR. Supersede or append an Outcome.
- ADRs record *why*. Runbooks record *how*. Benchmarks record *what it measured*.
  Don't let the ADR absorb either.

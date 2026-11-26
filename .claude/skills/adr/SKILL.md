---
name: adr
description: Record an architecture decision in docs/adr/ using the K2 ADR template (ADR-NNN-kebab-title.md, immutable once Accepted, Outcome appended on divergence). Use when a fork between technologies/patterns/schemas is being decided, a decision has just been made in conversation, an obvious option is being deliberately rejected, a prior ADR is being reversed, or the user says "write an ADR", "record this decision", "capture this choice". Also use when a plan or audit hits a decision fork.
---

# adr — record an architecture decision

One ADR per decision, in `docs/adr/`. Read `docs/adr/README.md`
first (it holds the conventions and the index you must update), and keep
`docs/adr/glossary.md` open: it holds the terms you may link instead of re-explaining.

**The rule (owner, 2026-11-26):** technical is welcome, it is a learning
opportunity; what matters is that the ADR is understandable within the ADR
itself. The test is whether a reader who has not opened the code can follow it
and learn from it.

## Workflow

1. **Confirm it deserves an ADR.** The test is cost of reversal: **more than a
   day to unwind** → ADR. One sane option, or a cheap-to-reverse detail → say
   so and stop. A corpus padded with trivia is a corpus nobody reads.
2. **Number it.** `ls docs/adr/ADR-*.md | tail -1` → next number, zero
   padded to 3. Never reuse, never renumber, including for rejected ADRs.
   Slug states the decision: `ADR-019-rust-capture-tier.md`, not
   `ADR-019-capture.md`.
3. **Draft from `template.md`** in this skill directory. Rules:
   - **Title**: short, plain, and stating the decision (under about ten words,
     no internal labels such as stage numbers, exit codes or table names).
   - **In plain words**: required, right under the header block. At most six
     short sentences on the problem, what we will do, what it costs, and what
     the reader must decide.
   - **Every term explained where it first appears.** Each internal label
     ("stage 2d"), tool concept (snapshot, selector, definer view) and mechanism
     is explained in place or linked to `docs/adr/glossary.md`. Depth is
     welcome; an undefined label is not.
   - **Context** — forces and constraints only, no solutions. Include the
     numbers that made it a problem (with their source). A stranger must feel
     the tension.
   - **Decision**: one bold sentence first: "We will X, because Y." Scope it
     ("for the lake only", "until the third exchange"). Then the detail.
   - **Rationale** — why this over the alternatives. Measurements beat opinion.
   - **Alternatives Considered** — 2–4 real options, including the popular one
     you rejected, one line of why each lost.
   - **Consequences** — what gets easier, harder, committed, risked, and a
     **concrete revisit trigger**: a metric, a date, or an event. Never
     "if needed". Include a **What is not yet verified** line: each claim no
     command has demonstrated yet.
   - As long as the argument needs, in short sections. Cite plans, benchmarks
     and runbooks; don't restate them.
4. **Status line** in the header block:
   - Decided by the user in conversation → `Accepted`
   - Built already → `Accepted — Implemented`
   - Proposing → `Proposed`; the user flips it
   - Reversed → the *new* ADR references the old; edit the old one's status to
     `Superseded by [ADR-NNN](ADR-NNN-slug.md)`. **That line is the only
     permitted edit to an Accepted ADR.**
5. **Update the index table** in `docs/adr/README.md` — the right table
   (design vs implementation decisions), with ADR number, title, status and a
   one-line Outcome cell. Keep the supersession chain readable.
6. **Report** the file path and the one-line decision. Land the ADR in the same
   PR as the work it governs.
7. **Read it as a newcomer**, last, before the PR: any term you did not define,
   define or link. New terms go into `docs/adr/glossary.md` in the same pull
   request.

## Outcome sections

When an implementation diverges from an accepted ADR, **append** a
`## Outcome (YYYY-MM)` section: what was actually built, why the original
reasoning failed, and what being wrong cost (CPU/GB/hours — with provenance).
Never rewrite the body to look prescient. ADR-008's Outcome is the reference
example: half the decision held, half was reversed, both are on the record.

## Hard rules

- One decision per ADR. Two decisions → two ADRs, cross-linked.
- Every new ADR has `## In plain words` under its header block, and a plain
  title that states the decision.
- No internal label, tool concept or mechanism without its explanation in
  place or a link to `docs/adr/glossary.md`. New terms go into
  `docs/adr/glossary.md` in the same pull request.
- Consequences carry a "What is not yet verified" line.
- Never edit the body of an Accepted ADR. Supersede or append an Outcome.
- ADRs record *why*. Runbooks record *how*. Benchmarks record *what it measured*.
  Don't let the ADR absorb either.

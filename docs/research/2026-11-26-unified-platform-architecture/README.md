# Unified platform architecture tournament: retained inputs (2026-11-26)

The full material behind [`../2026-11-26-unified-platform-architecture.md`](../2026-11-26-unified-platform-architecture.md),
which is the readable summary: start there. These files are a dated research record, not decisions.
Nothing in them is accepted until an ADR says so.

| File | What it is | Written by |
|---|---|---|
| [`BRIEF.md`](BRIEF.md) | The task given to the three architects: the owner's vision, ten fixed decisions, what to deliver | The agent that ran the tournament, from the owner's requirements |
| [`ADDENDUM-1.md`](ADDENDUM-1.md) to [`ADDENDUM-5.md`](ADDENDUM-5.md) | The owner's requirements as they changed during the day; a later addendum overrides an earlier one. Addendum 5 arrived after the entries were written and applies to the judge and reviewer only | The same agent, from the owner's requirements |
| [`JUDGING-CRITERIA.md`](JUDGING-CRITERIA.md) | The five criteria, in order, every design and added technology is scored against | The same agent, from the owner's requirements |
| [`entry-A.md`](entry-A.md), [`entry-B.md`](entry-B.md), [`entry-C.md`](entry-C.md) | Three competing architectures and plans, each about 1,200 lines | One independent agent per entry; none saw the others |
| [`CLICKHOUSE-INVENTORY.md`](CLICKHOUSE-INVENTORY.md) | Everything that reads or depends on ClickHouse, for planning its pause | An independent agent, read-only |
| [`PR239-REVIEW.md`](PR239-REVIEW.md) | A review of the in-flight file-source pull request, and where it is file-specific | An independent reviewer |
| [`VERDICT.md`](VERDICT.md) | The judge's scoring, fact checks and synthesis: architecture, acceptance cases, plan, decisions | An independent judge, who wrote no entry |
| [`REVIEW.md`](REVIEW.md) | An adversarial review of the verdict: findings by severity, a fact re-check, the dbt alternative | An independent reviewer, who wrote neither the entries nor the verdict |

**Reading order.** The summary; then `BRIEF.md`, the five addenda and `JUDGING-CRITERIA.md`; then
the entries in any order; then `VERDICT.md`; then `REVIEW.md`. The inventory and the PR #239 review
are supporting inputs to the judge and can be read when the verdict cites them.

**What was edited after the tournament.** The brief, the addenda and the criteria quoted the owner's
chat messages; those passages are restated as plain statements with the same meaning, and each file
says so in its first line. Absolute paths became repository-relative. The entries, the verdict and
the review are unedited. The verdict and the review cite a `LEDGER.md`, a working list of open items
given to the judge and the reviewer; it is not retained, because the repository does not keep
status trackers, and `VERDICT.md` section 6.6 assigns each of its items a phase.

**Snapshots.** Repository claims were read at `main` `bb6e77f` (entries, judge) and `43cd971`
(reviewer), with the in-flight branches named in each file. Line numbers have drifted since.

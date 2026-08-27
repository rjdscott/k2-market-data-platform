# 00 — Start here

> **You will learn** how this book is organised and where to jump in.
> **Read this if** you are new to the repo.
> **Before this** nothing.

`docs/architecture/` is written as a book: numbered chapters in the order a new joiner
should meet them, each one openable on its own. Every chapter starts with three lines —
what you will learn, who should read it, what to read first — so you can skip.

**The journey.** Chapter 01 says what the platform is for and what it refuses to be.
Chapters 02–03 explain the market-data and data-engineering ideas the rest assumes.
04 is the whole system on one page. 05–11 walk the data path in order: venue socket,
capture process, wire contracts, lake ingest, lake layers, served tier, observability.
12–17 are the design references those chapters cite: strategy, schema, partitioning,
capacity, failure modes, scale-out. A1 is the versions table.

**Three reading paths.**

| You are | Read |
|---|---|
| New to the platform | 01 → 04 → 05 → 08 → 09 → 10, then 02–03 when a term bites |
| Reviewing the engineering | 04 → 08 → 10 → 16, then the ADRs each chapter cites |
| On call | 11 → 16 → [`../runbooks/`](../runbooks/README.md) |

**Conventions.** Every number cites [`../benchmarks/`](../benchmarks/README.md); every
decision cites [`../adr/`](../adr/README.md); every practice names the file, test or alert
that enforces it. Diagrams are Mermaid, top to bottom. If a page and the code disagree,
the code is right and the page has a bug — open a PR.

# ADR-033: Data mesh, four domains, and a platform team that owns the paved road

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Ownership

---

## Context

Nothing in the repository names an owner for a table
([ADR-031](ADR-031-governed-self-serve-platform.md), Context). The 25 lake tables outside
`audit` were built by one maintainer in one sequence, raw to gold, and the only grouping they
have is the medallion layer. A second team arriving with a product would find no answer to
three questions: where its files go, who reviews a change to something it reads, and which
parts of the stack are its job and which are the platform's.

[ADR-032](ADR-032-odcs-contracts-and-k2gen.md) gives every table a contract inside a product,
so every product needs an owner the contract can name. The plan adds a fourth kind of data in
P4, FX reference rates, from a source with nothing in common with the venue feeds, and dbt
marts in P3 that analysts change more often than the ingest.

Two facts constrain the answer. The repository has one GitHub account, and `main` is
unprotected by choice until a second contributor arrives ([CLAUDE.md](../../CLAUDE.md)), so
no ownership rule can be enforced by review today. And the layer boundaries are load-bearing
for other reasons ([ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)) and
are not changed here.

---

## Decision

**We will group every data product into one of four domains, each owned by one named team,
and give a platform team the generator, the schemas, orchestration, the query engines, the
catalog and the controls, because an owner that is not declared per product cannot be
reviewed, routed or checked.**

| Domain | Kind | Owner | Products today |
|---|---|---|---|
| `market-capture` | source-aligned | `capture-team` | `raw-archive`, `venue-frames`, `venue-trades`, `venue-books` |
| `reference-data` | source-aligned | `reference-data-team` | `security-master` |
| `fx-reference` | source-aligned | `fx-reference-team` | none until P4 |
| `market-analytics` | consumer-aligned | `analytics-team` | `canonical-trades`, `candles`, `event-bars`, `book-snapshots` |

- The registry is `contracts/domains.yaml`; a domain owns `contracts/<domain>/`, and
  `.github/CODEOWNERS` has one line per domain plus the platform's paths. A domain with no
  product yet is declared there with its owner and no directory, which is how `fx-reference`
  exists before P4.
- `k2gen validate` enforces the registry: a product lives under its domain's directory, names
  a declared domain, and has that domain's owner and kind.
- Placement follows who can answer for the data, not its layer. `gold.book_state` is
  `market-capture`'s: it is the book replay's carry-over between runs, not a research
  product. `gold.dim_instrument` and `gold.dim_venue` are `reference-data`'s, with
  `config/instruments.yaml` declared as their external input rather than given a contract.
  `gold.trades` is `market-analytics`'s: it is the cross-venue product a researcher reads
  first, derived from the three per-venue silver tables.
- The teams are roles. Every one maps to the single account today, and CODEOWNERS says so
  in a comment per line rather than inventing handles.

---

## Rationale

- **Ownership follows the question a consumer asks.** "Is this what the venue sent?" is
  answered by whoever runs capture; "is this candle right?" by whoever owns the aggregation.
  The four domains are the four places those questions land.
- **Source-aligned and consumer-aligned are kept apart** because they change for different
  reasons: a venue changing its frame format moves `market-capture`, a research need moves
  `market-analytics`. Putting both in one domain would route every change to one owner.
- **The platform owns the road, not the products.** The generator, the schemas and the
  controls are what every domain depends on; one owner for them means one place to change
  the rules and every domain sees the same ones.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| One domain per layer: raw, bronze, silver, gold | Layers are a storage convention. The security master and the candles are both gold and have nothing else in common |
| One domain per venue | Splits every cross-venue product across three owners, and the venue count would set the team count |
| One domain for everything | No boundary to route a review across, which is the status quo this replaces |
| Owners per table, no products or domains | 25 owners for what is four bodies of work, and nothing to group a new team's tables under |

---

## Consequences

**Easier:** a new team knows its directory and its reviewer; a change to a product's inputs
shows which other domain it touches; the catalog in P5 loads domains and owners from one file.

**Harder:** four teams that are one person. CODEOWNERS routes no review while every line names
the same account, so the boundaries are declared and checked by `k2gen`, and not yet tested
by a second team using them. Moving a product between domains is a pull request that moves
its directory and changes its owner.

**Committed to:** `contracts/domains.yaml` as the domain registry; every product in exactly one
domain; the platform team as the owner of `platform/k2gen/`, `contracts/schema/` and the
registry itself.

**Risks:** boundaries drawn by one person may be wrong for the first real second team, and
nothing here can show they are right until one arrives. `fx-reference` is a declared domain
with no product, which is a claim about P4 until P4 lands.

**Revisit when:** a second GitHub account becomes a contributor (CODEOWNERS gets real owners
and branch protection turns on, the trigger [CLAUDE.md](../../CLAUDE.md) already names); or a
product's inputs cross domains in both directions, which would make two domains one; or P4
ends without an `fx-reference` product.

---

## References

- Plan: [2026-11-24-governed-self-serve-platform](../plans/2026-11-24-governed-self-serve-platform/README.md), phase [P1](../plans/2026-11-24-governed-self-serve-platform/001-contracts-generator-mesh.md)
- [ADR-031](ADR-031-governed-self-serve-platform.md), the umbrella that reserved this number
- [ADR-032](ADR-032-odcs-contracts-and-k2gen.md), the contracts each domain owns
- [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md), the layers, unchanged

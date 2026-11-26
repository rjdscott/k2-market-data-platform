# Final decisions for the unified data product platform (owner, 2026-11-26 UTC)

This file is the authority for the documentation update. Where it differs from `ARCHITECTURE-V2.md`,
`FULL-PLAN-BRIEF.md` or any earlier file, THIS FILE WINS. It records decisions; the reasons are in
the files it cites. Dates in repository files are UTC: today is 2026-11-26.

## 1. What is accepted
- **Architecture:** `ARCHITECTURE-V2.md` (in this directory) is accepted as the target
  architecture, amended by sections 2 to 4 below. It is a domain-agnostic data product platform:
  unified ingestion, storage, catalog and service layers; market data is the first tenant; value
  comes from governed joins across domains.
- **Scope:** the FULL plan of v2 section 6, nothing cut, each thing built once and properly.
  A cut-down "plan to the demo" was written and WITHDRAWN: never describe it as a plan.
- **Plan review:** `FULL-PLAN-REVIEW.md` returned "go, with changes"; every critical and high
  finding is accepted (section 3).
- **Build breakdown for the first phases:** `IMPLEMENTATION-N0-N1.md` (steps, waves, interfaces).

## 2. Owner decisions on v2's open items
| Item | Decision |
|---|---|
| O1a roles | At most one role per principal per product, on any number of products |
| O1b catalog | A generated, read-only static catalog site at the demo tag and at 3.0 (search; per product: schema, owner, state, quality, lineage diagram, who can read, example queries, join keys; links to the dbt docs site). No off-the-shelf catalog tool |
| O1c FX content | OPEN; needed by the enriched-product phase |
| O1d layers | A product has only the layers that add a distinct guarantee |
| O2 connector order | Settled by the phase order in section 3 |
| O3 identity | Passwords over TLS: Trino's password login over encrypted connections; the REST API uses keys and acts as the caller. Decided now and BUILT EARLY (in the first spine phase), because every client depends on it. Single sign-on is not planned for 3.0 |
| O4 destinations | Alerts are delivered to a Slack channel through an incoming webhook kept as a local secret. The backup copy stays on this host for the lab; revisited at the release gate |
| O5 infrastructure as code | This host only, fully proven: declared and checkable, one command shows drift, one converges. A cloud deployment is its own plan after 3.0 |
| O6 landing retention | OPEN; needed by the Kraken phase |
| O7 REST in slice 1 | Yes: a minimal REST data route in the ECB slice |
| O8 test window | Readers see ONLY TESTED DATA, always: interactive SQL readers are pointed at a marker that moves only after a build's tests pass (v2 option b). A small spike and about a day in the first spine phase |

## 3. The plan: order, phases, and the review's changes
**Order (the only change to v2's order):** N0, N1 (split in three), N2, N5, N6, N7, then the demo
tag, then N3, N4, N8, N9, N10, N11 and `v3.0.0`. After 3.0: the definition registry (database,
API, then UI, as the source of truth with approvals and history) and the AI interface.

**Two plan documents** (the house format allows 3 to 8 phases per plan):
- `docs/plans/2026-11-26-unified-product-platform/`: to the demo tag. Phases, in this order, files
  numbered `000` to `007`: N0 clear the ground; N1a the release and rules spine; N1b the run spine
  through ECB FX; N1c serving and teaching; N2 Kraken trades, instruments, security master;
  N5 the enriched product; N6 catalog, metadata, lineage, usage; N7 REST API complete, then demo
  readiness and the tag `v3.0.0-beta.1`.
- `docs/plans/2026-11-26-unified-product-platform-to-release/`: after the demo tag. Files `000` to
  `005`: N3 remaining connector kinds; N4 widen crypto; N8 controls, alerts, release engineering;
  N9 infrastructure as code (this host); N10 hardening; N11 release gate and `v3.0.0`.
- Both supersede phases P4 to P7 of `docs/plans/2026-11-24-governed-self-serve-platform/`, whose
  files stay untouched as dated records; only the plans index says so.
- Phase names in prose: use the descriptive names above; the codes N0 to N11 may appear once in a
  mapping table back to v2.

**The review's changes, all accepted:**
1. The old pipeline (`lake_ingest`, the old `raw`, `bronze`, `silver`, `gold` namespaces, its stage
   code) is retired at the END OF N0: Spark image rebased and proven by `make gates`; backup;
   pause; drop; stage code archived to `legacy/` with a README; readers of the old table names are
   moved or retired in the same phase, not later. Cost, to be stated plainly: no live crypto data
   and stale dashboards until the Kraken phase lands.
2. One INTERFACES pull request is merged before any parallel building: release layout and manifest;
   document set and checks interface; schemas; connector config and parser interface; naming of
   catalogs, writers, namespaces; the run-record table; the `k2_input` macro and the dbt wrapper's
   contract; access rule and expectation formats; REST routes (OpenAPI); the catalog site's input;
   the backup set; and a split of the shared files many workers would otherwise edit.
3. N1 is three phases with their own exits and tags: (a) release and rules spine: deploy from an
   immutable release, checks, catalogs, policies, access expectations, identity, alerts wiring it
   needs; (b) run spine through ECB FX: fetch, connector, landing, bronze, the dbt wrapper,
   tested-data marker, scoped storage keys, the ECB products; (c) serving and teaching: REST,
   catalog site first version, dbt labs, the owner's guide, docs.
4. A deploy recreates only what changed; captures and unrelated services are never restarted by it.
5. Image digests are pinned in the release from the first spine phase, so a rollback restores
   images as well as configuration.
6. Everything a phase builds has a command that proves it in that phase's exit.
7. The file connector's redirect fix is proven when the connector lands, not later.
8. `make demo` (reset to a known state, then only real commands; doubles as the release smoke
   test) and the reset needed for the live-onboarding moment are built inside the plan.
9. Already answered by spikes, so struck from N0: per-product run cost, dbt merge and rollback
   through the catalog, masks and filters on Iceberg (`SPIKE-*.md`). Remaining N0 spikes are those
   `IMPLEMENTATION-N0-N1.md` lists.

**The demo is only a tag** (`v3.0.0-beta.1`) on that plan, placed when four moments can be shown on
the real system: (a) onboard a product live from a prepared branch: a third, small product from a
new domain (a public economic series from a public API); pull request, checks, merge, deploy, data
arrives, catalog page appears; (b) find, trust, query: catalog page, then SQL and REST giving the
same answer; (c) permissions in action with two roles (delayed rows, a masked column); (d) bad data
stopped (a corrupt file refused, a failing test rolled back) and an enriched cross-domain product
with lineage. No demo-only code. Live on this host with a scripted walkthrough; screenshots and a
recorded run as fallback. Audience: engineers and potential users.

**Rules:** if late, the date moves; quality and order do not. No calendar date is promised until
the first spine phase gives a measured pace. One review round per pull request; merge only on
green CI; a phase audit fixing critical and high; a pre-release tag per phase. The platform is a
lab until the release gate: data is disposable, backup first, then drop; from the `v3.0.0` tag
production rules apply.

## 4. Standing requirements every phase carries
- Metadata is a first-class output of every step; open standards wherever a mature one exists
  (v2 section 4.12); nothing in 3.0 blocks a later AI interface.
- The security master: each venue has a trades product and an instruments product; instruments
  come from the venue's public reference API; two levels (asset, instrument) with venue symbols
  mapped, by rule plus a reviewed exceptions list.
- dbt-core (never Fusion) on Trino builds silver, gold and marts; ECB FX is the dbt worked example;
  dbt labs, an owner's guide to reviewing dbt work, the dbt docs site, and a "what this does and
  why" section in each dbt pull request.
- Each phase ships its runbooks (every command run before it is written), its architecture
  chapter with Mermaid diagrams, and updates the current-state documents in the same pull request
  that makes a change real.
- Technology: best practice, robust, easy to manage and use, modern yet tried and tested.

## 5. Names fixed for this documentation update (so separate writers link to the same things)
- ADR: `docs/adr/ADR-056-unified-data-product-platform.md`, status `Accepted`, decided by the
  owner on 2026-11-26. It supersedes the remaining plan of ADR-031 (phases P4 to P7); ADR-031's
  status line gains the pointer. ADR numbers 042 and 051 to 055 are held by open drafts; ADR-036
  (file connector, in PR #239) and ADR-055 (product definition, in PR #240) are Proposed and will
  be revised and accepted in the first spine phase.
- Architecture chapter: `docs/architecture/20-target-data-product-platform.md`, clearly labelled
  as the TARGET, not what runs today.
- Research: `docs/research/2026-11-26-unified-platform-architecture.md` and its directory of
  retained inputs (PR #244, branch `docs/research-architecture-tournament`).
- Plans: the two directories named in section 3.

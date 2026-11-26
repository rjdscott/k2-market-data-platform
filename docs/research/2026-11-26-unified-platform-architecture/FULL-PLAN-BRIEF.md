# The full plan: what is being reviewed (owner's decisions, 2026-11-26)

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

The owner accepted `ARCHITECTURE-V2.md` as the basis and decided to build its FULL plan (section 6),
nothing cut, each thing built once and properly. An earlier cut-down plan to the demo was
withdrawn because simple-now, proper-later pairs are a rework trap; do not propose one.

## Fixed
1. **Scope: everything in v2 section 6**, phases N0 to N11, then the definition registry and the AI
   interface after 3.0.
2. **Order, the only change to v2:** N0, N1, N2, N5 (enriched product), N6 (catalog, metadata,
   lineage, usage, identity), N7 (REST complete), then a demo tag, then N3 (remaining connectors),
   N4 (Binance and Coinbase), N8, N9, N10, N11 and `v3.0.0`.
3. **The demo is only a tag** (`v3.0.0-beta.1`) placed when four moments can be shown on the real
   system: (a) onboard a product live in a pull request; (b) find, trust, query: catalog page, then
   SQL and REST giving the same answer; (c) permissions in action with two roles (delayed rows, a
   masked column); (d) bad data stopped (a corrupt file refused, a failing test rolled back) and an
   enriched product with lineage across domains. No demo-only code. `make demo` only resets to a
   known state and runs real commands; it doubles as the release smoke test. Live on this host
   with a scripted walkthrough, plus screenshots and a recorded run as fallback. Audience:
   engineers and potential users.
4. **Catalog at the demo and at 3.0:** a generated, read-only static site (search; per product:
   schema, owner, state, quality, lineage diagram, who can read, example queries, join keys;
   links to the dbt docs site). This answers v2's open decision O1b.
5. **If late, the date moves; quality and order do not.** No calendar date is promised until N1
   gives a measured pace.
6. **Process:** one review round per pull request; merge only on green CI; a phase audit fixing
   critical and high; a pre-release tag per phase; a Slack update at each phase boundary. The
   orchestrator runs through without asking, except for decisions that are the owner's or a
   destructive action outside the plan. The platform is a lab until the release gate: data is
   disposable, backup first then drop.
7. **Other owner decisions since v2:** alerts go to a Slack channel by webhook (O4); the backup
   copy stays on this host for the lab, revisited at the gate (O4); a minimal REST data route in
   slice 1, yes (O7). Still open for the owner: O1a, O1c, O1d, O2 (now largely settled by the order
   above), O3 identity, O5 infrastructure-as-code scope, O6 landing retention, O8.
8. **How the work is built:** an orchestrating model plans, reviews and merges; independent worker
   agents (two capability tiers) each build one pull-request-sized piece in its own git worktree,
   several in parallel. Real parallelism (disjoint files, an interface agreed first) is valuable;
   parallel work on shared files causes drift and merge conflicts.

## Measured after v2 was written (see SPIKE-DBT.md, SPIKE-ACCESS.md, SPIKE-RUNCOST.md)
- dbt-core 1.12.7 on Trino through Lakekeeper: incremental merge, schema change, enforced model
  contract, snapshots and publish-test-rollback all work; one model with two tests builds in 3 to 4 s.
- Trino 484 masks and row filters hold on Iceberg tables provided readers use a read-only catalog,
  Iceberg metadata tables are denied to them, and no owner-rights view sits over restricted data.
  Rules reload only on restart unless a refresh period is set.
- One small product run on Spark: 6.5 s (10.5 s on a table with 200 snapshots).

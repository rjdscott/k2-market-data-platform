# Addendum 5 (owner, 2026-11-26, after the three entries were written). For the judge and reviewer.

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

The platform is a lab until its release gate: its data is disposable during the build and must be
treated as business-critical once the build is finished.

P. **Standing permission to drop and rebuild**, when the accepted plan calls for it, without asking
   each time: every lake table (bronze, silver, gold, marts), the raw archive (`raw.messages`),
   catalog warehouses and namespaces, storage buckets, ClickHouse data, Redpanda topics and their
   layout, Airflow run history. The owner's guide is best practice: naming, layout,
   partitioning and table design may be redone to the new product model instead of inherited.
Q. **Crypto migration is a clean rebuild**, not a side-by-side move. Build the per-venue products
   fresh on the new framework; prove correctness against independent references (the DuckDB
   recomputation in the parity scripts, synthetic fixtures with known answers, replay tests), not
   by matching the old tables; then switch the old pipeline off and drop its tables. This replaces
   addendum 2 item H's "old and new run side by side until they agree". The three entries were
   written under the old rule: where an entry's migration is slower or more complex only because
   of it, simplify, and say what proof replaces table-to-table parity. Note that the venues do
   not replay history, so say what is captured again from the stream's retained window and what
   history is simply let go.
R. **Critical from the 3.0.0 release gate.** The gate itself includes a clean rebuild from empty
   and a restore drill; from the tag onward, production rules apply (no drop without the owner's
   approval, backup and restore proven, change control). The plan must show the controls that
   make that true are built before the gate, not after.
S. **Before any drop**: a backup copy first (the existing `scripts/backup-lake.sh`), confirmed
   complete, then the drop.
T. **Priority: the platform, not its contents.** The data is for demonstration. Spend effort on the
   platform's capabilities (defining, onboarding, governing, cataloguing and serving a product) and
   on the machinery that proves correctness, not on preserving, back-filling or migrating existing
   data. History may simply be let go. A small, clean, well-understood demonstration data set that
   exercises every capability is worth more than a large inherited one. Keep the correctness
   proofs (fixtures with known answers, independent recomputation, audits) because they
   demonstrate the platform; drop work whose only purpose is to keep old data.
U. **Greenfield is allowed for the product path.** The owner is willing to rebuild from scratch to
   the best practice design, and states what matters most: the platform is about data products and
   services end to end, with unified ingestion, a unified data catalogue, and a unified query
   layer with permissioning. So the ingestion framework, the table and catalog layout, the product
   model, permissions, catalog and service layer may be built new instead of adapted from the
   existing job; the existing Spark ingest and its stage modules may retire to `legacy/` once the
   new path passes its proofs. This does NOT mean replacing proven substrate for its own sake:
   keep the engines already pinned and running, the standalone captures, the contracts generator,
   the CI and docs gates, backup and the ADR and runbook discipline, unless you can show a
   specific reason to replace one.
V. **Orchestrator's recommendation, for you to weigh and accept or refute with reasons** (not an
   owner decision yet): order the plan as vertical slices, not layer by layer. First one thin
   slice: a single simple product (ECB end-of-day FX) through every layer end to end (contract,
   connector, medallion transforms, catalog entry, permission, SQL endpoint, a minimal REST
   endpoint), in days. Second slice: one venue's trades from Redpanda. Then widen. The stated
   risk it answers is second-system scope growth.

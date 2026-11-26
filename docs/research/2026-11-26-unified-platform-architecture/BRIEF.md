# Architecture tournament brief: K2 as a unified, product- and service-oriented data platform

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

You are one of three independent architects. Each writes a competing target architecture and a
phased plan. A fourth, independent judge picks or synthesises. Do not look for the other entries.

## The owner's vision
The owner wants the K2 market data platform to be product- and service-oriented, with a unified
ingestion layer, unified storage layers, a unified catalog, and a unified service (query) layer.
The platform should act as an aggregator of unified ingestion, cataloguing and serving of data,
where end users can easily define, create and serve data sets from various sources. The owner notes
that today's platform has rigidly coupled pipelines and layering.

## Fixed decisions (owner, 2026-11-26). Honour all; do not re-litigate.
1. A product is defined once, in a YAML contract in a pull request (ODCS contracts, generator
   `k2gen`, CI). A catalog UI later shows products read-only. No form or app writes contracts.
2. One first-class lifecycle state per product (for example active, paused, deprecated, retired)
   that every layer obeys: capture, ingest, audits, alerts, catalog, downstream flags.
3. Source kinds to support, scope kept open: scheduled files (HTTP, S3), public APIs, SFTP and FTP,
   live venue streams, API and database pulls, internal lake tables and marts, derived SQL products.
4. The existing stream pipeline (Rust capture, Redpanda, one Spark job with venues hard-coded)
   works and passes parity. It is DECLARED in contracts now and obeys them; it is REGENERATED from
   contracts only later, as a separate project. No rewrite now.
5. Service layer: SQL through one endpoint, and a REST / data API. The API is designed now and
   built as its own phase after the catalog exists, before the final release gate.
6. Audience: a reference architecture for many teams (domains, owners, access rules, per-product
   lifecycle), running on one host within the resource ceiling.
7. ClickHouse (the hot store fed from the stream) is PAUSED: kept in the compose file behind an
   opt-in profile, data volume kept, its checks stood down explicitly and visibly; it returns later
   as a declared serving product rebuilt from the lake. Your plan says in which phase the pause
   happens and everything that reads ClickHouse today (find it all in the repo).
8. Order-book capture is already paused (ADR-050). Do not plan around resuming it.
9. A NEW plan supersedes phases P4 to P7 of `docs/plans/2026-11-24-governed-self-serve-platform/`.
   Old phase files stay as dated records. Keep those phases' goals unless your architecture makes
   one obsolete, and say which. The version `3.0.0` is tagged at the final release gate.
10. Process rules: one review round per pull request; a phase audit fixes critical and high only;
    a hardening sprint exists after the infrastructure-as-code phase for deferred items.

## Ground truth to read (repo at `.`, the repository root, READ-ONLY:
## no edits, no commits, no branch switches, no Docker writes; reading the running stack is fine)
- `CLAUDE.md`, `docs/architecture/README.md` and chapters, `docs/adr/README.md`, ADR-031 to ADR-050
  (especially 032, 033, 034, 035, 044, 046, 047, 050), `docs/adr/glossary.md`.
- The current plan: `docs/plans/2026-11-24-governed-self-serve-platform/` (README, 004 to 007).
- `contracts/`, `platform/k2gen/`, `docs/products/`, `docker/lake/` (ingest.py and its stages),
  `services/capture-rust/`, `docker/airflow/dags/`, `dbt/`, `docker/trino/`, `docker/clickhouse/`,
  `docker-compose.yml`, `scripts/` (health, gates, parity), `Makefile`.
- In flight, may be read with `git show origin/<branch>:<path>` after `git fetch`:
  `feat/file-source-pattern` (a contract declares a file source; generator emits a four-task
  Airflow DAG: fetch, load to a branch, audit, publish by fast-forward).
- Constraints that stay: single host, 25 CPU / 48 GiB ceiling (ADR-045); every image and package
  pinned; no secrets in the repo; ports bound to loopback; Accepted ADRs are immutable.

## What to deliver: ONE markdown file, at the path given to you, 500 to 900 lines
1. **The architecture in one page**: the four layers (ingestion, storage, catalog, service), the
   product as the unit that crosses them, one Mermaid diagram that fits a screen.
2. **Product model**: exact contract fields (which are ODCS standard, which custom), lifecycle
   states and transitions, source binding per source kind, declared inputs. One worked YAML example
   each for: a paused order-book stream product, a scheduled-file product, an API pull, a derived
   SQL product, and ClickHouse as a paused serving product.
3. **Each layer**: what exists today (cite file paths), the target, the smallest set of changes,
   what is generated versus hand-written, and what still needs code when a user adds a product of
   each source kind. Be honest about the stream path.
4. **Control flow**: how a runtime component learns a product's state and config (and why that
   mechanism), how a state change propagates, how upstream and downstream impact is computed and
   shown, how it is recorded in history.
5. **Service layer**: the one SQL endpoint; the REST API's resources, auth model, what it must not
   do, technology choice with a pinned version, resource cost.
6. **Catalog**: source of truth versus projection; lineage; what a product page shows.
7. **The new plan**: phases in order, each with scope, exit criteria that a command can prove,
   dependencies, rough effort in days, and what from old P4 to P7 it carries. Include where the
   ClickHouse pause, the REST API, infrastructure-as-code, the hardening sprint and the release
   gate sit. Say what happens to in-flight work.
8. **Costs and risks**: effort total, resource budget impact, the five biggest risks with a spike
   or escape hatch each, what you deliberately leave out and why.
9. **Why this design**: where it is stronger and weaker than the two obvious alternatives.

Rules for your text: plain, precise English; define every term on first use (the owner reads to
learn); no em-dash character; no secrets; no employer names; no real market prices or data counts;
every claim about the repo carries a file path you actually read. Verify or drop.
Final message back: under 150 words, the file path and your design's one-sentence thesis.

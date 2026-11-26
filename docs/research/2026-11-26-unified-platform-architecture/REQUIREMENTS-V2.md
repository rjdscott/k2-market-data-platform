# Requirements for the revised architecture (v2), 2026-11-26 evening

This file overrides `BRIEF.md`, the addenda, and `VERDICT.md` wherever they differ. It records what
the owner decided AFTER the verdict and the adversarial review (`REVIEW.md`). Everything in the
brief and addenda 1 to 5 that is not contradicted here still stands.

## A. What the owner said about the verdict's diagrams
The owner reviewed the six diagrams and is not comfortable with them: "there are some odd
connections or flows", and when asked which, chose every candidate offered and added "just all of
it". The named problems:
1. **One raw archive feeding every venue.** A single `raw_archive` product between the topics and
   all venue products couples the venues and has no clear owner. Decision: there is NO shared raw
   archive product. Each product owns its connector and its own verbatim landing. A venue's trades
   product reads that venue's own topic. A paused product means the platform stops consuming for
   that product; nothing lands; on resume it starts from what the source still retains.
2. **Generator arrows to everything.** Dotted lines from the generator to pipelines, pages, the
   query engine and the API, plus generated files committed to git and a `make apply` step, make it
   impossible to see what is configuration, what is deployment and what runs when. Diagrams must
   separate three moments: defining a product (design time), deploying a release, and running.
3. **Catalog off to the side.** The catalog is a unified layer users start from, not a side box.
   Draw it at the centre: ingestion, storage and query all register into it; people and services
   discover products, schemas, owners, state, lineage and access through it.
4. **REST API and permissions path.** It must be obvious what a person or service talks to, where
   permission is enforced (one place), and that the SQL endpoint and the REST API give the same
   answer for the same caller.
Consequence: redraw every diagram from scratch for a reader who is a platform owner and for a
stakeholder who will use the platform. Do not patch the old ones.

## B. Decisions since the verdict
1. **dbt reach: decided on the reviewer's evidence.** dbt-core (not the Fusion engine) on Trino
   builds silver, gold and marts. Connectors land verbatim data and a typed bronze table, with
   strict write, audit, publish on a table branch. Silver, gold and marts are dbt models: model
   contract checked before the build, data tests straight after, automatic rollback to the previous
   snapshot when a test fails. Logic that is not SQL (order-book replay, event bars, type-2 history
   if dbt snapshots do not fit) is a registered, named, tested code transform. There is NO custom
   transform vocabulary and no compiler for one.
2. **All ten critical and high findings of `REVIEW.md` are accepted** and must be designed in:
   no contract-supplied code runs with root storage rights; deploy from an immutable exported
   release, never from the working tree; named, comparable access policies (delay, mask, row scope)
   instead of free-form filters; independent hand-written access expectations; reader catalogs that
   cannot run table procedures; a backup set generated from contracts and an early restore drill;
   derived products that run when some inputs are missing and flag it; the per-product run cost
   measured in the first phase; the Spark image rebased onto a maintained base; SQL models instead
   of a private language. Take the medium and low findings unless you can show one is wrong.
3. **Product definitions are documents; where they are stored is a seam.** Today a product's
   definition sits in the repository (an Open Data Contract Standard contract per table and an
   Open Data Product Standard file per product). The owner's end state: definitions live in a
   database, controlled through an API, with a UI on top of that API; the registry is then the
   source of truth, with validation, versions, owner or platform approval and full history, and an
   export to git for audit. Timing: DESIGN THE SEAM NOW, BUILD THE REGISTRY AFTER 3.0. So for 3.0:
   git is the store; the compile step takes a set of product documents and must not care where
   they came from; the checks that run in CI today are written so they can run inside an API
   later; the read-only REST API already serves product metadata. The design must show the 3.0
   state and the later state side by side and say exactly what changes between them (the store and
   the approval step) and what does not (the document format, the compiler, the runtime).
   This softens the verdict's ethos principle "the generator is the only control plane": restate it
   so it stays true after the registry exists.
4. **Security master and instruments.** Every venue has an instruments product as well as a trades
   product. Instruments come from each venue's PUBLIC REFERENCE API through the public-API
   connector, not from a hand-kept file (the capture keeps its own subscription list; that is the
   producer's business). The security master has two levels: an asset (Bitcoin, one row) and an
   instrument (BTC/USD, BTC/USDT and BTC/USDC are three instruments of one asset), with each
   venue's own symbol mapped to its instrument, with history. Matching is by rule on normalised
   base and quote codes plus a small reviewed exceptions list (for example a venue that writes XBT
   for BTC); a symbol that matches nothing fails an audit, never a guess. Unified trades take
   their instrument id from the security master.
5. **Learning and documentation.** dbt is also a teaching goal for the owner: the ECB FX slice is
   the dbt worked example; the plan carries guided dbt labs, an owner's guide to reviewing dbt
   work, the dbt docs site with lineage served locally and linked from the catalog, and a plain
   "what this does and why" section in each pull request with dbt work. Every phase ships the
   runbooks for what it builds (onboarding a product per connector kind; pause, resume, deprecate,
   retire; failed audit; connector failure; grant, revoke and verify access; add a venue; rotate a
   credential; upgrade a pinned component; backup, restore, rebuild from empty). Architecture
   chapters with Mermaid diagrams and the design ethos land under `docs/architecture/`.
6. **Still open, for the owner (do not decide these; present them):** scope cuts proposed by the
   reviewer (one role per principal; catalog UI after 3.0; FX capability not content; optional
   layers per product); connector order (all four connector kinds before widening crypto);
   identity strength before 3.0; alert and off-host backup destinations; infrastructure-as-code
   scope; raw retention; a minimal REST route in the first slice.

## C. Rules for the diagrams (the owner must be able to read them without the author present)
- One idea per diagram. At most about twelve nodes. If a diagram needs more, split it.
- A fixed visual vocabulary used the same way in every diagram and explained in a legend at the
  top of the document: solid arrow = data moves; dashed arrow = configuration or metadata; one
  colour for what a person writes, one for what is generated, one for what runs; cylinders for
  stored data. Never mix design-time and run-time arrows in one diagram without labelling them.
- Every arrow has a direction that matches what actually flows, and a short label where the flow
  is not obvious. No arrow that means "is related to".
- Start from what a stakeholder sees (find a product, read its page, get access, query it by SQL
  or REST), then what a product owner does (define, change, pause), then how the platform does it.
- Each diagram has a one-paragraph reading guide and names the section of the text it draws. The
  text and the diagram must agree: every node appears in the text, every flow is described.
- Valid Mermaid (flowchart, sequenceDiagram or stateDiagram-v2), each fits one screen, no ASCII art.
- Required set, minimum: (1) the platform on one page with the catalog at the centre; (2) a
  stakeholder's journey: discover, request access, query by SQL and by REST; (3) defining a
  product: documents, checks, release (3.0: git; later: registry and API, shown side by side);
  (4) what runs for one product: connector, landing, bronze, dbt silver and gold, tests, publish,
  rollback; (5) the lifecycle states; (6) permissions: one enforcement point, both endpoints;
  (7) crypto as products: per venue trades and instruments, security master, unified trades,
  candles, with no shared archive; (8) ECB FX as products, the dbt worked example; (9) deploy:
  from merged definition to running release, and rollback; (10) today beside the target.

## D. What the platform is for (owner, confirmed 2026-11-26)
- The platform is **domain-agnostic**. Market data is its first tenant, not its definition. The
  owner will onboard many kinds of data: economic data, financial reference data, static data and
  others, "to run a successful data business".
- **The business value is in joining products**: a product from one domain enriched with products
  from others. So the design must make cross-domain joins a first-class, governed thing, not an
  accident of two tables sharing a column name:
  - **Conformed reference products** that many domains join to (the security master, a currency
    master, calendars, later entities and classifications) are ordinary products with owners and
    contracts, and the design says which identifiers a product should carry to be joinable.
  - A contract can declare its **keys and what they refer to** (for example "this column is an
    instrument id of the security master"), so the catalog can show what a product can be joined
    to, lineage crosses domains, and a check can fail when a reference does not resolve.
  - **Enriched (derived) products** that read several domains are first-class: their inputs are
    declared, their permissions can never be weaker than their inputs', their state reflects their
    inputs' state, and time alignment is explicit (which version of a reference row applied when
    the fact happened: point-in-time correctness).
  - Nothing market-specific belongs in the platform's core. A security master is a product of a
    reference-data domain; the core provides only the generic mechanics (connectors, layers,
    contracts, keys and references, lifecycle, permissions, catalog, serving).
- Diagram set addition: (11) how two products from different domains become an enriched product:
  shared reference products, declared keys, lineage, permissions inherited, point-in-time join.
- The three readers the document must satisfy: a principal engineer (is it sound and standard), a
  platform owner (can I run and govern it), and a stakeholder who uses products to create
  business value (can I find, trust, get and combine data quickly).

## E. Metadata, open standards, and readiness for AI (owner, 2026-11-26, added while v2 was being drafted)
The owner: metadata is important; enough of it must be captured everywhere across the platform;
as many open standards as possible; eventually the platform is to be AI friendly.
1. **Metadata is a first-class output of every step, not a by-product.** The design must have one
   section and one diagram (number 12) showing, for each kind of metadata, where it is captured,
   in what standard format, where it is stored, and how a person, a service or the catalog reads
   it. Kinds, at minimum:
   - descriptive and business: product and column descriptions, owner, domain, glossary terms,
     tags, classification and sensitivity;
   - structural: schemas and their versions, keys, references to other products, units,
     currencies, time semantics (event time, publication time, valid time);
   - provenance and terms: source, how and when fetched, content hash or offsets, licence and
     terms of use, attribution required (this is a data business: what may be redistributed
     matters);
   - operational: every run, its inputs and outputs, duration, row and byte counts, freshness,
     failures;
   - quality: every test and audit result, service level objectives and their history;
   - lineage: table level and column level, across domains, including which version of a
     reference product an enriched row used;
   - access: who may read what and under which policy, every grant and revocation, and usage
     (who queried which product), which is also what tells the owner which products create value;
   - lifecycle: every state change with who, when and why.
   Say honestly which of these the repository captures today (cite paths), which v2 adds in which
   phase, and which are deferred with a trigger.
2. **Open standards wherever one exists and is mature.** Provide a table: concern, standard,
   version, who stewards it, where the platform uses it, and what would be private invention
   without it. Candidates to evaluate, adopt or reject with a reason: Open Data Contract Standard
   and Open Data Product Standard (Bitol, Linux Foundation); Apache Iceberg and the Iceberg REST
   catalog protocol; Apache Parquet; Apache Avro and JSON Schema; SQL; OpenLineage for lineage
   events (the judge deferred it; reconsider, since the Airflow provider and the dbt integration
   emit it nearly for free); OpenTelemetry and OpenMetrics for telemetry; OpenAPI for the REST
   API; OAuth 2.0 and OpenID Connect for identity; DCAT for catalog interchange; Semantic
   Versioning; CloudEvents if events are published. Every custom field the design adds must say
   why no standard field fits. The owner's maturity rule still applies to each.
3. **AI friendly, eventually.** Not built for 3.0, but nothing in 3.0 may block it, and the
   design says what it means concretely: everything in the catalog is machine readable through
   the same REST API (not only rendered pages); descriptions exist at column level and are
   checked for presence; each product carries example queries and its join keys; identifiers are
   stable; a later Model Context Protocol server (or equivalent) can offer search of the catalog
   and governed queries, always as the calling identity through the one enforcement point, never
   around it; usage by AI agents is logged like any other. Give it a place in the post-3.0
   roadmap beside the registry.

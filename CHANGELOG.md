# Changelog

All notable changes to this platform are recorded here, in the
[Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/) format.

A version names one tested set of everything in this repository: capture, the lake,
Trino, dbt, Airflow, the contracts and the gates, under one
[SemVer](https://semver.org/) number. An alpha may change anything, including table
layouts and commands, without notice. `3.0.0` is reserved for the release gate that ends
[the plan to release](docs/plans/2026-11-26-unified-product-platform-to-release/README.md);
`3.0.0-beta.1` marks the demo point of
[the unified product platform plan](docs/plans/2026-11-26-unified-product-platform/README.md).

## [Unreleased]

Phase 004 of the unified product platform plan, "Kraken trades, instruments and the security master",
as built on branch `phase/004-005-kraken-and-enriched`. **Nothing here is deployed**: no release carries
it yet, and the stack runs `3.0.0-alpha.5.1`. Its proofs so far are unit suites and throwaway lakes,
each named below.

### Decided

- A Kafka product lands every record of its offset range in a landing of its own, then stages bronze
  from it by offset with the envelope decoded (ADR-063, **Proposed**, for the owner's acceptance). It
  would narrow ADR-021's shared archive and keeps ADR-022 and ADR-047.
- Owner's decisions of 2026-11-28: the Kafka landing is never expired (plan 004's option (a)), its
  growth reported by the soak and decided at the 3.0 gate; `LAKEKEEPER_ENCRYPTION_KEY` stays in `.env`
  for now, so the first restore drill does not prove a host loss; the nightly backup timer is not
  installed yet; the independent review's thirteen findings on the build breakdown are all accepted.
- Defaults taken in the build, each with its reason (departures from the plan's text):
  - The landing holds every record of the range, control frames included, and the `stream = trade`
    filter runs at bronze staging: the offset checks of phase 001 hold only over an unbroken range,
    and the frozen landing has no column for a skipped record (plan 004 said "the kept records").
  - Bronze carries the decoded `RawMessage` envelope, by name, beside the offset key and lineage:
    Trino has no SQL function that decodes Avro, so silver could not read it out of the bytes.
  - The connector's `envelope.contract` names `bronze_trade`, not a separate `raw_message` contract as
    the frozen I-1 section says: the schema has no build kind for a contract with no lake table.
  - A paused Kafka product resumed after its position left the log fails loudly like any other run
    below the log start, never a silent jump to the log start (plan 004 said it "resumes at the log
    start"): a silent jump is the unrecorded hole ADR-022 exists to prevent.
  - Every DAG is created paused, whatever its status: a release's new Kafka product would otherwise
    read its whole retained backlog under the deploy that adds it. The operator unpauses after `make
    release-verify`; an existing DAG keeps its state.
  - The holds have one writer, `svc.operator`, whose password file only the host holds, not
    `svc.runs` as the breakdown proposed: `svc.runs`'s password is in every dbt-runner one-shot and
    the scheduler, so its holder could forge or end a hold (review A4).
  - The role order: a role may be `within` others; `delayed_reader` is within `full_reader`, so
    `silver_trade` offers `delayed_reader` over the security master, which offers `full_reader` only
    and has no event time to delay; `public_reader` is within none (review A6).
  - `silver_trade`'s `delayed_reader` masks `kafka_offset` and `received_at` as well as `conn_id` and
    `conn_msg_seq` (plan 004 named the last two): a row is delayed by its trade time, so every column
    that counts or dates deliveries would show the last 20 minutes' activity on old rows.
  - Each input port states `whenAbsent`: `fail` for a consumer built as a `table` model (zero rows
    would rebuild it empty), `empty` for `canonical-trades`' optional venue, which then shows
    `partial`.
  - A degraded `kraken-trades` build starts one `kraken-instruments` run, by the product property
    `refresh`, one hop, never waiting for it.
  - A dbt output is compacted inside the wrapper after its tests, once it holds 12 delete files or 48
    small files beyond one per partition; a Kafka product's landing and bronze are compacted once a
    day in the product's own DAG with the Spark key, skipped while bronze's `audit` holds a batch.
  - `maxOffsetsPerPartition: 150000` and the ten-minute timeouts are engineering bounds, stated as
    such in the documents, until the first measured runs set them.

### Added

- The Kafka connector (`docker/lake/kafka.py`): plan from the landing's newest connector snapshot,
  land every record by one commit that requires `main` unmoved, stage bronze by offset past its
  through-offset, audit (`accounting`, `parses`, `keys_unique`) and publish; below the log start
  refused, an accept-data-loss run resuming at the log start with the loss in `k2.kafka-gaps`,
  cumulative. Proven on throwaway lakes by `scripts/verify/kafka-path.sh`, groups a, b and c (10, 5
  and 6 PASS, 0 FAIL on 2026-11-28).
- The public-API connector: the `public-api` fetch kind under the existing address guard, and its
  landing, audit and publish through the file connector.
- Five products: `market-capture/kraken-trades` (landing, `bronze_trade`, `silver_trade`, the held
  `silver_trade_unresolved`), `market-capture/kraken-instruments` (Kraken's `AssetPairs`, hourly, into
  `silver_listing`), `reference-data/security-master` (`asset`, type-2 `instrument` and
  `venue_symbol`, the claims and the held spellings, two hand-written seeds the owner reviews),
  `market-analytics/canonical-trades` (a union over its venue ports, Kraken the only one) and
  `market-analytics/candles` at 1m, 5m, 1h and 1d. `make test-dbt SELECTOR=candles` built all six in
  order with every test passing, on empty bronze.
- The operator hold: the platform's second table, `governance_build.run_records.holds`, append-only;
  `make hold PRODUCT= REASON=` and `make unhold` (`scripts/hold.py`); `resolve` refuses a held
  product's run; the principal `svc.operator`.
- Per-product alert rules compiled into each release (`compiled/prometheus/product-alerts.yml`):
  `ProductRunFailed_<dag>`, `ProductStale_<dag>`, `ProductRunSlow_<dag>`, with a promtool test file
  for `kraken_trades` (`platform/k2gen/tests/promtool/`).
- The wrapper reports a degraded build (a failed `warn` test), each input's `lagMinutes` and state,
  reads an absent optional input as zero rows per its port, and compacts after its tests; the one
  write it accepts on the tested snapshot is this run's own `replace` reading as many rows.
- `k2runs.state`: `complete`, `partial: <inputs>` or `degraded`, shown on the catalog page with the
  offset gaps an accept-data-loss run gave up.
- `maintain` for a Kafka product: whole-table offset continuity, expiry that keeps the newest
  connector snapshot, orphan cleanup with a 24-hour floor, and the compaction pass.
- k2gen checks: `connectors.*` (bronze shape, envelope, topic ownership, stack hosts, pagination,
  auth, url host), `refresh.*`, `access.role-within-invalid`, `access.input-undeclared`,
  `docs.empty-input-into-table`; `contracts/domains.yaml` lists each domain's topics.
- `make test-dbt SELECTOR=<name>` builds one product and the products its inputs name.
- `scripts/verify/dbt-patterns.sh` (type-2 from listings, a held table taken back, a `warn` test,
  compaction after the tests) and `scripts/verify/delete-files.sh` (each dbt output's delete files
  against a bound of 24, for the soak; not yet run).
- The principal `svc.partner-dashboard`, declared with no role, and the access rows for the new
  products and the holds table in `tests/access/expectations.yaml`.
- ADR-063, runbooks for onboarding a Kafka product and a public-API product, holding a product,
  accepting data loss and a blocked Kafka batch; architecture chapter 23, as target text.

### Changed

- Deploy never unpauses a held product's DAG, names the DAGs a release adds and never pauses or
  unpauses them; the drift check reports a platform table a rolled-back release added as LEFT, not
  drift.
- `docs.terms-missing` holds a grant outside the owning team to terms on every product upstream, not
  only the product granted.
- A delayed reader never sees a delivery or build counter.

### Fixed

Found by the phase's reviews before any release:

- A partition bronze never staged starts at the landing's first offset, never 0, so a first run on a
  trimmed topic no longer plans rows that were never there (D1).
- A Kafka batch killed before its audit or publish is restaged by the next run (D2); only a registry
  404 makes a schema id unresolvable, and `parses` compares what bronze staged (D3); two loads at once
  land a range once (D4).
- An input a model reads but does not declare is refused (A1); a topic belongs to one product of its
  owning domain (A2); terms are checked upstream (A3); the role order names declared roles with no
  cycle (A6).
- A Kraken trade frame `silver_trade` cannot type (not JSON, no data array, a field missing or not of
  its type) no longer fails every later build at a NOT NULL column: it is kept whole, with its reason
  and bytes, in the owner-only `silver_trade_rejected`, its warn rule `rejected_frames_zero` degrades
  the build and the rest publishes; and a quality rule's dbt test now carries the rule's name, so
  `warned` and `dq_test_results` say `unresolved_rows_zero`, not dbt's argument-made id.

### Known and open

- Not deployed: no live deploy, no first run on the stack, no `make hold` or `make unhold` against a
  stack, no accept-data-loss run through Airflow, no deploy rehearsal with a second platform table.
- `scripts/verify/kafka-path.sh` claims a11, a12 and b6 and `scripts/verify/dbt-patterns.sh`'s fixed
  and added claims are pending their rerun (its last run printed `10 PASS, 1 FAIL`); the widened
  `run-path.sh` (the chain with rows) is in progress.
- Kraken's terms of reuse are not recorded, so no grant on any phase 004 product: `svc.partner-dashboard`
  holds no role, and the delayed role on the new tables is unexercised until a grant.
- A product's `degraded` and `partial` states are not alertable in Prometheus (no series carries them);
  they show on the catalog page only. The catalog page does not show holds.
- `make product-verify`, `make rebuild`, `make restore`, `make restore-drill`, `make parity-silver`, the
  re-pinned `make parity-ohlcv`, the restore drill and plan 004's chaos scripts are not built yet.
- The 48-hour soak has not started, so the chain's run times and the landing's growth on disk are not
  measured.
- dbt labs 4 (`UNNEST WITH ORDINALITY` and window flags) and 5 (type-2 history from listings) are not
  written.

## [3.0.0-alpha.5.1] - 2026-11-27

### Fixed

- A deploy that does not drain no longer fails beside a running product build: the drift check at
  verify read dbt's `<model>__dbt_tmp` relation as drift and the deploy rolled back
  (`scripts/chaos/deploy-mid-run.sh` on the live stack, `2 PASS, 2 FAIL`). The check is run again, for
  300 s at most, while every difference is such a relation; one that stays is still drift. The script
  then printed `4 PASS, 0 FAIL in 105 s`.
- `scripts/chaos/deploy-mid-run.sh` reads Airflow's JSON past its log lines and asks the `tasks`
  command group for a run's task states.

### Added

- The "landed" lines of phases 002 and 003 in their plan files.

## [3.0.0-alpha.5] - 2026-11-27

Phase 003 of the unified product platform plan, "serving and teaching": `k2-api` serves the product
catalog and the data products over REST, asking Trino as the caller, so REST and SQL give one answer; a
generated catalog site and the dbt docs are served beside it; the dbt labs teach the platform's dbt.

### Decided

- Owner's decision of 2026-11-27: a reader's API key is written by the operator to a mode-600 file under
  `${K2_SECRETS}/api/`, never shown, and only its sha256 is kept in `keys.json`.
- Defaults taken in the build, each with its reason (departures from the plan's text):
  - `k2-api` is in the default service set: the phase's exit has every part report its release, and a
    deploy moves only the default set (17.30 CPU / 29.375 GiB at the bootstrap peak,
    `python3 scripts/check-budget.py`).
  - The data route orders rows by the table's declared key, so a REST page and the same SQL compare row
    for row, and phase 007's cursor has an order.
  - API keys come from `${K2_SECRETS}/api/keys.json`; the release holds only the principals `k2-api` may
    act as (`compiled/api/principals.json`), never a key map.
  - `k2-api` reads each output's tested snapshot from the base's `$refs` through Trino as `svc.api`, never
    the run records or Lakekeeper, so it never joins the catalog's network.
  - The catalog site is generated in `tools` as `svc.catalog` and served from a volume, so `k2-api` keeps
    one Trino identity; it and the dbt docs are regenerated after each deploy and rollback and daily.

### Added

- `k2-api` (`services/k2-api/`, `127.0.0.1:8095`): `/healthz`, `/v1/openapi.json` (the frozen OpenAPI 3.1
  document), `/v1/release`, `/v1/products`, one product's catalog document with the caller's access, its
  ODCS contracts, and `/v1/data/{domain}/{product}/{table}` with `fields`, `limit` and equality filters,
  read through the reader view as the caller (401 without a key, 403 when Trino denies); `make test-api`
  and `scripts/verify/api-schema.sh`.
- The catalog site's first version (`platform/catalog-site/k2site.py`): a static page per product and an
  index, at `/catalog/`; the dbt docs at `/dbt-docs/` (`k2-dbt docs`, which opens no connection);
  `make site`, `make site-install` (a daily timer) and `scripts/verify/catalog-site.sh`.
- The access suite's read and denial rows also run through `k2-api` for a principal with a key, and the
  REST answer must equal the SQL one.
- dbt labs 1 to 3 (`docs/development/dbt-labs/`), the owner's guide to reviewing dbt work
  (`docs/development/reviewing-dbt-work.md`) and a pull request template.

### Changed

- `release-verify` asks `k2-api` which release it serves; deploy writes both sites after every deploy and
  rollback, and a site failure never fails a deploy.
- Architecture chapter 22 describes `k2-api`, its keys and the catalog site.

### Security

- `k2-api` decides no access: it logs in as `svc.api`, which may become only the declared `p.*`
  principals, and every rule is Trino's. It never writes, accepts SQL, reads storage or Lakekeeper, or
  keeps a row; it sits on `k2-net` only, with a read-only root and no capability.
- A caller the rules deny learns whether an output is published: Trino analyses a view before it checks
  permission, so a never-published output answers 404 rather than 403.

## [3.0.0-alpha.4] - 2026-11-27

Phase 002 of the unified product platform plan, "the run spine, through ECB FX": the first product
pipelines run from a release. A publisher's file is fetched under a host allow-list, landed verbatim,
published to bronze only after its audit, built to silver and gold by dbt, and shown to readers only
once its tests passed. Each engine holds its own storage key.

### Decided

- dbt builds each product's silver and gold on Trino through one wrapper that publishes only tested data
  (ADR-062), superseding ADR-035's Spark and dbt boundary.
- Each engine holds its own storage key, and the catalog's remote signing is off on every warehouse
  (ADR-054), finished from the draft of #227.
- Owner's decisions of 2026-11-27: the parsers' tests use synthetic files in the publishers' formats,
  never the published files; a storage key per engine, generated by `make engine-keys`; the ECB
  product's reader `p.analyst-a` is granted gold only, after the ECB's terms page was read (free use,
  the ECB cited as the source, modifications stated; the rates are "for information purposes only");
  the scratch bucket of phase 000 is dropped (#254). Rotating the MinIO root password stays the owner's.
- Defaults taken in the build, each with its reason (departures from the plan's text):
  - No `governance/run-records` product document: the namespace is reserved in k2gen and the table has
    one DDL, so a document would be a second definition.
  - Maintenance has its own key, held in the Spark container where it runs, not in the scheduler, which
    holds no storage key.
  - Parser audits run in the fetch container beside the parser and reach Spark through `audits.json`:
    they are product code, kept away from every storage key.
  - dbt's target and logs live under the container's `/tmp`, its only writable path, not the spool,
    which is fetch's.
  - `build` runs when nothing upstream failed (`none_failed`): an unchanged file skips load, audit and
    publish, and a build fixed by a deploy, or an input that moved, still builds.
  - One `dbt build` with unit tests inside it, not unit tests first: the split fails on a product's
    first run. A rollback across a column change restores by create-or-replace, since a rollback
    restores rows and not schema.
  - The backup uses a read-only backup key, not the root pair.
  - fetch runs on its own egress-only network, `k2-egress`, with a spool mount per DAG: a parser is
    product code and must reach no service of the stack and no other product's files.
  - Landings expire after 14 days per product, built in this phase, which builds the landings, rather
    than the Kraken phase.
  - The drain waits at most 300 s; `make rollback` does not drain.
  - Notebooks through Trino move to phase 004, where `notebooks/` returns.
  - The plan's consumer check `docker exec k2-trino trino --execute "ALTER TABLE ... EXECUTE optimize"`
    names no password, so it is a statement row of the access suite for `p.analyst-a`, over TLS.

### Added

- The fetch container (`docker/fetch/`) and the `https-file` connector: declared hosts only, every
  resolved address public, the socket pinned to the checked address with TLS verified, redirects followed
  by hand over `https` to declared hosts at most five times, a 64 MiB cap and a 300 s deadline;
  `make test-connectors`, with no network.
- The Spark connector (`docker/lake/connector.py`): the spooled sha256 re-checked, a conditional write to
  the landing, rows onto bronze's `audit` branch, the audit, and a fast-forward of `main`; `expire` of a
  failed build's snapshots by id; a daily `maintain` that expires snapshots by retention and landings
  after 14 days.
- One DAG per product, built from the release by `docker/airflow/dags/factory.py`, pinned to one release
  per run; the one-shot operator `docker/airflow/dags/oneshot.py`.
- The dbt wrapper `k2-dbt` and its image `docker/dbt-runner/`; `k2_input` pins each input at its tested
  snapshot; the `k2_results` hook records every test.
- Run records, written by `platform/runs/k2runs.py`.
- Four products: `reference-data.currency-master` (the ISO 4217 list), `reference-data.calendars`
  (TARGET closing days from a seed), `fx-reference.ecb-fx-rates` (the ECB's 90-day reference rates to
  bronze, silver and gold) and `governance.quality-results`.
- A MinIO user and policy per engine (`docker/lake/minio/policies/`) and `make engine-keys`.
- `make backup` copies the live release's backup set, compiled from the contracts; `make backup-verify`.
- The access suite's storage-key rows, a new row shape; fence rows for fetch and for MinIO from the host.
- `scripts/verify/storage-keys.sh`, `oneshot-slots.sh` and `dbt-wrapper.sh`, each on a throwaway stack;
  a fast deploy rehearsal (`FAST=1`), which `make spine-e2e` runs unless `FULL=1`.

### Changed

- A release carries its run code under `platform/`; Airflow, Spark and Prometheus read their DAGs,
  scripts and alert rules from the release, and a run pins its release once.
- Deploy drains the DAGs it affects, runs `lake-init` from the release, creates missing networks, names
  every missing secret file in preflight, and removes a service the target puts behind a profile.
- The `fetch` and `dbt-runner` stubs are real images, under the same limits.
- `fx-reference.ecb-fx-rates` 1.1.1 stops a run at 10 minutes (`timeoutMinutes`), factor 5 on its
  largest measured run; the run times, with their commands, in `docs/benchmarks/2026-11-27-ecb-fx-run-times.md`.
- `make test` and CI run the connector, run-records, catalog site and wrapper tests; CI builds the Spark
  image its throwaway lakes need.

### Fixed

- Deploy's `views-narrow` skips a view the release it moves from does not declare, so a release that adds
  a product no longer fails there; the plan lists such views as `views new in this release`.
- A rollback across a release that added a product exits 1, not 3: the drift check reports what a release
  deployed or attempted since the live one created as `LEFT` lines (`--left-by`, `K2_DRIFT_LEFT_BY`),
  not `DRIFT`.
- `make lake-init [RECREATE="<service> ..."]` reruns `lake-init` from the live release, for use after
  `make engine-keys ROTATE=<engine>`.
- fetch stops reading at a response that has closed its connection: on a server that closes after the
  body, the next read set a timeout on the closed socket and the fetch exited 1 with `OSError: [Errno 9]
  Bad file descriptor`; a body short of its `Content-Length` now fails the task.
- `maintain` skips a dbt output not built yet, names it in its result's `notBuilt` and exits 0, instead
  of exiting 1 before a product's first good build; a missing bronze, checks or landing table still fails.
- The dbt wrapper's publish guard knows every statement of its build by a fresh Trino client tag per run,
  so a table that builds empty, whose `main` is the `CREATE OR REPLACE`'s snapshot that dbt never
  reports, publishes; an earlier run's leftover query and any write without this run's tag are still
  refused.
- The catalog fence admits the platform's own one-shots on `k2-catalog` (labels `com.k2.oneshot` and
  `com.k2.project`, the name `k2-<kind>-<dagId>-<try>`, the image the live release gives the kind),
  where a running dbt build failed `release-verify` before; any other container still fails it.
- `make backup` reads a gzip-compressed Iceberg metadata file (`<v>.gz.metadata.json`), which failed the
  first backup of real tables.

### Removed

- MinIO's host ports, 9000 and 9001, and with them the MinIO console screenshot.

### Security

- Every warehouse has remote signing and STS off, so each engine's own key is the one MinIO checks;
  Trino no longer holds the root pair. The root pair stays with MinIO, `lake-init`, Lakekeeper's stored
  warehouse credential and `make drop`.
- From the host, the lake's objects are reached only through Trino: neither MinIO nor Lakekeeper
  publishes a port.
- fetch, where product parsers run, is alone on `k2-egress` and holds no key or secret; the scheduler
  starts each one-shot only with what its kind may have, never its own variables or other secret files.
- Known and deferred: test results are written by the builder, so quality results are self-attested;
  the Spark container holds the maintenance key beside its own; a parser can reach the internet beyond
  its allow-list.

### Known and open
- The connector's publish reads the `audit` branch's head, then fast-forwards `main` in a second call
  with no compare-and-set between them, so a write to `audit` in between would be published unaudited
  (`docker/lake/connector.py`, `publish`).
- `lake-init` prints Lakekeeper's whole error body when a call fails, and nothing checks that body for
  the warehouse's stored credential (`docker/lake/init-lake.sh`, `post`).
- k2-api allows inline script on `/dbt-docs/`, where model descriptions render, and no test covers a
  description that carries script (`services/k2-api/k2api/app.py`, `DBT_DOCS_CSP`).
- k2-api caches the key file by its inode, modification time and size, so a rewrite that keeps all
  three is not read until it restarts (`services/k2-api/k2api/keys.py`).

## [3.0.0-alpha.3] - 2026-11-27

Phase 001 of the unified product platform plan, "the release and rules spine": the platform runs only sealed releases compiled from documents, and Trino
decides who sees what by rules compiled from them, behind passwords over TLS. No product
pipeline runs yet; the run spine (phase 002) brings the first.

### Decided

- Readers see a dbt output only through a generated view over its tested tag, owned by a
  publishing identity, its policies as Trino rules on the view's name (ADR-061); the view
  guards on its base's refs, so a forged base serves nothing. ADR-056's rule against
  definer views is narrowed to that one shape.
- Access rules are compiled per release and deny by default, over a read-only reader catalog
  and a build catalog per domain; the catalog service moves onto a network of its own
  (ADR-059, superseding ADR-044's one writer per domain).
- Every Trino login is a password over HTTPS under a local certificate authority (ADR-060).
- Releases are deployed by moving a pointer per service and rolled back by the same steps
  (ADR-058).
- Products are defined once in ODPS and ODCS documents (ADR-055, superseding ADR-032's
  descriptor format in part) and connectors land verbatim before bronze is published
  (ADR-036, revised).
- Owner's rulings of 2026-11-27: Airflow, Spark and Prometheus mount the releases root now and
  read their DAGs, scripts and rules through a pointer from phase 002; Trino keeps the MinIO
  root pair until phase 002's scoped keys; the compose project keeps its live name, given to
  deploy by `K2_COMPOSE_PROJECT`; folding the review's extra rows into the main rules suite
  waits for the hardening sprint; a fresh install of the release-shaped stack on an empty
  host is not yet proven.
- The decisions taken under the owner's delegation, and the owner's two rulings on FX content
  and landing retention, are listed in `docs/research/2026-11-26-decisions-addendum.md`.

### Added

- `k2gen release`: compiles a tag's documents into a sealed, read-only release named by the
  hash of its files and images, after the document, version, access and SQL checks pass;
  `k2gen diff` prints what deploying it would need.
- Emitters for Trino's configuration, rules and identity, the lake's DDL and reader views,
  the per-product pipelines, the dbt project and the catalog JSON.
- The first two products: an access canary that exercises every policy on the live stack,
  and the paused ClickHouse hot tier as a serving-only product.
- Principals and roles declared in `contracts/access/`; `make password`, `make api-key`,
  `make principal-remove` and `make principals-status`, with every secret generated under
  `${K2_SECRETS}` and never committed.
- `make tls-init`, `make tls-rotate` and `make tls-status` for the local CA and Trino's
  certificate.
- `make release`, `make deploy`, `make release-verify`, `make rollback`, `make release-status`,
  `make release-diff` and `make deploy-rehearsal`.
- The hand-written access suite (`tests/access/`, `make access-verify`, `make test-access`),
  the tested-data marker check and the catalog drift check.
- Alertmanager, delivering every alert to one Slack channel through a webhook kept as a local
  secret.
- `make spine-e2e`, the release and rules spine end to end from a scratch clone, run at merge
  points; `make catalog-fence`, which checks on the running stack that the catalog publishes
  no port, sits on its own network and shares it only with the listed services, and which
  every `release-verify` runs.
- CI compiles every pull request: `k2gen check`, a trial release and `k2gen diff` against the
  newest tag in the job summary, the emitted dbt trees, the rules suite and the access suite
  on a throwaway lake, and a test that holds the compose file to the catalog's allow-list.
- Architecture chapters 21 (releases and deploy) and 22 (access and identity); runbooks to
  deploy, roll back, verify access, manage TLS and passwords, and create or rotate a principal.

### Changed

- The stack runs from releases: services read their configuration through pointers under
  `${K2_RELEASES}`, not from the git checkout.
- Trino listens only on HTTPS and its Web UI is off; every client connects by name, verifies
  the local CA and logs in with its own password.

- The live stack was cut over to releases on 2026-11-27 (`docs/runbooks/first-cutover-to-releases.md`):
  the captures, Redpanda, MinIO and Postgres were not restarted.
- Generated files left git: `make generate`, `make new-product`, the old generator and
  validator, `docs/products/`, `docker/lake/generated/` and `docker/trino/etc/` are removed. A
  release is the only generated output.
- `lake-init` creates the warehouses of the release's storage plan.
- A deploy dumps a database before a one-shot migrates it, refuses when the image a rollback
  would need is gone, and rolls back with Trino down.

### Fixed

- A capture whose connect never returns is abandoned after a bound and retried, and a capture
  that delivers nothing for ten minutes exits to be restarted: on 2026-11-26 the Coinbase
  capture sat in its connect for 80 minutes with nothing logged.

- `scripts/principal-secret.py` no longer fails when two runs create the secrets directory at
  once.

### Security

- No credential, and no hash of one, is in git, a document or a release; a deploy record
  names each secret file the deploy used by presence, mode and size, never by content or
  digest.
- Readers cannot reach a dbt output's untested rows, its base table, its metadata tables or
  time travel; the catalog service has no host port and is reachable only from the
  platform's own services. Delay and row-scope policies suit trusted readers only: a delayed
  or scoped reader can infer value ranges of hidden rows (ADR-061, Outcome).
- The Docker socket is inside the catalog's trust boundary: the Airflow scheduler and cAdvisor
  hold it, and a network fence does not stop a service that can start containers (ADR-059,
  Outcome of 2026-11-27).
- The pinned MinIO image is no longer published by its vendor; the stack runs an unmodified
  mirror, `ghcr.io/rjdscott/minio`, pinned by digest (NOTICE). Replacing MinIO is a decision
  for the hardening sprint.

## [3.0.0-alpha.2] - 2026-11-26

Phase 000 of the unified product platform plan, "clear the ground": the old pipeline is
retired so the new platform is built once, on an empty lake. From this version until the
Kraken phase lands, no live crypto data reaches the lake; the captures keep writing to
Redpanda.

### Decided

- K2 will be rebuilt as a domain-agnostic data product platform: products defined once in
  contracts, one ingestion framework with connectors, dbt for silver and gold, one catalog,
  one enforcement point for SQL and REST (ADR-056, #245). The plan to the demo tag and to
  release (#246) supersedes phases P4 to P7 of the earlier plan; the target is drawn in
  architecture chapter 20 (#247) and the reasoning is kept in the research note (#244).
  Nothing in the running system changes with these documents.

- The ClickHouse hot tier is paused while the platform is built (ADR-057).
- ADR-048 and ADR-049, never built, are superseded by ADR-056.

### Added

- `make drop`: copies a lake table, namespace or bucket to the host, compares object count
  and bytes, and only then drops it; it refuses a bucket under object lock before copying
  (runbook `drop-and-rebuild.md`).
- Runbooks: pause and resume a serving product; upgrade a pinned component; drop and rebuild.
- The measured experiments the release spine depends on, as runnable scripts under
  `scripts/spikes/`, with their findings (`docs/research/2026-11-26-release-spine-spikes.md`)
  and the cost of one product run (`docs/benchmarks/2026-11-26-product-run-cost.md`).

### Changed

- Order-book capture is paused on every venue while the platform is built; trades are
  unaffected and the nine order-book tables keep their history (#232; ADR-050).

- The Spark image is built on the official Apache Spark 3.5.9 image, pinned by digest, and
  runs as a non-root user; the old base carried a Python and an OpenSSL past their end of
  life. Between jobs the container runs only the Spark History Server, on port 18080.
- ClickHouse starts only under the compose profile `hot-gold`; its volumes are kept.
- `make health` fails, where it used to warn, when a venue's capture is unhealthy or
  delivers nothing to Redpanda for two minutes.
- The declared budget at the bootstrap peak is 16.70 CPU / 28.688 GiB over 21 services
  (was 21.30 CPU / 37.812 GiB over 24).
- Gates that read the old tables print a SKIP line naming the phase that restores them:
  `lake-verify`, `parity-ohlcv`, `parity-bars`, `test-notebooks`, the lake chaos faults, the
  lake step of `make health`, the table copy of `make backup`, the dbt build of `make test-dbt`.

### Removed

- The v3 lake ingest: its Spark stages, the DAGs `lake_ingest`, `lake_maintenance`,
  `lake_rebuild` and `dbt_marts`, the dbt marts, the thirteen old-format product contracts,
  the notebooks, the lake exporter and its alerts and dashboard, and the services `lake-ddl`
  and `lake-metrics`. All of it is archived, unchanged, in `legacy/v3-lake-ingest/`, with a
  README that records the retirement.
- The data of the old pipeline: the `raw`, `bronze`, `silver`, `gold` and `audit` namespaces,
  the marts and the scratch namespaces, each dropped after a verified copy on the host. One
  scratch bucket under object lock is dropped when its lock expires.
- ClickHouse's alerts, scrape job, dashboard, chaos scripts and screenshots.

### Fixed

- Spark no longer shows the storage key in its job and history pages or keeps it in its event
  logs: the redaction pattern now covers the key names the lake catalog uses.
- A disk alert exists again: `DockerDiskUsageHigh` and `DockerDiskUsageCritical` read the
  volumes' disk from Redpanda, replacing the two rules that retired with the lake exporter.
- `make test` and `make gates` no longer rebuild the image tag the running Airflow services
  start from.
- Spark event logs are compressed and deleted after two days; they grew without bound in
  the Spark container.
- The ingest no longer fails when a batch holds no frame for a venue, which a paused
  order book produces on every run.

## [3.0.0-alpha.1] - 2026-11-26

First numbered version. It covers the work of phases P0 to P3 of the plan above, on top of the
v3 lake-first stack (tag `v3-phase-e`).

### Added

- Product descriptors and ODCS contracts for every lake table, with `k2gen validate`,
  `make generate` and a CI drift job (#183, #184; ADR-032, ADR-033).
- Airflow 3 runs the lake schedules, with the lake DAGs cut over from Prefect
  (#189, #195; ADR-034).
- Trino 484 over the lake through a read-only catalog, and one mart warehouse per data
  domain (#200; ADR-035, ADR-044).
- A dbt project and its first mart, `market_analytics.candles.ohlcv_1m`, with a
  `dbt_marts` DAG, delayed and scorecard marts, and mart maintenance (#201, #202).
- A governance domain, and one view of quality results across domains (#204; ADR-046).
- A batch audit: the ingest checks each batch after publishing it and never blocks the
  commit (#206; ADR-047).
- A nightly check that every live table matches its contract (#224).
- A nightly copy of the irreplaceable tables and the catalog database (#231).
- A per-venue order-book capture switch, all venues on (#230; ADR-050).
- cAdvisor, resource dashboards and reproducible screenshots (#194), and five guided
  lakehouse labs (#211).

### Changed

- The parity gate skips only a listed, recorded loss window, and a pin that cannot be
  resolved fails (#220, #226).
- Resource ceiling is 25 CPU / 48 GiB at the bootstrap peak, checked in CI
  (#180, #200; ADR-045).
- Readers resolve the snapshot `main` points at (#214).
- Branch protection on `main` requires CI to pass before merge.

### Fixed

- The ingest exits 75 when the writer lock is held, and one row per Binance book second
  survives across ingest runs (#181, #188).
- Ingest driver heap and offset cap raised for the bars recompute, with an alert on a
  sustained backlog (#203, #205).
- Guardrail gates that passed while violating what they guard (#228).
- A skipped dbt unit test is recorded on its model (#212).

### Removed

- The Prefect scheduler: its two services and the `legacy` compose profile are gone, and
  its code is archived in `legacy/prefect/` (#235; ADR-034 Outcome). There is no
  rollback to it in the compose file.

### Security

- Every published port binds to loopback, with a startup guard and a CI gate (#218).
- Every container log is capped (#229).
- Security bumps for the capture crates, notebooks and legacy v1 (#168, #177).

### Known limitations

- The restore from backup has not been drilled; a drill is planned for the hardening
  sprint (`docs/runbooks/lake-backup-and-restore.md`).
- The candle parity pin carries one recorded gap, a host out-of-memory kill, listed in
  `tests/parity/README.md` (ADR-029 Outcome).
- ADR-048 (the gold step reads only touched data and writes bars once) is Accepted and
  not built; ADR-049 (event bars extended, not recomputed) is Proposed.
- The plan's later phases (P4 to P7) are not done, so the contracts, marts and gates
  may still change.

[Unreleased]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.5.1...HEAD
[3.0.0-alpha.5.1]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.5...v3.0.0-alpha.5.1
[3.0.0-alpha.5]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.4...v3.0.0-alpha.5
[3.0.0-alpha.4]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.3...v3.0.0-alpha.4
[3.0.0-alpha.3]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.2...v3.0.0-alpha.3
[3.0.0-alpha.2]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.1...v3.0.0-alpha.2
[3.0.0-alpha.1]: https://github.com/rjdscott/k2-market-data-platform/releases/tag/v3.0.0-alpha.1

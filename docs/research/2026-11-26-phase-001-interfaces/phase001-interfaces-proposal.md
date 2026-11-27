> **Retained design input, dated 2026-11-26 (UTC):** the phase 001 interfaces proposal as the build ran it, its rulings box included, copied unchanged but for session-only paths and the notes marked superseded. It is a record, not the authority.
> Where it differs from `docs/development/phase-001-interfaces.md`, `platform/k2gen/INTERFACES.md` or an Accepted ADR, they win; in particular no credential hash is ever committed (the orchestrator's ruling on Q4).

# Phase 001 interfaces proposal (2026-11-27 UTC, revised after the O8 review)

Reconciles `IMPLEMENTATION-N0-N1.md` section A (A1 to A13, all revised) with `DECISIONS-FINAL.md`,
ADR-056, plans `001` to `003`, spikes (a) to (g) of
`docs/research/2026-11-26-release-spine-spikes.md`, and the independent review
`phase001-o8-review.md` (experiments in session scratch, not retained). Docs read at
`origin/phase/000-clear-the-ground` (`f586050`); post-000 state at `retire/v3-lake-ingest`
(`ffb73dc`).

> **Rulings (orchestrator, 2026-11-27).** Binding on everything below.
> 1. O8 is enforced by design (a): a definer view over the `audited` tag, as amended by five
>    mandatory conditions (section 1): a publisher principal owns the views; view SQL is generated
>    and policies stay Trino rules on the view's name; reader catalogs have no statistics; the view
>    update rule around a schema change; the catalog fence by network plus a drift check.
> 2. The gate `spine-h-tested-data-view` (h1 fence matrix, h2 polled cycle, h3 catalog fence) is
>    phase 001's first step and passes before the interfaces step merges.
> 3. The former Q4 is reversed: the catalog fence is built in phase 001.
> 4. REST `as_of` is an owner decision in phase `007`. SQL readers have no time travel.
> 5. New ADRs start at 058. The tested-data view is ADR-061; ADR-056 gains only an appended Outcome.
>
> **Owner-facing assumptions, stated in ADR-061:** ADR-056's "never definer views" is amended, under
> the five conditions only; SQL readers have no time travel; the catalog is fenced by network, not
> authentication, for 3.0, with authentication revisited at the release gate; products are held to
> tested inputs by `k2_input` plus a lint, not by Trino.

## 1. How O8 ("readers see only tested data, always") is enforced

**The constraint.** Trino 484 writes only `main` (spike g). dbt builds on Trino, so `main` holds
untested rows for the test window (2.6 s, SPIKE-DBT 5) and an empty table for 0.3 s in a
contracted rebuild (f). Whoever may `SELECT` a table reads `main`. Enforcement needs an object
between readers and table that only the build moves, and only Lakekeeper's REST API or Spark can
move a tag (g). ADR-056 s5 says "outputs are tables, never definer views": reader masks and
filters do not apply through one, though a rule on the view itself does (SPIKE-ACCESS).

**Options.**

| | Mechanism | Blocks `main`, time travel, `$` tables | Masks, filters | Cost per build | Rollback, 0.3 s window | Ops | Plan rules hit | Verdict |
|---|---|---|---|---|---|---|---|---|
| a | Definer view per output over the base `FOR VERSION AS OF 'audited'`, owned by a publisher; policies as rules on the view | Yes, measured (review run 1): every base path refused; views refuse numeric and timestamp versions | Yes (review run 1: filter, delay and mask on the view) | One multi-table REST commit (single-table 13.0 ms median, n=20); a view replace only on a column change | Invisible: 51 polls at 50 ms across a contracted replace all saw tested rows | Two namespaces and two principals per product, view sync, catalog fence | Amends ADR-056 s5 via ADR-061 | **Chosen, with conditions** |
| b1 | dbt builds on a branch, publish fast-forwards `main` | Would | Yes | 1 commit | Invisible | Low | None | Dead: Trino 484 and dbt-trino cannot write branches (g); bronze already does this by Spark WAP |
| b2 | dbt builds `__base`; publish copies the tested snapshot into a reader table (Trino CTAS-replace, or Spark `INSERT OVERWRITE` of touched partitions) | Yes | Yes (tables) | A rewrite of every output per build; Spark adds 6.5 s per publish in the `lake_spark` pool of 1, on a five-minute `silver_trade` chain | Invisible | High | Keeps all | Runner-up, too costly |
| c | Second table given the audited snapshot by metadata: `register` (409, g) or an `add-snapshot` graft (works, review E) | Would | Yes | One commit | Register is drop then register, not atomic | Shared files: either side's expiry deletes the other's; field ids mirrored by hand; no engine support | One table, two owners | Dead |
| d | Row filter keyed on snapshot, row lineage or `$file_modified_time` | No: MERGE deletes apply at `main`, compaction resets file times, rows carry no snapshot id (v2 tables) | Yes | A release per build | Visible | Absurd | | Dead |
| e | Accept the window, detect it from query events | No | Yes | None | Visible | Low | Breaks O8 | Rejected by the owner |
| f | Invoker view over the tag | No: the reader needs `SELECT` on the base; with the base denied it fails (review run 1) | Yes | As a | | Medium | None | Dead |

**Design (a), as amended. The five conditions, all mandatory:**

1. **Publisher owns the views.** `pub_<fence>` owns namespace `<fence>` in `<domain>_build` and
   alone holds `SELECT, GRANT_SELECT` on `<domain>.<fence>__base.*` through the reader catalog.
   The builder `dbt_<fence>` owns only `<fence>__base`, has no `GRANT_SELECT` anywhere, and is
   refused `CREATE VIEW` in `<fence>` (review run 1). A builder owning both served the untested row
   through a replaced view (B1). `svc.platform` has no `GRANT_SELECT`. The rules file has no
   `authorization` rules, so `SET AUTHORIZATION` on a view or schema is denied (verified). Product
   views compile with `view_security: invoker`; materialized views do not exist on the REST catalog.
2. **Generated view SQL, policies as rules.** The view is exactly `SELECT <contract columns> FROM
   <domain>.<fence>__base.<table> FOR VERSION AS OF 'audited'`: through the reader catalog, no
   predicate, no function. Every reader policy (`delay`, `mask`, `rowScope`) is a Trino rule on the
   view's exact name, never in view SQL: one enforcement point for SQL and REST, a policy change is
   a rules-only deploy, a view changes only when columns do. View SQL is visible to readers in
   `information_schema.views` (B5); it holds nothing secret.
3. **No statistics for readers.** Reader catalogs set `iceberg.table-statistics-enabled=false`
   (review F: bounds gone, masks and fences intact). The session property `statistics_enabled` is
   allowed only to `dbt_.*`, `pub_.*`, `svc.runs`, `svc.platform`, `svc.catalog`; the review
   wrote `svc.*`, but consumer services are `svc.<name>` too, so they are named, not matched. The
   deny rule is untested: h1 proves it.
4. **View update rule** (A7 step 6). C is the contract's columns, old and new the schemas of the
   tagged and the tested snapshot: replace the view with C ∩ old ∩ new, commit the tag, replace with
   C ∩ new. Idempotent, rerun at reconcile. The wrapper holds the publisher's secret; dbt never
   sees it. (Review run 1: tag first, then view, errors on a dropped column; view first does not.)
5. **Catalog fence.** Lakekeeper stores a view's owner as a plain property, `trino.run-as-owner`,
   that any REST client can set: the review forged definer views naming `pub_a` and `platform`,
   and grafted an untested snapshot into another product's table, each read by a reader (B2), from
   any container on `k2-net` or the host. So Lakekeeper moves to its own network `k2-catalog`
   (members below), with no host port. A drift check runs at `release-verify` and every reconcile:
   reader-namespace listings, view SQL and `trino.run-as-owner` must equal the release, and (from
   `002`, when run records exist) every `audited` tag must equal its table's newest `audited` run
   record, which also catches a moved tag or a graft.

Unchanged from the first draft: the view is created at the first tag (an untested product has no
reader name); one multi-table commit moves a product's tags, each output requiring `main` = the
tested id and `audited` = the previous id or null, a 409 moving nothing (exit 3, page); bronze
needs no tag (Spark WAP); platform tables (run records) stay plain tables in `<fence>`.

**Stated plainly: what (a) costs and leaves open.**

- ADR-061 amends ADR-056 s5 under the five conditions; plan `001`'s check becomes "a definer view
  or a materialized view in a product's documents fails `k2gen check`".
- SQL readers have no time travel: a numeric or timestamp version on a view fails. A string version
  is silently ignored and returns the tag's rows, so tests use a numeric id.
- REST `as_of` is the owner's decision in `007` (Q1).
- **Products are held to tested inputs by `k2_input` and the lint, not by Trino.** A builder with
  `SELECT` on an input base can copy its `main` (review: a CTAS copied the untested row, B4). It
  cannot serve those rows through a view of its own (no `GRANT_SELECT`; review run 4). ADR-061 says
  so, since ADR-056 says other products "read at the marker".
- Trino reports a query's processed input rows (`EXPLAIN ANALYZE` printed `Input: 4 rows` for 2
  visible; the client protocol's statistics likely carry the same, h1 confirms): a reader can count
  the rows a row filter hides, not read them. True of every row-filtered table on Trino (Q2).
- Interactive plans run without table statistics; pipeline principals may switch them on.
- The Lakekeeper UI leaves the host; tables are browsed through Trino.
- Postgres stays on `k2-net` for Airflow; its `lakekeeper` database is reachable only with the
  credential Lakekeeper uses. Separating that role is follow-on `004` ("Lakekeeper on the Postgres
  superuser").

**Runner-up: (b2).** It keeps time travel, `as_of` and structurally tested products with no
amendment, at a rewrite of every output per build. Taken only if the gate fails.

**Strongest objection.** "The guarantee now rests on a network, not on authentication: anything
that lands on `k2-catalog`, or holds the Docker socket, can forge an owner property, and the drift
check only finds it afterwards."
**Answer.** The members are platform services from this repository or pinned images, and no
third-party image (Grafana, Redpanda Console, cadvisor, Prometheus, statsd) joins. The socket holder
(the Airflow scheduler) is root on the host, so catalog authentication would not stop it either.
The drift check runs at every reconcile and deploy and pages, which bounds a forgery's life to one
run interval, and h3 proves it fires. The fence is the cheapest control that closes B2 for 3.0;
authentication is revisited at the release gate. The earlier objection (a definer view fails open
on a missing policy) is answered by condition 2. Policies live in one rules emitter by exact
name, and hand-written expectations run before every deploy.

**Gate, step S01, before the interfaces step merges:**
`scripts/spikes/2026-11-27/spine-h-tested-data-view/run.sh`, non-zero on any leak.

- **h1, fence matrix.** Condition rules, two products, three readers. Review run 1's matrix, plus:
  a builder replacing the view, a foreign definer view, `SET AUTHORIZATION`, `SHOW STATS` with
  statistics off, `SET SESSION <reader catalog>.statistics_enabled = true` as a reader (denied), and
  a numeric version on the view. It records the processed-rows counters. Pass: every base path
  refused, only the tag's rows with policies, no bounds.
- **h2, polled cycle.** Two outputs, a reader polling every 50 ms: a pass with a multi-table commit;
  a failing test; an added column; a dropped column; a kill between tag commit and view sync, then
  a reconcile. Pass: no poll sees an untested or empty result; no read error under condition 4;
  commit times, n=20.
- **h3, catalog fence.** The forged view and the graft fail to connect from `k2-net`. From inside
  `k2-catalog` they succeed, and the drift check then exits non-zero.

## 2. Interfaces A1 to A13, revised

**A1 Release directory and manifest.** *Was:* root `${K2_RELEASES:-$HOME/k2-releases}`, sealed
`<id>/`, `pointers/<service> -> ../<id>` swapped by `mv -T`, manifest with `needs`. *Changes:*
spike c (host swaps never seen); F4, F5 (fixed project, digests); no legacy emitter. *Revised:*
- Root created once, mounted read-only at `/k2/releases` in every service; never a pointer or a
  release directory. Pointers `trino`, `airflow`, `spark`, `prometheus`, `k2-api`, relative
  (`../<id>`), swapped only inside the `tools` one-shot (the one read-write mount):
  `ln -s ../<id> pointers/.<svc>.<uuid4> && mv -T pointers/.<svc>.<uuid4> pointers/<svc>`.
- A run resolves once (`realpath`) and uses `/k2/releases/<id>/` thereafter.
- Id `<tag>-<sha12>`, sha12 of canonical JSON of `files` plus `images`. Built as `.<id>.tmp`,
  `chmod -R a-w`, renamed. Kept: pointer targets, R-1, any release a 7-day run record names.
- `manifest.json` (schema `platform/k2gen/k2gen/schemas/manifest.schema.json`): `{schema: 1,
  release, tag, commit, builtAt, k2gen, previous, products: [{id, version, status, sha256}],
  files: {path: sha256}, images: {service: ref}, needs: {recreate: [service], trinoRestart,
  dagShapeChange, ddl: [table]}}`. Registry images by `repo@sha256:`; local builds (`k2-airflow`,
  `k2-capture`, `k2-spark-iceberg`, `tools`, `fetch`, `dbt-runner`, `k2-api`) tagged
  `<name>:<image id 12>` at build, verified against `docker inspect .Image`; an image a kept
  release names is never pruned.
- Deploy runs `docker compose -p k2 --project-directory "$K2_HOME" -f
  <release>/compose/docker-compose.yml up -d --no-deps <needs.recreate>`; captures, Redpanda,
  MinIO, Postgres, Lakekeeper are never in `recreate` (S22 recreates the last three once, for the
  network). `deploy-log.jsonl` at the root: `{at, release, from, step, outcome, operator}`.
- Trino starts from `--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc`; rules and password
  file refresh every 5 s through the pointer, rules and groups on separate timers, so
  `release-verify` waits two periods, then runs the drift check. Airflow
  `AIRFLOW__DAG_PROCESSOR__REFRESH_INTERVAL=30`. Prometheus rules through the pointer, reloaded by
  `SIGHUP` (untested: prove in S19). Secrets are env names only, never compiled.

**A2 Document set and provenance.** *Was:* `k2gen docset --tag --out`, `provenance.json`.
*Changes:* the review's lighter form (a function and a record, no class). *Revised:* a product
is `contracts/<domain>/<product>/`: `product.odps.yaml` (ODPS v1.1.0), one `<table>.odcs.yaml`
per table (ODCS v3.2.0, columns inline), `models/*.sql`, `unit_tests.yml`, `grants.yaml`,
optional `seeds/`, `fixtures/`. Schemas: `contracts/schema/odps-json-schema-v1.1.0.json`
(vendored, source and date stated), the existing ODCS file, and `k2-v2.schema.json` for every K2
custom property (`connector`, `schedule`, `terms`, `lifecycleChange`, `serves`, `required`,
`maxLagMinutes`, `build`, `policies`, `backup`, `unit`, `time`, `asOf`). `docset.load(tag: str,
repo: Path) -> DocumentSet` (`documents: dict[path, bytes]`, `provenance`), from `git archive
<tag> contracts/ domains/`, byte-identical on rerun. `provenance.json`: `{source: "git", tag,
commit, tagger, taggedAt, setSha256, documents: [{path, sha256, lastCommit, pr}]}`.

**A3 Compiler and checks.** *Was:* `EMITTERS` list, `k2gen release|check|diff`. *Changes:* no
legacy emitter; registration without a shared list; view SQL and remote signing in outputs.
*Revised:* each emitter is a module in `k2gen/emit/` exposing `emit(model, previous) -> dict[path,
str]`; each check a module in `k2gen/checks/` exposing `check(docs, previous) -> list[Finding]`,
`Finding = {code, severity: error|warning, path, message}`; both found by `pkgutil.iter_modules`, so
no worker edits a list; two emitters returning one path is a compile error. `generate.py` (596
lines) is not split: it is deleted in S23. `k2gen release --set <dir> --previous <dir|none>
--images <images.json> --out <root>` stays pure. "Previous" in CI is the newest `v3.*` tag.

**A4 Connector configuration** (frozen now, built in `002`). *Was:* `pipeline.json` per product
(connector, landing, bronze, build, inputs). *Changes:* ADR-036 revised (per-product landing,
parsing in the fetch container, no shared `source_files`, no global lock); inputs via reader
catalogs (e); keys off argv. *Revised:* A4's JSON with `inputs[].relation` =
`<domain>.<fence>__base.<table>`, `build.outputs` = `<domain>_build.<fence>__base.<table>`,
`bronze[].branch: "audit"` (Spark WAP); the lock is the DAG's `max_active_runs=1`. Parser
`domains/<domain>/parsers/<name>.py`: `parse(data: bytes) -> list[dict]`, one function per audit
returning problems. Fetch exits: 0 landed, 10 unchanged, 20 refused, 30 parse failed (bytes kept).
First task's XCom: `{"release": "<id>", "releasePath": "/k2/releases/<id>"}`. Spark takes keys
from `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` via S3FileIO's default chain; `spark_conf.py`
stops setting `s3.access-key-id` (PySpark passes builder confs to `spark-submit` as argv).

**A5 Landing and bronze.** *Was:* `lake.<fence>.*`, one fixed `audit` branch, in-order publish.
*Changes:* summary keys and name rules pinned. *Revised:* warehouse `k2`, namespace
`<fence>`: `landing_files`, `audit_checks`, `bronze_<name>`; landing key
`s3://k2-landing/<domain>/<product>/<YYYY-MM-DD>/<sha256>.<ext>`, conditional PUT. Snapshot
summary keys `k2.run-id`, `k2.release`, `k2.batch-id`, `k2.source-sha256`. Product names
`^[a-z][a-z0-9-]*$`, no `__`; reserved: `raw bronze silver gold audit scratch dq runs`.

**A6 Run-record table.** *Was:* `governance.run_records.run_record`, A6's columns, "newest
audited" = `max_by` over run records. *Changes:* the tag is authoritative; run records are
history and the drift check's reference. *Revised:* A6's columns; `platform/runs/run_record.sql`:
`CREATE TABLE governance_build.run_records.run_record (...) WITH (partitioning =
ARRAY['day(recorded_at)'])`, written only by `svc.runs`. `audited` = tag moved (dbt) or `main`
fast-forwarded (bronze). The one query (wrapper, k2-api, catalog): `SELECT snapshot_id FROM
<cat>.<ns>."<t>$refs" WHERE name = 'audited'`, else `main` for bronze and platform tables; only
`svc.runs`, `svc.catalog`, `svc.platform` may read `$refs`. Client `platform/runs/k2runs.py`:
`record(conn, **row)`, `audited_snapshot(conn, table) -> int | None`.

**A7 `k2_input` and the wrapper.** *Was:* two dbt invocations; `rollback_to_snapshot` only.
*Changes:* d (one build), f (restore across replace), g and condition 4 (tag move, view rule), a
(unique key), e (inputs via reader catalog). *Revised:*
- `k2_input(product_fence, table)`, spike d's macro as `dbt/macros/k2_input.sql`: no var,
  `source()`; var without the key, compiler error; unit test, `source()`; else `source() FOR
  VERSION AS OF BIGINT '<id>'`. Var: `{"k2_inputs": {"<fence>/<table>": {"snapshot": <id>}}}`.
- `k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id/run_id>`. Env exactly
  `K2_TRINO_HOST K2_TRINO_PORT K2_TRINO_USER K2_PUB_USER K2_RUNS_USER K2_CATALOG_URI K2_RELEASE`;
  files `/run/secrets/{trino_password,pub_password,runs_password,ca.pem}`, read by the wrapper
  only; the dbt subprocess's environment carries only the builder's credentials; `target/` under
  `/spool/<dagId>/<runId>/dbt/`.
- Steps: (1) Reconcile: the drift check; an unfinished run's outputs restored to the tag (no tag:
  `DROP TABLE`), `reconciled`; a tag ahead of the records gets its `audited` row; the view rule of
  step 6 rerun until the view equals C ∩ tagged schema. (2) Resolve inputs once by `$refs`; a
  required input with no tag exits 4. (3) `started`, `snapshot_before`. (4) One `dbt build
  --selector <fence> --vars '<k2_inputs>'` as the builder, unit tests included. (5) Any failure:
  restore every output whose `main` moved, `rolled_back`. Restore: columns at `snapshot_before`
  (`LIMIT 0 ... FOR VERSION AS OF`) equal to current, `rollback_to_snapshot(<id>)`; else `CREATE OR
  REPLACE TABLE t AS SELECT * FROM t FOR VERSION AS OF <id>`. (6) Pass, as `pub_<fence>`: for each
  output whose columns change, `CREATE OR REPLACE VIEW` with C ∩ old ∩ new; one multi-table tag
  commit; `CREATE OR REPLACE VIEW` with C ∩ new where it differs; `audited`. A first publish
  creates the view after the tag. (7) Last stdout line is the XCom: `{"exit": n, "expire":
  [{"table": ..., "ids": [...]}]}`.
- Exits 0 pass, 1 tests failed and restored, 2 build error and restored, 3 restore, tag commit,
  view sync or drift check failed (page), 4 required input never audited.
- Expire (Spark task): `expire_snapshots(table => t, snapshot_ids => array(...), older_than =>
  TIMESTAMP '1970-01-01 00:00:00')`; fail unless `deleted_manifest_lists_count >= len(ids)` (b).
- Incrementals: `unique_key` includes every partition source column; `incremental_predicates` in
  product config is a check error (a: silent duplicates). Partition transforms (`day(ts)`) are
  untested: prove with the first such table (phase `004`).

**A8 Naming, catalogs, writers, network, keys.** *Was:* reader `<domain>` and `<domain>_build`
catalogs, `dbt_<fence>` owning its namespace, `<fence>__<role>`, key env names. *Changes:*
`__base` (g), fence rules (e), signing (b), conditions 1, 3, 5. *Revised:*

| Thing | Name |
|---|---|
| Warehouses | `k2` (landings, bronze), one per domain; each with `remote-signing-enabled: false` in the storage plan (applied in `002`) |
| Catalogs | `lake` read-only over `k2`; `<domain_fence>` reader: `iceberg.security=read_only`, `iceberg.table-statistics-enabled=false`, `allow: read-only`; `<domain_fence>_build` writer, `allow: none` for all but `dbt_.*`, `pub_.*`, `svc.runs`, `svc.platform` |
| Namespaces | `<fence>` reader names (views for `build: dbt` or `code`, tables for `build: platform`); `<fence>__base` built tables |
| Builder `dbt_<fence>` | owner of `<fence>__base` in `_build` only: `SELECT INSERT DELETE UPDATE OWNERSHIP`, no `GRANT_SELECT`; inputs: `SELECT` on input bases by exact name in reader catalogs; nothing on another product in any `_build` |
| Publisher `pub_<fence>` | owner of `<fence>` in `_build` (views only, checked by drift); `SELECT, GRANT_SELECT` on `<domain>.<fence>__base.*` |
| Denied to all | `EXECUTE` on procedures; `authorization` rules (none exist); retention session properties except `svc.platform`; `statistics_enabled` except `dbt_.*`, `pub_.*`, `svc.runs`, `svc.platform`, `svc.catalog` |
| Principals | `dbt_<fence>`, `pub_<fence>`, `svc.runs`, `svc.catalog`, `svc.platform`, `svc.api`, `svc.<consumer>`, `p.<pseudonym>`, canary `k2_release_<sha12>` |
| Groups | `<fence>__<role>` |
| Resource groups | `pipeline`: `dbt_.*`, `pub_.*`, `svc.runs`, `svc.platform`, `svc.catalog`; `interactive`: everyone else, `svc.api`'s impersonated callers included |
| Tag, branch | `audited` on dbt outputs; `audit` on bronze |
| DAG, pools | DAG id `<fence>`; `lake_spark` 1, `fetch` 1, `dbt` 2 |
| Networks | `k2-catalog`: `lakekeeper`, `lakekeeper-migrate`, `prefect-db`, `minio`, `trino`, `spark-iceberg`, `lake-init`, `tools`, `dbt-runner`; Lakekeeper on no other network and no host port. `prefect-db`, `minio`, `trino`, `spark-iceberg` stay on `k2-net` too; `fetch` and third-party images never on `k2-catalog` |
| Storage keys | `K2_SPARK_S3_*` (rw `k2`; get and delete on domain prefixes for expiry), `K2_TRINO_S3_*` (rw domains, read `k2`), `K2_BACKUP_S3_*`, `K2_TOOLS_S3_*` (read only); `MINIO_ROOT_*` for `lake-init` only; fetch none. A8's `K2_MAINT_S3_*` dropped: maintenance runs in the Spark container |

**A9 Access expectations.** *Was:* hand-written rows (visible, masked, minimum age, must not
appear, denied, probes) and a coverage rule. *Changes:* marker, side-door, statistics, ownership
and fence rows. *Revised:* `tests/access/expectations.yaml`, schema
`k2gen/schemas/expectations.schema.json`, A9's rows plus:

```yaml
- {principal: p.canary-delayed, table: governance.access_canary.events, via: [sql],
   visible: [id, region, note, event_ts], masked: [note], minRowAgeMinutes: 20, mustNotAppear: "id = 4"}
- {principal: p.canary-full, table: governance.access_canary__base.events, denied: true}
- {principal: p.canary-full, sql: "SELECT * FROM governance.access_canary.events FOR VERSION AS OF 1", expect: error}
- {principal: p.canary-full, sql: "SELECT * FROM TABLE(governance.system.table_changes('access_canary__base', 'events', 0, 1))", expect: denied}
- {principal: p.canary-full, sql: "SHOW STATS FOR governance.access_canary.events", expect: noBounds}
- {principal: p.canary-full, sql: "SET SESSION governance.statistics_enabled = true", expect: denied}
- {principal: dbt_access_canary, sql: "CREATE OR REPLACE VIEW governance_build.access_canary.events AS SELECT 1 x", expect: denied}
- {principal: p.canary-full, table: governance.access_canary.foreign_view, expect: denied}   # a builder's definer view, planted by the test
- {probe: {from: k2-net, target: "lakekeeper:8181"}, expect: refused}
- {probe: {from: host, target: "127.0.0.1:18181"}, expect: refused}
- {drift: catalog, expect: clean}
```

Coverage (a check): every (view, role with a policy) has a row; every `__base` table has a
`denied` row; every layer denied to consumers has a row. Probes from `fetch` land in `002`.

**A10 Roles, principals, grants, policies, identity.** *Was:* `roles.yaml`, `principals.yaml`
with key hashes, `grants.yaml`, policies in ODCS; identity left to O3. *Changes:* O3 is built
here; publishers added. *Revised:* roles `full_reader`, `delayed_reader: {requires: [delay]}`,
`public_reader`; `principals.yaml`: `[{id, kind: person|service, owner, trinoBcrypt?,
apiKeySha256?}]` **[Superseded: no credential hash is ever committed; see the header.]**, id `^(p|svc)\.[a-z0-9-]+$` or `^(dbt|pub)_[a-z0-9_]+$` (one builder and one
publisher per product, generated with it); `grants.yaml: [{principal, role}]`, one role per
principal per product; policies in `customProperties.policies` as v2 5.1. Identity:
- Trino HTTPS only on 8443 (`http-server.https.enabled=true`, plain HTTP off,
  `internal-communication.shared-secret` from `K2_TRINO_SHARED_SECRET`), published on
  `127.0.0.1:${K2_TRINO_PORT:-8443}`; `password-authenticator.name=file`, `file.password-file`
  = the release's `compiled/trino/password.db` (bcrypt lines from `principals.yaml`, cost 10),
  `file.refresh-period=5s`.
- `make tls` writes a local CA and a server cert (`trino`, `localhost`, `127.0.0.1`) to
  `${K2_SECRETS:-$HOME/k2-secrets}/tls/`; clients mount `ca.pem`.
- `make password PRINCIPAL=<id>` and `make api-key PRINCIPAL=<id>` print a 32-byte random secret
  once (`k2_` prefix for keys) and write only its hash into `principals.yaml` for a pull request **[Superseded: no credential hash is ever committed; see the header.]**;
  service secrets (`dbt_*`, `pub_*`, `svc.*`) go to `${K2_SECRETS}/<id>.password`, mode 600,
  outside any checkout.
- k2-api logs in as `svc.api`; impersonation: deny `(dbt|pub)_.*` and
  `svc\.(runs|platform|catalog|api)`, then allow `svc.api` to become `(p|svc)\..*`.
- Clients moved in this phase: Trino CLI, dbt-trino (`method: ldap`, `cert: ca.pem`), the `trino`
  Python client (`BasicAuthentication`), `scripts/{dbt-test,trino-rules-test,health}.sh`, the
  access test. Lakekeeper stays unauthenticated, fenced by network (A8, ADR-059); authentication
  is revisited at the release gate.

**A11 Catalog JSON.** *Was:* one document per product, rendered by k2-api and the site.
*Changes:* readers' names only. *Revised:* A11's document, schema
`k2gen/schemas/catalog-product.schema.json`; `outputs[].relation` is the reader name; base names
never appear; run-time facts stay out of the release.

**A12 REST.** *Was:* slice-1 routes on `127.0.0.1:8095`, keys by sha256, 401/403/404, data read
by audited id. *Changes:* every `/v1` route frozen now; data reads the view; `as_of` deferred.
*Revised:* `services/k2-api/openapi.yaml` (OpenAPI 3.1): A12's routes plus `007`'s `cursor`,
`K2-Product-State`, `Deprecation`, `Sunset`, 410. `/v1/data` queries the reader view as the caller;
`snapshot` is the `$refs` id read before and after the query (equal, else one retry). `as_of` is
declared with a `422 as_of_unavailable` answer, so `007` only adds where it is offered. Any form
that reads a base must not make k2-api decide access (ADR-056 "never decides access"), so it is
the owner's call (Q1).

**A13 One-shot containers.** *Was:* `fetch`, `dbt-runner`, `tools` declared in compose, run by a
`OneShot` operator. *Changes:* `tools` needed now for swaps and the drift check. *Revised:* stubs
under profiles, the default budget unchanged: `tools` (profile `tools`, 0.25 / 256M; in `001` a
shell, Python and the `trino` client; on `k2-net` and `k2-catalog`), `fetch` (0.5 / 512M, `k2-net`
egress only), `dbt-runner` (0.5 / 768M, 2 replicas, `k2-catalog` for the tag commit), `k2-api`
(0.5 / 512M, profile `api` until `003`). Images for `fetch` and `dbt-runner` are `002`'s.

**Backup set** (emitted in `001`, consumed in `002`): `backup-set.json` `{release, entries:
[{kind: landing, bucket, prefix} | {kind: table, table, mode: restore|rebuild, ref: audited|main}
| {kind: lakekeeper-db} | {kind: releases}]}`; views, owners and tags travel in the Lakekeeper
dump; a restore is followed by the drift check, then `access-verify`.

**Shared-file split.**

| File | Rule |
|---|---|
| `Makefile` | S00 adds `include mk/*.mk`; each step owns one `mk/<area>.mk` |
| `docker-compose.yml` | S00 (`name: k2`, stubs), S09 (W1), S22 (W4); nobody else |
| `.github/workflows/ci.yml` | S21 only |
| `k2gen/cli.py` | S00 adds all four subcommand stubs; each fills its own module |
| `contracts/schema/`, `k2gen/schemas/` | S00 only |
| `platform/k2gen/tests/fixtures/v2/` | S00 the good set; each check step adds `bad/<its check>/` only |
| `scripts/trino-rules-test.sh` | S12 writes it; S13 adds TLS and passwords after S12 merges |
| `scripts/lib/throwaway-lake.sh` | S12; S20 reuses it unchanged |
| `scripts/drop.sh`, `CLAUDE.md`, `SECURITY.md` | S22 only |
| index files, `CHANGELOG.md` | one appended row; changelog by the orchestrator |
| ADR numbers | 058 releases and deploy, with ADR-042 revised (S19); 059 catalogs, builders and publishers, layout, the `k2-catalog` network (S12; supersedes ADR-044); 060 principals, grants, policies, identity (S13); 061 tested data through a view over a tag (S02; ADR-056 gains an Outcome line); 062 dbt per product (`002`) |

## 3. Phase 001 build breakdown, revised

Sizes: S under 300 changed lines, M to 700, L split.

| Id | Title (N1 row) | Files | Deps | Proof | Size | Risk |
|---|---|---|---|---|---|---|
| S01 | gate `spine-h-tested-data-view`: h1, h2, h3 (new); **first step** | `scripts/spikes/2026-11-27/spine-h-tested-data-view/`, a dated research note | none | `run.sh` exit 0 | M | decides O8 |
| S02 | ADR-061 tested data through a view over a tag; ADR-056 Outcome line (new) | `docs/adr/ADR-061-*.md`, ADR-056 (append only) | S01 | `make check-docs` | S | owner-facing |
| S03 | ADR-055 to ODPS/ODCS; ADR-036 revised (N1-6, N1-1) | both ADRs | none | `make check-docs` | M | |
| S00 | interfaces PR (N1-7 and new) | schemas A1 to A13, `model.py` types, `emit/`, `checks/`, cli stubs, OpenAPI, `k2_input.sql`, `run_record.sql`, `mk/`, compose stubs, `tests/fixtures/v2/` | S01 pass, S02 | `make test-k2gen` (fixtures valid; a removed required field fails); `docker compose config -q`; budget 16.70 / 28.688 | L | all depend |
| S04 | document model, docset (N1-8) | `k2gen/{model,docset}.py` | S00 | fixture tag twice, `diff -r` empty | M | |
| S05 | checks: documents (N1-16) | `checks/docs.py`, unique-key rule | S00 | `make test-k2gen` | M | |
| S06 | checks: versions (N1-17) | `checks/versions.py` | S00 | same | M | |
| S07 | checks: access, SQL (N1-18) | `checks/{access,sql}.py`: product definer or materialized view, `env_var`, foreign `source()`, `incremental_predicates` are errors | S00 | failing fixtures | M | |
| S08 | compiler, diff (N1-15) | `k2gen/{release,diff}.py` | S00 | two compiles, one id; tree read-only | M | critical |
| S09 | Alertmanager to Slack (new) | `docker/alertmanager/`, compose, `prometheus.yml`, `mk/alerts.mk`, ADR-010 Outcome, runbook | S00 | `amtool alert add`; sent counter up; budget 16.80 / 28.750 | S | |
| S10 | canary and `hot-gold` products (N1-30 part) | `contracts/governance/access-canary/`, `contracts/market-analytics/hot-gold/` | S00 | schema-valid; checks after S05 | S | |
| S11 | emit Trino etc, reader statistics off (N1-21) | `emit/trino_etc.py` | S04, S08 | compiled etc starts | M | |
| S12 | emit rules, conditions 1 and 3, throwaway lake; ADR-059 (N1-22) | `emit/trino_rules.py`, `scripts/trino-rules-test.sh`, `scripts/lib/throwaway-lake.sh`, ADR-059 | S11 | `make test-trino`: e's 40 statements, h1's matrix | M | highest |
| S13 | identity; ADR-060 (new) | `emit/trino_identity.py`, `scripts/tls.sh`, `mk/identity.mk`, `dbt/profiles.yml`, `scripts/{dbt-test,health}.sh`, `scripts/verify/identity.sh`, then `trino-rules-test.sh` | S11, S12 merged | no password, wrong password, plain HTTP refused; every client connects | M | high |
| S14 | principals incl. `pub_`, grants, keys (N1-35, N1-26 part) | `contracts/access/`, `scripts/principal-secret.py`, `mk/principals.mk`, `emit/api.py` | S04 | secret printed once, hash only | S | high |
| S15 | emit lake, generated view SQL (condition 2), storage plan, backup set (N1-23) | `emit/lake.py` | S04, S08 | golden files; view SQL is one projection | M | high |
| S16 | emit pipelines, DAG list, one-shots (N1-24) | `emit/pipeline.py` | S04, S08 | golden files | S | |
| S17 | emit dbt tree, invoker views (N1-25) | `emit/dbt.py`, `mk/dbt.mk` | S04, S08 | `dbt parse` of the fixture tree | M | |
| S18 | emit catalog JSON (N1-26) | `emit/catalog.py` | S04, S08 | schema-valid golden; no `__base` | S | |
| S20 | access-verify, marker check, catalog drift check (N1-36) | `tests/access/`, `scripts/access_verify.py`, `scripts/verify/{tested-data-marker,grant-refresh,resource-groups}.sh`, `scripts/verify/catalog_drift.py`, `mk/access.mk`, runbook | S12, S14, S15 | `make access-verify` on the throwaway lake; drift check non-zero on a planted view | M | high |
| S19 | deploy, verify, rollback; ADR-058 (N1-20) | `scripts/deploy.py`, `docker/tools/`, `mk/release.mk`, `tests/test_deploy.py`, runbook | S08, S11, S13; merges after S20 | forced mismatch restores R-1, exit 1; release-verify runs the drift check; Prometheus reload via pointer | M | critical |
| S21 | CI validation runner (N1-38) | `ci.yml` | S05 to S07, S20 | a passing and a failing fixture PR | M | |
| S22 | stack runs from releases, identity live, `k2-catalog` network (N1-32, N1-40 part) | compose, `scripts/drop.sh`, `scripts/verify/catalog-fence.sh`, `CLAUDE.md`, `SECURITY.md`, current docs naming 18181 | S19, S20 | rehearsal on project `k2-rehearsal`; `make deploy RELEASE=v3.0.0-alpha.3-rc.1 && make release-verify`; `rules-only-deploy.sh`; `catalog-fence.sh` (refused from Grafana, Redpanda Console and the host, open from `tools`); the `git grep 18181` over current docs prints nothing | M | critical, live |
| S23 | generated files leave git (N1-39) | delete `generate.py`, old validator and `k2.schema.json`, `docker/lake/{generated,ddl/generated}`, `docs/products/`, dbt `_sources.yml`, `selectors.yml`, Trino `rules.json`, catalogs, `test/local/`, `make generate`, the drift step | S22 | `git ls-files ...` prints 0; `make test` | M | |
| S24 | chapters 21, 22; audit; tag (N1-43) | docs; ADR-028 Outcome (s8); ADR-044 status line | all | plan `001` verification block | M | |

**Waves, re-checked for disjoint files after the changes.**

| Wave | Steps | Shared-file notes |
|---|---|---|
| W0 | S01 first; S03 beside it; S02 after S01; S00 after S01 passes and S02 | S02 alone appends to ADR-056 |
| W1 | S04 to S10 | S09 is the only compose and `prometheus.yml` editor; checks touch only their own module and `bad/` fixtures; S10 names canary principals that S14 declares |
| W2 | S11 to S18; S12 after S11; S13 after S12 | one emitter module each; `config.properties` is S11's (A10's HTTPS lines from the frozen spec), S13 emits only the authenticator files; `trino-rules-test.sh` S12 then S13 |
| W3 | S20, then S19 and S21 | S19 calls S20's drift script; `docker/tools/` is S19's |
| W4 | S22 | sole compose, `drop.sh`, `CLAUDE.md`, `SECURITY.md` editor |
| W5 | S23, then S24 | |

25 steps, 6 waves. **Critical path:** S01, S02, S00, S08, S11, S12, S13, S20, S19, S22, S23,
S24; S04 runs beside S08 and gates every emitter. **Extra adversarial review:** S01 and S02 (O8),
S00, S12, S15 (view SQL), S13 and S14 (identity, secrets), S20 (non-circular proof, drift), S19
and S22 (deploy, rollback, live cutover, network).

**N1 rows that no longer apply or moved.**

| Row | Now |
|---|---|
| N1-1 | #239 closes unmerged; ADR-036 revised in S03; ported in `002` |
| N1-2 to 6 | numbers shifted (057 taken); each ADR lands with its building step |
| N1-8 | "old validator skips ODPS directories" dropped: no old contract remains |
| N1-10 | `tools` and stubs here; `fetch`, `dbt-runner` images and key env lines `002` |
| N1-11 | `002`, plus remote signing off; its `generate.py` edit dropped (file deleted) |
| N1-12 | dropped: `notebooks/` archived with the old ingest, back in `004` |
| N1-13 | `002`, `backup-lake.sh` only: parity and replay scripts are gone; `drop.sh` moves to `k2-catalog` in S22 |
| N1-14 | macro in S00 (spike d's file), exercised in `002` |
| N1-15, N1-20 | S08 without the `legacy` emitter; S19 parsing dbt via `dbt/` until `dbt-runner` |
| N1-32 | S22, shrunk: `lake_script.py`, `lake.py`, `dbt_marts.py` are archived and no DAG runs, so no XCom retrofit (`resolve_release` comes with `002`'s factory); `PYTHONDONTWRITEBYTECODE=1` answers the `./docker/lake` write question |
| N1-40 | split: Lakekeeper off the host and onto `k2-catalog` in S22; MinIO's ports and the root rotation stay in `002` |
| N1-9, 19, 42 | `003` |
| N1-27 to 31, 33, 34, 37, 41 | `002` (`hot-gold` in S10) |
| N1-36, 39, 43 | S20 (SQL only), S23 (plus `generate.py`), S24 |

Budget at the exit: 16.80 CPU / 28.750 GiB (phase `000` also retired `lake-metrics` and `lake-ddl`,
so plan `001`'s 17.40 / 29.875 is stale and should be corrected in S09).

## 4. Open questions for the owner

| # | Question | Recommended default |
|---|---|---|
| Q1 | REST `as_of` (phase `007`): cut it on tables that carry a policy, or let k2-api read a base by audited id under a separate identity, accepting that k2-api then decides which ids were tested? | Cut it where a policy applies; on policy-free tables accept k2-api choosing the audited id, with an access-verify row that an `as_of` before a rollback returns no failed row |
| Q2 | A reader can count the rows a row filter hides (processed-rows statistics), on any design. Acceptable for `delayed_reader`? | Accept for 3.0, stated in ADR-061; revisit before a delayed product is licensed outside the owner's team |
| Q3 | May the orchestrator push `v3.0.0-alpha.3-rc.N` tags for the deploys before the phase tag? | Yes; phase tags by the owner |
| Q4 | Credential hashes in git (`principals.yaml`: bcrypt for Trino, sha256 for keys), rotation by pull request? | Yes: reviewed, compiled into the release, secrets never in git. **[Superseded: no credential hash is ever committed; see the header.]** |

## Appendix: worker briefs, waves 1 and 2

Every brief assumes: read this file's rulings, section 2 and the merged interfaces PR (S00); branch
`feat/<slug>`, conventional commits, one PR; `make test` plus the proof; no live-stack action; never
edit `CHANGELOG.md`; a frozen schema or `model.py` type changes only through the orchestrator.

**S04, document model and docset.** Fill the bodies of the dataclasses S00 froze in
`platform/k2gen/k2gen/model.py`: load ODPS product files, ODCS contracts, `grants.yaml`, and
`contracts/access/*.yaml` into the model, validating against the frozen schemas. Add
`platform/k2gen/k2gen/docset.py`: `load(tag, repo) -> DocumentSet` from `git archive <tag>
contracts/ domains/`, byte-identical on rerun, writing `provenance.json` per A2. Tests:
`tests/test_model.py`, `tests/test_docset.py`, using a temporary git repository. Proof: `make
test-k2gen`; `k2gen docset --tag <fixture tag> --out a` twice, then `diff -r` empty. Do not touch
`cli.py` (its stub already calls you), `emit/`, `checks/`, `contracts/schema/`, or `generate.py`.

**S05, checks: documents.** Write `platform/k2gen/k2gen/checks/docs.py` exposing `check(docs,
previous) -> list[Finding]`. It covers: both schemas validate; product names unique,
`^[a-z][a-z0-9-]*$`, no `__`, not reserved (A5); one producer per table; every column described;
terms before a non-owner grant; and the unique-key rule (A7: for an incremental `build: dbt` table,
`primaryKey` holds every partition source column). One failing fixture per finding code under
`platform/k2gen/tests/fixtures/v2/bad/docs/`. Proof: `make test-k2gen`, each bad fixture yields its
code. Do not touch other check modules, the good fixture set, `model.py` types, or `cli.py`.

**S06, checks: versions and transitions.** Write `platform/k2gen/k2gen/checks/versions.py`,
comparing a document set with the previous release's. Allowed lifecycle transitions follow v2 4.5
(`active`, `paused`, `deprecated`, `retired`; `retired` only after `deprecated`). A drop, retype,
rename or re-key of a column is a major version. There is no retirement while another product
declares the table as an input. `previous` may be `None` (first release: every product is new).
Fixtures go in pairs under `platform/k2gen/tests/fixtures/v2/bad/versions/<case>/{previous,next}/`.
Proof: `make test-k2gen`. Do not touch other check modules, the good fixtures, `model.py` types,
or anything outside `checks/` and your fixture directory.

**S07, checks: access and the SQL lint.** In `platform/k2gen/k2gen/checks/access.py`: grants name
declared roles and principals; one role per principal per product; an output's policy at least as
strict as each input's (v2 4.6 table); coverage per A9 (every view and role with a policy, every
`__base` table, every denied layer has an expectation row). In `checks/sql.py`, errors: `env_var`,
a definer or materialized view in product SQL or config, `incremental_predicates`, `source()` of
another product outside `k2_input`. Warnings: `run_query`, `statement`, `call`, `adapter.`, and any
other relation outside `source`, `ref` and `k2_input`. Fixtures under `bad/access/` and `bad/sql/`.
Proof: `make test-k2gen`. Do not touch the rules emitter (S12) or `tests/access/` (S20).

**S08, compiler and diff.** Write `platform/k2gen/k2gen/release.py`: `release(set_dir, previous,
images, out_root) -> id`. It builds `.<id>.tmp`, runs every module in `k2gen.emit` found by
`pkgutil.iter_modules`, and refuses two emitters writing one path. It writes `manifest.json` per
A1: `files` hashes, `images` from the `--images` input, and `needs` against the previous release.
Then `chmod -R a-w` and an atomic rename. Write `platform/k2gen/k2gen/diff.py`, a file-level and
storage-plan difference of two releases, printed as Markdown for a CI job summary. Proof: compiling
the fixture set twice gives one id; the tree is read-only; the manifest validates. Do not touch
any emitter, `scripts/deploy.py`, or Docker files.

**S09, Alertmanager to Slack.** Add `alertmanager` v0.35.0 by digest (0.1 CPU, 64M) to
`docker-compose.yml`; you are its only editor in this wave. In `docker/alertmanager/alertmanager.yml`,
route to Slack through `api_url_file`: the webhook URL lives in a git-ignored file under
`${K2_SECRETS}`, never in the repository. Also: the `alerting:` block in
`docker/prometheus/prometheus.yml`; `mk/alerts.mk`; `tests/test_check_budget.py`; the compose
summary comment and the ADR-010 Outcome line; `docs/runbooks/where-an-alert-goes.md`, every command
run first. Proof: `amtool alert add K2TestAlert severity=info`; the Slack sent counter rises and
the failed counter does not; `python3 scripts/check-budget.py` prints 16.80 / 28.750. Do not touch
other services.

**S10, canary and `hot-gold` products.** Write `contracts/governance/access-canary/`:
`product.odps.yaml`, `events.odcs.yaml` (`id`, `region`, `note`, `event_ts`, every column
described), a seed-based model, synthetic seed rows only, and `grants.yaml` giving `p.canary-full`
`full_reader` and `p.canary-delayed` `delayed_reader`. Policies: `delay {event_ts, 20}` and
`mask [note]` for `delayed_reader`. Also write `contracts/market-analytics/hot-gold/product.odps.yaml`:
status `paused`, `lifecycleChange` naming ADR-057, and `serves` naming the paused ClickHouse copy.
Proof: `make test-k2gen` validates both against the frozen schemas; `k2gen check` once S05 merges.
Do not touch `contracts/domains.yaml`, `contracts/access/` (S14 declares the canary principals),
or the schemas.

**S11, emit Trino etc.** Write `platform/k2gen/k2gen/emit/trino_etc.py`, which emits
`compiled/trino/etc/`:
- `config.properties` with A10's HTTPS-only and password-authentication lines, the shared secret as
  `${ENV:K2_TRINO_SHARED_SECRET}`; `jvm.config`; `node.properties`.
- Catalogs: `lake` (read-only over `k2`); per domain a reader (`iceberg.security=read_only`,
  `iceberg.table-statistics-enabled=false`) and a `_build` writer; keys only as `${ENV:...}`.
- `access-control.properties` (5 s refresh, rules through the pointer), `group-provider.properties`
  and the resource groups of A8.

Proof: golden files; the compiled etc starts on a throwaway Trino 484 with a fixture password file.
Do not emit `rules.json`, `groups.txt` (S12) or authenticator files (S13). Do not touch compose.

**S12, rules, conditions 1 and 3, ADR-059.** Write `platform/k2gen/k2gen/emit/trino_rules.py`,
which emits `rules.json` and `groups.txt` per A8:
- Builders own `<fence>__base` with no `GRANT_SELECT`. Publishers own `<fence>` and hold `SELECT,
  GRANT_SELECT` on their own base through the reader catalog.
- Reader rules use exact view names: masks to typed nulls, `delay` and `rowScope` filters. Nothing
  names a base for readers.
- No `authorization` or procedure `EXECUTE` rules. `statistics_enabled` and retention properties go
  to named pipeline principals only. Add the `svc.api` impersonation and the canary.

Also write `scripts/lib/throwaway-lake.sh` (Lakekeeper 0.13.9, Postgres and MinIO on a private
network, spike b's pattern) and `scripts/trino-rules-test.sh`, which runs spike e's 40 statements
and h1's matrix. Write ADR-059. Proof: `make test-trino`. Do not touch identity files.

**S13, identity, ADR-060.** Write `platform/k2gen/k2gen/emit/trino_identity.py`, which emits
`password-authenticator.properties` and `password.db` from `principals.yaml` bcrypt lines. **[Superseded: no credential hash is ever committed; see the header.]**
Also:
- `scripts/tls.sh` and `mk/identity.mk`: `make tls` writes a local CA and a server cert to
  `${K2_SECRETS}/tls/`, never the repository.
- Clients move to HTTPS and passwords: `dbt/profiles.yml` (`method: ldap`, `cert`),
  `scripts/dbt-test.sh`, `scripts/health.sh`. Once S12 merges, `scripts/trino-rules-test.sh`.
- `scripts/verify/identity.sh` and ADR-060.

Proof: on a throwaway Trino started from the compiled etc, no password, a wrong password and plain
HTTP are refused; the CLI, dbt-trino and the Python client connect. Do not touch
`config.properties` (S11), `principals.yaml` (S14) or compose (S22 makes it live).

**S14, principals, grants and keys.** Write `contracts/access/{roles,principals}.yaml` per A10,
declaring:
- `dbt_access_canary` and `pub_access_canary`;
- `svc.runs`, `svc.catalog`, `svc.platform`, `svc.api`, `svc.release-canary`;
- `p.canary-full` and `p.canary-delayed`.

Write `scripts/principal-secret.py` and `mk/principals.mk`: `make password PRINCIPAL=<id>` and `make
api-key PRINCIPAL=<id>` print a 32-byte secret once and write only its bcrypt or sha256 to
`principals.yaml`. **[Superseded: no credential hash is ever committed; see the header.]** Service secrets go to `${K2_SECRETS}/<id>.password`, mode 600. Write
`emit/api.py`, which emits `compiled/api/principals.json`. Proof: the secret appears once on stdout;
`git grep` for it finds nothing; golden key map. Do not touch emitters other than `emit/api.py`, or
product `grants.yaml`.

**S15, lake outputs and the generated view SQL.** Write `platform/k2gen/k2gen/emit/lake.py`,
which emits:
- DDL for Spark and platform tables under `compiled/lake/ddl/`.
- Per dbt output, `compiled/trino/views/<domain>.<fence>.<table>.sql`, exactly `SELECT <contract
  columns> FROM <domain>.<fence>__base.<table> FOR VERSION AS OF 'audited'` and nothing else
  (condition 2).
- `compiled/trino/views.json`, holding each view's columns and its owner `pub_<fence>`; the drift
  check and the wrapper's view rule read it.
- `contract_schemas.json` and `retention.json`.
- `storage-plan.json`, with warehouses at `remote-signing-enabled: false` and both namespaces.
- `backup-set.json`.

Proof: golden files; a test that each view file parses as one projection with no predicate or
function. Do not touch rules or dbt emitters.

**S16, pipelines, DAG list, one-shots.** Write `platform/k2gen/k2gen/emit/pipeline.py`, which
emits:
- `compiled/products/<id>/pipeline.json` (A4);
- `compiled/airflow/dags.json`;
- `compiled/oneshots.json`: per kind the image digest from the manifest inputs, limits, the A7 and
  A4 env allow-lists, and networks. `fetch` is never on `k2-catalog`; `dbt-runner` and `tools`
  are on it (A13).

Validate against `pipeline.schema.json`. In `001` no real product has a connector, so cover the
fixture set's synthetic ones. Proof: golden files and schema validation in `make test-k2gen`. Do not
write DAG code (the factory is `002`'s), and do not touch compose or `docker/airflow/`.

**S17, the dbt tree.** Write `platform/k2gen/k2gen/emit/dbt.py`, which emits `compiled/dbt/` per
release:
- Models under `models/<catalog>/<product>/`.
- `dbt_project.yml` with `+view_security: invoker` and `on_table_exists: replace`.
- A naming macro writing `<domain>_build.<fence>__base`.
- Sources: input ports on the reader catalog base, own bronze on `lake`.
- Model YAML from ODCS: `contract: {enforced: true}`, `unique_key` from `primaryKey`, tests scoped
  by `where`.
- One selector per product, seeds, and `k2_input.sql` copied.

Add `make dbt-dev` (symlink tree) in `mk/dbt.mk`. Proof: `dbt parse` of the compiled fixture tree
with the `dbt/` uv project; golden files. Do not touch `dbt/profiles.yml` (S13) or the repository's
`dbt/dbt_project.yml`.

**S18, catalog JSON.** Write `platform/k2gen/k2gen/emit/catalog.py`, which emits
`compiled/catalog/index.json` and `compiled/catalog/products/<id>.json` per A11. Each holds reader
names only, never a `__base` name. It includes owner, purpose, state, columns with descriptions,
roles with their policies in words, readers from grants, lineage nodes and edges from ports,
examples, join keys and the dbt docs link. Validate against `catalog-product.schema.json`. No HTML:
the site renders in `003`. Proof: golden files; a test that no emitted byte contains `__base`; schema
validation in `make test-k2gen`. Do not touch k2-api, `emit/api.py` (S14) or any other emitter.

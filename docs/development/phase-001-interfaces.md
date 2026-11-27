# Phase 001 interfaces: what is frozen, where, and who may change it

Frozen on 2026-11-26 by the interfaces step (S00) of
[phase 001](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md), before
any parallel building, so that two steps on either side of an interface build against one
definition. The step ran in three parts: **S00a** froze the document schemas, the k2gen types
and the fixtures (their page is [`platform/k2gen/INTERFACES.md`](../../platform/k2gen/INTERFACES.md));
**S00b** froze the shared files and the wiring listed here; **S00c** finalised both after an
independent review of the two and the gate
[`2026-11-26-tested-data-view-gate.md`](../research/2026-11-26-tested-data-view-gate.md), whose two
amendments ADR-061 records. The design inputs, dated, are in
[`2026-11-26-phase-001-interfaces.md`](../research/2026-11-26-phase-001-interfaces.md).
**S00d** later patched both additively, once the building steps had reported their gaps (the
last section of the k2gen page). The interfaces A1 to A13 are those of
[IMPLEMENTATION-N0-N1.md](../research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md)
section A, as revised for phase 001.

Everything here is **target state**. Nothing in the running stack reads these files yet: the
compose stubs are behind profiles nothing starts, the new `.env.example` names are commented
out, the macros are called by no model, and each `make` stub says `NOT BUILT` and exits 2. Each
step that makes one real updates the documents describing the running system in that same change.

**The rule.** A frozen file changes only through the step that owns it (the tables below). A
change to a frozen interface itself goes through the orchestrator, and every step that depends
on it is told.

## A1 to A13: the files that freeze them

| | Interface | Frozen in (shared files) | Frozen in (k2gen) | Built by |
|---|---|---|---|---|
| A1 | Release directory, manifest, pointers; the releases root mounted once, read-only, at `/k2/releases`; the deploy log | `docker-compose.yml` (`x-k2-releases-ro`, the `tools` read-write mount), `.env.example` (`K2_RELEASES`, `K2_HOME`), `mk/release.mk` | manifest and deploy-record schemas; the release paths, `NEEDS_BY_PREFIX` | S08, S19, S22 |
| A2 | Document set and provenance; the tag's `docker-compose.yml` is a document | | `platform/k2gen/INTERFACES.md` | S04 |
| A3 | Compiler and checks: one module per emitter and per check | | `platform/k2gen/INTERFACES.md` | S05 to S08, S11 to S18 |
| A4 | Connector configuration, the DAG list, one-shots; parser, fetch | the run-time contracts below | pipeline, dags and oneshots schemas | S16; phase 002 |
| A5 | Landing and bronze names, the platform tables' columns | | `model.PLATFORM_TABLES` | S15; phase 002 |
| A6 | The run-record table | `platform/runs/run_record.sql` | run-record schema | S15 emits its DDL, S19 applies it; phase 002 writes it |
| A7 | `k2_input`, the results hook, the dbt wrapper | `dbt/macros/k2_input.sql`, `dbt/macros/k2_results.sql` (stub), `dbt/tests/k2_input/`, `.env.example` (the wrapper's names, never set there), the run-time contracts below | | phase 002 |
| A8 | Naming, catalogs, the `k2-catalog` network, storage keys | `docker-compose.yml` (`k2-catalog`), `.env.example` (`K2_*_S3_*`) | `platform/k2gen/INTERFACES.md` | S11, S12, S22; keys phase 002 |
| A9 | Access expectations | `mk/access.mk` | expectations schema | S20 |
| A10 | Roles, principals, grants, policies, identity | `mk/identity.mk`, `mk/principals.mk`, `.env.example` (`K2_SECRETS`, `K2_TRINO_SHARED_SECRET`), `.gitignore`, the secrets table below | principals and grants schemas, `model.SECRET_FILES` | S13, S14, S22 |
| A11 | Catalog JSON, one document per product | `services/k2-api/openapi.yaml` (cites it) | catalog schema | S18 |
| A12 | REST: every `/v1` route | `services/k2-api/openapi.yaml` | | phase 003; phase 007 completes it |
| A13 | One-shot containers | `docker-compose.yml`: `tools`, `fetch`, `dbt-runner`, `k2-api` | oneshots schema | S19 (`tools`), phase 002, phase 003 |

The tests that hold them: `platform/k2gen/tests/test_interfaces.py` (`make test-k2gen`),
`tests/test_frozen_interfaces.py` (the OpenAPI document under a pinned validator; the run-record
DDL under a pinned parser, and equal to its JSON schema), `tests/test_secrets_untracked.py`,
`tests/test_mk_split.py`, `scripts/dbt-test.sh` step 4 (`k2_input`'s render cases), and
`python3 scripts/check-budget.py` (the stubs' limits and pins; the stubs leave the default
bootstrap peak unchanged, 16.80 CPU / 28.750 GiB with S09's Alertmanager).

## Who owns which shared file

| File | Owner | Wave |
|---|---|---|
| `Makefile` | S00b (the `include mk/*.mk` line); nobody after | W0 |
| `mk/alerts.mk`, `mk/identity.mk`, `mk/principals.mk`, `mk/dbt.mk`, `mk/access.mk`, `mk/release.mk` | S09, S13, S14, S17, S20, S19; no mk file names a target of the root Makefile or of another mk file (`tests/test_mk_split.py`) | W1, W2, W2, W2, W3, W3 |
| `docker-compose.yml` | S00b (stubs), then S09 alone, then S22 alone | W0, W1, W4 |
| `.github/workflows/ci.yml` | S21 only | W3 |
| `platform/k2gen/k2gen/cli.py`, `model.py`, `emit/__init__.py`, `checks/__init__.py` | S00a, S00c; each step fills its own module | W0 |
| `contracts/schema/` | S00a, S00c only | W0 |
| `platform/k2gen/tests/fixtures/v2/` | S00a, S00c the good set and `bad/schema/`; each check step adds only `bad/<its check>/` | W0, W1 |
| `platform/k2gen/tests/golden/` | S00c the marker `README.md`; each emitter step only `<its emitter>/` | W2 |
| `scripts/dbt-test.sh` | S00b, S00c (step 4), then S13 (TLS and passwords) | W0, W2 |
| `scripts/check_docs.py` gate (i) | S00c (the golden and seed passes); nobody else in phase 001 | W0 |
| `scripts/trino-rules-test.sh` | S12, then S13 after S12 merges | W2 |
| `scripts/lib/throwaway-lake.sh` | S12; S20 reuses it unchanged | W2, W3 |
| `scripts/drop.sh`, `CLAUDE.md`, `SECURITY.md` | S22 only | W4 |
| `.env.example` | S00b named every variable; each owning step uncomments its own line | any |
| Index files, `CHANGELOG.md` | one appended row each; the changelog by the orchestrator | any |
| ADR numbers | 058 (S19), 059 (S12), 060 (S13), 061 (S02), 062 (phase 002) | |

## Local secrets

Every credential is generated on the host into `${K2_SECRETS:-$HOME/k2-secrets}`, outside any
checkout, and never committed, compiled into a release or hashed into one: no document, schema
or release file has a field for a secret or a checksum of one. Deploy records each
secret file it found, by mode and size, in its own deploy record (`deploy-record.schema.json`, one line
per step of `<releases root>/deploy-log.jsonl`), never the content nor a digest of it (step S19b). A container mounts only the files it
reads, read-only, under `/run/k2-secrets/`. A file refreshed while its reader runs is mounted by
its directory and rewritten in place under an exclusive `flock` on the file itself, never renamed
over: through Docker Desktop's file sharing a container keeps reading the old file after a
rename, even under a directory mount (measured by S09 and S14, `scripts/principal-secret.py`).
Every writer of such a file takes that lock and writes in place, S19's canary line in
`password.db` included. The others are read at start, mounted as files, and a rotation
recreates their readers. The table is `model.SECRET_FILES` in code; `platform/k2gen/INTERFACES.md` repeats it,
and `tests/test_interfaces.py` holds both pages to the code.

| Under `${K2_SECRETS}` | What | Read-only in a container at | Mounted by | Written by |
|---|---|---|---|---|
| `trino/password.db` | Trino's file authenticator file: one `<user>:<bcrypt, cost 10>` line per principal with a password, the canary's included | `/run/k2-secrets/trino/password.db` (the directory; Trino rereads it every 5 s) | trino | S14 `scripts/principal-secret.py` (`make password`, `make principal-remove`); S19 `scripts/deploy.py` adds each release's canary line, in place under the same `flock`, never by a rename |
| `api/keys.json` | k2-api's key map, `{"<sha256 hex of the key>": "<principal id>"}` | `/run/k2-secrets/api/keys.json` (the directory) | k2-api | S14 `scripts/principal-secret.py` (`make api-key`) |
| `principals/<principal>.password` | one service principal's password, mode 600 | `/run/k2-secrets/<role>.password`: `builder` for `dbt_<fence>`, `publisher` for `pub_<fence>`, `<name>` for `svc.<name>` (`model.password_mount`) | dbt-runner (builder, publisher, runs, for one run), k2-api (api), tools | S14 `scripts/principal-secret.py` (`make password`) |
| `tls/ca.pem` | the local CA's certificate | `/run/k2-secrets/tls/ca.pem` | tools, dbt-runner, k2-api; the Trino CLI on the host reads it in place | S13 `scripts/tls.sh` (`make tls`) |
| `tls/ca.key` | the CA's private key | never mounted | | S13 `scripts/tls.sh` |
| `tls/trino.pem` | Trino's server key and certificate in one PEM file (`http-server.https.keystore.path`) | `/run/k2-secrets/tls/trino.pem` | trino | S13 `scripts/tls.sh` |
| `alertmanager/slack-webhook-url` | the Slack webhook URL, Alertmanager's `api_url_file`; mode 600, in a directory of mode 700 | `/run/k2-secrets/slack-webhook-url` (S09 mounts the directory `alertmanager/` there, and only it) | alertmanager | S09 `make alerts-install-webhook` |
| `canary/<canary>.password` | the password of the release canary `k2_release_<setSha256[:12]>` | never mounted: `release-verify` reads it on the host | | S19 `scripts/deploy.py`, with its `password.db` line |

The only principal-like name with one spelling and no declaration is the canary,
`k2_release_<setSha256[:12]>` (`model.canary_user`): S12's rules admit it, S19 writes its line,
and `contracts/access/principals.yaml` does not list it. Environment secrets
(`K2_TRINO_SHARED_SECRET`, the `K2_*_S3_*` key pairs) stay in `.env` and are named, never
compiled. A copy that lands inside a checkout is kept out all the same: `.gitignore` ignores
`k2-secrets/` and `*.password` at any depth, and `tests/test_secrets_untracked.py` fails if
either line goes or a matching file is tracked.

## Run-time contracts, frozen for phase 002

Phase 002 builds these; phase 001 freezes them so that the compiler (S16's `pipeline.json`,
`dags.json`, `oneshots.json`) and the code that reads them agree. They come from A4 and A7 of the
proposal and [ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md).

**The DAG.** One per product with a schedule, id `<fence>`, `max_active_runs=1` (no global lock),
its tasks in the order of `dags.json`. The first task, `resolve`, takes `realpath` of
`/k2/releases/pointers/airflow` once and hands the rest, by XCom,
`{"release": "<id>", "releasePath": "/k2/releases/<id>"}`; no task reads the pointer again.
Pools: `lake_spark` 1, `fetch` 1, `dbt` 2.

**The fetch container** (file and API kinds) works in the run's spool, `/spool/<dagId>/<runId>/`.
It writes the bytes as they arrived, `raw.<ext>`, and the fetch record `fetch.json` before it
parses anything, then the parsed rows, `rows.jsonl`. `fetch.json` is
`{"url", "finalUrl", "sha256", "bytes", "fetchedAt", "httpStatus"}`: the URL fetched with `{{ds}}`
resolved and never a credential, the URL after redirects, the bytes' sha256, their size, the UTC
time and the final status. It holds no storage key and is never on `k2-catalog`. Its exit codes:

| Exit | Meaning | Left in the spool |
|---|---|---|
| 0 | fetched and parsed; the Spark task lands it | `raw.<ext>`, `fetch.json`, `rows.jsonl` |
| 10 | unchanged: the same bytes, or an API response unchanged but for its volatile fields | nothing new |
| 20 | refused under ADR-036 part 3 (host, redirect, address, size); the message names scheme and host only | nothing |
| 30 | parse failed; the bytes are kept and land, and the batch fails at bronze | `raw.<ext>`, `fetch.json` |

Added 2026-11-27: after a successful parse, fetch also writes `audits.json`,
`{"audits": [{"name", "passed", "failures", "detail"}]}`, one entry per parser audit the bronze
tables declare (`def <audit>(rows: list[dict]) -> list[str]`, in the parser module, run in fetch);
the Spark audit task reads it into `audit_checks`, and a declared audit with no entry fails.

Added 2026-11-27: fetch runs on `k2-egress` alone, a compose network no other service joins (its kind in `oneshots.json` names it, and only fetch may), and sees only `/spool/<dagId>` of the spool volume.

Any other exit is a task error. The Spark task recomputes the sha256 from `raw.<ext>`, refuses a
mismatch, writes the object by conditional PUT to `model.landing_key`, and adds one row to
`lake.<fence>.landing_files` (`model.PLATFORM_TABLES`).

**The dbt wrapper**, run in a `dbt-runner` one-shot:

```text
k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>
```

- Its environment is exactly `K2_TRINO_HOST K2_TRINO_INTERNAL_PORT K2_TRINO_USER K2_PUB_USER
  K2_RUNS_USER K2_CATALOG_URI K2_RELEASE`. `K2_TRINO_INTERNAL_PORT` is Trino's port on the
  network (8443); `K2_TRINO_PORT` is only ever the host's published port. Its passwords are the
  files `/run/k2-secrets/{builder,publisher,runs}.password` and its CA
  `/run/k2-secrets/tls/ca.pem`, read by the wrapper alone. The dbt subprocess's environment
  carries only the builder's credentials, as `dbt/profiles.yml` names them; dbt's `target/` is
  `/spool/<dagId>/<runId>/dbt/`.
  Changed 2026-11-27: dbt's target and logs are under `/tmp` in the container (`/tmp/k2-dbt`,
  the one-shot's tmpfs, `docker/dbt-runner/k2_dbt.py`), never the spool, which `dbt-runner` does
  not mount.
- Its vars to dbt: `k2_inputs`, `{"<fence>/<table>": {"snapshot": <integer id>}}` for every input
  (`dbt/macros/k2_input.sql` refuses a missing, null or non-integer id at parse), and
  `k2_run_id`, which the results hook writes on every test row beside dbt's `invocation_id`.
  Added 2026-11-28 (step W0-7): an entry may instead be `{"absent": true}`, which `k2_input`
  renders as zero rows of the source's declared columns, each cast to its contract type. The
  wrapper passes it only for an optional input whose port says `whenAbsent: empty` (or
  `emptyIntoTable`), while that input is `paused` in the release or was never audited, and
  records it with `snapshot` and `lagMinutes` null and its `state`, so the product is `partial`.
  The default, `whenAbsent: fail`, is the rule above unchanged. `k2gen check` refuses `empty` in
  a product with a model that is not incremental (`docs.empty-input-into-table`), since the zero
  rows would rebuild it and publish it empty.
- Steps: (1) reconcile: the catalog drift check; an unfinished run's outputs restored to their
  tag (no tag: `DROP TABLE`), `reconciled`; a tag ahead of the records gets its `audited` row; the
  view rule of step 6 rerun until each view equals C ∩ the tagged schema. (2) Resolve every input
  once from `t$refs` at `model.tested_ref`, read twice and compared, since one read can straddle a
  publish; a required input never tested exits 4. (3) `started`, `snapshot_before`. (4) One
  `dbt build --selector <fence> --vars '<vars>'` as the builder, unit tests included. (5) On any
  failure, restore every output whose `main` moved, `rolled_back`. (6) On a pass, as the
  publisher: views to C ∩ old ∩ new where columns change, one multi-table tag commit, views to
  C ∩ new, `audited` (ADR-061 parts 4 and 5).
- Exits: 0 passed and published; 1 tests failed, outputs restored; 2 build error, outputs
  restored; 3 a restore, the tag commit, the view sync or the drift check failed (pages); 4 a
  required input was never tested.
- Its last line on stdout is the XCom:
  `{"exit": <n>, "expire": [{"table": "<relation>", "ids": [<snapshot id>, ...]}]}`.

**Expire**, a Spark task: `expire_snapshots(table => t, snapshot_ids => array(...), older_than =>
TIMESTAMP '1970-01-01 00:00:00')`, failing unless `deleted_manifest_lists_count >= len(ids)`.
Daily maintenance expires by `compiled/lake/retention.json`, the one retention source.

## The review's collision list, resolved

The independent review listed the surfaces two or more steps would otherwise each define. Each now
has one owner and one frozen place.

| Surface | Owner step | Frozen in |
|---|---|---|
| Stub assertions every filling step would edit | none: signatures only, discovery a superset | `platform/k2gen/tests/test_interfaces.py` |
| Secrets layout, canary password, TLS file names | S13 (TLS), S14 (password lines, keys, service passwords), S19 (canary line and password), S09 (webhook), S22 (mounts) | `model.SECRET_FILES`, the table above |
| dbt model naming, dbtDocs | S17 emits, S05 checks uniqueness, S18 links | `model.dbt_model`, `model.dbt_docs`, catalog schema `dbtDocs` |
| Relation dispatch by build kind | none: shared helpers | `model.reader_relation`, `input_relation`, `tested_ref` |
| `storage-plan.json` | S15 emits; S08 diffs; S19 applies | `storage-plan.schema.json` |
| `dags.json` | S16 emits; S08, S19 and phase 002 read | `dags.schema.json` |
| `oneshots.json` | S16 emits; S22 and phase 002 read | `oneshots.schema.json` |
| `lake/ddl` names, path to `needs` | S15 writes; S08 and S19 read | `model.ddl_path`, `model.NEEDS_BY_PREFIX` |
| The compose file in the release | S04 extracts, S08 copies, S19 runs | `model.COMPOSE`, `model.RELEASE_COMPOSE`, `k2gen release --compose` |
| YAML loader; a model's `config()` | none: shared helpers | `model.parse`, `model.model_config` |
| Golden files; seeds against gate (i) | each emitter step its own directory; S10 the canary's seed | `platform/k2gen/tests/golden/README.md`, `scripts/check_docs.py`, `k2-v2.schema.json#/$defs/seeds` |
| `$refs` for k2-api | S12 grants it | `model.REFS_READERS` |
| Platform tables' columns | S15 emits their DDL; phase 002 writes them | `model.PLATFORM_TABLES` |
| Who creates the run-record table | S15 emits it, S19 applies it | `platform/runs/run_record.sql`, `model.RUN_RECORD_SQL` |
| Retention | S15 emits it once; maintenance reads it | `model.RETENTION`; no `retainDays` in `pipeline.schema.json` |
| A compile over unfilled emitters | an error since S23 (every file or none) | `k2gen.emit.run` |
| Canary expectation rows | S20, after S10's product | `tests/access/expectations.yaml`, under `expectations.schema.json` |
| `trino-rules-test.sh`, `throwaway-lake.sh`, the drift CLI | S12 then S13; S20 | the shared-file table above |

## The compose project name

The proposal gives the compose file `name: k2`. It is not set, and step S22 did not set it: the
owner decided on 2026-11-27 to keep the live project name, because a rename recreates every
container and renames every volume. The compose file has no `name:`; `make deploy` is given the
live name by `K2_COMPOSE_PROJECT` in `.env`
([`docs/runbooks/first-cutover-to-releases.md`](../runbooks/first-cutover-to-releases.md)).

## Changed by the reviews, after S00d

The guarded reader view shape, `view_update`'s name-to-type maps and `lake.NoCommonColumns`, the
publisher's `$refs` grant, `password.db` in fixed slots and the Web UI switched off: each is stated,
with where it lives and why, in the last section of
[`platform/k2gen/INTERFACES.md`](../../platform/k2gen/INTERFACES.md#changed-by-the-reviews-of-s12-s15-and-s13-after-s00d).
For the wrapper above, step (6)'s two view statements are `emit/lake.view_update(view, old, new)`,
with `old` and `new` the tagged and tested snapshots' columns mapped to their types. The table
above holds for `trino/password.db` with one addition: each line sits in a fixed 128-byte slot,
rewritten by one aligned write in place, so a reader never sees a torn line.

## Run-record additions, 2026-11-28 (phases 004 and 005, step I-3)

Additions only: no column, enum value or key was renamed or removed, and a record written before
this section still validates against `contracts/schema/run-record.schema.json`.

- **Event `offset_gap`.** A load run with accept-data-loss writes one `offset_gap` row (task `load`)
  for the offsets it gave up. Its `detail` is JSON text shaped by `#/$defs/offsetGapDetail`: `partitions`
  (each `partition`, `fromOffset`, `toOffset`) and the operator's `reason`. `k2runs.EVENTS`, the schema
  enum and the `event` column's comment list it. The table gains no column.
- **The `lagMinutes` rule.** An input's `lagMinutes` is the run's start time minus the audited commit
  time of the snapshot it read, never below 0; null when the input was never audited. Stated in the
  schema's description of `inputs[].lagMinutes`.
- **The product state.** `#/$defs/productState` fixes the allowed forms of a derived product's
  completeness: `complete`, `partial: <contract ids of the inputs missing or late>` (comma and space
  separated) or `degraded`. It is computed from the newest run's inputs (`k2runs.state`, a later step), not
  stored in a column: the A6 columns are frozen, so a stored form would move them.
- **The `holds` table is not yet created.** The operator hold (columns `product_id`, `reason`,
  `held_by`, `held_at`, `released_at`, in the governance build catalog) needs the lake emitter,
  deploy, the drift check, the access expectations and the backup set to learn a second table, which this
  step does not touch; the step that writes `scripts/hold.py` owns it.

## A Kafka product's pipeline, 2026-11-28 (phases 004 and 005, step I-1)

Frozen against `docker/lake/kafka.py` as merged with W0-1 and W0-2; where the build breakdown and
the code differed, the code won and the line says so. Additions only, but for one departure from
plan 004 and one replaced plan sentence, each stated below. `model.SNAPSHOT_SUMMARY_KEYS` keeps its
four keys (a file connector's commits; `connector.py` zips them in order and
`tests/test_lake_connector.py` holds the two equal), so the Kafka keys are its sibling,
`model.KAFKA_SUMMARY_KEYS`.

**Snapshot summary keys.** `tests/test_frozen_interfaces.py` reads every `k2.*` key `kafka.py`
names, as a literal or through `offsets.py` and `connector.py`, and fails unless they are exactly
these. A renamed key orphans every earlier snapshot as a resume point (ADR-022).

| Commit | Key | Value |
|---|---|---|
| every Kafka connector commit | `k2.job` | `connector` (`model.KAFKA_JOB`), what the resume walk and expiry look for |
| | `k2.run-id`, `k2.release`, `k2.batch-id` | the run, its release, and the batch id, which is the run id |
| landing | `k2.kafka-offsets` | end offsets, exclusive, `{topic: {partition: offset}}`, every partition read before carried forward |
| | `k2.kafka-starts` | the batch's first offset per partition |
| | `k2.kafka-gaps` | only when the run accepted a loss: `{topic: {partition: [committed, log start]}}`, the offsets lost being committed to log start minus 1 |
| bronze | `k2.kafka-offsets` | the landing offsets bronze has staged through, exclusive: the landing's end offsets at the snapshot read, so trailing skipped records advance it and `max(kafka_offset) + 1` is not it |
| | `k2.src-snapshot-id` | the landing snapshot the stage read |
| | `k2.kafka-skipped` | the landing rows the filter skipped, per partition |

2026-11-28 (W0-4b): `k2.kafka-gaps` is cumulative. Every landing commit after the first accepted loss
carries every loss accepted so far, `{topic: {partition: [[committed, log start], ...]}}`, sorted and
merged where they meet, so expiring the commit that accepted a loss never drops its record; it is
absent until a loss is accepted.

The code over the breakdown: `k2.job` is on every Kafka connector commit only; a file connector's
commits carry `model.SNAPSHOT_SUMMARY_KEYS` and no `k2.job`, and a Kafka commit has no
`k2.source-sha256`.

**What the landing holds: a departure from plan 004.** Plan 004 says the landing table "holds the
kept records byte for byte". Frozen instead: `lake.<fence>.landing` holds every record of the
planned range, trade and control frames alike, `record_key` and `record_value` as read and never
decoded; the connector's `filter` (`stream` in `[trade]` for `kraken-trades`) runs at bronze
staging. The reason is the frozen landing columns (`model.PLATFORM_TABLES["landing"]`), which have
no place for a skipped record, and the offset checks reused from phase 001, which hold only over an
unbroken range: `offsets.batch_audit` passes a partition only if `end - start` rows landed at
`start..end - 1`, and `offsets.offset_gaps` calls it intact only if `max - min + 1` equals its row
count, so a landing without its control frames fails both at the first one, and skipped counts kept
only in summaries would go with the snapshots that carry them. Bronze accounting: per partition the
stage reads the landing rows from bronze's through-offset to the landing's end offset, at one
landing snapshot and never from the topic, and kept plus skipped must equal the rows read or
nothing stages. The descriptions of `kafkaConnector` and its `filter` in `k2-v2.schema.json` still
say the filter decides what lands; the next step to edit that file's descriptions brings them to
this.

2026-11-28 (review finding D1): a partition bronze has no through-offset for (the product's first
stage, or a partition added since) starts at the landing's first offset in it, its minimum
`kafka_offset` read from the landing's rows, never at 0. The landing lands from the log start
(`startFrom: earliest-retained`), so a range from 0 planned rows that were never there and the first
audit on a trimmed topic failed for good. The rows are the source because they are never expired;
the landing commit whose `k2.kafka-starts` recorded that start may be. The audit's accounting plans
from the same start, and Retention's "stages again from offset 0" reads "from the landing's first
offset".

**Bronze.** A Kafka product has one bronze table (`kafka.py` stages into exactly one), staged from the landing
by offset, never by run id, so a retry, or a kill between the landing commit and the stage, stages
nothing twice and leaves no hole. Its columns are exactly the landing's nine, by name and type, as
`stage_rows` selects them from the landing row: the key `(kafka_partition, kafka_offset)`, primary
key positions 1 and 2, which is also each row's lineage to the landing and the topic; the source's
values `kafka_timestamp`, `record_key` and `record_value`; and the lineage columns `batch_id`,
`landed_at`, `run_id` and `release_id`, those of the landing batch that landed the record. The run
that staged it is the bronze commit's `k2.batch-id` and `k2.run-id`. A column decoded from the
envelope would be computed at stage; adding one changes this interface.

2026-11-28 (the envelope decoded at stage): this replaces the columns above and the `values` in
"What k2gen compiles" below. Trino has no SQL function that decodes Avro, so silver could not read
the envelope out of `record_value`; bronze therefore carries it decoded, written by Spark at stage,
which decodes it already for the filter. The landing is unchanged: every record, its bytes as read.
Bronze's columns, in order: `kafka_partition` integer, `kafka_offset` bigint, `kafka_timestamp`, then
`schema_id` integer (the frame's Confluent writer schema id), then the envelope's top-level fields by
name and type as the product's bronze contract declares them (for `RawMessage`: `exchange`,
`stream`, `symbol`, `recv_ts_ns` bigint, `conn_id`, `conn_msg_seq` bigint, `payload` varbinary, the
venue frame as sent), then `batch_id`, `landed_at`, `run_id` and `release_id`. `record_key` and
`record_value` are not in bronze: the landing holds them, and bronze is rebuilt from it. The key
stays `(kafka_partition, kafka_offset)`; the compiled `values` are `kafka_timestamp`, `schema_id` and
the envelope columns. Each frame is decoded with the writer schema its id names, fetched from the
registry by id, so a writer schema with a field added still reads; each column is written by name
from the decoded record, and one the record lacks, or holds as a type that does not read as the
column's (equal, or Avro's `int` to `bigint` and `float` to `double`), is written null and fails the
batch in the audit's `parses`, which reads each staged frame again from the landing and names the
column. `schema_id` and the envelope columns are never `required`: a frame unframed, of an id that
is no `RawMessage`, or whose body does not read, is staged with them null and fails `parses`.
k2gen's `connectors.kafka-bronze-shape` and `pipeline.schema.json` hold this shape.

2026-11-28 (review finding D3): an id is unresolvable only when the registry answers 404 for it;
any other registry error fails the task, exit 1, before the stage writes or the audit records
anything. `parses` also compares each staged row with the frame's decode from the landing: a frame
whose bronze columns are not what its decode projects (null where the decode is not, as when the
registry answered 404 at the stage and serves the id since) fails the batch, naming partition,
offset and the columns.

2026-11-28 (review finding D2): a kill between the stage and the audit leaves no stuck batch either.
A stage that finds another run's batch unpublished on `audit` resets `audit` to `main` and stages
the range again from `main`'s through-offsets, with whatever landed since, when that batch has no
`audit_checks` row at its snapshot (never audited) or its newest audit passed every check (never
published); it is refused, naming the failed checks and the `REPLACE BRANCH` (no `main`: `DROP
BRANCH`) that clears it, when its newest audit failed or lacks a check, or no Kafka stage wrote the
head. A batch that failed its audit is never reset. The file kinds keep their rule: any other
batch blocks.

**The envelope contract.** The capture's `RawMessage` (`schemas/avro/raw-message.avsc`, subject
`market.crypto.v3.raw.<exchange>-value`) is declared by `market-capture` as the table contract
`contracts/market-capture/kraken-trades/raw_message.odcs.yaml`, id
`market-capture.kraken-trades.raw_message`, which the connector's `envelope.contract` names. Its
seven fields are the Avro schema's, by name; the Avro file stays the schema the registry serves.
W2-1 writes it. Two things are not settled today: nothing in k2gen checks that `envelope.contract`
resolves, and `k2-v2.schema.json#/$defs/build` has no kind for a contract that is no lake table but
the paused hot tier's `serving`.

**Accepting data loss.** The Airflow trigger `conf` of the product's own DAG, so per product by
construction:

```json
{"acceptDataLoss": true, "reason": "<why the operator accepts the loss>"}
```

Both keys or neither: `acceptDataLoss` the JSON boolean `true`, and `reason` a non-empty string,
carried verbatim into `offsetGapDetail.reason`. No `conf`, or `{}`, is a normal run. The factory
refuses, before `load`, a `conf` with any other key, a flag that is not `true`, a flag without a
reason or a reason without the flag, so a misspelt key never runs as a normal run. It passes
`--accept-data-loss` to `connector.py load` and to no other task; the reason never reaches the
connector.

The `offset_gap` run record (I-3): one row, task `load`, `table_name` the landing, `detail` the JSON
text of `run-record.schema.json#/$defs/offsetGapDetail`, one `partitions` entry per element of
load's result `offsetGaps` (`{topic, partition, committed, logStart, lost}`): `partition`,
`fromOffset` its `committed`, `toOffset` its `logStart - 1`. It is written when the landing commit
carrying `k2.kafka-gaps` was made, which load's result shows as exit 0, or as a result carrying
bronze's `table` (the stage runs only after that commit, so a stage refused after it still lost
the range). A run whose plan finds nothing below the log start is a normal run and writes no
`offset_gap`. The summary is the durable record: a load that dies after its landing commit and
before printing its result leaves the gap there alone, and nothing yet reconciles the two. A gap
whose resumed range is empty commits a landing snapshot of no rows; W0-4b proves Spark makes that
commit.

**Below the log start, and a paused product.** A start below the broker's log start is refused at
plan time, before Spark reads: load exits 2, nothing lands, and the message names every such
partition with its committed offset, the log start and the records gone. Only an accept-data-loss
run moves past it: those partitions resume at the log start, the gap committed in the landing's
summary and reported as `offset_gap`. A paused product lands and stages nothing (load exits 0, and
an accept-data-loss run on it records nothing). Resumed, its first run plans from its committed end
offsets like any run: still in the log, it continues with no loss; fallen below the log start
during the pause, it is the case above and fails loudly until an accept-data-loss run. This
replaces plan 004's "A paused product lands nothing and resumes at the log start": a silent jump
to the log start is the unrecorded hole ADR-022 exists to prevent. The header of `kafka.py` says
the first run after a resume "is an accept-data-loss run"; the code refuses it unless the run is
given the flag, and the code is what is frozen.

**The watermark a derived product reads from a Kafka input** is the bronze commit's
through-offsets: `k2.kafka-offsets` of the newest connector snapshot in the lineage of the snapshot
the build read, per partition, end exclusive; the build selects rows by `(kafka_partition,
kafka_offset)` against it and keeps the offsets themselves, never a snapshot id. Offsets only
advance (`offsets.merge_committed`), a compaction or expiry commit carries no `k2.job` of
`connector` and moves no row's offset, and the value needs no old snapshot to be evaluated, so it is
monotonic across compaction and survives expiry. Landing time is neither: `landed_at` is a driver's
wall clock, which can step back between runs, and a snapshot's commit time moves forward on every
compaction. For the same reason a Kafka input's `lagMinutes` (I-3) is taken from that connector
snapshot's commit time, its audited commit, never from a compaction snapshot after it.

**Retention.** A Kafka product's landing is never expired (owner, 2026-11-28): `maintain.py`'s
`LANDING_DAYS` is the file landings' rule alone. Snapshot expiry runs on the landing and on bronze
by `compiled/lake/retention.json` and never removes the newest connector snapshot of either: it is
the landing's resume point and bronze's staged-through point, and without it the next run lands
again from the log start, or stages again from offset 0, duplicating both. W1-9 builds it and W0-4c
proves the resume after a compaction and an expiry.

2026-11-28 (W0-4c, `bash scripts/verify/kafka-path.sh <release> c`, on the throwaway, each pass
`maintain.py` as the factory's task runs it): `--compact` is skipped, its reason in the result,
while bronze's `audit` holds an unpublished batch, which then publishes; with 53 small files on each
table (seeded by 50 runs, `COMPACT_MIN_FILES` unchanged) it writes one `replace` snapshot per table
carrying `k2.job` `maintenance` and no `k2.kafka-offsets`, every row as before. The default pass's
continuity check, expiry and `remove_orphan_files` run, deleting no live file: the listing the
maintenance key makes serves `s3://`, so Iceberg 1.8.1's own listing through Hadoop's FileSystem is
never needed. Expiry with every snapshot past retention keeps, on each table, main's head and the
newest connector snapshot under it (`retain_last` 2); the next run starts at that snapshot's end
offsets and lands and stages each record once. Iceberg's `remove_orphan_files` itself refuses an
`older_than` under 24 hours before now, the same floor as `ORPHAN_FLOOR`.

**What k2gen compiles for a Kafka product (W1-2).** In `pipeline.json`: `connector` as the product
file declares it (`kind` `kafka`, `brokers`, `registry`, `topic`, `envelope` `{format, contract}`,
`filter` `{field, in}` when it filters, `payload`, `startFrom`, `maxOffsetsPerPartition`);
`landing` `{table: lake.<fence>.landing}` (compiled today); `bronze`, exactly one entry: `table`
`lake.<fence>.bronze_<name>`, `key` `["kafka_partition", "kafka_offset"]`, `values`
`["kafka_timestamp", "record_key", "record_value"]` (today's `_bronze` leaves out only
`source_file_sha256`, so it would list the four lineage columns as values), `decimal` `[]`,
`patterns` `{}`, `audits` the contract's `k2-connector` checks, `branch` `audit`, `checks`
`lake.<fence>.audit_checks`; and the DAG's tasks with no `fetch` (compiled today). k2gen refuses a
Kafka product with more than one connector table, or one whose columns are not the landing's with
that key, and `pipeline.schema.json` gains the matching rule for `kind` `kafka` with its test
(its descriptions state it now; a rule there today would fail the emitter test that compiles a
Kafka connector over a fetch-kind fixture's bronze). The landing goes in the backup set as
`restore`, with no row expiry. The accept-data-loss `conf` is run time and is never compiled.

## Holds, 2026-11-28 (phases 004 and 005, step I-7)

Additive: the run record, its rules and its file are unchanged. The `holds` table is the platform's
second Trino table, `governance_build.run_records.holds`, in the run record's namespace, its DDL
frozen in `platform/runs/holds.sql` (`model.HOLDS_SQL`). `model.PLATFORM_SQL` lists both files; the
lake emitter copies each byte for byte to `compiled/lake/ddl/trino/governance.run_records.<table>.sql`
when the `governance` domain is declared, and a contract whose build is `platform` is still refused,
since the platform's tables come from those files alone. `tests/test_frozen_interfaces.py` holds the
columns, their descriptions and `k2runs.HOLD_COLUMNS` to each other. The namespace `run_records` was
reserved already (`model.RESERVED_PRODUCT_NAMES`), so no name is newly reserved.

| Column | Type | Meaning |
|---|---|---|
| `product_id` | varchar | the product held, `<domain>.<product>` |
| `kind` | varchar | `operator` (`make hold`), `rebuild` (`make rebuild`'s lock) or `restore` (`make restore`'s) |
| `reason` | varchar | the holder's reason, at most 1000 characters |
| `held_by` | varchar | who took it: the operator's name, or the make target for a lock |
| `held_at` | timestamp(6) with time zone | when it was taken; with `product_id` and `kind`, the hold's key |
| `released_at` | timestamp(6) with time zone | null on the hold row; set on the release row |
| `released_by` | varchar | null on the hold row; who ended it |

**Append-only: a release is a second row, never an update.** The writer holds SELECT and INSERT and
nothing else, as it does on the run record, so no row of either table can change or go; an UPDATE
would need the UPDATE privilege on the table the page reads, and an Iceberg row update writes
delete files a reader then merges. A hold is the row with `released_at` null; its release copies
that row's `product_id`, `kind`, `reason`, `held_by` and `held_at` and sets `released_at` and
`released_by`. A hold is open while no release row carries its `(product_id, kind, held_at)`. One
open hold per product and kind, so an operator hold and a restore's lock are apart: the end of a
restore never ends an operator's hold, and `make unhold` never ends a lock.

**Rules** (`emit/trino_rules.py`), default deny kept, three rules per platform table and no other:
svc.platform SELECT and OWNERSHIP through `governance_build` (it creates the table at deploy, and
deploy reads it, below); svc.runs SELECT and INSERT there (`make hold` and the locks write, a DAG's
`resolve` reads); svc.catalog SELECT through `governance`, for the page. No principal holds DELETE or
UPDATE on it, and no reader group reaches it.

**Backup and retention.** In the backup set as `restore` at `main`, and in `retention.json` at the
run records' 7 days of snapshots; no row is ever expired.

**The client** (`platform/runs/k2runs.py`, as svc.runs): `hold(conn, product, reason, by,
kind="operator")` inserts the hold row only while no hold of that kind is open (one statement,
`INSERT ... SELECT ... WHERE NOT EXISTS`), else `HoldError`; `release(conn, product, by,
kind="operator")` inserts the release row copied from the open hold of that kind, else `HoldError`;
`held(conn, product)` returns the newest open hold of any kind as a dict, or None. The conditional
insert is not a mutex: two writers in one instant can both pass it, since Iceberg appends do not
conflict; one operator per host is assumed.

**What honours a hold.**

- `resolve` has no Trino identity of its own: it runs inside the Airflow task and reads only the
  pointer. Having pinned the release, it loads `k2runs` from it, as `record` does, connects as
  svc.runs (whose password Airflow mounts for `record`) and calls `held(product)`. A hold fails the
  run in `resolve` with `AirflowFailException`, naming the kind, reason, holder and time; a read that
  fails fails `resolve` too, so a hold is never skipped because the table was unreachable. `record`
  then skips, as it does after any failed `resolve`.
- `make hold` also pauses the DAG (W1-11), so a held product normally starts no run; `resolve` is
  the guard for a manual trigger, a run already queued and the locks.
- No deploy unpauses or overrides a hold. Deploy unpauses only the DAGs it paused itself
  (`deploy-paused.json`), and from W1-12 skips any whose product `held` returns, read as
  svc.platform; deploy holds no INSERT, so it cannot end one.
- `make rebuild PRODUCT=` takes a `rebuild` hold on its product before it restages bronze and
  releases it after `k2-dbt build --full-refresh`; the product's own runs fail in `resolve` meanwhile.
- `make restore` holds every DAG of the live release's `dags.json` with a `restore` hold, and pauses
  each, before it touches a table, and releases each only after the tags equal the run records. The
  holds table is itself in the backup set, so the holds are written after it is put back.

**W1-6's trigger: a degraded build of `kraken-trades` starts a `kraken-instruments` run.** The
factory adds one task, `refresh`, downstream of `build` (all_success: a degraded build publishes,
so `build` succeeds), to a DAG whose `pipeline.json` names a refresh target. It is Airflow's own
`TriggerDagRunOperator` (the standard provider in the Airflow 3 image), whose `execute` raises
`AirflowSkipException` unless `build`'s XCom `result` carries `"degraded": true` (W1-7's wrapper
result). Bounded so it cannot loop: `trigger_run_id` is `refresh__<this run's run_id>`, with
`skip_when_already_exists`, `retries=0` and no `wait_for_completion`, so one degraded run starts at
most one target run; the target is one of the product's own input ports' products, and k2gen
refuses a refresh target that itself declares one, so the chain is one hop. A held or paused target
gets the run queued, and `resolve` fails it while held. Where `pipeline.json` gets the target, and
the contract field it comes from, is W1-6's, with `make test-airflow`.

2026-11-28, the field: the product property `refresh`, value a product id, compiles to
`pipeline.json`'s top-level `"refresh"` (optional in `pipeline.schema.json`). `k2gen check
refresh.*` (`platform/k2gen/k2gen/checks/refresh.py`) holds it: the target is a product of the set,
not retired, not the product itself, scheduled, upstream of the product through its input ports
directly or through other products (`kraken-instruments` feeds `kraken-trades` through the security
master, so a target is never a product the build does not read), naming no refresh of its own; and
the product has a warn-severity dbt test, the only thing that makes a build degraded.
`kraken-trades` declares `refresh: market-capture.kraken-instruments`.

## The role order, 2026-11-28 (phases 004 and 005)

A10 gains one optional field, additively: a role in `contracts/access/roles.yaml` may list the roles
it is `within` (roles whose holders see no less), schema `k2-v2.schema.json#/$defs/roles`. The
repository declares one pair, `delayed_reader` within `full_reader`. `access.inherit-role` then lets
an output offer a role over an input that offers, instead, a role it is declared within, and holds
the output to that role's policy on the input (delay, mask, row scope, as for the role itself). No
chain is followed, a role within none is comparable only to itself (`public_reader`), and an input
offering no role still blocks every role. Why: `silver_trade` of `kraken-trades` offers
`delayed_reader` (plan 004) over `security-master`, reference data with no event time to delay,
which offers `full_reader` alone (ADR-056 part 8: an enriched product is never less restricted
than its inputs).

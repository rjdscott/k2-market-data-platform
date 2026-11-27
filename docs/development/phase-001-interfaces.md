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

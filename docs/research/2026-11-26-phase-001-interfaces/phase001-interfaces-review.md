> **Retained design input, dated 2026-11-26 (UTC):** the independent review of the frozen interfaces (S00a and S00b) that step S00c acted on, finding by finding, copied unchanged but for session-only paths and the notes marked superseded. It is a record, not the authority.
> Where it differs from `docs/development/phase-001-interfaces.md`, `platform/k2gen/INTERFACES.md` or an Accepted ADR, they win; in particular no credential hash is ever committed (the orchestrator's ruling on Q4).

# Phase 001 interfaces review (S00a + S00b at b00e4b7, 2026-11-27 UTC)

Read from a detached worktree at `b00e4b7`, in session scratch. Ran `make test-k2gen` (181 passed), `make test-python` (526 passed), `make test-dbt` (PASS), plus experiments in session scratch. "Verified" means I ran it.

## Findings

| # | Sev | Where | Problem | Breaks | Smallest fix |
|---|---|---|---|---|---|
| 1 | Critical | platform/k2gen/tests/test_interfaces.py:38-48, 240-256, 284-300 | Asserts every stub raises `NotImplementedError("<step>")` and that module lists match exactly. Verified: filling `checks/docs.py` and `emit/catalog.py` and adding `emit/compose.py` gives 2 failures. Every filling step must edit the same lines | S04-S08, S11-S18 (13 parallel PRs conflict) | Keep only the signature and type-hint checks; `set(EMITTERS) <= found`; the CLI test monkeypatches |
| 2 | Critical | cli.py:97-111; ci.yml:85-86 | The old `validate`/`generate` globs `contracts/*/*/*.odcs.yaml` against `k2.schema.json`. Verified: with a v2 product under `contracts/` it fails, and so does CI's drift step | S10 stays red until S23 (W5); S20, S19, S22 need the canary | Old path skips any dir holding `product.odps.yaml` (the dropped N1-8), with a test |
| 3 | High | mk/principals.mk:7,11; docs/development/phase-001-interfaces.md:74; openapi.yaml:183 | These still put hashes into `principals.yaml` or a release key map. That contradicts the ruling (k2-v2.schema.json:5,669; INTERFACES.md:78; emit/api.py:1). The replacement is unfrozen: file names under `${K2_SECRETS}` for Trino's password file and the key map, container paths, which step writes them, who adds each release's canary `k2_release_<sha12>` line (model.py:238). A pure compiler cannot fill `Manifest.secrets`. The proposal's S14 calls the canary `svc.release-canary` | S13 (the authenticator's path), S14, S19 (canary login), S20 (logins), S22 (mounts), 003 | One frozen table: `trino/password.db` and `api/keys.json` (`{sha256: principal}`) under `${K2_SECRETS}` at fixed read-only container paths; `tls/` file names; deploy writes the canary line and fills or drops `secrets`; one canary name |
| 4 | High | release/catalog-product.yaml:39; good unit_tests.yml | One compiled dbt project implies model name = table name (`model.k2.gold_daily_load`, bare `model:`), but table names are unique only per product. Verified: dbt refuses duplicate model names. Canary `events`; every venue's trades product repeats names (DECISIONS-FINAL s4) | S17, S18 (dbtDocs), S05, S10 and every author | Freeze now: platform-unique dbt table names checked by S05, or `<fence>__<table>` plus alias, and the dbtDocs form |
| 5 | High | model.py:157-210; run-record.schema.json `inputs.relation` | The primitives exist, but the dispatch by build kind does not: what a builder reads, what a reader sees, which ref is tested (connector: lake, `main`; dbt or code: base and view, `audited`; platform: a table in `<fence>`) | S07, S12, S16, S17, S18, S20 each re-derive it | `input_relation(t)`, `reader_relation(t)`, `tested_ref(t)` in model.py |
| 6 | High | proposal A1; release.py:3-6; model.py:59 | Deploy runs `-f <release>/compose/docker-compose.yml`, but no emitter writes `compose/` and compose is outside DOCUMENT_ROOTS, so a pure compile cannot include it. Also unfrozen: storage-plan.json, dags.json, oneshots.json, the `compiled/lake/ddl/` names, and the path-to-`needs` map (`trinoRestart`, `dagShapeChange`, `recreate`) | S08, S15, S16, S19, S22, 002 | Small schemas for the three JSON files; `NEEDS_BY_PREFIX` in model.py; a compose decision (a `--compose` input, or the tag's file with its sha256 in the manifest) |
| 7 | High | openapi.yaml:130; model.py:88; k2-api stub on k2-net only | `/v1/data` must read `$refs`, but `svc.api` is no `$refs` reader, the impersonated caller is refused every base path (gate h1.08), and k2-api is off k2-catalog. catalog-product.schema.json:5 says k2-api reads "as svc.catalog"; A10 says it logs in as `svc.api` | S12 (the grant), 003 | Add `svc.api` to REFS_READERS, or freeze that k2-api holds `svc.catalog` |
| 8 | High | scripts/check_docs.py:140-141, 251 | Verified: gate (i) fails any `.json`/`.txt` under `fixtures/`, a CSV-looking `groups.txt` anywhere, and every `seeds/*.csv`. The only escape is NOTICE, which is not a split file | S10, S12, S14, S15, S16, S18, S21, 004 | Freeze `platform/k2gen/tests/golden/<emitter>/`, with golden groups as YAML; a gate (i) rule for `contracts/*/*/seeds/` |
| 9 | Medium | pipeline.schema.json:44,50,52; model.py:394-406; release/pipeline.yaml | `bronze[].lineage` (required) and `patterns` cannot be derived: no `Column.pattern`, no lineage marker. The example's `^s-[0-9]+$` is in no good document. The columns of `landing_files`, `audit_checks` and kafka `landing` are unfrozen, yet S15 emits their DDL | S16 (forced to change a frozen type), S15, 002 | `Column.pattern` from `logicalTypeOptions`; `LINEAGE_COLUMN`; freeze the three tables |
| 10 | Medium | k2-v2.schema.json:12, 513, 618 | Each verified valid: column `current_user` (an unquoted view returns the session user), rowScope value `north') OR (1=1`, physicalType `varchar, injected bigint`, table `x__base` (which catalog-product then refuses), a 300-character name | S12, S15, S17, S18 | `quote_ident` and `sql_string` helpers, or reserved words refused; a type grammar; tables `not __`; maxLength 63 |
| 11 | Medium | k2-v2.schema.json:361, 378, 393, 410 | The field says "may hold {{ds}}", but verified: any Jinja passes (`{{ var.value.x }}`, `{{ conn.minio.password }}`) | 002 factory: secret exfiltration to an allowed host | `^https://([^{}]\|\{\{ds\}\})*$`, the same for path and key |
| 12 | Medium | none | No frozen YAML loader; S04 and S05/S07 parse separately. Verified: safe_load keeps the last duplicate key (`{r: {mask: [a]}, r: {}}` becomes `{r: {}}`) | S04, S05, S07 | `model.parse(path, bytes)` refusing duplicate keys, aliases and merge keys |
| 13 | Medium | expectations.schema.json:74-84; good expectations.yaml | The session-header form (gate h1.24) cannot be written; the fixture lacks the gate's rows (h1.02 publisher write floor, h1.33 `svc.platform`, `SET AUTHORIZATION`, `table_changes`, foreign view) | S20, S07 | Optional `session` map on statement rows; add the rows |
| 14 | Medium | k2-v2.schema.json:495, 564-575 | hot-gold has no lake table, but every output needs an Iceberg contract with a lake build kind | S10 invents a table; S12, S15 and S20 emit for it | A `build: serving` kind that those emitters skip |
| 15 | Medium | ADR-036 (Accepted, about line 414); plan 001 items 4, 7 | Fetch exits, the spool, `fetch.json`, the wrapper CLI, exits 0-4 and the XCom are "frozen by this PR" but are in no repo file | 002's fetch, loader and factory | A section in INTERFACES.md |
| 16 | Medium | dbt/macros/k2_input.sql:59 | Verified: `\| int` turns null or garbage into `BIGINT '0'`. An optional input that was never audited has no rendering | 002 wrapper | Raise unless the value is a number; freeze the optional case |
| 17 | Medium | run_record.sql; record_test_results.sql; test_frozen_interfaces.py:83-103 | No key joins a publish to its test rows; no OpenLineage run id; the DDL and the JSON schema are each tested alone | catalog page, 006 | Two nullable columns; a test that schema equals DDL |
| 18 | Medium | openapi.yaml:135-161, 252, 256 | `filters` explode collides with columns named `fields`, `limit`, `cursor`, `as_of`; `next` stays null at 10000 rows, so truncation is hidden until 007; an int64 `snapshot` loses precision in JavaScript | 003, 007 | `filter[col]`; a 400 or a `truncated` field; `snapshot` as a string |
| 19 | Low | model.py:66-205, 685; pipeline.schema.json:67; run_record.sql:10; openapi.yaml:13; INTERFACES.md:61 | "Provisional until S01" survives the gate. Its two amendments (`pub_.*` `all` on reader catalogs; `svc.platform` nothing in reader namespaces) are in no frozen file | S12 | Finalise the markers; write both amendments down |
| 20 | Low | openapi.yaml:232; phase-001-interfaces.md:53; schema README | These name the old `k2gen/schemas/` path, and the README says ODCS_SCHEMA lives only in cli.py | | Correct them |
| 21 | Low | NOTICE | Covers the ODCS schema, not the vendored ODPS schema (Apache-2.0) | | One paragraph |
| 22 | Low | model.py:59 | Verified: `git archive <tag> contracts/ domains/ ...` fails (`pathspec 'domains/'`), including at v3.0.0-alpha.2, CI's "previous" | S04, S21 | Filter the roots by `git ls-tree` |
| 23 | Low | mk/*.mk; Makefile:220 | The stubs exit 0; an mk file overriding a root target only warns, and nothing tests it | S21 | Exit 2; a test for "overriding recipe" |
| 24 | Low | .env.example; A7 | `K2_TRINO_PORT` names both the host port (8088) and the wrapper's in-network port | 002, S22 | Rename one |
| 25 | Low | emit/__init__.py | An emitter whose output depends on `previous` gives one set two ids (CI versus deploy) | S08 | State that emitters ignore it for content |

## Collision list (S04 to S24)

| Surface | Steps | Frozen? |
|---|---|---|
| Stub assertions in test_interfaces.py | S04-S08, S11-S18 | no (1) |
| Secrets layout, canary password, TLS file names | S11, S13, S14, S19, S20, S22 | no (3) |
| dbt model naming, dbtDocs | S05, S10, S17, S18 | no (4) |
| Relation dispatch by build kind | S07, S12, S16, S17, S18, S20 | no (5) |
| storage-plan.json | S15, S08, S19, N9 | no |
| dags.json | S16, S08, S19 preflight, 002 | no |
| oneshots.json | S16, S22 networks, 002 | no, by design |
| `lake/ddl` names, path-to-needs map | S11, S15, S16, S08, S19 | no |
| compose in the release, pointer names | S11, S19, S22 | mount only |
| YAML loader, dbt `config()` parse (incremental, `incremental_predicates`; Table has no materialization) | S04, S05, S07 | no |
| Golden directory, seeds against gate (i) | S10, S12, S14-S16, S18, S21 | no (8) |
| `$refs` principal for k2-api | S12, 003 | no (7) |
| Platform table columns | S15, 002 | no (9) |
| Who creates run_record (S19 from `platform/runs`, outside the release, or S15's "platform tables" DDL) | S15, S19 | ambiguous |
| Retention (per table in retention.json, per product in `maintain.retainDays`) | S15, S16 | two sources |
| A compile over unfilled emitters (fail or skip) | S08, S11-S18 | unstated |
| Canary expectation rows | S10, S07, S20 | owner S20; safe if S21 follows |
| trino-rules-test.sh, throwaway-lake.sh, drift CLI | S12, S13, S20, S19 | sequenced, fine |

## Checked and sound

- **Vendored schemas:** ODPS and ODCS are byte-identical to upstream at the stated commits (fetched, sha256 matched). Both run before the K2 profile on every document, so a K2-valid document is also standard-valid. The standards refuse unknown fields. The custom-property enums are closed, so adding a property later is non-breaking. Landing retention (O6) is absent, but can be added without a breaking change.
- **Expressiveness:** validated in scratch: kafka, public-api, https-file, s3, sftp, ftp (with its TLS rule), inputs from two domains, paused with `serves` and `lifecycleChange`, deprecated requiring sunset and successor, delay, mask, rowScope, asOf, time, unit, examples, joinKeys, seeds.
- **Naming helpers:** they give exactly the gate's names: the view created through `<d>_build.<f>`, SQL over `<d>.<f>__base`, `pub_`/`dbt_`. PIPELINE_PRINCIPALS equals the gate's statistics allow list. `__` and reserved product and domain names are refused.
- **Compose:** the default service set is identical to main's (21 services). The stubs sit behind profiles with no published port and no secret default, and `config -q` over every profile passes. `fetch` is never on k2-catalog.
- **OpenAPI:** valid 3.1, one error shape, `as_of` always 422, no `__base` name.
- **k2_input:** its four render cases pass. One source name over two schemas works in dbt (verified).
- **run_record:** the DDL's 15 columns equal the JSON schema's; `day(recorded_at)`; every column has a comment.
- **Credentials:** no schema has a credential or hash field.

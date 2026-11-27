# Audit: phases 002 and 003, the run spine and serving, 2026-11-27

**Verdict:** not yet, for either tag. The branch builds what both phases claim, and most of it is
proven on throwaway stacks and in unit tests: CI is green on all 19 checks at `283777f`, the budget
matches the gate, no secret, real rate or status file is committed, ADR immutability holds, and
the access rows, the storage keys, the fetch guard and the wrapper each have a script. But nothing
of either phase has run on the live stack, which still runs `v3.0.0-alpha.3.2`, and 1 CRITICAL and
9 HIGH stand. The worst: `SECURITY.md` says the fetch one-shot, where product parsers run, connects
only to its allow-listed hosts, while its network reaches the whole internet (CRITICAL 1); the
deploy's drain calls an Airflow command that does not exist in Airflow 3.3.2, so every deploy that
meets a running run will fail, and its test fakes the same wrong command (HIGH 3); a product's
parser can still make Spark load another product's spooled file by a symlink (HIGH 6). The budget
gate fails at the audited head (HIGH 2). Two exit clauses of 002 have no proof (HIGH 4), and 003's
"same answer through SQL and REST" has never compared a row (HIGH 5). The new run stage,
`scripts/verify/run-path.sh`, replaces three of the plan's five missing chaos scripts, but no pass
of it is recorded (MED 11).
Before `v3.0.0-alpha.4`: the CRITICAL and HIGH fixed, a recorded `make spine-e2e` pass with its
`run` stage at the pushed head, a scratch `make release` against real images, the deploy, and the
002 rows of the live table below. Before `v3.0.0-alpha.5`: the 003 rows.

| | |
|---|---|
| **Commit** | `05c6c18` (local `phase/002-003-run-and-serve`, merged into the audit branch). Pull request #255's pushed head is `283777f`, 5 commits behind: those 5 add `scripts/verify/run-path.sh`, its spine-e2e stage and new dates on the ECB fixtures. Some investigations read `283777f`; each row below was re-checked at `05c6c18` where those files differ. The two reviews followed up here were made at `122699c` |
| **Live** | release `v3.0.0-alpha.3.2`, from before both phases. Nothing audited here runs on it |
| **Scope** | Plans `002-run-spine-through-ecb-fx.md` and `003-serving-and-teaching.md`: Delivers, Exit, Scope and every Verification line. The 73 steps of `docs/research/2026-11-27-phases-002-003-build-breakdown.md`. The documents the branch wrote: ADR-054, ADR-062, the Outcomes appended to ADR-010, 036, 046, 058, 059 and 061, chapters 21 and 22, `CHANGELOG.md` 3.0.0-alpha.4 and alpha.5, every runbook the diff touches, `docs/operations/`, `docs/development/` (spine-e2e, testing, setup, the dbt labs, the review guide), `README.md`, `CLAUDE.md`, `SECURITY.md`, `services/k2-api/README.md`, `tests/access/README.md`. The guardrails of `CLAUDE.md` on the diff. The frozen interfaces against the build. The rows added to `tests/access/expectations.yaml`. The 32 findings and questions of the two reviews. The scripts the reviews did not reach: `scripts/lib/throwaway-lake.sh`, `scripts/verify/*`, `scripts/drop.sh`, `scripts/backup_lake.py`, `platform/catalog-site/k2site.py`, the new steps of `scripts/deploy.py`, and CI's coverage of the new code |
| **Out of scope** | Re-reviewing what the two reviews examined and found sound; phase 004 and later; open rows of earlier audits, except where a claim here repeats one |
| **What "proven on a throwaway" limits** | A throwaway lake or the `k2-rehearsal` project proves a mechanism on this host's images, not the live stack's state: its `.env`, secrets, data, DAG history and MinIO users. The rehearsal has no k2-api, no `k2-site` volume, no scheduler and no `lake-init` (MED 17), so the deploy's site, k2-api, drain-with-DAGs and `lake-init` steps are proven on fakes only. Every exit clause that names the live stack, a tag or a scheduled run is open until the live table is filled |
| **Lens** | Claims against reality: the phases' exits, document accuracy, guardrails, whether each proof can fail |
| **Method** | No container started; the live checkout, its `.env`, `~/k2-secrets` and `~/k2-releases` untouched. Run in the audit worktree: `python3 scripts/check-budget.py` at `283777f` and `05c6c18`; `docker compose --env-file .env.example [--profile ...] config` (render only) for networks, environment names, ports and limits; `bash scripts/verify/ci-validate.sh <scratch>` (a trial release, `uv` only), then `bash scripts/verify/api-schema.sh --release <it>` and `bash scripts/verify/catalog-site.sh` (offline form); `make -n` for every target the plans and runbooks name; `gh pr checks 255`; `git` for history, tags and blame; Airflow 3.3.2's `cli_config.py` read from the `apache_airflow_core-3.3.2` wheel. Read: the plans, the breakdown, the frozen interfaces, every script named below, the compiled trial release. Six read-only investigations ran in parallel (breakdown steps; ADR, chapter and top-level claims; runbook and development claims; scripts and CI; guardrails and a sample of 28 tests; the two reviews); each CRITICAL and HIGH they returned was re-read at its file line here before it went in, and two candidates were dropped after checking (below the table). Test counts were not rerun, only checked for a stated command |
| **Severities** | CRITICAL (the README's BLOCKER): a false claim about access, safety or data loss, or a secret. HIGH: a false claim an operator would act on, a Verification line with nothing proving it and no stated replacement, a guardrail broken, a proof that cannot fail. MED: a document claims what was not run, a criterion met in part, a script whose header claims more than it checks. LOW: stale wording, stated limits, cosmetics. "LOW (stated)": a limit a document already states |
| **Findings** | 1 CRITICAL · 9 HIGH · 21 MED · 17 LOW |

---

## Plan 002: the Verification block, line by line

| Line | Proven by | What it asserts | Status |
|---|---|---|---|
| 108 `airflow dags test ecb_fx_rates` | `docker/airflow/tests/test_factory.py` (CI `Docker build (airflow)`): the DAG's tasks, pools and commands from the pinned release; `run-path.sh` runs each task's command on a throwaway lake, not the DAG | task logic, not a DAG run | needs the live stack |
| 109 `airflow dags test currency_master` | `test_factory.py` only; `run-path.sh` does not run currency-master | as above | needs the live stack |
| 110 `airflow dags test calendars` | `test_factory.py`; `run-path.sh` claim 1 builds calendars through the wrapper and its `audited` tag moves | the build, not the DAG | needs the live stack |
| 111 `scripts/chaos/fx-bad-file.sh` | no such script. `run-path.sh` claim 5 (`:374-393`): the corrupt fixture lands, audit and publish exit 3, bronze `main` and its row count unmoved, a third batch refused at load (exit 2) and landed all the same, then cleared. Unit: `tests/test_lake_connector.py:302`, `:333`, `:499` (fakes) | the line's three claims | replaced by `run-path.sh` claim 5; no pass recorded (MED 11) |
| 112 `scripts/verify/fx-idempotent.sh` | no such script. `run-path.sh` claim 4 (`:352-365`): no new landing object, `landing_files` row or bronze row, the build's rows the same. Unit: `tests/test_fetch.py:471` (fetch exits 10), `tests/test_lake_connector.py:233`, `:313`, `:522` | no new row | replaced by `run-path.sh` claim 4; no pass recorded (MED 11) |
| 113 `scripts/chaos/dbt-failing-test.sh` | no such script. `run-path.sh` claim 6 (`:394-414`): gold given a duplicate row, exit 1, silver and gold back on their tags with their counts, `expire.py` removes the XCom's ids, `maintain.py` exits 0; claim 7 the `rolled_back` records. A reader polling through the window: only `dbt-wrapper.sh` run2, on the access canary | rollback and expire on ECB | in part (MED 12) |
| 114 `scripts/chaos/trino-stop-mid-build.sh` | no such script. `dbt-wrapper.sh` run3 and run4 kill the dbt container after `started` and the next run reconciles (and the kill itself is not checked, MED 15); its Q1 rows cancel an orphaned builder query. Trino is never stopped, and no running runner's environment is read | a killed build is reconciled | open (HIGH 4) |
| 115 `scripts/chaos/deploy-mid-run.sh` | no such script. `test_factory.py:286` (a moved pointer does not move a run), `tests/test_deploy.py:1134` (the drain, Airflow faked with a wrong command, HIGH 3) | unit only | open (HIGH 4) |
| 116 `make test-connectors` | `tests/test_fetch.py`, DNS and sockets faked: `:292` a redirect off the rules, `:309` a host on a non-public address, `:316` a redirect to one, `:343` a URL off the rules, `:366` the sixth redirect; each exit 20, nothing connected or spooled | the line | proven (no network; CI `Python` runs `pytest tests`) |
| 117 `docker compose --profile build config dbt-runner \| grep -A12 'environment:'` | run here | `K2_CATALOG_URI K2_PUB_USER K2_RELEASE K2_RUNS_USER K2_TRINO_HOST K2_TRINO_INTERNAL_PORT K2_TRINO_USER`, the frozen seven; the trial release's `compiled/oneshots.json` lists the same seven | proven (render) |
| 118 `make access-verify` (key probes; MinIO and Lakekeeper refused) | 21 storage-key rows (`tests/access/expectations.yaml:175-195`), run by `scripts/verify/storage-keys.sh` step 4 (43 PASS, pull request #255) and spine-e2e's `access-verify` stage; the probe rows always SKIP in `access-verify` (`scripts/access_verify.py:693-694`) and run in `scripts/verify/catalog-fence.sh`, which `release-verify` runs | key rows on a throwaway lake; probes on a running stack only | key rows proven on a throwaway; probes need the live stack (LOW 42) |
| 119-120 `docker exec k2-trino trino --execute "ALTER TABLE ... optimize"` | cannot run as written (no password, no HTTPS). Replaced by three statement rows (`expectations.yaml:154-156`) for `p.analyst-a` over TLS: on the view `error`, on the base and the build catalog `denied` | a consumer cannot compact | replaced, as `CHANGELOG.md:107-108` states; proven on a throwaway |
| 121 `make backup && make backup-verify` lists the ECB landing | the trial release's `compiled/lake/backup-set.json` lists `k2-landing` prefix `fx-reference/ecb-fx-rates/`; `tests/test_backup_lake.py` (hand-built Avro, MED 20) | the set names it | needs the live stack (a landing exists only after a live fetch) |
| 122 `python3 scripts/check-budget.py` | run here | at `283777f`: PASS, `bootstrap build 18.30 CPU 30.750 GiB`, `bootstrap fetch 17.80 CPU 29.750 GiB`. At `05c6c18`: the same figures, then `FAIL: scripts/verify/run-path.sh: docker run 256 is not pinned`, exit 1 | open (HIGH 2) |
| 123 `make test` | not run (containers); CI on `283777f` | all 19 checks pass, `Trino (access rules)` among them | CI only, at `283777f`; red at `05c6c18` by line 122 |
| 124 `make check-docs` | not run (it starts the notebook gate); CI `Docs` passes at `283777f`; this file's own gates at the end | | CI only |

## Plan 003: the Verification block, line by line

| Line | Proven by | What it asserts | Status |
|---|---|---|---|
| 67 `make deploy RELEASE=v3.0.0-alpha.5 && make release-verify` | `tests/test_deploy.py:946-951`: verify asks a k2-api on a loopback port of the test and expects `/v1/release names <id>` | the check exists | needs the live stack; the tag is not cut |
| 68 `make access-verify` (every ECB row through SQL and REST, equal) | two of nine ECB table rows carry `via: [sql, rest]` (`expectations.yaml:143`, `:150`); on a throwaway lake the gold base holds no row, so the REST leg proves only the 200 (`scripts/access_verify.py:478-481`); live, every ECB row is a SKIP (`access_verify.py:568-576`) | a 200 with no row, and one 403 | open (HIGH 5) |
| 69-71 `curl ... gold_eur_rates_daily?limit=5` with a key | `services/k2-api/tests/test_data.py:56`, a fake Trino | rows of the view as the caller | needs the live stack |
| 72-73 no key: 401 | `test_data.py:137`; `test_keys.py` (14 malformed headers) | 401 before any query | proven in unit; live needs the stack |
| 74-75 a key on `bronze_daily`: 403 | `test_data.py:149` (fake); the REST leg of `expectations.yaml:150` on a throwaway lake | 403 on a table Trino denies | proven on a throwaway; live needs the stack |
| 76 `bash scripts/verify/api-schema.sh` | run here as `--release <trial release>` | `7 PASS, 0 FAIL`: the index and six product documents against `catalog-product.schema.json` | proven offline; the live form needs k2-api |
| 77 `curl /v1/openapi.json` prints 3.1.0 | `services/k2-api/tests/test_skeleton.py:62` (CI `k2-api (tests)`) | the served file is the frozen one, `3.1.0` | proven in unit; live needs the stack |
| 78 `bash scripts/verify/catalog-site.sh` | run here, offline form | `catalog-site: 6 products, 7 pages, 0 failures`; `a planted bearer token fails the check, as it must`; exit 0 in 2 s; facts not read (`--no-facts`) | proven offline; facts need the live stack |
| 79 `curl /dbt-docs/` 200 | `services/k2-api/tests/test_site.py:38-105` (a served tree; 503 before the first generation) | serving, not the generated docs | needs the live stack |
| 80 `cd services/k2-api && uv run --locked pytest` | CI `k2-api (tests)` at `283777f` (`ci.yml:120-121`, `make test-api`) | | proven in CI |
| 81 `make test` | as plan 002 line 123 | | CI only |
| 82 `make check-docs` | as plan 002 line 124 | | CI only |

## The Delivers and Exit clauses

| Plan | Clause | State |
|---|---|---|
| 002 Delivers | fetch container, `https-file` connector, per-product landing, bronze by WAP in order | built; WAP on real Spark and Iceberg only in `run-path.sh` (MED 11) |
| 002 Delivers | dbt runner and wrapper, run records, `k2_input`, rollback, reconciliation, the marker after tests | built; `dbt-wrapper.sh` (21 PASS, pull request #255, but see HIGH 7) and `run-path.sh` claims 1, 3, 6 |
| 002 Delivers | one DAG per product, pinned to one release | built (`factory.py`); never scheduled |
| 002 Delivers | a storage key per engine; the paths around Trino closed | built: five policies, MinIO with no host port (render); `storage-keys.sh` 43 PASS. The catalog database keeps a host port (MED 13) |
| 002 Delivers | the backup set compiled from contracts | built; never run against Iceberg-written manifests (MED 20) |
| 002 Delivers | `currency-master`, `calendars`, `ecb-fx-rates` to gold, terms recorded | built; ECB and calendars on a throwaway by `run-path.sh`; terms at `contracts/fx-reference/ecb-fx-rates/product.odps.yaml:93-98` |
| 002 Exit | the three DAGs run | open: needs the live stack |
| 002 Exit | a corrupt file never reaches bronze `main`; a later file waits | `run-path.sh` claim 5 (MED 11) |
| 002 Exit | the same bytes again add no row | `run-path.sh` claim 4 (MED 11) |
| 002 Exit | a failing test rolls silver back; no reader saw the failed rows | canary: `dbt-wrapper.sh` run2; ECB: rollback without a reader poll (MED 12) |
| 002 Exit | a Trino stop mid-build reconciled; a deploy during a run stays on its release | open (HIGH 4) |
| 002 Exit | redirects to `http`, off-list hosts, private addresses fail with no network | proven (`make test-connectors`) |
| 002 Exit | each engine's key refused outside its warehouse | proven on a throwaway (`storage-keys.sh`) |
| 002 Exit | MinIO and Lakekeeper unreachable from the host and a fetch container | rows `expectations.yaml:165-169`, run only by `catalog-fence.sh` on a running stack: needs the live stack |
| 002 Exit | the backup lists the ECB landing | needs the live stack |
| 002 Exit | tag `v3.0.0-alpha.4` | not cut (`git tag -l 'v3.0.0-alpha.*'` ends at `v3.0.0-alpha.3.2`) |
| 003 Exit | every ECB expectation the same through SQL and REST | open (HIGH 5) |
| 003 Exit | no key 401; a denied table 403 | proven in unit and, for 403, on a throwaway; a never-published view answers 404 to a denied caller (`CHANGELOG.md:65-66`; MED 22) |
| 003 Exit | product documents validate against the frozen schema | proven offline (`api-schema.sh --release`, 7 PASS) |
| 003 Exit | `/v1/openapi.json` declares 3.1 | proven in unit |
| 003 Exit | a catalog page per product, each linking the dbt docs | proven offline (`catalog-site.sh`) |
| 003 Exit | every part, k2-api included, reports the release | built (`scripts/deploy.py:1234-1240`); needs the live stack |
| 003 Exit | tag `v3.0.0-alpha.5` | not cut |

Scope bullets not built, each where it is stated: the `governance/run-records` product document
(`CHANGELOG.md:87-88`); notebooks through Trino (`CHANGELOG.md:106`, to 004); the ECB timeout from 20
runs (`product.odps.yaml:90-92`, chapter 21 `:296-297`); the onboarding runbook "from a run of the
whole path on the live stack" (its Last verified line, `:287-288`: sections 4 to 7 not yet).

## The two reviews of `122699c`: what became of each item

R: deploy and secrets. F: access, identity and the data path. Q: a question either review left.

| Id | Status | Evidence | What remains |
|---|---|---|---|
| R1 | fixed | `34b4a8e`, `f38daa9`; `tests/test_deploy.py::test_k2_api_starts_on_the_way_in_and_is_removed_on_a_rollback_to_its_stub` | |
| R2 | fixed | `75864b5`; `docker/prometheus/prometheus.yml:35-37`; `tests/test_catalog_fence_compose.py::test_prometheus_rules_off_the_pointer_fail` | |
| R3 | fixed | `27d8527`; `test_deploy.py::test_preflight_names_every_missing_secret_file_of_the_target_and_its_make_command` | |
| R4 | fixed | `1303108`; `tests/test_engine_keys.py::test_a_pair_left_on_the_root_pair_gets_its_own_and_nothing_else_moves` | |
| R5 | fixed | `e6301f9`; `test_engine_keys.py::test_rotate_replaces_one_engines_secret_and_keeps_its_user` | the rotation reaches MinIO only when `lake-init` reruns (HIGH 8) |
| R6 | fixed | `071c909`; `docker-compose.yml:1145`; `test_deploy.py::test_lake_init_runs_the_target_release_script_and_an_older_release_skips_it` | a policy-only release does not apply (stated, ADR-054 `:248-250`) |
| R7 | fixed | `b34cf20`; fetch on `k2-egress` alone (`docker-compose.yml:1515`); `docker/airflow/tests/test_oneshot.py::Refusals::test_a_kind_off_the_schema` | internet egress: CRITICAL 1 |
| R8 | fixed | `7f484e3`; `test_deploy.py::test_a_ddl_failure_unpauses_only_when_no_attempt_went_past_the_one_shots` | |
| R9 | deferred (stated) | ADR-054 `:237-240`; `CHANGELOG.md:157-158`; chapter 22 `:316-318` | `.env.example:147` still says Spark can "never delete one" (LOW 39) |
| R10 | fixed | `621d9f4`; `test_catalog_fence_compose.py::test_every_engine_holds_its_own_storage_pair_and_only_lake_init_the_root` | |
| R11 | fixed | `9e7f405`; `test_oneshot.py::Refusals::test_a_secret_or_env_name_the_frozen_contract_does_not_list_for_the_kind` | |
| Q1 (deploy) | open, and a bug | the drain's argv was never checked against Airflow; it is wrong | HIGH 3 |
| Q2 (deploy) | settled | `storage-keys.sh:134-139` on a real Lakekeeper; ADR-054 `:12` | |
| Q3 (deploy) | open, stated nowhere | `docker/lake/init-lake.sh:85-91` prints Lakekeeper's error body; the test's fake never echoes | MED 31 |
| Q4 (deploy) | open, stated wrongly | `deploy-a-release.md:164-165` "never one an operator paused" | LOW 40 |
| Q5 (deploy) | fixed in part | `38b9f15`; `tests/test_check_env.py::test_a_storage_value_outside_the_safe_set_is_refused_by_name` | preflight runs only `--mode-only`; `init-lake.sh` does not refuse (LOW 41) |
| F1 | fixed in part | `741f9f8`; a spool subpath per DAG (`oneshot.py:212-218`) | a symlink in its own entry still reaches another product's (HIGH 6) |
| F2 | fixed | `6feec8e`, `61604c6`; `platform/k2gen/tests/test_emit_dbt.py::test_parse_with_k2_inputs_for_one_product_only` | a frozen sentence and a comment still say "at parse" (LOW 34) |
| F3 | fixed | `3f2257b`, `e1a2dbe`; `docker/dbt-runner/test_k2_dbt.py::test_a_commit_after_the_tests_by_a_query_dbt_did_not_run_is_refused_and_never_tagged` | |
| F4 | deferred (stated) | ADR-062 `:238-242`; `CHANGELOG.md:157`; chapter 22 `:322-323` | quality results are the builder's word |
| F5 | fixed in part, rest stated | `b34cf20`; ADR-036 Outcome `:475-483`; `CHANGELOG.md:158-159` | the parser still runs in fetch's process with internet access; `SECURITY.md` says otherwise (CRITICAL 1) |
| F6 | fixed | `7088083`; `domains/*/tests/::test_a_document_that_is_not_utf8_is_refused_before_expat_can_expand_it` | |
| F7 | fixed | `0063416`; `tests/test_fetch.py::test_a_trickling_body_stops_at_the_deadline_not_after_a_full_chunk` | |
| F8 | open, stated nowhere | `docker/lake/connector.py:357-374` unchanged since `122699c` | MED 31 |
| F9 | fixed | `93e26b6`; `factory.py:318-323` (`expire >> maintain`); `test_factory.py::Shape::test_edges_pools_and_trigger_rules` | |
| F10 | fixed | `f3ecacb`; `services/k2-api/tests/test_db.py::test_a_query_with_no_row_by_its_deadline_is_cancelled_on_trino` | a client-side bound only |
| F11 | fixed | `d4c8227`; `tests/access/test_access_verify.py::test_a_storage_key_row_passes_on_its_outcome_alone` | |
| Q1 (access) | settled | `e1a2dbe`; `test_k2_dbt.py::test_an_orphaned_builder_query_is_killed_before_reconcile_reads_anything`; measured in ADR-061 `:276-285` | the orphan did commit, so cancel-first was needed |
| Q2 (access) | settled | `dbt-wrapper.sh` Q2a and Q2b; ADR-061 `:286-290` | |
| Q3 (access) | settled, accepted | `dbt-wrapper.sh` q3; ADR-062 `:201-203` | the Jinja sandbox is the one barrier (MED 26) |
| Q4 (access) | open, stated nowhere | `services/k2-api/k2api/app.py:51` keeps `script-src 'unsafe-inline'` for `/dbt-docs/` | MED 31 |
| Q5 (access) | open, stated nowhere | `services/k2-api/k2api/keys.py:32` caches by inode, mtime and size | MED 31 |

## Scripts and CI

| Script | Can it fail? | Note |
|---|---|---|
| `scripts/lib/throwaway-lake.sh` | yes (`die` exits 2) | runs the working tree's `init-lake.sh`, not a release's copy (MED 17); a latent silent exit at `:135` (LOW 43) |
| `scripts/verify/storage-keys.sh` | yes | needs PASS > 0 and FAIL = 0; a traceback counts as a FAIL (`:260`) |
| `scripts/verify/dbt-wrapper.sh` | no, in one case | a crash of the claims step after PASS lines exits 0 (HIGH 7); run3's kill is not checked (MED 15) |
| `scripts/verify/oneshot-slots.sh` | yes | exceptions become a FAIL (`:362`) |
| `scripts/verify/run-path.sh` | yes | exactly 7 PASS (`:82`); a FAIL stops the run (`:528-530`); no pass recorded (MED 11) |
| `scripts/verify/api-schema.sh` | yes | passes on an empty product list (LOW 43) |
| `scripts/verify/catalog-site.sh` | yes | its self-test plants a token that must fail |
| `scripts/verify/catalog_drift.py` | yes, but | the tag against the run records is a SKIP in the CLI release-verify runs (MED 19) |
| `scripts/verify/catalog-fence.sh` | yes | `6:refused` matches the port only (LOW 43) |
| `scripts/verify/deploy_rehearsal.py` | yes | covers less than its header claims (MED 17) |
| `scripts/drop.sh` | yes | step 2b runs `backup-lake.sh` with `\|\| die` after the dry-run exit (`:358-359`; `tests/test_drop.py`) |
| `scripts/backup_lake.py` | yes, loudly | codecs `null` and `deflate` only (`:157`); any other raises; no silent under-copy found (MED 20) |

| Code | CI | Nowhere in CI |
|---|---|---|
| `docker/fetch`, `docker/lake/{connector,expire,maintain}.py`, `scripts/backup_lake.py`, `scripts/engine-keys.py` | `Python` (`pytest tests`) | their runs on real Spark, Iceberg and MinIO (`run-path.sh`, spine-e2e only) |
| `docker/dbt-runner`, `platform/runs`, `platform/catalog-site`, `domains/**` | `Python` (`make test-platform`, `ci.yml:75-76`) | `dbt-wrapper.sh`, `catalog-site.sh` |
| `docker/airflow/dags` | `Docker build (airflow)` (unit tests in the image) | `oneshot-slots.sh` |
| `services/k2-api` | `k2-api (tests)`; `Trino (access rules)` runs its two REST rows | `api-schema.sh` |
| the storage-key rows | `Trino (access rules)` | `storage-keys.sh` |

`make test` (`Makefile:48`) includes `test-connectors`, `test-api` and `test-platform`. MED 16 is the
gap: five proof scripts run in no CI job, make target or spine-e2e stage, two of which need no Docker.

## Findings

| # | Severity | file:line | Claim | Reality | Fix |
|---|---|---|---|---|---|
| 1 | CRITICAL | `SECURITY.md:44-47`; `README.md:69` | "A product's parser runs only in the fetch one-shot, which ... connects only to the hosts its contract's `allowHosts` names"; "a file product's fetch runs in a one-shot that ... reaches only its listed hosts" | The allow-list guards `fetch.py`'s own download. The parser runs in the same process (`docker/fetch/fetch.py:250-257`) on `k2-egress`, a plain bridge (`docker-compose.yml:35-36`) whose comment says "fetch reaches the internet" (`:29-30`). ADR-036's Outcome (`:475-483`) and `CHANGELOG.md:158-159` say a parser "can reach the internet beyond its allow-list"; the security page says the opposite | State in both that the allow-list bounds the download, not the parser, which can reach any internet host; link ADR-036's Outcome |
| 2 | HIGH | `scripts/check-budget.py:170`; `scripts/verify/run-path.sh:507` | The budget gate passes and CI stays green | At `05c6c18`: `FAIL: scripts/verify/run-path.sh: docker run 256 is not pinned`, exit 1. The parser does not know `--pids-limit` takes a value, so reads `256` as the image. CI's `compose` job runs it (`ci.yml:374-375`) | Add `--pids-limit` to `RUN_VALUE_FLAGS` and one case to `tests/test_check_budget.py` |
| 3 | HIGH | `scripts/deploy.py:598`, docstring `:558-560`; `tests/test_deploy.py:129` | The drain's argv is "read from apache-airflow-core 3.3.2's cli_config.py: ... `dags states-for-dag-run <dag> <run> -o json`" | In the 3.3.2 wheel's `airflow/cli/cli_config.py`, `states-for-dag-run` is in `TASKS_COMMANDS` (`:1446-1542`, the command at `:1537`), not `DAGS_COMMANDS` (`:1194`). `run()` raises on the non-zero exit (`deploy.py:206-208`); the drain resumes and re-raises (`:638-640`). Every deploy that drains while an affected DAG has a running run fails, `ALLOW_RUNNING=1` included; the test fake answers the same wrong argv. The first deploy (no DAG yet) does not meet it; the next one will, with the canary every 10 minutes | `"tasks", "states-for-dag-run"` in `running_tasks` and the fake; correct the docstring |
| 4 | HIGH | plan 002 `:19-20`, `:114-115` | A Trino stop mid-build is reconciled by the next run; a deploy during a run leaves that run on its release | No script, nothing on a throwaway, and no document names a replacement; only pull request #255's to-do list ("the chaos scripts"). Partial: `dbt-wrapper.sh` run3 and run4 kill the dbt container, not Trino; `test_factory.py:286` and `test_deploy.py:1134` fake Airflow | Write the two scripts (the deploy one needs HIGH 3 first), or name the partial proofs as their replacement in a durable document and run both as live drills before the tag |
| 5 | HIGH | plan 003 `:11`, `:68`; `CHANGELOG.md:18-19`, `:49-50` | Every ECB expectation gives the same answer through SQL and REST; "so REST and SQL give one answer" | Two of nine ECB table rows have a REST leg (`expectations.yaml:143`, `:150`). On a throwaway lake the gold base has no row, and the REST leg then proves only the 200 (`scripts/access_verify.py:478-481`); live every ECB row SKIPs (`:568-576`: the owner's password, and a product other than the canary). `run-path.sh` holds ECB gold rows (claim 3) but starts no k2-api. A row of ECB data has never been compared | After `run-path.sh` claim 3, run `p.analyst-a`'s REST leg against that lake (`throwaway-lake.sh` `api_up`); add `rest` to the `silver_rates` denial row |
| 6 | HIGH | `docker/lake/connector.py:113-147`; `docker-compose.yml:966`; `CHANGELOG.md:101-102` | fetch has "a spool mount per DAG: a parser is product code and must reach ... no other product's files" | fetch sees only `/spool/<dagId>` (`oneshot.py:212-218`), but the Spark tasks mount the whole volume (`k2-spool:/spool:ro`) and `Run.entry`, `spooled()`, `lines()` and `audits()` follow symlinks. Product A's parser can leave `raw.<ext>`, `fetch.json` and `rows.jsonl` as relative links to `../<B's dag>/<run>/...`: A's load then lands B's bytes, which match their own sha256, and stages B's rows on A's bronze, readable by A's readers. Proven by code and a symlink experiment off Docker; stated nowhere | In `Run`, refuse a link anywhere in the entry (`O_NOFOLLOW`, and `entry.resolve().parent == SPOOL / <dagId>`), with a test |
| 7 | HIGH | `scripts/verify/dbt-wrapper.sh:566-569` | "each claim below one PASS or FAIL line, then `<n> PASS, <m> FAIL`" | `probe claims ... \| tee` has its status dropped; the exit is `[ "$fail" = 0 ] && [ "$pass" -gt 0 ]`. A crash after some PASS lines (a missing `state-<run>.json`, `orphan-q1a.json`) exits 0 with the later claims never run; reproduced on a copy: `dbt-wrapper: 2 PASS, 0 FAIL`, `exit=0`. This script is the proof of the marker, the rollback and reconcile | Check `${PIPESTATUS[0]}`, or require exactly 21 PASS as `run-path.sh` requires 7 |
| 8 | HIGH | `docs/adr/ADR-054-a-storage-key-per-engine-with-catalog-signing-off.md:151-154` | "The next `lake-init` (every `make deploy` runs it) sets that user's secret ... so the old one stops working" | Deploy runs `lake-init` only when the plan names it, as the same ADR says at `:248-250`: a rotation changes `.env` alone, so the next deploy skips it, MinIO keeps the old secret (the one being retired stays valid), and a recreated holder is refused. `scripts/engine-keys.py:18` and `first-cutover-to-releases.md:222` say the same | "then `docker compose up --force-recreate --no-deps lake-init`, then recreate the holders", in all three |
| 9 | HIGH | `docs/adr/ADR-062-dbt-builds-silver-and-gold-per-product-through-one-wrapper.md:104-105` | "The publisher `pub_<fence>` owns the reader views and alone may move the tag" | The tag moves by `POST .../transactions/commit` straight to Lakekeeper with no credential (`docker/dbt-runner/k2_dbt.py:743-747`); Lakekeeper has no authentication, so any of the nine members of `k2-catalog` can move it. ADR-059 part 7 and chapter 22 `:172-173` state that exposure | "only the wrapper is meant to move it; Lakekeeper does not enforce this (ADR-059 part 7); the drift check names a tag moved otherwise" |
| 10 | HIGH | `docs/runbooks/onboard-a-file-product.md:271` | "`make access-verify LIVE=1  # the product's rows on the stack, once it is published`" | Live skips every row naming a product other than the canary (`scripts/access_verify.py:573-576`); `mk/access.mk:27` says "the canary and platform rows". An operator would take the product's access as proven live | "the canary, platform and storage-key rows; the product's own rows ran at release time, in preflight" |
| 11 | MED | `scripts/verify/run-path.sh`; `tests/test_lake_connector.py:6-7`; `docs/runbooks/file-source-audit-failed.md:21`, `:25`, `:203`; ADR-036 `:363-365` | The connector's shells are "proven on a throwaway lake by scripts/verify/connector-wap.sh"; `fx-bad-file.sh` "is the drill that will measure this page" | Neither script exists. `run-path.sh` (landed in `01e269f`, fixed in `fba74f9`) is the one run of the connector, `expire.py` and `maintain.py` against Spark, Iceberg and MinIO, and the replacement for plan lines 111 to 113, but no pass is recorded in the tree, the commits or pull request #255, and no document names it as the replacement | `ONLY="run" make spine-e2e` at the pushed head, its 7 PASS lines in the pull request; repoint the test header and the runbook; an Outcome line on ADR-036 naming `run-path.sh` |
| 12 | MED | plan 002 `:113`, `:129-130` | `dbt-failing-test.sh` "also polls the table as a reader through the test window"; `rolled_back` and `expired` rows | On ECB, `run-path.sh` claim 6 restores and expires with no reader polling; the poll is only on the canary (`dbt-wrapper.sh` run2). No script asserts an `expired` run record (`test_factory.py:226` only) | A poller in claim 6, as `dbt-wrapper.sh` has; assert the `expired` row |
| 13 | MED | `SECURITY.md:42-43`; `CHANGELOG.md:153-154` | "no port on the host reaches the lake's data but Trino's and k2-api's"; "the lake's objects are reached only through Trino" | `prefect-db` publishes `127.0.0.1:15432` (`docker-compose.yml:624`) and sits on `k2-catalog` and `k2-net` (render); it holds the catalog database under the superuser Lakekeeper shares, which can run a program inside that container (the P0 audit's HIGH, still open) | Name the port and that open row in both, or give Lakekeeper its own role and drop the port |
| 14 | MED | `platform/catalog-site/k2site.py:582-585`, `:603-609`; `scripts/deploy.py:1438` | `--strict`: "exit 1 when the run-time facts could not be read"; on a site failure "the site is the one before" | `generate()` publishes, swapping `current`, before `main` returns 2 or 1: `--strict --no-facts` printed `wrote .../current` and exited 1 with the swap done. The daily timer replaces a site with facts by one without and reports a failure; `sites()` can publish the catalog then fail on the dbt docs under the same message | Check problems and strict before `publish`; say which part was written |
| 15 | MED | `scripts/verify/dbt-wrapper.sh:538-539`, claim `:369-372` | "run3 R1 killed once its started row is written; run4 R1 reconciles it" | The kill's result is not recorded: a run3 that finished before the kill ends `audited`, which the claim accepts (`ev("run3")[-1] in ("reconciled", "audited", "rolled_back")`) | Record the kill's exit and require it |
| 16 | MED | `scripts/verify/{storage-keys,dbt-wrapper,oneshot-slots,catalog-site,api-schema}.sh`; `CHANGELOG.md:131-132` | These prove phases 002 and 003 | No CI job, make target or spine-e2e stage runs any of the five (`git grep`); `catalog-site.sh` and `api-schema.sh --release` need no Docker and still run nowhere | Both offline scripts in CI's contracts job after `ci-validate.sh`; `dbt-wrapper.sh` and `storage-keys.sh` as spine-e2e stages under `FULL=1` |
| 17 | MED | `scripts/lib/throwaway-lake.sh:118-123`; `scripts/verify/deploy_rehearsal.py:2-3`, `:352-364` | Lakes run "the real docker/lake/init-lake.sh ... as lake-init runs it"; the rehearsal shows "every claim of scripts/deploy.py" | Production runs the release's copy (`docker-compose.yml:1145`); both throwaways mount the working tree's. The rehearsal compose has no k2-api, `k2-site`, scheduler or `lake-init`, so the site step, the `/v1/release` check, `remove-<svc>` and a drain with DAGs run only against `tests/test_deploy.py`'s fakes (HIGH 3 is what that missed) | Run the release's `init-lake.sh` in `lake_init`; add k2-api and the scheduler to the rehearsal, or narrow its header |
| 18 | MED | `scripts/deploy.py:1366-1371`; chapter 22 `:201-203`, `:339`; chapter 21 `:241-243`; ADR-054 `:174-175` | At every `release-verify` "the probes, the storage-key rows and the drift row always run"; `LIVE=1` runs "the canary, platform, storage-key and REST rows" | One pending product makes `s20_checks` return before the live suite: no storage-key row runs at release-verify until every product is published, the first deploy of these phases included. Probe rows SKIP in every scope (`access_verify.py:693-694`). No REST leg can run live (`:568-576`) | Run the live suite for the published products; correct the four sentences |
| 19 | MED | `scripts/verify/catalog_drift.py:371`, `:387`; `scripts/deploy.py:1314` | "Each tag must equal its table's newest audited run record" | The CLI release-verify runs never gets `newest_audited`, so every tag check is a SKIP and the exit 0; only the wrapper's next reconcile, for its own product, compares | A `--runs` option reading the run records as `svc.runs` in the drift one-shot |
| 20 | MED | `tests/test_backup_lake.py:4`, `:28-51`; `scripts/backup_lake.py:157` | "manifests are real Avro written by fastavro" | The schemas are hand-written imitations of Iceberg's; nothing runs the copy against a manifest Spark or Trino wrote. Only `null` and `deflate` codecs; another raises and the backup fails loudly | Run `backup_lake.plan` on the real table `storage-keys.sh` writes, or commit one Iceberg-written manifest list and manifest as fixtures |
| 21 | MED | `docs/runbooks/lake-backup-and-restore.md:327`; `docs/operations/README.md:438` | Recreate with `docker compose up -d --force-recreate --no-deps ... k2-api` from the checkout; k2-api "any time" | The checkout's compose names `k2-api:local` with `build:` (`docker-compose.yml:1603-1606`); a release tags `<name>:<id12>` (`deploy.py:1812-1817`), so compose builds the checkout's image; deploy recreates from the release's compose and pins (`deploy.py:311-319`). `connector-failure.md:192` says never to | Recreate through the release (as `deploy.py` does) and say so once, in the operations README |
| 22 | MED | `services/k2-api/README.md:50-52`; ADR-061 `:300-304` | "a denial is 403 whatever the relation's publication state"; the 404 to a denied caller was "measured through `k2-api` by the access suite's REST rows" | `k2api/app.py:240-246` maps `INVALID_VIEW` to 404 `not_published` for a denied caller too, as chapter 22 `:324-326` and `CHANGELOG.md:65-66` say. `tests/access/prove.py:202-205` asks the denied principal over SQL only; k2-api is asked as the allowed reader | Replace the README sentence with chapter 22's; "measured over SQL" in an Outcome line |
| 23 | MED | `docs/operations/README.md:143-145` | The engine must offer "at least 30 GiB ... (29.250 GiB at the bootstrap peak ...)" | `bootstrap build 24 services 18.30 CPU 30.750 GiB` (both dbt slots); `fresh-install.md:42` and `setup.md:11` say 31 | "at least 31 GiB (30.750 GiB with both dbt runners)" |
| 24 | MED | `docs/development/dbt-labs/lab-1-a-source-and-a-model.md:328`, `:339-340`; `lab-2-...:206`, `:210`; `lab-3-...:51-60` | Bump the product "1.0.0 to 1.1.0"; "Set `version: 1.0.1`"; the wrapper's steps start at reconcile | `product.odps.yaml:9` is `1.1.0` already (`f731003`), so lab 1's bump is refused and lab 2's goes backwards; the wrapper now cancels orphaned queries first (`k2_dbt.py:304`, `:560`) and publish checks `built_here` (`:491-492`) | 1.1.0 to 1.2.0 in lab 1, 1.1.0 to 1.1.1 in lab 2; the two steps in lab 3 |
| 25 | MED | `docs/runbooks/onboard-a-file-product.md:108`, `:134-135`, `:238`; `airflow-scheduling.md:270` | `bash scripts/verify/ci-validate.sh /tmp/k2-ci` "after every change"; `make test-dbt-tree` then the rules suite; `NET=${K2_COMPOSE_PROJECT}_k2-net` | A second run into the same directory fails (the tag exists, `ci-validate.sh:37`; `docset.py:105-106`); `make test-dbt-tree` runs no rules suite (`mk/dbt.mk:12-14`; it is `make test-trino`); both names are commented out in `.env.example:121-137`, so `NET` becomes `_k2-net` | `rm -rf /tmp/k2-ci &&`; name `make test-trino`; `${K2_COMPOSE_PROJECT:-k2-market-data-platform}` and `${K2_SECRETS:-$HOME/k2-secrets}` |
| 26 | MED | `SECURITY.md:44`, `:47-49` | "Product code runs without keys or a way in. ... dbt models run in the `dbt-runner` one-shot, on `k2-catalog`" | `k2-catalog` holds the unauthenticated Lakekeeper and MinIO, and the runner holds three password files; only dbt's Jinja sandbox stands between a model and them, as ADR-062 `:201-203` accepts | Say the sandbox is the barrier and what the network reaches |
| 27 | MED | ADR-059 `:311` (Outcome) | "`k2-api` reaches Trino only" | k2-api is on `k2-net` (render), with MinIO (no key), `prefect-db`, Redpanda and Alertmanager; the same Outcome says MinIO is reached from any `k2-net` container (`:296-297`) | "talks only to Trino; on `k2-net` it can reach ..." |
| 28 | MED | `docs/runbooks/airflow-scheduling.md:161` | "`<task> exited <n>`: 1 a task error, 2 refused, 3 the batch failed its audit" | `connector.py:588`, `:591` also exit 2 on a bad argument or a pipeline and DAG mismatch (`argparse`, no JSON line), and `factory.py:70` does not retry 2: a release defect reads as "refused" | Return `EXIT_ERROR` there, or document "2 with no JSON line" |
| 29 | MED | `tests/access/test_access_verify.py:626`, `:628`, `:656` | Test values are obviously synthetic (the repository's data terms) | `"USD", "1.0512000000"` and `1.0513000000`: a realistic EUR/USD level, unlike every other FX fixture (`AAA`/`BBB`, 1.1, 2.2). Not the ECB's fixing for that date (committed before its publication), but not obviously invented | `"2.0000000000"` and `"2.0001000000"` |
| 30 | MED | `docs/runbooks/file-source-audit-failed.md:21` and plan 002 Verification | The chaos drills will measure this page's MTTR | Three of the five plan scripts are replaced by `run-path.sh` claims that measure no time; two are not built (HIGH 4); the MTTR rows stay unmeasured with no drill named | Name the drill that will measure each row, or say none is planned |
| 31 | MED | `docker/lake/connector.py:357-374`; `docker/lake/init-lake.sh:85-91`; `services/k2-api/k2api/app.py:51`; `k2api/keys.py:32` | The reviews' items are fixed or stated | Four are open and stated nowhere: publish reads `refs` then fast-forwards, so a write to `audit` between them publishes an unaudited head (F8); `lake-init` prints Lakekeeper's whole error body, unchecked for the credential (deploy Q3); `/dbt-docs/` allows inline script under a model description no test covers (access Q4); the key cache keys on inode, mtime and size (access Q5) | Fix F8 with one REST commit requiring `main` and `audit`; settle the three questions or state them in chapter 22's limits |
| 32 | LOW | `docs/development/spine-e2e.md:59`, `:108`, stage table `:43-50` | "all five stages" | Six since `05c6c18` (`stage run`, `spine-e2e.sh:75`), and the table has no `run` row | Add the stage |
| 33 | LOW | `docker/fetch/Dockerfile:5-8` | "reaches only k2-net (A8)" | `k2-egress` alone (`docker-compose.yml:1515`, `oneshot.py:69`) | "only k2-egress" |
| 34 | LOW | `docs/development/phase-001-interfaces.md:161-162`; `docker/dbt-runner/k2_dbt.py:406-407` | `k2_input` "refuses a missing, null or non-integer id at parse" | Since F2's fix a missing entry is refused only when the model compiles (`dbt/macros/k2_input.sql:26-31`); recorded in `spine-e2e.md:145-146`, not as a dated line in the frozen document | A dated line under A7; fix the comment |
| 35 | LOW | `docker-compose.yml:1451` | `tools ... Phase 001 stub` | A real image (`docker/tools/Dockerfile`, `:1454-1461`); the numbers are right | "tools one-shot, profile tools, not in the sums" |
| 36 | LOW | `tests/test_lake_connector.py:422`; `tests/test_backup_lake.py:355` | The connector imports no product code; backup never reads the root pair | Both check that strings are absent from the source; `__import__`, `runpy` or a built-up name passes | The AST walk of `test_oneshot.py:332`; assert the copy's environment |
| 37 | LOW | `docs/operations/README.md:19`; `docs/development/testing.md` (parsers row, `make test-dbt` "about 75 s"); `docs/runbooks/README.md:103`; `deploy-a-release.md:90-93`; `lake-backup-and-restore.md:41-42`; `fresh-install.md:61-63` | dbt-runner under a profile of its name; parsers run by CI's python job alone; first cutover "not yet on the live stack"; a phase tag builds `k2-capture`; backup reads "the two key names alone"; `K2_API_PORT` in `.env` | The profile is `build`; parsers are in `make test-platform`; the cutover ran live (`first-cutover-to-releases.md:268`); a running capture keeps its image (`deploy.py:1787-1792`); it reads four names; `.env.example` has no `K2_API_PORT`; 75 s has no stated run | Correct each |
| 38 | LOW | `lab-1:25-26`, `:299`; `lab-2:58-59`, `:107`; `lab-3:133`; `docs/development/reviewing-dbt-work.md:14` | Every model opens with "What this does and why"; "its line 135"; `year()`; `29 passed`; `67 passed` | Two models do not (`events.sql:1`, `test_results.sql:1`); the line is `dbt-test.sh:180`; the spike lists `day(ts)`, `month(d)`; the suites have grown since (about 31 and 79 functions, not rerun) | Narrow the claim; drop the line number; rerun and paste |
| 39 | LOW | `onboard-a-file-product.md:6`, `:63-64`, `:243-244`; `file-source-audit-failed.md:90-91`; `airflow-scheduling.md:207`; `.env.example:147` | "the three products this platform runs"; currency bronze is rebuilt as "the list can be fetched again"; run records from fetch to publish; a failed batch's file kept "until an operator clears"; Spark can "never delete one" | Not live; the contract says "rebuilt from the landed files"; `build` and `maintain` write rows too; `maintain.py:166-169` keeps it for good; Spark's container holds the maintenance key (R9, stated) | Correct each |
| 40 | LOW | `docs/runbooks/deploy-a-release.md:164-165` | Deploy unpauses "exactly those ... never one an operator paused" | A DAG an operator pauses after a deploy died mid-drain is in `deploy-paused.json` and is unpaused by the next deploy (`deploy.py:602-614`) | Say so, and how to keep one paused |
| 41 | LOW | `docker/lake/init-lake.sh:52`; `scripts/deploy.py:776` | Storage values outside the safe set are refused | Only `make up` runs the full `check-env`; preflight runs `--mode-only` and `init-lake.sh` does not refuse a root password holding `"` or `\` | The same refusal in `init-lake.sh` |
| 42 | LOW | plan 002 `:118` | `make access-verify` probes MinIO and Lakekeeper | It skips probe rows (`SKIP_PROBE`, `access_verify.py:103`); `catalog-fence.sh` runs them inside `release-verify` | Name `make catalog-fence` in the plan's landed line |
| 43 | LOW | `scripts/lib/throwaway-lake.sh:135`; `scripts/verify/catalog-fence.sh` (`dial`, `6:refused`); `scripts/verify/api_schema.py:57-104`; `scripts/deploy.py:40-60`, `:1528` | | A release with no `ddl/spark/` exits `up` silently under `pipefail`; an open host port passes as refused when another project publishes it; zero products pass; the docstring omits the `/v1/release` check; the sites render before the drained DAGs resume | Each a one-line fix |
| 44 | LOW | chapter 22 `:63`, `:177`, `:198`; ADR-062 `:106`; `CLAUDE.md:191`; `CHANGELOG.md:16`, `:68` | `svc.runs` writes `governance.run_records.run_record`; "third-party images never join"; a row "whose principal holds an API key" runs through k2-api; `LIVE=1` with "`.env` loaded"; the two version headings | It writes `governance_build.` (`k2runs.py:14`); Lakekeeper, MinIO, Postgres and Trino are third-party and join; a row needs `via: [sql, rest]`; the recipe loads no `.env` (keys come from the shell, `mk/access.mk:12-13`); no link references, no tags | Correct each; add the links when tagging |
| 45 | LOW | `README.md:16-19`, `:69`; `CLAUDE.md:9-12` | "What runs end to end today is ... ISO 4217 currencies, TARGET calendars and the ECB's ... rates, each fetched"; "Airflow runs one DAG per product of the deployed release" | True only after the deploy (live is `v3.0.0-alpha.3.2`); calendars is built from a seed with no fetch (`contracts/reference-data/calendars/product.odps.yaml:2`) | "calendars from a seed"; the rest holds once deployed |
| 46 | LOW (stated) | `SECURITY.md:127-131` | Check 2 "prints `loopback 000` too" | Not run; its own `VERIFY` marker at `:131` says so | Run after the deploy |
| 47 | LOW (stated) | plan 002 `:96-98`; plan 003 `:58`; plan 002 `:68-69` | The ECB timeout from 20 runs; the onboarding runbook from a live run; the run-records product | Not built; stated at `product.odps.yaml:90-92`, the runbook's Last verified line, `CHANGELOG.md:87-88` | As stated |
| 48 | LOW | `docs/research/2026-11-27-phases-002-003-build-breakdown.md` W1-20 | `grep -c runs.password` prints 1 | Prints 2 (source and target) | none: a dated snapshot |

Counts: 1 CRITICAL · 9 HIGH · 21 MED · 17 LOW.

Dropped after checking: plan 003's budget, 19.40 CPU / 32.375 GiB, which looked unlike the gate's
figures, is the build and fetch profiles together (18.80 / 31.250) plus the two retired services, as
ADR-010's Outcome says; and the `governance/run-records` product, which `CHANGELOG.md:87-88` states.

Guardrails, each clean but for the rows above: no Avro, ClickHouse DDL or schema chapter changed and
no lake DDL outside the k2gen goldens; no `cpus` or `memory` value changed, and the gate, ADR-010's
Outcome and the compose summary print the same 15.80 / 27.750 steady and 17.30 / 29.250 bootstrap; no
status file; no em-dash on an added line (the count over `git diff origin/main...HEAD` prints `0`); the fixtures are
patterned (ECB 1.1 to 5.5 per euro on 2026-03-02 to 04, invented countries for ISO 4217), the TARGET
closing days public calendar facts; the only key-shaped strings are AWS's documentation examples, and
the nine values added to `.env.example` are placeholders; no employer name; every new dependency
pinned with `==` or a lock. Of 28 sampled tests, 2 are weak (LOW 36) and none tautological. Every
Accepted ADR the branch touched is a pure append, ADR-035 its status line only.

## The build breakdown, step by step

At `05c6c18`: 59 built or built differently, 5 in part, 9 missing, of 73.

| Id | Verdict | Evidence |
|---|---|---|
| W0-1 | built | `init-lake.sh:161-185`; `tests/test_init_lake.py:158-209` |
| W0-2 | built | `docker/lake/minio/policies/*.json`; `init-lake.sh:235-248` |
| W0-3 | built | `expectations.schema.json:123-140`; `access_verify.py:196-312` |
| W0-4 | built | `scripts/verify/storage-keys.sh` |
| W0-5 | built | `fetch.py:66`; `test_fetch.py:292-366`; `make test-connectors` |
| W0-6 | built | `fetch.py:25-29`, `:70`; `test_fetch.py:380-530` |
| W0-7 | built | `oneshot.py:94`, `:263-265`; `test_oneshot.py` |
| W0-8 | built | `scripts/verify/oneshot-slots.sh` |
| W1-1 | built differently | fetch on `k2-egress`, not `k2-net` (`docker-compose.yml:1515`) |
| W1-2 | built | seven environment names (render) |
| W1-3 | built | own keys in compose; `check-env.py:119-122`; live leg not run |
| W1-4 | built | `model.py:79-87`, `:148-157` |
| W1-5 | built differently | pointers for DAGs and rules; proven by `test_catalog_fence_compose.py:290-310`, not the rehearsal |
| W1-6 | built | `deploy.py:537-617`; but HIGH 3 |
| W1-7 | missing | no product document; stated (`CHANGELOG.md:87-88`) |
| W1-8 | built | `platform/runs/k2runs.py` |
| W1-9 | built | `connector.py:150-170` |
| W1-10 | built | `connector.py:241-337` |
| W1-11 | built | `connector.py:352-394`; `expire.py` |
| W1-12 | built differently | no `connector-wap.sh`; `run-path.sh` claims 4 and 5 |
| W1-13 | built | `k2_results.sql`; `dbt-test.sh:95-181` |
| W1-14 | built | `k2_dbt.py:386-413` |
| W1-15 | built | `k2_dbt.py:278-299` |
| W1-16 | built | `k2_dbt.py:481-555`; `dbt-wrapper.sh` run8 |
| W1-17 | built | `k2_dbt.py:304-386`; the CLI drift check still SKIPs its run-record half (MED 19) |
| W1-18 | built | `dbt-wrapper.sh`; but HIGH 7 |
| W1-19 | built | `factory.py`; `test_factory.py` |
| W1-20 | built | the scheduler mounts `runs.password` and the CA |
| W1-21 | built | `maintain.py`; `run-path.sh` claim 6 |
| W1-22 | built | `backup_lake.py`; `drop.sh:358-359`; live leg not run |
| W1-23 | built | MinIO with no host port; probe rows `expectations.yaml:168-169` |
| W1-24 | missing | needs the live stack |
| W1-25 | built differently | `stage run` (`spine-e2e.sh:75`) on calendars and ECB, not the canary |
| W2-1 | built | `contracts/reference-data/currency-master/` |
| W2-2 | built differently | `iso4217.py` declares no audit; the connector's generic check stands in |
| W2-3 | built | `models/currency.sql`; live leg not run |
| W2-4 | built | `contracts/reference-data/calendars/`; `run-path.sh` claim 1 |
| W2-5 | built | `ecb-fx-rates/product.odps.yaml` |
| W2-6 | built | `ecb_fx.py`; `test_ecb_fx.py:108` |
| W2-7 | built | `silver_rates.sql`; `unit_tests.yml` |
| W2-8 | built | `gold_eur_rates_daily.sql`; `run-path.sh` claim 3 |
| W2-9 | built | `expectations.yaml:143-156` |
| W2-10 | built | `contracts/governance/quality-results/` |
| W2-11 | built | `run-path.sh` claims 2, 3, 7; the spool seeded, the network fetch not run |
| W2-12 | built differently | `run-path.sh` claim 5 |
| W2-13 | built differently | `run-path.sh` claim 4 |
| W2-14 | partly | `run-path.sh` claim 6; no reader poll on ECB, no `expired` row (MED 12) |
| W2-15 | missing | HIGH 4 |
| W2-16 | missing | HIGH 4 |
| W2-17 | missing | no `timeoutMinutes`, no benchmark; needs live runs |
| W2-18 | built | ADR-054, ADR-062; ADR-035 status line; ADR-010 Outcome |
| W2-19 | partly | three runbooks, each "Last verified: not yet" |
| W2-20 | partly | chapter 21 and `CLAUDE.md` done; `spine-e2e.md` not (LOW 32) |
| W2-21 | missing | this file is the audit; no scratch release recorded; no tag |
| W3-1 | built | `services/k2-api/pyproject.toml`; `test_skeleton.py:62` |
| W3-2 | built | `app.py:235`, `:264-277` |
| W3-3 | built | `keys.py`; `test_keys.py`; live leg not run |
| W3-4 | built | `app.py:331-436`; `test_data.py` |
| W3-5 | built | k2-api on `k2-net`, `127.0.0.1:8095` (render) |
| W3-6 | built | `test_deploy.py:946`; live leg not run |
| W3-7 | built | `throwaway-lake.sh:34-39`; two REST rows (HIGH 5) |
| W3-8 | built | `api-schema.sh` |
| W3-9 | built | `platform/catalog-site/k2site.py` |
| W3-10 | built | `catalog-site.sh` |
| W3-11 | built | `k2_dbt.py:772-832`; `app.py:108-109` |
| W3-12 | built | `deploy.py:1398-1436`; `scripts/systemd/k2-site.*` |
| W3-13 | built | lab 1 (MED 24) |
| W3-14 | built | lab 2 (MED 24) |
| W3-15 | built | lab 3 (MED 24) |
| W3-16 | built | `reviewing-dbt-work.md`; `.github/pull_request_template.md` |
| W3-17 | partly | sections 1 to 3 on the working tree; not from a live run |
| W3-18 | built | `README.md`, `docs/operations/`, chapter 22, ADR-010 |
| W3-19 | missing | no tag |

The breakdown is a dated snapshot written before the work; its "Facts this builds on" and its budget
line describe `origin/main` and are not findings.

## Frozen interfaces

Every departure but one carries a dated line: the sixth expectation shape
(`expectations.schema.json`, `phase-001-interfaces.md` A9), `k2-egress` (`oneshots.schema.json`, A13),
`audits.json` and the per-DAG spool, dbt's target under `/tmp`, `PLATFORM_DIR` and `PLATFORM_CODE`
(`platform/k2gen/INTERFACES.md`). The wrapper's new first step, cancelling the builder's orphaned
queries, and the publish's query-id tie are in ADR-062 (`:115`, `:144-147`), not in the frozen step
list. The one with neither is `k2_input`'s "at parse" (LOW 34). `services/k2-api/openapi.yaml` is
unchanged, and `test_skeleton.py:69` holds the app to exactly its routes.

The 46 rows the branch adds to `tests/access/expectations.yaml`: 9 owner-only denials, 12 ECB rows (one
read, eight denials, three statements), 4 probes, 21 storage keys. Each comment matches the compiled rules and the policy files, but two: the ECB block's
"through REST as well as SQL" holds for two rows (HIGH 5), and the probe block's "Run on the stack by
`scripts/verify/catalog-fence.sh`" is right where `make access-verify` is not (LOW 42).

## Live verification, to be run after the deploy

Run on the live stack after `make deploy` of the release cut from the pushed head. The operator
appends each result below this table with the date, the release id and the trimmed output; a row is
never rewritten.

| # | Command | Expected | Result |
|---|---|---|---|
| 1 | `make deploy RELEASE=<id> && make release-verify` | exit 0; a `k2-api: GET http://127.0.0.1:8095/v1/release names <id>` line; `catalog-fence: <n> PASS, 0 FAIL` with the rows `fetch -> lakekeeper:8181`, `fetch -> minio:9000`, `host -> 127.0.0.1:9000`, `host -> 127.0.0.1:9001` refused | |
| 2 | `curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:9000`, then `:9001` | `000` twice | |
| 3 | `python3 scripts/check-budget.py` | PASS; `bootstrap default 23 services 17.30 CPU 29.250 GiB` | |
| 4 | `docker exec k2-airflow-scheduler airflow dags list` | `access_canary`, `calendars`, `currency_master`, `ecb_fx_rates`, `quality_results`, each from the release's DAG folder | |
| 5 | `docker exec k2-airflow-scheduler airflow dags test calendars`, then `currency_master`, then `ecb_fx_rates` | each exits 0 | |
| 6 | `trino_cli svc.platform "SELECT run_id, task, event, release_id FROM governance_build.run_records.run_record WHERE run_id LIKE 'ecb_fx_rates/%' ORDER BY recorded_at"` (`docs/operations/quick-reference.md:189`) after the first scheduled run | one row per task and outcome, each with the release id deployed in row 1 | |
| 7 | `make access-verify LIVE=1` once no product is pending | exit 0; the 21 storage-key rows PASS; ECB rows SKIP, as `access_verify.py:568-576` decides | |
| 8 | the plan 003 `curl` lines with `p.analyst-a`'s key (`make api-key PRINCIPAL=p.analyst-a`): `?limit=5`, no key, `bronze_daily` | rows; `401`; `403` | |
| 9 | the same five gold rows read by `p.analyst-a` over SQL (its own password) and over REST (row 8) | equal, value for value (HIGH 5's live half) | |
| 10 | `bash scripts/verify/api-schema.sh` | `7 PASS, 0 FAIL` and OpenAPI `3.1.0` | |
| 11 | `curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8095/dbt-docs/`, then `/catalog/` | `200` twice | |
| 12 | `make backup && make backup-verify` after the first ECB fetch | a landing under `fx-reference/ecb-fx-rates/`, each table's snapshot id, exit 0 | |
| 13 | while an `ecb_fx_rates` build runs: `docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' <k2-dbt-runner-...> \| cut -d= -f1` | the seven `K2_` names and the image's own `PATH`-like lines, no other `K2_` and no `*PASSWORD*` | |
| 14 | `curl -s 127.0.0.1:9090/api/v1/rules \| python3 -c "import json,sys; print(len(json.load(sys.stdin)['data']['groups']))"` | the release's rule groups, more than 0 | |
| 15 | a second deploy while `access_canary` runs (after HIGH 3's fix) | the drain waits, then deploys; the run's records name the release it started on | |

## Known open items, and where they are durable

| Item | Durable in |
|---|---|
| Quality results are self-attested (F4) | ADR-062 `:238-242`; `CHANGELOG.md:157`; chapter 22 `:322-323` |
| Spark's container holds the maintenance key (R9) | ADR-054 `:237-240`; `CHANGELOG.md:157-158` |
| A parser can reach the internet beyond its allow-list (F5) | ADR-036 Outcome `:475-483`; `CHANGELOG.md:158-159`; chapter 21 `:306-307`. Not in `SECURITY.md` (CRITICAL 1) |
| A policy-only release does not reach MinIO | ADR-054 `:248-250`; chapter 21 `:316-317` |
| `make rollback` does not drain | chapter 21 `:314-315` |
| Images kept releases name are never pruned | chapter 21 `:320-321` |
| CI compiles with `--previous none` | chapter 21 `:318-319` |
| A denied caller learns whether an output is published | chapter 22 `:324-326`; `CHANGELOG.md:65-66` |
| The ECB timeout from 20 runs | `product.odps.yaml:90-92`; chapter 21 `:296-297` |
| No run-records product document | `CHANGELOG.md:87-88` |
| Notebooks through Trino | `CHANGELOG.md:106` (phase 004) |
| The catalog database on `127.0.0.1:15432` | the P0 audit's open HIGH only (MED 13) |
| The two missing chaos scripts, the run stage's pass, the scratch release, the first deploy | pull request #255's description only: not durable (HIGH 4, MED 11) |
| F8, deploy Q3, access Q4 and Q5 | nowhere (MED 31) |

### Results, 2026-11-27, release `v3.0.0-alpha.4-rc.3-48ad71fba732`

Run by the operator on the live stack after `make deploy RELEASE=v3.0.0-alpha.4-rc.3-48ad71fba732`
(10:50 to 10:51 UTC); output trimmed, never altered.

1. `make deploy` exit 0 in 56 s, then `make release-verify` exit 0: PASS. `k2-api: GET
   http://127.0.0.1:8095/v1/release names v3.0.0-alpha.4-rc.3-48ad71fba732`; `catalog-fence: 10 PASS, 0 FAIL`;
   `catalog drift check clean (0 differences, pending none, 6 skipped, 0 left by a release since)`;
   `identity.sh: 18 PASS, exit 0`.
2. MinIO's `127.0.0.1:9000` and `:9001`: PASS, both closed from the host after the `rc.2` deploy, and
   the fence's `host -> 127.0.0.1:9000` and `:9001` rows are among row 1's 10 PASS.
3. `python3 scripts/check-budget.py`: PASS, `bootstrap default      23 services   17.30 CPU   29.375 GiB`
   (29.375, not 29.250: `167b93a` gave `lake-init` 256M).
4. `airflow dags list`: PASS, the five DAGs; `airflow dags list-import-errors`: `No data found`.
5. `airflow dags test` not run as written; the first runs instead, each success: `calendars`,
   `ecb_fx_rates` (121.7 s, every task success, `expire` skipped), `access_canary`, and after `rc.3`
   `currency_master` (48 s) and `quality_results` (21 s).
6. not yet run.
7. `access-verify LIVE=1 passed: {'PASS': 82, 'FAIL': 0, 'SKIP': 30}`, inside row 1's `release-verify`: PASS.
8. As `p.analyst-a`, the key read from `~/k2-secrets/api-keys/p.analyst-a.key`: gold with `limit=10000`
   200; no key 401; `bronze_daily` 403: PASS. The plan's `?limit=5` on the whole table is 422
   `result_too_large`, `more than 5 rows match; narrow the filters (cursors arrive in phase 007)`; the
   same with two equality filters 200.
9. not yet run on the live stack (`scripts/verify/run-path.sh` claim 4 compares them on a throwaway lake).
10. `bash scripts/verify/api-schema.sh`: `8 PASS, 0 FAIL`, and `/v1/openapi.json` says `3.1.0`: PASS (the
    table expected 7; the script counts 8 today).
11. `/dbt-docs/` and `/catalog/`: 200 twice: PASS; `/healthz`, `/v1/release` and `/v1/openapi.json` 200
    too.
12. `make backup`: `backup-lake: run runs/20261127T100533Z: 60 objects, 426190 bytes fetched`; `make
    backup-verify`: a snapshot line for each of the six restore tables, `backup-verify: landing
    s3://k2-landing/fx-reference/ecb-fx-rates/: 1 objects, 69492 bytes`, `backup-verify: OK: 0 problem(s)`:
    PASS, after the fix `092ba53`.
13. not yet run.
14. not yet run.
15. In part: `rc.3`'s drain met real DAGs, `drained access_canary, calendars, currency_master,
    ecb_fx_rates, quality_results (the DAG code moved since v3.0.0-alpha.4-rc.2-e2dcf759de67) in 11 s`, and
    unpaused the three it paused; the run records of a run overtaken by the deploy are not yet checked
    (`scripts/chaos/deploy-mid-run.sh`, not yet run).

## This file's own gates

Run at `05c6c18` with this file and the index row: `python3 scripts/check_docs.py words <this file>
docs/audits/README.md` exit 0; `python3 scripts/check_docs.py counts` exit 0; `python3 scripts/check_docs.py
data` exit 0; the relative-link check of `scripts/check-docs.sh` (a) on both files, no broken link.
`make check-docs` was not run.

## Resolutions

Appended after the audit; the findings above stand as written.

- **CRITICAL 1. Resolved in `b6f32cc`**: `SECURITY.md` and `README.md` now say the allow-list guards the
  download, not the parser, which runs afterwards in the same container with ordinary sockets on
  `k2-egress` and can reach the internet, but no network of the stack; the glossary's two entries and
  chapter 20 said the same of the container and are corrected. ADR-036's Outcome already said it.
- **HIGH 2. Resolved in `15574f4`**: the pin gate knows `--pids-limit` and `--network-alias` take a value;
  `python3 scripts/check-budget.py` prints PASS at `4b73b29`. No case was added to
  `tests/test_check_budget.py`.
- **HIGH 3. Resolved in `5ce3cba`**: the drain asks `airflow tasks states-for-dag-run`, and pauses and
  unpauses with `-y`; the test's fake answers the corrected argv.
- **HIGH 6. Resolved in `dc7ec1a`**: the connector opens each spool file one level at a time from `/spool`
  with `O_NOFOLLOW` (the DAG and run directories included) and requires a regular file with one link;
  a link at any level, a hard link, a FIFO (opened without blocking) or another kind is refused, exit 2,
  nothing landed, the message naming the entry. `tests/test_lake_connector.py` plants each in real files.
  fetch's `write_new` already ends at one link; `maintain.py` and `expire.py` read no spool.
- **HIGH 7. Resolved in `ca1b8dc`**: `dbt-wrapper.sh` exits 0 only when its claims step exited 0
  (`PIPESTATUS`) and printed its last line `claims: every claim run, 0 FAIL`; `tests/test_verify_exit.py`
  runs the script's own tail with a stub that prints two PASS lines and crashes. `storage-keys.sh`'s
  Spark claims and tools steps dropped their exits the same way and now add a FAIL on one.
- **HIGH 8. Resolved in `42e011e`**: `make lake-init [RECREATE="<service> ..."]` runs lake-init on the live
  release with deploy's own invocation, then recreates the named services through the release;
  ADR-054, `scripts/engine-keys.py` (its help and its last line, the holders derived from compose) and
  `first-cutover-to-releases.md` give the order: rotate, `make lake-init RECREATE=...` (`trino` for
  trino, `spark-iceberg` for spark and maintenance, none for backup and tools).
- **HIGH 9. Resolved in `ecffd20`**: ADR-062 and lab 3 say only the wrapper is meant to move the tag,
  that Lakekeeper does not enforce it, and that the network fence and the commit's compare-and-set
  requirements protect it.
- **HIGH 10. Resolved in `5c3325c`**: the onboarding runbook says live runs the canary's, the platform's
  and the storage-key rows, the product's own rows SKIP there and ran at release time in deploy's
  preflight; the verify-access runbook's rollback step says the same.
- **MED 13. Resolved in part in `b3ad3fc`**: `SECURITY.md` names `prefect-db`'s `127.0.0.1:15432` as the
  path to Lakekeeper's and Airflow's databases without Trino, guarded by loopback and the shared
  superuser password; `CHANGELOG.md:153-154` is unchanged.
- **MED 31. Stated in `4b73b29`, not fixed**: `CHANGELOG.md` 3.0.0-alpha.4 has a "Known and open" heading
  naming F8, deploy Q3 and access Q4 and Q5, each with its file.
- **HIGH 4. Resolved in `9c61f26` and `ed42d71`**: `scripts/chaos/trino-stop-mid-build.sh` recorded
  `trino-stop: 6 PASS, 0 FAIL in 159 s` on a throwaway lake on 2026-11-27 (`scripts/chaos/README.md`);
  `scripts/chaos/deploy-mid-run.sh` is written and not yet run on the live stack.
- **HIGH 5. Resolved in `5c26fc6`**: `scripts/verify/run-path.sh` claim 4 compares ECB gold read
  through the lake's `k2-api` as `p.analyst-a` with the rows the same principal reads over SQL, on
  rows, in key order; no key 401, `bronze_daily` and `silver_rates` 403. The live half is row 9 of the
  results above, not yet run.
- **MED 11. Resolved in part in `9c61f26`**: `scripts/chaos/fx-bad-file.sh` exists and recorded
  `fx-bad-file: 3 PASS, 0 FAIL in 126 s` on 2026-11-27; `tests/test_lake_connector.py:7` still names
  `scripts/verify/connector-wap.sh`, which does not exist.
- **MED 12. Resolved in `9c61f26`**: `scripts/chaos/dbt-failing-test.sh` polls gold as `p.analyst-a`
  through its view every 50 ms across the failed build and asserts the expire task's run record (task
  `expire`, event `passed`, its detail the ids); `dbt-failing-test: 6 PASS, 0 FAIL in 160 s` on
  2026-11-27.
- **MED 20. Resolved in `092ba53`**: the first backup of real tables on the live stack failed reading a
  gzip-compressed Iceberg metadata file (`<v>.gz.metadata.json`) as JSON; the gzip magic now decides, and
  `make backup-verify` then printed a snapshot line for each of the six restore tables and `OK: 0
  problem(s)` on the live stack (results row 12).
- **HIGH 4, the live half. Resolved in `e34c6d5`**: `scripts/chaos/deploy-mid-run.sh ecb_fx_rates <release>` ran
  on the live stack on 2026-11-27. Onto `v3.0.0-alpha.5` it printed `deploy-mid-run: 2 PASS, 2 FAIL in 119 s`:
  the run finished on R-1 and its run-record rows carry R-1, and the deploy failed its verify step on
  `DRIFT view fx_reference.ecb_fx_rates__base.silver_rates__dbt_tmp`, a relation dbt holds while it builds,
  and rolled back. With the drift check waiting for such relations, onto `v3.0.0-alpha.5.1-rc.1` it
  printed `deploy-mid-run: 4 PASS, 0 FAIL in 105 s`.

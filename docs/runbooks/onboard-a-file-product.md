# Runbook: Onboard a file product

Use this to add a product whose source is one file fetched over HTTPS (connector kind
`https-file`): its documents, its parser and audits, the checks before a pull request, the release,
the secrets it needs, the deploy, its first run, and its catalog page. The worked example is the
three products this platform runs: `fx-reference/ecb-fx-rates` (a file, bronze, silver and gold),
`reference-data/currency-master` (a file, bronze and one dbt output) and `reference-data/calendars`
(no file: a seed a person writes, built by dbt). A Kafka source is phase 004's; granting a reader is
[grant-and-revoke-access.md](./grant-and-revoke-access.md). Why the path is shaped this way:
[ADR-056](../adr/ADR-056-unified-data-product-platform.md) and
[ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md).

> **Plan 003 says this runbook is written last, from a run of the whole path on the live stack.**
> Sections 1 to 3 ran on 2026-11-27 on the working tree, no container started. Sections 4 to 7 ran
> on the live stack on 2026-11-27, for the ECB and currency-master products, through the deploys of
> `v3.0.0-alpha.4-rc.2-e2dcf759de67` (which added them) and `v3.0.0-alpha.4-rc.3-48ad71fba732`;
> the output below is theirs. A command that did not run there is marked not yet.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded** (sections 4 to
7, in the live checkout); sections 1 to 3 need no `.env`.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Write the documents and the parser | a day | not measured: a person's work |
| 2 | Check them, no container | < 5 min | `ci-validate.sh` 1.6 s, `k2gen check --previous` 0.3 s, `make test-k2gen` 37 s, the dbt tree proof 75 s (2026-11-27, below) |
| 3 | The pull request | review | not measured |
| 4 | Release and secrets | < 10 min | not measured: each `make password` and `make api-key` exited 0 (2026-11-27) |
| 5 | Deploy | < 10 min | 166 s, the deploy that added the products; 56 s for the next, with a drain (2026-11-27, below) |
| 6 | First run | the product's own | `ecb_fx_rates` 121.7 s, `currency_master` 48 s (2026-11-27) |
| 7 | Its catalog page | < 1 min | the site step, 16.0 s inside the deploy that added the products (2026-11-27) |

---

## 1. Write the documents and the parser

**When**, a source has been chosen and its terms of reuse read.

**Expected behaviour**, every fact about the product lives in its documents; the compiler derives
the rest. Names follow from two words, the domain and the product, kebab case:

| Name | Rule (`platform/k2gen/k2gen/model.py`) | ECB example |
|------|-----------------------------------------|-------------|
| fence | the product name with `-` as `_` (`fence()`) | `ecb_fx_rates` |
| DAG id | the fence | `ecb_fx_rates` |
| bronze, landing log, audit results | `lake.<fence>.<table>`, `lake.<fence>.landing_files`, `lake.<fence>.audit_checks`, in warehouse `k2` | `lake.ecb_fx_rates.bronze_daily` |
| landing prefix | `k2-landing/<domain>/<product>/<fetch day>/<sha256>.<ext>` | `fx-reference/ecb-fx-rates/` |
| dbt outputs | `<domain fence>_build.<fence>__base.<table>`, read by readers as `<domain fence>.<fence>.<table>` | `fx_reference.ecb_fx_rates.gold_eur_rates_daily` |
| builder, publisher | `dbt_<fence>`, `pub_<fence>` (`builder()`, `publisher()`): not declared, known from the product | `dbt_ecb_fx_rates`, `pub_ecb_fx_rates` |

**The documents**, under `contracts/<domain>/<product>/`; the domain must be one of
[`contracts/domains.yaml`](../../contracts/domains.yaml) (a new domain is a new entry there, with
its owner and kind, and a new Lakekeeper warehouse at the next deploy, ADR-044). The two file
products hold:

| File | `ecb-fx-rates` | `currency-master` | What it says |
|------|----------------|-------------------|--------------|
| `product.odps.yaml` | yes | yes | the product (ODPS): id, status, purpose, limitations, its input ports, and `customProperties`: `connector` (`kind: https-file`, `url`, `allowHosts`, `format`, `parser`), `schedule` (`cron`, later `timeoutMinutes` from measured runs), `terms`, `examples` |
| `<bronze>.odcs.yaml` | `bronze_daily` | `bronze_currencies` | the connector's table (ODCS): its columns, `source_file_sha256` among the primary key, `quality` entries of `type: custom, engine: k2-connector` naming a parser audit, `customProperties` `layer: bronze`, `build: connector`, `backup: restore` or `rebuild` |
| `<output>.odcs.yaml` | `silver_rates`, `gold_eur_rates_daily` | `currency` | each dbt output's contract, its roles and policies |
| `models/<output>.sql` | 2 models | 1 model | the dbt models; their opening words are the pull request's "What this does and why" |
| `unit_tests.yml` | yes | yes | dbt unit tests, fixtures typed in SQL |
| `grants.yaml` | `p.analyst-a`, `full_reader` | `grants: []` | who reads which role; a grant outside the owning team needs `terms` recorded first (`k2gen check` refuses it otherwise) |

`backup: restore` is for what cannot be fetched again: the ECB's bronze, since its 90-day file
forgets; `currency-master`'s bronze is `rebuild` (the list can be fetched again). The backup set is
compiled from it ([lake-backup-and-restore.md](./lake-backup-and-restore.md)). `allowHosts` is the
only place a host is allowed: fetch refuses any other, redirects included, and widening it is a
contract change in a pull request, never a setting ([connector-failure.md](./connector-failure.md)).

**The parser**, `domains/<domain>/parsers/<parser>.py`, its name the connector's `parser`
([`ecb_fx.py`](../../domains/fx-reference/parsers/ecb_fx.py),
[`iso4217.py`](../../domains/reference-data/parsers/iso4217.py)). The contract fetch holds it to
([`docker/fetch/fetch.py`](../../docker/fetch/fetch.py)):

- `def parse(raw: bytes) -> list[dict]`: one dict per row, exactly the bronze table's columns but
  `source_file_sha256`, which the connector sets. Any raise is a parse failure: fetch exits 30,
  the bytes land, nothing is staged, and the audit fails the batch. Keep values as published
  strings; typing is the model's job.
- `def <audit>(rows: list[dict]) -> list[str]` for each `implementation` the bronze `quality` names:
  one description per failing row, `[]` passes. An audit that is missing, raises, or returns
  anything else is recorded as failed with the reason.
- Standard library only, and no import of a sibling module: fetch loads the file by its path, in a
  container on `k2-egress` with no secret. Untrusted bytes: refuse a DOCTYPE or entity declaration
  and any encoding but UTF-8, as both parsers do.

**Fixtures are synthetic**: `domains/<domain>/tests/fixtures/`, invented values in the exact format,
with a `README.md` that says so (the ECB set: a good file, one that parses and fails both audits,
one cut mid-document). No third-party data is committed: `python3 scripts/check_docs.py data` is
the gate. Tests: `domains/<domain>/tests/test_<parser>.py`.

**Measured**, not measured: a person's work.

---

## 2. Check them, no container

**When**, before the pull request, and after every change to it.

**Expected behaviour**, each command below starts no container. `ci-validate.sh` is CI's own
validation runner: from a scratch clone of the current tree (tracked and untracked files) at a
scratch tag, `k2gen docset`, `k2gen check --previous none` and a trial compile with placeholder
image digests, never deployed; the release directory is its last line.

**Recovery**

```bash
uv run --no-project --with pytest==9.2.0 pytest domains -q          # the parsers, on their fixtures
python3 scripts/check_docs.py data                                    # no real data committed
bash scripts/verify/ci-validate.sh /tmp/k2-ci                         # docset, check, trial release
uv run --project platform/k2gen --locked -q k2gen check --set /tmp/k2-ci/pr/set \
  --previous "${K2_RELEASES:-$HOME/k2-releases}/pointers/trino/documents"   # against the live release's set
make test-k2gen
(cd dbt && uv sync --locked -q) && (cd platform/k2gen && K2_DBT_PROOF=1 uv run --locked -q pytest -q -rs tests/test_emit_dbt.py)
```

Run on the working tree on 2026-11-27 (the `--previous` set was the tag `v3.0.0-alpha.3.2`'s, from
`k2gen docset --tag v3.0.0-alpha.3.2 --out <dir>`, standing in for the live release's documents):

```text
53 passed in 0.03s
k2gen release: NO PIPELINE market-analytics.hot-gold: it has no schedule, so no DAG runs it
<dir>/pr/releases/v0.0.0-ci.1-a6f273a3498f
1394 passed, 4 skipped in 36.96s
31 passed in 75.01s (0:01:15)
```

`check_docs.py data` and `k2gen check` print nothing and exit 0 when clean; a finding is printed as
`<severity> <code> <path> <pointer>: <message>`, and any `error` exits 1. In the trial release, read
what the product compiled to: `compiled/products/<domain>.<product>/pipeline.json` (fence, DAG id,
connector, bronze checks, builder and publisher), `compiled/airflow/dags.json` (its task list),
`compiled/lake/backup-set.json` and `compiled/catalog/products/<domain>.<product>.json`.

Three more need containers and are not run here:

- `make test-dbt-tree` is the dbt tree proof above, then `bash scripts/trino-rules-test.sh`, the
  rules suite on a throwaway lake (Trino, Lakekeeper, MinIO): containers, no stack.
- `make test-dbt`: lint, parse and compile, then each compiled product built with its tests on a
  throwaway lake and its results table read back (`scripts/dbt-test.sh`, about 75 s).
- `make access-verify RELEASE=<the trial release dir>`: every row of
  `tests/access/expectations.yaml` on a throwaway lake. Add the product's rows there in the same
  pull request, as the ECB rows do: its bronze and its landing log denied, each base denied, each
  reader view as its grants say.

**Measured**, 2026-11-27 on the working tree: `ci-validate.sh` 1.6 s, `k2gen check --previous` 0.3 s,
`make test-k2gen` 37 s, the dbt tree proof 75 s.

---

## 3. The pull request

**When**, the checks of section 2 pass.

**Expected behaviour**, one pull request carries the documents, the parser, its tests and fixtures,
and the access rows. Its template ([`.github/pull_request_template.md`](../../.github/pull_request_template.md))
opens with **What this does and why**, required for a change under `contracts/**/models/`: the
grain (what one row is), the rule each model applies, the fact that makes the rule necessary, and
why its materialization fits, in the same words that open the model file. How a reviewer reads it:
[`docs/development/reviewing-dbt-work.md`](../development/reviewing-dbt-work.md). CI runs
`ci-validate.sh --diff`, which writes the `k2gen diff` from the newest `v3.*` tag's release into
the job summary: the new DAG, tables, landing and backup entries should be all it adds.

**Recovery**, a red check: the job's log names the command; rerun it locally from section 2.

**Measured**, not measured.

---

## 4. Release and secrets

**When**, the pull request is merged and an annotated tag is approved on `main`.

**Expected behaviour**, `make release` compiles the tag into a sealed release
([deploy-a-release.md](./deploy-a-release.md) section 1). A dbt product needs two password files
before its deploy, its builder's and its publisher's: deploy's preflight refuses without them,
naming each with the command that writes it (`scripts/deploy.py` `required_secrets`). A plan-only
deploy on the live stack on 2026-11-27, before the files existed, printed (trimmed):

```text
deploy: preflight FAILED after 1.7 s: secret files missing under ~/k2-secrets: api/keys.json (make api-key); principals/dbt_calendars.password (make password PRINCIPAL=dbt_calendars TO_FILE=1); ...
deploy: REFUSED: ...
deploy: exit 2: refused before any change
```

`make password` knows a builder or publisher only from the product's documents in the checkout it
runs in (`scripts/principal-secret.py`), so run it from the live checkout at the tag. A reader the
grant names needs its own password, and a k2-api key to read over REST
([create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md)).

**Recovery**, run on the live stack on 2026-11-27 for `dbt_` and `pub_` of `calendars`,
`currency_master`, `ecb_fx_rates` and `quality_results`, each exit 0 with its file mode 600, and the
reader key of `p.analyst-a`:

```bash
make release TAG=<tag>
make password PRINCIPAL=dbt_<fence> TO_FILE=1
make password PRINCIPAL=pub_<fence> TO_FILE=1
make api-key PRINCIPAL=p.analyst-a TO_FILE=1
make principals-status                 # not yet run: the two show a password line and a password file
```

```text
principal-secret: p.analyst-a: sha256 in ~/k2-secrets/api/keys.json, key in ~/k2-secrets/api-keys/p.analyst-a.key (mode 600)
```

**Measured**, not measured.

---

## 5. Deploy

**When**, the release is compiled and its secrets written.

**Expected behaviour**, the plan names the DDL the product needs (its Spark tables, bronze,
`landing_files` and `audit_checks`) and the namespaces of its storage plan. A Spark table is made in
the `ddl` step, or in `ddl-late` when the deploy recreates `spark-iceberg`, as it did here. The
release-time access suite runs its rows on a throwaway lake in preflight; after the swap the factory
builds the new DAG within 30 s of the pointer moving ([airflow-scheduling.md](./airflow-scheduling.md));
a dbt output's reader view waits for the wrapper's first tag; the site step regenerates the catalog
site and the dbt docs, and never fails the deploy.

**Recovery**, run on the live stack on 2026-11-27 but `make release-diff` (not yet):

```bash
make release-diff RELEASE=<release id>
make deploy RELEASE=<release id> PLAN=1
make deploy RELEASE=<release id>        # the release that added the products also changed MinIO: ALLOW_STATEFUL=minio (deploy-a-release.md section 3)
make release-verify
docker exec k2-airflow-scheduler airflow dags list            # the new DAG id is listed
docker exec k2-airflow-scheduler airflow dags list-import-errors
```

The deploy that added the products, `v3.0.0-alpha.4-rc.2-e2dcf759de67`, printed (trimmed):

```text
deploy: plan v3.0.0-alpha.3.2-bae18019daef -> v3.0.0-alpha.4-rc.2-e2dcf759de67 (also undoing attempts v3.0.0-alpha.4-rc.1-7d00791fc1fe)
  Trino restart: no; DDL: k2.currency_master.audit_checks, k2.currency_master.bronze_currencies, k2.currency_master.landing_files, k2.ecb_fx_rates.audit_checks, k2.ecb_fx_rates.bronze_daily, k2.ecb_fx_rates.landing_files
  views to re-sync: fx_reference.ecb_fx_rates.gold_eur_rates_daily, fx_reference.ecb_fx_rates.silver_rates, governance.quality_results.test_results, reference_data.calendars.target_days, reference_data.currency_master.currency
  k2.ecb_fx_rates.bronze_daily: created after the recreate, by spark
deploy: ddl ok (36.0 s)
deploy: recreate ok (80.3 s)
  created k2.ecb_fx_rates.bronze_daily (spark)
deploy: ddl-late ok (9.8 s)
  fx_reference.ecb_fx_rates.gold_eur_rates_daily: not published yet; the wrapper creates it at its first tag
deploy: views-widen ok (1.4 s)
  catalog-fence: 10 PASS, 0 FAIL
deploy: verify ok (8.6 s)
deploy: site ok (16.0 s)
deploy: v3.0.0-alpha.4-rc.2-e2dcf759de67 is live and verified
deploy: exit 0: done
```

`airflow dags list` then listed five DAGs (`access_canary`, `calendars`, `currency_master`,
`ecb_fx_rates`, `quality_results`), and `airflow dags list-import-errors` printed `No data found`.
After the next release, `make release-verify` exited 0 with, among its lines, `catalog-fence: 10
PASS, 0 FAIL` and `access-verify LIVE=1 passed: {'PASS': 82, 'FAIL': 0, 'SKIP': 30}`.

A refusal or a rollback: [deploy-a-release.md](./deploy-a-release.md) sections 5 and 6.

**Measured**, 2026-11-27: 166 s for the deploy that added the products (`ALLOW_STATEFUL=minio`), 56 s
for the next (`v3.0.0-alpha.4-rc.3-48ad71fba732`, a drain of 12.8 s, nothing recreated).

---

## 6. First run

**When**, the deploy is verified.

**Expected behaviour**, on the releases up to `3.0.0-alpha.5.1` an `active` product's DAG started
unpaused and ran at its cron. **From the release that carries phase 004** (not yet deployed), every
DAG a release adds is created paused, whatever its status, and deploy never unpauses it: unpause it
after `make release-verify` exits 0, inputs first (`docker exec k2-airflow-scheduler airflow dags
unpause <fence> -y`, not yet run by hand; the order for phase 004's products is in
[onboard-a-kafka-product.md](./onboard-a-kafka-product.md) section 4). A DAG that existed before
keeps its state. A trigger runs it now. Its inputs must be tested first: `ecb-fx-rates` requires `calendars`' tested
`target_days`, or its build exits 4. A first run of a file product fetches, lands the file, stages
and audits its rows on bronze's `audit` branch, publishes `main`, builds and tests the dbt outputs,
moves their `audited` tags, and writes one run-record row per step.

**Recovery**, the runs below happened on the live stack on 2026-11-27; the run-record query has not
yet run there:

```bash
docker exec k2-airflow-scheduler airflow dags trigger <fence>
docker exec k2-airflow-scheduler airflow dags list-runs <fence>
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run <fence> <run_id>

# The run record, as svc.catalog (trino_cli: tls-and-passwords.md, NET=${K2_COMPOSE_PROJECT}_k2-net).
trino_cli svc.catalog "SELECT task, event, table_name, rows_written, release_id, recorded_at
  FROM governance.run_records.run_record WHERE run_id = '<fence>/<run_id>' ORDER BY recorded_at"
```

Every task `success` (`maintain` and `expire` may be `skipped`); run-record rows `fetch`, `load`,
`audit`, `publish` `passed`, and the build's `started` then `audited` per output. The first runs on
the live stack:

| DAG | Result | Tasks |
|-----|--------|-------|
| `calendars` | success | `resolve`, `build`, `maintain`, `record` success; `expire` skipped |
| `ecb_fx_rates` | success in 121.7 s | every task success; `expire` skipped |
| `currency_master` | success in 48 s, after `v3.0.0-alpha.4-rc.3-48ad71fba732` | fetch's log: `fetch: spooled <n> bytes, sha256 <sha256>`, then `fetch: <n> rows` |

The one-shots ran as `k2-fetch-currency_master-1` and `k2-dbt-runner-ecb_fx_rates-1`
(`k2-<kind>-<dagId>-<try>`). `currency_master`'s first run, under the release before, failed twice
over; both were defects, fixed in the release that followed (Failure modes below). A red task:
[airflow-scheduling.md](./airflow-scheduling.md) section 2, then
[connector-failure.md](./connector-failure.md) or
[file-source-audit-failed.md](./file-source-audit-failed.md).

**Measured**, 2026-11-27: `ecb_fx_rates` 121.7 s, `currency_master` 48 s, one run each.

---

## 7. Its catalog page, and a read

**When**, after the first run.

**Expected behaviour**, k2-api (`127.0.0.1:${K2_API_PORT:-8095}`) serves the catalog site deploy
wrote: an index at `/catalog/`, and one page per product at
`/catalog/<domain>/<product>/index.html` (`platform/catalog-site/k2site.py`), with each output's
tested snapshot, the product's newest run and its test counts, which `svc.catalog` reads from the
run records and the quality results. A page shows the run only once the site is regenerated: deploy
does it after each deploy and rollback, `make site` now, and the daily timer of `make
site-install` at 05:15 UTC. A reader the grant names reads the gold view over SQL or REST.

**Recovery**, run on the live stack on 2026-11-27 but the two lines marked not yet:

```bash
make site
API=http://127.0.0.1:${K2_API_PORT:-8095}
curl -s -o /dev/null -w '%{http_code}\n' $API/catalog/                                  # 200
curl -s -o /dev/null -w '%{http_code}\n' $API/catalog/fx-reference/ecb-fx-rates/index.html   # not yet run
curl -s $API/v1/products | python3 -m json.tool                                          # six products
curl -s $API/v1/products/fx-reference/ecb-fx-rates | python3 -m json.tool | head -20     # not yet run
make access-verify LIVE=1              # ran inside make release-verify: the canary's, the platform's and the storage-key rows; this product's SKIP
```

`make site` printed:

```text
catalog site: k2site: wrote /site/catalog/current; facts read
dbt docs: k2-dbt: wrote /site/dbt-docs/tree-<id>
site: <release id>
```

`/catalog/` answered 200 with `cache-control: no-cache` and `x-content-type-options: nosniff`, and
the content-security-policy `services/k2-api` sets. The live suite, run by `make release-verify`,
printed `access-verify LIVE=1 passed: {'PASS': 82, 'FAIL': 0, 'SKIP': 30}`.

Live, the suite runs the storage-key rows and the rows of principals whose password the platform holds
that name nothing but the access canary (`scripts/access_verify.py`, `live_skip`), so this product's own
rows print a SKIP there. They ran at release
time on a throwaway lake started from the release, in deploy's preflight;
`make access-verify RELEASE=<release dir>` runs them again.

The REST data route is `GET /v1/data/<domain>/<product>/<table>` with `Authorization: Bearer
<key>` (`services/k2-api/openapi.yaml`); the key's principal must hold a role on the table. Filters
are query parameters `filter.<column>=<value>`, one equality per declared column; when more rows
match than `limit`, the answer is 422 `result_too_large` (cursors arrive in phase 007), so a small
`limit` needs filters. As `p.analyst-a`, the key read from its mode-600 file, on 2026-11-27:

```bash
KEY=$(cat "${K2_SECRETS:-$HOME/k2-secrets}/api-keys/p.analyst-a.key")
D=$API/v1/data/fx-reference/ecb-fx-rates
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" "$D/gold_eur_rates_daily?limit=10000"   # 200
curl -s -o /dev/null -w '%{http_code}\n' "$D/gold_eur_rates_daily?limit=10000"                                   # 401, no key
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" "$D/bronze_daily"                       # 403
curl -s -H "Authorization: Bearer $KEY" "$D/gold_eur_rates_daily?limit=5"                                        # 422
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" \
  "$D/gold_eur_rates_daily?filter.<column>=<value>&filter.<column>=<value>&limit=5"                             # 200, two declared columns
bash scripts/verify/api-schema.sh                                                                                # 8 PASS, 0 FAIL
```

The 422 answer's `error` was `result_too_large` and its `message` `more than 5 rows match; narrow
the filters (cursors arrive in phase 007)`.

**Measured**, 2026-11-27: the site step 16.0 s, inside the deploy that added the products.

---

## Failure modes / incidents

- **2026-11-27, `currency_master`'s first run on the live stack.** Its `fetch` exited 1 with
  `OSError: [Errno 9] Bad file descriptor`: a defect of the fetch on a host that closes the
  connection after the body, not of the source ([connector-failure.md](./connector-failure.md)
  section 4). With no build, its `maintain` then exited 1 reading a dbt output not built yet
  (`TABLE_OR_VIEW_NOT_FOUND`); it now skips such an output as `not built yet` and names it in
  `notBuilt` ([`docker/lake/maintain.py`](../../docker/lake/maintain.py)). Both fixes shipped in
  `v3.0.0-alpha.4-rc.3-48ad71fba732`, after which the run passed in 48 s.

---

**Last verified:** sections 1 to 3 on 2026-11-27 against the working tree of the phase 002 and 003
branch, no container started. Sections 4 to 7 on 2026-11-27 on the live stack (releases
`v3.0.0-alpha.4-rc.2-e2dcf759de67` and `v3.0.0-alpha.4-rc.3-48ad71fba732`), but `make
principals-status`, `make release-diff`, the run-record query, the product's own catalog page and
`/v1/products/<domain>/<product>`: not yet.

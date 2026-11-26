# Review of the full plan (ARCHITECTURE-V2 section 6, with the owner's order), 2026-11-26

Reviewer: independent, wrote none of it. Scope: the plan (phases, order, dependencies, exits, estimates, build
method), not the architecture. Facts are from `main` at `9c86ada`, `origin/feat/file-source-pattern`, the three spikes,
`gh`, and read-only looks at the live stack. Terms: **WAP** is write, audit, publish on a hidden Iceberg branch;
**worktree** is a second git checkout one worker builds in; **one-shot** is a container that runs once and exits;
**pointer** is the link that says which release a service runs; **hot spot** is a file many pieces must edit.

## Summary for the owner

**Verdict: go, with these changes.** The architecture holds and your order breaks no hard dependency: everything the
four demo moments need can be built by N7. What will go wrong is how the plan gets built, not what it builds.

1. **Retire the old pipeline at the end of N0, not at the start of N2.** If it stays alive, N1 has to carry 13
   old-format products, the old writers and the old mounts through the new compiler and the new deploy model, or run
   two deploy models side by side. Nothing later compares against the old tables: v2 chose proof over old-versus-new
   parity (4.10). What you lose is live crypto data in the lake for N1's length, and stale dashboards. That is cheap in
   a lab.
2. **Split N1 into three phases, each with its own exit and tag:** N1a release and rules spine, N1b run spine through
   ECB, N1c serving and teaching. You get your first measured pace after N1a instead of after all of N1. Build the
   release and deploy first. Workers need it to test their branches on the live stack at all.
3. **Merge one interface pull request before any parallel work.** It should fix the release layout, the schemas,
   the run-record table, `k2_input`, the expectations format, the REST shapes and the catalog JSON. It should also
   split the four hot spot files. This host can run only one heavy Docker job at a time, so the orchestrator also
   needs a queue for those jobs. Without the interface PR and the queue, parallel workers will drift and stall.
4. **Nothing in the plan builds the demo.** Add a demo gate step after N7: `make demo`, a reset, the walkthrough and
   the recordings. Also decide how a product onboarded live in a pull request can be deployed and then reset, given
   that you deploy only from a tag after merge.
5. **Pull five things earlier.** The proof of the redirect fix (now N3), image digests (N8), alert delivery (N8),
   the reader migration of generic files (N4) and the identity decision O3 (N6). Each sits after something that
   relies on it.

Counts: 2 critical, 8 high, 12 medium, 3 low.

## Findings

| Id | Sev | Where | Problem | Evidence | Change required |
|---|---|---|---|---|---|
| F1 | critical | N0, N1, N2 | The old pipeline stays live through N1, which rewrites everything it depends on | N1 moves the compiler to ODPS, takes generated files out of git, runs compose from a release and makes today's domain catalogs read-only. Live today: `lake_ingest`, `lake_maintenance`, `dbt_marts` (`docker/airflow/dags/lake.py:128-196`, `dbt_marts.py:102`); 13 old-format `contracts/*/*/product.yaml`, compiled by `generate.py:353-476`; `dbt/profiles.yml` writes catalog `market_analytics`, which N1 makes read-only | Retire it as N0's last steps (answer 2). Archive the old contracts and code in the same PR, so N1's compiler reads one format |
| F2 | critical | brief 8, N1 | Parallel workers start with no frozen interfaces, five hot spot files, and a host that takes one heavy Docker job at a time | Every N1 piece edits `docker-compose.yml`, `Makefile`, `platform/k2gen/k2gen/generate.py` (633 lines, one module), `contracts/schema/*.json` and the doc index counts. On 2026-11-24, `make test` in a worktree beside a live step killed the Docker engine (memory, unattended-run lessons). The live stack mounts the main checkout (25 `./` mounts), so a worktree branch cannot run live until `make deploy` exists | Merge an interface PR first (answer 4). Split `generate.py` per output and the Makefile into included files. Reserve ADR numbers per workstream. Queue throwaway-container tests and live steps. Build deploy first |
| F3 | high | N1 | One phase holds about ten subsystems, judged at 12 days, and gives the first pace reading only at its end | The N1 row: release and deploy, document model, checks, catalogs, writers, policies, expectations, fetch container, connector, dbt runner and wrapper, run records, scoped keys, backup set, three products, k2-api, eight ADRs, three labs, six runbooks, two chapters | Split into N1a, N1b and N1c (answer 3) |
| F4 | high | N1, 4.9 | A deploy run from a release directory can recreate every container, the captures included | Compose resolves `./` mounts against the compose file's own directory. Captures mount `./config` (`docker-compose.yml:1204,1250,1298`). Memory: "never a bare `docker compose up -d`, it would recreate everything". Recreating a capture opens a gap in the feed | Fix one project directory and one project name. Mount `releases/` once and resolve `current` inside it (the repo's inode gotcha). N1a exit: a rules-only deploy leaves `State.StartedAt` unchanged for captures, redpanda, minio, postgres and lakekeeper |
| F5 | high | N1 vs N8 | Image digests arrive in N8, so N1's rollback moves pointers but not images, and the manifest format changes later | The images `k2-airflow:3.3.2` and `k2-capture:v3` are built locally under tags that can be moved. ADR-042 is already drafted (#225) | Put digests in N1a's manifest. N8 keeps only the version shown by `make health` |
| F6 | high | N1 exit | N1 builds things that no exit command proves | Pinning through XCom: V2-CHANGELOG:200 says "proved by N1 chaos", but no script is listed. Also unproved: generated files leaving git, resource groups, one writer denied on another product's table (procedures included), key denials per engine, `/v1/products` and `/v1/data`, the validation runner's `k2gen diff` on a PR (demo moment a), and bronze publishing in order | Add one command per item (answer 3) |
| F7 | high | N3 vs N1 | The proof of #239's HIGH redirect fix comes after the tag, but `https-file` runs live from N1 | `file_source.py:145-146` on the branch. N3's exit holds the tests for a redirect to http, to a host outside `allowHosts` or to a private address, and `make test-connectors` | Move those tests and the no-network harness into N1b's exit |
| F8 | high | N2, N4 | Old tables are dropped in N2, but their readers move only in N4, after the tag | `git grep` today: 135 files name `raw.messages` and 265 name an old layer table. They include the README, `docs/operations`, notebooks, labs, the queries of the `.claude/skills` release-check and benchmark-report skills, and screenshots | Move the generic readers together with the retirement. Only Binance and Coinbase files wait for N4. `make demo` and release-check use the new names before the tag |
| F9 | high | 8 O3, N6 | Identity is decided and built in N6, after N1 has built every client on trusted user names | O3 (b) says "every client changes, writers and platform services get passwords". (c) adds an identity provider. Demo moment c would show engineers permissions that any client can bypass by claiming another name | Answer O3 before N1a. Build it where the clients are built |
| F10 | high | brief 3; no row | No phase builds the demo | No row has `make demo`, a reset, the walkthrough, screenshots or the recording. Live onboarding clashes with "deploy only from a tag after merge" (4.3) and with retirement needing 30 days with no query (4.5) | Add a demo gate step after N7, with an exit and days. `make demo` restores a recorded baseline (N2's `make restore`) and then runs real commands. The owner decides the onboarding mechanics by N1a |
| F11 | medium | N0 | The spikes did part of N0, and half of the run-cost work is still unmeasured | Done: rollback through Lakekeeper (SPIKE-DBT 5), the read-only catalog and the refresh period (SPIKE-ACCESS), the fixed cost of a run (SPIKE-RUNCOST). Not done: `incremental_predicates`, Spark expiring a Trino-written snapshot by id, the link swap, the contended chain, Airflow overhead, memory per run | Strike what is done. Measure the rest through Airflow and the one-shot container (answer 2) |
| F12 | medium | N0, N1 | Risks the spikes found are not in the plan | Definer views skip masks, and `dbt/dbt_project.yml:37-38` sets `view_security: definer` today. Only `$files` and `$snapshots` were tested. The uniqueness audit grows from 0.47 s to 4.40 s at 200k rows. Rollback across a contracted table's create-or-replace is untested, and `gold_eur_rates_daily` is a `table` | The checks library refuses definer views. `access-verify` probes every `$` table. Bronze audits are scoped to the batch. One more N0 spike |
| F13 | medium | N2 exit | The exit cites a delete-file bound that "N0 set", but N0 sets none | The N0 row's scope and exit | Add the bound to N0's benchmark |
| F14 | medium | N4 exit | `git grep ... raw.messages ... \| wc -l` printing 0 cannot pass | 37 matches sit under `docs/adr`, `audits`, `research` and `plans`, which CLAUDE.md makes immutable | Exclude the immutable surfaces with pathspecs |
| F15 | medium | N2 vs N5 | `candles-eur` reads `candles.ohlcv_1d`, but N2 builds only "candles on Kraken alone" and N4 adds the timeframes | 5.3, line 1079; the N4 row | N2 builds every timeframe. Timeframes are not venue work |
| F16 | medium | N2, N4 | Candle and bar parity are re-pinned only in N4, after the demo has shown candles and `candles-eur` | N4's exit; the old plan's rule that "derived numbers have a second computation" | Re-pin `parity-ohlcv` on Kraken in N2 |
| F17 | medium | N6, brief 4 | The catalog site has no defined scope, and the catalog is rendered three ways | N6 says "catalog UI per O1b, else pages". 4.2 lists a `/catalog` route. `docs/products/` is generated today. The site shows quality and state, which change on every run | One generator from the `/v1/products` JSON, rerun after each deploy and on a schedule, served by k2-api. Restate N6's days |
| F18 | medium | N1 to N7 vs N8 | Alerts go nowhere until N8, so the alert step of N1's runbooks cannot be run | Ledger: "alerts are delivered nowhere". O4 is decided (Slack webhook) | Alertmanager to Slack in N1a (0.1 CPU / 64 MiB) |
| F19 | medium | #239 vs N1 | #239 follows the old model, so merging it and then reshaping it builds it twice | PR239-REVIEW, "where it is file-specific". The branch adds `governance/landed-files`, `fileSource` in `k2.schema.json`, fetch and parse inside Spark, and the global lock | Close #239 unmerged and port its sound parts. Rewrite ADR-036 for v2 before accepting it |
| F20 | medium | several rows | Some things are built twice | `k2_input` in N1 and N5. The compatibility check in N1's checks and in N8. Three smoke tests: `make demo`, N4's Trino smoke test and N11's release-check. `hot-gold` declared in N0 in the old format | Assign each once (answer 8) |
| F21 | medium | 8 O5, O6 | Two open decisions block earlier than the plan says | O5 decides whether deploy's storage plan is the converge engine, which is N1 work. O6: landings never expire and the backup sits on the same disk, with 61 GB in `k2-lake` today (`du`, read-only) | Answer O5 by N1a and O6 by N2 |
| F22 | medium | section 6 | N1, N2 and N6 are underestimated, and the demo gate has no estimate | Answer 5 | Re-estimate after N1a |
| F23 | low | N11 | Branch protection is already on | `gh api .../branches/main/protection`: 13 required checks, `enforce_admins` true | Strike it from N11 |
| F24 | low | N0 exit | Timeouts are set "from p99" with n of at least 20 | The p99 of 20 samples is just the maximum | Set the timeout from the maximum times a factor, or use n of at least 100 |
| F25 | low | N1 exit | `docker inspect` of the dbt runner | A one-shot `--rm` container is gone once the run ends | Check `docker compose config` for the runner service, plus a run that prints its environment names |

## 1. Order

**Decision: keep the owner's order. It breaks no dependency, but move five items earlier.** N5 needs `candles`
(Kraken, N2), the security master (N2) and `ecb-fx-rates`, `currency-master` and `calendars` (N1). N6's usage events
and OpenLineage events are landed by the platform's own Kafka connector (4.12), which N2 builds. N7 needs only N1's
`/v1/data` and expectations. Putting N3 and N4 later is safe, because the demo needs no SFTP or FTP and no Binance or
Coinbase.

**Rework that the move causes.** (a) `canonical-trades` is built on one venue in N2 and widened in N4. Write it in N2
as a union over its declared venue input ports. Then N4 only adds ports and runs `make rebuild` on the derived chain:
`candles-eur` looks back 7 days (5.3), so it will not backfill new venues by itself. (b) N6's "every grant" and N7's
`make api-verify` cover only the products that exist before N4. N4 adds its own grants and expectations, which follows
the plan's per-product rule. (c) Parity is re-pinned in N4 (F16), and readers move in N4 (F8).

**Things before the tag that rely on later phases.** The redirect and private-address proofs and
`make test-connectors` (N3 serves N1, F7). Image digests, needed for a rollback that is true (N8 serves N1, F5).
Alert delivery, needed by N1's runbooks and by demo moment d (N8, F18). The compatibility check (N8 repeats N1, F20).
The Trino smoke test (N4, which `make demo` needs). The generic reader migration (N4, F8). Nothing that the four demo
moments need sits after the tag once those move.

## 2. N0

**Strike, done by the spikes:** the spike for `rollback_to_snapshot` through Lakekeeper (SPIKE-DBT 5); the read-only
reader catalog (SPIKE-ACCESS, procedures refused); the refresh period itself (live within 12 s at a 5 s period); the
fixed cost of one product run (SPIKE-RUNCOST: 6.5 s, or 10.5 s at 200 snapshots). Retirement also strikes the
following: the trades-only measurement day and any ingest retune (LEDGER:31), the response to ADR-034's fired trigger
(record an Outcome that says "resolved by retiring `lake_ingest`"), the owed `maintenance.py --with-paused` audit
(book data is let go), and building ADR-048 (set its status to Superseded).

**Left:** pausing ClickHouse and its stand-down list (CLICKHOUSE-INVENTORY). The Spark image rebase. `make drop`.
Four spikes: `incremental_predicates` on a partitioned merge, Spark `expire_snapshots` by id on a Trino-written table,
the refresh through a link swap inside a mounted `releases/` directory, and rollback across a contracted table's
create-or-replace. The contended chain benchmark, **run through Airflow**: the spikes used `docker exec` and called dbt
directly, so Airflow's per-task overhead, the one-shot container start, memory per run and the results write
(SPIKE-RUNCOST:20) are all unmeasured. Add the delete-file bound that N2's exit cites (F13). Size the bronze audits to
the batch (F12).

**Retire the old pipeline in N0: yes, as its last steps, in this order.**
1. Rebase the Spark image and prove it on the live old pipeline. `make gates` passes on the new image, which is the
   only real workload available to test it. The planned exit only prints the versions of Python and OpenSSL.
2. Back up (`make drop` mirrors first; the host has 272 GB free and `k2-lake` holds 61 GB). Pause `lake_ingest`,
   `lake_maintenance` and `dbt_marts`.
3. Run the contended benchmark on a quiet Spark container. With `lake_ingest` beside it (median 189 s of every 300 s,
   SPIKE-RUNCOST:18) the numbers would describe a load that the target does not have.
4. Drop the old namespaces. Stand down the 14 rules in `lake-alerts.yml`, the lake panels and the gates, with visible
   SKIP lines. Do this in the same sweep as ClickHouse's stand-down, because it is the same pattern on the same files.
   Archive the old contracts and the ingest code, keeping the modules v2 reuses (`offsets.py`, the framing half of
   `wire.py`, `bars.py`, `books.py`), and write their README.

**What is lost, and why it is acceptable.**
- Live crypto data in the lake, from N0 until N2's `kraken-trades`. This is a lab. Redpanda keeps 48 hours, and the
  new product starts from the log start.
- `lake-verify` and the 2026-11-25 parity pins as live gates. They prove only the pipeline being retired, and the pin
  lapses about 2027-05-25 anyway. Keep the scripts for N2's re-pin.
- Fresh dashboards, labs (#211), notebooks and screenshots. They would go stale at N2 anyway.
- ClickHouse's reload from lake gold (`clickhouse-rebuild-from-lake.md`). So N0's runbook "pause and resume a serving
  product" can verify a resume only from topics within 7 days. Say so in the runbook.

Nothing later compares new tables against old ones: N2's `parity-silver` recomputes from the new landing, N4 re-pins
on new tables, and the determinism check compares two new builds (4.10). Do not declare `hot-gold` in the old format
in N0. Declare it in N1a in ODPS (F20).

## 3. N1: split it into three phases, with the foundation first

| Phase | Scope | Exit, one command per item | Tag |
|---|---|---|---|
| **N1a Release and rules spine** | 1. The interface PR (answer 4). 2. Release layout, manifest with digests, `k2gen release`, `make deploy`, `release-verify`, rollback, compose from the release, generated files out of git. 3. The ODPS and ODCS model, the checks library and the transitions. 4. Reader and writer catalogs, per-product writers, rules with every `$` table denied, groups, resource groups, the link-swap refresh. 5. Principals, roles, grants, policies, expectations, the coverage rule, `access-verify` over SQL on a seeded fixture product. 6. Alertmanager to Slack | The existing deploy and forced-mismatch exits. A rules-only deploy recreates no container (F4) and a changed grant goes live within the refresh period. `git ls-files` lists no generated file. A writer of product A is denied select, insert and `rollback_to_snapshot` on product B. A definer-view fixture and a policy with no expectation both fail `k2gen check`. A pipeline query queues behind the `pipeline` group's cap. `amtool` reaches Slack | alpha.3 |
| **N1b Run spine through ECB** | Fetch container, `https-file` with the HIGH fix, parser interface, landing, bronze WAP in order; dbt runner, wrapper, run records, `k2_input`, reconciliation; scoped keys and the paths around Trino closed; backup set; `currency-master`, `calendars`, `ecb-fx-rates` to gold, terms | The existing chaos and `access-verify` exits, plus: a redirect to http, to a host outside `allowHosts` or to a private address fails, and `make test-connectors` runs with no network. A deploy during a DAG run leaves that run on R-1 and its run record says R-1. Trino's key cannot write `k2`, and Spark's cannot write a domain warehouse. A failed bronze batch blocks the next one | alpha.4 |
| **N1c Serve and teach** | k2-api `/v1/products`, `/v1/data`, `make api-key`, dbt docs; labs 1 to 3, the owner's guide, runbooks, chapters 21 and 22 | `make access-verify` runs every ECB expectation through REST and SQL and they match; `curl /v1/products` validates against the frozen schema; `make check-docs` | alpha.5 |

**Foundation, everything stands on it, and it must come first:** the release layout and deploy (also the only way a
worktree branch can be tested live), the document model and the checks, the catalogs and writers, policies and
expectations, the run-record table and the dbt wrapper. **Grows with the ECB product:** the `https-file` details, the
`ecb_fx` parser, the three products' models and tests, terms, the ECB expectation rows, `/v1/data` for ECB, labs and
runbooks. Write ADRs in the PR that builds each decision.

**Riskiest unknowns, none of which the N1 row orders first:**
1. Deploy without mass recreation, and pointer resolution through bind mounts (F4).
2. Rollback across the dbt failure modes: create-or-replace, a killed run, a Trino stop.
3. Per-engine MinIO keys while Lakekeeper vends no credentials (`lake.properties`: "vends nothing").
4. The fetch allow-list enforced in-process: connect to the resolved address only, and handle redirects.
5. Airflow starting one-shot containers through the socket on the scheduler (`docker-compose.yml:642`), with
   `on_kill`, inside four LocalExecutor slots, while a continuous consumer holds one of them.

The split runs 1 in N1a. 2 needs the spike in N0. 3 to 5 are the first PRs of N1b.

**Exits that do not prove their item:** `docker inspect` of an `--rm` runner (F25). `airflow dags test` runs in
process and skips pools, the executor and pinning, so it proves the task logic but not the scheduling. The planned
exits are missing proofs for the items listed in F6.

## 4. Interfaces to merge before parallel work

Merge one PR, reviewed once, that contains schemas, stubs and fixtures and no behaviour.
1. **Release layout and manifest.** The tree of `releases/<id>/` (documents, compiled outputs, code, compose file),
   the JSON schema of `manifest.json` (id, git commit, provenance, file hashes, image digests), the release id format,
   the pointer semantics and the path at which containers see `releases/`. Secrets are named by environment variable
   only and never compiled; today's catalogs already use `${ENV:...}`.
2. **Document set and checks API.** `load(tag) -> DocumentSet` with its provenance record (source, version, hash,
   approver, time). `check(docs, previous) -> [Finding{code, severity, path, message}]`.
3. **Schemas:** ODPS 1.1.0 and ODCS 3.2.0 with every K2 `customProperties` (`connector` per kind, `schedule`,
   `terms`, `lifecycleChange`, `serves`, `required`, `maxLagMinutes`, `build`, `policies`, `backup`, `unit`, `time`,
   `asOf`), as JSON Schema files.
4. **Generated connector config and parser interface.** The JSON that the fetch container and the Spark loader read
   per product. Parser: `parse(bytes) -> iterator of dict`, with how errors are reported. The spool layout and the
   fetch record (sha256, size, URL, status, time). Exit codes (75 when the lock is held, a no-op when the response is
   unchanged). The XCom value.
5. **Naming.** Landing keys, the per-product namespace and tables, the audit branch, snapshot summary properties
   (batch, sha256, release), catalogs (`<domain>`, `<domain>_build`, `lake`), principals (`svc.*`, `p.*`,
   `dbt_<product>`), groups (`<product>__<role>`), resource groups. Also the per-product lock that replaces
   `docker/lake/lock.py`.
6. **Run-record and governance tables:** run, inputs (product, table, snapshot, state, lag), outputs (before, after,
   audited, rolled back), counts, bytes, outcome. Lifecycle, grant and hold events. The query "newest audited snapshot
   of X", which `k2_input`, transforms, k2-api and the catalog all use.
7. **`k2_input(product, table)`.** The variable name and shape passed by the wrapper, the behaviour when unset (a
   plain `source()` in unit tests), and an error when nothing has been audited. **The wrapper's command line:**
   arguments, the environment allow-list, exit codes, where `run_results.json` goes, and the hand-off of snapshot ids
   to the Spark expiry task.
8. **Access formats:** `roles.yaml`, `principals.yaml`, `grants.yaml`, the three policy kinds, and
   `tests/access/expectations.yaml` with its negative probes and the coverage rule.
9. **REST:** OpenAPI for every `/v1` route that N7 will finish (filters, `fields`, `limit`, cursor, `as_of`, 403,
   410, state headers). Freeze it in N1, so that N7 completes these routes rather than reworking them.
10. **Catalog JSON.** One document per product (schema, owner, state, quality, lineage edges, policies worded, example
    queries, join keys, dbt docs link). The `/v1/products/{d}/{p}` body is this document, and the static site renders
    it.
11. **`backup-set.json`.** Also the **hot spot splits**: `generate.py` becomes one module per output, the Makefile
    gains `mk/*.mk` includes, compose service stubs are added for `k2-api`, `fetch` and `dbt-runner`, and ADR numbers
    from 056 are reserved per workstream.

Later, before N5: the signature of `k2_as_of` and the `relationships`/`asOf` rules.

## 5. Estimates

**Observed pace.** The 2026-11-24 plan judged P0 to P7 at 12 to 13 working days (its README:337). P0 to P3 landed
between 2026-11-24 and 2026-11-26: 51 PRs that are not Dependabot (`git log --since=2026-11-24`), worked around the
clock. Those phases extended known patterns. Fixes came at about one per feature (15 `feat`, 14 `fix`, for example
#203, #205, #208, #214, #228, #238 after P3). The first phase with a new pattern, old P4's file source (#239), stopped
at review with a HIGH finding and became a redesign. v2 is almost entirely new patterns. So the pace on P0 to P3 does
not transfer.

**Most likely underestimated:**
- **N1 (12 days).** It holds the old P1, P3 and P4 together, plus the deploy model and the security perimeter.
- **N2 (10 days).** A Kafka connector rewrite, the type-2 security master, held rows, the first restore drill, and a
  48-hour soak. The soak is calendar time, which a figure in working days hides.
- **N6 (6 days).** O3 (b) or (c) adds 3 to 5 days. Two OpenLineage sources and the usage listener go through the
  governance topic. The static site is now in scope: O1b (a) had saved about 2 days, and the owner chose a site.
- **The demo gate.** No days at all (F10).
- **The audits.** At 0.5 day each they are thin, given about one fix per feature.

**Single most likely cause of N1 stalling:** an integration done all at the end. Workers build the connector, the
dbt runner, the access test and k2-api against today's working tree, while the release layout and the compiler they
all feed into are still moving. The pieces then meet only when they first run from a release.
**Earliest signal:** a merged N1 PR whose tests or chaos script run from `./docker/...` paths or from `make generate`
output instead of from a release. Or two N1 PRs open at once that both edit `generate.py` or `contracts/schema/`. Or
`make deploy RELEASE=<last tag> && make release-verify` not yet green on `main` by the third N1 merge.

## 6. Over-building (default KEEP; for the owner to decide at the phase)

1. **Per-service pointers and drain (4.9).** Lighter form: one pointer for the whole release, with compose
   recreating only the services that changed (that is its normal behaviour, once the project directory is stable).
   Keep immutability, preflight and automatic rollback. Evidence: on one host with one operator, mixed-release states
   exist only to be detected by `release-verify`. Decide at N1a.
2. **The document-set adapter as an interface with one implementation.** CLAUDE.md, Style: "No abstraction with one
   implementation". Lighter form: the provenance record format plus `k2gen release --tag`. The seam is the record, not
   a class. Decide at N1a.
3. **`make parity-silver`**, a DuckDB re-implementation of each venue's silver. Silver parses, types and keys; it
   does no arithmetic. Lighter form: `make product-verify` accounting, dbt unit tests and the `EXCEPT ALL`
   determinism check. Keep parity for candles and bars. Decide at N2.
4. **A staggered maintenance task in each of about 20 DAGs (4.4).** Lighter form: one maintenance DAG that walks the
   products under per-product locks. That gives the same isolation from one schedule. Decide at N2.
5. **OpenLineage from two sources through the governance topic (N6).** Product and model lineage from ports, the
   manifest and run records (N1, N5) already gives demo moment d its lineage across domains. Lighter form: usage
   events only in N6, with OpenLineage when something consumes column lineage. It is already in the plan's cut order.
   Decide at N6.

## 7. Holes

**Holes no phase covers:**
- The demo gate and its onboarding mechanics (F10). There are two options. (i) The demo PR onboards a real, lasting
  product, and the reset is a lab-mode `make drop` of that product. (ii) A pre-release tag on the PR branch, deployed
  in lab mode, which breaks 4.3's rule of a tag after merge.
- The catalog site: its generator, when it is regenerated, how it is served, and its search (F17).
- The `ohlcv_1d` timeframe in N2 (F15).
- The delete-file bound (F13).
- Every proof missing from N1's exits (F6).
- `LAKEKEEPER_ENCRYPTION_KEY` "kept off the host", which N2's restore drill needs, while O4 keeps the backup on the
  host.
- The backup timer, an owner step (`make backup-install`). It is needed before N2's drill.

**When each open decision must be answered:**

| Decision | Needed by | Why |
|---|---|---|
| O1a, roles per principal per product | N1a starts | It shapes the group naming, the rules generator and the expectations format |
| O1d, layers per product | N1a starts | It shapes the layer rules in the checks and the ECB product's tables |
| O8, what readers see while a build is tested | N1a starts | (b) adds a spike and a day, and changes the reader catalogs and `k2_input` |
| O3, identity | N1a starts (built by N6, before the tag) | Otherwise N6 reworks every N1 client (F9) |
| O5, infrastructure-as-code scope | N1a starts, N8 at the latest | Otherwise N9 rebuilds the resource creation that N1's deploy already does |
| O6, landing retention | N2 starts | It is the first landing without a bound, with backups on the same disk (F21) |
| O1c, FX content | N5 starts | It changes N5 by about 3 days and decides whether `make parity-fx` is built |
| New: demo onboarding mechanics | N1a starts | It decides whether a non-main tag can ever be deployed |

## 8. Traps: building twice, old and new side by side, later rework

1. **The old pipeline alive through N1 (F1).** It means a dual-format compiler or two deploy models. Retire it in N0.
2. **#239 merged and then reshaped (F19).** Close it and port its parts into N1b.
3. **Readers moved after their tables are dropped (F8).** Old ingest code sits beside new code in `docker/lake/` from
   N2 until N4, across the demo. Archive it at retirement.
4. **`k2_input` is built in N1 and again in N5.** Build it once in N1b. N5 adds only `k2_as_of`.
5. **The compatibility check is in both N1 and N8.** The checks library's major-version rule is that check. Strike
   it from N8.
6. **The manifest gains digests in N8 (F5).** Fix its format once, in N1a.
7. **Three smoke tests.** `make demo` becomes the Trino smoke test, built before the tag. N4 and N11's release-check
   call it.
8. **Three catalog renderings.** k2-api `/catalog`, the generated `docs/products/`, and the owner's site. Build one
   generator from one JSON, and retire `docs/products/` generation when generated files leave git in N1a.
9. **Three edits to the gates.** ClickHouse skip (N0), stand-down (N2), re-pin (N4). Do one stand-down in N0, then
   re-pin on Kraken in N2.
10. **Three backup paths.** The fixed four tables of `make backup` (`scripts/backup-lake.sh:30`), `make drop`'s
    `mc mirror`, and N1's `backup-set.json`. `make drop` should call one backup command, and N1b should replace that
    command's list with the set.
11. **Identity added under built clients (F9), and IaC after deploy (O5).** Decide both before N1a.
12. **Runbooks written in N1 to N7 that cite alerts going nowhere (F18).** These must be re-verified in N8 unless
    Alertmanager moves to N1a.
13. **Definer views that live on through N1 (F12).** `ohlcv_delayed_20m` and `dq_results` are views, and the staging
    default is `definer`. They vanish with retirement in N0. Otherwise they leak through the new rules until N4.

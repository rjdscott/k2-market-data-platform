# Adversarial review of the tournament verdict

Reviewer's report to the owner, 2026-11-26. I wrote none of the entries or the verdict. Inputs:
`VERDICT.md` (all 999 lines), `BRIEF.md`, addenda 1 to 5, `JUDGING-CRITERIA.md`, `LEDGER.md`,
`CLICKHOUSE-INVENTORY.md`, `PR239-REVIEW.md`, the three entries where the verdict rejected
something, the repository at `main` `43cd971`, the branch `origin/feat/file-source-pattern`, the
running stack (read only), three throwaway containers (Trino 484, Spark 3.5.5, a two-service
compose project, each removed after use) and the web. Times are UTC.

Terms used below. **WAP** (write, audit, publish): write to a hidden Iceberg branch, audit it,
then fast-forward `main`, the branch readers see. **ODCS** is the Open Data Contract Standard (one
table's contract); **ODPS** is its sibling, the Open Data Product Standard (one product's
descriptor), both from the Bitol project of the Linux Foundation. A **definer view** runs its query
with its owner's rights, not the reader's. A **table procedure** is Trino's `ALTER TABLE ...
EXECUTE` (optimize, expire snapshots, roll back). A **DAG** is an Airflow pipeline.

## 1. Summary for the owner

**Does the verdict stand?** Its spine does. Contracts in git, one generator, generated files read
by Airflow, Spark, Trino and a small REST service, Iceberg on Lakekeeper, Trino as the one SQL
endpoint, vertical slices, ClickHouse paused first: that shape is sound, fits one host, and every
ledger item has a home. I re-checked twenty-three facts; most of the judge's version and date
claims hold.

**What does not stand, in order of harm.**

1. **A contract can publish the storage root password.** Transform expressions are Spark SQL
   fragments run by a Spark process that holds the MinIO root credential in its environment.
   Spark's `reflect` function reads environment variables; I ran it in a throwaway copy of the
   stack's Spark image and it returned a variable's value as a column. A scalar subquery in the
   same field reads any table, which defeats the "declared graph is the executed graph" check and
   every column mask. This is the one critical finding; the fix is cheap if made in slice 1.
2. **The ETL centre is a private language.** Eleven operations, four incremental modes, a plan
   hash and a compiler, beside dbt for marts, beside parsers and registered code: four ways to
   write a transform. The current venue logic is already SQL strings. You asked whether dbt can
   carry this: section 6 shows that dbt-core on Trino can carry silver, gold and marts within the
   repo's own warehouse layout (ADR-044), at the cost of a weaker form of publish-after-audit. For
   an owner whose rule is "modern yet tried and tested" and who wants to learn the standard tool, I
   judge it the better choice.
3. **Access safety rests on things that are not true yet.** Through a definer view the reader's
   masks and row filters vanish (I ran it); the "rules at least as strict" check the verdict
   promises cannot be computed for arbitrary SQL filters; the access proofs compare two files
   written by the same compiler; and any reader of a domain catalog can roll back or expire its
   tables (ADR-044's recorded gap), which no phase closes.
4. **The working tree is the deploy.** Airflow, Spark and (with the new refresh setting) Trino read
   generated files straight from the checkout, so a `git checkout` changes live state within
   seconds. A broken rules file stops every SQL query for every user (I ran it). The judge used
   exactly this failure to mark entry B down.
5. **Backup covers the old layout only.** `scripts/backup-lake.sh` copies four named tables; after
   slice 3 drops their namespaces, `make drop`'s "confirmed backup" protects nothing new, and the
   first restore drill is at the release gate.
6. **The five-minute chain is unproven and the proof comes late.** Today's single job already has
   a two-day p90 of 248 s against ADR-034's 240 s line; the design splits it into about nine Spark
   sessions per tick and measures the cost only after slice 1 has built the runner.
7. **The Spark image is past end of life**: Python 3.10 (end of life 2026-10-01), OpenSSL 1.1.1w,
   a base image last published 2025-03-11. Every new internet-facing connector would run there.

**Recommendation: accept with the listed changes.** No new tournament is needed. Two changes are
large enough that the judge should rewrite the affected sections (3.5, 3.6, 4.1, 4.2 and phases N0
to N3) before you sign: the ETL engine decision (section 6) and the deploy model (finding F2).
Everything else is an edit to a phase's scope or exit.

## 2. Findings

Severity: **critical**, the spine is wrong or data or access safety is broken; **high**, a phase
would fail or a requirement is missed; **medium**, a real defect with a contained blast radius;
**low**, worth fixing when touched. Counts: 1 critical, 9 high, 10 medium, 7 low.

| Id | Sev | Where in VERDICT.md | Problem | Evidence | Change required |
|---|---|---|---|---|---|
| F1 | critical | 3.5, lines 283-286 ("fragments, never statements"); 3.3 lines 173-175 | Contract expressions are code with root rights. `reflect('java.lang.System','getenv',X)` returns any environment variable, and the Spark container holds `AWS_SECRET_ACCESS_KEY` and `MINIO_ROOT_PASSWORD`. A scalar subquery in a `select`, `flags` or `filter` reads any table, unlisted in `inputs`, so lineage, the "only declared inputs" check and every mask are bypassed. One review round is the only guard, for "many teams" | `docker run ... k2-spark-iceberg:3.5.5_1.8.1 spark-sql -e "SELECT reflect('java.lang.System','getenv','HARMLESS_DEMO')"` printed `visible`; `docker exec k2-spark-iceberg sh -c 'env \| cut -d= -f1'` lists both credential names (values not read) | In N1: parse every expression with Spark's own parser in the compile test and refuse subqueries, `reflect`, `java_method` and any function outside an allow-list; run contract SQL in a session whose environment holds no root key (ADR-054's scoped key, pulled forward from N10). Moot for SQL models if section 6 is adopted, since Trino has no reflection and its writer is fenced per domain |
| F2 | high | 3.6, lines 352-368 and 355; attack on B, lines 964-966 | The checkout is the deploy. `./docker/airflow/dags`, `./docker/lake` (read-write) and `./docker/trino/etc` are bind-mounted from the working tree (`docker-compose.yml:640,692,783,1082`). `make apply` refusing a dirty tree does not stop a `git checkout` changing what Airflow parses, what the runner reads and, once `security.refresh-period` is added, who may read what. A partial or invalid rules file fails every query | Throwaway Trino 484 with `security.refresh-period=5s`: after truncating `rules.json`, `alice` and `admin` both got `Invalid JSON file '/rules/rules.json'`; queries recovered on the next refresh after the file was restored | Deploy from an immutable export: `make apply` writes `git archive <tag>` to a release directory, flips a relative `current` symlink inside one directory mount, and every service reads only `current/`. Do not add `refresh-period`; let `make apply` restart Trino. Hold the verdict to the standard it applied to B |
| F3 | high | 3.3 lines 174-175; 3.8 lines 432-433 | "An output reading a ruled table declares rules at least as strict" is not computable for free-form SQL filters on different columns (a 20-minute filter on `exchange_ts` against one on a candle's bucket start), so the check will degrade to "some rule exists". Through a definer view, base-table masks and filters do not apply at all | Throwaway Trino 484, memory catalog: `alice` reading table `t` saw rows filtered and `secret` masked; through `v_definer` (owner `admin`) she saw every row unmasked; a rule on the view itself (`carol`) did apply its mask and filter | Replace free-form row and column rules with named policies whose strictness compares: `delay: {column, minutes}`, `mask: column`, `rowScope: {column, values}`. Masked columns propagate by column lineage: absent or masked downstream. CI refuses an output whose policy is weaker |
| F4 | high | N6 exit (line 783); N1, N2 and N7 exits ("REST equals Trino") | The access proofs are circular. `make access-verify` compares Trino's behaviour with `permissions.json`; both are written by the compiler that wrote `rules.json`, so a compiler bug that drops a mask passes. REST equals Trino by construction, since the API queries Trino | Verdict 3.8 and 3.10 text; the generator is the only control plane (E2) | A hand-written expectation file per role and table (visible, masked, filtered columns; a known row that must not appear) checked against a throwaway Trino in CI, extending `make test-trino`, which already starts the generated `etc/` |
| F5 | high | 3.8; N6 (line 783); D3 (line 980): absent | Any user who can reach a writable domain catalog can run `optimize`, `rollback_to_snapshot`, `expire_snapshots` on its tables: file-based rules do not cover table procedures in Trino 484. From the tag, a read-only consumer can roll back a mart. No phase closes it | `docs/adr/ADR-044-...md:116-120` ("any user can still run ... `rollback_to_snapshot`"); `:29-30`; its revisit trigger at `:154` names the entitlements phase | In N6: give consumers a reader catalog per domain with `iceberg.security=read_only` (ADR-044's own evidence row at `:30` shows that connector denies table procedures) and deny them the writer catalog, or adopt OPA; flip `make test-trino`'s known-gap check |
| F6 | high | Phase rules, lines 768-769; 6.6 line 869; N11 line 788 | Backup scope is four hard-coded tables in the old namespaces (`TABLES="raw.messages audit.checks gold.dim_instrument gold.dim_venue"`). After N3 drops those namespaces, backups and `make drop`'s precondition cover none of the new product tables, the landing bucket, or the governance warehouse holding state history. The restore drill at N11 is the first time anyone would notice | `scripts/backup-lake.sh:22-27`; `docs/runbooks/lake-backup-and-restore.md:16` ("not yet drilled") | In N1: generate the backup set from contracts (`backup: restore` or `rebuild-from: <input>` per table), include landing buckets and the governance warehouse; run a first restore drill at the end of N3, not only at N11 |
| F7 | high | 3.3 line 164 (`trigger: {after: [tables]}`); lifecycle table line 207; 3.7 lines 404-406 | Airflow asset scheduling waits for all listed assets. `canonical-trades` scheduled after three venue tables stops when one venue is paused or failing, and `candles` with it: the opposite of "flagged partly fed" and of addendum 3 L's independent failure. The OR form runs it three times per tick, three Spark sessions | Airflow docs (3.4.0): "Airflow schedules a Dag after all assets the Dag consumes have been updated at least once since the last time the Dag ran" | Derived products trigger on the archive asset or a cron and read whatever inputs published since their recorded snapshots, with a per-input freshness flag; state the semantics in 3.3 |
| F8 | high | Risk 1, lines 39-41; N2 line 779 (spike S2); 3.7 lines 413-417 | The biggest risk is measured after the work that depends on it (N1 builds the per-product runner and pool), and measured while `lake_ingest` still competes for the same `flock`. Today's single job: 6 h mean 140 s; two-day p50 185 s, p90 248.5 s, above ADR-034's 240 s; latest window p90 140.5 s. About nine sessions per tick add their fixed cost on top. The verdict also dropped Spark Connect from the escape list | Prometheus `airflow_dagrun_duration_success` (140.3 s, 6 h); Airflow metadata DB, `task_instance` for `lake_ingest` (329 runs, two days); bare Spark SQL session in a throwaway container 3.5 s wall | Move S2 into N0: time one chain (session, catalog loads, branch, audit, fast-forward) in a scratch namespace with `lake_ingest` paused for the window. Retire `lake_ingest` at the start of N2, not the end of N3: addendum 5 Q makes old tables irrelevant to the proof and P permits the drop. Keep Spark Connect as an escape |
| F9 | high | 3.4 table lines 226-231 ("Spark container, stdlib fetch"); 5, line 751 | Every new connector, including the redirect-safe HTTPS fetch, runs on an engine image past end of life: Python 3.10.16 (end of life 2026-10-01), OpenSSL 1.1.1w (end of life 2023-09-11), base `tabulario/spark-iceberg` last published 2025-03-11. Criterion 5 asks for active releases; addendum 5 U allows replacing substrate for a specific reason, and this is one | Throwaway run: `python3 -c "import sys,ssl..."` printed `3.10.16 OpenSSL 1.1.1w`; endoflife.date API; Docker Hub tags API | In N0 or N1: rebase `docker/spark/Dockerfile` on the official `spark:3.5.9-scala2.12-java17-python3-ubuntu` (published 2026-11-20) with pinned Iceberg jars, or run fetch connectors in a slim maintained Python image holding only the landing key |
| F10 | high | 3.5, lines 267-317; E5 | The transform vocabulary reinvents SQL: `select`, `explode`, `filter`, `union`, `aggregate` and `flags` are clauses of one `SELECT`; the Coinbase flat index is already a SQL expression (`docker/lake/silver.py:130-135`); `trade_flags@1` is two window functions (`silver.py:186-221`). With dbt for marts, parsers and registered code, there are four ways to write a transform (criterion 2) and a private language where a standard exists (criterion 4) | `docker/lake/silver.py:88-140` (specs are `SELECT ... LATERAL VIEW posexplode` strings and `expr AS name` lists) | Replace the vocabulary with SQL models. Preferred: dbt-core on Trino for silver, gold and marts (section 6). Minimum: one `.sql` file per table plus a small header (key, incremental mode), run by the verdict's runner |
| F11 | medium | 3.1, lines 83-86 | `product.yaml` is a private descriptor where a standard exists. ODCS deprecated its string field `dataProduct` in v3.1.0 because ODPS took that role; ODPS v1.1.0 was released the same day as ODCS v3.2.0. ODPS has `status` (proposed, draft, active, deprecated, retired), input and output ports with `contractId`, team, support and custom properties | GitHub releases API: ODCS v3.2.0 and ODPS v1.1.0 both 2026-09-08; vendored `contracts/schema/odcs-json-schema-v3.2.0-20261117.json` (`dataProduct`: "DEPRECATED since v3.1.0", a string) | Make `product.yaml` an ODPS v1.1.0 document; keep `paused`, `lifecycleChange`, `pipeline` and `access` as ODPS custom properties; vendor its schema beside ODCS's |
| F12 | medium | N4, line 781 | Addendum 1 D: "the first phases of your plan must deliver these four connectors". Public API and FTP/SFTP arrive in N4, after the crypto rebuild and the old job's retirement, about day 22. Not put to the owner | `ADDENDUM-1.md` item D; N4 depends only on N1 | Run N4 before N3 (it needs only N1), or list it as an owner decision |
| F13 | medium | 1.1, line 58 | A paused venue product is still consumed by `raw-archive`, against addendum 1 A ("the platform stops consuming"). The judge resolved this tension alone. And `raw-archive` is one Spark job for three topics: one venue's fault stops landing for all, the coupling diagram 6 criticises | `ADDENDUM-1.md` A; verdict line 520 | Add it to section 8 as an owner decision; land each topic in its own task inside `raw-archive` so one topic's failure does not stop the others |
| F14 | medium | 3.3, lines 155-156 ("service identities, never people") | People have no identity path to the SQL endpoint, yet the audience is "many teams" (brief item 6) and criterion 3 asks for access without tribal knowledge | Verdict text; D3 covers authentication strength, not principals | Allow pseudonymous person ids in `consumers.yaml`, one role each; or groups through the file group provider, which the judge verified exists in 484 |
| F15 | medium | 3.6, lines 370-372; 9, line 997 | Product-state history from a dbt snapshot is not rebuildable from empty (a release-gate item), lives in a warehouse the backup skips, and its `valid_from` is build time | `scripts/backup-lake.sh:26` ("the per-domain dq.dbt_test_results are not copied"); dbt docs: snapshots on Trino Iceberg need a `current_timestamp(6)` override | Generate the history seed from `git log` of `contracts/` (merge commit and time per state change); load it as a dbt seed. Exact, rebuildable, no snapshot |
| F16 | medium | 1, lines 18-19 ("publishing every table through write, audit, fast-forward"); 3.5 line 261; cut order line 790 | Marts do not use WAP. `dbt build` replaces the table, then runs its data tests; a failed test leaves the bad table published. Blue/green dbt, the fix, is second in the cut order. Derived SQL products break criterion 1's "never publishes unaudited data" | `dbt/dbt_project.yml` (`+on_table_exists: replace`); `docker/airflow/dags/dbt_marts.py` (one `dbt build --select <node>` per node); Trino 484 Iceberg docs list branch reads only, no branch writes | State the gap; take blue/green out of the cut order, or roll back on test failure (`ALTER TABLE ... EXECUTE rollback_to_snapshot`), the audit-after-publish pattern ADR-047 already accepts |
| F17 | medium | N11, line 788; D2, line 979 | Three days for a fresh-clone rebuild, a never-run restore, the governance pack, five golden paths, the README tour and benchmarks is not credible. A restore "into a fresh stack" cannot run beside the live stack on one host (fixed `k2-*` container names, ports, the ceiling). D2's destinations are not a blocking exit, so the tag can ship with alerts going nowhere | `docs/runbooks/lake-backup-and-restore.md:16`; the restore also needs `LAKEKEEPER_ENCRYPTION_KEY` (`:49`) | N11 exits add: a test alert received at the external destination; the off-host copy listed; `make drop` refusing under `mode: production`; branch protection shown by `gh api`. Say the drill stops the live stack. First drill earlier (F6) |
| F18 | medium | N0, line 777; 6.6 line 869 ("053: make drop (N1)") | N0 drops `lake.scratch_p4`, `lake.scratch_p4_pr1` and bucket `scratch-p4-landing` "after a backup", but the backup does not copy them and `make drop` is built in N1: addendum 5 S broken in the first phase | `scripts/backup-lake.sh:27` (fixed `TABLES`); `LEDGER.md` ("Landing bucket not in the backup") | Build `make drop` in N0 with an `mc mirror` of the named objects as its backup step |
| F19 | medium | 3.7, line 407 ("one dbt DAG per mart") | The existing dbt DAG renders with cosmos `LoadMode.DBT_LS`, which runs `dbt ls` at parse on a cache miss. Many dbt DAGs from one generated file, all invalidated by one contract change, risk the DAG processor's per-file timeout, and then every product DAG in that file disappears | `docker/airflow/dags/dbt_marts.py:135`; `airflow dags report` today: `dbt_marts.py` 0.145 s (cached) | Render from a manifest built in CI or the image (`LoadMode.DBT_MANIFEST`), or run one `dbt build --selector <product>` task per product run |
| F20 | medium | Line 790 ("Total about 50 days"); 6.5, lines 851-859 | The estimate omits twelve phase audits and review rounds, the ISO 4217 product, D3's three days (folded into N10's six with everything else), and the readers of the old table names: 6.5 lists readers of `lake_ingest`, but 231 tracked files name `raw.messages`, `bronze.*`, `silver.trades_*` or `gold.*` (dashboards, alert rules and tests, notebooks, maintenance, backup, parity, skills) | `git grep -l -E '\b(raw\.messages\|bronze\.(binance\|kraken\|coinbase)\|silver\.trades_\|gold\.(trades\|ohlcv_\|bars\|dim_))' -- ':!legacy' \| wc -l` printed 231 | Re-estimate after slice 1 from measured pace; list the table-name readers in N3's scope; give audits their own line |
| F21 | low | 3.3 line 170 (`mask or hide`); 3.8 lines 430-431 | A hidden column makes `SELECT *` fail while `information_schema` hides the column: the user sees no column and an error naming it | Throwaway Trino: `bob` `SELECT *` gave `Access Denied: Cannot select from columns [secret]`; his `information_schema.columns` listed only `id`, `ts` | Drop `hide`; masks only (one way to do it) |
| F22 | low | 3.6 line 363; N9 line 786 | `make apply` means the post-merge deploy in 3.6 and the IaC converge in N9 | Verdict text | One target, documented once, or two names |
| F23 | low | 3.10 lines 489-490 against 3.8 line 435 | The API "must not evaluate a rule" yet answers 403 early from `permissions.json`: a second enforcement point (contradicts E7) that can disagree with Trino | Verdict text | Let Trino deny and map its error to 403; pick columns from `DESCRIBE` run as the consumer |
| F24 | low | 3.5, lines 311-312 | The grep guard on registered transforms misses `spark.sql(...)`, `df.write` and `df.sparkSession` | Verdict's list | Call it advisory; the real control is `CODEOWNERS` and review |
| F25 | low | 5, line 750 ("not checked") | Iceberg 1.8.1 is four minor releases behind 1.12.0 (2026-09-30); Lakekeeper runs v0.13.9 (2026-11-14) while v0.15.1 is out (2026-11-15). Upgrade debt with no trigger | GitHub releases API | Add both to N10 with a dated revisit trigger |
| F26 | low | 3.5 (WAP); `file_source.py:29` on the PR branch | A failed audit keeps its branch "for inspection"; a stream product failing every five minutes with per-run branch names accumulates refs in table metadata | `git show origin/feat/file-source-pattern:docker/lake/file_source.py` lines 23-32, 335, 377 | One fixed audit branch per table, replaced on each run |
| F27 | low | 4.2, lines 678-716 | The ECB product has four storage steps (landing, bronze, silver, gold) for one small daily file; gold adds one column silver could carry | Verdict's own example | Make layers optional; a product materialises only layers with distinct guarantees |

## 3. Fact re-check

| # | Claim | Judge said | I found | How |
|---|---|---|---|---|
| 1 | Trino 484 file rules support column `mask`, row `filter`, `security.refresh-period`; first match applies, no match denies | verified, docs | Confirmed by docs and by running: masks and filters applied on a table; unmatched users denied | trino.io/docs/484 file-system access control page; throwaway `trinodb/trino:484`, memory catalog |
| 2 | Masks and filters through definer views | "not run"; slice 2 tests | Base-table masks and filters **do not** apply through a definer view; rules on the view itself do | Same throwaway Trino, users `alice`, `carol`, `dave` |
| 3 | Hiding a column under `SELECT *` | "documentation is silent" | `allow: false` fails `SELECT *` with Access Denied; `information_schema` omits the column | Same, user `bob` |
| 4 | Trino behaviour on a failed rules refresh | could not verify (for B's HTTP source) | With a file source, an invalid file fails every query for every user until fixed; recovers at the next refresh | Same, `refresh-period=5s`, truncated file |
| 5 | Airflow 3.3.2 pinned; 3.4.0 released 2026-11-06 | verified | Confirmed; 3.3.2 released 2026-09-17; cosmos 1.16.0 (2026-11-03) | GitHub releases API; PyPI; `docker/airflow/Dockerfile:4-5` |
| 6 | Multi-asset triggers suit derived products | not examined | All listed assets must update before a run (AND) | airflow.apache.org asset-scheduling page (3.4.0) |
| 7 | `lake_ingest` averages about 151 s (147 s) | verified | 6 h mean 140 s; two-day p50 185 s, p90 248.5 s, max 478 s; the latest window p90 140.5 s | Prometheus HTTP API; Airflow metadata DB (`task_instance`) |
| 8 | JVM start about 4 s | verified | A bare local Spark SQL session took 3.5 s wall; per-run catalog, branch and audit cost still unmeasured | Throwaway `k2-spark-iceberg` container, no network |
| 9 | Bootstrap 21.30 CPU / 37.812 GiB; parallelism 4 | verified | Confirmed; the budget script already resolves profiles, so the N0 figure of 17.30 / 29.812 follows | `python3 scripts/check-budget.py`; `docker-compose.yml:92` |
| 10 | N0 exit `docker compose config --services` lacks a profiled service | stated | Confirmed. Also answers the inventory's open question: a plain `docker compose down` leaves a stopped container of an inactive profile in place, and keeps its volume | Throwaway two-service compose project |
| 11 | Raw topics keep 48 h or 2 GiB per partition | verified | Confirmed; 12 partitions per raw topic | `docker/redpanda/init.sh:138-147` |
| 12 | ODCS v3.2.0; ODCS "deprecated its product object in v3.1.0" | verified | v3.2.0 released 2026-09-08; required fields are `version`, `apiVersion`, `kind`, `id`; the deprecated item is a string `dataProduct`; ODPS v1.1.0 shipped the same day and the verdict never mentions it | GitHub releases API; vendored schema; bitol-io ODPS changelog |
| 13 | FastAPI 0.162.5 (newest at least two weeks old), 0.165.0 latest; uvicorn 0.54.0; trino 0.340.0; dbt-core 1.12.7; dbt-trino 1.12.0 | verified | All confirmed with dates | PyPI JSON |
| 14 | Alertmanager v0.35.0 and v0.35.1; OpenTofu v1.13.9 and v1.13.10 | verified | Confirmed | GitHub releases API |
| 15 | Iceberg 1.8.1, branches and `fast_forward` | branch code read; latest not checked | Used correctly in the PR branch (create or replace branch, `fast_forward`, drop branch); latest Iceberg is 1.12.0 | `file_source.py:335-377` on the branch; GitHub API |
| 16 | Lakekeeper v0.13.9 | stated | v0.13.9 published 2026-11-14 on an older line; v0.15.1 current | GitHub API |
| 17 | Spark image has Python 3.10.16 and `ftplib.FTP_TLS` | verified | Confirmed, and Python 3.10 reached end of life 2026-10-01; OpenSSL 1.1.1w; base image last published 2025-03-11 | Throwaway run; endoflife.date; Docker Hub API |
| 18 | Redpanda Connect's Iceberg output needs an Enterprise licence | verified | Confirmed | docs.redpanda.com Iceberg output page |
| 19 | dbt v2 proprietary, an Apache "dbt OSS"; stay on 1.x | verified | Confirmed; 1.x stays Apache 2.0 and maintained; I found no Fusion engine adapter for Trino | getdbt.com licensing FAQ; docs.getdbt.com Fusion pages |
| 20 | Iceberg branches usable for WAP from Trino (matters for dbt) | not examined | Trino 484 documents only reading a branch (`FOR VERSION AS OF`), not writing one | trino.io/docs/484 Iceberg connector page |
| 21 | Contract expressions are safe "fragments" | asserted | `reflect` exists in Spark 3.5.5 and reads the process environment | Throwaway run, `DESCRIBE FUNCTION reflect` and a harmless variable |
| 22 | All thirteen descriptors say `lifecycle: draft` | verified | Confirmed | `grep -h '^lifecycle' contracts/*/*/product.yaml \| sort \| uniq -c` |
| 23 | Table procedures fenced per user | not discussed | Not fenced on writable catalogs in 484; the repo records it as an open limit | `docs/adr/ADR-044-...md:29-30,116-120` (not re-run by me) |

The judge's other repo line references that I spot-checked held (`offsets.py:38,278`,
`contract_schema.py:45`, `maintenance.py:165-171`, `ci.yml:84-89`, `parity_ohlcv.py:480-482`,
`generate.py:365,408`, `ADR-050:133`, `lake.sql:29`). `Makefile` lines have drifted a few lines
since `bb6e77f`.

## 4. Requirements checklist

| Item | Requirement | Verdict | Where |
|---|---|---|---|
| Brief 1 | Product defined once, YAML in a PR, ODCS, k2gen, CI; read-only catalog UI | met; the product descriptor ignores ODPS (F11) | 3.3, 3.9 |
| Brief 2 | One lifecycle state obeyed by every layer | partly: derived-product triggers contradict "partly fed" (F7) | 3.3 table |
| Brief 3 | Source kinds incl. API and database pulls, internal tables, derived SQL | partly, by design: database pull designed only (D6) | 3.4, 8 |
| Brief 4 | Stream pipeline declared, not rewritten | superseded by addendum 2 H then 5 Q; met as clean rebuild | 3.11 |
| Brief 5 | SQL endpoint plus REST, REST built after catalog, before gate | met, with D1 put to the owner | 3.10, N1, N7 |
| Brief 6 | Many teams: domains, owners, access rules, one host | partly: people have no identity path (F14); table-procedure gap (F5) | 3.3, 3.8 |
| Brief 7 | ClickHouse paused behind a profile, volume kept, checks stood down visibly, every reader found, return as a declared serving product | met; the inventory's open question answered here (fact 10) | 6.2 |
| Brief 8 | Book capture paused, no resumption planned | met | 3.11, N3 |
| Brief 9 | New plan supersedes P4 to P7, goals kept or named obsolete, `3.0.0` at gate | met; tag spelled `v3.0.0`, stated openly; OpenLineage capture deferred with a trigger | 6, 6.6, 3.9 |
| Brief 10 | One review round; audits fix critical and high; hardening after IaC | met | 6, N10 after N9 |
| Constraints | One host, 25 / 48, pins, no secrets, loopback, ADRs immutable | partly: the engine image is past end of life (F9); everything else met | 1, 5 |
| Add. 1 A | Captures outside; paused product means the platform stops consuming | contradicted for venue products, resolved by the judge alone (F13) | 1.1 |
| Add. 1 B | Kafka/Redpanda is a connector kind declared in the contract | met | 3.4 |
| Add. 1 C, 2 F | Airflow-backed, config-driven; latency stated; continuous mode reserved | met | 3.4 |
| Add. 1 D | First phases deliver public API, Kafka, FTP/SFTP, files | contradicted quietly (F12) | N4 |
| Add. 1 E | Separate generic connector from venue logic | met | 3.11 |
| Add. 2 G | ETL design: layers, vocabulary, engines, incrementality, schema change, tests, lineage, escape hatch, build versus adopt | met in depth; the vocabulary is the weak point (F10, section 6) | 3.5, 5 |
| Add. 2 G | Three worked examples (JSON API, Redpanda, SFTP CSV) | partly: J replaced two; the short SFTP and JSON examples are absent from the verdict | 4 |
| Add. 2 I | Captures in the same repo, own area | met | 3.4 |
| Add. 3 J | Kraken and ECB through all four layers, every file shown | partly: contracts "abridged"; the derived products' contracts and `roles.yaml` not shown | 4.1, 4.2 |
| Add. 3 K | Venue products separate; unified trades and candles derived, with lineage | met | 4.1 |
| Add. 3 L | One pipeline per product; fits the host; start cost, lock, scheduler load | partly: cost unmeasured and measured late (F8); AND triggers couple products (F7) | 3.7 |
| Add. 3 M | Product, column and row permissions into Trino and REST; catalog shows who reads what | partly: definer views and strictness (F3), circular proof (F4), table procedures (F5) | 3.8 |
| Add. 4 N | Five to eight ethos principles | met (E1 to E8) | 2 |
| Add. 4 O | Six Mermaid diagrams with reading guides; chapters phase item | met | 3, 6.1 |
| Add. 5 P | Standing permission to drop and rebuild | met | 6 |
| Add. 5 Q | Clean rebuild, independent proofs, what is captured again and let go | met; `lake_ingest` could retire earlier (F8) | 3.11 |
| Add. 5 R | Gate includes rebuild from empty and restore drill; controls built before the gate | partly: backup scope (F6), first drill at the gate, D2 not blocking (F17) | N10, N11 |
| Add. 5 S | Backup first, confirmed, then drop | contradicted in N0 (F18) and hollow after N3 (F6) | N0, 6 |
| Add. 5 T | Platform over contents | partly: full FX content kept (N5) | N5 |
| Add. 5 U | Greenfield allowed; keep proven substrate unless a specific reason | met, but a specific reason exists for the Spark image (F9) | 3.12 |
| Add. 5 V | Vertical slices | met with one change (N0 first), argued | 6.1 |
| Ledger | Every item placed or dropped with a reason | met: each ledger line maps to a row of 6.6 | 6.6 |

## 5. Failure modes not covered by findings

- **03:00, maintenance.** `lake_maintenance` ran 2,043 s at p50 over the last two nights
  (Airflow DB), holding the global lock for about 34 minutes while the raw archive queues. The
  48 h retention covers it; the first archive run afterwards reads up to `maxOffsetsPerPartition`
  per partition and every downstream product then pays a catch-up run. Fine if F8's measurement
  includes the catch-up tick.
- **A generator bug.** With everything generated, the drift check proves only that committed
  files match the generator. The independent oracles are F4's expectation file for access, the
  parity scripts for data, and `make test-trino` for the rules file loading. Add a CI step that
  starts Trino on the generated `etc/` with `refresh-period` absent and runs F4's fixture.
- **A bad contract merge.** A contract that compiles but selects the wrong column publishes
  through WAP if its audits are weak. The example tests catch it only if the author wrote a case
  for it; a readiness scorecard that demands at least one example per output is cheap.
- **Lakekeeper down.** Every run fails at catalog load and retries; nothing publishes; Trino
  queries fail. Loud and recoverable, as today (`scripts/chaos/lake-lakekeeper-stop.sh`).
- **Airflow scheduler with the Docker socket.** The scheduler mounts `/var/run/docker.sock`
  (`docker-compose.yml:642`), so every generated DAG runs with root on the Docker engine. The PR
  branch's factory passes contract values as an argument list, not a shell string
  (`factory.py:59`), which is the right pattern; keep it a generator rule.

## 6. The dbt-core alternative for silver, gold and marts

**The alternative.** Connectors (Spark for Kafka, Python for files and APIs), generated from the
contract, land raw and bronze only. Silver, gold and marts are dbt-core models on Trino, one
folder per product scaffolded by `k2gen` from the ODCS contract (sources, columns, model
contract, tests), with dbt unit tests, data tests, incremental merge and snapshots. Order-book
replay and event bars stay registered Spark code.

**Facts at the pinned versions.**

| Question | Answer | Source |
|---|---|---|
| Versions and licence | dbt-core 1.12.7 (2026-11-12), dbt-trino 1.12.0 (2026-11-13), Apache 2.0, already pinned and in CI (`make test-dbt`) | PyPI; `dbt/pyproject.toml` |
| Fusion engine | fails criterion 5 here: no Trino adapter found, newer than two years, proprietary distribution beside an Apache subset | docs.getdbt.com Fusion pages; licensing FAQ |
| Incremental | `append`, `delete+insert`, `merge` (Trino `MERGE`; Iceberg v2 position deletes, v3 deletion vectors). Not yet run through Lakekeeper in this repo | dbt trino-configs page; Trino 484 Iceberg page |
| `on_schema_change` | supported | dbt trino-configs page |
| Model contracts | supported; column names and types checked at build, before publish; only `not_null` constraints enforced, so keys stay data tests | dbt trino-configs page |
| Snapshots | supported; on Iceberg override `trino__current_timestamp()` to precision 6 | dbt trino-configs page |
| Branch WAP | not available: Trino 484 reads Iceberg branches but cannot write them | Trino 484 Iceberg page |
| May Trino write silver and gold? | Yes, without touching ADR-035's read-only `lake`: each domain's dbt user writes its own warehouse (ADR-044), so `market_capture.kraken_trades.silver_trade` is legal. ADR-035's role table assigns silver and gold to Spark, so a new ADR supersedes that row (Accepted ADRs are immutable) | `docker/trino/etc/catalog/*.properties`; `docs/adr/ADR-035-...md:65` |
| Prerequisite | F5 must be closed first, or every reader of silver and gold can roll them back | `ADR-044-...md:116-120` |
| Cost per run | dbt task per node today: p50 5.3 s, p90 20.8 s (141 builds, two days), mostly process start; a Spark session starts in about 3.5 to 4 s plus unmeasured catalog and WAP cost. dbt moves silver and gold compute off the 3 CPU Spark container, the current bottleneck, onto Trino (2 CPU / 5 GiB), which then needs a resource group so five-minute builds cannot starve interactive SQL | Airflow metadata DB; `docker-compose.yml` limits |
| Cosmos | today one task per dbt node (`dbt_marts.py`); for five-minute products render one `dbt build --selector <product>` task per run, from a CI-built manifest (F19) | `dbt_marts.py:128-145` |
| Lineage, docs, catalog | the dbt manifest is the model graph across products (sources are bronze, `ref` is an upstream product); `dbt docs generate` is a static catalog; OpenLineage has a dbt integration and an Airflow provider, so the standard format comes nearly free; `k2gen` still draws product-level lineage | dbt and OpenLineage documentation |

**What is lost against the verdict.**
- **Strict E4 for silver and gold.** Publish-after-audit becomes: model contract checked before
  publish, unit tests in CI, data tests right after the merge, and on failure an automatic
  `ALTER TABLE ... EXECUTE rollback_to_snapshot` to the previous snapshot. Downstream products do
  not run, because the asset event is emitted only after the build task passes; a direct SQL
  reader can see bad rows for seconds. That is ADR-047's audit-after-publish, already accepted for
  the landing. Files and streams keep branch WAP in their connectors.
- **Snapshot-id incrementality.** Silver advances by a watermark on `src_partition` and
  `src_offset` in SQL instead of `k2.src-snapshot-id`; idempotent through `merge` on the key.
- **One engine for the producer layers.** Lineage crosses engines twice (bronze in Spark, silver
  and gold in dbt, event bars back in Spark, marts in dbt); event bars write to the producer
  lake, which Spark alone writes.
- **A translation, not a string move.** `LATERAL VIEW posexplode` becomes `CROSS JOIN UNNEST ...
  WITH ORDINALITY`; Trino's decimal multiplication overflows where Spark's does not
  (`ADR-035-...md:43`), already handled in the existing dbt candle mart. Addendum 5 Q's proofs
  (fixtures, DuckDB recomputation) cover the translation the same way they cover the verdict's.

**What is gained.** No private language: SQL plus dbt's `config()`, the industry standard the
owner wants to learn. One way to write a SQL transform instead of two. Tests, contracts, docs and
lineage in standard forms. F1 closes for SQL transforms, since Trino has no reflection and its
writer is fenced per domain. D5 ("Spark in CI") shrinks to the registered transforms, which
already have pure-Python fixtures. `trade_flags@1` becomes two window functions in SQL.

**Which is better.** For this owner, dbt-core on Trino, by a clear margin on criteria 2 to 5 and a
small loss on criterion 1 that the rollback narrows. If you weigh strict branch WAP above all
else, the second-best option is the verdict's runner executing plain Spark SQL files, with no
vocabulary.

**Plan change, a judgement.** N1 drops the compiler, the four incremental modes, the plan hash and
the Spark compile tests (ADR-057, ADR-058), and gains `k2gen` scaffolding of dbt folders, the
reader catalogs of F5, the rollback-on-failure macro and a Trino resource group: about the same
size, but built from parts the repo already runs. N2 and N3 translate three venue silvers and the
candles to dbt SQL instead of lifting strings into YAML. Net about three to five days fewer, one
decision (D5) mostly removed, one ADR added (silver and gold on Trino, superseding ADR-035's row),
and E4 reworded per engine.

## 7. Cuts and deferrals

No loss to the stated goal (products end to end; unified ingestion, catalogue and query layer with
permissions):

1. **The transform vocabulary and its compiler**, replaced by dbt models (section 6) or plain SQL
   files. The largest invention in the design.
2. **Per-consumer rule compilation over a union of roles.** Give each principal exactly one role
   (`k2gen validate` refuses two; a combined need is a combined role) and write rules per role
   through the file group provider. Smaller rules file, no union calculus, same predictability.
3. **The catalog UI and live panel at 3.0.** Fixed decision 1 says the UI comes "later"; the
   generated pages in `docs/products/` (they already draw lineage) plus `dbt docs` are the 3.0
   catalog. Trigger: a second team asks for search.
4. **FX content beyond the capability.** Keep `fx-crosses` (a derived SQL product) and the
   stablecoin basis (a cross-domain product); defer returns, volatility, reporting-currency bars
   and the decomposition, which add content, not capability (addendum 5 T). Your call under brief
   item 9.
5. **`maturity` as a declared field.** Show the readiness scorecard's result on the page; no
   second state to keep in step with `lifecycle`.
6. **`hide` column rules** (F21) and **mandatory gold layers** for small products (F27).
7. **Raw archive retention:** choose 7 to 14 days for the lab under D7, not 30.
8. **D4:** take option (a), host IaC only.

## 8. The simpler alternative

The simplest design that meets the stated goal: ODCS contracts plus an ODPS product file in git;
`k2gen` generates Iceberg DDL, Trino rules per role, product pages and one Airflow DAG per product;
each DAG is a connector task (Spark for Kafka landing and bronze decode, a maintained slim Python
image for files and APIs, each with branch WAP as PR #239 does) followed by `dbt build --selector
<product>` on Trino for silver, gold and marts, with rollback on a failed test; event bars and book
replay stay registered Spark code; Trino is the one SQL endpoint with reader catalogs that refuse
table procedures; FastAPI over Trino is the REST layer; deploy is an exported tag. Compared with it,
the verdict buys strict branch WAP on every silver and gold write, one engine from raw to gold with
Kafka-offset resume semantics, and no pipeline load on Trino. It pays with a private transform
language, a compiler, Spark in CI, and the security exposure of F1. For one owner who wants a
standard, teachable platform, the simpler design is the better one; the verdict's spine (files,
generator, per-product DAGs, slices) carries over unchanged.

## 9. What I could not check

- **The fixed cost of a product run** (catalog loads, branch, audit, fast-forward on Lakekeeper and
  MinIO): measuring it needs a write to the live stack. F8 asks for it in N0.
- **Masks and filters on Iceberg REST tables specifically.** I tested on Trino's memory catalog;
  access control is engine-level, so I expect the same, but did not run it on `lake`.
- **Whether a read-only reader catalog keeps definer views working** (the F5 fix). Not run.
- **dbt-trino `merge` and snapshots through Lakekeeper**, and whether dbt-trino supports the
  `microbatch` strategy: documented or unknown, not run.
- **Airflow 3.3.2's handling of several asset events between runs** (the 3.4.0 page describes a
  new batching option; I did not confirm 3.3.2's default).
- **Trino under five-minute dbt merges beside interactive queries.** Reasoned, not measured.
- **ODPS v1.1.0's full field list**: I read the changelog and the v1.0.0 reference, not the 1.1.0
  schema.
- **Whether the N0 scratch objects still exist**: I did not list MinIO buckets.
- **The days estimate.** Neither the judge's 50 days nor my three to five day saving is a
  measurement.

Sources consulted on the web: trino.io/docs/484 (file-system access control; Iceberg connector),
airflow.apache.org asset scheduling, docs.getdbt.com (trino-configs; licensing; Fusion),
bitol-io ODPS changelog and v1.0.0 reference, docs.redpanda.com (Iceberg output), PyPI JSON,
GitHub releases API, Docker Hub tags API, endoflife.date API.

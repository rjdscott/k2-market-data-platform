# Audit: phase 004, the Kraken chain, 2026-11-29

**Verdict:** not yet, for the tag `v3.0.0-alpha.6`. The chain runs live: release candidate
`v3.0.0-alpha.6-rc.1` was deployed on 2026-11-28, the first runs of its five DAGs were green, `make
product-verify` accounts for every offset of `kraken-trades` on the live stack, and the 48-hour soak runs
on it. Every finding the three adversarial reviews raised is fixed but one, A7, which waits on the
owner's ruling on Kraken's terms; both CRITICAL findings (D1, the first stage of a trimmed topic; R1, a
drill that damaged before it checked) were fixed before anything they touch ran live, D1 before the rc and
R1 before any live drill. Five of the Exit's nine clauses are proven, four on throwaway lakes and one on the
live stack; the drill is proven on a throwaway only.
What is unproven, plainly: **no restore, drill, rebuild or parity check has run on the live stack**, and
rc.1 cannot run them (its lake-init makes no restore user, its dbt runner has no `--full-refresh`, its
`tools` image no DuckDB); the restore after a host loss is not proven by anything; a Kafka product's
rebuild restages its whole landing in one Spark batch, unmeasured at live size; nothing rebuilds a file
product's bronze; the soak has not ended. This audit adds two MED findings of its own (E1, E2): the drill
checks that the restore key is set but not that MinIO accepts it, and the host-run tools of the exit
branch do not refuse a live release older than they are.
Before the tag: a release of the exit branch (rc.2 or later) deployed after `make engine-keys`, the soak's
benchmark, `make restore-drill` on the live volumes with its time, the parity pair live, E1 and E2 fixed (the
restore step is on them) before that drill, then W2-26's review and `FULL=1 make spine-e2e`.

| | |
|---|---|
| **Commit** | `1117aae`, the head of `feat/phase-004-exit` (local and pushed; CI `success` at it, `gh run list --branch feat/phase-004-exit`). Phase 004 merged to `main` in pull request #257 at `100ee0d` (2026-11-28 10:44 UTC); the exit branch is 35 commits past it (`git log --oneline origin/main..1117aae`) |
| **Live** | `v3.0.0-alpha.6-rc.1` (tag at `8df5be3`, the head of #257 before its merge), deployed 2026-11-28; the 48-hour soak runs on it since 2026-11-28 11:21 UTC. None of the exit branch is in it (`git merge-base --is-ancestor df043fc v3.0.0-alpha.6-rc.1` exits 1, as does `1f27a61`) |
| **Scope** | Plan `004-kraken-trades-instruments-security-master.md`: its Exit and every Verification line. Phase 004 as merged in #257, and the exit branch: `make product-verify`, `make rebuild` with `k2-dbt build --full-refresh`, `make restore` and `make restore-drill` with the restore key, `make parity-silver` and `make parity-ohlcv` with DuckDB in `tools`, the six chaos scripts, the proof scripts `kafka-path.sh` group d, `restore-path.sh` and `duckdb-landing.sh`. The findings of the three reviews, by id, and what fixed each |
| **Out of scope** | Phase 005; re-reviewing what the three reviews examined and found sound (each review's "found sound" list stands); open rows of earlier audits, except where a claim here repeats one |
| **Method** | No container started; the live stack, its checkout, `.env`, `~/k2-secrets` and `~/k2-releases` untouched. Read at `1117aae`: the plan, the build breakdown (`docs/research/2026-11-28-phases-004-005-build-breakdown.md`), `scripts/restore.py`, `scripts/rebuild.py`, `scripts/product_verify.py`, `scripts/parity_silver.py`, `scripts/parity_ohlcv.py`, the four new `mk/` files, the proof scripts' headers, the chaos README. `git log --grep` and `git log -S` for each fix's commit; `git show 8df5be3:<path>` for what rc.1 carries; `gh run list` and `gh run view --log-failed` for CI. The reviews: two independent reviews of #257 at `1d4a6be` (2026-11-27 18:29 UTC; data path and deploy, ids D; access, identity and secrets, ids A), one of the exit branch at `d86675c` (2026-11-28 13:22 UTC; ids R). Each file line given for a D or A finding was re-read at `1d4a6be` here, and each E finding's at `1117aae`. The proof scripts' results are quoted from their recorded final lines, all runs of 2026-11-28 (UTC) on throwaway lakes; the live facts are the orchestrator's runs on the stack, named as such |
| **Severities** | As the 2026-11-27 audit: CRITICAL (the README's BLOCKER): a false claim about access, safety or data loss, or a defect that loses data. HIGH: a defect an operator would meet in normal operation, a false claim an operator would act on. MED: a defect with a stated or recoverable consequence, a check weaker than its text. LOW: stated limits, cosmetics |
| **Findings** | 2 CRITICAL · 5 HIGH · 13 MED · 9 LOW (29); 26 resolved, 3 open: A7, and E1 and E2, which the restore step is fixing (E1 and E2: Resolved in `145a81a`) |

---

## Plan 004's Exit, clause by clause

| Clause | Proven by | Where | Status |
|---|---|---|---|
| a killed Kafka connector resumes exactly once | `bash scripts/chaos/kafka-connector-kill.sh <release dir>`: kraken-trades' load killed after its landing commit, before it, then retried under its run id; each next run lands and stages the range once, no hole. `kafka-connector-kill: 3 PASS, 0 FAIL in 302 s` | throwaway | proven on a throwaway; not induced live |
| `make product-verify` accounts for every offset | `make product-verify PRODUCT=market-capture.kraken-trades` on the live stack: every check PASS (landing spans, bronze accounting, level, each output's tag). The plan's "a second run adds nothing" is `kafka-path.sh` claim a3, on the fixture product | live, rc.1 | proven live |
| an unknown symbol is held without stopping the venue | `unknown-symbol.sh`: the listed symbol publishes, the build is `degraded`, the refresh rule starts the instruments run, the next build takes the held trade in once. `unknown-symbol: 4 PASS, 0 FAIL in 418 s`. The trigger itself is the scheduler's: `docker/airflow/tests/test_factory.py` | throwaway | proven on a throwaway |
| a failed venue test leaves the derived products unchanged | `venue-test-fails.sh`, `4 PASS, 0 FAIL in 318 s` | throwaway | proven on a throwaway |
| a paused input makes the derived product `partial` | `pause-input.sh kraken-trades`, `4 PASS, 0 FAIL in 223 s` | throwaway | proven on a throwaway |
| the delayed role sees nothing newer than 20 minutes and a null connection id | the rules are compiled (`silver_trade.odcs.yaml`: `delay: {column: trade_ts, minutes: 20}` and its masks), but no principal holds a role on a Kraken table, by the owner's decision, so no row exercises them | none | open: A7 |
| after 48 hours the delete-file count stays under its bound and the chain's run times are published | the soak runs on rc.1 since 2026-11-28 11:21 UTC | live | open, by the clock |
| the restore drill passes and records its time | `bash scripts/verify/restore-path.sh <release dir>`, `restore-path: 19 PASS, 0 FAIL in 661 s`; the drill's restore 32.0 s, 53.9 s with its checks and damage (claim 10) | throwaway | open: not yet live, and rc.1 cannot run it |
| tag `v3.0.0-alpha.6` | | | not cut |

## The Verification block, line by line

| Line | Status |
|---|---|
| 111 `kafka-connector-kill.sh` | `3 PASS, 0 FAIL in 302 s`, throwaway |
| 112 `kafka-below-log-start.sh` | `4 PASS, 0 FAIL in 160 s`, throwaway, on its second run: the first stopped at claim 4 on an Airflow image built before `trino` joined it; the script now builds the image from the tree (`scripts/chaos/README.md`) |
| 113 `make product-verify PRODUCT=kraken-trades` | live, rc.1: every check PASS, run from a checkout of the exit branch with the live checkout as `K2_HOME` (rc.1's checkout has no `mk/product-verify.mk`) |
| 114 `make parity-silver PRODUCT=kraken-trades` | throwaway only (`duckdb-landing.sh` claims 4 and 6, `7 PASS, 0 FAIL in 546 s`); live: not yet, and rc.1's `tools` image has no DuckDB (`parity_silver.py` refuses: `this image has no DuckDB (a tools image built before W0-11)`) |
| 115 `make parity-ohlcv` | as line 114, claims 5 and 7 |
| 116 `unknown-symbol.sh` | `4 PASS, 0 FAIL in 418 s`, throwaway |
| 117 `bronze-failed-then-repaired.sh` | `4 PASS, 0 FAIL in 316 s`, throwaway |
| 118 `venue-test-fails.sh` | `4 PASS, 0 FAIL in 318 s`, throwaway |
| 119 `pause-input.sh kraken-trades` | `4 PASS, 0 FAIL in 223 s`, throwaway |
| 120 `make access-verify` (delayed_reader) | open (A7); the replacement until a grant: the canary's `p.canary-delayed` rows, whose rule has the same compiled shape (`contracts/governance/access-canary/events.odcs.yaml`: `delay: {column: event_ts, minutes: 20}`, `mask: [note]`), run in every live `make release-verify` |
| 121 `docker exec k2-trino trino --execute ...` | not run here; as written it cannot run (Trino serves HTTPS with password logins only, ADR-060), the same as plan 002's lines 119 to 120 in the 2026-11-27 audit |
| 122 `make hold` then `make unhold` | not run on a stack; the build breakdown (W2-28) places it after the soak |
| 123 `delete-files.sh` | the soak's start and end (W2-18, W2-20); not recorded in the tree |
| 124 `make restore-drill` | throwaway only (`19 PASS, 0 FAIL in 661 s`); live: not yet |
| 125 `make test` | not run here (containers); CI `success` at `1117aae` |
| 126 `make check-docs` | this file's gates, below |

---

## Findings

D and A: the two reviews of #257 at `1d4a6be`. R: the review of the exit branch at `d86675c`. M: found by
the build's own proofs, the rehearsal or CI. E: found by this audit. Lines are at the reviewed commit.

| # | Severity | File | Claim | Reality | Fix | Resolved in |
|---|---|---|---|---|---|---|
| D1 | CRITICAL | `docker/lake/kafka.py:233` (`stage_ranges`) | a Kafka product's first stage plans the range it landed | a partition with no bronze through-offset started at 0 while `startFrom: earliest-retained` lands from the log start, so the audit planned rows that were never there: the first live run on the trimmed raw topic would fail its audit for good, a reset restaging the same range. Every throwaway topic began at 0, so nothing could meet it | a partition bronze never staged starts at the landing's first offset in it; a unit test with the log start above 0; `kafka-path.sh` claim b6 (the topic trimmed before the first run) | `1f19e9b` (merge `0f850f9`), in rc.1; live, the first `kraken_trades` runs, landing from the log start of a topic retention trims (`docker/redpanda/init.sh`), were green, and `make product-verify` passed |
| D2 | HIGH | `docker/lake/kafka.py:410-417`, `:491-493`; `docker/lake/connector.py:503-505` | an interrupted run is retried | only the run that staged a batch could audit or publish it; with retries 0 and a 10-minute DAG timeout, a killed run left a batch on `audit` that refused every later run, with no runbook | a Kafka batch is its offsets: one never audited, or passed and never published, is discarded and its range restaged by the next run; only a failed audit blocks (`kafka-batch-blocked.md` section 3) | `caf46f4` (merge `0f850f9`); `kafka-path.sh` claim a11, no PASS line recorded |
| A1 | HIGH | `platform/k2gen/k2gen/checks/access.py:280` (`_inputs`) | an output's inputs are checked for inheritance | they were the author's declared ports alone, never the model's `k2_input` calls: a model could read an undeclared input and skip every `inherit-*` rule (reproduced: `k2gen check` exit 0) | inputs are the declared ports plus every `k2_input` reached through the `ref` chain; an undeclared one is `access.input-undeclared` | `02aeebf` (merge `0c128b0`) |
| A2 | HIGH | `platform/k2gen/k2gen/checks/connectors.py` | a Kafka product reads its domain's data | nothing tied a topic to a product and Redpanda has no ACLs: any new product could read the raw Kraken topic, build silver with no delay and grant its own team | `contracts/domains.yaml` lists each domain's topics; one product of the owning domain reads each (`connectors.topic-claimed-twice`) | `2b0ca9b` (merge `0c128b0`) |
| D3 | MED | `docker/lake/catalog.py:157` | an unresolvable schema id is a decode failure | any registry HTTP error, a 5xx included, read as unresolvable; the frame staged with every envelope column null, and `parses` re-decoded from the landing, so a trades-only batch could publish null payloads | only a 404 is unresolvable, any other answer fails the task; `parses` compares what bronze staged | `514dc5f` (merge `0f850f9`) |
| D4 | MED | `docker/lake/kafka.py:360` (`land`) | one load lands a range once | the landing commit was a plain append: two loads at once landed a range twice | the landing commit requires its parent snapshot | `da4a9f7` (merge `0f850f9`) |
| D5 | MED | `contracts/market-analytics/canonical-trades/models/trades.sql` | a rebuild of canonical-trades leaves candles correct | `--full-refresh` numbered `load_seq` from 1, and candles reads rows past its stored `through_load_seq`: every row at or below it would never be read, a silent loss unless candles was rebuilt too | the full refresh numbers after the highest `load_seq` the table held (`trades.sql`, its header and the `numbered_after` block), so candles' next build recomputes every window | `218a7ee`; `kafka-path.sh` group d claims 1 and 2, `8 PASS, 0 FAIL` |
| D6 | MED | `docs/runbooks/onboard-a-file-product.md:268` | a new DAG starts unpaused | every DAG is created paused; nothing said so, and a fresh install left the canary paused and the live access checks weaker | verify names each of the release's DAGs that sits paused with no hold, and the unpause command; the onboarding runbooks give the unpause order | `3745714` and `5e13c15` |
| A3 | MED | `platform/k2gen/k2gen/checks/docs.py:292` | a grant outside the team needs terms | only the product's own terms were read: a derived product could be granted while an input had none | terms are checked on every product upstream | `3d49f20` (merge `0c128b0`) |
| A4 | MED | the holds table's rules | only `make hold`, rebuild and restore write holds | Trino enforced only that `svc.runs` may INSERT; its password is in every dbt-runner one-shot and the scheduler, so any of them could forge or end a hold | one writer, `svc.operator`, its password file host-held and mounted by nothing; every other principal reads holds through the read-only `governance` catalog | `8503b35` (merge `e000dff`) |
| D7 | LOW | `scripts/deploy.py` | a deploy that exits 0 left nothing to do | when the holds could not be read, the DAGs the drain paused stayed paused and the deploy still exited 0 | exit 4, naming the DAGs, the reason and the unpause command; kept in `deploy-paused.json` | `e62788f` (merge `258ff80`) |
| D8 | LOW | `docker/lake/maintain.py` | a landing object is expired once published | expiry matched the sha256 alone: a published observation could delete the object a later unpublished one of the same bytes still needed | each logged observation is checked at its own `fetched_at` | `9a15180` (merge `258ff80`) |
| A5 | LOW | the delayed role's masks | a delayed reader learns nothing of the last 20 minutes | build and delivery counters (`load_seq`, `through_load_seq`, `kafka_offset`, `received_at`) let it infer recent activity | masked for `delayed_reader` | `948cde4` (merge `e000dff`) |
| A6 | LOW | `roles.yaml` `within` | the role order is checked | any identifier passed, a role within itself included | `within` names declared roles, no cycle; `public_reader` within none | `8f20f7f` (merge `0c128b0`) |
| A7 | LOW | `tests/access/expectations.yaml` (the Kraken tables' rows) | the delay and mask rules on the new tables hold | the rows are denials to a principal with no role; the delay and mask are compiled but no row exercises them until a grant, and the owner grants nothing on a venue-derived product until Kraken's terms are ruled on | rows for a delayed grantee once a grant exists; until then the canary's rows of identical compiled shape (Verification line 120) | **open** |
| R1 | CRITICAL | `scripts/restore.py` (`drill`) | the drill can put back what it destroys | it damaged the lake before the backup checks: a release that added a table since the backup made the lake unrestorable; no check for what was committed since the run; the DAGs not paused first | every refusal first (keys, the run newest, complete and of the live set, nothing committed or landed since it, no rebuild lock), a statement of what it destroys and `CONFIRM=<live release id>`, then pause, drain and check again before any damage; deletes by location, sparing a file product's bronze | `8d596bb`; `restore-path.sh` claim 7 |
| R2 | HIGH | `scripts/restore.py` (`catalog_lost`) | the catalog database is replaced only when lost | any psql error read as lost: a busy or restarting Postgres would have had its live catalog replaced by the dump | lost only on a definite answer (no warehouse row, or the database or table does not exist); any other answer replaces nothing and fails | `8d596bb`; `restore-path.sh` claim 9 |
| R3 | HIGH | `scripts/restore.py` | the restored lake is consistent when the DAGs run again | the restore recreated canonical-trades' table empty, kept candles, then unpaused: `load_seq` restarted at 1 and candles skipped rows, a silent loss | a product with a table registered, re-pointed or recreated, and every product downstream, stays held (restore) and paused until `make rebuild` passes, upstream first; the order is printed | `8d596bb`; `restore-path.sh` claims 14 and 17 |
| R4 | MED | `scripts/restore.py` | nothing writes while tables come back | no drain before the restore; a rebuild lock counted as a hold | every DAG drained as `make rebuild` drains; an open rebuild lock refuses | `8d596bb`; `tests/test_restore.py` |
| R5 | MED | `scripts/restore.py` | the restore leaves the DAGs as it found them | it unpaused every DAG, also after a refusal past the catalog replay | only the DAGs it paused, and none after a replay that then stopped | `8d596bb`; `tests/test_restore.py` |
| R6 | MED | `scripts/restore.py` (`whole`) | a table is whole when its files are there | data files were not checked | every manifest list, manifest, data and delete file of `main` and the `audited` tag, from one listing | `8d596bb`; `restore-path.sh` claim 18 |
| R7 | LOW | `scripts/restore.py` (`docker_run`) | the restore key reaches only what writes | it was passed to containers that need none | only the `platform` and `rest` phases get it; `check` reads with the backup key | `8d596bb`; `tests/test_restore.py` |
| R8 | LOW | `mk/rebuild.mk`, `scripts/rebuild.py` | (unstated) | bronze reads no row from the replace until the restage publishes, said nowhere | the help and the header say it | `8d596bb` |
| M1 | MED | `scripts/access_verify.py` (`untested`) | the tested-data marker proves every reader view hides untested rows | it planted only copies of the tagged rows, so an output whose tag holds none (`venue_symbol_held`, `silver_trade_unresolved`, `silver_trade_rejected`, empty in their normal state) got nothing past it, and preflight refused the release, exit 2 | one placeholder row in the contract's columns past every tag, an empty one included | `a5b53db` (merge `5889658`) |
| M2 | MED | `docker/dbt-runner/k2_dbt.py` (the publish) | a reader never fails during a publish | a query that read the view before a narrowing and its base after the tag commit failed with `INVALID_VIEW` (seen in the marker's schema change and in the full deploy rehearsal of 2026-11-28); the catalog has no commit across a view and a table | the publish waits after narrowing a view, before moving the tag; ADR-061's Outcome | `9a78c0d`, Outcome in `8df5be3` |
| M3 | LOW | `scripts/deploy.py` (`containers`) | `make release-verify` reads the live services | it took a throwaway lake's Spark container, a `docker run` of the image compose built and carrying its labels, for the live `spark-iceberg`, and failed while a throwaway ran (2026-11-28) | only containers compose created (its config-hash label) | `d1123b1`, not in rc.1: `make release-verify` runs the checkout's `scripts/deploy.py`, so from rc.1's checkout it still fails while a throwaway runs |
| M4 | LOW | `platform/k2gen/k2gen/emit/pipeline.py` (`ONESHOT_KINDS`) | the compiled one-shot limits are compose's | after `tools` moved to 1 CPU / 2G in compose, the compiled `tools` kind kept 0.25 / 256M; CI's `Contracts (k2gen)` failed at `c4e8bfd` on `test_the_one_shot_kinds_agree_with_the_compose_stubs` | the emitter follows compose; CI `success` at `ff034a5` | `ff034a5` |
| E1 | MED | `scripts/restore.py:691-701` (`prepare`), `:849-889` (`drill`) | `make restore-drill` is "refused ... unless the newest backup run can put back all it destroys" (`mk/restore.mk`) | its checks prove the restore key is set (`pair`), never that MinIO accepts it: the `check` phase reads with the backup key, and the restore key is first used by the `platform` phase, after the damage. On a stack whose lake-init has not made the restore user (`make engine-keys` run, the deploy after it not yet), the drill empties the lake and the restore fails there, DAGs paused, until a release that makes the user is deployed and `make restore` runs again. The copy on the host is untouched, so it is recoverable | one listing of a warehouse prefix with the restore key among the checks before any damage | `145a81a` |
| E2 | MED | `scripts/rebuild.py:309-353`; `mk/restore.mk`, `mk/parity.mk` | (the tools run against the live release) | the exit branch's host-run tools run this checkout's scripts against whatever release is live, and none refuses one older than it. `make rebuild` against rc.1 replaces and restages a Kafka product's bronze (step 2), then the build is refused, since rc.1's `k2-dbt` has no `--full-refresh` (`git show 8df5be3:docker/dbt-runner/k2_dbt.py` has no such flag): a whole-landing restage for nothing. The parity targets refuse cleanly (`this image has no DuckDB`) | refuse before the lock when the live release predates what the tool needs (its runner's flags, the restore key in its lake-init); or run host tools from the live release's own copy | `145a81a` |

---

## Live, release candidate 1

Run by the orchestrator on the stack, not by this audit:

- `v3.0.0-alpha.6-rc.1` deployed 2026-11-28; the first runs of the five phase 004 DAGs were green.
- The five-minute chain's run times, first measurements and **not a benchmark** (end minus start of
  each run in `airflow dags list-runs <dag> -o json`, catch-up runs included): `kraken_trades` median
  71 s (n=11), `canonical_trades` 21 s (n=10), `candles` 30 s (n=10). The soak's benchmark publishes the
  measured p50 and p90.
- `make product-verify PRODUCT=market-capture.kraken-trades`: every check PASS.
- The 48-hour soak runs on rc.1 since 2026-11-28 11:21 UTC.

Not yet run on the live stack: `make restore`, `make restore-drill`, `make rebuild`, `make parity-silver`,
`make parity-ohlcv`. Each is proven on a throwaway lake only (last verified: throwaway, 2026-11-28; live:
not yet).

## Open items

- **A7**, above: no grant on any phase 004 product by the owner's decision (Kraken's terms, sections 8 and
  9, not yet ruled on); the canary's rows stand in.
- **E1 and E2**, above: resolved in `145a81a`. E1: the restore key proven against MinIO before any
  pause or damage (a listing and a put refused as existing, per scope). E2: `make rebuild` and the drill
  read `--full-refresh` from the live release's own pinned dbt-runner image, and the restore refuses a
  live release without the restore user or the holds table.
- **D5** is resolved: the counter continues above the old maximum (`trades.sql`, lines 16 to 23 and the
  `numbered_after` block), proven by `kafka-path.sh` group d.
- **A file product's bronze is rebuilt by nothing.** `make rebuild` refuses a file product whose bronze a
  restore recreated, and the drill spares it; a lost file-product bronze comes back only through its
  `restore` copy where the backup set says `restore`, and `k2.currency_master.bronze_currencies` says
  `rebuild`.
- **The live drills**: `make restore-drill` on the live, disposable volumes (W2-22), after a release of
  the exit branch is deployed following `make engine-keys`; then the three rebuilds it leaves, the first a
  whole-landing restage whose time and memory at live size are unknown.
- **The host loss** is not proven: the restore uses the `LAKEKEEPER_ENCRYPTION_KEY` the running
  Lakekeeper holds, and the key stays in `.env` by the owner's decision of 2026-11-28.
- **The landing is never expired**, by the owner's decision; its growth on disk is the soak's to report.
- **The 48-hour soak** runs on rc.1 since 2026-11-28 11:21 UTC.
- `kafka-path.sh` claims a11, a12 and b6 (D2's and D1's proofs) were added by the review fixes; merge
  `95e12a0` records groups a, b and c green with them, and no PASS line of that run is kept in the tree.

## This file's own gates

Run on this file's branch, with no container (Docker hidden from `PATH`, so gates (c) and (c2), the
Prometheus rules this change does not touch, print their skip note): `bash scripts/check-docs.sh`, gates
(a), (b), (d), (d2), (e), (f), (h), (i) and (j) PASS, (g) WARN only on Mermaid lines that predate this
change, exit 0. Its first run failed gate (j) on a runbook's `ISO 8601`, fixed before this file was final.

## Resolutions

Appended after the audit; the findings above stand as written.

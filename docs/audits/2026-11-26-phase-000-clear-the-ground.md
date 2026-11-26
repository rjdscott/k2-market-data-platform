# Audit: phase 000, clear the ground, 2026-11-26

**Verdict:** yes, phase 000 meets its exit, with two items that wait on the clock and are
disclosed: the scratch bucket `scratch-p4-landing` is under object lock until
2026-11-27T06:00:18Z, and the tag `v3.0.0-alpha.2` is not cut yet. Every other exit line was
re-run on the live stack and holds: ClickHouse is out of the default service list and its
container is gone, the budget gate passes at 16.70 CPU / 28.688 GiB (the landed line's figure),
the new Spark image ran three scheduled ingests and a passing `make gates` before the drop, the
sixteen drop manifests match the copy on disk to the object and the byte, every catalog in Trino
and every warehouse in Lakekeeper is empty, the `k2-lake` bucket holds no object, the grep for the
two old table names finds nothing, `make gates` exits 0 with five SKIP lines, and the four spikes
run for this audit exit 0. Nothing the phase says is gone is still running or scraped. No BLOCKER or HIGH was
found, so nothing must be fixed before the tag. The worst finding is a retention claim: the
operator guide says `~/k2-backup` is never pruned and the archive README says Airflow keeps the
retired DAGs' run history, but those DAG records are deleted and the only dump that still holds
them is rotated out by `make backup` around 2026-12-08. Recommended before the tag although not
required: copy the 14:59:55Z dumps out of the rotation (MED 1), drop the bucket once its lock
expires (MED 3), and recapture the screenshots (MED 2). The rest goes to the hardening list.

| | |
|---|---|
| **Commit** | `3d8495b` (`origin/phase/000-clear-the-ground`, the head of pull request #249) |
| **Scope** | The phase's claims: `docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md` (Delivers, Exit, Verification, the landed line), ADR-057 and its Outcome, `CHANGELOG.md` `3.0.0-alpha.2`, `legacy/v3-lake-ingest/README.md`, the body of pull request #249. Against them: the running stack, the tree, the three runbooks written in the phase (`pause-and-resume-a-serving-product.md`, `drop-and-rebuild.md`, `upgrade-a-pinned-component.md`), a sample of the documents that describe the running system (`README.md`, `CLAUDE.md`, `docs/operations/README.md`, chapters 04 and A1), and the copy under `~/k2-backup/drop/2026-11-26/` |
| **Out of scope** | Findings of the P0, P2 and P3 audits that this phase did not touch (the factory-default MinIO pair and the Postgres password equal to its user are still live, as the P0 audit recorded); the target design of ADR-056; Git history before `origin/main` |
| **Lens** | Claims against reality: doc accuracy, data safety of the drop, what is gone and what still runs |
| **Method** | Live stack strictly read-only, except throwaway containers (`audit000-*`, `k2-spark-candidate`, the spikes' own) that were removed, and the four spikes' scratch namespaces, which they dropped (Lakekeeper lists no namespace in any warehouse afterwards, `k2-lake` holds 0 objects). Every line of the plan's Verification block was run (results below). `make gates` ran from a worktree of the audited commit once the orchestrator's run had finished (`pgrep` empty, 15.8 GiB available). Credentials were read from `.env` inside scripts that print none; secret values were compared in-process, printing counts only. Prometheus and Grafana were asked through their APIs, Lakekeeper through its REST API, MinIO with `mc` in a throwaway container, Airflow with its CLI in the scheduler. |
| **Severities** | BLOCKER: data loss, secret exposure, or a gate that lies. HIGH: a claim in the phase's exit that is false, or a broken command in a runbook. MED, LOW. |
| **Findings** | 0 BLOCKER · 0 HIGH · 4 MED · 6 LOW |

---

## The Verification block, re-run

Run 2026-11-26 between 15:53 and 16:20 UTC.

| Line | Printed | Holds |
|---|---|---|
| `docker compose config --services \| grep -cx clickhouse` | `0` (`1` with `--profile hot-gold`) | yes |
| `docker ps -a --filter name=k2-clickhouse -q` | nothing | yes |
| `python3 scripts/check-budget.py` | `bootstrap default 21 services 16.70 CPU 28.688 GiB`, `PASS`, exit 0 | the landed line's figure, not the Exit's 17.30 / 29.812 (disclosed: `lake-ddl` and `lake-metrics` retired) |
| `docker run --rm k2-spark-iceberg:3.5.9_1.8.1 python3 -c "import sys, ssl; ..."` | `3.10.12 ... OpenSSL 3.0.2 15 Mar 2022` | yes |
| `for s in scripts/spikes/*/*/run.sh` | run: `spike-dbt` exit 0 (60 s), `spine-a-incremental-predicates` exit 0 (44 s), `spine-d-k2-input` exit 0 (33 s), `spine-f-rollback-across-replace` exit 0 (35 s), each printing its finding | yes for the four run. **Not run:** `spike-runcost`, `spine-g-audited-marker` and `spine-b-expire-by-id` (Spark jobs), `spine-c-pointer-swap` (about 25 minutes by its own header), `spike-trino` and `spine-e-writer-fence` (four were enough for the brief's three) |
| `make drop TARGET=k2.scratch_p4 DRY_RUN=1` | `drop: FAIL: the catalog has no namespace k2.scratch_p4`, exit 2 | as the landed line says: no target left |
| `docker exec k2-airflow-scheduler airflow dags list` | `No data found` | yes, and `airflow dags list-runs lake_ingest` prints `DAG: lake_ingest does not exist in 'dag' table` |
| `docker exec k2-trino trino --execute "SHOW SCHEMAS FROM lake"` | `information_schema`, `system` | as the landed line says; the same in all six Iceberg catalogs |
| `git grep -l -E '\b(raw\.messages\|audit\.checks)\b' -- <exclusions> \| wc -l` | `0` (the same pattern without exclusions finds 127 files, so the regex works) | yes |
| `make gates` | exit 0 in 3 min 38 s; `SKIP` lines for the dbt build (phase 002), `test-notebooks`, `lake-verify`, `parity-ohlcv`, `parity-bars` (phase 004); `499 passed`, Rust, `clickhouse-schema-test: PASS`, Trino checks, docs gates (a) to (j) PASS | yes |
| `make check-alerts`, `make check-docs` | inside `make gates`: `PASS: (c) promtool check rules`, `PASS: (c2) promtool test rules`, every docs gate PASS | yes |
| `bash scripts/health.sh` | `health: PASS`, exit 0; `(2) clickhouse gold.trades: SKIP (... ADR-057 ...)`, `(3) lake: SKIP (... phase 004)` | as the landed line says: a SKIP line, not no line |
| `make test` | inside `make gates`, as above | yes |

Also re-run: `make generate` exit 0 with an empty `git status --porcelain`, as pull request #249
states.

## Beyond the block

**Gone, and gone.** No container named `lake`, `clickhouse`, `spike` or `candidate` remains
besides `k2-lakekeeper` and the `k2-lake-init` one-shot. Prometheus loads 12 rules in three groups
(`airflow` 2, `capture_critical` 6, `capture_warning` 4), none firing, and scrapes 8 targets, all
up: `airflow-statsd`, `cadvisor`, three captures, `grafana`, `prometheus`, `redpanda`; the
`lake-metrics` job's last sample is at 15:49 UTC, when Prometheus was recreated. Grafana's API
lists four dashboards (`k2-airflow`, `k2-l2-capture`, `k2-pipeline-overview`, `k2-resources`) and
none mentions ClickHouse, the lake tables, dbt or `lake_ingest`. Lakekeeper's REST API lists six
warehouses with no namespace in any. MinIO: `k2-lake` holds 0 objects and 0 versions (unversioned);
`scratch-p4-landing` holds 13 objects, 19 versions, 2,814 bytes, under lock (MED 3). Every image
compose references is present on the host.

**Still running, and healthy.** All three captures pass `health.sh` step (2) and their
healthchecks. Every raw and trades topic's high watermark grew between two reads 60 s apart; the
three book topics did not (books paused, ADR-050). Retention as documented: raw topics
`retention.ms 172800000` and `retention.bytes 2147483648`, trades and book `604800000` with no
byte cap; `group_offset_retention_sec 604800`. Both ClickHouse groups describe `STATE Empty`,
`MEMBERS 0`. The Spark container runs only tini and the History Server as uid 185, with the
event-log cleaner at `2d` and 28 MB of event logs. The backup timer is enabled, next run
2026-11-27 04:30 UTC; the last success was 15:57:24Z, copying no table (SKIP) and two dumps.

**Disk.** `docker system df`: images 31.64 GB (13.09 GB reclaimable), volumes 44.49 GB
(`redpanda-data` 30.18 GB, `clickhouse-data` 2.967 GB kept, `minio-data` 66.75 kB), build cache
13.83 GB. Inside the Docker VM, 74 GB of 259 GB used. On the host, `/` has 209 GB free of 961 GB
(78% used), and `Docker.raw` occupies 260 GB (LOW 6).

**The copy of the dropped data.** `~/k2-backup/drop/2026-11-26/` holds 16 manifests whose
`objects` and `bytes` sum to 69,914 objects and 64,752,178,540 bytes (60.31 GiB), exactly the
legacy README's table, target by target. For every manifest the files on disk under its `copy`
paths were counted and summed again: all 16 match. The bucket's version copy holds 19 files,
2,814 bytes. Every `drop-<target>.log` but the bucket's ends `drop: OK: ... dropped`; the
bucket's ends with MinIO's `WORM protected` refusal. Every `tabular_purge` task Lakekeeper created
since 15:00 UTC in `k2`, `market_analytics` and `governance` is `SUCCESS` (28, 7 and 4 tasks). No secret value from `.env` appears in any drop log, manifest or
gates log. The pre-drop gates log (`~/k2-backup/drop/gates-new-image-2026-11-26.log`) prints
`lake-verify passed`, and `k2-spark-iceberg` has run image `sha256:54462083...`
(`k2-spark-iceberg:3.5.9_1.8.1`) since 14:30:48Z; the saved run list shows the scheduled
`lake_ingest` runs of 14:31, 14:36 and 14:41 succeeded on it.

**Runbooks of the phase.** Every read-only command was run and worked:
`pause-and-resume-a-serving-product.md` section 0 (five commands), section 2 checks 1 and 2
(check 2 prints nothing), section 3 (three commands, `604800`, `600000`, the retention lines),
section 4; `drop-and-rebuild.md` section 1's checks (`404`, Trino lists only the two schemas, `0`
objects, `mcrun ls k2`), section 2's purge task list, section 4's three lock commands (19 versions
retained until 2026-11-27T06:00:18.47Z, no legal hold); `upgrade-a-pinned-component.md` section 1
(Docker Hub tags, the digest `sha256:8901b842...`, Maven's `kafka-clients 3.4.1` and
`commons-pool2 2.11.1`), section 3's greps, section 4 steps 1, 3, 4, 7 and 8 on a throwaway
candidate (History Server `3.5.9`, 0 zombies, `spark_conf.lake_session` imports), section 5. Not
run: anything that stops, starts, drops or restores, the image build, and section 4 steps 5 and 6,
which the page itself marks not runnable until phases 004 and 002. The legacy README's
verification block reprints as written (`125 files changed, 0 insertions(+), 0 deletions(-)`;
`19 failed, 253 passed, 22 errors`; `./chaos/lib.sh: No such file or directory`).

**Known open items, and where they are durable.** The locked bucket: the landed line, the legacy
README's "Known residue", `CHANGELOG.md:70-71`. The required check kept for the archived exporter:
`.github/workflows/ci.yml:155-158` and the legacy README's "Known residue"; branch protection
still requires "Docker build (lake-metrics)", with `enforce_admins` on. ClickHouse's two 7-day
clocks: ADR-057's Outcome ("about 14:21 UTC on 2026-12-03"), with the check that will show the
expiry. The screenshots: nowhere in the tree (MED 2).

---

## Findings

| Severity | Location | Claim | Reality | Fix |
|----------|----------|-------|---------|-----|
| MED | `docs/operations/README.md:767`; `legacy/v3-lake-ingest/README.md:222-224` | "`~/k2-backup` is never pruned"; "the four DAGs are paused, not deleted, at this writing, so Airflow's database keeps their run history (and the 15:00 dump holds it too)" | The DAG records are deleted: `airflow dags list` prints `No data found` and pull request #249 says "old DAG records deleted". In the newest Airflow dump, `20261126T155723Z.airflow.sql.gz`, the old DAG ids appear only in `public.log`; their `dag_run`, `task_instance` and `task_instance_history` rows are only in the 04:33:53Z and 14:59:55Z dumps. `scripts/backup-lake.sh:36`, `:204-209`, `:238-239` keep the newest 14 dumps per database (the backup runbook says so, `docs/runbooks/lake-backup-and-restore.md:34-35`), so the 14:59:55Z dump is deleted by the nightly run of 2026-12-08 04:30 UTC, sooner with each manual `make backup` (one ran at 15:57:23Z). The run lists saved as `dag-runs-<dag>.json` in the drop directory are outside the rotation and survive. | Copy `catalog/20261126T145955Z.sql.gz` and `.airflow.sql.gz` into `~/k2-backup/drop/2026-11-26/`, which nothing prunes; correct both sentences (dumps rotate at 14; the DAG records were deleted after the drop, and where the run history now lives). |
| MED | `README.md:67`; `docs/operations/dashboards.md:19-21`, `:101` (generated by `scripts/screenshots/shots.py`) | The README's first picture is "K2 Pipeline Overview on a live run"; the caption under it names marker 3 "bytes per second Redpanda accepts from the captures" | `docs/images/dashboards/manifest.json` dates all eight images 2026-11-25, before the phase. Image 01 shows a "ClickHouse Pipeline" row and a `clickhouse UP` tile beside a README that calls ClickHouse paused (`README.md:14`, `:64`), and its marker 3 sits on "ClickHouse Row Insert Rate", so the regenerated caption no longer matches the picture. Image 15 shows a `clickhouse 1/1 up` scrape pool that Prometheus no longer has. The list of images that need a new capture ("01, 05, 07, 13 to 16") is only in pull request #249's body: `git grep -i recapture` finds no record of it in the tree. | `make screenshots` on the new stack, then `python3 scripts/screenshots/check.py --since 2026-11-26`; until then, say in the landed line which images predate the phase. |
| MED | `docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md:17-18` (Exit), `:152-153` (landed line); `legacy/v3-lake-ingest/README.md:213-220` | Exit: "`make drop` mirrored, counted and then dropped the scratch objects"; landed: the bucket "is dropped after" its lock expires | Disclosed, and still open: `make drop TARGET=bucket:scratch-p4-landing DRY_RUN=1` prints `FAIL: ... 19 object version(s) under COMPLIANCE,GOVERNANCE retention until 2026-11-27T06:00:18.47Z`, exit 2; the bucket holds 13 objects, 19 versions, 2,814 bytes, all also in the copy. No step, owner or timer is recorded for the drop, and the bucket's default retention (`GOVERNANCE 1DAYS`) re-locks anything written to it. | After 2026-11-27T06:00:18Z, `make drop TARGET=bucket:scratch-p4-landing`, then replace "is dropped after that" in the landed line and the legacy README with the result, before the tag if the tag waits for it. |
| MED | `CLAUDE.md:102-104`; `docs/adr/README.md:26-29` | CLAUDE.md: an Accepted ADR's "only permitted changes" are the status line and an appended Outcome | This phase added a third permitted change in the ADR index ("the link's target may be repointed or unlinked") and used it in six Accepted ADRs: 019, 028, 029, 030, 046, 047. Every changed line in those six differs only in a link target (checked by blanking every Markdown link target and diffing against `origin/main`: 0 other differences), and gate (h) now passes such a line. The working contract and the ADR index now disagree, and CLAUDE.md is the one every agent reads first. | The owner either approves the exception and CLAUDE.md:102 states it, or the six ADRs' links revert to dead targets with a note in each Outcome. |
| LOW | Pull request #249 body | "`make drop` over 15 targets"; "Prometheus, Grafana, Airflow recreated" | 16 targets were copied and compared (16 manifests, all matching), 15 dropped. `k2-airflow-api-server` was not recreated: created 2026-11-25T16:58Z, image `sha256:2cb77bd5...`, which no tag names now, while the scheduler and dag-processor run `k2-airflow:3.3.2` (`sha256:13c67d6b...`); `pip freeze` is the same in both. | Correct the body; recreate the API server with the operator guide's pattern when idle, so all three run one build. |
| LOW | `docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md:150`; `CHANGELOG.md:16` | "Phase 000 landed 2026-11-26, tag `v3.0.0-alpha.2`"; "`[3.0.0-alpha.2] - 2026-11-26`" | `git ls-remote --tags origin 'v3.0.0-alpha*'` lists only `v3.0.0-alpha.1`, which is expected before this audit; if the tag waits for the bucket (after 06:00 UTC on 2026-11-27), both dates are wrong. | Date both on the UTC day the tag is cut. |
| LOW | `docs/runbooks/drop-and-rebuild.md:248-252`, `:256-293` | "Last verified ... Run: every command on this page" | Section 4 was appended below that footer with its own "Last verified", so the page has two and the first predates section 4. Section 1's dry run is marked verified and now exits 2 (`no namespace k2.scratch_p4`); its comment at `:79` says so. | Move section 4 above the footer and merge the two lines. |
| LOW | `CLAUDE.md:15` | "`SHOW DATABASES` lists `gold` and nothing else" | Present tense, with no ClickHouse server to answer while `hot-gold` is paused (`docker ps -a --filter name=k2-clickhouse -q` prints nothing). | "listed `gold` and nothing else when it last ran, 2026-11-26". |
| LOW | `config/bars.yaml:1`, `:15`, `:23` | "the canonical event-bar thresholds behind `lake.gold.bars`", sized "from `lake.gold.trades`" | Both tables are dropped; the exit's grep checks only `raw.messages` and `audit.checks`, so it does not see these. | Say the tables are retired and that the thresholds return with `event-bars` (to release, phase 001). |
| LOW | Host disk (no claim) | n/a | The drop emptied MinIO (`minio-data` 66.75 kB) but freed nothing on the host: `Docker.raw` occupies 260 GB for 74 GB used inside the VM, while the drop copy (61 GB) and the backup copy (26 GB) were added on the same disk; `/` has 209 GB free. | `docs/runbooks/docker-desktop-disk.md`'s `fstrim` when the stack is idle; no shrink of the disk image. |

Every row carries `file:line` or a command with its output. A row that could not be
demonstrated was dropped.

---

## Resolutions

Appended after publication; the table above is never edited.

- Medium, backup pruning and the retired DAGs' run history: resolved in `a989438`. The two
  sentences are corrected, and the Airflow and catalog dumps of 14:59:55 UTC are copied into
  `~/k2-backup/drop/2026-11-26/`, which the nightly backup never prunes.
- Medium, the README's first screenshot: resolved in `b07adc0`. Every image was recaptured on
  the stack after the retirement, so no list of images awaiting recapture is needed.
- Medium, the immutability rule in `CLAUDE.md` against the link rule in `docs/adr/README.md`:
  resolved in `a989438`. `CLAUDE.md` states the link rule.
- Medium, the locked bucket: open. `make drop TARGET=bucket:scratch-p4-landing` is run after
  2026-11-27 06:00:18 UTC by the phase's operator; the result is appended here.

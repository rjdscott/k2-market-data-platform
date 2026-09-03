# Audit: end-to-end user stories across every tool — 2026-09-03

**Verdict:** Six adversarial passes over the same commit tell one story: every tool works
only if the reader already knows what's wrong with it. Of the quant story's five
requirements — trades with top-of-book, 1-minute bars, a security master, hour
completeness, and the same answer from two engines — exactly one worked, and only after
eighteen hand-written lines of SQL; the other four returned nothing, two disagreeing
numbers, or no answer at all. Four of the eight browser UIs (Prefect, Redpanda Console's
Avro decoding, Lakekeeper, and lake compaction itself) were broken outright when reached
the way a user reaches them, each by a one-line configuration or DDL bug. A newcomer on a
laptop with fewer than 15 cores gets no working stack at all, and the failure is silent:
six containers die with `invalid argument` and the page a newcomer is sent to never
mentions `cpuset`. The two gates the repo relies on to catch exactly this both passed
while lying: `scripts/check-docs.sh` reports every alert's runbook annotation resolves,
without ever checking that an annotation exists — five of six ClickHouse alerts have
none; and `make parity-ohlcv`, the only artifact that answers whether ClickHouse and the
lake agree, hard-crashes on a stale pinned snapshot id instead of running the comparison
it exists to run. None of the twenty BLOCKER findings below is a data-correctness bug in
the capture-to-ClickHouse hot path; every one is a wiring, path, or documentation defect
sitting in front of it.

| | |
|---|---|
| **Commit** | `1412e40` |
| **Scope** | End-to-end user stories across every tool the repo exposes: newcomer bring-up, the quant research story, operator alert→runbook→dashboard, developer/contributor workflow, docs navigation and staleness, and a live browser walkthrough of all 8 UIs |
| **Out of scope** | Single-diff/PR review (`/code-review`'s job), a fresh-clone release gate (`/release-check`'s job), security posture beyond what surfaced incidentally, `legacy/v1` and `legacy/v2-kotlin` (archived, not part of the live story), published benchmark numbers (`docs/benchmarks/`'s job) |
| **Lens** | UX and coherence — does the platform do what its own docs say, from the perspective of the person reading them |
| **Method** | Six adversarial passes, each walking one user story using only what the repo says, every claim refuted against source or the running stack (15 containers up); a seventh pass (Live UI) drove all 8 browser UIs directly via Chrome automation and cross-checked Grafana via its API. No praise recorded; every row below carries a `file:line` or a command output |
| **Findings** | 87 — 20 BLOCKER · 37 HIGH · 21 MED · 9 LOW |

---

## Findings

Grouped by story. Each group's id prefix traces to its source review (A = newcomer
onboarding, B = quant, C = docs layout, D = operator observability, E = developer, F =
live UI). Where the same underlying finding surfaced in two reviews, it is written once
under the story with the strongest evidence, with a pointer left in the other story's
group.

### Newcomer — clone, bring up, see live data, find every tool, run one query

**Story verdict:** completes only on a ≥15-core Linux/bash host in roughly 25 minutes,
almost all of it image builds; fails silently under 15 cores, breaks on zsh's
word-splitting, and "find every tool" has no single answer anywhere in the repo.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| A1 | BLOCKER | `.env.example:70,73`; `docs/development/setup.md:14-20` | `cp .env.example .env && make up` brings up the stack | On any host with <15 cores, the 3 capture containers **and** ClickHouse/Spark/lake-ddl die at start with `invalid argument` (`K2_CAPTURE_CPUSET=12-14` ships uncommented); `setup.md` — the page a newcomer is sent to — never mentions cpuset | Ship both cpuset lines commented out and default unpinned, or add a two-line cpuset note to `setup.md`'s "First run" |
| A2 | BLOCKER | `docker-compose.yml:516` | Comment calls port 8888 "Jupyter (if needed)" | Unauthenticated, token-less, **root** Jupyter on `0.0.0.0:8888`, in the container holding the lake's MinIO write credentials; appears in no URL table, no runbook | Bind `127.0.0.1:8888:8888` or delete the mapping |
| — | — | — | — | Prefect at documented `localhost:4200` — see **Live UI F-01**, which reproduces this live; same finding as A3/A4 | — |
| A5 | HIGH | `README.md:124` | `set -a && . ./.env && set +a` is needed before `docker compose up` | Compose reads `.env` unaided (verified with `env -i`); the line is bash-only and is the first thing that breaks a zsh user | Move the line to the first command that actually needs exported vars |
| A6 | HIGH | `docs/operations/quick-reference.md:75`; `docs/operations/data-inspection.md:17` | `CH="docker exec …"` / `$CH -q "…"` is the way to query | Fails in zsh (default macOS shell) — zsh does not word-split an unquoted var — and echoes the ClickHouse password into the resulting error | Replace with a shell function: `ch() { docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" "$@"; }` |
| A7 | HIGH | `README.md:137-145`; `docs/operations/quick-reference.md:28-38` | Every tool/UI is listed somewhere | No single page lists purpose + URL + credential + first-thing-to-look-at; JupyterLab (:8889, the actual research surface) and the spark container's Jupyter (:8888) are in neither table | One tool table with a "what it's for" column, add both Jupyters |
| A8 | HIGH | `README.md:128` | This `rpk` command "sees live market data" | Prints Confluent-framed Avro as unreadable mojibake; README never says values are Avro-encoded (quick-reference.md does) | Consume with `-f '%p %o %k\n'`; point at Redpanda Console for decoded values |
| A9 | HIGH | `README.md:135` vs `docs/runbooks/fresh-install.md:57-75` | "Every lake layer is populated within five minutes of a fresh clone" | Measured: first `lake-ingest` completes at +14m21s, ≈15m total from zero | Replace with the measured split: ~10m builds, ~3m to healthy, first lake data at ~14m |
| A10 | HIGH | `docs/operations/README.md:13,16,17,23,61-72,80` | Operations index describes the running stack | Stale in six places at once: wrong alert count, Prefect schedules called "not yet deployed" when they are, `k2` database referenced as live when dropped, wrong runbook/ADR counts, a dead `IcebergOffload*` section | Rewrite against the current stack |
| A11 | HIGH | `README.md`; `Makefile` | (implicit) `docker compose ps` tells a newcomer if the stack is healthy | No `health`/`smoke` target exists; the closest thing is buried inside `dev-up.sh` and, run standalone as `make lake-verify`, pauses the live ingest schedule and prints a "DATA LOSS" banner | Extract `dev-up.sh` steps (e)+(f) into `scripts/health.sh`, expose as `make health` |
| A12 | HIGH | `docs/operations/data-inspection.md:140-188`; `notebooks/README.md` | "Run one query against the lake" is a documented, copy-pasteable step | No bare DuckDB snippet exists in any markdown file; the only route is `make notebooks`, which needs `uv`, never listed as a prerequisite | Add a 6-line DuckDB CLI block to `quick-reference.md`; list `uv` as a prerequisite |
| A15 | MED | `docs/development/setup.md:10` vs `README.md:118`, `fresh-install.md:18` | Docker memory prerequisite | 28 GB on one page, 24 GB on the other two; declared bootstrap peak is 27.125 GiB, so 28 is the defensible number | Make all three say 28 GB, once, linking `docker-resources.md` |
| A16 | MED | `docs/development/setup.md:26-29` | `redpanda-init` "still creates" 6 frozen v2 topics · 160 partitions | Those topics were deleted at the Phase E cutover; only the 9 v3 topics + `_schemas` exist | Delete the stale parenthetical |
| A18 | LOW | `docs/runbooks/README.md:3,31,55` | Runbook index intro: "the running v2 stack", capture block: "these **four** carry no measured MTTR" | v2 is gone (nothing to be "running"); the capture block lists **five** files; `make chaos` did measure `CaptureDown` on 2026-08-26 | Rewrite the two lead sentences; fix four→five or name which four |
| A20 | HIGH | `README.md:126-127`; `docs/runbooks/fresh-install.md:83` | `docker compose ps` shows the 4 one-shots at `Exited (0)` | `docker compose ps` hides exited containers; `-a` is required, or the newcomer sees 15 lines instead of 19 and cannot perform the check | Use `docker compose ps -a` in both places |
| A21 | HIGH | `README.md:143`; `docs/operations/quick-reference.md:34` | Lakekeeper presented as API-only (`none (/health, /catalog/v1/...)`) | It has a web UI at `/ui/` (bare link 308-redirects there); no sentence anywhere in `docs/architecture/` says what Lakekeeper *is*, and there is no glossary | Add the `/ui/` URL to the tool tables |
| A22 | MED | `docker-compose.yml:832` | Compose comment: v2 topics/`k2` database are "**FROZEN, not dropped** … Phase E deletes them" | Phase E shipped 2026-08-27; both are gone — `SHOW DATABASES` has no `k2`, `rpk topic list` has no v2 topics | Two-line edit to the compose comment |
| A23 | LOW | `README.md:171-172` | Repository-layout block lists `schemas/avro/` (3 files) and `config/` (1 file) | `schemas/avro/` has a 4th file (`normalized-trade.avsc`); `config/` also holds `bars.yaml`, mounted at `docker-compose.yml:524` and required by `gold.bars` | Add the two names |
| A24 | MED | `docs/operations/data-inspection.md:25-37` | Schema cheat sheet lists the tables to query | Omits `gold.bars` (live, in the DDL and the server) and `lake.gold.book_state` | Add the two rows (same gap as B17) |
| A25 | MED | `README.md:158`; `Makefile:80` | `make lake-verify` sits in the Tests table next to unit-test targets | Pauses the live `lake-ingest-5min` schedule for ~2 minutes and exits non-zero with a "DATA LOSS" banner on any stack stopped past Redpanda retention — the normal state of a laptop on day two | One parenthetical in the README row |
| A13/A14/A19 | LOW | `README.md:86,154-156`; `docs/README.md:37,41`; `docs/operations/README.md:80` | "27 ADRs", "229 Python tests", "9 ClickHouse assertions", "60 Rust tests", "12"/"eleven" runbooks | 30 ADRs; 273 Python tests collected; the schema-test script hard-fails unless it gets exactly 10 assertions; 68 `#[test]`/`#[tokio::test]` attributes; 14 runbooks | Generate the counts, or link the one page that already has the right number instead of restating it |

### Quant — Kraken BTC/USD, one hour: trades+book, 1m bars, security master, completeness, same answer both ways

**Story verdict:** of five requirements, only trades+top-of-book worked, and only after 18
hand-written lines; bars gave two different counts, the security master was 99.5% empty
on the lake and absent on ClickHouse, completeness had no answer, and "same answer both
ways" was false — the two engines disagreed by up to $37 on the same second, and the one
check that would have caught it crashed.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| B1 | BLOCKER | `docker/lake/scd2.py:109,116`; `notebooks/03_asof_trades_book.ipynb` cell 7; `docker/lake/ddl/lake.sql:677` | An ASOF join against `dim_instrument` gives the security master as of trade time | Silently discards 99.5% of the archive — 36 of 7,651 trades for the target hour — because `valid_from` is set to when SCD2 first ran, not to the archive's start | Backdate the first SCD2 version of every instrument to `min(exchange_ts)` of the archive |
| B2 | BLOCKER | `docker/clickhouse/ddl/10-gold-tables.sql` (whole file) | Security master is mirrored to ClickHouse (`13-schema-design.md:49`, ADR-026) | `gold.dim_instrument` does not exist in ClickHouse — `Code: 60 UNKNOWN_TABLE` | State lake-only on the catalog page |
| B3 | BLOCKER | `10-gold-tables.sql:87-115,374-392` vs `lake.sql:1151-1194,1201-1214` | "Same answer from both engines" (story requirement e) | Top-of-book differs by up to $37 on the same `(venue, symbol, second)` — ClickHouse `book_top20` collapses to the *latest* 1 Hz sample in the second, lake `book_top20` replays the state at the *end* of the second; same table name, two constructions | Name them differently, or state the divergence and which is authoritative once |
| B4 | BLOCKER | `tests/parity/pinned.json`; `Makefile:96`; `scripts/parity_ohlcv.py:68` | `make parity-ohlcv` — the release gate (ADR-029:134) — answers "do the two engines agree" | Hard-crashes: `Could not find snapshot with id 1622213366608023449`, a literal from a different host/run | Fall back to `--pin-current` and print "pin stale, ran at current" instead of a traceback |
| B5 | HIGH | `docs/architecture/09-lake-layers.md:124-125`; `02-market-data-concepts.md:100-101` | "No security master yet … designed and deferred" | `gold.dim_instrument` is live (ADR-030, landed 2026-08-29, 34 rows) | Two-line edit pointing at ADR-030 |
| B6 | HIGH | `notebooks/03_asof_trades_book.ipynb` markdown cell 0 (only) | (implicit — no ClickHouse-side documentation of the join rule) | The `+1 SECOND` BBO-join rule lives in exactly one notebook cell; get it wrong and trade-through moves 44.49%→53.41% on identical data | Move the rule to the catalog page and the DDL comment above `gold.bbo_live` |
| B7 | HIGH | `docs/operations/data-inspection.md:236-237,39-42`; `10-gold-tables.sql:57-58,196-199` | `SELECT *` produces the documented candle/BBO export | `SELECT *` silently omits every ALIAS column — the documented OHLCV CSV export has no open/high/low/close | Spell the columns out in the export recipes |
| B8 | HIGH | *(absent)* | (implicit — `data-inspection.md` is presented as the cheat sheet) | No data catalog page existed; writing one query meant reading five doc pages and two DDL files, each with at least one wrong fragment | Write `docs/operations/data-catalog.md` |
| B9 | HIGH | `lake.sql:1146-1147` vs `10-gold-tables.sql:95-98` | "So ClickHouse `gold.book_top20` loads it column for column" | False — lake uses `bid_px_e8`/`ask_px_e8`, ClickHouse uses `bid_px`/`ask_px`; the claim is repeated in three places | Drop the "column for column" claims; show both spellings once |
| B10 | HIGH | `10-gold-tables.sql:152-171`; `lake.sql:750` | (implicit) 1-minute bars agree between engines | ClickHouse `ohlcv_live(bucket=60)` returns 36 bars for the hour, lake `ohlcv_1m` returns 30; no tie-breaker stated, and the served tier's `gold.trades` was six days ahead of the lake with nothing saying so | State on the catalog page that lake-gold is the record |
| B11 | HIGH | `lake.sql:599-600,1287-1295`; `10-gold-tables.sql:49-70` | (implicit — story requirement d, "is the hour complete") | `lake.audit.checks` is empty; ClickHouse `gold.trades` carries no `seq_gap`/`missing_before` at all; no per-instrument-hour completeness query exists anywhere | Add a `completeness()` helper |
| B12 | HIGH | `notebooks/05_bars.ipynb` cells 3,5,7,9; `Makefile:92-93` | `make notebooks-run` is the repo's runnable check for the research surface | Stays green while asserting nothing — hardcodes `DATE '2026-08-26'` (wiped 2026-08-28), compares two empty frames; a tautological test CLAUDE.md forbids | Derive the day dynamically; assert non-empty before the equality |
| B13 | HIGH | `docs/operations/data-inspection.md:30-31`; `10-gold-tables.sql:187,345` | `gold.ohlcv_1m`/`gold.bbo_1s` are the candle/BBO tables to query | Both empty (0 rows) on a running stack — pull-only, populated only by the ClickHouse-rebuild runbook. Same finding as Live UI F-09 | Mark the pull-fed tables; point at `ohlcv_live`/`bbo_live` until the pull runs |
| B14 | HIGH | `notebooks/k2lake.py:46,64,103` | (implicit) the research helper library covers the primary story | No helper does "one symbol, one hour"; the minimum working cell is 18 hand-written lines of triple-ASOF SQL | Add a `trades()` helper |
| B15 | MED | `docker/clickhouse/users.xml:30-49`; `docs/operations/data-inspection.md:17,21` | The read-only `quant` user is the documented research path | Every SQL example on both ops pages runs as `default`; `quant` is named once and never used in an example, and lacks the `system.parts` access the recipes need | Define `CHQ=` alongside `CH=` and use it for the research recipes |
| B16 | MED | `notebooks/pyproject.toml:20` (`package = false`) | (implicit) `k2lake` is importable | Only importable with cwd = `notebooks/`; a script anywhere else gets `ModuleNotFoundError` | One line in `notebooks/README.md`, or drop `package = false` |
| B17 | MED | `docs/operations/data-inspection.md:35,36,28` | Cheat-sheet time columns and table list | Silver book time column given as `snapshot_ts` (no such column — it's `recv_ts`/`recv_ts_ns`); lake-gold row omits `lake.gold.bars`; `dim_*` given a time column it doesn't have (`valid_from`/`valid_to`/`is_current`) | Fix the three cells |
| B18 | MED | `docs/architecture/13-schema-design.md:48` | Gold contract columns named `price`, `qty` | They're `price_e8`/`qty_e8`; `price`/`qty` exist only as ClickHouse ALIASes | Rename in the doc cell |
| B19 | MED | `10-gold-tables.sql:145-147,157` vs `lake.sql:746-748` vs `10-clickhouse-gold.md:71` | Candle open/close use "the same total order" | Three files state three different orderings | Settle on one, cite it from the other two |
| B20 | MED | `notebooks/k2lake.py:126-132` | (implicit) `bars()` is safe to call | Interpolates `symbol`/`exchange`/`start`/`end` directly into SQL | DuckDB parameter binding |
| B21 | MED | `lake.sql:596` vs `lake.sql:1163` | (implicit) `recv_ts_ns` means one thing | Two different semantics under the same name on two tables, disambiguated nowhere | One catalog row per meaning |
| B22 | MED | `docs/operations/quick-reference.md:143` | Documented Lakekeeper namespace output: `raw/bronze/audit/scratch` | Actual output: `raw/bronze/silver/gold/audit` — `scratch` doesn't exist, `silver`/`gold` are missing from the doc. Same finding as Newcomer A17 | Paste the real output |

### Operator — alert fires, is there a runbook, does the dashboard confirm recovery

**Story verdict:** five of six ClickHouse alerts carry no runbook reference at all; the
dashboard the docs call "open first" has zero lake or gold-freshness panels; and an
operator cannot be paged on this stack at all — no Alertmanager is wired, a gap the docs
at least disclose honestly.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| D-F1/D-F2 | BLOCKER | `docker/prometheus/rules/clickhouse-alerts.yml:35,56,82,116,145`; `docs/operations/observability.md:79`; `docs/architecture/11-observability.md:96`; `docs/runbooks/README.md:20` | "Every alert in `docker/prometheus/rules/` names a runbook in its annotations, and that path must resolve" | 5 of 6 ClickHouse alerts (`ClickHouseHighMemoryUsage`, `ClickHouseQueryFailureRateHigh`, `ClickHouseMergeQueueLarge`, `ClickHouseGoldFeedStale`, `ClickHouseKafkaMessagesFailed`) carry no runbook reference, neither annotation nor description line. `scripts/check-docs.sh` gate (d) only checks that a *declared* `runbook:` resolves, never that one exists — passes CI silently. Same finding as Docs-layout C-F5/C-F6 | Add `runbook:` annotations to the 5 alerts; add an "every alert has one" check to gate (d) |
| D-F3 | BLOCKER | `docker/grafana/dashboards/k2-pipeline-overview.json` (whole file) | `docs/operations/observability.md:70` calls this "the one to open first" | Zero lake panels, zero `k2_lake_*` queries, no panel on the gold-freshness signal — the two questions the operator story asks by name ("is lake ingest current", "is gold fresh") have no answer here | Add a 4th "Lake & Gold" row (5-panel spec in the source review) |
| D-F4 | BLOCKER | `docker/prometheus/prometheus.yml:14-17`; no Grafana `alerting/` provisioning dir; `/api/org/preferences` → `{}` | (the story's premise) "an operator gets paged" | Cannot happen on this stack at all — no Alertmanager container, no unified-alerting rules or contact points provisioned. Disclosed honestly at `11-observability.md:9,40` and `observability.md:225` | A Grafana native "Alert list" panel (reads Prometheus rule state directly, needs no Alertmanager) |
| D-F5 | HIGH | `docs/runbooks/capture-feed-stale.md`; `clickhouse-rebuild-from-lake.md`; `docs/runbooks/template.md:1-46` | The story requires "confirm recovery on the same dashboard" | 2 of 3 graded runbooks never name a dashboard or panel; `template.md`'s own shape (symptom → detection → expected → recovery → measured) has no dashboard field, so every new runbook inherits the gap | Add a "**Dashboard:** `<uid>` panel `<name>`" field to `template.md` |
| D-F6 | HIGH | `docker/prometheus/rules/clickhouse-alerts.yml:24-25` | `ClickHouseDown`'s Impact text: "All bronze/silver/gold writes are failing. Data is buffering in Redpanda" | Per CLAUDE.md, the v2 bronze/silver medallion is frozen — nothing produces to those tables, so there is nothing to fail or buffer; only `gold` (v3) is a live write path | Rewrite the Impact line to name `gold` only |
| D-F7 | HIGH | `clickhouse-alerts.yml:3` | File header: "Last Updated: 2026-02-19" | The file's own `clickhouse_gold` group (Phase E, ADR-026) postdates that stamp by roughly six months — the file reads as pre-v3 when it isn't | Bump the header date whenever the file changes |
| D-F8 | HIGH | all 4 dashboards | (implicit) ClickHouse alert conditions are watchable somewhere | 3 of 6 ClickHouse alerts (`ClickHouseDown`, `ClickHouseGoldFeedStale`, `ClickHouseKafkaMessagesFailed`) have no dedicated panel anywhere; nowhere to watch the fault clear except a fresh `curl` | Add the two Kafka-message metrics plus a dedicated `up` stat to `clickhouse-overview.json` |
| D-F9 | HIGH | no `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` | `observability.md:70` calls the pipeline-overview dashboard "the one to open first" | No org home-dashboard preference is set (`/api/org/preferences` → `{}`); a cold Grafana login lands on the stock welcome page | Set `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` |
| D-F10 | HIGH | all 4 dashboards, `links: []` | (implicit) an operator can click from a degrading panel to its runbook | Zero dashboard-level and zero panel-level links exist across all 4 files | Add dashboard `links:` cross-links |
| D-F11 | HIGH | `lake-alerts.yml:194,236`; `docker/grafana/dashboards/k2-lake.json` | (implicit) alertable gauges have a panel | `LakeUnresolvableSchemaId`/`LakeOffsetGap` carry correct runbook annotations but have no dashboard panel for their metrics | Add 2 stat panels to the "Maintenance and exporter" row |
| D-F12 | MED | `docker/grafana/provisioning/dashboards/default.yml:6` | (implicit) folder name reflects the current architecture | Folder is `K2 Platform v2` while 3 of 4 dashboards are titled `(v3)`; misdirects a newcomer browsing for "the current stuff" | Rename the folder to `K2 Platform` |
| D-F13 | MED | `clickhouse-overview.json:title` | Dashboard titled "ClickHouse Overview (gold)" | None of its 4 panels are gold-specific — generic server metrics that look identical with or without gold | Drop "(gold)" from the title, or add the gold panels (closes with D-F8) |
| D-F14 | LOW | `services/capture-rust/src/metrics.rs:96-99,90-94` | (implicit) emitted counters are observable | `k2_capture_unknown_frames_total`/`k2_capture_book_updates_ignored_total` are live in Prometheus but on no dashboard, in no alert | Add both to the Throughput row on `k2-l2-capture.json` |

### Developer — add a field, add a venue, understand what the test suite covers

**Story verdict:** the repo's own `/schema-change` skill and CLAUDE.md point a
contributor at a database that was dropped six days before this commit; the
fourth-exchange checklist omits two files that crash or silently blind monitoring the
moment someone follows it; and the testing doc undercounts the suite by two-thirds.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| E-F1 | BLOCKER | `CLAUDE.md:97`; `.claude/skills/schema-change/SKILL.md:19,49,55` | ClickHouse DDL for a schema change lives at `docker/clickhouse/ddl/01-k2-schema.sql`; verify against `k2.silver_trades` | That file only exists in `legacy/v2-clickhouse/`; the `k2` database is dropped. `13-schema-design.md` and `tests/test_wire_format.py` already cite the correct files (`10-gold-tables.sql`, `20-gold-kafka.sql`) — the two docs a contributor is told to use first are the wrong two | Repoint the skill and CLAUDE.md at the real files/table |
| E-F2 | BLOCKER | `docs/architecture/06-capture-venues.md:139` vs `docker/lake/gold.py:72,268,280`, `docker/lake/metrics.py:56-70` | "Adding a fourth venue" table says "Lake: nothing" | `_VENUE_DEPTH[exchange]` has no `.get()` — a new venue crashes `_build_dims` with `KeyError` on the SCD2 build; `metrics.py`'s hardcoded `TABLES` dict means the venue's lake-freshness metrics never register | Add both files to the checklist; fix the summary table |
| E-F3 | BLOCKER | `docs/development/testing.md:9-27,64` | Test inventory: 164 tests across 4 files; CI runs 6 jobs | Actual suite: 273 tests across 13 files (undercounted by 66%); CI runs 7 jobs — the `clickhouse` job is never mentioned | Regenerate the inventory from `pytest --collect-only` |
| E-F4 | HIGH | `CLAUDE.md:114`; `CONTRIBUTING.md:24` | `make test` = "python + rust" | It's three suites: `test-python test-rust test-clickhouse` | One-word fix in both files |
| E-F5 | HIGH | `CLAUDE.md:115`; `CONTRIBUTING.md:26`; `docs/development/testing.md:47` | `make test-python` command as documented | Omits `--with fastavro==1.12.2`; copy-pasted verbatim it fails collection on `test_replay_export.py`'s module-level import | Add the flag in all three places, or point at the Makefile target |
| E-F6 | HIGH | `docs/operations/adding-new-exchanges.md` (whole file) | This is "the full procedure" for a fourth exchange (`setup.md:181`, `06-capture-venues.md:130`) | Never mentions `gold.py`'s `_VENUE_DEPTH`, `metrics.py`'s `TABLES`, the one hardcoded Grafana panel (`up{job=~"capture-binance\|capture-kraken\|capture-coinbase"}`), or that `parity_ohlcv.py`/`tests/parity/pinned.json` assume a fixed 3-venue set | Add the 4 missing files to the Post-Integration checklist |
| E-F7 | LOW | `docker/offload/`, `docker/iceberg/warehouse/` (untracked, this host only) | — | Local leftover state from before the Phase D deletion (`git ls-files` → 0 for both); a fresh clone would not see them | Nothing to fix in the repo; host cleanup only |
| E-F8 | LOW | `services/capture-rust/src/exchanges/mod.rs:3-9` vs `adding-new-exchanges.md`, `06-capture-venues.md:127-139` | `mod.rs`: "there are exactly three venues and there will not be a fourth this year" | Two docs walk through adding a fourth venue in detail — a tone mismatch, not a functional bug (the enum's exhaustive `match` is the real per-site checklist) | Soften the comment, or state the exhaustiveness guarantee explicitly in the doc |

### Docs layout — navigation, staleness, the doc gate itself

**Story verdict:** the hub page (`docs/README.md`) and the tool-and-credential table
(`quick-reference.md`) were both unreachable from the root README by any link path; the
one screenshot embedded in the docs showed the wrong (v2) topic-naming scheme; and the
CI doc gate passes while runbook-annotation coverage silently regresses.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| C-F1 | BLOCKER | `README.md:186-192` | (implicit) README's Documentation section is the way into the docs | Links only `architecture/`, `adr/`, `runbooks/`, `benchmarks/`, `MIGRATION-JOURNEY.md` — zero inbound links to `docs/README.md` (the stated hub), `docs/operations/`, `docs/development/` | Add `docs/README.md`, `operations/README.md`, `development/` to the list |
| C-F2 | BLOCKER | `docs/operations/quick-reference.md` | (implicit) this is the page for "every UI + URL + credential" and "how to query" | Unreachable from `README.md` by any link path | Link it from README's Quick start |
| C-F3 | BLOCKER | `docs/operations/observability.md:75` (`redpanda-console-topics.jpg`) | The one embedded screenshot shows current topic names | Shows `market.crypto.trades.binance`-style names — `docker/redpanda/init.sh:44` calls this pattern explicitly "the v2 topic", not the real v3 `market.crypto.v3.{raw,trades,book}.<exchange>` | Recapture the screenshot, or drop it for prose |
| C-F4 | HIGH | `observability.md:79` vs `:138` vs `:161` | "28 alert rules"; "eleven" lake alerts | Internal arithmetic doesn't add up — `lake-alerts.yml` has 12 rules, the table lists 12; "(11)"/"eleven" are stale by one | Change `(11)` → `(12)` |
| — | — | — | — | 5-of-6 ClickHouse alerts without runbooks — see **Operator D-F1/D-F2** above; same finding as this review's F5/F6 | — |
| C-F7 | HIGH | `docs/README.md:37` | "27 ADRs" | 30 ADRs; `docs/adr/README.md:3` (linked from the same page) already says "Thirty" | Change the count |
| C-F8 | HIGH | `docs/README.md:41` | "12 incident procedures" | 14; the same file says "14 runbooks" for v1 two lines later | Change the count |
| C-F9 | HIGH | `docs/images/grafana-pipeline-overview.jpg`, `prefect-deployments.jpg` | `docs/audits/2026-08-26-doc-accuracy.md:60`: "Replaced with real embeds of the three `.jpg` files" | Only 1 of 3 is embedded anywhere in current docs — 2 are orphan assets | Embed or delete them; append (never rewrite) the prior audit's resolution trail |
| C-F10 | MED | `observability.md:71` | Docs table: dashboard name "ClickHouse Overview (v2)" | Live dashboard title is "ClickHouse Overview (gold)" — the source was renamed, the doc table wasn't | Match the docs table to the live title |
| C-F11/C-F12 | LOW | `docs/plans/2026-08-26-.../000-...md`, `001-...md`; `docs/operations/README.md` | (implicit) these pages are reachable | Plan phase files are named as bare text in a table cell — zero inbound hyperlinks; `operations/README.md` is never linked explicitly, only reached via directory convention | Turn the phase-table cells into links; add an explicit Reference-table link |
| C-F13 | LOW | *(no page exists)* | (implicit) naming is discoverable in one place | No single page carries topic/namespace/table/container/make-target naming together — 27 files touch topic names alone. Not urgent: each concept has one authoritative home already | A one-paragraph glossary stub in `docs/README.md`'s Reference table, linking out |

### Live UI — a browser walkthrough of all 8 UIs

**Story verdict:** four of eight browser UIs were broken outright at review time —
Prefect, Redpanda Console's Avro decoding, Lakekeeper, and lake compaction itself via a
real missing-sort-order DDL bug — and the incident that made the lake six days stale was
recovered exactly per runbook, but the only place that explanation appeared was a Prefect
UI nobody could load.

| ID | Sev | Location | Claim | Reality | Fix |
|---|---|---|---|---|---|
| F-01 | BLOCKER | `scripts/dev-up.sh:42-47`; `docker-compose.yml` (`PREFECT_UI_API_URL`); `README.md:142`, `quick-reference.md:33`, `setup.md:166`, `prefect-schedules.md` (×3), `benchmarks/2026-02-19-v2-baseline.md:191` | Prefect is at `localhost:4200` | `localhost:4200` → nothing listening; the real published port is 14200, undocumented in all 7 places above. At 14200 the UI itself shows "Unable to connect to Prefect server" and an empty deployments list, even though 2 deployments exist and are `READY` via the API. Same finding as Newcomer A3/A4 | Set `PREFECT_UI_API_URL=http://localhost:14200/api`; fix all 7 doc references |
| F-02 | BLOCKER | `docker-compose.yml` (Redpanda Console env vars) | (implicit) Redpanda Console decodes the Avro-contract messages it shows | Every message renders "There were issues deserializing the value"; Schema Registry page shows "No data found" — caused by `KAFKA_`-prefixed env vars the pinned `console:v3.5.1` image doesn't read (it wants the unprefixed names) | Drop the `KAFKA_` prefix on the two schema-registry env vars |
| F-03 | BLOCKER | `docker/lake/ddl/lake.sql:1201`; `docker/lake/maintenance.py:121` | (implicit) `lake-maintenance-daily` compacts every gold table | Fails on every run: `IllegalArgumentException: Cannot sort data … table 'lake.gold.bbo_1s' is unsorted` — it's the only gold table missing a `WRITE … ORDERED BY`; compaction never completes for any table after it in the loop | Add the missing `ORDERED BY` clause to `gold.bbo_1s` |
| F-04 | BLOCKER | *(incident, not a doc bug)* | (the story's premise) "the lake is healthy after waiting ten minutes" | Lake was 141.7h stale — every 5-minute ingest failed `failOnDataLoss` after the 2026-08-28→09-03 downtime (see incident note below) | This is the alert working as designed; the UX failure is that the explanation only appeared inside a Prefect UI that could not load (F-01) |
| F-05 | BLOCKER | `docker-compose.yml:623` (`LAKEKEEPER__BASE_URI`) | (implicit) Lakekeeper's UI is browsable | `/ui/` redirects to "server-offline" — the SPA fetches `http://lakekeeper:8181`, a Docker-internal hostname the browser cannot resolve; the API is fine from the host | Set `LAKEKEEPER__BASE_URI=http://localhost:18181` |
| F-06 | HIGH | Grafana `/api/org/preferences` → `{}`, `/api/folders` → `[]` | (implicit) Grafana has a clear entry point | No home dashboard, no folders; a first login lands on the stock welcome page; the overview dashboard has zero lake and zero Prefect panels | Set the home-dashboard env var; add a lake/Prefect stat row |
| F-07 | HIGH | `docker/lake/metrics.py:55` (`TABLES` dict); K2 Lake dashboard | (implicit) the K2 Lake dashboard shows the lake | Shows 14-15 of the lake's 26 actual tables — the SCD2 security master (#128) and the crashing `gold.bbo_1s` (F-03) are both invisible; a missing table looks identical to a healthy one on this dashboard | Derive `TABLES` from the catalog listing instead of hardcoding it |
| — | — | — | — | Pull-fed ClickHouse tables empty — see **Quant B13** above; same finding as this review's F-09 | — |
| F-10 | HIGH | `docker-compose.yml` (8888 mapping); `Makefile:89` | (implicit) port 8888 belongs to K2 | Serves the vendor's unmodified `tabulario/spark-iceberg` sample notebooks, unauthenticated — none of K2's own notebooks (those are on 8889 via `make notebooks`) | Stop publishing 8888, or label it "vendor samples, not K2" |
| F-11 | MED | all 4 dashboard JSONs, `links: []` | (implicit) an operator can click through from a panel to its runbook | Zero dashboard-level or panel-level links exist anywhere | Add a `links:` array per dashboard |
| F-12 | MED | Grafana dashboard tags; `default.yml` provisioning folder | (implicit) dashboard identity reflects the current architecture | `ClickHouse Overview` still tagged `v2` while capture/lake dashboards say `v3`; provisioning folder says `K2 Platform v2` and never takes effect (`foldersFromFilesStructure: true` overrides it) | Retag; delete the dead folder line |
| F-13 | MED | `capture-alerts.yml:257-269`; `k2-l2-capture.json` panel description | Panel text: ingress latency "includes internet path + clock skew, **not an SLO**" | `CaptureIngressLatencyHigh` pages on exactly that metric at >2s for 10m — pending live for binance at 4.718s during the review | Reword one of the two so they agree |
| F-14 | MED | Lakekeeper `/management/v1/warehouse` → `k2`; `lake.sql` uses `lake.<ns>.<table>` everywhere | (implicit) the catalog/warehouse name is consistent | Lakekeeper's warehouse is named `k2`; every doc/query calls the catalog `lake`; `?warehouse=lake` returns `NoSuchWarehouseException` | Document the mapping (now on the catalog page); rename the warehouse at the next fresh install |
| F-15/F-17 | LOW | all 8 UIs; Spark Master; `observability.md`, `11-observability.md` | (implicit) the UIs present as one coherent K2 platform | Every UI is an untouched vendor default (Redpanda's own marketing feed, MinIO/ClickHouse/Spark stock landing pages), none mentions K2, none links to another; Spark Master shows "0 applications ever" because lake jobs run as local drivers and never register with it; neither observability doc mentions Redpanda Console or the Prefect UI as part of the toolkit | No new service — cross-link the existing UIs from Grafana; one-line notes for Spark Master and the two docs |
| F-16 | MED | `CLAUDE.md` preamble | "The ClickHouse medallion is … readable history" | On this host the `k2` database doesn't exist at all — lost in the 2026-08-28 Docker wipe — `UNKNOWN_DATABASE`, not readable | Drop the "readable history" clause |

---

## Incident recorded during this audit

Between the 2026-08-28 and 2026-09-03 stack restarts the host was down long enough that
committed Redpanda offsets fell off the 48-hour raw-topic retention window. Every
5-minute `lake-ingest` run then correctly refused with `failOnDataLoss: 31 partition(s)
are below broker retention and 401534 records are permanently gone`, per
`docs/runbooks/lake-ingest-lag.md §3`. The stack was resumed with `--accept-data-loss` at
approximately 2026-09-03 13:00 UTC; the gap is recorded in `lake.audit.checks` as
`offset_gap` rows, as designed. This is the alert and the runbook working correctly — the
UX finding this audit surfaced is that the only place the failure text (partition count,
record count, and the runbook section to follow) appeared was inside a Prefect flow-run
log, in a UI that could not be reached from a browser (**F-01**, **F-04**).

---

## Resolutions

Appended after publication; the table above is never edited.

- **Resolved in `91e9d6c` (#130):** B1, B5 — SCD2's first version now opens at EPOCH
  instead of at the run; live dims migrated; chapters 02/09 and the notebooks README
  corrected.
- **Resolved in `9932f42` (#131):** A1, A2, A3, A4, A7, A10, A11, A20, A21, A25, F-01,
  F-02, F-05, F-08, F-10, F-14 (stated mapping), F-16, and the `dev-up` health-probe bug
  (jemalloc line; the probe PASSed on an empty listing).
- **Resolved in `9f868b1` (#132):** B6 (rule now in `trades()`'s docstring), B11
  (`completeness()`), B12, B14, B16, B20.
- **Resolved in `84f5ca2`** (#133, data catalog): B2 (stated lake-only),
  B3 (stated, lake wins), B7, B8, B9, B10, B13, B17, B18, B19, B21, B22 (= A17, A24 ⊆
  B17), C-F1, C-F2, C-F7, C-F8, F-09 (= B13).
- **Resolved in `fc601a8`** (#134, gates): B4, F-03, F-07, C-F4,
  C-F5, C-F6 (= D-F1/D-F2), D-F1, D-F2, D-F6, D-F7, E-F1, E-F2, E-F3, E-F4, E-F5, and the
  README/testing count mismatches (A13, A14, A19).

### Open

No fix in flight. Each item carries a concrete revisit trigger.

- Grafana dashboard completeness — home dashboard, lake/gold-freshness/Prefect panels on
  the overview, dashboard cross-links, audit-gauge panels, folder/tag naming, dead-metric
  panels (F-06, F-11, F-12, D-F3, D-F4, D-F8, D-F9, D-F10, D-F11, D-F12, D-F13, D-F14,
  C-F10) — trigger: the next operator on-call handover, or the first missed lake
  incident.
- `tests/parity/pinned.json` still pins the wiped day 2026-08-27, so `make parity-ohlcv`
  now reports "nothing to compare" instead of crashing (#134); a `--pin-current`
  run on 2026-09-03 found 134 of 1,825 buckets differing because ClickHouse consumed
  trades live during the outage that the lake lost past raw retention — trigger: the
  first full UTC day with both sides intact (2026-09-04), then
  `scripts/parity-ohlcv.sh --pin-current <day>` and commit the pin.
- Recreating `prefect-server` while a flow run is in flight leaves that run `RUNNING`
  forever in the API with no process behind it (observed 2026-09-03: `lake-maintenance`
  run `polite-leopard`, started 13:14, server recreated ~13:15, no `maintenance.py` in
  `k2-spark-iceberg` or `k2-prefect-worker`), and `lake-maintenance-daily`'s
  `concurrency_limit=1` then parks every later run in `AwaitingConcurrencySlot` — the
  nightly job is silently blocked until someone runs `prefect flow-run cancel <id>`.
  No alert covers a run that never finishes; `LakeAuditFailed` reads a summary the
  blocked run never writes — trigger: the next `prefect-server` recreate, then add a
  "flow run older than 2 h" rule and a line in `docs/runbooks/lake-ingest-lag.md §2`.
- ClickHouse `book_top20` construction vs lake (B3, documented not unified) — trigger:
  any research number quoted from ClickHouse book views.
- `CaptureIngressLatencyHigh` vs the "not an SLO" panel text (F-13) — trigger: the next
  false page.
- Lakekeeper warehouse `k2` vs catalog `lake` (F-14) — trigger: the next fresh install,
  rename the warehouse then.
- Every UI a vendor default with no cross-links, including Spark Master's "0
  applications" framing and both observability docs never naming Redpanda Console or the
  Prefect UI (F-15, F-17, D-F15) — trigger: the next external demo.
- The stale screenshot (C-F3) and orphan images (C-F9) — trigger: the next screenshot
  refresh.
- MinIO 1 GiB limit under concurrent ingest + host reads (noted by the helpers worker:
  `IOException: Failed to read connection error`, MinIO at 867 MiB of 1 GiB) — trigger: a
  second occurrence, then raise via ADR-010 Outcome.
- `make health` reads a lock-refused ingest run (`exited 2`, another writer holds the
  flock) as FAIL — trigger: first time it misleads someone.
- Shell-portability papercuts in the copy-pasted quick start (A5 unnecessary bash-only
  `set -a`, A6 `CH=` fails in zsh) — trigger: the next reported copy-paste failure from a
  zsh/macOS reader.
- README's "live data" and "five minutes" claims (A8 `rpk` mojibake with no Avro note,
  A9 measured ~14m vs claimed 5m) — trigger: the next README quick-start edit.
- No copy-pasteable DuckDB query outside a notebook (A12; PR #133's data-catalog page
  added Python/`k2lake` examples but they still require the `notebooks/` checkout) —
  trigger: the next `quick-reference.md` edit.
- Docker memory prerequisite disagreement, 28 GB vs 24 GB (A15) — trigger: the next
  Docker Desktop VM sizing doc edit.
- `redpanda-init`'s stale "still creates" v2-topics parenthetical (A16) — trigger: the
  next `docker/redpanda/init.sh` edit.
- Runbook index intro's stale "v2 stack" framing and four-vs-five capture-runbook count
  (A18) — trigger: the next `docs/runbooks/README.md` intro edit.
- Repository-layout block under-lists `schemas/avro/` and `config/` (A23) — trigger: the
  next file added to either directory.
- `quant` ClickHouse user documented but never used in any example (B15) — trigger: the
  next `data-inspection.md`/`quick-reference.md` edit touching the quant user.
- Plan-phase files and `operations/README.md` reachable only by convention, not by link
  (C-F11, C-F12) — trigger: the next `docs/README.md` or `docs/plans/.../README.md` edit
  that touches its link list.
- No naming/glossary page (C-F13, not urgent per its own review) — trigger: the next
  time a naming question needs a cross-file grep to answer.
- Runbook template has no dashboard/panel field (D-F5) — trigger: the next `/runbook`
  invocation.
- Fourth-exchange checklist gaps beyond `gold.py`/`metrics.py`: the hardcoded Grafana
  panel, the hardcoded `parity_ohlcv.py`/`pinned.json` venue set, and `mod.rs`'s "not
  this year" comment reading stale against the two docs that invite it (E-F6, E-F8) —
  trigger: the next fourth-exchange onboarding attempt.

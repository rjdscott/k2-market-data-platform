# V2 changelog: findings on ARCHITECTURE-V2.md and what became of them

2026-11-26. Inputs: three diagram checkers and two adversarial reviewers, 96 findings. The source JSON reused the
ids F1 to F14 and F1 to F15 for two different checkers, so they are prefixed here: **DA-** is the checker of diagrams
1 to 4, **DB-** the checker of diagrams 5 to 8, **D9 to D11-** the checker of diagrams 9 to 11; **C, H, M, L** are
reviewer 1; **V2-nn** is reviewer 2. "Accept" means the document changed as asked; "partly" means the problem was
fixed but not in the way proposed, with the reason; nothing was rejected outright. Section numbers refer to the
corrected `ARCHITECTURE-V2.md` (1,200 lines; all 13 Mermaid blocks parse with mermaid 11.4.1).

**Totals.** Critical 3: 1 accepted, 2 partly. High 24: 22 accepted, 2 partly. Medium 45: 36 accepted, 9 partly.
Low 24: 20 accepted, 4 partly. Rejected: none.

**Evidence I re-checked in the repository before acting on critical and high findings:** `notebooks/k2lake.py:10-13,
33-34,107` (host ports, MinIO root key); `docker-compose.yml:885,893` (Lakekeeper published, audit trace off),
`:290-291` (MinIO published), `:94,99-100` (secrets in the shared Airflow environment, names only), `:642` (Docker
socket), `:91-92,665` (LocalExecutor, 4 slots, 1.5 CPU / 3 GiB); `docker/airflow/dags/dbt_marts.py:96,144`
(`append_env`); `docs/adr/ADR-047-ingest-batch-audit-after-publish.md:45`; `docker/redpanda/init.sh:145-146`;
`docker/lake/offsets.py:215-230`; `platform/k2gen/k2gen/generate.py:389-390,410-416`; `scripts/dbt_manifest_check.py:49-54`;
`scripts/lake-verify.sh:7,64-65,83`; `docker/lake/maintenance.py:1-25`; `docker/lake/lock.py:1-8`;
`docker/airflow/dags/lake.py:175-196`; `docker/trino/etc/jvm.config` (`-Xmx2816M`); `LEDGER.md` (MinIO default
credential). Read-only probes on the running stack: `supports_versioning = False` in
`airflow/dag_processing/bundles/local.py` of `k2-airflow:3.3.2`; the `trinodb/trino:484` plugin directory holds
`kafka-event-listener`, `http-event-listener` and `openlineage`; the Airflow image carries `openlineage-python` 1.53.0
as a cosmos dependency. Web, 2026-11-26: the ODPS v1.1.0 schema (no licence field; `authoritativeDefinitions` exists)
and its releases page (v1.0.0 2025-09-24, v1.1.0 2026-09-08). The vendored ODCS schema: no licence field; relationship
`to` accepts the host-less fully qualified form and rejects a URL with host and port (regex tested).

## (a) Every finding

| Id | Sev | Where | Problem | Decision | What changed, or why not as proposed |
|---|---|---|---|---|---|
| DA-F1 | high | D4 rollback message | diagram said readers keep the old snapshot; text said direct readers see bad rows briefly | accept | message now "readers see the previous snapshot again"; guide states the test-window exposure, and that other products and REST read audited snapshots only (C1); new decision O8 |
| DA-F2 | high | D2 guide | step numbers off by one, no step 12 | accept | diagram redrawn (13 messages); guide numbered 1-2, 3, 4-7, 8-9, 10-13, counted after autonumber |
| DA-F3 | high | D1 | storage registered in Lakekeeper; release and table catalog not connected to the product catalog | accept | `ING & TRF -.-> LK` (register, commit); release cylinder added, feeding runs, catalog routes and Trino rules; governance cylinder feeds the catalog through Trino |
| DA-F4 | med | D1 PC to users | "request access" drawn from catalog to users; no request origin | partly | label now "pages: products, owners, state, access in words"; requests are drawn in diagram 2, and the guide says so, instead of adding request arrows to diagram 1, which shows what flows |
| DA-F5 | med | D1 nodes | k2-api looked like two services; Trino split | accept | both nodes labelled "k2-api (one service)"; TRF "dbt-core models, their SQL run on Trino" |
| DA-F6 | med | D4 start | no source, no fetch; release R undefined | accept | participant "Source, a topic, API, file or FTP server"; step 3 reads or fetches; step 1 resolves release R |
| DA-F7 | med | D4 outcomes | run record only on success; no saved snapshot id | accept | step 9 records each output's snapshot id; step 16 writes a run record on every outcome |
| DA-F8 | med | D2, D4 vs legend | dashed meant reply, legend said metadata; no colours | accept | legend gains the sequence-diagram rule; coloured `box` groups in both; rules hand-over drawn solid |
| DA-F9 | med | D2 steps 3-5 | design time and run time mixed; owner deploys; key source missing | accept | participants split into Definitions and Platform deploy; two labelled `rect` blocks; step 7 issues the API key; owner only approves |
| DA-F10 | med | D3 guide | "left, middle, right" positions Mermaid will not produce | accept | `flowchart TB`, inner `direction` lines removed, guide names boxes by title |
| DA-F11 | med | D3 vs text | previous release, CI trial compile, release target, git target, shared documents missing | accept | PREV feeds the validation runner; runner does trial compile and diff; REL says "deployed in diagram 9"; EXP "for audit only"; DOC lists shared roles and principals |
| DA-F12 | low | D3 PR, TAG | human steps coloured as running; unlabelled arrows | accept | PR and TAG orange; every arrow labelled; guide says arrows move documents or start a check |
| DA-F13 | low | D1-D4 terms | "catalog" had three meanings; undefined words | accept | terms table: three catalogs, table procedure, CODEOWNERS, audit branch; D1 box "product catalog and table catalog"; D2 "read-only catalog fx_reference" |
| DA-F14 | low | D4 DB | implied dbt rolls back natively | accept | participant "dbt build and its wrapper"; message "wrapper rolls each failed table back" |
| DB-F1 | med | D5, 4.3 | proposed both stored and never stored | accept | proposed is a submission, not a `status` value; removed from the K2 values; drawn "in no release" |
| DB-F2 | med | D5 | no paused to deprecated | accept | `paused --> deprecated` added; checks allow it |
| DB-F3 | med | D6 requests | requests drawn dashed (metadata) | accept | requests solid with labels; legend: solid carries data or a request for data |
| DB-F4 | med | D6 identity | hid that Trino trusts the user name | accept | arrow "user name not yet authenticated (O3)"; guide says so |
| DB-F5 | med | D6 k2-api | no source for the key-to-principal map | accept | `RUL -.->|key hashes to principals| API`; release node names the key map |
| DB-F6 | low | D6 TST | test had no output or labels | accept | node "any difference stops the deploy"; inputs labelled |
| DB-F7 | low | D6 RUL, order | release drawn as a rectangle; run time last | accept | RUL is a green cylinder; `flowchart TB` with run time first |
| DB-F8 | med | D7 | security master's read of the currency master not drawn | accept | `CUR -->|ISO 4217 codes, to classify fiat| SM` |
| DB-F9 | med | D7 | no domain boundaries | accept | three domain subgraphs |
| DB-F10 | low | D7 labels | unlabelled arrows; point in time not named | accept | "canonical trades"; "as of trade time", guide points to 4.11 |
| DB-F11 | med | D7 OTH | two venues and two product kinds in one node; cycle | accept | separate trades and instruments nodes and an outside source node for the two venues; no cycle |
| DB-F12 | low | D7 VBK | paused product coloured as running | accept | new legend class "declared, paused" (dashed border), used in D7 and D10 |
| DB-F13 | med | D8 CAL | calendars drawn as a hand-written file | accept | calendars drawn as a product (blue), seed mentioned in node and guide |
| DB-F14 | med | D8 CUR to SLV | dashed, wrong direction, inconsistent with CAL | accept | solid `CUR -->|currency codes, read by silver's generated relationships test| SLV`; guide says a miss fails the build and rolls back; both reference inputs drawn the same way |
| DB-F15 | med | D8 | no product boundaries; terms undefined | partly | subgraphs for ecb-fx-rates and fx-crosses; currency-master and calendars share one "reference products" box, each its own node, to stay readable; ISO arrow labelled; TARGET in terms; gold label in plain words |
| D9-1 | med | D9 | only the happy path; no end node | accept | STOP node on any failed check; DONE node "R is live" |
| D9-2 | med | D9 | all arrows dashed, deploy time unlabelled | accept | whole diagram in a "Deploy time" box; legend says dashed arrows in D9 are steps in order |
| D9-3 | low | D9 vs D3 TAG | same thing in two colours | accept | orange in both (approved by a person) |
| D9-4 | low | D9 PREV | origin of R-1 unclear; rollback actor unclear; data not mentioned | accept | PREV "what the pointers named before, kept"; rollback is automatic in `make deploy`, then fails loudly; guide: code and rules, never data |
| D9-5 | med | 4.9 title | "backup" in title, not drawn | accept | heading "Deploy and rollback"; backup moved to its own sub-heading |
| D9-6 | med | D9 PRE, SVC | storage plan and additive DDL undefined; five actions in one box | accept | storage plan defined in the guide; DDL node "new tables and new nullable columns only"; SVC split into Trino pointer and Airflow, Spark, Prometheus pointers |
| D10-1 | high | D10 T2 to T3 | store to store with no job; T3 contained T2 | accept | today drawn as one storage node written by `lake_ingest` ("every layer") |
| D10-2 | high | D10 T5, G5 | ClickHouse floated with no feed | accept | outside topics node feeds T1, T5 and G1; G5 paused with no arrow, as the guide says |
| D10-3 | med | D10 box arrow | arrow meant "is replaced by" | accept | arrow deleted; method stated in the guide |
| D10-4 | med | D10 | sides not parallel | accept | five aligned rows per side: ingest, storage, transform, serve, hot tier |
| D10-5 | med | D10 G4 | "catalog at the centre" as an end node; no Lakekeeper today | partly | Lakekeeper named in both storage rows; G4 is the serve row (product catalog, read-only catalogs); "at the centre" dropped and the guide points to diagram 1, rather than adding two more nodes |
| D10-6 | low | D10 guide | garbled sentence; undefined terms | accept | rewritten; table procedure defined in the terms table |
| D11-1 | high | D11, 5.3 | instrument drawn straight to currency master; asset hop missing | accept | asset node and declared chain; 5.3 SQL joins instrument to asset to ECB quote; asset added to inputs |
| D11-2 | med | D11 | design and run time mixed; "every key resolves" ambiguous | accept | "Run time" and "Design and release time" boxes; checks label says what the release checks; build-time tests are on the input products |
| D11-3 | med | D11 ASOF | degraded path and the two time bases not shown | accept | ASOF label "instrument by valid time, rate by knowledge time, no rate gives rate_missing"; guide covers stablecoins and `partial` |
| D11-4 | low | D11 PCT | catalog fed only from the derived product | partly | catalog fed from the release's declared keys and ports; guide says it already knows each input from diagram 1, instead of adding arrows from every input |
| D11-5 | low | D11 | newcomer lost; naming drift | accept | guide starts with the goal and says the currency master is not read; `candles-eur` is the product id, `candles_eur` the namespace (5.3) |
| C1 | crit | 4.4, 5.x | derived products read inputs' current, untested snapshots; killed builds leave untested snapshots | accept | audited snapshot per output in the run record; `k2_input()` reads inputs `FOR VERSION AS OF` it; REST reads it; reconciliation rollback before each build and in `on_kill`; N2 chaos exit |
| C2 | crit | 4.1, 4.4, 4.6 | notebooks, Lakekeeper and MinIO bypass Trino; fetch isolation claim false | partly | trusted-path table in 4.6; N1 unpublishes MinIO and Lakekeeper, moves notebooks to Trino, rotates the root key, adds IPv6 ranges and negative probes; Lakekeeper authentication in O3 (c). Not taken: the forward proxy, a new service for one operator; the fetcher instead connects only to addresses resolved from a declared `allowHosts` list, with a stated trigger to add a proxy |
| H1 | high | 4.3, 4.4 | dbt runs in the scheduler with its secrets; `depends_on` misses undeclared reads | partly | one-shot `dbt-runner` with an allow-listed environment; per-product writer `dbt_<product>` selecting only its inputs; lint of `env_var`, `run_query` and relations. Not taken: a separate CI parser of compiled SQL, because Trino under the release's rules in CI denies the same reads with the same rules as production (one enforcement point) |
| H2 | high | 4.4, D4 | strict WAP on the Kafka landing contradicts Accepted ADR-047; auto-skip on resume | accept | landing commits first and audits after; bronze is the WAP step; resume after a pause writes `offset_gap`; below log start an active product fails loudly and needs a per-product accept-data-loss run |
| H3 | high | 4.9 | pinning fails in Airflow 3.3.2; one link for all; every grant restarts Trino; release contents | accept | first task passes the release id by XCom; per-service pointers in order; rules refreshed by link swap, restart only for catalogs, with a drain; release holds code, DAG factory, macros, k2-api, compose |
| H4 | high | 4.7, 5.1 | one unknown symbol stops the venue; first version from fetch time | accept | held table `silver_trade_unresolved`, loud audit without rollback, on-demand instruments run; first version at 1970-01-01 (ADR-030) |
| H5 | high | 4.9, N2, N4 | drill and exits depend on old-layout commands; no rebuild; restore points at missing files | accept | `make product-verify`, `make rebuild`, generated `make restore` with `purgeRequested=false`; exits use product-verify |
| H6 | high | 4.4 | no table maintenance designed | accept | daily per-product maintenance after its own build, expiry windows, freshness alerts skip it, N2 delete-file exit |
| H7 | high | N0, 4.4 | N0 measures nothing that grows or contends | accept | N0 on seeded days of synthetic trades, three chains contending, 20+ ticks with p50, p90, n; tests scoped to new rows; `incremental_predicates`; dbt off the scheduler |
| H8 | high | whole doc | REQUIREMENTS-V2 E missing; no usage | accept | new 4.12 with diagram 12, metadata table, standards table, AI readiness; usage product from Trino's Kafka listener in N6; retirement reads usage |
| H9 | high | D1, D2, D4 | odd flows: storage to Lakekeeper, unnamed metadata store, success-only records, k2-api reads, request access, keys | accept | D1 redrawn with release and governance cylinders and writers committing to Lakekeeper; k2-api reads governance through Trino as `svc.catalog` and never Lakekeeper; D4 records every outcome; D2 issues keys and labels the SQL name as unauthenticated |
| H10 | high | D3, D9, 6 | seam contradicts itself | accept | one design: the compiler's only input is an approved document set with provenance, from a tag in 3.0 or a registry version later; D3 and D9 both start from it; CI diffs against the last tag |
| H11 | high | 4.2, 4.4 | time travel serves rolled-back snapshots; `$` tables | accept | a follow-up Spark task expires rolled-back snapshots by id; `as_of` resolves to audited snapshots only; exact table names deny `$` tables; N1 and N7 exits test both |
| M1 | med | D4, 5.1 | landing and bronze as one step; late bronze skipped by the watermark | partly | two steps; bronze decodes forward from the last landing snapshot and publishes in offset order per partition; N2 fixture. Not taken: moving silver off the offset watermark, since in-order publishing already makes it safe |
| M2 | med | 4.1, 4.4 | registered outputs in `k2` cannot be read by consumers; no policy check | accept | option (b): transforms write their domain warehouse through a Spark catalog; outputs as strict as any input; N4 consumer SELECT exit |
| M3 | med | 4.5 | no run-time hold; history at merge time | accept | `make hold` and `make unhold` with a governance row; history from deployed manifests |
| M4 | med | 4.2, O3 | F5 closed only for honest callers; O3 had no default | accept | 4.2 says "for honest callers"; O3 (a) marked plan, consequences name writer impersonation, option (c) added |
| M5 | med | 4.4, 6 | fetcher not counted by the budget; shared spool | accept | fetcher is a compose service under a `fetch` profile; one spool directory per run; sha256 checked at load |
| M6 | med | D7, D8, D10, D11 | "related to" arrows, mixed times, wrong colours, cycle | accept | no reference arrows (keys are labels on data arrows); labelled time boxes; products as products; cycle split; box arrow removed |
| L1 | low | 4.2 | k2-api interprets access itself | accept | caller's access asked of Trino as the caller (`LIMIT 0`); worded masks checked by `make access-verify` |
| L2 | low | N1 ADRs | ADR-044 naming change and ADR-047 not superseded | partly | N1 supersedes ADR-044's catalog, writer and warehouse layout. Not needed: an ADR-047 supersession, because H2 now keeps ADR-047 as decided |
| L3 | low | 4.4, 4.7 | volatile responses land every hour; type-2 scan grows | accept | `volatileFields` excluded from the hash; type-2 built incrementally from the last version |
| L4 | low | 5.1, 5.3 | opposite validity intervals | accept | one half-open convention, ADR-030's; both examples use `k2_as_of` |
| L5 | low | D5 | paused straight to retired; no withdrawal exit | accept | retirement only through deprecation; `proposed --> [*]: withdrawn or rejected` |
| V2-01 | crit | 4.3, 4.6 | undeclared reads invisible to `depends_on`; writers read everything | partly | writers select only their input ports (generated rules, no catch-all); lint of three-part names, `run_query`, `env_var`; failing fixtures in N1. Not taken: `EXPLAIN (TYPE IO)` in CI, because the CI build under the release's rules already fails on the same reads with the production enforcement point |
| V2-02 | high | 4.4 | dbt `env_var` can publish scheduler secrets | accept | one-shot runner, allow-listed environment, no socket; N1 exit checks `docker inspect` and an `env_var` fixture |
| V2-03 | high | 4.4, D9 | rollback lost when Trino or the build dies; no drain | partly | durable pre-run record and reconciliation; N1 chaos script stops Trino mid-build. Drain happens only when Trino restarts or DAGs change shape, not on every deploy, because rule-only releases no longer restart Trino (H3) |
| V2-04 | high | 4.6, D2, O1a | roles are platform-wide, not per product | accept | grant = (principal, product, role) in the product's `grants.yaml`, approved by its owner; one group per (product, role); O1a restated |
| V2-05 | high | whole doc | section E missing; licence, usage, standards | accept | 4.12; licence via ODPS `authoritativeDefinitions`, K2 `terms` with the reason; standards table; usage; AI readiness after 3.0 |
| V2-06 | high | D3, D9, 6 | seam not real: deploy, CI checks, history, code | accept | document set and provenance record; validation runner used by CI and the registry; history from manifests; code through git in both states |
| V2-07 | high | D1 | release missing; k2-api split; request arrow | accept | as DA-F3, DA-F5, DA-F4 |
| V2-08 | high | D11, 5.3 | join on coincident names; asset not an input | accept | as D11-1 |
| V2-09 | med | 4.7, 4.11, 5.x | interval rules implicit; instrument lacks validity columns | accept | half-open intervals, sentinel, first version at 1970-01-01 per key stated in 4.11; `instrument` has validity columns; N2 exit for a trade before the first fetch |
| V2-10 | med | 4.7 | one symbol rolls back the venue; one venue's listing blocks the master | partly | held rows; a spelling claimed twice is held for that venue only; on-demand instruments run. Not taken: moving `venue_symbol` into each venue's product, because REQUIREMENTS-V2 B.4 puts the venue mapping in the security master |
| V2-11 | med | 4.1, D7 | event-bars unreadable by consumers | accept | as M2 option (b); N4 exit reads it through `market_analytics` |
| V2-12 | med | N0 | Ubuntu 22.04's Python 3.10 remains | accept | N0 prints Python and OpenSSL; April 2027 trigger in N10; untrusted bytes parsed in the Python 3.12 fetch container |
| V2-13 | med | legend, several | vocabulary inconsistent | partly | legend, `rect` blocks, labelled time boxes, split OTH, no D10 arrow, D2 "search after O1b" all done. Not taken: drawing every reference from referring column to referenced product, because rule C wants arrows to follow what flows; references became labelled data arrows (M6) |
| V2-14 | med | 4.4, 5.x | market logic in the core | partly | generic `envelope` and `filter`; `wire.py` framing split (N2); `calendar` names a table id; parsers and domain macros under domain folders; `realtime_reader` renamed `full_reader`. Kept: `delayed_reader`, since a delay policy applies to any time series |
| V2-15 | med | 4.6 | expectations cover only what someone remembered | accept | coverage rule in the checks; N1 exit fixture |
| V2-16 | med | 4.2, D4 | time travel to rolled-back snapshots | partly | k2-api accepts only audited snapshots, stricter than `is_current_ancestor`; rolled-back snapshots expired within minutes and recorded in the run record, instead of a page note that they stay reachable until expiry |
| V2-17 | med | 6 | existing products, ledger, `serves` unmapped | accept | table of all 13 products and every ledger item by phase; `serves` port property; paused `hot-gold` product in N0 |
| V2-18 | med | whole doc | not self-contained; undefined terms; long lines | partly | terms table extended; ClickHouse inventory items inlined in N0; prose wrapped under 122 characters. Kept: tournament references, each now with its reason inline, and table rows over 120 characters, since a Markdown table row cannot wrap |
| V2-19 | med | N0 | one run cannot give p90; no decision step | accept | 20+ ticks, p50 and p90 with n, decision recorded before N2 |
| V2-20 | low | 4.3 | ODPS maturity and `paused` deviation unstated | accept | maturity line with release dates and fallback; `paused` declared as a deviation |
| V2-21 | low | several | versions unpinned | partly | FastAPI 0.162.5, uvicorn 0.54.0, `trino` 0.340.0, Alertmanager v0.35.0 named (REVIEW's check). Python and Debian bases are pinned by digest in the release manifest, without a patch number here, because I did not verify the current patch |
| V2-22 | low | 6 | diagram 9 in no chapter; runbooks missing for N7, N9 | accept | diagram 9 in chapter 21; key runbook in N7; converge runbook in N9 |
| V2-23 | low | 4.11, 5.1 | private reference notation; two id schemes | partly | ODCS fully qualified form, served by k2-api, one scheme including the domain. Not taken: a URL prefix with host, because the vendored pattern rejects a host with a port (tested) |
| V2-24 | low | 4.7 | master read other products' landings | accept | master reads the instruments products' published silver through input ports |
| V2-25 | low | 4.3 | no local dbt project for editing | accept | `make dbt-dev` |
| V2-26 | low | 4.4, 5.2 | database and lake-table kinds, continuous mode, SFTP example | accept | reserved connector rows; one sentence on continuous mode; short SFTP CSV example in 5.2 |

## (b) Every flow redrawn since the judge's diagrams

"Judge" rows are flows in `VERDICT.md`'s six diagrams; "v2 draft" rows are flows in the first v2 draft that the
checkers flagged. The new flow is in the corrected document's diagram named.

| From | Old flow | New flow | Why |
|---|---|---|---|
| Judge 1 | `k2gen -.-> connectors & pages & Trino & API` | one release cylinder: `REL -.-> ING & TRF` (pinned per run), `REL -.-> catalog routes`, `REL -.-> Trino` (rules); how it is made is diagram 3, how it is deployed diagram 9 | owner A.2: generator arrows to everything hid what is configuration, deploy and run |
| Judge 1 | `lake & mart --- Lakekeeper` (no direction) | `ING & TRF -.->|register tables, commit snapshots| LK`; `LK -.->|resolves table names| TR` | rule C: no "related to" arrows; engines commit, storage is passive |
| Judge 1 | `lake --> mart` | `STO -->|inputs at audited snapshots| TRF -->|silver, gold, marts| STO` | a store does not feed a store; the job does |
| Judge 1 | `API --> TR`, catalog a side box of pages | `TR -->|rows, as the caller| API`; catalog box at the centre holding Lakekeeper and the k2-api catalog routes; `PC -.->|pages| USR` | owner A.3, A.4: catalog at the centre; arrows follow what flows |
| Judge 1 | `topics & public sources --> one connectors box` | `SRC --> ING` (each product's own connector); diagram 7: each topic and API reaches exactly one product | owner A.1 |
| Judge 2 | `[*] --> active: merged`, `paused --> retired` | `proposed` (a submission, in no release) with a withdrawn exit; `paused --> deprecated --> retired`; retirement needs no reader and no query for 30 days | readers of a paused product get notice; proposed is not a stored state |
| Judge 3 | `registered transform -.-> config transforms` | registered code runs in place of dbt for its product and writes its domain warehouse (diagram 4 text, 4.4) | no transform vocabulary remains (B.1) |
| Judge 3 | `bronze & silver & gold -.- runner (WAP every write)` | landing committed first, audit after (ADR-047); bronze by WAP; dbt tests then rollback; run record every outcome (diagram 4, a sequence) | H2: blocking the archive loses data; B.1 chose dbt test and rollback |
| Judge 4 | `CI --> generated files committed`, `merge --> make apply`, `generated files --> runs and queries` | diagram 3 ends in an immutable release; diagram 9 deploys it with preflight, pointers, verify and automatic rollback; nothing generated is committed | owner A.2; REVIEW F2 |
| Judge 5 | `k2gen --> permissions.json --> REST API` | `RUL -.->|key hashes to principals| API` only; Trino is the one decision point | REVIEW F23: a second rule interpreter can disagree |
| Judge 5 | `contract access, global roles --> one rule per consumer and table` | grants (principal, product, role) compile into one group per (product, role) and rules per table | addendum 3 M; V2-04 |
| Judge 5 | `Trino --> tables` | `TBL -->|rows| TR`; requests solid with the identity caveat; access test gates the deploy | direction of data; DB-F3, DB-F4 |
| Judge 6 | `raw_archive --> every venue product` | each venue's topic feeds only its trades product; each venue's API feeds only its instruments product | owner A.1 |
| Judge 6 | `kraken_instruments --> security_master (registered scd2)` | every venue's listings and the currency master feed the security master (dbt, type-2); it gives instrument ids back to each trades product | owner B.4 |
| Judge 6 | `raw_archive -.-> paused book products`; `TODAY -.-> TARGET` | venue-books drawn paused with no arrow; no box-to-box arrow, the method is in the guide | a paused product consumes nothing; no "replaced by" arrows |
| v2 draft 1 | `STO -.->|every table and snapshot| LK` | `ING & TRF -.->|register tables, commit snapshots| LK` | DA-F3, H9 |
| v2 draft 1 | `ING & TRF -.->|runs, publishes| product catalog` | `ING & TRF -.-> GOV` (governance tables); `GOV -.->|read through Trino| PC` | H9: name the store; k2-api owns no metadata |
| v2 draft 1 | `PC -.->|find, read, request access| USR` | `PC -.->|pages| USR`; access is asked of the owner in diagram 2 | DA-F4 |
| v2 draft 2 | `O ->> D: approve, release, deploy`; `D -->> T: rules on restart` | `O ->> G: approve grant`; `G ->> P: release R`; `P ->> T: rules at next refresh`; `P ->> S: API key` | DA-F9, H3, H9 |
| v2 draft 3 | `RDB -.->|approved documents| CMP` beside `TAG -.-> CMP` | both stores feed one approved document set; the set is the compiler's only input | H10, V2-06 |
| v2 draft 4 | `CN ->> LB: write landing and bronze to the audit branch` | landing committed first; bronze decoded onto the audit branch; source participant added | H2, M1, DA-F6 |
| v2 draft 4 | `AF ->> CT: run record` on success only | `AF ->> GV: run record on every outcome` | DA-F7, H9 |
| v2 draft 6 | `SQLC -.->|principal| TR`, `RSTC -.->|API key| API`, `API -.->|query| TR` | solid request arrows, "user name not yet authenticated (O3)" | DB-F3, DB-F4 |
| v2 draft 7 | `OTH -->|listings| SM` and `SM --> OTH` | separate other-venue trades and instruments nodes; listings in, ids out to trades only | DB-F11, M6 |
| v2 draft 8 | `CUR -.->|every quote must resolve| SLV`; `ISO --> CUR` unlabelled | `CUR -->|codes, read by silver's relationships test| SLV`; `ISO -->|https-file connector| CUR` | DB-F14, DB-F15 |
| v2 draft 9 | one path ending only in rollback | STOP on failed preflight; drain, DDL, Trino pointer, other pointers, verify; DONE or automatic rollback to R-1 | D9-1, D9-6, H3 |
| v2 draft 10 | `T2 -->|stages| T3`; ClickHouse unfed; `TODAY -.-> TARGET` | `TOP --> T1 -->|every layer| T2`; `TOP --> T5`; no box arrow | D10-1, D10-2, D10-3 |
| v2 draft 11 | `CND -.->|refers to| SMI`, `SMI -.-> CMS`, `FXR -.-> CMS`, `CHK -.-> CEU` | data arrows into the model labelled with the keys used; asset node; `CMS --> SMA & FXR` read by tests; checks gate the build from a design box | D11-1, D11-2, M6, V2-08 |

## (c) Where reviewers disagreed, and how I resolved it

- **How to catch undeclared reads** (H1: parse compiled SQL in CI; V2-01: `EXPLAIN (TYPE IO)` on a throwaway Trino).
  Neither: each product's writer may select only its input ports, and CI builds each changed product on a throwaway
  Trino under the release's rules, so the enforcement in CI is the enforcement in production (E7). A lint stays as an
  early warning.
- **Rolled-back snapshots** (H11: expire them; V2-16: let k2-api accept only `is_current_ancestor` snapshots and tell
  readers SQL time travel reaches them until expiry). Both, tightened: k2-api accepts only audited snapshots, and a
  follow-up task expires rolled-back ones within minutes.
- **Usage events** (H8: Trino's Kafka event listener into Redpanda; V2-05 cited the HTTP listener). Kafka: the
  platform's own Kafka connector lands it as a governance product; HTTP would need a receiving service, and k2-api must
  not write.
- **Registered-transform outputs** (M2 offered a reader catalog over `k2` or domain warehouses; V2-11 chose domain
  warehouses). Domain warehouses: consumers see one naming scheme, and storage follows domain, not engine.
- **Reference arrows** (V2-13: from referring column to referenced product; DB-F14: direction should follow the data;
  M6: show keys as labels on the data arrow). M6 with DB-F14: no arrow means "refers to"; the referenced product's
  codes flow into the model or test that reads them.
- **Request arrows** (DB-F3: requests solid; the old legend: solid means data). The legend now says a solid arrow
  carries data or a request for data; diagram 1 shows only what flows and leaves requests to diagram 2 (DA-F4).
- **Rules refresh** (REVIEW F2, accepted in B.2: "do not add `refresh-period`"; H3: add it for rule-only releases).
  H3: F2's failure was a partly written file in a working tree; the rules file now changes only by an atomic link
  swap after preflight. N0 spikes it; if it fails, every grant falls back to a drained Trino restart.
- **Drain** (V2-03: drain on every deploy; H3: drain before Trino restarts). Drain when Trino restarts or DAGs change
  shape; rule-only releases skip it.
- **Venue mapping** (V2-10: each venue's instruments product publishes its own `venue_symbol`; H4 and B.4: the security
  master holds it). B.4 wins; per-venue isolation is done by holding rows per venue inside the master.
- **Fetch egress** (C2: an internal network with a pinned forward proxy; criterion 2: few moving parts). No proxy in
  3.0: the fetcher connects only to resolved addresses of declared hosts; the proxy waits for the first source whose
  hosts cannot be listed.

## (d) What remains uncertain

- dbt-trino `merge` with `incremental_predicates` and `rollback_to_snapshot` through Lakekeeper 0.13.9; Spark
  `expire_snapshots` by id on a Trino-written table and how quickly Trino stops serving it; Trino's rules refresh
  through a link swap under load. All are N0 spikes; none was run here.
- Airflow pinning through XCom when a DAG file changes shape between tasks; covered by the drain, proved by N1 chaos.
- The per-run cost of a one-shot container plus dbt start for about twenty products, and Trino as build engine beside
  interactive users. N0 measures; the escapes are in 4.4.
- The host-gateway reachability is the reviewer's probe, not re-run by me; the allow-list does not depend on it.
- Trino Kafka event-listener volume, and OpenLineage's Kafka transport for `dbt-ol` and the Airflow provider on 3.3.2.
- Whether interactive SQL readers may see a build's rows for its test window: B.1 accepts it; O8 offers an `audited`
  tag that would need a ref update per run through Lakekeeper (unverified).
- The ECB and ISO 4217 terms of use (redistribution, attribution) are recorded in N1; I have not read them.
- Binance and Coinbase instrument endpoint field names; the Model Context Protocol's steward and version.
- About 70 days, a judgement, up from 62, re-estimated after N1.

**New for the owner to decide:** O8 (above). **Changed:** O1a now asks one role per principal *per product*, since
grants are per product; O3 gains option (c), OpenID Connect for Trino, k2-api and Lakekeeper, and states that under
(a) a local client can claim a writer's name. **Consequence to know:** from N1 notebooks query through Trino as a
named principal, because MinIO and Lakekeeper stop being published to the host.

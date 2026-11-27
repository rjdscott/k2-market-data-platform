> **Retained design input, dated 2026-11-26 (UTC):** the independent review of the proposal's section 1 (decision O8) that ADR-061's Context cites as B1 to B5, copied unchanged but for session-only paths and the notes marked superseded. It is a record, not the authority.
> Where it differs from `docs/development/phase-001-interfaces.md`, `platform/k2gen/INTERFACES.md` or an Accepted ADR, they win; in particular no credential hash is ever committed (the orchestrator's ruling on Q4).

# O8 review: proposal section 1 (2026-11-27 UTC)

**Method.** Three experiments in a throwaway Trino 484 container, `spike-o8rev-h`: file rules, the file group provider, a writer catalog `bld` and a read-only reader catalog `ro`, over the empty `reference_data` warehouse in Lakekeeper 0.13.9. Scripts and output were in session scratch, not retained. Nothing was left behind.

**Verdict.** Build (a), with four changes. In the proposal's ownership shape, product code defeats O8 with one hook. Q4 is unsafe in every design, tables included.

## 1. Does (a) enforce O8?

**What held:**
- **Every base path is refused to a reader:** the base through either catalog, `'audited'` read on the base, its `$snapshots`, `$files` and `$refs`, and `table_changes`.
- **Views refuse time travel by number and by timestamp.** A *string* version is silently ignored (`'zzz'` returned the tag's rows): no leak, but it misleads, so A9 tests a numeric id.
- **Writer B cannot serve A's rows through its own view.** With `SELECT` but no `GRANT_SELECT` on A's base, B's definer view is created, but reading it fails ("View owner 'dbt_b' cannot create view that selects from …"). `GRANT_SELECT` is the boundary.
- **`SET AUTHORIZATION` is refused:** with no authorization rules, the file rules deny it (trino.io/docs/484/security/file-system-access-control.html).
- **Materialized views do not exist here:** "not supported for Iceberg REST catalog".
- **The tag survives a contracted `CREATE OR REPLACE`:** 51 polls at about 50 ms all returned `1,2`, so the 0.3 s empty window is invisible.
- **A dropped column has a safe order:** tag first errors ("Column 'amount' cannot be resolved"); view first, then tag, does not.
- **Policies on the view hold.** A group rule `region <> 'restricted' AND ts < current_timestamp - INTERVAL '20' MINUTE` with a mask returned `1:open:**** 2:open:****`.
- **No query or file cache is configured.**

**What broke:**
- **B1, high: the proposal's owner.** In the proposal, `dbt_<fence>` owns `<fence>` (proposal :40, :208), so any model hook can `CREATE OR REPLACE VIEW` without the tag. Doing exactly that, the reader saw `4:open:s-4`, the untested row. **Fix:** a publisher `pub_<fence>` owns `<fence>`, and the builder only `__base`. Verified: the builder is refused with "Cannot create view".
- **B2, critical, every design (Q4).** Lakekeeper stores a view's owner as a plain property, `trino.run-as-owner`, that any REST client can set.
  - **Forged view:** I REST-POSTed a view into B's namespace naming `pub_a`, and another naming `platform`, reading A's `main`. Both returned 200; the reader got `[1..5]` with the raw secrets.
  - **Grafted snapshot:** `add-snapshot` plus `set-snapshot-ref` grafted A's untested snapshot into a table in B. It returned 200, and the same rows were read.
  - **Who can do this today:** third-party images on `k2-net` (grafana, redpanda-console, cadvisor, prometheus, statsd), and the host through port 18181. Q4's trigger has fired.
- **B3, high, every design.** `SHOW STATS` on the view and on a table showed 2 rows but `id` high 5, `amount` high 50, and the `ts` of the row the delay hides. `EXPLAIN ANALYZE` prints `Input: 4 rows`.
  - **Fix, partly tested:** `iceberg.table-statistics-enabled=false` on the reader catalog, with the view naming the base through that catalog. Result: no bounds, and the masks and B's refusal still hold.
  - **Gap:** a reader's `SET SESSION ro.statistics_enabled = true` brought the bounds back. Readers must be denied that property; the deny rule is untested.
- **B4, medium, by design.** `dbt_b`'s CTAS from A's base `main` copied the untested row. For products, "tested" rests on `k2_input` and a lint. O8 names interactive SQL readers (DECISIONS-FINAL:31), but ADR-056:192-195 says "other products read at the marker", so ADR-061 must say so.
- **B5, low.** `information_schema.views` shows readers the view's SQL, though `SHOW CREATE VIEW` is refused.

## 2. Policies in view SQL or in the rules file

Possible, since inside a definer view `current_user` and `current_groups()` return the reader. Don't:
- View SQL is readable by readers (B5).
- With one role per principal per product, the policies turn into `CASE` trees, a second policy engine.
- `access-verify` already exercises the rules file.

Keep policies as rules on the view's exact name. A policy change is then a rules-only deploy, live within 5 s, and a view changes only when columns do. REST impersonates the caller and hits the same rule, so there is still one enforcement point.

## 3. Alternatives

- **b1, build on a branch:** dead. The 484 docs list branch reads only; the connector reports "does not support creating branches" (spikes:482), though the engine has had branch grammar since 477 (trinodb/trino#25152).
- **Metadata-only publish to a second table:** possible, which refutes proposal :30, since the graft worked. Rejected anyway:
  - The two tables share files, so expiring either deletes the other's.
  - Field ids must be mirrored by hand: after a replace they were 6 and 5.
  - No engine supports it. `register` returns 409 (spikes:497), and drop plus register is not atomic.
- **b2, copy into a reader table.**
  - **What it would keep:** time travel, `as_of`, structurally tested products, and no amendment.
  - **Why not:** it rewrites every output on every build, and `silver_trade` (plan 004) runs on a five-minute chain. A partition overwrite in Spark adds 6.5 s per publish in the `lake_spark` pool of 1, plus logic to compute touched partitions. Too costly.
- **Invoker view with the base denied:** refused (`Cannot select from table …__base.t`; spikes:469). Dead.
- **Row filter on `$file_modified_time`:** dead. MERGE deletes still apply at `main`, compaction resets file times, and each build would need a release.
- **(e) eventual visibility:** free, and rejected by the owner.

## 4. Running (a), as amended

- **Per product:** 2 namespaces, 2 principals, 1 tag per output, one multi-table commit (13 ms), and a view sync only when columns change.
- **Owner's PR review:** models and the contract; the compiled diff shows each view and its rules.
- **Failures at 3 am:**
  - A 409 on the tag commit exits 3 and pages.
  - A stale view makes reads fail with an error, never wrong data, and the reconcile repairs it.
  - With no tag yet, the product has no reader name.
- **Restore drill:** the Lakekeeper dump carries views, owners and tags; after a restore, run the drift check, then `access-verify`.
- **REST `as_of` (Q2):** it makes k2-api decide which ids were tested, against ADR-056:243 ("never decides access"). Owner's choice in `007`: cut `as_of` where a policy applies, or accept that rule and give it an access-verify row.

## 5. Conditions

1. **`pub_<fence>`:** owns `<fence>` in `_build` (views only) and has `SELECT, GRANT_SELECT` on its own `__base.*` through the reader catalog.
   - **Builders and `svc.platform`:** no `GRANT_SELECT`.
   - **Product views:** `view_security: invoker`.
   - **The rules file:** no `authorization` rules.
2. **Generated view SQL:** `SELECT <contract columns> FROM <domain>.<fence>__base.<t> FOR VERSION AS OF 'audited'`, with no predicate.
3. **Reader catalogs:** statistics off, with `statistics_enabled` allowed only for `dbt_.*` and `svc.*`.
4. **View sync:** C ∩ old ∩ new, then the tag commit, then C ∩ new. C is the contract's columns; old and new are the snapshot schemas. Run it idempotently at reconcile; the wrapper holds pub's secret and dbt never sees it.
5. **Catalog fence:**
   - **Network:** Lakekeeper on its own network with Trino, Spark, the one-shots and its database, and off the host port (ADR-056:260).
   - **Drift check:** at `release-verify` and at each reconcile, reader-namespace listings, view SQL and `trino.run-as-owner` must equal the release.

**Gate:** `spine-h-tested-data-view/run.sh`, non-zero on any leak.
- **h1, fence matrix.** Inputs: the condition rules, two products, three readers. Steps: the run-1 matrix, plus a builder replacing the view, a foreign definer view, `SET AUTHORIZATION`, `SHOW STATS` and `SET SESSION`. Pass: every base path is refused, only the tag's rows come back with policies applied, and no bounds appear.
- **h2, polled cycle.** Two outputs: a pass with a multi-table commit, a failing test, an added column, a dropped column, and a kill between the commit and the view sync, then a reconcile. Pass: no 50 ms poll sees an untested or empty result, there are no errors under condition 4, and commit times are recorded with n=20.
- **h3, catalog fence.** The forged view and the graft fail to connect from `k2-net`. From inside the catalog network they succeed, and the drift check must then exit non-zero.

**ADR-061 text** (ADR-056 gets only an Outcome line pointing here):

> "A dbt output is read only through one definer view, `<domain>.<fence>.<table>`, compiled as `SELECT <contract columns> FROM <domain>.<fence>__base.<table> FOR VERSION AS OF 'audited'` and nothing else. It is owned by `pub_<fence>`, the only principal granted `GRANT_SELECT` on that product's base. Every reader policy is a rule on the view's exact name, and no reader rule names a base. No other view or materialized view may exist where readers can select. Owner transfer is denied. The catalog's API is reachable only from Trino, Spark and the platform's one-shots, because it stores a view's owner as a property any client can set. Products are held to tested inputs by `k2_input` and a lint, not by Trino."

**What changes in the proposal:**
- **Section 1:** the owner, the view SQL through the reader catalog, B3, and Q4 reversed.
- **A7 step (6):** condition 4.
- **A8:** `pub_<fence>`, builders without `GRANT_SELECT`, statistics.
- **A9:** rows for statistics, `SET SESSION`, the foreign view, the builder's view, and the drift check.
- **A10:** `pub_` principals.
- **A12:** `as_of`.
- **Steps:** S01 adds h3; S12 adds conditions 1 and 3; S15 adds statistics; S20 adds the drift check; S22 adds the network and its probe. ADR-059 records the network.

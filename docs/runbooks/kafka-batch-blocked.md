# Runbook: A Kafka product's batch failed its audit and blocks bronze

Use this when a Kafka product's `audit` failed a check, or its `load` was refused because a failed
batch waits on bronze's `audit` branch. A failed batch is never reset by code: it blocks the product's
bronze until an operator decides between bad data and a wrong check and clears it. The landing keeps
committing meanwhile, so no record is lost while bronze waits. A batch that was only interrupted
(never audited, or passed and never published) is not this case: the next run restages it by itself.
Why: [ADR-063](../adr/ADR-063-kafka-product-landing-per-product-bronze-staged-by-offset.md) part 4.
The file products' version of this page is [file-source-audit-failed.md](./file-source-audit-failed.md),
whose `lake_sql` helper ("Reading the lake: who may, and how") every command here uses.

> **Phase 004, live as `v3.0.0-alpha.6-rc.1` since 2026-11-28; nothing here has run on the live
> stack.** A corrupt frame failing `parses`, `main` unmoved, is proven on a throwaway lake (`bash
> scripts/verify/kafka-path.sh <release dir> a`, claim 9, group a `10 PASS, 0 FAIL` on 2026-11-28).
> Sections 1 and 2 were induced end to end on `kraken-trades` on a throwaway lake on 2026-11-28 by `bash
> scripts/chaos/bronze-failed-then-repaired.sh <release dir>` (`bronze-failed: 4 PASS, 0 FAIL in 316
> s`): a frame of a schema id not yet registered fails `parses`, the next run lands its records and is
> refused naming the failed batch, bronze unmoved; the schema registered and the clearing statement run
> as the Spark key, the next run restages both frames, publishes, and silver takes both trades once. It
> clears through its own Spark helper (`scripts/lib/run_path_spark.py clear`), not this page's `lake_sql`,
> and runs no Airflow. Claim a11 (section 3) was added by a later review fix; no PASS line of its run is
> recorded. Every message quoted is the code's own text.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** The example is
`market-capture/kraken-trades`: DAG `kraken_trades`, landing `lake.kraken_trades.landing`, bronze
`lake.kraken_trades.bronze_trade`, checks `lake.kraken_trades.audit_checks`. A batch's id is
`<dag>/<run>`.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | `audit` failed a check | < 30 min to a decision | induced on a throwaway lake, 2026-11-28: `bash scripts/chaos/bronze-failed-then-repaired.sh <release dir>`, `bronze-failed: 4 PASS, 0 FAIL in 316 s` for the script, the chain's first cycle included; the decision is the operator's and is not timed; live: not yet |
| 2 | Clear the blocked batch | < 15 min after section 1 | the same script's claim 4, not timed apart; live: not yet |
| 3 | An interrupted batch (no action) | the next run | not yet measured on a stack |

---

## 1. The audit failed

**Symptom**, the run's `audit` task fails with exit 3 and `publish` does not move `main`. Its log
prints one line per check, `pass` or `FAIL`, then:

```text
audit: batch kraken_trades/<run> at audit snapshot <id>: FAILED <checks>; main not moved
```

Every later run lands its records, then its `load` fails with exit 2 at the stage:

```text
load refused: audit of lake.kraken_trades.bronze_trade holds batch kraken_trades/<run> at <id>, unpublished: its newest audit, at <time>, failed [<checks>] and lacks nothing. This batch waits behind it (its records are landed); once the cause is fixed an operator clears it as the Spark key (docs/runbooks/file-source-audit-failed.md, step 5): ALTER TABLE lake.kraken_trades.bronze_trade REPLACE BRANCH audit AS OF VERSION <main snapshot id>
```

When bronze has no `main` yet (the product's first batch failed), the statement it names is `ALTER
TABLE lake.kraken_trades.bronze_trade DROP BRANCH audit`. The same refusal says `no Kafka stage wrote
it` when the head of `audit` is not a Kafka stage's commit, which only a hand-made write explains.

**Detection**, `ProductRunFailed_kraken_trades` (the release's `compiled/prometheus/product-alerts.yml`)
and `AirflowTaskFailed` with `task_id="audit"`, then with `task_id="load"`; later
`ProductStale_kraken_trades`.

**Expected behaviour**, bronze, silver and everything built on them stay at their last tested
snapshot; the landing keeps growing, every run's records committed, so clearing the batch loses
nothing: the next stage takes every landing row past bronze's through-offset.

**Recovery**, read the failed checks, not yet run:

```bash
lake_sql "SELECT batch_id, check_name, passed, detail, checked_at FROM lake.kraken_trades.audit_checks
  WHERE table_name = 'lake.kraken_trades.bronze_trade' ORDER BY checked_at DESC LIMIT 12"
lake_sql "SELECT name, snapshot_id FROM lake.kraken_trades.bronze_trade.refs"
```

| Check | Fails when | Most likely |
|-------|-----------|-------------|
| `accounting` | the landing does not hold every offset of the range once (but those an accepted loss records), staged plus skipped is not that count, or the commit added rows outside its range | a defect in the connector or a hand-made write: stop and investigate, do not clear |
| `parses` | a staged frame is not Confluent-framed, its schema id is not one the registry serves as `RawMessage`, its body does not read, its writer schema lacks a bronze column or has a type that does not read as it, or bronze's row is not what the frame decodes to; the detail names partition, offset and column, never a byte of the frame | a capture writing a new envelope shape, or the registry answering 404 for an id at stage time |
| `keys_unique` | a `(kafka_partition, kafka_offset)` repeats in the batch, or a key of its range is on `main` already | a defect: stop and investigate |

Decide, as in [file-source-audit-failed.md](./file-source-audit-failed.md) section 1 step 4:

- **The data is bad** (a frame that truly does not decode): the batch is discarded by section 2 and
  its range is staged again by the next run, which fails the same way, since the landing holds the
  same bytes. A frame that will never decode needs a fix in the product's documents or the capture;
  that is a change under review, never a clearing step.
- **The check or the code was wrong**, or the cause has passed (the registry serves the id again):
  fix it through a release if it is code, then clear the batch (section 2); the next run restages the
  range and audits it again.

**Measured**, induced on a throwaway lake, 2026-11-28 (`bronze-failed-then-repaired.sh` claim 4):
`parses` failed naming the unregistered id with exit 3, bronze `main` and silver unmoved; the next run's
stage refused with exit 2 naming the failed batch while its records landed. Not timed apart; live: not
yet.

---

## 2. Clear the blocked batch

**When**, the cause is fixed.

**Expected behaviour**, the clearing statement is the one the refusal printed, run as the Spark key.
It resets `audit` to `main` and discards the failed batch's snapshot; its `audit_checks` rows stay as
the record. Nothing is lost: the next run stages the whole range from bronze's through-offset on
`main`, with whatever landed since.

**Recovery**, not yet run:

```bash
docker exec k2-airflow-scheduler airflow dags pause kraken_trades -y
lake_sql "SELECT name, snapshot_id FROM lake.kraken_trades.bronze_trade.refs"
lake_sql "ALTER TABLE lake.kraken_trades.bronze_trade REPLACE BRANCH audit AS OF VERSION <main snapshot id>"
lake_sql "SELECT name, snapshot_id FROM lake.kraken_trades.bronze_trade.refs"      # audit = main
docker exec k2-airflow-scheduler airflow dags unpause kraken_trades -y
docker exec k2-airflow-scheduler airflow dags trigger kraken_trades
```

If the product is held ([hold-a-product.md](./hold-a-product.md)), leave the DAG paused and release
the hold instead of unpausing. The next run prints `load: landed <n> records in
lake.kraken_trades.landing`, then `load: <rows> rows staged on audit of lake.kraken_trades.bronze_trade
from lake.kraken_trades.landing snapshot <id>, <skipped> skipped, snapshot <id>`, an audit ending
`passed`, and `publish: main <old> -> <new>, batch kraken_trades/<run>`.

**Measured**, on a throwaway lake, 2026-11-28 (`bronze-failed-then-repaired.sh` claim 4): the clearing
statement reset `audit` to `main` as the Spark key, and the next run restaged both batches' frames,
passed every check, published, and built silver with both trades once. Not timed apart; no Airflow, so
the pause, unpause and trigger above have not run; live: not yet.

---

## 3. An interrupted batch heals itself

**Symptom**, a run was killed (its timeout, a restart of the Spark container, a deploy) between
staging and publishing. Its batch sits on `audit`, never audited, or audited and passed but never
published.

**Detection**, the killed run's `ProductRunFailed`; no alert after it.

**Expected behaviour**, no action. The next run's `load` prints:

```text
load: audit of lake.kraken_trades.bronze_trade holds batch kraken_trades/<run> at <id>, never audited: a Kafka batch is its offsets, so it is discarded and its range staged again by this run, with what landed since
```

(or `audited and passed, never published`), then stages, audits and publishes that range with its own
as one batch. It is restaged rather than adopted, so a publish is always licensed by the run's own
audit.

**Measured**, not yet measured on a stack; claim a11 of `kafka-path.sh` group a is the proof, its run
pending.

---

## Failure modes / incidents

None yet: nothing here has run against a stack.

---

**Last verified:** sections 1 and 2 end to end on `kraken-trades` on a throwaway lake on 2026-11-28
(`bash scripts/chaos/bronze-failed-then-repaired.sh <release dir>`, `bronze-failed: 4 PASS, 0 FAIL in 316
s`), and section 1's `parses` failure with `main` unmoved the same day (`kafka-path.sh` group a, claim
9). No PASS line recorded: claim a11 (section 3). Not yet on the live stack: every command in sections 1
and 2.

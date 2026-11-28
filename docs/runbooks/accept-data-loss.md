# Runbook: Accept data loss on a Kafka product

Use this when a Kafka product's `load` fails because the records it would read next were deleted by
the broker before it read them: the committed offset fell below the partition's log start. Nothing
moves the product past the hole on its own; an operator's accept-data-loss run gives the range up,
records it, and resumes at the log start. It cannot recover the records: the venues do not replay. Why
the product fails loudly instead of skipping:
[ADR-063](../adr/ADR-063-kafka-product-landing-per-product-bronze-staged-by-offset.md) part 5 and
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md). The conf shape is frozen in
[`phase-001-interfaces.md`](../development/phase-001-interfaces.md), "Accepting data loss".

> **From the release that carries phase 004 (`v3.0.0-alpha.6`, not yet tagged or deployed).** The
> failure and the accept-data-loss load are proven on a throwaway lake by `bash
> scripts/verify/kafka-path.sh <release dir> b` (group b, `5 PASS, 0 FAIL` on 2026-11-28; its claim
> b6, a topic trimmed before the first run, was added since and its run is pending), which runs
> `connector.py load --accept-data-loss` directly. **The Airflow trigger with its conf, the
> `offset_gap` run record it writes and the catalog page's list have not run on any stack**; the
> factory's conf rule and the record are unit-tested (`docker/airflow/tests/test_factory.py`). Every
> message quoted is the code's own text.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a`
loaded.** The example is `market-capture/kraken-trades`: DAG `kraken_trades`, landing
`lake.kraken_trades.landing`, bronze `lake.kraken_trades.bronze_trade`.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Recognise the failure and decide | < 15 min | not yet measured |
| 2 | The accept-data-loss run | < 10 min | not yet measured on a stack; plan 004's `scripts/chaos/kafka-below-log-start.sh` will induce it |
| 3 | Check what was recorded | < 5 min | not yet run |

---

## 1. Below the log start

**Symptom**, every run of the product fails at `load`, and nothing lands or stages. The task log ends
with exit 2 and, from `docker/lake/kafka.py` (`plan`):

```text
load refused: below the log start: market.crypto.v3.raw.kraken/<p> committed <offset>, log start <log start>, <n> records gone. Nothing landed; an accept-data-loss run records the gap and resumes those partitions at the log start
```

Every such partition is named, the entries `; ` separated, with its committed offset, the broker's log start and
the count of records gone.

**Detection**, `ProductRunFailed_kraken_trades` (`compiled/prometheus/product-alerts.yml` in the
release) and `AirflowTaskFailed` with `task_id="load"`; after the stale window,
`ProductStale_kraken_trades`.

**Expected behaviour**, nothing heals this: a scheduled run is created with no conf and fails the same
way every five minutes. It happens when the product did not read for longer than the topic keeps its
records (48 hours or 2 GiB per partition, `docker/redpanda/init.sh`, whichever comes first on the
busiest partition): the product was held or paused too long, its runs failed for that long, or a
backlog grew faster than `maxOffsetsPerPartition` drains it. A paused product that is resumed after
its point left the log is the same case.

**Recovery**, decide first. The records are gone from the broker whatever you do; the choice is only
whether the product moves on. Before you accept:

1. Find why the product fell behind, and fix that first, or the next gap follows (a held product:
   [hold-a-product.md](./hold-a-product.md); failed runs: [airflow-scheduling.md](./airflow-scheduling.md)
   section 2).
2. Write the reason down: it is stored verbatim, at most 200 characters, and shown on the catalog page.
3. If the capture itself was down for the same window, that is a hole in time the offsets cannot see;
   record it as the capture runbooks say ([capture-sequence-gaps.md](./capture-sequence-gaps.md)).

**Measured**, not yet measured.

---

## 2. The accept-data-loss run

**When**, the cause is fixed and the owner accepts the loss.

**Expected behaviour**, a **manually triggered** run of the product's own DAG with exactly this conf:

```json
{"acceptDataLoss": true, "reason": "<why the loss is accepted, at most 200 characters>"}
```

`resolve` checks the conf before anything is read and refuses (the run fails there) on any other key,
a flag that is not the JSON `true`, a flag without a reason or a reason without the flag, a reason
over 200 characters, a product whose connector is not `kafka`, or a run that is not manual. The
refusals, from `docker/airflow/dags/factory.py` (`data_loss_reason`), all start `conf refused:`; for
example `conf refused: only a manually triggered run accepts data loss, this run is 'scheduled'`. Only
`load` gets `--accept-data-loss`; the reason never reaches the connector.

`load` then plans the partitions below the log start from the log start, prints one line per
partition to the task log:

```text
load: ACCEPTED DATA LOSS market.crypto.v3.raw.kraken/<p>: offsets <committed>..<log start - 1>, <n> records, resuming at <log start>
```

and lands and stages as a normal run does. A partition that was not below the log start is
unaffected. A run whose plan finds nothing below the log start is a normal run and records no gap.

**Recovery**, not yet run on a stack:

```bash
docker exec k2-airflow-scheduler airflow dags trigger kraken_trades \
  --conf '{"acceptDataLoss": true, "reason": "<why>"}'
docker exec k2-airflow-scheduler airflow dags list-runs kraken_trades
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run kraken_trades <run_id>
```

Every task `success` (`maintain` and `compact` may be `skipped`). A scheduled run that started before
the trigger fails again at `load`; the manual one waits behind it (one run at a time).

**Measured**, not yet measured.

---

## 3. What was recorded, and where it shows

**Expected behaviour**, the loss is recorded in three places, none of which expires it:

- **The landing's summary.** The accept-data-loss run's landing commit carries `k2.kafka-gaps`,
  `{topic: {partition: [[committed, log start], ...]}}`, and so does every landing commit after it:
  the key is cumulative, every loss accepted so far merged where ranges meet, so expiring the commit
  that accepted one never drops its record. The audit's `accounting` check excludes those offsets,
  and `maintain`'s whole-table continuity check reads them too.
- **The run record.** One row, task `load`, event `offset_gap`, `table_name` the landing, `detail` the
  JSON `{"partitions": [{"partition", "fromOffset", "toOffset"}, ...], "reason": "<yours>"}`, written
  by `record` when load's result shows the landing commit carrying the gap was made. A load that dies
  after that commit and before printing its result leaves the gap in the summary alone; nothing yet
  reconciles the two.
- **The catalog page.** `/catalog/market-capture/kraken-trades/index.html` lists, under "Offset gaps
  accepted", each `offset_gap` row newest first: its time, `partition <p> offsets <from> to <to>` per
  partition, and the reason. The page is regenerated after each deploy, by `make site`, and daily.

**Recovery**, not yet run. `lake_sql` is the Spark helper of
[file-source-audit-failed.md](./file-source-audit-failed.md) ("Reading the lake"); `trino_cli` is
[tls-and-passwords.md](./tls-and-passwords.md)'s:

```bash
lake_sql "SELECT committed_at, summary['k2.kafka-gaps'] AS gaps, summary['k2.kafka-offsets'] AS ends
  FROM lake.kraken_trades.landing.snapshots ORDER BY committed_at DESC LIMIT 3"
trino_cli svc.catalog "SELECT recorded_at, detail FROM governance.run_records.run_record
  WHERE product_id = 'market-capture.kraken-trades' AND event = 'offset_gap' ORDER BY recorded_at DESC"
make site
curl -s http://127.0.0.1:${K2_API_PORT:-8095}/catalog/market-capture/kraken-trades/index.html | grep -A3 'Offset gaps accepted'
```

**Measured**, not yet run.

---

## Failure modes / incidents

None yet: nothing here has run against a stack.

---

**Last verified:** section 1's refusal and section 2's load on a throwaway lake on 2026-11-28 (`bash
scripts/verify/kafka-path.sh <release dir> b`, group b `5 PASS, 0 FAIL`, the connector run directly,
not through Airflow; claim b6 pending). Not yet: the Airflow trigger and its conf refusals on a stack,
the `offset_gap` run record, the catalog page's list, every command in sections 2 and 3.

# Runbook: A lake audit failed

The nightly maintenance run ends in **24 checks of six kinds** over the lake, and any
failure exits non-zero, fails the Prefect run and raises an alert.
`docker/lake/maintenance.py`'s `AUDITS` tuple is the list: `offset_continuity` on
`raw.messages`; `duplicate_identifiers` on each of `bronze.trades`,
`bronze.book_snapshots_l2` and the six per-venue bronze tables; `venue_replay` on
`bronze.trades`; `sequence_gaps` on each unified bronze table; and, per venue table,
`bronze_unparseable` and `bronze_schema_drift` (§6, §7). They ask different questions,
have different causes, and some are **findings to record** rather than faults to repair , 
telling them apart is what this runbook is for.

`venue_replay` is the odd one out and it is here so that it cannot be mistaken for a
failure: it is **informational**, has no pass/fail semantics, and is excluded from the
`N audits passed` summary line (`INFORMATIONAL` in `maintenance.py`). It publishes the
Coinbase replay rate that §2 would otherwise be blamed for, see §4. Stage 2 of the ingest
also files rows into the same table under `job='ingest'`, see §5.

It does **not** cover ingest being behind ([lake-ingest-lag.md](./lake-ingest-lag.md)) or
the tier being down ([lake-recovery.md](./lake-recovery.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified, the Phase D burn-in fills this in.** The audits exist in
> `docker/lake/maintenance.py` and every tuple and check name below is read from it, but
> nothing here has been run against a populated `raw`/`bronze`/`audit`. Commands marked ✅
> were run read-only against the running stack on 2026-08-26.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | `offset_continuity`, a hole in the archive | **investigation, not repair** | occurred 2026-08-26, one recorded hole, 1,168,954 records, netted out by the check since 2026-08-27; see §1 |
| 2 | `duplicate_identifiers`, a row landed twice | < 60 min | not yet verified, Phase D burn-in |
| 3 | `sequence_gaps`, venue sequence discontinuity | **investigation, not repair** | not yet verified, Phase D burn-in |
| 4 | `venue_replay`, informational; **cannot fail** | n/a, read the rate, do not repair it | not yet verified, Phase D burn-in |
| 5 | `unresolvable_schema_id`, filed by the ingest, not by the audit | < 60 min | not yet verified, Phase D burn-in |
| 6 | `bronze_unparseable`, a venue frame did not parse as the declared shape | < 1 day (a schema change + a table rebuild) | pass verified 2026-08-27 (0 rows on all six tables after the 61.9 M-row rebuild); a failure not yet induced |
| 7 | `bronze_schema_drift`, the venue sends a key the table does not declare | < 1 day (same) | pass and **fail** verified 2026-08-27: 23/23 audits green in 290 s; with `M`,`m` removed from `binance_trade`'s declared keys the check failed with `$.data: ['M', 'm']` |
| 8 | `bronze_parity`, filed by the ingest: frames in ≠ rows out + control frames | < 60 min | not yet verified, Phase E |
| 9 | `silver_parity`, silver rows ≠ trades in the bronze snapshot silver last read | < 60 min (rebuild the venue) | not yet verified, Phase E |
| 10 | `silver_flags`, informational; **cannot fail** | n/a, read the rates | not yet verified, Phase E |
| 11 | `gold_parity`, gold rows per venue ≠ silver first deliveries | < 60 min (rebuild gold) | not yet verified, Phase E |
| 12 | `ohlcv_parity`, a stored 1m candle ≠ recomputed from gold.trades | < 60 min (rebuild gold) | not yet verified, Phase E |
| 13 | `kraken_checksum`, a replayed Kraken book failed the venue's CRC32 | **investigation**, a missed or misapplied frame | **failed as designed** 2026-08-27: 386,962 of 40.2 M frames, all inside the 2026-08-26 chaos window; every other hour zero. See `docker/lake/README.md` § Books |
| 14 | `bars_parity`, a stored bar ≠ recomputed from gold.trades | < 60 min (rebuild bars) | not yet verified |

---

## Start here: what failed, and when did it last pass?

`lake.audit.checks` is append-only, one row per check per run, so the question "when did
this last hold" is a query rather than a log grep. That is the whole reason it is a table.

**Detection**, `LakeAuditFailed` from `docker/prometheus/rules/lake-alerts.yml`, over
`k2_lake_audit_failures_total`, which `docker/lake/metrics.py` reads from this table.

**Prometheus knows how many, the table knows which.** `k2_lake_audit_failures_total` is
declared label-free in `docker/lake/metrics.py`, it is the failed-check *count* stamped
into the `audit.checks` snapshot summary by the run that wrote it, and `LakeAuditFailed`
adds only `severity`, `component` and `tier`. There is no `check_name` or `scope` label to
select on, on either the metric or the alert; the check identity only ever exists as a row.

```bash
# How many checks failed in the last maintenance run?  not yet run: Phase D burn-in
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_audit_failures_total' | jq -r '.data.result[].value[1]'

# Is the alert firing, and since when?                                     ✅ verified
curl -s localhost:9090/api/v1/alerts | \
  jq -r '.data.alerts[] | select(.labels.alertname=="LakeAuditFailed")
         | "\(.labels.severity) \(.activeAt)"'
# (empty on 2026-08-26: the only alert firing is CaptureFeedStale)
```

```sql
-- WHICH checks failed, and the last time each scope passed. This query, not the
-- alert labels, is the diagnosis.            not yet run, Phase D burn-in
SELECT run_ts, check_name, scope, observed, detail
FROM lake.audit.checks
WHERE passed = false AND run_ts >= current_date() - INTERVAL 7 DAYS
ORDER BY run_ts DESC;

SELECT check_name, scope, max(run_ts) AS last_pass
FROM lake.audit.checks WHERE passed = true
GROUP BY check_name, scope ORDER BY last_pass;
```

**A failed audit row is never edited or deleted.** It is the record that something was
wrong on a given night; a later passing row is how recovery is expressed. Removing it makes
the archive's completeness claim untrue in exactly the way the audits exist to prevent.

---

## 1. `offset_continuity`: a hole in the archive

**Symptom**, the audit reports non-abutting offset ranges for a `(topic, partition)`:
consecutive ingest snapshots leave a numeric gap, or the day seam does not join up.

**Expected behaviour**, this is the archive's most serious check, and it fails for exactly
two reasons that demand opposite responses:

| Cause | Distinguishing evidence | Response |
|---|---|---|
| **Real loss**, records were evicted from Redpanda before ingest read them | a `failOnDataLoss` failure in the same window ([lake-ingest-lag.md §3](./lake-ingest-lag.md#3-failondataloss--the-offsets-point-below-what-the-broker-holds)); the broker's `LOG-START-OFFSET` above the last committed offset | **record the gap**, it is unrecoverable, public feeds do not replay |
| **An audit bug**, a legitimate seam counted as a gap | no ingest failure, no eviction, and the "gap" sits at a day boundary, a topic recreation, or a compaction snapshot | **fix the audit**, a false positive here trains people to ignore the one alert that must never be ignored |

**Recovery**

```bash
# 1. Was there an eviction? Committed offsets vs what the broker holds.    ✅ verified
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.kraken
```

```sql
-- 2. The offsets on the snapshots either side of the reported gap.
--    not yet run, Phase D burn-in
SELECT snapshot_id, committed_at, summary['k2.kafka-offsets']
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 10;

-- 3. Confirm against the data rather than the metadata: is the offset
--    sequence actually dense across the reported boundary?
SELECT offset FROM lake.raw.messages
WHERE topic = 'market.crypto.v3.raw.kraken' AND partition = 7
  AND offset BETWEEN 918430 AND 918460 ORDER BY offset;
```

**An already-recorded gap has already been reconciled, by the check itself.** A
`--accept-data-loss` repair files an `offset_gap` row in this same table naming the exact
range Redpanda evicted, and `offset_continuity` **nets those ranges out**: it reads the
recorded gaps, reads the actual holes for the flagged partition, and passes when every hole
sits inside a recorded range and the hole sizes account for the entire shortfall. The
passing row still carries the number, `N recorded gaps netted (first..last)`, so the
archive never claims to be dense when it is not; it claims the holes are the ones a person
signed for. So a `LakeAuditFailed` on `offset_continuity` **is news**, which is the only
state in which a critical alert is worth having.

What still fails, and must:

- a hole **wider** than what was recorded, even by one offset. Partial coverage is not
  coverage; those offsets are records nobody wrote down.
- a hole on a partition with **no** `offset_gap` row at all.
- a partition whose hole sizes do not add up to the reported shortfall. `observed` is
  `missing - duplicated`, so 100 acknowledged missing offsets plus 100 rows written twice
  reports 0, the arithmetic check is what stops a duplication hiding inside an
  acknowledged hole.
- an `offset_gap` row whose `detail` cannot be parsed for its two offsets (a hand-filed row
  in some other wording). It nets nothing out, which fails, which is the safe direction.

The netting is `offsets.uncovered_holes` / `offsets.recorded_gaps` (pure, unit-tested in
`tests/test_lake_offsets.py`), wired in `maintenance._net_recorded`. The exact holes are
read **only** for a partition the aggregate check already flagged, the healthy nightly path
is still one group-by, not a window function over the whole archive.

**To see the list yourself**, the same reconciliation, by hand, which is the way to read
*which* incident a netted row is netting:

```sql
-- Every recorded gap and every continuity result, by scope, oldest first.
-- A continuity row that passes with "recorded gaps netted" names the ranges it
-- netted; an offset_gap row above it names the incident that recorded them.
SELECT check_name, scope, observed, run_ts, detail FROM lake.audit.checks
WHERE check_name IN ('offset_gap', 'offset_continuity') ORDER BY scope, run_ts;
```

Measured 2026-08-26: `market.crypto.v3.raw.kraken/0` carries an `offset_gap` of
**1,168,954** and `offset_continuity` reported **1,168,954 missing** on the same partition
the two records agreeing. Measured 2026-08-27, after the netting landed, the same
partition passes:

```text
ok   offset_continuity  market.crypto.v3.raw.kraken/0
     1 recorded gaps netted (1615463..2784416); 1168954 records missing, all
     acknowledged by an offset_gap row in lake.audit.checks, nothing else is
```

`scripts/lake-verify.sh`'s `offsets gapless` line calls this same `audit_offset_continuity`,
so it nets the same rows and prints the same detail, one definition, not two. Its first
run on 2026-08-27 failed on exactly the acknowledged 1,168,954 before that was true.

**If the gap is real and not yet recorded**, its deliverable is a record, not a repair:
[lake-ingest-lag.md §3](./lake-ingest-lag.md#3-failondataloss--the-offsets-point-below-what-the-broker-holds)
is the procedure, and `ingest.py --accept-data-loss` writes the row itself. Then fix the
cause upstream, ingest downtime, or a per-run bound below the real arrival rate.

**If the gap is not real**, the bug is in the audit, and it is worth naming the two
lookalikes that will produce it. **Compaction snapshots carry no `k2.kafka-offsets`** and
must be skipped when walking the offset history
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), an audit that treats a
compaction snapshot as an ingest one sees a gap the size of a night. And a **topic
recreation resets the offset space**, so continuity is only meaningful within one epoch of
a topic; that is an event to record, not a routine operation
([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md), *Risks*).

**Measured**, not yet verified.

---

## 2. `duplicate_identifiers`: a row landed twice

**Symptom**, the audit finds more than one row per identifier-field tuple. The tuples are
the ones `docker/lake/maintenance.py` passes into `audit_duplicates`, which are the
`SET IDENTIFIER FIELDS` clauses in `docker/lake/ddl/lake.sql`:

| Table | Duplicate key |
|---|---|
| `bronze.trades` | `(exchange, symbol, trade_id, src_topic, src_partition, src_offset)`, **the source lineage, not `conn_id`** |
| `bronze.book_snapshots_l2` | `(exchange, symbol, conn_id, snapshot_ts_ns)` |

**Both keys were settled by measurement, and the trade key is the most misread line on this
page.** Over 287,184 trades captured on 2026-08-26, `(exchange, symbol, trade_id)` *alone*
had **956 duplicated keys**, every one Coinbase, every one a pair of rows with identical
price, qty, side and `exchange_ts` under two different `conn_id`s. Coinbase replays recent
`market_trades` on resubscribe, so a reconnect genuinely delivers the same trade twice and
the append-only archive genuinely holds both frames. The book table went the other way for
the same kind of reason: `conn_msg_seq` records which frame the book last folded in, so a
quiet book gives two consecutive 1 Hz samples the same value, **484 duplicated
`(exchange, symbol, conn_id, conn_msg_seq)` keys over 47,331 snapshots**, while the
sampler's own clock, `snapshot_ts_ns`, had zero. Both numbers and their commands are in
`docker/lake/ddl/lake.sql`.

**`conn_id` turned out not to be enough.** The first day of the archive (2026-08-26) held
**5,034 Coinbase `(exchange, symbol, trade_id, conn_id)` keys twice**, two distinct
`market_trades` frames ~15 s apart on one connection (`src_offset` 9374 and 9772 on
`trades.coinbase/9` are one pair; identical price, qty, side and `exchange_ts`). The venue
re-sends recent trades inside a live subscription, not only after a reconnect. So the key
is now the **source lineage**: one archived record decodes into one row per trade id it
carries, and that is the only uniqueness the ingest can promise.

**So venue replay of the same `(exchange, symbol, trade_id)`, across connections or within
one, is EXPECTED, and it is not this check.** It is counted separately by `venue_replay` (§4), which reports it as a
number and never fails. **One sample exists and no daily rate is extrapolated from it:**
956 replayed trades in 287,184, over 30 minutes on 2026-08-26, all three venues
([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md), repeated above
`bronze.trades` in `docker/lake/ddl/lake.sql`). Reconnects are bursty, the replay follows
resubscribes, not the clock, so scaling half an hour to a day would invent a number the
sample cannot support. The Phase D burn-in produces the daily figure. Either way,
escalating this as an ingest bug is escalating the venue's documented behaviour.

**Expected behaviour**, a `duplicate_identifiers` failure on the **lineage** key is the
one that means the exactly-once contract broke, and that should be impossible. The ingest is
exactly-once by construction: a run's own commit moves its start offset, so no run can
re-read a range another run committed
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). A duplicate on the full
key therefore means one of the mechanism's assumptions has broken, not that a venue
repeated itself.

**Recovery**

```sql
-- 1. What exactly is duplicated, and does it come from one source row or two?
--    Grouping on the venue key re-finds the replay §4 already reports, the
--    distinct_sources column is what separates the two.   run 2026-08-26: 5,034 keys, all distinct_sources = 2
SELECT exchange, symbol, trade_id, conn_id, count(*) AS n,
       count(DISTINCT (src_topic, src_partition, src_offset)) AS distinct_sources
FROM lake.bronze.trades
WHERE exchange_ts >= current_date() - INTERVAL 1 DAYS
GROUP BY exchange, symbol, trade_id, conn_id HAVING count(*) > 1 LIMIT 20;
```

The `distinct_sources` column splits the diagnosis in one query, this is why the lineage
columns exist:

| `distinct_sources` | Meaning | Where the bug is |
|---|---|---|
| **1** | one archived frame decoded into two bronze rows | stage 2 ran twice over the same source snapshot range, check `k2.src-snapshot-id` on consecutive `bronze.*` commits |
| **2** | two archived frames, on the *same connection*, carry the same venue trade id | **the venue re-sent it**, measured 2026-08-26, 5,034 Coinbase pairs ~15 s apart. Not an ingest bug and, since the key became the lineage, no longer this check; §4 counts it |

```bash
# 2. Was ingest running more than once at a time? Concurrency 1 is a
#    correctness setting, not a politeness one.   not yet run: Phase D burn-in
docker exec k2-prefect-server prefect deployment inspect 'lake-ingest/lake-ingest-5min'
```

```sql
-- 3. Source-snapshot lineage on the bronze commits around the window.
SELECT snapshot_id, committed_at, summary['k2.src-snapshot-id'], summary['added-records']
FROM lake.bronze.trades.snapshots ORDER BY committed_at DESC LIMIT 10;
```

**Do not delete the duplicates first.** They are the evidence, the tables are copy-on-write
so a delete rewrites files, and a duplicate that is *the venue's* fault must not be silently
removed, it is a fact about the feed and belongs in ADR-027's or ADR-024's Outcome, with
the measurement behind it. Fix the cause; then decide, deliberately, whether to rewrite the
affected partition from `raw.messages`, which is always available because `bronze.*` is a
pure function of the archive.

**Measured**, not yet verified.

---

## 3. `sequence_gaps`: venue sequence discontinuity

**Symptom**, the audit's lag-over-`seq` check finds a jump in a venue's sequence numbers
within one `conn_id`.

**Expected behaviour**, **this is a data finding, not a platform fault**, and it usually
means a message was lost between the venue and this host. It cannot be repaired: public
feeds do not replay, so the frames are gone. The counter and the audit exist so the loss is
*recorded* rather than silent, v2 had no gap detection at all, which is why a dropped
message there was invisible ([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md),
Context).

Sequencing means three different things across the three venues, and reading the failure
wrong is easy. The authoritative table is in
[ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md); the short form:

| Venue | `seq` is | A "gap" means |
|---|---|---|
| **Binance** | `lastUpdateId` | it regressed or repeated backwards |
| **Kraken** | **always 0**, the venue does not sequence this stream | nothing. `seq = 0` is a documented sentinel for "unanswerable", and an audit treating it as a gap is an **audit bug** |
| **Coinbase** | `sequence_num`, **connection-wide across all channels** | a message was lost, but not which stream lost it |

**Recovery**

```bash
# 1. Did capture see it too? The capture-side counters are the corroboration. ✅ verified
for m in gaps reconnects resyncs; do
  echo "== $m"
  curl -s --get localhost:9090/api/v1/query \
    --data-urlencode "query=increase(k2_capture_${m}_total[24h])" | \
    jq -r '.data.result[] | "  \(.metric.exchange) \(.value[1])"'
done
```

```sql
-- 2. Does the gap sit at a conn_id boundary? If it does, it is a reconnect,
--    not a loss, a new connection starts a new sequence space.
--    not yet run, Phase D burn-in
SELECT conn_id, min(seq), max(seq), count(*), min(exchange_ts), max(exchange_ts)
FROM lake.bronze.trades
WHERE exchange = 'coinbase' AND exchange_ts >= current_date() - INTERVAL 1 DAYS
GROUP BY conn_id ORDER BY min(exchange_ts);
```

Then classify, the same three-way split the capture runbook uses, applied to the archive
instead of to a counter:

- **Gap at a `conn_id` change** → expected. Binance also does a scheduled reconnect at
  ~23 h of connection life. If the audit counts these, that is an **audit bug**: the
  boundary is detectable from `conn_id` and should be excluded.
- **Gap inside one `conn_id`, with a matching `k2_capture_gaps_total` increment** → a real
  loss. Record the window.
- **Gap in the archive with no capture-side increment** → the two disagree, which is worse
  than either alone. One of them is wrong, and finding out which is the priority.

Capture-side triage is [capture-sequence-gaps.md](./capture-sequence-gaps.md); this
runbook's job is the archive-side record.

**Measured**, not yet verified.

---

## 4. `venue_replay`: informational, and it cannot fail

**Symptom**, none. This check never fails; `passed` is always `true` and `maintenance.py`
lists it under `INFORMATIONAL`, so it is excluded from the `N audits passed` count and
printed with an `info` marker rather than `ok`. It cannot raise `LakeAuditFailed`.

**What it measures**, how many logical trades arrived on more than one connection:
`(exchange, symbol, trade_id)` groups on `bronze.trades` with `count(DISTINCT conn_id) > 1`.
That is the Coinbase resubscribe replay described in §2, measured at **956 such trades in
287,184 over 30 min on 2026-08-26**. Those rows are real frames that really arrived, and
the archive keeps both.

**What to do with it**, read the *rate*, not the level. The number exists so that a
**change** in it is visible: a jump means reconnect churn, which is a capture-tier
question ([capture-sequence-gaps.md](./capture-sequence-gaps.md)), not a lake one.

```sql
-- The replay count per run, over the last fortnight. A step change is the signal.
-- There is no expected level to compare a single run against yet: the only
-- measurement is the 30-minute sample above, and the daily rate is what this
-- query produces rather than what it is checked against.
-- not yet run, Phase D burn-in
SELECT run_ts, observed, detail
FROM lake.audit.checks
WHERE check_name = 'venue_replay' AND run_ts >= current_date() - INTERVAL 14 DAYS
ORDER BY run_ts;
```

The 956 itself is `audit_venue_replay()` in `docker/lake/maintenance.py`, and it is one
query, run it against any window to reproduce the count for that window:

```sql
-- not yet run against real tables, Phase D burn-in
SELECT count(*) FROM (
  SELECT exchange, symbol, trade_id
  FROM lake.bronze.trades
  GROUP BY exchange, symbol, trade_id
  HAVING count(DISTINCT conn_id) > 1
);
```

A research query that wants one row per logical trade deduplicates on
`(exchange, symbol, trade_id)` itself, that is the logical key, and `conn_id` is in the
*identifier* key so that the storage claim stays true without hiding the replay
(`docker/lake/ddl/lake.sql`, `bronze.trades`).

**Measured**, not yet verified.

---

## 5. `unresolvable_schema_id`: a record framed with a schema the registry will not serve

**Symptom**, a row in `lake.audit.checks` with `job = 'ingest'`, `check_name =
'unresolvable_schema_id'`, `scope` of the form `<table>/schema_id=<n>` and `passed = false`.
Stage 2 prints `stage 2: SKIPPING schema id <n>: …, filing an audit row` and carries on.

**Expected behaviour, the run does not die, and that is deliberate.** The record is already
in `raw.messages`, `raw.messages` is never expired, and stage 2 re-reads the same snapshot
range until it succeeds, so raising on an unregistered id would be a **permanent** outage
from one bad frame rather than one skipped id (`fetch_schema` /
`UnresolvableSchema` in `docker/lake/ingest.py`). Everything else in the batch decodes
normally; only that id is skipped.

**This has its own alert, and it does not disturb `LakeAuditFailed`.** `audit.checks` has
two writers, so each gauge names the job whose number it claims to be:
`k2_lake_audit_failures_total` is read off the newest snapshot carrying
`k2.job=maintenance`, `k2_lake_unresolvable_schema_ids_total` off the newest carrying
`k2.job=ingest` (`latest_job_summary()` in `docker/lake/metrics.py`). Reading either off
the *current* snapshot is the bug that pair replaced: the ingest's commit lands on top of
the audit's, so its own count, 0 for a clean run, used to become
`k2_lake_audit_failures_total` and clear a firing `LakeAuditFailed` with no audit having
passed. `LakeUnresolvableSchemaId` (warning, `for: 15m`) is this finding's own signal, and
the regression is asserted in `docker/prometheus/rules/tests/lake-alerts_test.yml`.

**How this alert clears, which is not the obvious way.** Stage 2 commits a row to
`audit.checks` *only when it found an unresolvable id*, a clean run writes nothing there , 
so no later ingest overwrites the last bad summary, and a gauge read straight off it would
latch at that count for as long as nothing else went wrong. It is therefore aged:
`fresh_ingest_failures()` in `docker/lake/metrics.py` returns 0 once the newest
`k2.job=ingest` summary is older than three ingest cycles (15 min). A genuine case re-files
the same row every 5 minutes, stays fresh, holds the gauge above 0 indefinitely and keeps
the alert firing. A fixed one stops being re-filed, ages out within 15 minutes of the last
bad cycle, and the alert resolves on its own, there is no clean commit to clear it sooner,
so do not wait for one. All of a run's findings ride on one commit, so the gauge is the
number of unserved ids that run saw, not 1 for whichever row went last.

**Recovery**, the schema id is the whole diagnosis.

```bash
# 1. Does the registry hold it at all?             not yet run: Phase D burn-in
curl -s "localhost:8081/schemas/ids/<n>" | jq .

# 2. What is on the topic right now, and how is it framed?
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --probe
```

Three causes, in order of likelihood: the registry volume was rebuilt and lost the
subject (restore it, then re-run the ingest, the range is re-read and the rows decode);
a foreign producer wrote to a v3 topic (a capture-tier finding, and the archive keeps the
bytes either way); or the id is genuinely fabricated, which `wire.schema_id_guarded_expr`
already rejects at stage 1 for short frames.

**The skipped rows are not lost, but they are not always retried either**, and the
difference is worth knowing before deciding how fast to act (`_decode_into` in
`docker/lake/ingest.py`):

- **Some other id in the batch decoded** → the commit advances `k2.src-snapshot-id` past the
  whole range, skipped rows included. Fixing the registry afterwards does *not* bring them
  back on its own; they need a bounded re-read of that snapshot range from `raw.messages`.
- **Nothing in the batch decoded** → no commit, `k2.src-snapshot-id` does not move, and the
  next run re-reads the same range and decodes it once the id resolves.

Either way the bytes are in `raw.messages` verbatim and `bronze.*` stays a pure function of
the archive, so the repair is a replay rather than a loss. Fix the registry first.

**Measured**, not yet verified.

---

## 6. `bronze_unparseable`: a frame is in the table with its venue columns NULL

**Symptom**, `check_name = 'bronze_unparseable'`, `scope` one of the six
`lake.bronze.<venue>_<msgtype>` tables, `observed` = the row count, `detail` naming the
first ten `(src_partition, src_offset)` pairs.

**What it means**, `docker/lake/bronze.py` parses the venue JSON with `from_json` in
PERMISSIVE mode, so a frame whose shape differs from the table's DDL lands with lineage
intact and every venue column NULL instead of failing the run. Nothing is lost , 
`raw.messages` holds the bytes, but the table is lying by omission until the DDL matches
what the venue sent.

```sql
-- the frames, verbatim, from the archive
SELECT r.topic, r.partition, r.offset, CAST(substring(r.payload, 6) AS STRING)
FROM lake.raw.messages r
WHERE r.topic = '<src_topic>' AND r.partition = <src_partition> AND r.offset IN (<src_offset>, ...);
```

Decide from the payload: a new nesting or type is a `/schema-change` on the table's
`VenueTable.schema` in `bronze.py` **and** its DDL in `lake.sql`, then
`make lake-rebuild LAYER=bronze EXCHANGE=<venue>`; a genuinely malformed frame from the
venue is a finding to record (`job = 'operator'`) and leave.

## 7. `bronze_schema_drift`: the venue added a key

**Symptom**, `check_name = 'bronze_schema_drift'`, `detail` listing the JSON path and the
undeclared keys, e.g. `$.data[0]: ['liquidity']`.

**What it means**, the one way a PERMISSIVE parse loses data silently: a key with no
column is dropped without a NULL to show for it. The check samples 0.1 % of the venue's
last day of **raw** frames (not bronze) and diffs `json_object_keys` at each declared path
against `VenueTable.keys`. A key the venue *stopped* sending is not reported, the column
reads NULL, nothing was lost.

Add the column (nullable, a schema change, so `/schema-change`), refresh the fixture in
`tests/fixtures/bronze/`, and rebuild the table so the archive's earlier frames get the
column too. Until the rebuild, `raw.messages` is the only place the key exists.

## 8. `bronze_parity`: filed by the ingest when the frames do not balance

**Symptom**, `job = 'ingest'`, `check_name = 'bronze_parity'`, `scope = bronze.<venue>`,
`observed` = frames in − (rows written + control frames).

**What it means**, every run prints one line per venue:
`stage 2b: kraken: 61,412 frames = 61,240 decoded + 172 control (heartbeat=170, status=2)`.
When the sum does not match, a frame went missing between the raw read and the six writes,
which should be impossible: the same `from_avro` output feeds both the routed writes and
the control count. Treat it as a bug in `bronze.py`, re-run the decode for the venue
with `make lake-rebuild LAYER=bronze EXCHANGE=<venue>` after reading the run's log.

## 9. `silver_parity`: a bronze frame's trades are not all in silver

**Symptom**, `check_name = 'silver_parity'`, `scope` one of `lake.silver.trades_<venue>`,
`observed` = silver rows − trades in bronze (negative: missing; positive: extra).

**What it means**, silver reads each bronze table incrementally by snapshot and
explodes every frame to one row per trade; the count is compared against the bronze
snapshot silver last recorded (`k2.src-snapshot-id`), so a bronze commit after the
last silver tick is not a finding. A shortfall is a run that committed partially or a
frame whose trades did not explode (a shape silver's `explode` SQL does not expect);
an excess is a double write.

```bash
make lake-rebuild LAYER=silver EXCHANGE=<venue>     # drop, recreate, replay by day
```

## 10. `silver_flags`: the rates, informational

One line per venue: rows, venue replays, trade-id gaps, ids never received, rows
beyond 8 decimals. It cannot fail; it is here so a *change* is visible, a jump in
gaps is a capture-tier question (`k2_capture_reconnects_total`, produce errors), a
jump in replays is reconnect churn.

## 11. `gold_parity`: gold does not equal silver's first deliveries

**Symptom**, `check_name = 'gold_parity'`, `scope = lake.gold.trades/<venue>`, `observed` =
gold rows − silver `venue_replay = false` rows at the silver snapshot gold last read.

**What it means**, gold.trades is a projection of silver with no rule of its own; a
mismatch is a partial commit or a stage that read a range twice. There is nothing to
reconcile by hand:

```bash
make lake-rebuild LAYER=gold          # drops and recreates gold.* from silver, ~minutes
```

## 12. `ohlcv_parity`: a stored candle disagrees with the trades

**Symptom**, `check_name = 'ohlcv_parity'`, `scope = lake.gold.ohlcv_1m`, `observed` = the
number of yesterday's (exchange, symbol, minute) buckets whose stored open/high/low/close/
count differ from a fresh aggregation of `gold.trades`, or exist on one side only.

**What it means**, a late trade landed and the `MERGE` for its bucket did not, or a
bucket was written from a partial view. The candle tables are derived and cheap:
`make lake-rebuild LAYER=gold` recomputes every bucket. If it recurs, the bucket-key
derivation in `gold.stage_ohlcv` and `gold.candles` disagree, that is a code bug, not
a data one.

## 13. `kraken_checksum`: the replayed book does not hash to the venue's checksum

**Symptom**, `check_name = 'kraken_checksum'`, `observed` = frames with `checksum_ok = false`
in `lake.silver.book_kraken`; the detail also counts the unverifiable ones (`NULL`: no
precision for the pair, or a frame before its connection's snapshot).

**What it means**, for that frame, the book replayed from the connection's frames
(truncated to depth 25, hashed over the top 10 at the pair's precision) is not what the
venue had. Either a frame is missing between the snapshot and this one (a capture-side
loss: compare `conn_msg_seq` continuity in `raw.messages` for the connection), or a level
was applied wrongly (a `book.py` bug, the unit test pins Kraken's own example, so start
with a frame whose precision changed mid-connection: `bronze.kraken_instrument` updates).
The capture verifies the same checksum live and resubscribes on a mismatch
(`k2_capture_checksum_failures_total`); if the capture saw no failure at that time, the
replay is wrong, not the data.

```sql
SELECT symbol, conn_id, conn_msg_seq, checksum, frame_type, recv_ts
FROM lake.silver.book_kraken WHERE checksum_ok = false ORDER BY recv_ts LIMIT 20;
```

**Acknowledging a window.** Once the cause is known and it is an archive hole, not a
replay bug, file an operator row and the audit nets those frames out on every later run
(they stay in the table; the detail line still counts them). Exactly the `offset_gap`
pattern of §1:

```sql
INSERT INTO lake.audit.checks VALUES (
  current_timestamp(), 'operator', 'checksum_failure_acknowledged', 'lake.silver.book_kraken', true, 386962,
  'from 2026-08-26T16:00:00Z to 2026-08-26T18:00:00Z: capture-kill / queue-full / redpanda-stop chaos runs (scripts/chaos/results/2026-08-26.tsv); 31,464 Kraken records dropped on a full producer queue; every failure of the archive is in this window');
```

Filed 2026-08-27 for exactly that window, after the first books rebuild.

## 14. `bars_parity`: a stored bar disagrees with the trades

**Symptom**, `check_name = 'bars_parity'`, `scope = lake.gold.bars`, `observed` = the
number of yesterday's `(exchange, canonical_symbol, bar_kind, bar_seq)` bars whose stored
open/high/low/close/volume/quote_volume/trade_count differ from a fresh recompute over
`gold.trades` (`bars.bars_sql`, the same SQL `gold.stage_bars` runs), or exist on one side
only.

**What it means**, same failure shape as `ohlcv_parity` (§12), one bucket lower: a late
trade landed and `gold.stage_bars`'s delete-then-append for its `(exchange, symbol, day)`
did not run, or ran over a stale `gold.trades` snapshot. Because every later `bar_seq` in a
touched day shifts with it, a missed trade tends to show up as a run of mismatched
`bar_seq` values for that day rather than one bar.

```bash
make lake-rebuild LAYER=bars          # drops and recreates gold.bars from gold.trades
```

If it recurs after a rebuild, the bucket arithmetic in `bars.bars_sql` and the audit's own
recompute have drifted apart — a code bug, not a data one, since both call the same
function.

**Measured**, not yet verified.

## After any failure: do not silence the audit

The audit fails the maintenance run on purpose, that is
[ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md)'s "fail-fast for audit" policy
carried into the lake. Loosening a threshold to get a green run turns the one signal that
proves the archive is complete into decoration. If a venue genuinely behaves in a way the
audit calls a failure, that is a fact worth recording in the relevant ADR's Outcome, with
the measurement behind it, not a threshold to move.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten. **Every real gap
found by §1 or §3 is recorded here**, with its window and its evidence._

---

## Related

- [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md), offset continuity, and why compaction snapshots must be skipped when reading the offset history
- [ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md), the identifier fields the duplicate check uses, and why the book table's differ from the trade table's
- [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql), the `SET IDENTIFIER FIELDS` clauses §2 asserts, with the 956/287,184 and 484/47,331 measurements that put `conn_id` and `snapshot_ts_ns` in them
- [`docker/lake/maintenance.py`](../../docker/lake/maintenance.py), the `AUDITS` tuple: the six checks, their scopes, and `INFORMATIONAL`
- [ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md), three sequencing mechanisms, and the `seq = 0` sentinel
- [ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md), the compact → expire → audit ordering and the fail-fast-on-audit policy the lake inherits
- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md), the lineage columns that make §2 a one-query diagnosis
- [capture-sequence-gaps.md](./capture-sequence-gaps.md), the capture-side half of §3
- [lake-ingest-lag.md](./lake-ingest-lag.md), `failOnDataLoss`, the usual cause of a real §1 failure

---

**Last verified:** not yet verified, the audit code exists but has never run against a
populated lake, and the `raw`/`bronze`/`audit` tables hold no rows on this host. Commands
marked ✅ were run read-only against the running stack on 2026-08-26. Stamp this line at the
Phase D burn-in and replace each "not yet run" marker with real output.

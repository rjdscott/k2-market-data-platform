# Runbook: Pause and resume a serving product

A *serving product* is a copy of a product held in a second engine for fast reads. The only one
today is the ClickHouse hot tier, the `gold` database of `k2-clickhouse`, fed straight from the
trade and book topics. It is paused for the platform build behind the compose profile `hot-gold`
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md); the ADR says why).
A *compose profile* is a label that keeps a service out of `docker compose up` unless that
profile is named. This page covers the pause, a resume, the two 7-day clocks that bound a resume
and what a resume after them loses, the consumer-group checks after one, and why the
reload from the lake no longer applies. It does not cover the planned return, which is a
rebuild of the hot tier as the serving copy of a product of the new platform (ADR-057,
decision 4), not a resume.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a` loaded.**

| # | Operation | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Pause the hot tier | a planned operation, no target | performed in phase 000: read on 2026-11-26 (section 0), no container, both volumes kept, both groups `Empty` |
| 2 | Resume within 7 days of the stop | < 5 min to `Stable` groups | not yet performed |
| 3 | Resume after 7 days | not a repair: it leaves a hole | not yet performed |

---

## 0. Where it stands now

Read-only, safe at any time:

```bash
docker compose --profile hot-gold config --services | grep -x clickhouse   # the service, behind the profile
docker compose config --services | grep -cx clickhouse                     # 0 once the profiles: line is in
docker ps -a --filter name=k2-clickhouse --format '{{.Names}} {{.Status}}'  # nothing while paused
docker volume ls --filter name=clickhouse                                   # both volumes, paused or not
docker exec k2-redpanda rpk group list                                      # k2-gold-trades, k2-gold-book
```

Run 2026-11-26 on the live checkout, after the pause: the first printed `clickhouse`, the
second `0`, the third nothing, the fourth `k2-market-data-platform_clickhouse-data` and
`k2-market-data-platform_clickhouse-logs`, the fifth both groups `Empty`. Before the `profiles:`
line reached it, the same day, they printed `clickhouse`, `1`, `k2-clickhouse Up 14 hours
(healthy)`, the two volumes, and both groups `Stable`.

---

## 1. Pause the hot tier

**Symptom**, none: a planned operation. Pause when nothing needs fast reads and the 4.0 CPU /
8 GiB of its limits are wanted elsewhere.

**Detection**, manual only, and there is nothing to detect afterwards: ADR-057 removes every
signal that reads ClickHouse with the pause (below), so no alert fires for a stopped hot tier and
none fires for a resumed one.

**Expected behaviour**, the container goes, its data stays, and nothing upstream notices.

- **Kept:** the volumes `clickhouse-data` and `clickhouse-logs`; the `image:` line and the DDL in
  `docker/clickhouse/ddl/`, which `make test-clickhouse` and CI apply to a throwaway server.
- **Unaffected:** capture and Redpanda. Nothing else reads the topics until phase 004: the v3
  lake ingest that did retired in phase 000 ([`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/)).
- **Stood down with it** (ADR-057, decision 2), each in its own change of phase 000: the parity
  check's ClickHouse leg, a SKIP line naming ADR-057 until the whole check was archived with the
  v3 lake ingest (`make parity-ohlcv` now prints one SKIP naming phase 004); the six
  rules in `docker/prometheus/rules/clickhouse-alerts.yml` and their tests, and the `clickhouse`
  scrape job; the dashboard `clickhouse-overview.json` and the ClickHouse panels of
  `k2-pipeline-overview.json`; the ClickHouse half of `make health` step (2); the chaos scripts
  `clickhouse-stop.sh` and `clickhouse-corrupt-record.sh` with their `Makefile` lines and
  failure-mode rows; screenshots 08 and 09.
- **The two consumer groups** lose their members and keep their committed offsets, the position
  in each partition up to which ClickHouse has read. Both 7-day clocks start now (section 3).

**Recovery** (the steps)

```bash
# 1. The stop time, in UTC: both clocks in section 3 count from it. Append it to ADR-057 as an
#    Outcome line (the only change an Accepted ADR takes), where the next operator finds it.
date -u +%FT%TZ

# 2. Where the groups are: the record a resume is checked against.
docker exec k2-redpanda rpk group describe -s k2-gold-trades k2-gold-book

# 3. Stop, with 60 s to shut down cleanly, then remove the container. Never add -v.
docker compose --profile hot-gold stop -t 60 clickhouse
docker compose --profile hot-gold rm --force clickhouse

# 4. Check: no container, both volumes still there, the groups without members.
docker ps -a --filter name=k2-clickhouse -q                  # prints nothing
docker volume ls --filter name=clickhouse                    # both volumes
docker exec k2-redpanda rpk group describe -s k2-gold-trades k2-gold-book   # MEMBERS 0
```

Step 2 run 2026-11-26 while the tier ran (lag replaced, it counts market records):

```
GROUP                  k2-gold-book
COORDINATOR-NODE       0
COORDINATOR-PARTITION  __consumer_offsets/2
STATE                  Stable
BALANCER               range
MEMBERS                2
TOTAL-LAG              <n>
```

and the same block for `k2-gold-trades`. Step 3 was rendered with `--dry-run`, which changes
nothing, on 2026-11-26: `stop -t 60` printed `Container k2-clickhouse Stopping` and `Stopped`;
`rm --stop --force` printed `Stopping`, `Stopped`, `Going to remove k2-clickhouse`, `Removing` and
`Removed`. The `rm --force` of step 3 alone, rendered against the running container, prints
`No stopped containers`, which is why the stop comes first. After step 3 the state of a group with
no members and stored offsets reads `Empty`: both groups printed `STATE Empty` and `MEMBERS 0`
after the pause of 2026-11-26.

**Why not `make down` or a plain stop.** A stop without the profile line in the compose file is
undone by the next `make up`. And `docker compose down` without `--profile hot-gold` does not touch
a container under a profile: on a scratch project with one service under a profile, `down` removed
the other service and left that one running.

**Measured**, performed once, on 2026-11-26: the container stopped at 14:21 UTC (the ADR-057
Outcome, which says how the minute was read); step 4's checks, run later the same UTC day,
printed no container, both volumes and both groups `Empty`. The stop was not timed.

---

## 2. Resume within 7 days of the stop

**Symptom**, someone needs the hot tier back before the new platform's serving copy exists. This
is the emergency path ADR-057 allows, not the planned return.

**Detection**, manual only.

**Expected behaviour**, inside 7 days the groups still hold their committed offsets, so each
Kafka engine table carries on from where it stopped and nothing is missed: its lag starts at
everything produced during the pause and drains. A block written to ClickHouse but not yet
committed to Redpanda when it stopped is read again; `gold.trades` is `ReplacingMergeTree`, so the
repeat collapses under `FINAL`.

**Before it, three checks**, read-only:

```bash
# 1. Do the committed offsets still exist? STATE Empty: yes. STATE Dead: they expired, go to section 3.
docker exec k2-redpanda rpk group describe -s k2-gold-trades k2-gold-book

# 2. Is every committed offset still inside its topic? Prints nothing when it is. A line means the
#    records between that offset and the topic's start are gone from the broker: section 3.
docker exec k2-redpanda rpk group describe k2-gold-trades k2-gold-book \
  | awk '$1 ~ /^market\./ && $3 != "-" && $3 + 0 < $4 + 0 { print "behind log start:", $1, $2, $3, $4 }'

# 3. Room for it: the hot tier adds 4.0 CPU / 8 GiB of limits. Look before starting it on this host.
free -h
```

Run 2026-11-26 while the tier ran: check 1 printed both groups `Stable`, and check 2 printed nothing
over the 72 partition rows of the two groups (an empty partition shows `-` and is skipped). Fed a
synthetic line whose committed offset was below its log start, the filter of check 2 printed
`behind log start: market.crypto.v3.trades.kraken 0 5 9`. A group with no stored offsets describes
as `STATE Dead` with `MEMBERS 0`: run 2026-11-26 against a group name that never existed. With
the profile running, the bootstrap is 20.70 CPU / 36.688 GiB against the ceiling of 25 / 48
(the `bootstrap hot-gold` line of `python3 scripts/check-budget.py`, 2026-11-26, after the v3 lake
ingest's two services retired; it was 21.30 / 37.812 with them).

**Recovery**

```bash
# Start it, by name, with its profile. Never a bare `docker compose up -d`: without the profile it
# does not start ClickHouse at all, and `docker compose --profile hot-gold up -d` with no service
# named also recreates every other service whose definition changed, which is a roll-out.
docker compose --profile hot-gold up -d clickhouse

# Repeat until it prints healthy (8 s after the recreate of 2026-11-25, operator guide section 6).
docker inspect -f '{{.State.Health.Status}}' k2-clickhouse
```

The `docker inspect` line printed `healthy` on 2026-11-26 against the running tier. The `up -d`
was rendered with `--dry-run` on 2026-11-26 while the container still ran: it printed
`Container k2-clickhouse Recreate`, `Recreated`, `Starting` and `Started`. After the pause there is
no container, so it creates one, on the kept volume. The DDL in `docker/clickhouse/ddl/` runs only
on an empty volume, so the tables come back as they were.

**Then check the consumer groups and lag:**

```bash
# Both groups Stable with 2 members each (two consumers per Kafka engine table).
docker exec k2-redpanda rpk group describe -s k2-gold-trades k2-gold-book

# Lag per topic: it should fall between two runs a minute apart, to near zero.
docker exec k2-redpanda rpk group describe -t k2-gold-trades k2-gold-book

# The consumers from ClickHouse's side: two rows per table, each with its share of the 36
# partitions of that table's three topics, last_poll_time within seconds of now.
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, length(assignments.topic) AS partitions, last_poll_time, last_commit_time
   FROM system.kafka_consumers WHERE database = 'gold' ORDER BY table"
```

All three run 2026-11-26 against the running tier. The second printed one lag line per topic,
`market.crypto.v3.book.binance` through `market.crypto.v3.trades.kraken`; the third printed two
rows each for `q_book` and `q_trades`, `18` partitions per row. `q_book`'s `last_commit_time` is
hours old because the book topics carry nothing since book capture paused
([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)); that is expected.

**While it runs**, nothing watches it: no alert, no dashboard, no `make health` line, no parity
leg. `make down` does not stop it either (the scratch result in section 1); pause it again with
section 1's steps.

**Measured**, not yet performed.

---

## 3. The two 7-day clocks, and a resume after them

Both start when ClickHouse stops reading, at the pause.

| Clock | Setting | Value | What expires |
|---|---|---|---|
| Committed offsets | `group_offset_retention_sec`, checked every `group_offset_retention_check_ms` | 604800 s (7 days), checked every 600000 ms (10 min) | each group's committed offsets, 7 days after it last had a member |
| Topic retention | `retention.ms` on `market.crypto.v3.trades.*` and `book.*`, `retention.bytes` -1 | 604800000 ms (7 days), no byte cap | the records themselves, oldest first |

```bash
docker exec k2-redpanda rpk cluster config get group_offset_retention_sec
docker exec k2-redpanda rpk cluster config get group_offset_retention_check_ms
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -c | grep -E '^retention\.(ms|bytes) '
```

Run 2026-11-26: `604800`, `600000`, and `retention.bytes -1 DEFAULT_CONFIG`, `retention.ms
604800000 DYNAMIC_TOPIC_CONFIG` (set by `docker/redpanda/init.sh`). Nothing in the repository sets
the first two, so they are the broker defaults. That the offsets go at 7 days and not before was
read from the setting; the expiry itself has not been watched on this host (ADR-057).

**Symptom**, a resume more than 7 days after the stop: check 1 of section 2 prints `STATE Dead`,
or check 2 prints `behind log start` lines.

**Detection**, manual only: those two checks. Nothing reports the hole afterwards.

**Expected behaviour**, the resume runs and the hot tier serves, with a hole in it:

- the groups have no offsets, so each partition starts where librdkafka's `auto.offset.reset`
  puts it: by the comment in [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql)
  the start of the topics' 7 days; what ClickHouse 24.3 does here is not yet verified (ADR-057);
- the topics hold only the last 7 days, so every trade from the stop to 7 days before the resume
  is in neither the broker nor the kept volume: the volume ends at the stop, the feed starts 7 days
  back from the resume, and the gap between is the hole;
- the one source that could have filled it, the lake's old `gold`, was dropped with the v3 lake
  (section 4), so nothing fills it until the new platform's trades products have a gold layer
  (phase 004, Kraken trades first).

**Recovery**: do not resume by habit. If the hot tier is needed after 7 days, the supported path is
the return as a product (ADR-057, decision 4). If it must be started anyway, run section 2, then
append to ADR-057's Outcome the hole's window in UTC (the stop time from section 1 to the resume
time less 7 days), so the next reader knows the gap is known.

**Measured**, not yet performed.

---

## 4. The reload from the lake no longer applies

[clickhouse-rebuild-from-lake.md](./clickhouse-rebuild-from-lake.md) reloaded the hot tier from
the lake's `gold` tables through `iceberg()`. Phase 000 dropped the old lake namespaces, `gold`
among them, and archived that runbook with the v3 lake
([phase 000](../plans/2026-11-26-unified-product-platform/000-clear-the-ground.md), wave 3). So
a hot tier that lost rows, by a resume after the clocks or a lost volume, stays without them
until the new platform's gold exists.

```bash
# No old namespace is left.
docker exec k2-trino trino --execute "SHOW SCHEMAS FROM lake"
```

Phase 000's exit expects `"information_schema"` and `"system"`, which every catalog has, and
nothing else. Run 2026-11-26 during the drop, it still listed `"audit"`, `"bronze"`, `"gold"`,
`"raw"` and `"silver"` beside them; earlier the same day, `"scratch_p4"` and `"scratch_p4_pr1"`
too.

---

## Failure modes / incidents

None yet. The first pause was phase 000's, on 2026-11-26; its stop time belongs in ADR-057's
Outcome (section 1, step 1).

---

**Last verified:** commands rendered and checked with `docker compose ... config`/`--dry-run` on
2026-11-26; live pause performed by the phase's operator. Run for real on 2026-11-26, read-only,
against the live stack with ClickHouse still running: every `rpk` command on this page, `docker
volume ls`, `docker ps -a`, `docker compose --profile hot-gold config --services`, the
`system.kafka_consumers` read and `SHOW SCHEMAS FROM lake`. Run on a scratch compose project: a
profiled service's absence from `config --services` without the profile, its start by name with
`up -d`, and `down` leaving it running. Not run from this page: the stop and the remove (done by
the phase's operator), the start of `k2-clickhouse` and every check after a start. Section 1
step 4's checks were run after the pause, 2026-11-26. Run again read-only after the pause, 2026-11-26:
section 0's five commands and section 4's `SHOW SCHEMAS FROM lake`.

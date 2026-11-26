# tests/parity

The pinned inputs of the last passing three-way OHLCV parity run
(`scripts/parity-ohlcv.sh`; runbook `docs/runbooks/clickhouse-rebuild-from-lake.md` §4).
`pinned.json` names the UTC day, the `lake.gold.ohlcv_1m` snapshot-id and the silver
snapshot-id per venue that `gold.trades` was built from — literal ids, never a moving
pointer, so the run is repeatable to the same answer. This is the seed of the Phase G CI
parity job (plan 004).

The `bars` block is `scripts/parity-bars.sh`'s: the UTC day, the `lake.gold.bars` and
`lake.gold.trades` snapshot ids, and the symbols the pure-Python reference was run over
(every symbol is a 10 M-row Python loop; two is the arbiter at CI cost).

## Known gaps

`known_gaps` is an optional list in `pinned.json`. Each entry names a window of the pinned
day that `scripts/parity_ohlcv.py` takes out of the comparisons the entry names, and out of
nothing else. Everything outside the window is still compared at tolerance zero, and the
comparisons the entry does not name still compare the window.

**When one may be listed:** only for a loss window already recorded as a row in
`lake.audit.checks`, with its cause, where the two stores cannot be made equal without
deleting data: a hard kill of the host or the Docker VM is the case it was built for
([`failure-recovery.md` §8](../../docs/runbooks/failure-recovery.md#8-host-or-docker-vm-killed-the-hot-tier-against-the-lake)).
Never for a difference nobody has explained, and never to get a run green: that is the
tolerance [ADR-029](../../docs/adr/ADR-029-research-production-parity-contract.md) rejects.
In practice that means `sides: ["clickhouse"]`: the lake, DuckDB over silver and dbt all
read the lake, so a loss on one store shows only between ClickHouse and the lake.

```json
"known_gaps": [
  {
    "start": "2001-01-01T00:01Z",
    "end": "2001-01-01T00:02Z",
    "sides": ["clickhouse"],
    "cause": "The host killed the Docker VM and the stack restarted; both stores hold different trades for this minute.",
    "recorded": "2001-01-02",
    "audit_ref": "lake.audit.checks job=operator check_name=hot_tier_divergence run_ts=2001-01-02T03:04:05Z"
  }
]
```

| Field | What it holds |
|-------|---------------|
| `start`, `end` | UTC, ISO 8601, to the minute; `end` is exclusive, so the example is one minute |
| `sides` | the comparisons it applies to, each the lake against one of `clickhouse`, `silver`, `dbt` |
| `cause` | one sentence: what happened |
| `recorded` | the date the entry was added, `YYYY-MM-DD` |
| `audit_ref` | where the `lake.audit.checks` row is: job, check name, run time |

**The run stops**, non-zero and naming the entry, on an unknown key, a missing field, an
empty `cause` or `audit_ref`, a time that is not UTC to the minute, an `end` not after its
`start`, a window longer than 60 minutes, a window outside the day compared, a side not in
the list above or named twice, or more than 5 entries. It also fails when a listed gap
removes no bucket at all from a comparison it names: stale or mistyped.

**Every run prints** each gap with its cause, the buckets it removed and how many of them
differed, and each comparison's bucket count after the removal. A gap whose window now
matches in full prints a notice to remove it; that is not a failure. A run with a gap
applied ends `PARITY: ok (1 known gap applied, see above)`: `PARITY: PASS` is kept for a
run that left nothing out. A gap whose sides this run skips (`--skip-clickhouse`) is printed
as not applied.

**Review:** an entry lands in a pull request with the pin it applies to, and the repo owner
reviews it. The
reviewer checks that the row `audit_ref` names exists with the same window and cause
(`docker exec k2-trino trino --execute "SELECT run_ts, scope, observed, detail FROM
lake.audit.checks WHERE job = 'operator' AND check_name = 'hot_tier_divergence' ORDER BY
run_ts"`), and that the run's output shows the gap removing only the buckets that differ
there. `make test-python` checks the committed entries against every rule above.

**Removing one:** delete the entry, in the same pull request that pins a different day:
a gap outside the day compared stops the run, so re-pinning with `--pin-current` to another
day fails until it is gone. Delete it sooner if a run prints the notice that its window
matches.

The bars parity (`scripts/parity_bars.py`) has no known gaps: its three sides all read the
lake, so a loss in ClickHouse cannot reach it.

# Runbook: Verify a Kafka product: its offsets, its layers, and parity with a recompute

Use this after a deploy, a rebuild ([rebuild-a-product.md](./rebuild-a-product.md)), a restore or an
incident, to prove a Kafka product read-only. `make product-verify` accounts for every offset from the
topic to bronze and holds each dbt output's tag to its run record; `make parity-silver` and `make
parity-ohlcv` recompute `kraken-trades`' silver and the candles with DuckDB from what they were built
from and compare every column. None of them writes anything. A failed audit is
[kafka-batch-blocked.md](./kafka-batch-blocked.md); a loss below the log start is
[accept-data-loss.md](./accept-data-loss.md).

> **Built on the phase 004 exit branch.** `make product-verify` ran on the live stack under
> `v3.0.0-alpha.6-rc.1` during the soak's first day (2026-11-28 to 29), run from a checkout of that
> branch: every check PASS. The parity targets need DuckDB in the release's `tools` image, which
> rc.1's does not have: last verified: throwaway, 2026-11-28 (`bash scripts/verify/duckdb-landing.sh
> <release dir>`, `7 PASS, 0 FAIL in 546 s`); live: not yet. Every message quoted is the code's own
> text; no live output is pasted, since its figures are counts of captured data.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** Each target is
one `tools` one-shot of the live release (the release's compose file, its pinned `tools` image, this
checkout's `scripts/` and `docker/lake/` mounted read-only), on the catalog's network: a container
job, so one at a time. `K2_HOME` is the compose project directory whose `.env` the stack runs with,
by default this checkout: run from a checkout that is not the live one (as rc.1's live checkout has
no `mk/product-verify.mk`) with `K2_HOME=<the live checkout>`.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Offsets accounted, layers level | < 10 min (its `WAIT`) | live, rc.1, every check PASS; its time not recorded |
| 2 | Silver against a recompute from the landing | none set | throwaway only, 2026-11-28; live: not yet |
| 3 | Candles against a recompute from canonical-trades | none set | throwaway only, 2026-11-28; live: not yet |

---

## 1. Offsets accounted, layers level

**When**, after a deploy or rebuild of a Kafka product or anything downstream of it, or to answer
"did we lose or double anything".

**Expected behaviour**, the check reads snapshot summaries and refs from the catalog's REST API (GET
only) and the run records as `svc.catalog` through the read-only `governance` catalog, never a row of
the product's tables (`scripts/product_verify.py`, header). One line per check:

| Check | Passes when |
|---|---|
| `landing` | every connector commit on the landing's `main` lineage holds exactly its spans (`k2.kafka-offsets` less `k2.kafka-starts` per partition), each starting where the one before ended, a jump only over a loss `k2.kafka-gaps` records, the recorded losses never shrinking, and no other commit changing the row count |
| `bronze` | every connector commit on bronze's `main` is staged through the landing's end offsets at the landing snapshot it read, and its rows plus `k2.kafka-skipped` are the landing rows of its range, less the recorded losses |
| `level` | bronze's newest through-offsets are the landing's newest end offsets |
| `tag` | each dbt output of the product and of every product downstream of it: its `audited` tag is its newest `audited` run record |

`level` and `tag` are false for a moment while a run is between its landing commit and its publish,
or between a tag and its run record; those two are read again every 20 s for `WAIT` seconds (default
600). The others fail at once: each commit is atomic, so no run in flight makes them false. What
snapshot expiry cut is not proved: the walk starts at the oldest commit `main`'s lineage still holds,
and says so.

**Recovery**, run on the live stack under rc.1 (every check PASS):

```bash
make product-verify PRODUCT=market-capture/kraken-trades          # WAIT=<s> to read level and tag again for longer
```

Its lines, from the code; the figures are the product's own and are not shown here:

```text
product-verify: market-capture.kraken-trades of <release id>: lake.kraken_trades.landing, lake.kraken_trades.bronze_trade and <n> dbt output(s)
PASS  landing lake.kraken_trades.landing: <n> connector commit(s) over <p> partition(s), each whole and starting where the one before ended, <j> jump(s) each over a recorded loss
PASS  bronze  lake.kraken_trades.bronze_trade: <n> connector commit(s): <staged> staged + <skipped> skipped = <read> landing rows read
PASS  level   lake.kraken_trades.bronze_trade: bronze is staged through the landing's end on all <p> partition(s)
PASS  tag     <output>: audited tag <snapshot id>, as its newest audited run record says
product-verify: market-capture.kraken-trades: every offset accounted for, every layer level
```

One `tag` line per output. Exit 0 every check passed; 1 one failed (`product-verify: <product>: <n>
check(s) failed`); 2 refused: no such Kafka product, a table not in the catalog, the catalog or Trino
unreachable (`product-verify: REFUSED: ...`, `product-verify: unreachable: ...`).

Reading a FAIL:

| Line | Most likely | Do |
|---|---|---|
| `landing`: `a hole no k2.kafka-gaps records`, `an overlap`, `added <n> rows; its spans hold <m> offsets` | a defect in the connector, or a hand-made write | hold the product ([hold-a-product.md](./hold-a-product.md)) and investigate; nothing here repairs a landing |
| `landing`: `changed the rows from <x> to <y> and is no connector commit` | a write that is not the connector's (a compaction must keep the count) | as above |
| `bronze`: `<n> staged + <m> skipped, but its range holds <k> landing rows` | bronze's staging lost or doubled rows | hold the product, then [rebuild-a-product.md](./rebuild-a-product.md) restages bronze from the landing |
| `level`: `bronze is staged behind the landing's end ...; a batch waits on its audit branch, unpublished` | a failed audit blocks bronze | [kafka-batch-blocked.md](./kafka-batch-blocked.md) |
| `level`: `bronze is staged behind the landing's end ...` with no batch waiting | the five-minute run is between its steps | `make product-verify PRODUCT=... WAIT=900` |
| `tag`: `not built yet`, `has no audited tag yet`, `... but its newest audited run record says <id>` | a first build pending, or a tag moved without its record | read the build's run records ([airflow-scheduling.md](./airflow-scheduling.md) section 4); a tag moved otherwise is the drift check's case ([verify-access-after-a-release.md](./verify-access-after-a-release.md)) |

**Measured**, live, rc.1: every check PASS; its time not recorded.

---

## 2. Silver against a recompute from the landing

**When**, after a change to `silver_trade`'s model or to the connector, after a rebuild, or before a
tag.

**Expected behaviour**, `scripts/parity_silver.py` recomputes `market-capture.kraken-trades`'
`silver_trade` (the only product it recomputes) from the landing's bytes with its own Avro reader and
JSON parser, not from what dbt or Spark made of them, at the snapshots one build read, and compares
keys, counts and every column with the silver that build published. The pin is a build's run records:
`RUN=<dag_id>/<run_id>`, by default the newest audited build of `silver_trade`; the same `RUN` compares
the same snapshots for as long as they are held. It reads Iceberg's files straight through Lakekeeper
with the `tools` storage key alone, which reads every warehouse and writes none, and never through
Trino, where no principal reads the venue's rows.

**Recovery**, not yet run on the live stack:

```bash
make parity-silver PRODUCT=market-capture/kraken-trades           # RUN=<dag_id>/<run_id> pins another build
```

Its lines, from the code:

```text
parity-silver: pin run <run id> (its audited run record): <silver>@<snapshot>, <master>@<snapshot>, lake.kraken_trades.bronze_trade@<snapshot> (lake.kraken_trades.landing@<snapshot> below {<through-offsets>}); RUN=<run id> compares these again
INFO  landing                  <n> row(s) read: <n> skipped by the filter, <n> kept; <n> trade frame(s) with <n> trade(s): <n> frame(s) in silver, <n> held (a symbol unresolved), <n> rejected (a trade untyped)
INFO  rows                     recomputed <n>, <silver> <n>
PASS  undecoded                none
PASS  <class>                  none
parity-silver: market-capture.kraken-trades: every class empty over <n> row(s)
```

The classes: `undecoded` (a kept landing frame whose envelope does not decode, which bronze's audit
should have stopped), duplicate keys on either side, `missing` (in the recompute, not in silver),
`extra` (in silver, not in the recompute), and one per column whose values differ on a key both hold.
A failing class prints `FAIL  <class> <n> key(s); first: <keys>`. Exit 0 every class empty; 1 a class
holds a difference (`parity-silver: <product>: <n> class(es) differ`); 2 refused, as
`parity-silver: REFUSED: <why>`:

| Refusal | Do |
|---|---|
| `this image has no DuckDB (a tools image built before W0-11)` | the live release's `tools` image predates DuckDB (rc.1's does): deploy a release that has it |
| `parity-silver: K2_TOOLS_S3_ACCESS_KEY is not set: load the stack's .env first (set -a && . ./.env && set +a)` (`make` prints it, with no `REFUSED:`) | load `.env` |
| `no audited run record of <table>...: nothing built to compare` | no build yet, or `RUN` names none |
| `bronze's main lineage holds no connector commit before the build started (expired, or none yet)` | pin a newer build with `RUN=` |

**Measured**, throwaway only, 2026-11-28 (`duckdb-landing.sh` claims 4 and 6): clean on a silver whose
tested snapshot holds position deletes, and `exit 1` with `conn_msg_seq` and `missing` the only failing
classes on a planted release. The tools one-shot's largest `memory.peak` was 105 MiB of its 2 GiB, on a
lake of synthetic frames: a fixed cost, not a sizing.

---

## 3. Candles against a recompute from canonical-trades

**When**, as section 2, for `market-analytics/candles`.

**Expected behaviour**, `scripts/parity_ohlcv.py` recomputes every timeframe (1m, 5m, 1h, 1d) from
`canonical-trades` at the snapshot one candles build read, written from the candle rules and not from
the models' SQL, and compares every window and value with the tables that build published. The pin is
that build's run records (`RUN=`, by default the run of `ohlcv_1m`'s newest); every timeframe of the run
must have read the same snapshot. `through_load_seq` is bookkeeping and is not compared.

**Recovery**, not yet run on the live stack:

```bash
make parity-ohlcv                                                   # RUN=<dag_id>/<run_id> pins another build
```

```text
parity-ohlcv: pin run <run id> (its audited run records): <trades>@<snapshot>, ohlcv_1d@<snapshot>, ohlcv_1h@<snapshot>, ohlcv_1m@<snapshot>, ohlcv_5m@<snapshot>; RUN=<run id> compares these again
INFO  trades                 <n> canonical row(s), <n> flagged is_duplicate and not counted
INFO  ohlcv_1m.rows          recomputed <n>, <table> <n>
PASS  ohlcv_1m.<class>       none
parity-ohlcv: market-analytics.candles: every class empty over 4 timeframes
```

Exits and refusals as section 2. A window whose every trade turned duplicate keeps its old row (a merge
deletes nothing) and is reported `extra`, as it is.

**Measured**, throwaway only, 2026-11-28 (`duckdb-landing.sh` claims 5 and 7): clean after a build that
rewrote windows with position deletes, and `exit 1` with `ohlcv_5m.volume` the only failing class on a
planted release.

---

## Known limits

- **Parity reads the whole landing** below bronze's through-offsets on every run (DuckDB given 1GB and
  2 threads, spilling to `/tmp`, inside the `tools` one-shot's 1 CPU / 2G). Its time and memory at live
  size are unmeasured; the first live run measures them, and
  [ADR-010](../adr/ADR-010-resource-budget.md)'s Outcome says when the limit moves.
- **Parity needs a release whose `tools` image has DuckDB**, which rc.1's does not.
- `make product-verify` does not prove what snapshot expiry cut, and reads no row: a row whose values
  are wrong but whose count is right passes it. Section 2 is the check for that.

---

## Failure modes / incidents

None yet.

---

**Last verified:** section 1 on the live stack under `v3.0.0-alpha.6-rc.1` during the soak's first day
(2026-11-28 to 29), every check PASS. Sections 2 and 3 on a throwaway lake, 2026-11-28: `bash
scripts/verify/duckdb-landing.sh <release dir>`, `7 PASS, 0 FAIL in 546 s`; live: not yet.

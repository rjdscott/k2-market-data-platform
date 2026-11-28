# Runbook: Rebuild a product from what it is built from

Use this when a product's outputs are wrong or lost and must be built again from their inputs, or
when `make restore` left a product for rebuild ([lake-backup-and-restore.md](./lake-backup-and-restore.md)
section 3a). `make rebuild PRODUCT=<domain/name>` rebuilds one product of the live release under a
rebuild hold: a Kafka product's bronze is restaged from its landing, then every dbt output is built
again by `k2-dbt build --full-refresh`. It does not cover a batch that failed its audit
([kafka-batch-blocked.md](./kafka-batch-blocked.md)), nor a file product's bronze, which nothing
rebuilds from its landing (Known limits). The landing can feed a restage because a Kafka product's
landing is never expired ([ADR-063](../adr/ADR-063-kafka-product-landing-per-product-bronze-staged-by-offset.md));
the holds and their one writer are [hold-a-product.md](./hold-a-product.md)'s.

> **Built on the phase 004 exit branch, not in the live release.** `make rebuild` (`scripts/rebuild.py`,
> `mk/rebuild.mk`) and `k2-dbt build --full-refresh` came after `v3.0.0-alpha.6-rc.1` was cut: that
> release's dbt runner refuses `--full-refresh`, and `make rebuild` refuses such a release before its
> lock, changing nothing (section 0).
> Last verified: throwaway, 2026-11-28 (`bash scripts/verify/kafka-path.sh <release dir> d`, `8 PASS,
> 0 FAIL`, and `bash scripts/verify/restore-path.sh <release dir>` claim 17); live: not yet. On the
> throwaway, Airflow and the scheduler's one-shot launcher are stand-ins; the holds, Spark, the dbt
> wrapper and the lake are real. Every message quoted below is the code's own text.

**Run every command from the repo root of the live checkout, on a release that carries `make rebuild`,
with `set -a && . ./.env && set +a` loaded.** `make rebuild` runs on the host (`uv` with
`trino==0.340.0`), writes its hold over TLS on `localhost:${K2_TRINO_PORT:-8443}` as `svc.operator`,
and reaches Airflow and Spark by `docker exec`.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Rebuild a product | none set | throwaway only, not timed apart (group d); live: not yet; a Kafka product's restage at live size unmeasured |
| 2 | The products a restore left for rebuild | none set | throwaway only (`restore-path.sh` claim 17); live: not yet |
| 3 | A rebuild that failed, or was killed | < 5 min | throwaway only (group d claims 3 and 4); live: not yet |

---

## 0. Before the first rebuild

- **The operator's password file**, once per host: `make password PRINCIPAL=svc.operator TO_FILE=1`
  ([hold-a-product.md](./hold-a-product.md) section 0). Without it every run refuses, exit 2:

  ```text
  rebuild: REFUSED: no password file for svc.operator at <K2_SECRETS>/principals/svc.operator.password: the operator makes it once with `make password PRINCIPAL=svc.operator TO_FILE=1` (host-held: no container mounts it)
  ```

- **A live release that knows `--full-refresh`.** It came with commit `df043fc`. `make rebuild` checks
  it itself before its lock, from the live release's own wrapper, never the checkout's: a sealed release
  carries no copy of `k2_dbt.py`, so it asks the dbt-runner image the release's `compiled/oneshots.json`
  pins, with no network and nothing pulled (`docker run --rm --network none --pull never --entrypoint
  k2-dbt <image> build --help`), and refuses unless its options list `--full-refresh`. Against rc.1:

  ```text
  rebuild: REFUSED: release <id>'s k2-dbt (<image>) takes no --full-refresh (step W1-15a): a rebuild would replace bronze and then have its build refused; deploy a release whose dbt-runner has it
  ```

  By hand, `make release-status` names the live release and its tag, and for rc.1:

  ```bash
  git merge-base --is-ancestor df043fc v3.0.0-alpha.6-rc.1; echo $?     # printed 1: not an ancestor
  ```

---

## 1. Rebuild a product

**When**, a product's outputs are wrong or lost and its inputs are sound: a model fixed through a
release, a table damaged by hand, or a rebuild table a restore recreated empty.

**Expected behaviour**, in order, stopping at the first step that fails (`scripts/rebuild.py`, header):

0. **check**: the live release's own `k2-dbt` takes `--full-refresh` (section 0), else refused before
   anything changes.
1. **lock**: a `rebuild` hold on the product, refused while one is open; its DAG paused unless it
   is already, then every task of its running runs waited for (up to 900 s). From here the
   product's runs fail in `resolve`, and nothing else builds it.
2. **bronze**, a Kafka product's only: bronze's `audit` branch dropped, then the release's own DDL
   run as `CREATE OR REPLACE TABLE`, which keeps the table and its old snapshots and leaves `main`
   with no snapshot; then the product's own `connector.py` `load`, `audit` and `publish` restage it
   from the landing's first offset as **one batch of the whole landing**. From the replace until
   that publish, **bronze reads no row** (the product is locked, so no build reads it either). A
   load that stages nothing stops here, so silver is never rebuilt from an empty bronze.
3. **build**: `k2-dbt build --full-refresh` in a dbt-runner one-shot started by the release's own
   `oneshot.py` in the scheduler, named `k2-dbt-runner-<dag>-rebuild`, with a DAG build's image,
   mounts, principals and limits. A failed build leaves every output at its `audited` tag.
4. **unlock**: the lock released only if step 1 placed it; the DAG unpaused only if step 1 paused it
   and no other hold is open.

The run id is `<dag>/rebuild__<the UTC start, to the second>`; the restage's bronze commit, its
`audit_checks` rows and the build's run records carry it.

`canonical-trades` numbers the rows of a full refresh after the highest `load_seq` the table held
(`contracts/market-analytics/canonical-trades/models/trades.sql`, its header), so `candles`' next
scheduled build rewrites every window with nothing more to run: rebuild `canonical-trades` alone,
not `candles` after it (kafka-path.sh group d, claims 1 and 2).

**Recovery**, not yet run on the live stack:

```bash
make rebuild PRODUCT=market-analytics/canonical-trades
```

`PRODUCT` is `<domain>/<name>`, `<domain>.<name>` or a name one product has. For a Kafka product
(`market-capture/kraken-trades`) its own lines are these, among the connector's and the build's output:

```text
rebuild: market-capture.kraken-trades locked (rebuild hold); run kraken_trades/rebuild__<UTC> on release <release id>
rebuild: waiting for <task> (run <run id>, <state>)
rebuild: spark: ALTER TABLE `lake`.`kraken_trades`.`bronze_trade` DROP BRANCH IF EXISTS `audit`
rebuild: spark: CREATE OR REPLACE TABLE "lake"."kraken_trades"."bronze_trade" (
rebuild: restage load done
rebuild: restage audit done
rebuild: restage publish done
rebuild: market-capture.kraken-trades rebuilt: {<the build's result>}
rebuild: released the lock on market-capture.kraken-trades; DAG kraken_trades unpaused
```

The `waiting for` line appears only while a run drains, and the two `SET spark.sql.` lines of the
release DDL print between the `ALTER` and the `CREATE`. The last line is one of: `DAG <dag> unpaused`;
`DAG <dag> is left as this command found it` (it was paused before); `DAG <dag> stays paused: a
<kind> hold is open`. Exits: 0 rebuilt; 1 a step failed (said, with what it left); 2 refused before
anything changed; 130 interrupted.

Refused before the lock, exit 2, nothing changed:

| Message after `rebuild: REFUSED:` | Why |
|---|---|
| `<product> is paused in the live release: nothing rebuilds it` (or `retired`) | a paused Kafka load stages nothing |
| `<product> has no dbt build to rebuild` | the product builds no output |
| `<arg> is not a product of the live release (...)`, `<arg> is ambiguous: ...` | `PRODUCT` names none, or several |
| `<product> is held by a restore (make restore at <time>); rebuild after it` | a restore is running, or one stopped with its holds kept: finish it first |
| `<product> has a rebuild lock open already: another make rebuild runs, or one was killed; ...` | section 3 |
| `release <id>'s k2-dbt (<image>) takes no --full-refresh (step W1-15a): a rebuild would replace bronze and then have its build refused; deploy a release whose dbt-runner has it` | the live release predates `--full-refresh` (rc.1): deploy a later one |
| `could not read release <id>'s k2-dbt (<image>): exit <n>: ...` | its pinned dbt-runner image is not on this host (nothing is pulled) or did not answer: `docker images` |
| `release <id> pins no dbt-runner image (compiled/oneshots.json)` | the live release pins no dbt runner: deploy one that does |

Then read the product: [verify-a-kafka-product.md](./verify-a-kafka-product.md) for a Kafka product
and the products downstream of it.

**Measured**, throwaway only, 2026-11-28: group d claims 1 (canonical-trades rebuilt, every row
again, `load_seq` after the old maximum), 2 (candles' next build rewrites every window, each value as
before), 5 and 6 (kraken-trades' bronze replaced, then restaged as one batch whose audit passes, the
landing untouched) and 8 (the lock held while the build ran, the DAG paused and unpaused). No time per
rebuild is recorded.

---

## 2. The products a restore left for rebuild

**Symptom**, `make restore` ended with `restore: rebuild <i> of <n>: make rebuild PRODUCT=<product>`
lines and `restore: each product above stays held (restore), its DAG paused, until its make rebuild
passes, in that order`.

**Expected behaviour**, each such product's restore hold carries the rebuild it waits for, so `make
rebuild` runs over that hold, and only once every product upstream of it that the restore left is
rebuilt. Its full refresh passed, the restore hold is released too, and the DAG unpaused if the
restore paused it and no other hold is open.

**Recovery**, in the order `make restore` printed, not yet run on the live stack:

```bash
make rebuild PRODUCT=market-capture.kraken-trades
make rebuild PRODUCT=market-analytics.canonical-trades
make rebuild PRODUCT=market-analytics.candles
```

Each ends with one more line than section 1's, for example:

```text
rebuild: released the restore hold on market-capture.kraken-trades; DAG kraken_trades unpaused, as the restore found it
```

or `DAG <dag> was paused before the restore, and is left so`. Out of order it is refused, exit 2:

```text
rebuild: REFUSED: market-analytics.canonical-trades reads market-capture.kraken-trades, which the restore left for rebuild too: make rebuild PRODUCT=market-capture.kraken-trades first
```

A file product whose bronze the restore recreated empty is refused and stays held: `<product>: the
restore recreated its bronze <table> empty, and a file product's bronze is its parser's (the fetch's):
a full refresh would publish its outputs empty; it stays held`. Nothing here rebuilds it (Known
limits).

**Measured**, throwaway only, 2026-11-28 (`restore-path.sh` claim 17): canonical-trades refused before
kraken-trades, then the three rebuilt in the printed order, each ending its restore hold; `ohlcv_1m`
then held every trade it held before the damage, and a scheduled cycle after took one more trade.

---

## 3. A rebuild that failed, or was killed

**The build failed**, exit 1:

```text
rebuild: FAIL: Failed: k2-dbt build --full-refresh exited <n> (<result>): its outputs are back at their audited tags (k2-dbt restores every output whose main moved), and what is above stays as it is
```

The lock is released and the DAG unpaused as in section 1. Read the build's run records
([airflow-scheduling.md](./airflow-scheduling.md) section 4), fix the cause through a release, rebuild
again. Proven on the throwaway, group d claim 3: a full refresh whose data test failed after it
replaced the table, `main` rolled back across the replace to the `audited` tag, every row as before,
an operator's hold left open and the DAG left paused.

**The restage failed**, exit 1, `rebuild: FAIL: Failed: restage <load|audit|publish> exited <n>: bronze
<table> holds no published row and its restage stopped; the landing is untouched, and a later load
stages bronze again from the landing's first offset (after a failed audit, once its batch is cleared
as connector.py says)`. Bronze reads no row until a load publishes. After a failed audit, clear the
batch ([kafka-batch-blocked.md](./kafka-batch-blocked.md) section 2) and rebuild again.

**Interrupted** (Ctrl-C), exit 130: the lock and the pause are left, since a step may still run in its
container. The message names what to check:

```bash
docker ps --filter name=k2-dbt-runner-<dag>-rebuild           # no container
docker exec k2-spark-iceberg ps -ef | grep connector.py        # no connector.py of the rebuild's run
make rebuild PRODUCT=<product> UNLOCK=1
```

```text
rebuild: released the rebuild lock on <product>; DAG <dag> is left as it is (`docker exec k2-airflow-scheduler airflow dags unpause <dag> -y` once it should run)
```

`UNLOCK=1` is refused while the one-shot runs (`k2-dbt-runner-<dag>-rebuild still runs: that rebuild is
alive; wait for it`) and when no lock is open. Proven on the throwaway, group d claim 4: a lock already
open refuses a rebuild before any pause or build, and `UNLOCK=1` ends it.

**The lock could not be released**, exit 1: `rebuild: LOUD FAILURE: the rebuild lock on <product> is
still open (...); its runs fail in resolve until make rebuild PRODUCT=<product> UNLOCK=1 ends it`.

**Measured**, not timed.

---

## Known limits

- **Not on rc.1.** The live release `v3.0.0-alpha.6-rc.1` (`8df5be3`) predates `--full-refresh`, so
  `make rebuild` refuses it before the lock (section 0; finding E2 of the phase 004 audit, resolved in
  `145a81a`). The refusal is proven by `tests/test_rebuild.py` against fakes; on a throwaway lake the
  check has only met this tree's dbt-runner image, which passes.
- **Unmeasured at live size.** A Kafka product's restage reads the whole landing in one Spark batch
  ([ADR-063](../adr/ADR-063-kafka-product-landing-per-product-bronze-staged-by-offset.md): the landing
  is never expired, by the owner's decision of 2026-11-28, so it only grows). Its time and memory are
  measured only on synthetic frames.
- **Bronze reads no row** from the replace until the restage publishes.
- **A file product's bronze** is its parser's (the fetch container's); nothing rebuilds it from its
  landing. A file product is rebuilt from silver up, its bronze as it is.

---

## Failure modes / incidents

None yet: nothing here has run on the live stack.

---

**Last verified:** 2026-11-28 on throwaway lakes: `bash scripts/verify/kafka-path.sh <release dir> d`
(`8 PASS, 0 FAIL`) and `bash scripts/verify/restore-path.sh <release dir>` claim 17 (`19 PASS, 0 FAIL in
671 s`, at `fc353b9`); `git merge-base` in section 0 run in this checkout. Live: not yet.

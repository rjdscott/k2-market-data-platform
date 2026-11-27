# Runbook: Hold a product, and release it

Use this to stop one product's runs for an incident, without a release: `make hold` writes an
operator hold and pauses the product's DAG, and `make unhold` ends it. A hold is not a lifecycle
state (pausing a product for good is a release that sets its `status: paused`); it is recorded, it
survives every deploy, and nothing but `make unhold` ends it. Why holds are append-only rows with one
writer: [`phase-001-interfaces.md`](../development/phase-001-interfaces.md), "Holds, 2026-11-28", and
[ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)'s Outcome
of 2026-11-28.

> **From the release that carries phase 004 (`v3.0.0-alpha.6`, not yet tagged or deployed).** The
> holds table, `make hold` and `make unhold`, `resolve`'s refusal and deploy's respect for a hold are
> built on branch `phase/004-005-kraken-and-enriched` and unit-tested (`tests/test_hold.py`,
> `tests/test_deploy.py`, `docker/airflow/tests/test_factory.py`). **No command below has run against a
> stack yet**; no output is shown where none was read. Every message quoted is the code's own text.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a`
loaded.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 0 | The operator's password file, once | < 1 min | not yet run |
| 1 | Hold a product | < 1 min | not yet run |
| 2 | What a held product does | n/a | not yet run |
| 3 | Release the hold | < 1 min | not yet run |

---

## 0. Once per host: the operator's password file

**When**, before the first hold, after the release that carries phase 004 is deployed.

**Expected behaviour**, the holds have one writer, the principal `svc.operator`, the operator's own
Trino login from the host: INSERT on the holds table through `governance_build` and nothing else, its
reads through the read-only `governance` catalog. Its password file lives under `${K2_SECRETS}` and no
compose service or one-shot mounts it, so nothing holding `svc.runs`'s password (every dbt-runner
one-shot and the scheduler) can write or end a hold. Deploy's live access suite logs in as
`svc.operator` for its rows, so a deploy whose live suite runs needs the file too.

**Recovery**, not yet run:

```bash
make password PRINCIPAL=svc.operator TO_FILE=1
make principals-status                 # svc.operator shows a password line and a password file
```

Without the file, `make hold` and `make unhold` refuse with exit 2:

```text
hold: REFUSED: no password file for svc.operator at <K2_SECRETS>/principals/svc.operator.password: the operator makes it once with `make password PRINCIPAL=svc.operator TO_FILE=1` (host-held: no container mounts it)
```

**Measured**, not yet run.

---

## 1. Hold a product

**When**, a product is producing wrong data, its source is misbehaving, or it must not run while you
work on it.

**Expected behaviour**, `PRODUCT` is `<domain>/<name>`, `<domain>.<name>`, or a bare name only one
product of the live release has; `REASON` is required, at most 200 characters. The hold row is written
first and is the truth; then the DAG is paused (`docker exec k2-airflow-scheduler airflow dags pause
<dag> -y`). A run already going when you hold finishes its current task; its later tasks and every
new run fail in `resolve`.

**Recovery**, not yet run:

```bash
make hold PRODUCT=market-capture/kraken-trades REASON="<why, at most 200 characters>"
```

On success it prints, from `scripts/hold.py`:

```text
held market-capture.kraken-trades (DAG kraken_trades paused): no deploy will unpause it; `make unhold PRODUCT=market-capture.kraken-trades` ends it
```

| Exit | Line | Meaning |
|------|------|---------|
| 0 | `held <product> (DAG <dag> paused): ...` | held and paused |
| 1 | `hold: LOUD FAILURE: <product> is held, but DAG <dag> was NOT paused (...)` | the hold stands and `resolve` refuses every run; pause by hand with `docker exec k2-airflow-scheduler airflow dags pause <dag> -y` |
| 2 | `hold: REFUSED: <product> is held already by <who> at <time>: <reason>` | one operator hold per product; a rebuild or restore lock is separate and does not refuse this |
| 2 | `hold: REFUSED: <arg> is not a product of the live release (...)` or `... is ambiguous: ...` | name it as `<domain>/<name>` |
| 2 | `hold: REFUSED: REASON is empty: a hold carries its reason` | give `REASON=` |

**Measured**, not yet run.

---

## 2. What a held product does

**Symptom**, the product's DAG is paused and its catalog page stops moving.

**Detection**, from the product's compiled alert rules (`compiled/prometheus/product-alerts.yml`):
`ProductStale_<dag>` fires once the stale window passes with no successful run (three schedule
intervals, at least one interval plus the timeout: 15 minutes for a five-minute product), and
`ProductRunFailed_<dag>` fires for each run `resolve` refuses. Silence them for the hold's expected
length ([where-an-alert-goes.md](./where-an-alert-goes.md) section 3); not yet run for these rules:

```bash
docker exec k2-alertmanager amtool --alertmanager.url=http://localhost:9093 silence add \
  product=market-capture.kraken-trades --duration=4h --author=owner --comment="<the hold's reason>"
```

**Expected behaviour**:

- **A run that starts anyway** (a manual trigger, one queued before the pause, a refresh run that
  a degraded build of a product reading this one starts: `kraken-trades` starts `kraken-instruments`) fails in its first task, `resolve`, which reads the hold as `svc.runs`
  through the release the run pinned, and prints:

  ```text
  run refused: market-capture.kraken-trades is held (operator hold by <who> at <time>): <reason>; it runs again once the hold is released
  ```

  A read of the holds that fails fails `resolve` too: a hold is never skipped because the table was
  unreachable. `record` then skips, as after any failed `resolve`.
- **A deploy never unpauses it.** Deploy unpauses only the DAGs it paused itself for its drain
  (`deploy-paused.json`), and skips any whose product is held, printing `kept paused, held: <dag>
  (operator hold by <who> at <time>: <reason>); each stays paused until its hold is released`. If the
  holds cannot be read, deploy keeps every DAG it paused paused.
- **For a Kafka product**, the topic keeps filling while the product is held. The broker evicts after
  48 hours or 2 GiB per partition; a hold longer than that ends in the below-log-start failure at the
  first run after release, and an accept-data-loss run ([accept-data-loss.md](./accept-data-loss.md)).
- **A product downstream** of the held one reads its last tested snapshot and its page still says
  `complete`: the product state counts an input as missing only when the release says the input
  product is `paused` or it has no tested snapshot (`platform/runs/k2runs.py`, `state`), and a hold is
  neither. Its run records carry each input's `lagMinutes`, which grows while the hold lasts.

To see the open holds (as `svc.catalog`, `trino_cli` from [tls-and-passwords.md](./tls-and-passwords.md),
`NET=${K2_COMPOSE_PROJECT}_k2-net`), not yet run:

```bash
trino_cli svc.catalog "SELECT product_id, kind, reason, held_by, held_at, released_at, released_by
  FROM governance.run_records.holds ORDER BY held_at DESC LIMIT 20"
```

A hold is open while no row with `released_at` set carries its `(product_id, kind, held_at)`. The
catalog site does not show holds at this release.

---

## 3. Release the hold

**When**, the cause is fixed.

**Expected behaviour**, `make unhold` writes the release row (a copy of the hold with `released_at` and
`released_by` set; no row is ever updated or deleted), then unpauses the DAG, unless a `rebuild` or
`restore` lock on the same product is still open, which `make unhold` never ends.

**Recovery**, not yet run:

```bash
make unhold PRODUCT=market-capture/kraken-trades
docker exec k2-airflow-scheduler airflow dags list-runs kraken_trades     # the next scheduled run starts
```

| Exit | Line | Meaning |
|------|------|---------|
| 0 | `released <product> (DAG <dag> unpaused)` | free, and scheduled again |
| 0 | `released the operator hold on <product>; DAG <dag> stays paused: a <kind> hold is still open` | a rebuild or restore lock remains; its own command releases it |
| 1 | `unhold: <product> is released, but DAG <dag> was NOT unpaused (...); docker exec k2-airflow-scheduler airflow dags unpause <dag> -y` | run that line |
| 2 | `unhold: REFUSED: <product> has no operator hold open` | nothing to release |

Remove the silence of section 2 once the product's next run succeeds.

**Measured**, not yet run.

---

## Failure modes / incidents

None yet: nothing here has run against a stack.

---

**Last verified:** not yet. Every section is written from the code on branch
`phase/004-005-kraken-and-enriched` at 2026-11-28, unit-tested only; the first deploy of the release
that carries phase 004 is the first chance to run sections 0 to 3 (plan 004's line `make hold
PRODUCT=kraken-trades REASON=drill && make unhold PRODUCT=kraken-trades`).

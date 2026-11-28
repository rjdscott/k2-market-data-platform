# Runbook: Onboard a Kafka product

Use this to add a product that reads a Redpanda topic its own domain's capture writes (connector kind
`kafka`): its documents, the bronze shape the connector stages, the checks an author meets, the
password files, the deploy, the unpause order and its first run. The worked example is
`market-capture/kraken-trades` and the four products built on it. A product fetched from a public
endpoint is [onboard-a-public-api-product.md](./onboard-a-public-api-product.md); a file is
[onboard-a-file-product.md](./onboard-a-file-product.md), whose sections 2, 3 and 7 (the checks with no
container, the pull request, the catalog page) apply here unchanged. Why the path is shaped this way:
[ADR-063](../adr/ADR-063-kafka-product-landing-per-product-bronze-staged-by-offset.md) (Proposed).

> **From the release that carries phase 004 (`v3.0.0-alpha.6`, not yet tagged or deployed).** Sections
> 1 and 2 are written from the documents and checks on branch `phase/004-005-kraken-and-enriched`;
> `make test-dbt SELECTOR=candles` built six products in their input order (`candles` and the five it
> reads through its inputs, `currency-master` among them) with every test passing on a throwaway lake
> on 2026-11-28, on empty bronze: no row of a frame was built. **Sections 3 to 5 have not run anywhere**;
> no output is shown for them.

**Run every command from the repo root.** Sections 3 to 5 need the live checkout at the tag with
`set -a && . ./.env && set +a` loaded.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Write the documents | a day | not measured: a person's work |
| 2 | Check them, no container | < 5 min | not measured for these products |
| 3 | Release and secrets | < 10 min | not yet run |
| 4 | Deploy, then unpause in order | < 15 min | not yet run |
| 5 | First runs | the backlog's | not yet run |

---

## 1. Write the documents

**When**, the topic exists, its domain lists it, and its envelope is a schema the registry serves.

**Expected behaviour**, the product's directory under `contracts/<domain>/<product>/` holds what a file
product's does ([onboard-a-file-product.md](./onboard-a-file-product.md) section 1: the names follow
from the domain and product), with three differences:

- **The topic is the domain's.** `contracts/domains.yaml` lists, under the domain, every topic its
  capture writes (`market-capture` lists the nine `market.crypto.v3.{raw,trades,book}.<venue>`
  topics). A product reads only a topic its own domain lists, and one product reads each topic.
- **The connector block**, in `product.odps.yaml`'s `customProperties`, as `kraken-trades` writes it:

  ```yaml
  - property: connector
    value:
      kind: kafka
      brokers: "redpanda:9092"
      registry: "http://redpanda:8081"
      topic: market.crypto.v3.raw.kraken
      envelope:
        format: avro-confluent
        contract: market-capture.kraken-trades.bronze_trade
      filter:
        field: stream
        in: [trade]
      payload: json
      startFrom: earliest-retained
      maxOffsetsPerPartition: 150000
  - property: schedule
    value:
      cron: "*/5 * * * *"
      timeoutMinutes: 10
  ```

  The brokers and registry are services of the stack's compose file on a network the Spark container
  joins; no field carries a credential. `maxOffsetsPerPartition` and `timeoutMinutes` are engineering
  bounds until the first measured runs set them: say so in a comment beside each, as the product does.
- **One bronze table**, `<bronze>.odcs.yaml`, `build: connector`, whose columns are in this order:
  `kafka_partition` integer and `kafka_offset` bigint (the primary key, positions 1 and 2),
  `kafka_timestamp`, `schema_id` integer, then the envelope's fields by their Avro names and types
  (for `RawMessage`: `exchange`, `stream`, `symbol`, `recv_ts_ns`, `conn_id`, `conn_msg_seq`,
  `payload`), then `batch_id`, `landed_at`, `run_id`, `release_id`. `schema_id` and the envelope
  columns are never `required`: a frame that does not decode is staged with them null and fails the
  audit's `parses`. The landing, `lake.<fence>.landing`, is the platform's and needs no document; it
  is in the backup set as `restore` and is never expired.

The dbt outputs follow the file products' rules. Each input port states `required`, and for an
optional one `whenAbsent` (`fail` for a consumer built as a `table` model, `empty` for an
incremental one) and `maxLagMinutes`. A product that should start an upstream product's run when its
build is degraded names it in the property `refresh` (`kraken-trades` names
`market-capture.kraken-instruments`) and has a `warn`-severity test.

**Measured**, not measured: a person's work.

---

## 2. Check them, no container

**When**, before the pull request, and after every change.

**Expected behaviour**, the commands of [onboard-a-file-product.md](./onboard-a-file-product.md)
section 2. A finding prints as `<severity> <code> <path> <pointer>: <message>`; any `error` exits 1.
The checks a Kafka product meets beyond a file product's:

| Code | Means |
|------|-------|
| `connectors.kafka-bronze-shape` | not exactly one connector table, or its columns are not the shape of section 1 by name, type, order and key |
| `connectors.envelope-unresolved` | `envelope.contract` names no table contract of the set |
| `connectors.topic-not-owned` | the topic is not one the product's own domain lists in `contracts/domains.yaml` |
| `connectors.topic-claimed-twice` | another product reads the topic, or two domains list it |
| `connectors.host-outside-stack` | a broker or the registry is not a compose service on a network of the Spark container |
| `refresh.target-unresolved`, `-self`, `-unscheduled`, `-not-upstream`, `-chains` | the `refresh` target is not another scheduled, non-retired product upstream through input ports, or declares a refresh of its own |
| `refresh.rule-not-warn` | a rule `refresh.rules` names is not a `warn`-severity rule of the product, so it could never start the refresh |
| `docs.empty-input-into-table` | an input port with `whenAbsent: empty` feeds a model that is not incremental, which would rebuild it empty |
| `access.input-undeclared` | a model reads a `k2_input` the output port does not declare, directly or through a sibling's `ref()` |
| `access.inherit-role` | an output offers a role its inputs do not offer, nor a role it is declared `within` (`contracts/access/roles.yaml`) |
| `docs.terms-missing` | a grant outside the owning team while the product, or any product upstream of it, records no terms |

Then build the product and its inputs on a throwaway lake (a container job: one at a time on this
host):

```bash
make test-dbt SELECTOR=<selector>          # e.g. SELECTOR=candles builds its inputs first
```

A Kafka product's own connector is proven by `bash scripts/verify/kafka-path.sh <release dir> a|b|c` on
a fixture product, not on yours; the access rows for its landing and bronze go in
`tests/access/expectations.yaml` in the same pull request (both denied to every reader).

**Measured**, not measured for these products.

---

## 3. Release and secrets

**When**, the pull request is merged and an annotated tag is approved.

**Expected behaviour**, as a file product ([onboard-a-file-product.md](./onboard-a-file-product.md)
section 4): each dbt product needs its builder's and publisher's password files before the deploy, and
preflight refuses without them. For the release that carries phase 004 that is five pairs, and the
operator's file for holds, once ([hold-a-product.md](./hold-a-product.md) section 0). A reader's
password and key wait for its grant, which waits for the venue's terms.

**Recovery**, not yet run:

```bash
make release TAG=<tag>
for f in kraken_instruments security_master kraken_trades canonical_trades candles; do
  make password PRINCIPAL=dbt_$f TO_FILE=1 && make password PRINCIPAL=pub_$f TO_FILE=1
done
make password PRINCIPAL=svc.operator TO_FILE=1
make principals-status
```

**Measured**, not yet run.

---

## 4. Deploy, then unpause in order

**When**, the release is compiled and its secrets written.

**Expected behaviour**, the deploy runs as [deploy-a-release.md](./deploy-a-release.md) section 3
says. Every DAG a release adds is **created paused**, and deploy never pauses or unpauses it: its plan
lists them as `new in this release, with no run to drain, never paused or unpaused by deploy:
<dags>`. Unpause each after `make release-verify` exits 0, inputs first, so each product's first
build finds its inputs tested: instruments, security master, trades, canonical trades, candles.
`kraken_trades`' build requires `venue_symbol` tested (else its build exits 4), and
`security_master`'s requires `silver_listing`.

**Recovery**, not yet run:

```bash
make deploy RELEASE=<release id> PLAN=1
make deploy RELEASE=<release id>
make release-verify
S=k2-airflow-scheduler
docker exec $S airflow dags unpause kraken_instruments -y
docker exec $S airflow dags trigger kraken_instruments            # its cron is hourly: run it now
# when kraken_instruments' run succeeded:
docker exec $S airflow dags unpause security_master -y && docker exec $S airflow dags trigger security_master
# when security_master's run succeeded:
docker exec $S airflow dags unpause kraken_trades -y
docker exec $S airflow dags unpause canonical_trades -y
docker exec $S airflow dags unpause candles -y
docker exec $S airflow dags list                                  # is_paused False for the five
```

**Measured**, not yet run.

---

## 5. First runs

**When**, the DAGs are unpaused.

**Expected behaviour**, `kraken_trades`' first run plans from the log start of each partition, so it
reads the topic's whole retained backlog, at most `maxOffsetsPerPartition` per partition per run; a
run that hits the bound leaves a `backlog` in load's result, and the next run continues. Its tasks:
`resolve`, `load` (land, then stage), `audit`, `publish`, `build`, `refresh` (skipped unless the build
is degraded), `expire`, `maintain` and `compact` (once a day), `record`. A trade whose symbol the
security master does not resolve waits in `silver_trade_unresolved`, the `warn` test
`unresolved_rows_zero` fails, the build publishes degraded and starts a `kraken_instruments` run, at
most one an hour however many builds stay degraded (the venue's API allows a few calls an hour). A
frame the model cannot type is kept in `silver_trade_rejected` instead: `rejected_frames_zero` fails
and the build publishes degraded, but starts no instruments run, which could not cure it (section 5a).

What the first runs must confirm, and where each is read:

- **The backlog drains**: load's result shows `backlog` 0 per partition after a normal run, and the
  run time stays inside the ten-minute timeout (W0-12's prediction; the soak measures it).
- **Symbols resolve**: the first live `AssetPairs` fetch's `wsname` values, recomposed through
  `seeds/code_exceptions.csv`, must equal the `symbol` the capture writes on the trades stream for
  every instrument in `config/instruments.yaml`
  ([onboard-a-public-api-product.md](./onboard-a-public-api-product.md) section 4). An unresolved
  spelling shows as rows in `silver_trade_unresolved` and the product `degraded` on its catalog page.
- **The audit passes on control frames**: `accounting` counts the skipped heartbeat and status frames.

**Recovery**, a red task: [airflow-scheduling.md](./airflow-scheduling.md) section 2; `load` refused
below the log start: [accept-data-loss.md](./accept-data-loss.md); `audit` failed or `load` refused
behind a failed batch: [kafka-batch-blocked.md](./kafka-batch-blocked.md).

**Measured**, not yet run.

## 5a. A rejected frame

**Symptom**, the product `degraded` with `test.k2.rejected_frames_zero.<hash>` in the build's
`warned`: a trade frame `silver_trade` cannot type (its payload not JSON, no `data` array, a trade
lacking a field or holding one that does not cast) is in `silver_trade_rejected`, whole, with its
`reason` and its bytes, and none of its trades is in `silver_trade`. The rest of the venue publishes,
and the build is degraded on every run while the frame stands; `refresh` is skipped, since a listing
cannot make the frame typeable.

**Recovery**, read the rows as the owning team (`kafka_partition`, `kafka_offset`, `reason`,
`payload`) and decide whether the venue changed its format or sent one bad frame. A rejected frame is
taken in by the first build of a release whose `silver_trade` model types it, with no other step,
since every build reads again each frame `silver_trade` does not hold; a frame no model can type
stays, and the product degraded, until that frame leaves `bronze_trade`.

**Last verified:** not yet.

---

## Failure modes / incidents

None yet: nothing here has run against a stack.

---

**Last verified:** sections 1 and 2 against the documents and checks on branch
`phase/004-005-kraken-and-enriched` on 2026-11-28, `make test-dbt SELECTOR=candles` passing on empty
bronze on a throwaway lake. Not yet: sections 3 to 5, every command in them.

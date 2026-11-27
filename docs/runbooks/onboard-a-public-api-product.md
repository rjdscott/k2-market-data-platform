# Runbook: Onboard a public-API product

Use this to add a product fetched from a public JSON endpoint (connector kind `public-api`): its
connector block, its parser and audits, the checks an author meets, and what its first live fetch
must confirm. The worked example is `market-capture/kraken-instruments`, Kraken's `AssetPairs`
listing, hourly. Everything after the fetch is a file product's path:
[onboard-a-file-product.md](./onboard-a-file-product.md) sections 2 to 7 apply unchanged, and the
deploy and unpause order is [onboard-a-kafka-product.md](./onboard-a-kafka-product.md) section 4. Why
fetches run this way: [ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md).

> **From the release that carries phase 004 (`v3.0.0-alpha.6`, not yet tagged or deployed).** The
> fetch kind is unit-tested with every socket faked (`make test-connectors`, `tests/test_fetch.py`),
> the parser on a hand-written synthetic response (`domains/market-capture/tests/`), and the product
> built by `make test-dbt SELECTOR=candles` on a throwaway lake on 2026-11-28, on empty bronze. **No
> fetch of the real endpoint has run**, so section 4 is a checklist for the first one, with no output.

**Run every command from the repo root.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Write the connector block and the parser | a day | not measured: a person's work |
| 2 | Check them | < 5 min | not measured for this product |
| 3 | Release, deploy, unpause | as a file product | not yet run |
| 4 | Confirm the first live fetch | < 30 min | not yet run |

---

## 1. Write the connector block and the parser

**When**, the endpoint is public, needs no key, and its terms of reuse have been read.

**Expected behaviour**, the connector block in `product.odps.yaml`, as `kraken-instruments` writes it:

```yaml
- property: connector
  value:
    kind: public-api
    url: "https://api.kraken.com/0/public/AssetPairs"
    allowHosts: [api.kraken.com]
    pagination: none
    minIntervalS: 1
    records: result
    parser: kraken_asset_pairs
- property: schedule
  value:
    cron: "17 * * * *"
    timeoutMinutes: 10
```

- `allowHosts` is the only place a host is allowed; the url's host must be one of them exactly.
- `pagination` is `none` at this release: one request per run. The fetch runs `next-link` (the `Link`
  header only) and `page` (`page=<n>` from 1), but the contract cannot yet say where a next link sits
  in a body or name the page parameter, so k2gen refuses both.
- `volatileFields` names fields stripped for the change test only, never from the landed bytes: a
  response unchanged but for them lands nothing (fetch exits 10). `kraken-instruments` names none,
  since its response carries no timestamp or counter; add them only when live fetches show landings
  that change nothing silver keeps.
- No `authHeaderEnv`: the fetch container holds no secret.

The parser, `domains/<domain>/parsers/<parser>.py`, is held to the file kind's contract
([onboard-a-file-product.md](./onboard-a-file-product.md) section 1): `parse(raw) -> list[dict]`,
standard library only, untrusted bytes, a raise is a parse failure (exit 30, bytes landed, nothing
staged). `kraken_asset_pairs` fails the fetch on the venue's `error` array, no pairs, a pair without
base or quote, or a websocket name claimed by two pairs. Its fixture is a hand-written response in the
publisher's format, never a fetched one (`python3 scripts/check_docs.py data` is the gate).

**Measured**, not measured: a person's work.

---

## 2. Check them

**When**, before the pull request.

**Expected behaviour**, the commands of [onboard-a-file-product.md](./onboard-a-file-product.md)
section 2, then `make test-connectors` and the parser's tests. The checks a public-API product meets
beyond a file product's:

| Code | Means |
|------|-------|
| `connectors.pagination-unsupported` | `pagination` is not `none` |
| `connectors.auth-unsupported` | the connector names `authHeaderEnv` |
| `connectors.url-host-not-allowed` | the url's host is not exactly one of `allowHosts` |
| `docs.connector-mismatch` | a table is `build: connector` and the product declares no connector, or a connector and no table it builds |
| `docs.terms-missing` | a grant outside the owning team while this product, or one upstream, records no terms |

```bash
make test-connectors
uv run --no-project --with pytest==9.2.0 pytest domains/market-capture -q
make test-dbt SELECTOR=kraken_instruments         # a container job
```

**Measured**, not measured for this product.

---

## 3. Release, deploy, unpause

**When**, the pull request is merged and a tag approved.

**Expected behaviour**, as [onboard-a-file-product.md](./onboard-a-file-product.md) sections 4 and 5:
the builder's and publisher's password files (`dbt_kraken_instruments`, `pub_kraken_instruments`),
then the deploy. The new DAG is created paused; unpause it first of the phase 004 products, and
trigger it once, since its cron is hourly ([onboard-a-kafka-product.md](./onboard-a-kafka-product.md)
section 4 has the order and the commands).

**Measured**, not yet run.

---

## 4. Confirm the first live fetch

**When**, the first `kraken_instruments` run has succeeded, before `security_master` is unpaused.

**Expected behaviour**, fetch's log names the request it made and the bytes it spooled; the run lands
one object, stages one bronze row per pair, and builds `silver_listing`. What must be confirmed by a
person, because no synthetic fixture can:

1. **The symbol spelling.** For every Kraken instrument in `config/instruments.yaml`, the pair's
   `wsname`, split at its one slash, each code upper-cased and mapped through
   `contracts/reference-data/security-master/seeds/code_exceptions.csv` (Kraken's older `XBT` is
   `BTC`, `XDG` is `DOGE`), must equal the `symbol` the capture writes on the trades stream. A
   spelling that differs needs a reviewed row in that seed, in a pull request; until then its trades
   wait in `silver_trade_unresolved` and `kraken-trades` shows `degraded`.
2. **No spelling claimed twice**: the security master holds a websocket name two pairs claim over
   overlapping spans (`venue_symbol_held`) for that venue only.
3. **The change test**: a second run an hour later with an unchanged listing exits 10 at fetch and
   lands nothing. A run that lands a new object while nothing silver keeps changed means a volatile
   field the contract should name.
4. **The run time**, against the ten-minute timeout set from the ECB product's runs.

**Recovery**, not yet run (`lake_sql` and `trino_cli` as in
[file-source-audit-failed.md](./file-source-audit-failed.md) and
[tls-and-passwords.md](./tls-and-passwords.md)):

```bash
docker exec k2-airflow-scheduler airflow dags list-runs kraken_instruments
lake_sql "SELECT count(*) FROM lake.kraken_instruments.bronze_pairs"
```

A fetch that failed or was refused: [connector-failure.md](./connector-failure.md); an audit that
failed: [file-source-audit-failed.md](./file-source-audit-failed.md).

**Measured**, not yet run.

---

## Failure modes / incidents

None yet: nothing here has run against a stack.

---

**Last verified:** sections 1 and 2 against the documents and tests on branch
`phase/004-005-kraken-and-enriched` on 2026-11-28 (`make test-dbt SELECTOR=candles` passing on empty
bronze). Not yet: sections 3 and 4, and every command in them.

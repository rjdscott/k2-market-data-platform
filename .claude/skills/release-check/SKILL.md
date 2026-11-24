---
name: release-check
description: Fresh-clone release gate — clone the repo to a temp dir, bring the whole stack up from nothing, wait for health, run smoke queries against every tier, run make test, sweep for forbidden strings and broken links, and report pass/fail. Use before tagging a release, before flipping the repo public, when the user says "release check", "is it ready to tag", "does a fresh clone still work", or after a change to compose, Dockerfiles, DDL or the quickstart.
---

# release-check — does a stranger's clone actually work?

The only test that matters for a public repo: someone clones it, follows the
README, and it comes up. Run this from a **fresh clone in a temp dir**, never
from the working tree — the working tree has state a stranger doesn't.

Report pass/fail per step. Any FAIL blocks the tag.

## 1. Fresh clone and env

```bash
WORK=$(mktemp -d) && git -C "$WORK" clone --depth 1 file:///path/to/k2-market-data-platform k2
cd "$WORK/k2"
cp .env.example .env
# .env.example ships change-me placeholders — set real values or nothing starts
sed -i 's/change-me-in-production/localdev-check-pw/' .env
set -a && . ./.env && set +a
```

## 2. Bring it up from nothing

```bash
docker compose up -d --build     # first run builds three images; minutes
```
Wait for health, don't guess:
```bash
until [ -z "$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -v ' healthy$' | grep -v ' $')" ]; do
  docker compose ps --format '{{.Name}}\t{{.Health}}'; sleep 15; done
```
FAIL if anything is `unhealthy` or restarting after ~10 minutes. Record which,
and its logs.

## 3. Smoke queries — one per tier

Allow a few minutes of trades to land first.

```bash
# ClickHouse gold, fed straight from Redpanda: all three exchanges producing
# (the query scripts/health.sh runs). There is no k2 database any more, only gold.
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM gold.trades FINAL WHERE exchange_ts > now() - INTERVAL 2 MINUTE GROUP BY exchange ORDER BY exchange"
# → three rows, all non-zero
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM gold.book_top20 GROUP BY exchange ORDER BY exchange"
# → three rows, all non-zero
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT count() FROM gold.ohlcv_live(bucket = 60) WHERE window_start > now() - INTERVAL 10 MINUTE"
# → non-zero: candles on read over the deduplicated trades

# Lake: every layer committed, through the same Spark session scripts/lake-verify.sh
# opens. Needs the first lake-ingest-5min run to have finished (`make health` step 3).
docker exec -i k2-spark-iceberg python3 - /home/iceberg/lake <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from spark_conf import lake_session
spark = lake_session("k2-release-check")
try:
    for t in ("raw.messages", "bronze.binance_trade", "bronze.kraken_trade",
              "bronze.coinbase_market_trades", "silver.trades_binance",
              "silver.trades_kraken", "silver.trades_coinbase", "gold.trades", "gold.ohlcv_1m"):
        print(t, spark.sql(f"SELECT count(*) FROM lake.{t}").collect()[0][0])
finally:
    spark.stop()
PY
# → every count non-zero

# Redpanda: topics and consumers
docker exec k2-redpanda rpk cluster health
docker exec k2-redpanda rpk topic list

# Prefect: deployments registered
docker exec k2-prefect-server prefect deployment ls

# Prometheus: every target up
curl -s localhost:9090/api/v1/targets \
  | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.health)"' | sort -u
# → no "down"

# Grafana: dashboards provisioned
curl -su "admin:$GRAFANA_PASSWORD" localhost:3000/api/search | jq -r '.[].title'
```

## 4. Tests

```bash
make test    # python (uv) + rust (docker) + clickhouse gold schema (docker) + notebook helpers (uv)
make parity-ohlcv parity-bars   # live three-way parity at tests/parity/pinned.json (ADR-029); needs the stack from step 2
```

Parity cannot run in GitHub's CI (no stack there), so this is the only place it gates a
tag. A failure prints the first differing rows; the Python reference is the arbiter.

## 5. Grep sweep — forbidden strings in published docs

```bash
grep -rInE "TODO|FIXME|XXX|change-me-in-production|localhost:8123 with password" \
  --include='*.md' . | grep -v '^./legacy/'
grep -rIn "Spring Boot API\|docker-compose.v2\|clickhouse/schema/" --include='*.md' . | grep -v '^./legacy/'
```
Anything published that describes a component that was never built, or a path
that no longer exists, is a FAIL. `docs/adr/` is exempt where an ADR is
deliberately recording a rejected design — check, don't assume.

## 6. Links and secrets

```bash
# relative links that don't resolve
grep -rhoE '\]\(\.{0,2}[^)#]*\.md' --include='*.md' . | sed 's/^](//' | sort -u | while read -r f; do
  [ -e "$f" ] || echo "BROKEN: $f"; done
# no real secrets committed
git log --oneline -1 && docker run --rm -v "$PWD":/src aquasec/trivy fs --scanners secret /src
```

## 7. Numbers still true

Any headline number in `README.md` must cite the latest
`docs/benchmarks/<date>.md`. If the stack changed materially since that file,
run `/benchmark-report` before tagging rather than shipping a stale figure.

## 8. Tear down

```bash
docker compose down -v && rm -rf "$WORK"
```

## Report

A table: step, PASS/FAIL, evidence (counts, target list, test summary). State
the verdict in the first line. Do not tag on a FAIL, and do not downgrade a
FAIL to a warning because it "probably only affects my machine" — a stranger's
machine is the entire point of this check.

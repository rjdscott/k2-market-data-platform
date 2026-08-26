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
# Silver: all three exchanges producing
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM k2.silver_trades GROUP BY exchange ORDER BY exchange"
# → three rows, all non-zero

# Gold: candles materialising
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT count() FROM k2.ohlcv_1m"

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
make test    # kotlin (docker) + python (uv)
```

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

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

A bare `docker compose up` no longer starts the stack: Trino reads its etc and rules only
from a deployed release (ADR-058). Use a scratch compose project, never the live one, with
`K2_RELEASES` and `K2_SECRETS` set to directories under `$WORK` (their defaults are the live
stack's `~/k2-releases` and `~/k2-secrets`), and
follow `docs/runbooks/first-cutover-to-releases.md` and `docs/runbooks/deploy-a-release.md`
for the order: the `K2_TRINO_*`, `K2_HOME` and `K2_COMPOSE_PROJECT` lines of `.env`,
`make tls`, each service's password, `make release TAG=<annotated tag>`,
`make deploy RELEASE=<id>`, `make release-verify`.
<!-- VERIFY: this order from a fresh clone on a scratch project, from nothing; then rewrite this step with the commands as run -->
Wait for health, don't guess:
```bash
until [ -z "$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -v ' healthy$' | grep -v ' $')" ]; do
  docker compose ps --format '{{.Name}}\t{{.Health}}'; sleep 15; done
```
FAIL if anything is `unhealthy` or restarting after ~10 minutes. Record which,
and its logs.

## 3. Smoke queries — one per tier

Allow a few minutes of frames to land first. What runs is capture to Redpanda; the lake
stack runs empty (the old ingest is retired to `legacy/v3-lake-ingest/`, no live crypto
data until phase 004 of the plan) and ClickHouse is paused (ADR-057).

```bash
# Capture to Redpanda: every venue delivered in the last 2 minutes, each capture healthy.
# The lake check prints a SKIP naming phase 004, and ClickHouse's a SKIP naming ADR-057.
make health
docker exec k2-redpanda rpk cluster health
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance --num 1
# → one record

# ClickHouse: paused behind profile hot-gold, so a plain `up` must not have started it
docker compose ps -a --format '{{.Name}}' | grep -cx k2-clickhouse
# → 0

# Lake: the catalog has no host port; the fence is refused or open where expected
make catalog-fence
# Trino: HTTPS with password logins only (ADR-060); the release it runs, checked part by part
make release-verify
# The lake holds none of the old namespaces: SHOW SCHEMAS FROM lake through the trino_cli
# helper of docs/runbooks/tls-and-passwords.md
# → "information_schema" and "system" only

# Airflow: no DAG until phase 002 adds the first product DAG
docker exec k2-airflow-scheduler airflow dags list
# → none listed

# Prometheus: every target up
curl -s localhost:9090/api/v1/targets \
  | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.health)"' | sort -u
# → no "down"

# Grafana: dashboards provisioned
curl -su "admin:$GRAFANA_PASSWORD" localhost:3000/api/search | jq -r '.[].title'
# → K2 Airflow, K2 Capture (v3), K2 Pipeline Overview, K2 Resources
```
<!-- VERIFY: run SHOW SCHEMAS FROM lake with trino_cli on the scratch project and write the exact command and output above -->

## 4. Tests

```bash
make test    # python, rust, clickhouse gold schema, trino, dbt, k2gen, airflow (docker and uv)
make lake-verify parity-ohlcv parity-bars   # each prints SKIP naming phase 004 and exits 0
```

The live parity gates read the old lake tables, so they are stood down until phase 004's
first live product re-pins them. A SKIP is not a pass: the release report lists each
stood-down gate and the phase that restores it.

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

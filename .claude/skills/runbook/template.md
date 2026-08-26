# Runbook: <Imperative title>

One or two lines: when to use this, and what it does not cover.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | … | <2 min | ~10 s (2026-08-26) |

---

## 1. <Scenario name>

**Symptom** — what the operator sees.

**Detection** — `AlertName` from `docker/prometheus/rules/<file>.yml`. (Or:
"manual only — no alert covers this", stated plainly.)

**Expected behaviour** — what recovers on its own, and why.

**Recovery**

```bash
docker compose restart <service>
# check it came back
docker exec k2-<service> <health command>

# confirm data is moving again
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 2 MINUTE GROUP BY exchange"
```

**Measured** — <duration>, <date>. What was observed: row counts, consumer
state, whether anything was lost. What to do if it exceeds this.

---

## Failure modes / incidents

- **YYYY-MM-DD** — what happened, what the runbook got wrong, what fixed it.
  Appended, never overwritten.

---

**Last verified:** YYYY-MM-DD against <commit or stack state>.

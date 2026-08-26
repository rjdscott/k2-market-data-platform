# Runbook (ARCHIVED): v2 Kotlin feed handler crash

**Archived 2026-08-26.** The three `feed-handler-*` containers this describes were
retired to [`legacy/v2-kotlin/`](../README.md)
([ADR-019](../../../docs/adr/ADR-019-rust-capture-tier.md)) and removed from
`docker-compose.yml`. The `FeedHandlerDown` / `FeedHandlerHighErrorRate` /
`FeedHandlerFrequentReconnects` alerts and the `feed_handler_*` metric family
went with them. **Do not follow this against the running stack** — the v3
equivalent is [`docs/runbooks/capture-down.md`](../../../docs/runbooks/capture-down.md).

Kept because the MTTR below was measured, and a measured number is not rewritten.

---

**Symptom** — one exchange stops appearing in `silver_trades`; the other two are fine.

**Detection** — `FeedHandlerDown` for that exchange (scrape target down for 2m), or the
container healthcheck flipping to unhealthy.

**Expected behaviour** — full cross-exchange isolation. The Redpanda topic stays alive,
other handlers are untouched, and the crashed handler resumes on start.

**Recovery**

```bash
docker compose ps feed-handler-binance
docker logs --tail 100 k2-feed-handler-binance
docker compose start feed-handler-binance

# If it was a config change rather than a crash:
docker compose up -d --force-recreate --no-deps feed-handler-binance
```

**Measured** — 30 s from `docker compose start`, on 2026-02-19. Kraken and Coinbase were
unaffected throughout.

**Known trap** — if the handler is stuck in a schema-registration retry loop at startup
(seen with Coinbase), `docker restart` will not clear it. Force-recreate for a fresh JVM.

## Alert rules, as they stood at retirement

The three rules and one recording rule that pointed here lived in
`docker/prometheus/rules/feed-handler-alerts.yml` and are archived beside this file as
[`feed-handler-alerts.yml`](./feed-handler-alerts.yml).

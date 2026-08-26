#!/usr/bin/env bash
#
# docker/redpanda/init.sh — one-shot Redpanda bootstrap.
#
# Creates every topic, hardens `_schemas`, sets the registry's global
# compatibility level, and registers the three v3 Avro schemas under all nine
# subjects. Runs inside docker.redpanda.com/redpandadata/redpanda:v25.3.4 as the
# `redpanda-init` service, after the broker reports healthy.
#
# Mounts it expects (wired in docker-compose.yml):
#   ./schemas/avro    -> /schemas:ro     the .avsc files
#   ./docker/redpanda -> /init:ro        this script
#
# RE-RUNNABLE. Every step is create-if-missing or converge-to-desired, so a
# second run is a no-op that exits 0. That matters more than it looks: this is
# the service every feed handler waits on with `service_completed_successfully`,
# so a non-zero exit on restart blocks the whole stack from coming up.
#
# ── Why rpk and not curl for the registry ────────────────────────────────────
# The redpanda image ships curl and rpk. It has no jq and no python3 (verified:
#   docker run --rm --entrypoint sh <image> -c 'which jq curl python3'
#   -> /usr/bin/curl, /usr/bin/rpk only).
# Registering a schema over REST means embedding the .avsc as a JSON *string*
# inside a JSON body, which without jq -Rs means hand-rolling escaping in sed —
# doable, and a quoting bug there produces a registered-but-wrong schema that
# nothing catches until a consumer decodes garbage. `rpk registry` speaks the
# same registry API, takes the file path directly, and cannot get the escaping
# wrong. curl is still used for the readiness probe and the final verification,
# where the payloads are trivial.

set -euo pipefail

BROKERS="${K2_BROKERS:-redpanda:9092}"
ADMIN="${K2_ADMIN_API:-redpanda:9644}"
REGISTRY="${K2_REGISTRY:-redpanda:8081}"
SCHEMA_DIR="${K2_SCHEMA_DIR:-/schemas}"

EXCHANGES="binance kraken coinbase"

# ── v3 topic prefix — DEVIATION FROM 001-phase-b-foundations.md, deliberate ──
#
# The plan names the v3 topics `market.crypto.{raw,trades,book}.<ex>`. Two of
# those three are new; `market.crypto.trades.<ex>` is NOT — it is the v2
# normalized Avro topic, live right now, produced by the Kotlin feed handlers.
# The collision is not cosmetic. Verified against the running v2 stack on
# 2026-08-26:
#
#   $ curl -s localhost:8081/subjects
#   ["market.crypto.trades.kraken-value","market.crypto.trades.binance-value",
#    "market.crypto.trades.coinbase-value"]          # NormalizedTrade, v1, id 1
#
#   $ curl -s -X POST localhost:8081/compatibility/subjects/\
#     market.crypto.trades.binance-value/versions/latest -d '<trade.avsc>'
#   {"is_compatible":false}
#
# So registering v3 `Trade` on the plan's subject is rejected under
# BACKWARD_TRANSITIVE (different record name, different every field), this
# script exits non-zero, and every feed handler waiting on
# `service_completed_successfully` is blocked. Phase B's exit criterion is "old
# v2 pipeline still green"; the plan's names cannot satisfy it.
#
# Nor is this only a Phase B problem: ADR-018 commits to "a parallel-run period
# where Rust capture and Kotlin handlers both produce and are compared per
# symbol over 24 h before cutover". Comparing two producers requires two topics.
# The plan bullet predates that requirement.
#
# Prefixing all nine uniformly rather than special-casing `trades`: a v3 topic
# is identifiable at a glance in `rpk topic list` and in Console, the segment
# matches the `com.k2.market.v3` Avro namespace already fixed in the schemas,
# and `raw`/`book` do not become inconsistent with `trades` for one venue's
# historical accident.
#
# At cutover (Phase E, once the Kotlin handlers move to legacy/v2-kotlin/ and
# the v2 topics are deleted) this is one line to change or to leave alone.
V3_PREFIX="${K2_V3_PREFIX:-market.crypto.v3}"

# ── v3 retention ────────────────────────────────────────────────────────────
#
# raw.*  48 h  = 172800000 ms. The raw topics are a replay buffer, not the
#        archive — Spark lands them in Iceberg `raw.messages` where they are
#        never expired (ADR-018). 48 h is "how long can the batch job be broken
#        before data is actually lost", i.e. a weekend plus a morning.
#
# retention.bytes is PER PARTITION and is the hard floor on disk. Arithmetic:
#
#   budget        20 GB total for raw across all three exchanges. That is the
#                 slice of the single-host disk (ADR-010) this tier gets.
#   / 3 topics =  6.67 GB per exchange
#   / 12 parts =  555 MB per partition
#   round down =  512 MiB = 536870912   -> 512 MiB x 12 x 3 = 18 GiB actual,
#                 leaving headroom under the 20 GB budget for segment overhead.
#
# Sense-check against the one measured number we have: a Coinbase level2
# subscribe snapshot is 5.2 MB across ~44k levels (spike, 2026-08-26), re-sent
# on every reconnect. 6.67 GB per exchange therefore holds roughly 1280 full
# snapshots' worth of bytes, so reconnect churn is not the driver — continuous
# delta traffic is, and that rate is unmeasured until Phase C.
#
# Which limit binds first is therefore an open question, and deliberately left
# as one: 48 h is the target, 512 MiB/partition is the guarantee. If Phase C's
# burn-in shows bytes evicting well inside 48 h, the fix is a bigger disk slice
# or fewer raw partitions — not a silently shorter retention.
RAW_RETENTION_MS=172800000
RAW_RETENTION_BYTES=536870912

# trades.*/book.* 7 d = 604800000 ms. These are derived and rebuildable from
# raw, so they only need to outlive a Spark backfill window. No byte cap: Avro
# fixed-point records are a fraction of the raw JSON they came from.
DERIVED_RETENTION_MS=604800000

log() { echo "  $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# 1. v2 topics — unchanged from the inline redpanda-init command they replace.
#    Partition counts are copied exactly (binance 40, kraken 20, coinbase 20);
#    changing them here would silently repartition nothing (rpk will not shrink
#    a topic) while making the file disagree with the running cluster.
#    These go away only after the ClickHouse cutover in Phase E.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ v2 topics"
rpk topic describe market.crypto.trades.binance.raw  --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.binance.raw  --partitions 40 --brokers "$BROKERS"
rpk topic describe market.crypto.trades.binance      --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.binance      --partitions 40 --brokers "$BROKERS"
rpk topic describe market.crypto.trades.kraken.raw   --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.kraken.raw   --partitions 20 --brokers "$BROKERS"
rpk topic describe market.crypto.trades.kraken       --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.kraken       --partitions 20 --brokers "$BROKERS"
rpk topic describe market.crypto.trades.coinbase.raw --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.coinbase.raw --partitions 20 --brokers "$BROKERS"
rpk topic describe market.crypto.trades.coinbase     --brokers "$BROKERS" >/dev/null 2>&1 || rpk topic create market.crypto.trades.coinbase     --partitions 20 --brokers "$BROKERS"
log "✅ 6 v2 topics present"

# ─────────────────────────────────────────────────────────────────────────────
# 2. v3 topics — 9 topics, 12 partitions each.
#
#    12 partitions: the key is the canonical symbol and there are at most 12
#    instruments on any one exchange, so 12 is one partition per instrument at
#    the current registry — enough for per-symbol ordering with parallel
#    consumers, and small enough that 9 topics x 12 is 108 partitions on one
#    broker rather than the 160 the v2 topics alone carry. Uniform across
#    exchanges on purpose: v2's 40/20/20 split encodes an instrument count that
#    has already drifted from config/instruments.yaml.
#
#    Create-then-alter rather than create-only: alter-config converges a topic
#    that already exists with the wrong retention, which create-if-missing
#    silently would not. Both are idempotent.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ v3 topics"
for ex in $EXCHANGES; do
  for kind in raw trades book; do
    topic="${V3_PREFIX}.${kind}.${ex}"

    rpk topic describe "$topic" --brokers "$BROKERS" >/dev/null 2>&1 \
      || rpk topic create "$topic" --partitions 12 --brokers "$BROKERS"

    if [ "$kind" = "raw" ]; then
      rpk topic alter-config "$topic" --brokers "$BROKERS" \
        --set "retention.ms=${RAW_RETENTION_MS}" \
        --set "retention.bytes=${RAW_RETENTION_BYTES}" >/dev/null
    else
      rpk topic alter-config "$topic" --brokers "$BROKERS" \
        --set "retention.ms=${DERIVED_RETENTION_MS}" >/dev/null
    fi
  done
done
log "✅ 9 v3 topics present (raw ${RAW_RETENTION_MS}ms/${RAW_RETENTION_BYTES}B per partition, trades+book ${DERIVED_RETENTION_MS}ms)"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Wait for the schema registry.
#
#    The broker healthcheck says nothing about the registry, which binds later
#    in the same process. It also lazily creates the `_schemas` topic on first
#    contact — so this loop is not just politeness, it is what guarantees
#    `_schemas` exists before step 4 tries to alter it.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ schema registry"
for _ in $(seq 1 60); do
  curl -sf "http://${REGISTRY}/subjects" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "http://${REGISTRY}/subjects" >/dev/null || {
  echo "❌ schema registry at ${REGISTRY} did not come up within 60s" >&2
  exit 1
}
log "✅ registry reachable at ${REGISTRY}"

# ─────────────────────────────────────────────────────────────────────────────
# 4. Harden `_schemas` — verbatim from the redpanda-init command this replaces.
#    Compact policy + infinite retention prevents offset_out_of_range on schema
#    registry restart (default delete policy trims records after 24h local
#    retention). The nodelete list is set twice on purpose: `_schemas` can only
#    be added to it once the topic exists, and the first call establishes the
#    baseline list that the second one extends.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ _schemas hardening"
rpk cluster config set kafka_nodelete_topics '["_redpanda.audit_log","__consumer_offsets"]' --api-urls "$ADMIN"
rpk topic alter-config _schemas --set cleanup.policy=compact --set retention.ms=-1 --set retention.local.target.ms=-1 --brokers "$BROKERS"
rpk cluster config set kafka_nodelete_topics '["_redpanda.audit_log","__consumer_offsets","_schemas"]' --api-urls "$ADMIN"
log "✅ _schemas hardened (compact, infinite retention)"

# ─────────────────────────────────────────────────────────────────────────────
# 5. Global compatibility level.
#
#    BACKWARD_TRANSITIVE, set globally rather than per subject so a subject
#    registered later cannot quietly inherit the BACKWARD default. Rationale is
#    in schemas/README.md: the Iceberg archive is never rewritten, so a reader
#    must handle EVERY prior version, not just the previous one.
#
#    This tightens the level on the three live v2 subjects too (they are on the
#    BACKWARD default today). Harmless: the Kotlin handlers auto-register a
#    schema byte-identical to their already-registered version 1, and an
#    identical schema is a lookup, not an evolution, so no compatibility check
#    runs. If v2 ever needed a real schema change it would now be blocked — and
#    a frozen v2 contract during the v3 migration is the desired behaviour.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ registry compatibility"
curl -sf -X PUT "http://${REGISTRY}/config" \
  -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
  -d '{"compatibility":"BACKWARD_TRANSITIVE"}' >/dev/null
log "✅ global compatibility = $(curl -sf "http://${REGISTRY}/config")"

# ─────────────────────────────────────────────────────────────────────────────
# 6. Register the v3 schemas — TopicNameStrategy, so subject = <topic>-value.
#
#    Idempotent by registry semantics: POSTing a schema identical to the latest
#    version returns that version's id and creates nothing. A *changed* schema
#    is checked against BACKWARD_TRANSITIVE first and rejected if incompatible,
#    which is the desired behaviour — this script must not be able to force a
#    breaking contract change through on a restart.
#
#    No `-key` subjects: keys are the canonical symbol as plain UTF-8 (see
#    schemas/README.md).
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ v3 schemas"
for ex in $EXCHANGES; do
  for kind in raw trades book; do
    case "$kind" in
      raw)    schema="raw-message.avsc" ;;
      trades) schema="trade.avsc" ;;
      book)   schema="book-snapshot-l2.avsc" ;;
    esac

    [ -f "${SCHEMA_DIR}/${schema}" ] || {
      echo "❌ ${SCHEMA_DIR}/${schema} not found — is ./schemas/avro mounted at ${SCHEMA_DIR}?" >&2
      exit 1
    }

    rpk registry schema create "${V3_PREFIX}.${kind}.${ex}-value" \
      --schema "${SCHEMA_DIR}/${schema}" \
      -X "registry.hosts=${REGISTRY}" >/dev/null
  done
done
log "✅ 9 subjects registered"

echo "▶ done"
rpk topic list --brokers "$BROKERS"
curl -sf "http://${REGISTRY}/subjects"
echo

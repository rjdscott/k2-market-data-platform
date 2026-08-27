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
# the service every capture container waits on with
# `service_completed_successfully`, so a non-zero exit on restart blocks the
# whole stack from coming up.
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
# normalized Avro topic, which was live and produced by the Kotlin feed handlers
# when this was written (they retired 2026-08-26; the topic is frozen, not gone,
# until Phase E). The collision is not cosmetic. Verified against the running v2
# stack on 2026-08-26:
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
# script exits non-zero, and every producer waiting on
# `service_completed_successfully` is blocked. Phase B's exit criterion was "old
# v2 pipeline still green"; the plan's names could not satisfy it.
#
# Nor was this only a Phase B problem: ADR-018 committed to "a parallel-run
# period where Rust capture and Kotlin handlers both produce and are compared per
# symbol before cutover". Comparing two producers requires two topics. The plan
# bullet predated that requirement.
#
# Prefixing all nine uniformly rather than special-casing `trades`: a v3 topic
# is identifiable at a glance in `rpk topic list` and in Console, the segment
# matches the `com.k2.market.v3` Avro namespace already fixed in the schemas,
# and `raw`/`book` do not become inconsistent with `trades` for one venue's
# historical accident.
#
# The Kotlin handlers have since moved to legacy/v2-kotlin/ and the v2 topics are
# frozen; at the Phase E cutover, once they are deleted, this prefix is one line
# to change or to leave alone.
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
# Which limit binds first was an open question. MEASURED 2026-08-26: the bytes
# bind, and they bind at 7 h, not 48. `market.crypto.v3.raw.kraken` partition 0
# held 4,887,694 records between LOG-START and HIGH-WATERMARK whose Kafka
# timestamps are 25,227,274 ms apart — 7.01 h, 193.9 records/s, 119.7 B/record
# on disk (docs/architecture/15-capacity-model.md §4d, note of that date).
#
# The arithmetic above is not what was wrong; its assumption was. It divides the
# topic's bytes by twelve, and the traffic does not divide by twelve: records
# are keyed by symbol, so partition 0 holds 558 MB while partitions 2, 5, 8 and
# 11 hold ~600 kB each, and the topic uses 2.87 GB of the 6 GiB these caps
# allow. A per-partition byte cap under keyed partitioning caps the busiest key.
#
# 512 MiB STANDS, deliberately. The bus is a buffer sized for the 5-minute
# ingest cadence — 7 h is ~84 cycles of slack — and it is NOT the archive: the
# lake is (ADR-021), and raw.messages is never expired. A lake that cannot keep
# up is fixed in the lake, which is why the ingest's per-run bound is now
# 200,000 offsets/partition, ~3.6x the measured arrival rate. Revisit these two
# numbers when k2_lake_ingest_backlog_offsets for any topic exceeds one hour of
# that topic's arrival rate for two consecutive cycles
# (docs/runbooks/lake-ingest-lag.md §3) — and then by raising the disk slice or
# cutting the raw partition count, never by a silently shorter retention.
#
# What it cost to learn: 1,168,954 records evicted unread from that partition on
# 2026-08-26, recorded as an offset_gap row in lake.audit.checks.
RAW_RETENTION_MS=172800000
RAW_RETENTION_BYTES=536870912
RAW_MAX_MESSAGE_BYTES=8388608   # largest measured frame 5,195,904 B (S5); agrees with sink.rs MESSAGE_MAX_BYTES

# trades.*/book.* 7 d = 604800000 ms. These are derived and rebuildable from
# raw, so they only need to outlive a Spark backfill window. No byte cap: Avro
# fixed-point records are a fraction of the raw JSON they came from.
DERIVED_RETENTION_MS=604800000

log() { echo "  $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# 1. v2 topics — deleted at the Phase E cutover, 2026-08-27.
#
#    The six market.crypto.trades.<ex>{,.raw} topics (40/20/20 partitions) had no
#    producer since the Kotlin handlers retired (ADR-019) and no consumer once the
#    ClickHouse `k2` database went with them. They are not recreated: a from-scratch
#    bring-up and the running cluster now agree on nine topics.
# ─────────────────────────────────────────────────────────────────────────────
# The six v2 topics (market.crypto.trades.<ex>{,.raw}) were deleted at the Phase E
# cutover, 2026-08-27: nothing had produced to them since ADR-019 and nothing
# reads them. docker/redpanda/README.md keeps the history.

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

    if ! rpk topic describe "$topic" --brokers "$BROKERS" >/dev/null 2>&1; then
      if [ "$kind" = "raw" ]; then
        rpk topic create "$topic" --partitions 12 --brokers "$BROKERS" \
          --topic-config "max.message.bytes=${RAW_MAX_MESSAGE_BYTES}"
      else
        rpk topic create "$topic" --partitions 12 --brokers "$BROKERS"
      fi
    fi

    if [ "$kind" = "raw" ]; then
      # max.message.bytes=8 MiB: Coinbase's level2 subscribe snapshot is
      # 5,195,904 bytes for BTC-USD (ADR-018 Appendix A, S5) and the Redpanda
      # default (1,048,576, = kafka_batch_max_bytes) rejects it, so the archive
      # lost the snapshot frame on every reconnect. Matches
      # MESSAGE_MAX_BYTES in services/capture-rust/src/sink.rs and the WS cap
      # in ws.rs. Topic-level overrides the cluster default (verified: rpk
      # topic describe -c shows 8388608 DYNAMIC_TOPIC_CONFIG while
      # kafka_batch_max_bytes stays 1048576, and a 5 MB record lands).
      # Trades/book stay at the default: fixed-point Avro records are ~100 B.
      rpk topic alter-config "$topic" --brokers "$BROKERS" \
        --set "retention.ms=${RAW_RETENTION_MS}" \
        --set "retention.bytes=${RAW_RETENTION_BYTES}" \
        --set "max.message.bytes=${RAW_MAX_MESSAGE_BYTES}" >/dev/null
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
#
#    `cleanup.policy=compact` + `retention.ms=-1` is the pair that prevents
#    offset_out_of_range on schema registry restart: the default delete policy
#    would trim records out from under the registry's stored offsets.
#
#    `retention.local.target.ms=-1` is belt-and-braces only. It governs the LOCAL
#    tier under Tiered Storage, and this cluster runs cloud_storage_enabled=false
#    (dev-container mode), so it is inert today. Kept so the topic stays correct
#    if Tiered Storage is ever switched on, not because it is doing work now.
#
#    The nodelete list is set twice on purpose: `_schemas` can only be added to
#    it once the topic exists, and the first call establishes the baseline list
#    that the second one extends.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ _schemas hardening"
rpk cluster config set kafka_nodelete_topics '["_redpanda.audit_log","__consumer_offsets"]' --api-urls "$ADMIN"
rpk topic alter-config _schemas --set cleanup.policy=compact --set retention.ms=-1 --set retention.local.target.ms=-1 --brokers "$BROKERS"
rpk cluster config set kafka_nodelete_topics '["_redpanda.audit_log","__consumer_offsets","_schemas"]' --api-urls "$ADMIN"
log "✅ _schemas hardened (compact + retention.ms=-1)"

# ─────────────────────────────────────────────────────────────────────────────
# 5. Global compatibility level.
#
#    BACKWARD_TRANSITIVE, set globally rather than per subject so a subject
#    registered later cannot quietly inherit the BACKWARD default. Rationale is
#    in schemas/README.md: the Iceberg archive is never rewritten, so a reader
#    must handle EVERY prior version, not just the previous one.
#
#    This tightens the level on the three v2 subjects too (they are on the
#    BACKWARD default today). Harmless, and now moot: the Kotlin handlers that
#    auto-registered `NormalizedTrade` are retired, so nothing re-registers those
#    subjects at all. They stay in the registry until Phase E for the same reason
#    the topics do — the frozen v2 data is still decodable.
# ─────────────────────────────────────────────────────────────────────────────
#    NON-FATAL (see step 6's banner): a registry that will not take the global
#    level is a v3 problem, and blocking the v2 feed handlers over it is worse
#    than running them under the BACKWARD default they already have.
echo "▶ registry compatibility"
if curl -sf -X PUT "http://${REGISTRY}/config" \
     -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
     -d '{"compatibility":"BACKWARD_TRANSITIVE"}' >/dev/null; then
  log "✅ global compatibility = $(curl -sf "http://${REGISTRY}/config")"
else
  echo "  ⚠️  WARN: could not set global compatibility to BACKWARD_TRANSITIVE" >&2
  echo "  ⚠️  WARN: registry stays on its current default — v3 subjects are unguarded" >&2
  V3_COMPAT_OK=0
fi

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
#
#    ── NON-FATAL, deliberately ──────────────────────────────────────────────
#    Every capture container waits on this service with
#    `service_completed_successfully`. Under `set -e` a single failed schema
#    registration exits 1 and takes the whole stack down with it. This was
#    written when v2 carried the traffic and v3 had no producers; the polarity
#    has since flipped — v3 is the live tier now — but non-fatal is still the
#    right call, because a capture container that starts and warns is more
#    diagnosable than a stack that will not boot.
#    v2 topic creation and `_schemas` hardening stay fatal: the frozen `k2`
#    Kafka-engine queues still need those topics to exist.
#
#    The final summary line is the thing to alert on — "9/9" is healthy, any
#    other count means Phase C has work to do before it can produce.
# ─────────────────────────────────────────────────────────────────────────────
echo "▶ v3 schemas"
V3_TOTAL=0
V3_OK=0
for ex in $EXCHANGES; do
  for kind in raw trades book; do
    case "$kind" in
      raw)    schema="raw-message.avsc" ;;
      trades) schema="trade.avsc" ;;
      book)   schema="book-snapshot-l2.avsc" ;;
    esac

    subject="${V3_PREFIX}.${kind}.${ex}-value"
    V3_TOTAL=$((V3_TOTAL + 1))

    if [ ! -f "${SCHEMA_DIR}/${schema}" ]; then
      echo "  ⚠️  WARN: ${SCHEMA_DIR}/${schema} not found (is ./schemas/avro mounted at ${SCHEMA_DIR}?) — skipping ${subject}" >&2
      continue
    fi

    if rpk registry schema create "$subject" \
         --schema "${SCHEMA_DIR}/${schema}" \
         -X "registry.hosts=${REGISTRY}" >/dev/null 2>&1; then
      V3_OK=$((V3_OK + 1))
    else
      echo "  ⚠️  WARN: failed to register ${subject} from ${schema}" >&2
    fi
  done
done

if [ "$V3_OK" -eq "$V3_TOTAL" ]; then
  log "✅ v3 schemas: ${V3_OK}/${V3_TOTAL} subjects registered"
else
  echo "  ⚠️  WARN: v3 schemas: only ${V3_OK}/${V3_TOTAL} subjects registered" >&2
  echo "  ⚠️  WARN: v2 is unaffected and this service still exits 0 — but Phase C" >&2
  echo "  ⚠️  WARN: cannot produce until the missing subjects are registered." >&2
fi

echo "▶ done"
rpk topic list --brokers "$BROKERS" || true
curl -sf "http://${REGISTRY}/subjects" || true
echo

# Exit 0 even on partial v3 registration — every capture container gates on this.
# v2 topic creation and `_schemas` hardening already exited non-zero if they failed.
if [ "$V3_OK" -eq "$V3_TOTAL" ] && [ "${V3_COMPAT_OK:-1}" -eq 1 ]; then
  echo "✅ redpanda-init complete: 6 v2 topics, 9 v3 topics, ${V3_OK}/${V3_TOTAL} v3 subjects"
else
  echo "⚠️  redpanda-init complete WITH WARNINGS: v2 tier healthy (6 topics, _schemas hardened); v3 subjects ${V3_OK}/${V3_TOTAL}"
fi

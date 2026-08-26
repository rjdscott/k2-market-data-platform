#!/usr/bin/env python3
"""
v2 (Kotlin) vs v3 (Rust) trade parity over one labelled window.

This is the evidence generator for the Kotlin retirement PR. It consumes both
trade topics for one exchange over the same wall-clock window, decodes both Avro
contracts, normalises them onto one comparable tuple, and prints a per-symbol
markdown table with a PASS/FAIL verdict. The table is pasted into the PR; the
exit code is what a script would gate on.

    uv run --no-project --with "confluent-kafka[avro]==2.15.0" \
      python scripts/parity/compare_trades.py \
        --exchange kraken \
        --window-start 2026-08-26T10:00:00Z \
        --window-end   2026-08-26T12:00:00Z

Exit 0 = every symbol PASS. Exit 1 = any symbol FAIL, or the run could not be
completed (registry down, empty window, unknown schema id).

── What is being compared ───────────────────────────────────────────────────

  v2  market.crypto.trades.<ex>       com.k2.marketdata.crypto.NormalizedTrade
      price/quantity are decimal STRINGS, exchange_timestamp is a bare int64 of
      MILLISECONDS (its `logicalType` sits as a sibling of `type`, so Avro drops
      it — the v2 bug that tests/test_contracts.py exists to prevent recurring).

  v3  market.crypto.v3.trades.<ex>    com.k2.market.v3.Trade
      price/qty are fixed-point int64 at 1e-8, exchange_ts is a real
      timestamp-micros logicalType (fastavro hands back a tz-aware datetime).

Both are normalised to `Norm(canonical_symbol, trade_id, price, qty, side,
exchange_ts_us)` with prices as exact 1e-8 integers via `Decimal` — never a
float, because a float round-trip of "78600.44000000" is exactly the silent
corruption this comparison is supposed to be able to detect.

`side` is the taker side, and both contracts already carry it: v2's `TradeSide`
enum is `BUY`/`SELL` (normalized-trade.avsc:49), v3's `Side` enum is
`buy`/`sell` (trade.avsc:38). They are case-folded onto one vocabulary and
compared at zero tolerance. This is not decoration: both tiers derive the
Binance side from the same `is_buyer_maker` boolean by inverting it
(TradeNormalizer.kt:27, binance.rs:313), and trade.avsc's own doc names that
inversion as the hazard. A tier that flipped it would emit the same id, price,
quantity and timestamp for every trade, so counts, the ID join and px/qty would
all stay green — side is the only column that can see it.

── Why a tolerance at all ───────────────────────────────────────────────────

The window is cut on the Kafka record timestamp of each topic independently, and
the two producers stamp a trade at different points in their own pipelines. A
trade that lands at 11:59:59.998 on one topic can land at 12:00:00.001 on the
other, so the two windows do not contain literally the same set of trades at the
edges — with two edges and three exchanges, a handful of boundary trades is
expected and means nothing. The tolerance is `max(2, 0.1% of count)`: two trades
absorbs the edges on a quiet symbol, 0.1% scales it on a busy one, and neither
is large enough to hide a producer that is actually dropping messages. What has
NO tolerance is disagreement about a trade both tiers saw: `px/qty mismatch`
must be 0.

── Kraken is compared differently, on purpose ───────────────────────────────

v2's Kraken trade IDs are synthesised, not real: `"KRAKEN-${timestampMs}-${pair
.hashCode()}"` (ADR-018 gap 5, ADR-019 Consequences). Two trades in the same
millisecond on the same pair get the same ID by construction, so an ID join
against v3's real integer `trade_id` is not merely unavailable — it would be
meaningless, and a green result from it would be a lie. For Kraken the ID
comparison is skipped and the verdict rests on counts plus a multiset
comparison of `(price, qty, exchange_ts)` instead.

That multiset is keyed on (price, qty, side, exchange_ts@ms) — truncated to
MILLISECONDS because v2 cannot represent anything finer: the v2 schema stores
milliseconds and Kraken publishes microseconds. Comparing at microsecond
granularity would fail on every single trade for a reason that has nothing to do
with parity.

Because that path has no `mismatched` column, a Kraken price or side divergence
can only surface as a multiset difference — which would otherwise be judged by
the same proportional tolerance as a count delta, letting 150 wrong prices
through on a 150k-trade symbol. So the multiset differences are gated at
EDGE_ALLOWANCE, never at the 0.1% slope. See `SymbolStats.passed`.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import NoReturn

SCALE_EXP = 8  # fixed-point scale of the v3 contract: value = round(x * 1e8)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

V2_TOPIC = "market.crypto.trades.{exchange}"
V3_TOPIC = "market.crypto.v3.trades.{exchange}"

EXCHANGES = ("binance", "kraken", "coinbase")

# Venues whose v2 trade_id was synthesised by K2 rather than supplied by the
# exchange, and therefore cannot be joined on. See the module docstring.
SYNTHETIC_V2_ID_EXCHANGES = frozenset({"kraken"})

# docker-compose.yml:58 publishes the external listener (`19092:19092`), which is
# advertised as `localhost:19092` (:67), so this resolves from the host.
DEFAULT_BROKERS = "localhost:19092"
DEFAULT_REGISTRY = "http://localhost:8081"

POLL_TIMEOUT_S = 1.0
IDLE_POLLS_BEFORE_GIVING_UP = 15

# Marks a note produced by the early-stop path. A truncated read cannot produce a
# verdict worth pasting, so `main` fails the run on it rather than rendering an
# authoritative-looking table with a caveat nobody reads.
TRUNCATION_NOTE = "**read truncated**"

# Both producers are still writing into a window that ends near `now`, and they
# are not read at the same instant: v3's `consume_window` runs only after v2's has
# fully drained, and each truncates at its own high-watermark snapshot. A window
# ending now therefore hands v3 every record produced during the v2 read and v2
# none of them — a systematic one-sided deficit reported as a parity failure.
MIN_WINDOW_END_AGE_S = 60

# Largest `exchange_ts` disagreement a matched pair can show without it being a
# real disagreement. v2 stores milliseconds (normalized-trade.avsc:64) and floors
# to them (`Instant.parse(time).toEpochMilli()`, TradeNormalizer.kt:117) while v3
# stores microseconds, so truncation alone can never reach a full millisecond.
MAX_TS_DELTA_US = 1000

# Cap on a Kraken multiset difference. Window-edge effects are bounded by how many
# trades fall within the two tiers' stamping skew of the two boundaries — a small
# constant — not by a fraction of the symbol's volume. See the module docstring.
EDGE_ALLOWANCE = 2


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation — pure, no Kafka, unit-tested in tests/test_parity.py
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Norm:
    """One trade, reduced to what both contracts can express exactly."""

    canonical_symbol: str
    trade_id: str
    price: int  # fixed-point, scale 1e-8
    qty: int  # fixed-point, scale 1e-8
    side: str  # taker side, case-folded: "buy" | "sell"
    exchange_ts_us: int  # microseconds since epoch, UTC


def to_fixed_1e8(value: str) -> int:
    """
    Decimal string -> exact fixed-point int64 at scale 1e-8.

    '78600.44000000' -> 7860044000000. Never via float: float('0.1') * 1e8 is
    10000000.000000002, and the whole point of the v3 contract is that the
    integer is exact.

    More than 8 decimal places is an error rather than a rounding, matching the
    v3 capture rule ("rejected at capture and counted, never silently rounded",
    trade.avsc). No crypto venue K2 subscribes to quotes finer than 1e-8, so
    this firing means the assumption broke, not that the trade is unusual.
    """
    try:
        scaled = Decimal(value).scaleb(SCALE_EXP)
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a decimal number") from exc
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"{value!r} has more than {SCALE_EXP} decimal places and cannot be "
            "represented at scale 1e-8 without rounding"
        )
    return int(scaled)


def to_micros(value: object, *, unit: str) -> int:
    """
    Timestamp -> microseconds since epoch.

    Accepts an int in `unit` ('ms' or 'us') or a datetime — fastavro decodes a
    real timestamp-micros logicalType to a tz-aware datetime, while v2's
    sibling-`logicalType` field decodes to a bare int of milliseconds, so both
    shapes turn up depending on which contract is being read.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (value - EPOCH) // timedelta(microseconds=1)
    if unit == "ms":
        return int(value) * 1000
    if unit == "us":
        return int(value)
    raise ValueError(f"unknown timestamp unit {unit!r}")


def to_side(value: object) -> str:
    """
    Either contract's taker-side enum -> one vocabulary.

    v2's `TradeSide` is `BUY`/`SELL`, v3's `Side` is `buy`/`sell`; both are
    documented as the taker (aggressor) side, so case is the only difference.
    An unrecognised symbol is an error rather than a pass-through: the whole
    value of this column is that a disagreement cannot be spelled away.
    """
    side = str(value).lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"{value!r} is not a taker side (want buy/sell)")
    return side


def normalise_v2(record: dict) -> Norm:
    """com.k2.marketdata.crypto.NormalizedTrade -> Norm."""
    return Norm(
        canonical_symbol=record["canonical_symbol"],
        trade_id=str(record["trade_id"]),
        price=to_fixed_1e8(record["price"]),
        qty=to_fixed_1e8(record["quantity"]),
        side=to_side(record["side"]),
        exchange_ts_us=to_micros(record["exchange_timestamp"], unit="ms"),
    )


def normalise_v3(record: dict) -> Norm:
    """com.k2.market.v3.Trade -> Norm."""
    return Norm(
        canonical_symbol=record["canonical_symbol"],
        trade_id=str(record["trade_id"]),
        price=int(record["price"]),
        qty=int(record["qty"]),
        side=to_side(record["side"]),
        exchange_ts_us=to_micros(record["exchange_ts"], unit="us"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Comparison — pure
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SymbolStats:
    symbol: str
    count_v2: int
    count_v3: int
    only_v2: int
    only_v3: int
    mismatched: int | None  # None = ID join skipped (synthesised v2 IDs)
    max_ts_delta_us: int | None  # None = nothing pairable to measure across

    @property
    def count_delta(self) -> int:
        return self.count_v2 - self.count_v3

    @property
    def tolerance(self) -> int:
        return tolerance_for(max(self.count_v2, self.count_v3))

    @property
    def side_allowance(self) -> int:
        """
        Allowance on only-v2 / only-v3. On the ID-join path these are ID
        differences and a px/qty/side divergence is caught separately at zero
        tolerance, so the full tolerance applies. On the Kraken path there is no
        `mismatched` column, so a divergence has nowhere else to show up and the
        proportional slope would swallow it — 150 wrong prices on a 150k-trade
        symbol sit inside 0.1%. There, the constant edge allowance is the cap.
        """
        return EDGE_ALLOWANCE if self.mismatched is None else self.tolerance

    @property
    def passed(self) -> bool:
        # One side seeing the symbol and the other seeing nothing at all is never
        # a window-edge effect, so the tolerance must not apply to it. Without
        # this, a symbol with two trades in the window "passes" against a v3 tier
        # that is not running — the floor of 2 swallows it — and a table full of
        # PASS rows gets pasted into the retirement PR as evidence of nothing.
        if (self.count_v2 == 0) != (self.count_v3 == 0):
            return False
        # Both tiers read the same exchange timestamp off the same wire frame, so
        # the only legitimate difference is v2's millisecond resolution. Anything
        # at or past a full millisecond means they disagree about when the trade
        # happened, which an ungated printed number would let a reader wave away.
        if self.max_ts_delta_us is not None and self.max_ts_delta_us >= MAX_TS_DELTA_US:
            return False
        return (
            abs(self.count_delta) <= self.tolerance
            and self.only_v2 <= self.side_allowance
            and self.only_v3 <= self.side_allowance
            and not self.mismatched  # None (skipped) and 0 both pass
        )


def tolerance_for(count: int) -> int:
    """
    Per-symbol allowance for window-edge effects. See the module docstring for
    why this is not zero. Floor of 2 so a symbol with three trades in the window
    is not judged by a 0.1% that rounds to nothing.
    """
    return max(2, int(count * 0.001))


def _ms_bucket_key(n: Norm) -> tuple[int, int, str, int]:
    """Multiset key for the no-usable-ID path: v2 cannot resolve finer than ms."""
    return (n.price, n.qty, n.side, n.exchange_ts_us // 1000)


def compare_symbol(v2: list[Norm], v3: list[Norm], *, join_on_id: bool) -> tuple:
    """
    Returns (only_v2, only_v3, mismatched, max_ts_delta_us) for one symbol.

    `join_on_id=False` is the Kraken path: no ID join is possible, so the two
    sides are compared as multisets of (price, qty, side, exchange_ts@ms) and
    there is no such thing as "same trade, different price" to count.
    """
    if not join_on_id:
        c2, c3 = Counter(map(_ms_bucket_key, v2)), Counter(map(_ms_bucket_key, v3))
        return sum((c2 - c3).values()), sum((c3 - c2).values()), None, None

    # ponytail: first-wins on a duplicate ID. A real duplicate on binance or
    # coinbase would be a capture bug, and it still shows up here — as a count
    # delta the verdict catches — just not broken out into its own column.
    by_id_v2 = {}
    for n in v2:
        by_id_v2.setdefault(n.trade_id, n)
    by_id_v3 = {}
    for n in v3:
        by_id_v3.setdefault(n.trade_id, n)

    common = by_id_v2.keys() & by_id_v3.keys()

    def comparable(n: Norm) -> tuple[int, int, str]:
        return (n.price, n.qty, n.side)

    mismatched = sum(
        1 for tid in common if comparable(by_id_v2[tid]) != comparable(by_id_v3[tid])
    )
    max_delta = max(
        (abs(by_id_v2[tid].exchange_ts_us - by_id_v3[tid].exchange_ts_us) for tid in common),
        default=None,
    )
    return (
        len(by_id_v2.keys() - by_id_v3.keys()),
        len(by_id_v3.keys() - by_id_v2.keys()),
        mismatched,
        max_delta,
    )


def compare(v2: Iterable[Norm], v3: Iterable[Norm], *, exchange: str) -> list[SymbolStats]:
    """Per canonical symbol, over the union of symbols seen on either topic."""
    join_on_id = exchange not in SYNTHETIC_V2_ID_EXCHANGES

    left: dict[str, list[Norm]] = defaultdict(list)
    right: dict[str, list[Norm]] = defaultdict(list)
    for n in v2:
        left[n.canonical_symbol].append(n)
    for n in v3:
        right[n.canonical_symbol].append(n)

    stats = []
    for symbol in sorted(left.keys() | right.keys()):
        a, b = left.get(symbol, []), right.get(symbol, [])
        only_v2, only_v3, mismatched, max_delta = compare_symbol(a, b, join_on_id=join_on_id)
        stats.append(
            SymbolStats(
                symbol=symbol,
                count_v2=len(a),
                count_v3=len(b),
                only_v2=only_v2,
                only_v3=only_v3,
                mismatched=mismatched,
                max_ts_delta_us=max_delta,
            )
        )
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Rendering — pure
# ─────────────────────────────────────────────────────────────────────────────


def render_markdown(stats: list[SymbolStats], header: dict) -> str:
    lines = [
        f"# v2 / v3 trade parity — {header['exchange']}",
        "",
        f"- **window:** `{header['window_start']}` → `{header['window_end']}`"
        f" ({header['window_hours']:.2f} h — a labelled sample, not a soak)",
        f"- **v2 topic:** `{header['v2_topic']}` — {header['v2_consumed']:,} records consumed",
        f"- **v3 topic:** `{header['v3_topic']}` — {header['v3_consumed']:,} records consumed",
        "- **tolerance:** `max(2, 0.1% of count)` per symbol on the count delta;"
        f" px/qty/side mismatch must be 0; `exchange_ts` delta must stay under"
        f" {MAX_TS_DELTA_US:,} µs",
    ]
    if header["join_on_id"]:
        lines.append(
            "- **join:** exchange `trade_id`. only-v2/only-v3 carry the same"
            " tolerance as the count delta."
        )
    else:
        lines.append(
            "- **join:** ID comparison **skipped** — v2 Kraken IDs are synthesised"
            " (`KRAKEN-<ms>-<hash>`, ADR-018 gap 5) and collide by construction."
            " Compared as a multiset of `(price, qty, side, exchange_ts@ms)` instead."
        )
        lines.append(
            "- **carve-out:** with no ID join there is no `px/qty/side mismatch`"
            " column, so a Kraken price, quantity or side divergence can only"
            f" appear as a multiset difference. Those are capped at"
            f" {EDGE_ALLOWANCE} (the window-edge allowance), never at the 0.1%"
            " slope — a divergence inside that cap is not distinguishable here"
            " from a boundary trade."
        )
    if header["notes"]:
        lines += [f"- **note:** {n}" for n in header["notes"]]
    lines += [
        "",
        "| symbol | v2 | v3 | Δ | only-v2 | only-v3 | px/qty/side mismatch | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in stats:
        lines.append(
            f"| {s.symbol} | {s.count_v2:,} | {s.count_v3:,} | {s.count_delta:+,} |"
            f" {s.only_v2:,} | {s.only_v3:,} |"
            f" {'n/a' if s.mismatched is None else f'{s.mismatched:,}'} |"
            f" {'PASS' if s.passed else '**FAIL**'} |"
        )

    passed = sum(1 for s in stats if s.passed)
    overall = passed == len(stats) and not header.get("truncated")
    lines += ["", f"**OVERALL: {'PASS' if overall else 'FAIL'}**"
              f" — {passed}/{len(stats)} symbols"
              + (" (read truncated — not evidence)" if header.get("truncated") else "")]

    deltas = [s.max_ts_delta_us for s in stats if s.max_ts_delta_us is not None]
    if deltas:
        worst = max(deltas)
        verdict = "within v2 truncation" if worst < MAX_TS_DELTA_US else "**over the bound**"
        lines.append(
            f"\nMax `exchange_ts` delta on matched IDs: {worst:,} µs — {verdict}."
            f" v2 floors to milliseconds (`Instant.toEpochMilli()`), so a matched pair"
            f" cannot legitimately differ by a full millisecond; {MAX_TS_DELTA_US:,} µs"
            " or more fails the symbol rather than being noted and waved away."
        )
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Kafka
# ─────────────────────────────────────────────────────────────────────────────


def die(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_deserializer(registry_url: str):
    from confluent_kafka.schema_registry import SchemaRegistryClient
    from confluent_kafka.schema_registry.avro import AvroDeserializer

    client = SchemaRegistryClient({"url": registry_url})
    try:
        client.get_subjects()
    except Exception as exc:  # noqa: BLE001 - the cause is in the message
        die(
            f"schema registry at {registry_url} is unreachable ({exc}). "
            "Is the stack up? `docker compose ps redpanda` and "
            f"`curl -s {registry_url}/subjects`."
        )
    # schema_str=None -> decode with the writer's schema, so this script does not
    # have to carry a copy of either contract that could drift from the registry.
    return AvroDeserializer(client, schema_str=None)


def _schema_id_of(payload: bytes) -> int | None:
    """Confluent wire format: magic byte 0, then a big-endian int32 schema id."""
    if len(payload) < 5 or payload[0] != 0:
        return None
    return struct.unpack(">I", payload[1:5])[0]


def consume_window(consumer, topic: str, start_ms: int, end_ms: int, deserializer, normalise):
    """
    Consume `topic` from the offsets at `start_ms` up to the first record past
    `end_ms` on each partition.

    Returns (records, consumed_count, note). `records` is None when the topic
    does not exist in the cluster at all — distinct from an empty list, which
    means the topic is there but had nothing in the window.
    """
    from confluent_kafka import KafkaException, TopicPartition
    from confluent_kafka.serialization import MessageField, SerializationContext

    try:
        md = consumer.list_topics(topic, timeout=15)
    except KafkaException as exc:
        die(f"cannot reach the broker: {exc}. See scripts/parity/README.md.")

    meta = md.topics.get(topic)
    if meta is None or meta.error is not None:
        return None, 0, f"topic `{topic}` does not exist in the cluster"

    partitions = sorted(meta.partitions)
    starts = consumer.offsets_for_times(
        [TopicPartition(topic, p, start_ms) for p in partitions], timeout=30
    )
    highs = {
        p: consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=15, cached=False)[1]
        for p in partitions
    }

    # offset < 0 means no record at or after window-start on that partition.
    assignment = [tp for tp in starts if tp.offset >= 0 and tp.offset < highs[tp.partition]]
    if not assignment:
        return [], 0, f"topic `{topic}` empty in window"

    consumer.assign(assignment)
    ctx = SerializationContext(topic, MessageField.VALUE)
    pending = {tp.partition for tp in assignment}
    records, consumed, idle = [], 0, 0

    while pending:
        msg = consumer.poll(POLL_TIMEOUT_S)
        if msg is None:
            idle += 1
            if idle >= IDLE_POLLS_BEFORE_GIVING_UP:
                # This truncates the read, so it has to reach the artefact and not
                # just the terminal: a one-sided truncation renders a fake FAIL and
                # a symmetric one a fake PASS, either way with a table that looks
                # authoritative and says nothing about being short.
                truncated = (
                    f"{TRUNCATION_NOTE} — `{topic}` returned no records for "
                    f"{IDLE_POLLS_BEFORE_GIVING_UP * POLL_TIMEOUT_S:.0f}s with "
                    f"{len(pending)} of {len(assignment)} partition(s) still short of "
                    "the window end. Counts below are a lower bound on this topic and "
                    "the verdict is not evidence."
                )
                print(f"warning: {truncated}", file=sys.stderr)
                return records, consumed, truncated
            continue
        idle = 0
        if msg.error():
            die(f"{topic}: consume error: {msg.error()}")

        partition, offset = msg.partition(), msg.offset()
        _, ts_ms = msg.timestamp()
        if ts_ms > end_ms:
            pending.discard(partition)
            consumer.pause([TopicPartition(topic, partition)])
            continue

        value = msg.value()
        if value is not None:
            try:
                decoded = deserializer(value, ctx)
            except Exception as exc:  # noqa: BLE001 - re-raised with context
                sid = _schema_id_of(value)
                die(
                    f"{topic} p{partition}@{offset}: could not decode Avro "
                    f"(schema id {sid if sid is not None else 'absent — not Confluent-framed'}): "
                    f"{exc}"
                )
            try:
                records.append(normalise(decoded))
            except (KeyError, ValueError) as exc:
                die(f"{topic} p{partition}@{offset}: cannot normalise record: {exc}")
            consumed += 1

        if offset + 1 >= highs[partition]:
            pending.discard(partition)

    return records, consumed, None


def make_consumer(brokers: str):
    from confluent_kafka import Consumer

    return Consumer(
        {
            "bootstrap.servers": brokers,
            # Unique group, no commits: this is a read-only measurement and must
            # never move a real consumer's offsets.
            "group.id": f"k2-parity-{uuid.uuid4()}",
            "enable.auto.commit": False,
            "auto.offset.reset": "error",
            "session.timeout.ms": 30000,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not ISO 8601 (want e.g. 2026-08-26T10:00:00Z)"
        ) from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # naive = UTC


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n── ")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--exchange", required=True, choices=EXCHANGES)
    p.add_argument("--window-start", required=True, type=parse_iso, help="ISO 8601, UTC if naive")
    p.add_argument("--window-end", required=True, type=parse_iso, help="ISO 8601, UTC if naive")
    p.add_argument("--brokers", default=DEFAULT_BROKERS)
    p.add_argument("--registry", default=DEFAULT_REGISTRY)
    p.add_argument("--json", action="store_true", help="machine-readable output instead of markdown")
    p.add_argument(
        "--v2-only",
        action="store_true",
        help="consume only the v2 topic and print per-symbol counts. No verdict — "
        "this is the smoke test that decoding and offsets_for_times work before "
        "the Rust tier is producing anything.",
    )
    args = p.parse_args(argv)
    if args.window_end <= args.window_start:
        p.error("--window-end must be after --window-start")
    age = (datetime.now(timezone.utc) - args.window_end).total_seconds()
    if age < MIN_WINDOW_END_AGE_S:
        p.error(
            f"--window-end is {age:.0f}s in the past; it must be at least "
            f"{MIN_WINDOW_END_AGE_S}s. The two topics are read one after the other, "
            "so a window that is still being written into gives v3 every record "
            "produced during the v2 read and v2 none of them — a one-sided deficit "
            "that is an artefact of this tool, not a parity finding."
        )
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    start_ms = int((args.window_start - EPOCH) // timedelta(milliseconds=1))
    end_ms = int((args.window_end - EPOCH) // timedelta(milliseconds=1))
    v2_topic = V2_TOPIC.format(exchange=args.exchange)
    v3_topic = V3_TOPIC.format(exchange=args.exchange)

    deserializer = build_deserializer(args.registry)
    consumer = make_consumer(args.brokers)
    notes = []
    try:
        v2_rows, v2_consumed, note = consume_window(
            consumer, v2_topic, start_ms, end_ms, deserializer, normalise_v2
        )
        if note:
            notes.append(note)
        if v2_rows is None:
            die(f"{note} — nothing to compare against")

        if args.v2_only:
            counts = Counter(n.canonical_symbol for n in v2_rows)
            if args.json:
                print(json.dumps({"topic": v2_topic, "consumed": v2_consumed,
                                  "counts": dict(sorted(counts.items()))}, indent=2))
            else:
                print(f"# v2-only smoke — `{v2_topic}`\n")
                print(f"- window: `{args.window_start}` → `{args.window_end}`")
                print(f"- consumed: {v2_consumed:,} records, {len(counts)} symbols\n")
                print("| symbol | v2 |\n|---|---:|")
                for symbol, n in sorted(counts.items()):
                    print(f"| {symbol} | {n:,} |")
                if not counts:
                    print("\n(no records in window)")
            # The same gate the full comparison applies. A truncated read makes
            # these counts a lower bound, and the smoke test's whole job is to
            # say whether decoding and offsets_for_times worked over the window
            # asked for - a short read that exits 0 reports success for a window
            # it never finished reading.
            return 1 if any(n.startswith(TRUNCATION_NOTE) for n in notes) else 0

        v3_rows, v3_consumed, note = consume_window(
            consumer, v3_topic, start_ms, end_ms, deserializer, normalise_v3
        )
        if note:
            notes.append(note)
        if v3_rows is None:
            v3_rows, v3_consumed = [], 0
    finally:
        consumer.close()

    if not v2_rows and not v3_rows:
        die(
            f"empty window: no records on either `{v2_topic}` or `{v3_topic}` between "
            f"{args.window_start} and {args.window_end}. Check the window is in the past "
            "and inside the topics' retention."
        )

    stats = compare(v2_rows, v3_rows, exchange=args.exchange)
    header = {
        "exchange": args.exchange,
        "window_start": args.window_start.isoformat(),
        "window_end": args.window_end.isoformat(),
        "window_hours": (args.window_end - args.window_start).total_seconds() / 3600,
        "v2_topic": v2_topic,
        "v3_topic": v3_topic,
        "v2_consumed": v2_consumed,
        "v3_consumed": v3_consumed,
        "join_on_id": args.exchange not in SYNTHETIC_V2_ID_EXCHANGES,
        "notes": notes,
        "truncated": any(n.startswith(TRUNCATION_NOTE) for n in notes),
    }
    overall = all(s.passed for s in stats) and not header["truncated"]

    if args.json:
        print(json.dumps({**header, "overall": "PASS" if overall else "FAIL",
                          "symbols": [{**asdict(s), "delta": s.count_delta,
                                       "tolerance": s.tolerance,
                                       "verdict": "PASS" if s.passed else "FAIL"}
                                      for s in stats]}, indent=2))
    else:
        print(render_markdown(stats, header), end="")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())

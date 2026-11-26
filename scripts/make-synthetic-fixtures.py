#!/usr/bin/env python3
"""Generate every venue-format fixture in the repository from one fixed seed.

The repository ships no real venue market data (README, "What this is, and data
terms"; NOTICE). The frames the tests replay are made here instead: invented
prices, sizes, ids and timestamps on a plainly artificial scale (books centred on
1000.0, 100.00 and 10.0000, a clock that starts at 2001-09-09T01:46:40Z), in each
venue's exact wire format: the same message types, field names and key order,
number spellings, sequence behaviour and session shape the adapters were written
against. Kraken's CRC32 is computed here over the synthetic book exactly as
`kraken.rs::book_checksum` computes it, so every book frame verifies.

Outputs, all named `synthetic-*`, and `tests/fixtures/SHA256SUMS`, the manifest of
their sha256 digests (`sha256sum -c tests/fixtures/SHA256SUMS` from the root).
Check-docs.sh gate (i) fails CI on a data file that is neither in that manifest at
its hash nor listed in NOTICE, and on a `synthetic-*` file the manifest does not
list, so a file is synthetic because this script wrote it, not because of its name:

- `services/capture-rust/tests/fixtures/`: three clean sessions and three failure
  fixtures, each derived from its clean sibling by one edit (a wrong Kraken
  checksum, one missing Coinbase `sequence_num`, two Binance depth frames swapped).
- `tests/fixtures/bronze/`: one frame per bronze table, taken from those sessions.
- `docs/research/2026-08-26-v3-spikes/`: the S2 and S5 sample files.

Deterministic: same seed, same bytes; `tests/test_synthetic_fixtures.py` regenerates
and compares. After a deliberate change, run this, then re-pin the `.sha256` files:
each is what `k2-capture replay --exchange <x> --fixture <f> --instruments-file
../../config/instruments.yaml | sha256sum` prints (the replay tests print the new
digest when it moves). Real frames, for stronger evidence, come from
`make record-fixtures` into a git-ignored directory and are never committed.
"""

import datetime
import hashlib
import json
import random
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "services/capture-rust/tests/fixtures"
BRONZE = ROOT / "tests/fixtures/bronze"
SPIKES = ROOT / "docs/research/2026-08-26-v3-spikes"
MANIFEST = BRONZE.parent / "SHA256SUMS"

SEED = 20261124
E8 = 100_000_000  # the wire contract's 1e-8 fixed point
SEC = 1_000_000_000
MS = 1_000_000
T0 = 1_000_000_000 * SEC  # 2001-09-09T01:46:40Z: no venue was trading then


# ── formatting ────────────────────────────────────────────────────────────────


def dec(units: int, dp: int) -> str:
    """1e-8 units as a decimal with exactly `dp` places: Kraken and Binance spell
    every number at a fixed precision (`1000.0`, `0.25000000`)."""
    whole, frac = divmod(units, E8)
    return f"{whole}.{frac:08d}"[: len(str(whole)) + 1 + dp] if dp else str(whole)


def trim(units: int) -> str:
    """Coinbase spelling: trailing zeros dropped (`9.999`, `1.2`, `0`)."""
    return dec(units, 8).rstrip("0").rstrip(".")


def iso(ns: int, digits: int, strip: bool = False, sep: str = "T", zone: str = "Z") -> str:
    """RFC 3339 UTC at `digits` fractional digits; `strip` drops trailing zeros
    as Go's RFC3339Nano does, which is how Coinbase prints its clocks."""
    base = datetime.datetime.fromtimestamp(ns // SEC, tz=datetime.timezone.utc).strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    frac = f"{ns % SEC:09d}"[:digits]
    if strip:
        frac = frac.rstrip("0")
    return f"{base}.{frac}{zone}" if frac else f"{base}{zone}"


def compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def line(recv_ns: int, payload: str) -> str:
    """One fixture line, as `k2-capture record` writes it."""
    return compact({"recv_ts_ns": recv_ns, "payload": payload})


# ── the book ──────────────────────────────────────────────────────────────────


class Ladder:
    """One symbol's book on a fixed grid of `ticks` prices either side of a fixed
    mid. Bids and asks live on disjoint ticks, so no edit can cross the book, and
    an edit is applied exactly as `book.rs` applies a frame: absolute quantities,
    zero removes, then (for a depth-limited feed) truncate to `window` per side."""

    # ponytail: the mid never moves, so the book wanders inside a fixed band; give
    # it a walk (and re-check the no-cross guarantee) if a test needs a trend.
    def __init__(self, rng: random.Random, mid: int, tick: int, ticks: int, depth: int, lot: int, window=None):
        self.rng, self.mid, self.tick, self.ticks, self.lot, self.window = rng, mid, tick, ticks, lot, window
        self.side = {s: {self.px(s, k): self.size() for k in range(1, depth + 1)} for s in ("bids", "asks")}

    def px(self, side: str, k: int) -> int:
        return self.mid - k * self.tick if side == "bids" else self.mid + k * self.tick

    def size(self) -> int:
        return (1 + self.rng.randrange(499)) * self.lot

    def pick(self, seq):
        return seq[self.rng.randrange(len(seq))]

    def top(self, side: str, n: int) -> list[tuple[int, int]]:
        """Best `n`: bids descending, asks ascending."""
        return sorted(self.side[side].items(), reverse=side == "bids")[:n]

    def edit(self, side: str) -> list[tuple[int, int]]:
        """One venue-style change to `side`, applied, and returned as the levels
        the venue publishes for it. Half re-size a resting level; a quarter
        delete one and bring the next level into view behind the worst (Kraken's
        delete-then-insert pair); a quarter insert inside the book, which on a
        depth-limited feed pushes the worst level out with no message for it."""
        book = self.side[side]
        outside = (lambda p, w: p < w) if side == "bids" else (lambda p, w: p > w)
        free = [p for p in (self.px(side, k) for k in range(1, self.ticks + 1)) if p not in book]
        r = self.rng.random()
        if r < 0.5 or not free:
            out = [(self.pick(sorted(book)), self.size())]
        elif r < 0.75:
            gone = self.pick(sorted(book))
            rest = [p for p in book if p != gone]
            worst = min(rest) if side == "bids" else max(rest)
            out = [(gone, 0), (self.pick([p for p in free if outside(p, worst)] or free), self.size())]
        else:
            worst = min(book) if side == "bids" else max(book)
            out = [(self.pick([p for p in free if not outside(p, worst)] or free), self.size())]
        for p, q in out:
            if q:
                book[p] = q
            else:
                book.pop(p, None)
        if self.window:
            for p, _ in sorted(book.items(), reverse=side == "bids")[self.window :]:
                del book[p]
        return out


def kraken_checksum(ladder: Ladder, price_dp: int = 1, qty_dp: int = 8) -> int:
    """`kraken.rs::book_checksum`: top 10 asks then top 10 bids, each price and
    qty at the pair's precision with the point and leading zeros removed, CRC32."""
    digits = "".join(
        f"{p // 10 ** (8 - price_dp)}{q // 10 ** (8 - qty_dp)}"
        for p, q in ladder.top("asks", 10) + ladder.top("bids", 10)
    )
    return zlib.crc32(digits.encode())


def spread(rng: random.Random, n: int, start: int, end: int) -> list[int]:
    """`n` increasing times in [start, end): an even grid with jitter inside each
    slot, so every stretch of the session has frames in it."""
    step = (end - start) // n
    return [start + i * step + rng.randrange(step // 2) for i in range(n)]


def merge(events: list[tuple[int, int, str]]) -> list[str]:
    """(recv_ns, tiebreak, payload) events into fixture lines in arrival order."""
    return [line(t, p) for t, _, p in sorted(events)]


# ── Kraken: WS v2, instrument + book depth 25 + trade on one socket ───────────

K_SYMBOL = "BTC/USD"


def kraken_levels(levels: list[tuple[int, int]]) -> str:
    # Built as text: Kraken prices and quantities are JSON numbers at a fixed
    # precision, and json.dumps would re-spell 0.25000000 as 0.25.
    return "[" + ",".join(f'{{"price":{dec(p, 1)},"qty":{dec(q, 8)}}}' for p, q in levels) + "]"


def kraken_book(kind: str, bids, asks, checksum: int, ts: int) -> str:
    return (
        f'{{"channel":"book","type":"{kind}","data":[{{"symbol":"{K_SYMBOL}","bids":{kraken_levels(bids)},'
        f'"asks":{kraken_levels(asks)},"checksum":{checksum},"timestamp":"{iso(ts, 6)}"}}]}}'
    )


def kraken_session(rng: random.Random) -> list[str]:
    """20 s of one symbol, the frame mix a live BTC/USD session has: status, three
    acks, the instrument snapshot (precision 1 / 8, the checksum's inputs), the
    book snapshot, a heartbeat a second, three trades and 1156 book updates, of
    which every one carries the checksum of the book after it."""
    ladder = Ladder(rng, mid=1000 * E8, tick=E8 // 10, ticks=30, depth=25, lot=E8 // 100, window=25)
    lag = 50 * MS  # venue clock to receipt, constant
    ev: list[tuple[int, int, str]] = []

    def ack(t, result):
        ev.append((t, 0, compact({"method": "subscribe", "result": result, "success": True,
                                  "time_in": iso(t - lag, 6), "time_out": iso(t - lag + 20_000, 6)})))

    ev.append((T0, 0, compact({"channel": "status", "type": "update", "data": [
        {"version": "2.0.10", "system": "online", "api_version": "v2", "connection_id": 1}]})))
    ack(T0 + 300 * MS, {"channel": "instrument", "snapshot": True, "warnings": ["tick_size is deprecated, use price_increment"]})
    ack(T0 + 300 * MS + 40_000, {"channel": "book", "depth": 25, "snapshot": True, "symbol": K_SYMBOL})
    ack(T0 + 300 * MS + 80_000, {"channel": "trade", "snapshot": False, "symbol": K_SYMBOL})
    ev.append((T0 + 920 * MS, 0, compact({"channel": "instrument", "type": "snapshot", "data": {"assets": [], "pairs": [
        {"symbol": K_SYMBOL, "base": "BTC", "quote": "USD", "status": "online", "qty_precision": 8,
         "qty_increment": 1e-08, "price_precision": 1, "cost_precision": 5, "marginable": True, "has_index": True,
         "ws_display_price_precision": 1, "cost_min": 1.0, "margin_initial": 0.5, "position_limit_long": 1000,
         "position_limit_short": 1000, "tick_size": 0.1, "price_increment": 0.1, "qty_min": 0.01}]}})))
    t = T0 + 940 * MS
    ev.append((t, 0, kraken_book("snapshot", ladder.top("bids", 25), ladder.top("asks", 25), kraken_checksum(ladder), t - lag)))
    for j in range(20):
        ev.append((t + 50_000 + j * SEC, 0, '{"channel":"heartbeat"}'))

    # Trades and updates are placeholders here, built in arrival order below so
    # each is priced against, or applied to, the book of its own moment.
    ev += [(T0 + s * SEC + 123 * MS, 1, "") for s in (5, 10, 15)]
    ev += [(ts, 2, "") for ts in spread(rng, 1156, T0 + 950 * MS, T0 + 19_950 * MS)]

    out, trade_id = [], 0
    for ts, kind, payload in sorted(ev):
        if kind == 1:
            trade_id += 1
            side = "buy" if trade_id % 2 else "sell"
            px = ladder.top("asks" if side == "buy" else "bids", 1)[0][0]
            payload = (
                f'{{"channel":"trade","type":"update","data":[{{"symbol":"{K_SYMBOL}","side":"{side}",'
                f'"price":{dec(px, 1)},"qty":{dec(ladder.size(), 8)},"ord_type":"market",'
                f'"trade_id":{trade_id},"timestamp":"{iso(ts - lag, 6)}"}}]}}'
            )
        elif kind == 2:
            side = "bids" if rng.random() < 0.5 else "asks"
            levels = ladder.edit(side)
            bids, asks = (levels, []) if side == "bids" else ([], levels)
            payload = kraken_book("update", bids, asks, kraken_checksum(ladder), ts - lag)
        out.append(line(ts, payload))
    return out


# ── Binance: combined stream, <sym>@trade + <sym>@depth20@100ms ──────────────

B_SYMBOL = "BTCUSDT"


def binance_session(rng: random.Random) -> list[str]:
    """10 s of one symbol: a depth20 frame every 100 ms on the dot (101 of them,
    `lastUpdateId` strictly increasing) and 438 trades between them, priced at
    the touch. The 100 ms before the first grid line carries no trade, so the
    depth frames either side of it are adjacent lines: the pair the regression
    fixture swaps."""
    ladder = Ladder(rng, mid=100 * E8, tick=E8 // 100, ticks=30, depth=25, lot=E8 // 1000, window=25)
    stream = B_SYMBOL.lower()
    ev = [(T0 + k * 100 * MS, 0, "") for k in range(101)]
    slots = [k for k in range(100) if k != 9]
    for _ in range(438):
        ev.append((T0 + rng.choice(slots) * 100 * MS + MS + rng.randrange(98 * MS), 1, ""))

    out, update_id, trade_id = [], 1000, 0
    for ts, kind, _ in sorted(ev):
        if kind == 0:
            for _ in range(rng.randrange(4)):
                ladder.edit("bids" if rng.random() < 0.5 else "asks")
            update_id += 1 + rng.randrange(20)
            data = {"lastUpdateId": update_id,
                    "bids": [[dec(p, 8), dec(q, 8)] for p, q in ladder.top("bids", 20)],
                    "asks": [[dec(p, 8), dec(q, 8)] for p, q in ladder.top("asks", 20)]}
            payload = compact({"stream": f"{stream}@depth20@100ms", "data": data})
        else:
            trade_id += 1
            maker_bid = rng.random() < 0.5  # `m`: the buyer rested, so the aggressor sold at the bid
            px = ladder.top("bids" if maker_bid else "asks", 1)[0][0]
            ms = (ts - 5 * MS) // MS
            payload = compact({"stream": f"{stream}@trade", "data": {
                "e": "trade", "E": ms, "s": B_SYMBOL, "t": trade_id, "p": dec(px, 8),
                "q": dec(ladder.size(), 8), "T": ms, "m": maker_bid, "M": True}})
        out.append(line(ts, payload))
    return out


# ── Coinbase: Advanced Trade, level2 + market_trades + heartbeats ────────────

C_SYMBOL = "ATOM-USD"


def coinbase_frame(channel: str, ts: int, seq: int, events: list) -> str:
    return compact({"channel": channel, "timestamp": iso(ts, 9, strip=True), "sequence_num": seq, "events": events})


def coinbase_levels(side_levels, ts: int) -> list:
    return [{"side": "bid" if s == "bids" else "offer", "event_time": iso(ts, 6, strip=True),
             "price_level": trim(p), "new_quantity": trim(q)} for s, levels in side_levels for p, q in levels]


def coinbase_updates(rng: random.Random, ladder: Ladder, ts: int, seq: int, product: str) -> str:
    """One `l2_data` update: one to six level changes across both sides."""
    changes = []
    for _ in range(1 + rng.randrange(6)):
        side = "bids" if rng.random() < 0.5 else "asks"
        changes.append((side, ladder.edit(side)))
    event = {"type": "update", "product_id": product, "updates": coinbase_levels(changes, ts - 10 * MS)}
    return coinbase_frame("l2_data", ts, seq, [event])


def coinbase_session(rng: random.Random) -> list[str]:
    """20 s of one product on one connection-wide `sequence_num` (0..158): the
    full-depth `level2` snapshot first, the three subscription acks, the
    `market_trades` snapshot of recent history, a heartbeat a second and 134
    `l2_data` updates."""
    ladder = Ladder(rng, mid=10 * E8, tick=E8 // 10_000, ticks=150, depth=100, lot=E8 // 100)
    lag = 20 * MS
    ev = [(T0 + 2 * MS + j * SEC, 1, j) for j in range(20)]
    ev += [(ts, 2, 0) for ts in spread(rng, 134, T0 + 50 * MS, T0 + 19_900 * MS)]

    snap = {"type": "snapshot", "product_id": C_SYMBOL,
            "updates": coinbase_levels([("bids", ladder.top("bids", 999)), ("asks", ladder.top("asks", 999))], T0 - lag - 10 * MS)}
    history = [{"product_id": C_SYMBOL, "trade_id": str(1020 - i), "price": trim(ladder.mid + (1 if i % 3 else -1) * ladder.tick),
                "size": trim(ladder.size()), "time": iso(T0 - (i + 1) * 500 * MS, 6, strip=True),
                "side": "BUY" if i % 3 else "SELL"} for i in range(20)]
    subs = [{"level2": [C_SYMBOL]}, {"level2": [C_SYMBOL], "market_trades": [C_SYMBOL]},
            {"heartbeats": ["heartbeats"], "level2": [C_SYMBOL], "market_trades": [C_SYMBOL]}]
    head = [
        (T0, "l2_data", [snap]),
        (T0 + 1_000_000, "subscriptions", [{"subscriptions": subs[0]}]),
        (T0 + 1_010_000, "market_trades", [{"type": "snapshot", "trades": history}]),
        (T0 + 1_020_000, "subscriptions", [{"subscriptions": subs[1]}]),
        (T0 + 1_030_000, "subscriptions", [{"subscriptions": subs[2]}]),
    ]
    out = [line(t, coinbase_frame(ch, t - lag, seq, events)) for seq, (t, ch, events) in enumerate(head)]
    for seq, (ts, kind, j) in enumerate(sorted(ev), start=len(head)):
        if kind == 1:
            beat = {"current_time": iso(ts - lag, 9, strip=True, sep=" ", zone=" +0000 UTC") + f" m=+{j + 1}.000000001",
                    "heartbeat_counter": j + 1}
            payload = coinbase_frame("heartbeats", ts - lag, seq, [beat])
        else:
            payload = coinbase_updates(rng, ladder, ts - lag, seq, C_SYMBOL)
        out.append(line(ts, payload))
    return out


# ── failure fixtures: one edit each on a clean session ───────────────────────


def payload_of(fixture_line: str) -> str:
    return json.loads(fixture_line)["payload"]


def recv_of(fixture_line: str) -> int:
    return json.loads(fixture_line)["recv_ts_ns"]


def kind(fixture_line: str) -> tuple:
    """(channel, type) of a frame: Kraken's own pair, Coinbase's channel and
    first event type, Binance's stream suffix and None. Acks are (None, None)."""
    p = json.loads(payload_of(fixture_line))
    if "stream" in p:
        return p["stream"].split("@", 1)[1], None
    return p.get("channel"), p.get("type", (p.get("events") or [{}])[0].get("type"))


def split(fixture_line: str) -> tuple[str, str]:
    """`{"recv_ts_ns":N,"payload":` and the escaped payload string after it, so an
    edit touches only the payload and every other byte survives."""
    i = fixture_line.index('"payload":') + len('"payload":')
    return fixture_line[:i], fixture_line[i:]


def kraken_checksum_fail(clean: list[str]) -> tuple[list[str], str]:
    """The first `book` update at or after T0 + 9.5 s says checksum 1: the ninth
    sampler tick has passed, the tenth has not, so the clean session's 19
    snapshots become 9 verified ones plus the one marked `checksum_ok=false`."""
    i = next(i for i, x in enumerate(clean) if recv_of(x) >= T0 + 9_500 * MS and kind(x) == ("book", "update"))
    want = json.loads(payload_of(clean[i]))["data"][0]["checksum"]
    assert want != 1 and recv_of(clean[i]) < T0 + 10 * SEC
    head, body = split(clean[i])
    out = list(clean)
    out[i] = head + body.replace(f'\\"checksum\\":{want}', '\\"checksum\\":1', 1)
    ignored = sum(kind(x) == ("book", "update") for x in clean[i + 1 :])
    return out, f"line {i + 1}: checksum {want} -> 1, {ignored} book updates after it"


def coinbase_seq_gap(clean: list[str]) -> tuple[list[str], str]:
    """The first `l2_data` update at or after T0 + 9.5 s never arrives: the
    heartbeat at T0 + 9.002 s has already taken the ninth sample."""
    i = next(i for i, x in enumerate(clean) if recv_of(x) >= T0 + 9_500 * MS and kind(x) == ("l2_data", "update"))
    frame = json.loads(payload_of(clean[i]))
    assert recv_of(clean[i]) < T0 + 10 * SEC
    return clean[:i] + clean[i + 1 :], f"line {i + 1} deleted (sequence_num {frame['sequence_num']})"


def binance_regression(clean: list[str]) -> tuple[list[str], str]:
    """The depth frames at T0 + 0.9 s and T0 + 1.0 s, adjacent by construction,
    arrive with their payloads swapped: `lastUpdateId` regresses on the frame
    that crosses the first grid line, so that line samples no book."""
    a = next(i for i, x in enumerate(clean) if recv_of(x) == T0 + 900 * MS)
    ids = [json.loads(payload_of(x))["data"]["lastUpdateId"] for x in clean[a : a + 2]]
    assert recv_of(clean[a + 1]) == T0 + SEC and len(ids) == 2
    (h1, p1), (h2, p2) = split(clean[a]), split(clean[a + 1])
    out = list(clean)
    out[a], out[a + 1] = h1 + p2, h2 + p1
    return out, f"lines {a + 1} and {a + 2} swapped: lastUpdateId {ids[1]} arrives before {ids[0]}"


# ── everything ────────────────────────────────────────────────────────────────


def spike_line(payload: str, recv_ns: int) -> str:
    """The spikes' own format: the parsed frame plus `recv_ts_ns`, json.dumps defaults."""
    return json.dumps(dict(json.loads(payload), recv_ts_ns=recv_ns))


def build() -> tuple[dict[Path, str], list[str]]:
    """Every output path and its exact content, plus what each derived fixture
    changed (the facts the replay tests and the capture README quote)."""
    rng = random.Random(SEED)
    kraken, binance, coinbase = kraken_session(rng), binance_session(rng), coinbase_session(rng)
    derived = {
        "synthetic-kraken-20s-checksum-fail.jsonl": kraken_checksum_fail(kraken),
        "synthetic-coinbase-20s-seq-gap.jsonl": coinbase_seq_gap(coinbase),
        "synthetic-binance-10s-regression.jsonl": binance_regression(binance),
    }
    files = {
        CAPTURE / "synthetic-kraken-20s.jsonl": kraken,
        CAPTURE / "synthetic-binance-10s.jsonl": binance,
        CAPTURE / "synthetic-coinbase-20s.jsonl": coinbase,
        **{CAPTURE / name: lines for name, (lines, _) in derived.items()},
    }
    out = {path: "\n".join(lines) + "\n" for path, lines in files.items()}

    def first(lines, k):
        return payload_of(next(x for x in lines if kind(x) == k))

    bronze = {
        "binance_trade": first(binance, ("trade", None)),
        "binance_depth20": first(binance, ("depth20@100ms", None)),
        "kraken_trade": first(kraken, ("trade", "update")),
        "kraken_book": first(kraken, ("book", "update")),
        "kraken_instrument": first(kraken, ("instrument", "snapshot")),
        "coinbase_market_trades": first(coinbase, ("market_trades", "snapshot")),
        "coinbase_level2": first(coinbase, ("l2_data", "update")),
    }
    out.update({BRONZE / f"synthetic-{name}.json": payload + "\n" for name, payload in bronze.items()})

    # S2 kept the first 20 lines of a book-only capture; S5 the first three
    # BTC-USD updates after the snapshot burst.
    book = [x for x in kraken if kind(x)[0] == "book"][:20]
    out[SPIKES / "s2-kraken-instrument/synthetic-kraken-book.sample.jsonl"] = "".join(
        spike_line(payload_of(x), recv_of(x)) + "\n" for x in book)
    btc = Ladder(rng, mid=1000 * E8, tick=E8 // 100, ticks=150, depth=100, lot=E8 // 1000)
    out[SPIKES / "s5-coinbase-level2/synthetic-coinbase.sample.jsonl"] = "".join(
        spike_line(coinbase_updates(rng, btc, T0 + n * 300 * MS, n, "BTC-USD"), T0 + n * 300 * MS + 20 * MS) + "\n"
        for n in (1, 2, 3))
    out[MANIFEST] = "".join(
        f"{hashlib.sha256(text.encode()).hexdigest()}  {path.relative_to(ROOT)}\n" for path, text in sorted(out.items()))
    return out, [f"{name}: {what}" for name, (_, what) in derived.items()]


if __name__ == "__main__":
    files, facts = build()
    for path, text in files.items():
        path.write_text(text)
        print(f"{path.relative_to(ROOT)}: {text.count(chr(10))} lines")
    print(*facts, sep="\n")

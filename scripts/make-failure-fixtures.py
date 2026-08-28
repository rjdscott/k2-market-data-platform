#!/usr/bin/env python3
"""Derive the three failure fixtures from the clean recorded ones.

Each output is its input with exactly one disturbance, so the failing frame is
real venue output rather than something hand-written: a wrong Kraken checksum, a
missing Coinbase `sequence_num`, an out-of-order Binance `lastUpdateId`. The
captures are stopped and these failures never occurred on the wire during a
recording window, so they are manufactured here rather than waited for.

Idempotent: run it after re-recording a clean fixture, then regenerate the
`.sha256` files (the command is in `services/capture-rust/tests/replay_failures.rs`)
and update the counts asserted there. Edits are textual on the `payload` string so
every other byte of every other line survives untouched.
"""

import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "services/capture-rust/tests/fixtures"
PAYLOAD = '"payload":'


def read(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def write(name: str, lines: list[str]) -> None:
    (FIXTURES / name).write_text("\n".join(lines) + "\n")
    print(f"{name}: {len(lines)} lines")


def split(line: str) -> tuple[str, str]:
    """`{"recv_ts_ns":N,"payload":` and the escaped payload string after it."""
    i = line.index(PAYLOAD) + len(PAYLOAD)
    return line[:i], line[i:]


def kraken() -> None:
    """Line 600's `book` update carries a checksum the local book cannot match."""
    lines = read("kraken-20s.jsonl")
    head, payload = split(lines[599])
    assert r'\"checksum\":1925110775' in payload, "line 600 is not the recorded book update"
    lines[599] = head + payload.replace(r'\"checksum\":1925110775', r'\"checksum\":1', 1)
    write("kraken-20s-checksum-fail.jsonl", lines)


def coinbase() -> None:
    """Line 81 (`sequence_num` 80) never arrives, so 79 is followed by 81."""
    lines = read("coinbase-20s.jsonl")
    frame = json.loads(json.loads(lines[80])["payload"])
    assert (frame["channel"], frame["sequence_num"], frame["events"][0]["type"]) == (
        "l2_data",
        80,
        "update",
    ), "line 81 is not the recorded l2_data update at sequence_num 80"
    del lines[80]
    write("coinbase-20s-seq-gap.jsonl", lines)


def binance() -> None:
    """Two adjacent depth20 frames arrive swapped, so `lastUpdateId` regresses."""
    lines = read("binance-10s.jsonl")
    ids = [json.loads(json.loads(x)["payload"])["data"]["lastUpdateId"] for x in lines[66:68]]
    assert ids == [99193217500, 99193217508], f"lines 67-68 are not the recorded depth frames: {ids}"
    (h1, p1), (h2, p2) = split(lines[66]), split(lines[67])
    lines[66], lines[67] = h1 + p2, h2 + p1
    write("binance-10s-regression.jsonl", lines)


if __name__ == "__main__":
    kraken()
    coinbase()
    binance()

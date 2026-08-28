#!/usr/bin/env python3
"""
One archived WebSocket connection out of `raw.messages`, at a pinned snapshot,
in the JSONL shape `k2-capture replay` reads (Phase G, ADR-029).

    # which connections does the archive hold for a venue?
    uv run --no-project --with duckdb==1.4.4 --with pytz --with fastavro==1.12.2 \
        python scripts/replay_export.py --exchange kraken --list

    # one connection, every frame, in conn_msg_seq order, as a replay input
    uv run ... python scripts/replay_export.py --exchange kraken \
        --snapshot-id 8675983916383659458 --conn-id 1dfb9139-ef8d-45cb-a0d9-3c677c1560ee > conn.jsonl
    k2-capture replay --exchange kraken --fixture conn.jsonl --conn-id 1dfb9139-...

A connection is the unit, not a time window: the adapters need the frames a
subscribe produces first (Kraken `instrument` for precision, the `book`
snapshot, Coinbase's `level2` snapshot) before a delta means anything, and
exchange sequence numbers only run within one `conn_id` (raw-message.avsc). A
replay that starts mid-connection produces trades and no books, silently.
`--until` trims the tail, never the head.

Reads from the HOST through the published ports, like scripts/parity_ohlcv.py
and notebooks/k2lake.py. The Avro body is decoded here (fastavro against
schemas/avro/raw-message.avsc, after the 5-byte Confluent header) so the Rust
binary needs no lake client: a fixture and a lake export are one input shape.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

from fastavro import parse_schema, schemaless_reader

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
ROOT = Path(__file__).resolve().parent.parent
RAW_SCHEMA = parse_schema(json.loads((ROOT / "schemas" / "avro" / "raw-message.avsc").read_text()))

# The bronze table that carries every connection of a venue with its
# conn_msg_seq range — cheap to group (1 s for 29 connections, 2026-08-28)
# where decoding raw.messages to find the same thing is a 40 M-row scan.
CONNECTIONS_TABLE = {
    "kraken": "lake.bronze.kraken_book",
    "binance": "lake.bronze.binance_depth20",
    "coinbase": "lake.bronze.coinbase_level2",
}


def env() -> dict:
    """MINIO_* from the repo's .env when not already in the environment."""
    e = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                e.setdefault(k.strip(), v.strip())
    return e


def duck():
    # Imported here so tests of the pure part (`frames`) need no duckdb.
    import duckdb

    e = env()
    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'")
    c.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    c.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{e["MINIO_ROOT_USER"]}', SECRET '{e["MINIO_ROOT_PASSWORD"]}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    c.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return c


def connections(c, exchange: str) -> list:
    """(conn_id, frames, first_recv_ts, last_recv_ts) per archived connection, oldest first."""
    return c.execute(
        f"SELECT conn_id, count(*), min(recv_ts)::VARCHAR, max(recv_ts)::VARCHAR "
        f"FROM {CONNECTIONS_TABLE[exchange]} GROUP BY 1 ORDER BY 3"
    ).fetchall()


def decode(payload: bytes) -> dict | None:
    """The RawMessage inside a Confluent-framed value, or None for an un-framed row."""
    if len(payload) < 6 or payload[0] != 0:
        return None
    return schemaless_reader(io.BytesIO(payload[5:]), RAW_SCHEMA)


def frames(rows, conn_id: str, until_ns: int | None = None) -> list:
    """Fixture lines for `conn_id`, sorted by conn_msg_seq, from (schema_id, payload) rows.

    Pure so tests can feed it encoded rows. Sorting is on the archive's own
    counter, not on Kafka offset: the raw topic is keyed by symbol, so one
    connection's frames are spread over partitions and only conn_msg_seq
    restores the order the socket delivered them in.

    The counter is monotonic from 1 and unbroken within a connection
    (raw-message.avsc), so anything other than exactly 1..n here means the
    export is not the whole connection — a frame the scan window missed, or a
    duplicate — and a truncated replay produces plausible-looking output from
    an incomplete book. That is a `SystemExit`, not a warning.
    """
    out = []
    for schema_id, payload in rows:
        if schema_id is None:
            continue
        rec = decode(bytes(payload))
        if rec is None or rec["conn_id"] != conn_id:
            continue
        if until_ns is not None and rec["recv_ts_ns"] > until_ns:
            continue
        out.append((rec["conn_msg_seq"], rec["recv_ts_ns"], rec["payload"]))
    out.sort()
    seqs = [seq for seq, _, _ in out]
    if seqs and seqs != list(range(1, len(seqs) + 1)):
        raise SystemExit(
            f"{conn_id}: conn_msg_seq is not 1..n unbroken — {len(seqs)} frames, "
            f"first {seqs[0]}, last {seqs[-1]}; the export would be a truncated connection"
        )
    return [{"recv_ts_ns": ts, "payload": pl.decode("utf-8")} for _, ts, pl in out]


def export(c, exchange: str, snapshot_id: int, conn_id: str, until_ns: int | None, out) -> int:
    bounds = {r[0]: (r[2], r[3]) for r in connections(c, exchange)}
    if conn_id not in bounds:
        sys.exit(f"{conn_id} is not a connection {CONNECTIONS_TABLE[exchange]} knows; --list prints them")
    first, last = bounds[conn_id]
    # kafka_ts is the producer's CreateTime, within seconds of recv_ts, and the
    # bounds come from the bronze table, which only holds the venue's book
    # frames. The tail needs 2 minutes, not 1: a continuous stream must be
    # silent 60 s before the session watchdog reconnects (CONTINUOUS in
    # services/capture-rust/src/main.rs), so the last frames of a connection —
    # a heartbeat, the close — can land a minute after bronze's last book row.
    # A minute at the head is enough: nothing precedes the subscribe.
    # The conn_id filter below does the exact cut, and `frames` refuses to
    # return a set of frames whose conn_msg_seq is not 1..n.
    #
    # `schema_id > 0` and not `IS NOT NULL`: DuckDB 1.4.4's Iceberg reader returned zero rows for the
    # IS NOT NULL form on this table (no column stats on schema_id), while
    # count(schema_id) over the same rows was 51,544 (2026-08-28).
    cur = c.execute(
        f"""SELECT schema_id, payload FROM lake.raw.messages AT (VERSION => {int(snapshot_id)})
            WHERE topic = 'market.crypto.v3.raw.{exchange}' AND schema_id > 0
              AND kafka_ts >= TIMESTAMP '{first}' - INTERVAL 1 MINUTE
              AND kafka_ts <= TIMESTAMP '{last}' + INTERVAL 2 MINUTE"""
    )
    rows = []
    while batch := cur.fetchmany(20_000):
        rows.extend(batch)
    lines = frames(rows, conn_id, until_ns)
    for line in lines:
        out.write(json.dumps(line, separators=(",", ":")) + "\n")
    return len(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exchange", choices=sorted(CONNECTIONS_TABLE), required=True)
    ap.add_argument("--list", action="store_true", help="print the archived connections and exit")
    ap.add_argument("--snapshot-id", type=int, help="lake.raw.messages snapshot id to read at (pinned, never latest)")
    ap.add_argument("--conn-id", help="the connection to export, whole")
    ap.add_argument("--until", help="drop frames received after this UTC instant (ISO 8601); the head is never trimmed")
    ap.add_argument("--out", default="-", help="file, or - for stdout")
    args = ap.parse_args()

    c = duck()
    if args.list:
        for conn_id, n, first, last in connections(c, args.exchange):
            print(f"{conn_id}\t{n:>9}\t{first}\t{last}")
        return
    if not (args.snapshot_id and args.conn_id):
        ap.error("--snapshot-id and --conn-id are required unless --list")
    until_ns = None
    if args.until:
        from datetime import datetime, timezone

        until_ns = int(datetime.fromisoformat(args.until.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1e9)
    out = sys.stdout if args.out == "-" else open(args.out, "w")
    n = export(c, args.exchange, args.snapshot_id, args.conn_id, until_ns, out)
    print(f"{n} frames of {args.conn_id} at raw.messages snapshot {args.snapshot_id}", file=sys.stderr)


if __name__ == "__main__":
    main()

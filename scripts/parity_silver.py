#!/usr/bin/env python3
"""make parity-silver (plan 004, step W1-18): DuckDB recomputes a Kafka product's silver_trade from its
landing, at the snapshots one of its builds read, and compares the result with the silver that
build published: keys, counts and every column, each difference reported by its class.

    parity_silver.py --release <release dir> --product <domain/name> [--run <dag_id>/<run_id>]

Run in a tools one-shot of the live release (mk/parity.mk), the one place on the catalog's network,
with the tools MinIO key alone (K2_TOOLS_S3_ACCESS_KEY and _SECRET_KEY, by name for that run): its
policy reads every warehouse's objects and writes none (docker/lake/minio/policies/tools.json), so
Iceberg's files are read straight through Lakekeeper and never through Trino, where no principal
reads the venue's rows. DuckDB and its extensions are the image's (docker/tools/Dockerfile), loaded
from its own directory with autoinstall off: nothing is fetched at run time.

The pin is a run record, never a file: the build's `audited` row of silver_trade (the newest, or
--run's) and its `started` row, both written by the dbt wrapper. They name silver's tested snapshot
and the security master's snapshot the build resolved symbols with (`inputs`); bronze, which the
build reads at main, is the newest connector commit on bronze's main lineage committed before the
build started (its DAG run publishes bronze, then builds, one run at a time), and that commit's
summary names the landing snapshot it staged from and its through-offsets. Nothing is written, so
the same --run compares the same snapshots for as long as they are held.

The recompute, from the landing's bytes and nothing dbt or Spark made of them: every landing row of
the topic's partitions below bronze's through-offsets; its Confluent framing (docker/lake/wire.py)
and its Avro body, decoded here by its writer schema (fetched from the registry by the frame's id)
with this file's own reader; the connector's filter (stream in its values); the payload as UTF-8
JSON, its numbers read as exact decimals. Then in DuckDB: one row per element of a trade frame's
data array with its ordinal, each field typed, the symbol resolved by an as-of join to the master's
version open at the trade's time; a frame is in silver only when every trade in it types and
resolves; the first delivery of a (venue_symbol, trade_id) by (partition, offset, ordinal) is the
original and every later one is_duplicate.

Classes, one line each, `PASS|FAIL <class>: <detail>`: duplicate keys on either side, missing (in
the recompute, not in silver), extra (in silver, not in the recompute), one per column whose values
differ on a key both hold, and undecoded (a kept landing frame whose envelope does not decode, which
bronze's audit should have stopped). INFO lines account for the landing rows read.

Exit 0 every class empty; 1 a class holds a difference; 2 refused (no such product, no pin, a key,
the catalog or the registry missing).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "docker" / "lake")]
import kafka as K  # noqa: E402  the envelope's name (is_envelope)
from hold import Refused, resolve  # noqa: E402  PRODUCT as make hold reads it
from product_verify import connector, lineage, split, summary_of  # noqa: E402  bronze's main lineage
from verify.catalog_drift import CATALOG_URI, Catalog, Unreachable  # noqa: E402  the REST client, GET only

import offsets as O  # noqa: E402
import wire  # noqa: E402  the Confluent framing

PRODUCT = "market-capture.kraken-trades"  # the one product whose silver this recomputes
VENUE = "kraken"  # silver_trade.sql's venue literal
RUNS = "governance_build.run_records.run_record"  # k2runs.TABLE
EXTENSIONS = "/opt/k2/duckdb"  # docker/tools/Dockerfile installs httpfs, avro and iceberg here
# DuckDB inside the tools service's limits (docker-compose.yml, 1 CPU / 2G): half the memory, the rest
# left to Python, the CSV writer and the extensions' code; spilled to /tmp beyond it.
MEMORY, THREADS = "1GB", 2
EXAMPLES = 5  # keys printed per class
KEY = ("kafka_partition", "kafka_offset", "trade_ordinal")
COLUMNS = ("trade_ts", "trade_id", "venue_symbol", "instrument_id", "side", "price", "quantity", "order_type",
           "frame_type", "is_duplicate", "received_at", "conn_id", "conn_msg_seq")  # silver_trade's, after the key


# --- the Avro body, by its writer schema -----------------------------------------------------------


class Undecodable(ValueError):
    """A frame whose bytes this reader cannot take as a record of its writer schema."""


def _varint(b: bytes, i: int) -> tuple[int, int]:
    """An Avro int or long at b[i]: zig-zag, seven bits a byte, low first."""
    n = shift = 0
    while True:
        if i >= len(b):
            raise Undecodable("the body ends inside a number")
        x = b[i]
        i += 1
        n |= (x & 0x7F) << shift
        if x < 0x80:
            return (n >> 1) ^ -(n & 1), i
        shift += 7
        if shift > 63:
            raise Undecodable("a number longer than ten bytes")


def _read(t: Any, b: bytes, i: int) -> tuple[Any, int]:
    """One value of Avro type `t` at b[i], and the index after it. ponytail: the types a record of
    primitives and unions needs, RawMessage's; any other (enum, fixed, array, map, a named reference)
    is refused, so a writer schema that grows one fails loudly as undecoded, never misread."""
    if isinstance(t, list):
        k, i = _varint(b, i)
        if not 0 <= k < len(t):
            raise Undecodable(f"union branch {k} of {len(t)}")
        return _read(t[k], b, i)
    if isinstance(t, dict):
        if t.get("type") == "record":
            out = {}
            for f in t["fields"]:
                out[f["name"]], i = _read(f["type"], b, i)
            return out, i
        return _read(t.get("type"), b, i)  # a primitive with attributes (a logicalType): its type's bytes
    if t == "null":
        return None, i
    if t == "boolean":
        if i >= len(b) or b[i] > 1:
            raise Undecodable("a boolean that is not one byte 0 or 1")
        return b[i] == 1, i + 1
    if t in ("int", "long"):
        return _varint(b, i)
    if t in ("float", "double"):
        n = 4 if t == "float" else 8
        if i + n > len(b):
            raise Undecodable(f"the body ends inside a {t}")
        return struct.unpack_from("<f" if t == "float" else "<d", b, i)[0], i + n
    if t in ("bytes", "string"):
        n, i = _varint(b, i)
        if n < 0 or i + n > len(b):
            raise Undecodable(f"a {t} of {n} bytes where {len(b) - i} remain")
        v = bytes(b[i:i + n])
        try:
            return (v.decode("utf-8") if t == "string" else v), i + n
        except UnicodeDecodeError:
            raise Undecodable("a string that is not UTF-8") from None
    raise Undecodable(f"Avro type {t!r} is not read here")


def avro_record(schema: dict, body: bytes) -> dict:
    """The record `body` holds under its writer `schema`, every byte of it read."""
    value, i = _read(schema, body, 0)
    if i != len(body):
        raise Undecodable(f"{len(body) - i} bytes left after the record")
    return value


def registry_schema(registry: str, schema_id: int) -> dict | None:
    """The RawMessage writer schema the registry serves for `schema_id`, parsed; None when the id is
    unknown (404) or names another record. Any other answer is the run's failure."""
    try:
        with urllib.request.urlopen(f"{registry.rstrip('/')}/schemas/ids/{int(schema_id)}", timeout=15) as r:  # noqa: S310
            text = json.load(r)["schema"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Unreachable(f"the registry answered HTTP {e.code} for schema id {schema_id}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Unreachable(f"the registry at {registry}: {getattr(e, 'reason', e)}") from None
    return json.loads(text) if K.is_envelope(text) else None


def envelope(value: bytes, schemas: dict, fetch) -> dict:
    """The decoded RawMessage of a landing record's value: its framing, its writer schema by id
    (`schemas` caches `fetch(id)`), its body. Undecodable otherwise."""
    try:
        sid, body = wire.parse_confluent(value)
    except wire.BadFrame as e:
        raise Undecodable(str(e)) from None
    if sid not in schemas:
        schemas[sid] = fetch(sid)
    if schemas[sid] is None:
        raise Undecodable(f"schema id {sid} is no RawMessage the registry serves")
    rec = avro_record(schemas[sid], body)
    # each field silver reads, by name: absent or of another type fails bronze's parses audit; a null
    # of a nullable writer type is staged as null, so it is read as null here too
    for f, kind in (("stream", str), ("recv_ts_ns", int), ("conn_id", str), ("conn_msg_seq", int), ("payload", bytes)):
        if f not in rec or not (rec[f] is None or isinstance(rec[f], kind)):
            raise Undecodable(f"the record has no {f} of type {kind.__name__}")
    return rec


# --- the payload's trades, typed -------------------------------------------------------------------

# A decimal(38,18) holds 20 digits before the point. ponytail: each field is typed by the JSON type
# Kraken's v2 trade frame gives it (strings, numbers, an integer trade id); a field of any other JSON
# type reads as null here, where Trino may cast it (a numeric string, a boolean). Kraken sends none,
# and one would show as its own class, never as an equal row.
Q18, LIMIT, WIDE = Decimal(1).scaleb(-18), Decimal(10) ** 20, Context(prec=80)


def text(v: Any) -> str | None:
    """A field the venue sends as a JSON string."""
    return v if isinstance(v, str) else None


def number(v: Any) -> Decimal | None:
    """A JSON number as decimal(38,18), from its digits, never a double: more than 18 decimals round
    half up, more than 20 integer digits is null (Trino's cast of a JSON number to that decimal)."""
    if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
        return None
    try:
        d = WIDE.create_decimal(v).quantize(Q18, rounding=ROUND_HALF_UP, context=WIDE)
    except InvalidOperation:
        return None
    return d if abs(d) < LIMIT else None


def bigint(v: Any) -> int | None:
    """A JSON integer as bigint."""
    return v if isinstance(v, int) and not isinstance(v, bool) and -2**63 <= v < 2**63 else None


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def trades(payload: bytes | None) -> list[tuple] | None:
    """`[(ordinal, timestamp text, trade_id, symbol, side, price, qty, ord_type)]` of a trade frame,
    and the frame's type as each row's last field; None for a frame that is no trade-channel JSON
    object. A frame whose data is not an array has no trade, as an element that is no object has
    every field null. Invalid UTF-8 reads as U+FFFD, as Trino's from_utf8 reads it."""
    if payload is None:
        return None
    try:
        frame = json.loads(payload.decode("utf-8", errors="replace"), parse_float=Decimal, parse_constant=_no_constant)
    except ValueError:
        return None
    if not isinstance(frame, dict) or text(frame.get("channel")) != "trade":
        return None
    kind, data = text(frame.get("type")), frame.get("data")
    out = []
    for n, t in enumerate(data if isinstance(data, list) else [], start=1):
        t = t if isinstance(t, dict) else {}
        out.append((n, text(t.get("timestamp")), bigint(t.get("trade_id")), text(t.get("symbol")), text(t.get("side")),
                    number(t.get("price")), number(t.get("qty")), text(t.get("ord_type")), kind))
    return out


# --- the landing to a CSV DuckDB reads -------------------------------------------------------------

TRADE_COLUMNS = {
    "kafka_partition": "INTEGER", "kafka_offset": "BIGINT", "trade_ordinal": "INTEGER", "ts": "VARCHAR",
    "trade_id": "BIGINT", "venue_symbol": "VARCHAR", "side": "VARCHAR", "price": "DECIMAL(38,18)",
    "quantity": "DECIMAL(38,18)", "order_type": "VARCHAR", "frame_type": "VARCHAR", "recv_ts_ns": "BIGINT",
    "conn_id": "VARCHAR", "conn_msg_seq": "BIGINT",
}


def write_trades(rows: Iterable[tuple[int, int, bytes]], keep: dict, schemas: dict, fetch, out) -> dict:
    """Each landing row `(partition, offset, value)` decoded, filtered by `keep` (the connector's
    `{"field", "in"}`), its trades written to `out` as CSV rows of TRADE_COLUMNS: strings quoted,
    null an empty unquoted field (DuckDB reads it so with allow_quoted_nulls off). The counts, and
    the first undecoded keys with why."""
    w = csv.writer(out, quoting=csv.QUOTE_STRINGS, lineterminator="\n")
    n = {"read": 0, "kept": 0, "skipped": 0, "frames": 0, "trades": 0, "undecoded": 0, "undecoded_first": []}
    for p, o, value in rows:
        n["read"] += 1
        try:
            rec = envelope(bytes(value), schemas, fetch)
        except Undecodable as e:
            n["kept"] += 1  # the connector keeps a frame nobody can classify, for its audit to fail
            n["undecoded"] += 1
            if len(n["undecoded_first"]) < EXAMPLES:
                n["undecoded_first"].append(f"({p}, {o}): {e}")
            continue
        if keep and rec.get(keep["field"]) not in keep["in"]:
            n["skipped"] += 1
            continue
        n["kept"] += 1
        found = trades(rec["payload"])
        if found:
            n["frames"] += 1
        for t in found or []:
            n["trades"] += 1
            w.writerow((p, o, t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], rec["recv_ts_ns"],
                        rec["conn_id"], rec["conn_msg_seq"]))
    return n


# --- the recompute and the comparison, in DuckDB ---------------------------------------------------


def q(s: str) -> str:
    """A SQL string literal."""
    return "'" + str(s).replace("'", "''") + "'"


def ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def at(relation: str, snapshot: int) -> str:
    """An attached Iceberg table at one snapshot, as a FROM item."""
    return f"{relation} AT (VERSION => {int(snapshot)})"


def load_trades(c, path: str) -> None:
    """r_trades: the CSV write_trades wrote."""
    cols = "{" + ", ".join(f"{q(n)}: {q(t)}" for n, t in TRADE_COLUMNS.items()) + "}"
    c.execute(f"""CREATE OR REPLACE TEMP TABLE r_trades AS SELECT * FROM read_csv({q(path)}, header = false,
                  auto_detect = false, delim = ',', quote = '"', escape = '"', new_line = '\\n',
                  allow_quoted_nulls = false, columns = {cols})""")


def recompute(c, master: str) -> None:
    """recomputed: silver_trade from r_trades and the master's venue_symbol (`master`, a FROM item).
    received_at is the receive time cut to the microsecond (the nanoseconds below dropped, never
    rounded); trade_ts is DuckDB's own reading of the ISO-8601 text."""
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE recomputed AS
        WITH typed AS (
          SELECT kafka_partition, kafka_offset, trade_ordinal, TRY_CAST(ts AS TIMESTAMPTZ) AS trade_ts, trade_id,
                 venue_symbol, side, price, quantity, order_type, frame_type,
                 timezone('UTC', make_timestamp((recv_ts_ns - recv_ts_ns % 1000) // 1000)) AS received_at,
                 conn_id, conn_msg_seq
          FROM r_trades
        ),
        versions AS (
          SELECT venue_symbol, valid_from, valid_to, instrument_id FROM {master} WHERE venue = {q(VENUE)}
        ),
        resolved AS (
          -- the version that opened last at or before the trade, if it is still open at the trade
          SELECT t.*, CASE WHEN t.trade_ts < v.valid_to THEN v.instrument_id END AS instrument_id
          FROM typed AS t ASOF LEFT JOIN versions AS v ON t.venue_symbol = v.venue_symbol AND t.trade_ts >= v.valid_from
        ),
        frames AS (
          SELECT kafka_partition, kafka_offset
          FROM resolved
          GROUP BY ALL
          HAVING bool_and(instrument_id IS NOT NULL)
             AND bool_and(trade_ts IS NOT NULL AND trade_id IS NOT NULL AND venue_symbol IS NOT NULL
                          AND side IS NOT NULL AND price IS NOT NULL AND quantity IS NOT NULL AND frame_type IS NOT NULL)
        )
        SELECT r.kafka_partition, r.kafka_offset, r.trade_ordinal, r.trade_ts, r.trade_id, r.venue_symbol,
               r.instrument_id, r.side, r.price, r.quantity, r.order_type, r.frame_type,
               row_number() OVER (PARTITION BY r.venue_symbol, r.trade_id
                                  ORDER BY r.kafka_partition, r.kafka_offset, r.trade_ordinal) > 1 AS is_duplicate,
               r.received_at, r.conn_id, r.conn_msg_seq
        FROM resolved AS r SEMI JOIN frames AS f USING (kafka_partition, kafka_offset)""")


def accounting(c) -> dict:
    """Frames of r_trades by where silver_trade's rules put them: in silver, held (every trade types,
    a symbol does not resolve), rejected (a trade does not type)."""
    (silver, total) = c.execute("SELECT count(DISTINCT (kafka_partition, kafka_offset)), "
                                "(SELECT count(DISTINCT (kafka_partition, kafka_offset)) FROM r_trades) FROM recomputed").fetchone()
    (rejected,) = c.execute("""SELECT count(*) FROM (SELECT kafka_partition, kafka_offset FROM r_trades GROUP BY ALL
        HAVING NOT bool_and(TRY_CAST(ts AS TIMESTAMPTZ) IS NOT NULL AND trade_id IS NOT NULL AND venue_symbol IS NOT NULL
               AND side IS NOT NULL AND price IS NOT NULL AND quantity IS NOT NULL AND frame_type IS NOT NULL))""").fetchone()
    return {"silver": silver, "rejected": rejected, "held": total - silver - rejected}


def compare(c, left: str, right: str, key: tuple, cols: tuple, names: tuple[str, str]) -> list[tuple[str, int, list]]:
    """`(class, keys, first keys with both values)` of `left` (the recompute) against `right`: a
    duplicate key on either side, keys only left (missing), only right (extra), and per column the
    keys both hold whose values differ (IS DISTINCT FROM: a null against a value differs)."""
    k = ", ".join(key)
    out = []
    for side, rel in zip(names, (left, right)):
        rows = c.execute(f"SELECT ({k})::VARCHAR FROM {rel} GROUP BY {k} HAVING count(*) > 1 ORDER BY {k}").fetchall()
        out.append((f"duplicate_key_{side}", len(rows), [r[0] for r in rows[:EXAMPLES]]))
    on = " AND ".join(f"l.{x} = r.{x}" for x in key)
    c.execute(f"""CREATE OR REPLACE TEMP TABLE joined AS
                  SELECT {', '.join(f'coalesce(l.{x}, r.{x}) AS {x}' for x in key)}, l.{key[0]} IS NOT NULL AS in_l,
                         r.{key[0]} IS NOT NULL AS in_r,
                         {', '.join(f'l.{x} AS "l.{x}", r.{x} AS "r.{x}"' for x in cols)}
                  FROM {left} AS l FULL OUTER JOIN {right} AS r ON {on}""")

    def keys(where: str, shown: str = "") -> tuple[int, list]:
        (n,) = c.execute(f"SELECT count(DISTINCT ({k})) FROM joined WHERE {where}").fetchone()
        rows = c.execute(f"SELECT ({k})::VARCHAR{shown} FROM joined WHERE {where} ORDER BY {k} LIMIT {EXAMPLES}").fetchall()
        return n, [" ".join(str(x) for x in r) for r in rows]

    out.append(("missing", *keys("NOT in_r")))
    out.append(("extra", *keys("NOT in_l")))
    for col in cols:
        shown = f", '{names[0]}', \"l.{col}\"::VARCHAR, '{names[1]}', \"r.{col}\"::VARCHAR"
        out.append((col, *keys(f'in_l AND in_r AND "l.{col}" IS DISTINCT FROM "r.{col}"', shown)))
    return out


def report(classes: list[tuple[str, int, list]]) -> int:
    """One line per class; the number of classes holding a difference."""
    failed = 0
    for name, n, first in classes:
        failed += bool(n)
        print(f"{'FAIL' if n else 'PASS'}  {name:<24} " + (f"{n} key(s); first: {'; '.join(first)}" if n else "none"))
    return failed


# --- the pin: run records and bronze's lineage -----------------------------------------------------


def build_pin(c, runs: str, table: str, run: str | None) -> dict:
    """The audited run record of `table` (the newest, or `run`'s) and that run's `started` row of it
    before the audit: `{run_id, snapshot, recorded_us, started_us, inputs}`. Refused when either is
    missing: then that build cannot be compared."""
    where, args = ("AND run_id = ?", [table, run]) if run else ("", [table])
    found = c.execute(f"""SELECT run_id, snapshot_after, epoch_us(recorded_at) FROM {runs}
                          WHERE table_name = ? AND event = 'audited' AND snapshot_after IS NOT NULL {where}
                          ORDER BY recorded_at DESC LIMIT 1""", args).fetchone()
    if found is None:
        raise Refused(f"no audited run record of {table}" + (f" for run {run}" if run else "") + ": nothing built to compare")
    run_id, snapshot, recorded = found
    started = c.execute(f"""SELECT epoch_us(started_at), inputs FROM {runs}
                            WHERE run_id = ? AND table_name = ? AND event = 'started' AND epoch_us(recorded_at) <= ?
                            ORDER BY recorded_at DESC LIMIT 1""", [run_id, table, recorded]).fetchone()
    if started is None or started[0] is None:
        raise Refused(f"run {run_id} has an audited row of {table} and no started row before it: its inputs are unknown")
    return {"run_id": run_id, "snapshot": int(snapshot), "recorded_us": recorded, "started_us": started[0],
            "inputs": json.loads(started[1] or "[]")}


def input_snapshot(pin: dict, contract: str) -> int:
    """The snapshot of input `contract` the pinned build read; refused when it read none."""
    for i in pin["inputs"]:
        if i.get("contractId") == contract and isinstance(i.get("snapshot"), int):
            return i["snapshot"]
    raise Refused(f"run {pin['run_id']} read no snapshot of {contract} (its started row's inputs: {pin['inputs']})")


def bronze_pin(meta: dict, started_us: int, topic: str) -> dict:
    """`{snapshot, landing, through}` of bronze as a build started at `started_us` read it: the newest
    connector commit on main's lineage committed before then (a compaction after it changes no row),
    the landing snapshot it staged from and its through-offsets `{partition: offset}`."""
    before = [s for s in lineage(meta) if connector(s) and int(s["timestamp-ms"]) * 1000 <= started_us]
    if not before:
        raise Refused("bronze's main lineage holds no connector commit before the build started (expired, or none yet)")
    s = summary_of(before[-1])
    if O.SRC_SNAPSHOT_ID not in s:
        raise Refused(f"bronze commit {before[-1]['snapshot-id']} names no landing snapshot ({O.SRC_SNAPSHOT_ID})")
    return {"snapshot": before[-1]["snapshot-id"], "landing": int(s[O.SRC_SNAPSHOT_ID]),
            "through": O.decode(s[O.KAFKA_OFFSETS]).get(topic, {})}


def landing_rows(c, landing: str, through: dict):
    """`(partition, offset, value)` of the landing (a FROM item) below each through-offset, streamed."""
    if not through:
        return
    c.execute("CREATE OR REPLACE TEMP TABLE through_offsets (p INTEGER, e BIGINT)")
    c.executemany("INSERT INTO through_offsets VALUES (?, ?)", sorted(through.items()))
    # a subquery, not `<item> AS l`: DuckDB takes an alias before an AT clause, never after it
    cur = c.execute(f"""SELECT l.kafka_partition, l.kafka_offset, l.record_value
                        FROM (SELECT kafka_partition, kafka_offset, record_value FROM {landing}) AS l
                        JOIN through_offsets AS t ON l.kafka_partition = t.p AND l.kafka_offset < t.e""")
    while batch := cur.fetchmany(10_000):
        yield from batch


# --- the release and the lake ----------------------------------------------------------------------


def release_facts(release: Path) -> tuple[list, dict, dict]:
    """(dags.json's entries, {product: pipeline}, {Trino catalog: warehouse}) of a compiled release."""
    try:
        dags = json.loads((release / "compiled/airflow/dags.json").read_text())["dags"]
        pipes = {d["product"]: json.loads((release / d["pipeline"]).read_text()) for d in dags}
        files = (release / "compiled/trino/etc/catalog").glob("*.properties")
        warehouses = {f.stem: m[1] for f in files if (m := re.search(r"(?m)^iceberg\.rest-catalog\.warehouse=(\S+)$", f.read_text()))}
    except (OSError, ValueError, KeyError) as e:
        raise Refused(f"cannot read the release at {release}: {type(e).__name__}: {e}") from None
    return dags, pipes, warehouses


def duck_relation(relation: str, warehouses: dict) -> str:
    """A Trino relation as DuckDB names it: its warehouse, attached under that name, then namespace and table."""
    catalog, ns, table = split(relation)
    if catalog not in warehouses:
        raise Refused(f"{relation}: the release has no catalog file for {catalog}")
    return ".".join(ident(x) for x in (warehouses[catalog], ns, table))


def lake(warehouses: Iterable[str], environ: dict, tmp: str):
    """DuckDB on the lake: the image's extensions alone, the tools key, each warehouse of `warehouses`
    attached under its own name through Lakekeeper (no authentication, no credential vending, ADR-059
    part 7)."""
    import duckdb

    names = ("K2_TOOLS_S3_ACCESS_KEY", "K2_TOOLS_S3_SECRET_KEY")
    if missing := [n for n in names if not environ.get(n)]:
        raise Refused(f"{', '.join(missing)} not set: the tools key goes in by name (mk/parity.mk)")
    endpoint = environ.get("K2_S3_ENDPOINT", "http://minio:9000")
    c = duckdb.connect(config={"extension_directory": EXTENSIONS, "autoinstall_known_extensions": False,
                               "autoload_known_extensions": True, "allow_community_extensions": False,
                               "memory_limit": MEMORY, "threads": THREADS, "temp_directory": tmp})
    c.execute("SET TimeZone = 'UTC'")
    c.execute("LOAD httpfs")
    c.execute("LOAD iceberg")
    c.execute(f"""CREATE SECRET lake (TYPE s3, KEY_ID {q(environ[names[0]])}, SECRET {q(environ[names[1]])},
                  ENDPOINT {q(re.sub(r'^https?://', '', endpoint))}, USE_SSL {str(endpoint.startswith('https')).lower()},
                  URL_STYLE 'path', REGION {q(environ.get('K2_S3_REGION', 'local-01'))})""")
    uri = environ.get("K2_CATALOG_URI", CATALOG_URI)
    for w in sorted(set(warehouses)):
        c.execute(f"ATTACH {q(w)} AS {ident(w)} (TYPE iceberg, ENDPOINT {q(uri)}, AUTHORIZATION_TYPE 'none', "
                  "ACCESS_DELEGATION_MODE 'none')")
    return c


def subject(release: Path, arg: str) -> dict:
    """What parity-silver reads of `arg`'s product in the release."""
    dags, pipes, warehouses = release_facts(release)
    product, _ = resolve(arg, dags)
    if product != PRODUCT:
        raise Refused(f"{product}: parity-silver recomputes {PRODUCT}'s silver_trade only")
    pipe = pipes[product]
    [bronze] = pipe["bronze"]
    [silver] = [o for o in pipe["build"]["outputs"] if o.endswith(".silver_trade")]
    [master] = [i for i in pipe["inputs"] if i["contractId"] == "reference-data.security-master.venue_symbol"]
    for relation in (RUNS, pipe["landing"]["table"], bronze["table"], silver, master["relation"]):
        duck_relation(relation, warehouses)  # each in a warehouse the release names, or refused here
    return {"product": product, "topic": pipe["connector"]["topic"], "keep": pipe["connector"].get("filter"),
            "registry": pipe["connector"]["registry"], "landing": pipe["landing"]["table"], "bronze": bronze["table"],
            "silver": silver, "master": master, "warehouses": warehouses}


def run(s: dict, c, bronze_meta, fetch, tmp: str, run_id: str | None) -> int:
    """Pin, recompute, compare, report: the exit code. `bronze_meta(relation)` is its REST metadata."""
    w = s["warehouses"]
    pin = build_pin(c, duck_relation(RUNS, w), s["silver"], run_id)
    master = input_snapshot(pin, s["master"]["contractId"])
    meta = bronze_meta(s["bronze"])
    if meta is None:
        raise Refused(f"{s['bronze']} is not in the catalog")
    b = bronze_pin(meta, pin["started_us"], s["topic"])
    print(f"parity-silver: pin run {pin['run_id']} (its audited run record): {s['silver']}@{pin['snapshot']}, "
          f"{s['master']['relation']}@{master}, {s['bronze']}@{b['snapshot']} ({s['landing']}@{b['landing']} below "
          f"{json.dumps(b['through'], sort_keys=True)}); RUN={pin['run_id']} compares these again")
    # ponytail: the whole landing below the through-offsets on every run, every frame since the product
    # began; bound it to one UTC day's trades (the archived pin's unit, with each trade's earlier
    # deliveries for is_duplicate) when a run passes 10 minutes or the tools service's 2G.
    path = os.path.join(tmp, "trades.csv")
    with open(path, "w", newline="", encoding="utf-8") as out:
        n = write_trades(landing_rows(c, at(duck_relation(s["landing"], w), b["landing"]), b["through"]),
                         s["keep"], {}, fetch, out)
    load_trades(c, path)
    recompute(c, at(duck_relation(s["master"]["relation"], w), master))
    c.execute(f"CREATE OR REPLACE TEMP TABLE silver AS SELECT * FROM {at(duck_relation(s['silver'], w), pin['snapshot'])}")
    a = accounting(c)
    print(f"INFO  landing                  {n['read']} row(s) read: {n['skipped']} skipped by the filter, {n['kept']} kept; "
          f"{n['frames']} trade frame(s) with {n['trades']} trade(s): {a['silver']} frame(s) in silver, {a['held']} held "
          f"(a symbol unresolved), {a['rejected']} rejected (a trade untyped)")
    (got, want) = c.execute("SELECT (SELECT count(*) FROM silver), (SELECT count(*) FROM recomputed)").fetchone()
    print(f"INFO  rows                     recomputed {want}, {s['silver']} {got}")
    classes = [("undecoded", n["undecoded"], n["undecoded_first"])]
    classes += compare(c, "recomputed", "silver", KEY, COLUMNS, ("recomputed", "silver"))
    failed = report(classes)
    print(f"parity-silver: {s['product']}: " + (f"{failed} class(es) differ" if failed else f"every class empty over {want} row(s)"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", required=True, type=Path, help="a compiled release directory")
    ap.add_argument("--product", required=True, help="domain/name, domain.name or a bare name one product has")
    ap.add_argument("--run", help="the build's run id, <dag_id>/<run_id>, of an audited run record (default: the newest)")
    a = ap.parse_args(argv)
    try:
        import duckdb
    except ImportError:
        print("parity-silver: REFUSED: this image has no DuckDB (a tools image built before W0-11)", file=sys.stderr)
        return 2

    try:
        s = subject(a.release.resolve(), a.product)
        uri = os.environ.get("K2_CATALOG_URI", CATALOG_URI)

        def bronze_meta(relation: str) -> dict | None:
            catalog, ns, t = split(relation)
            return Catalog(uri, s["warehouses"][catalog]).table(ns, t)

        with tempfile.TemporaryDirectory(prefix="k2-parity-") as tmp:
            used = {s["warehouses"][split(r)[0]] for r in (RUNS, s["landing"], s["silver"], s["master"]["relation"])}
            c = lake(used, dict(os.environ), tmp)
            return run(s, c, bronze_meta, lambda sid: registry_schema(s["registry"], sid), tmp, a.run)
    except Refused as e:
        print(f"parity-silver: REFUSED: {e}", file=sys.stderr)
        return 2
    except (Unreachable, OSError, duckdb.Error) as e:  # the lake unread is no difference found
        print(f"parity-silver: unreachable: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""scripts/parity_silver.py (make parity-silver, W1-18): the Avro reader against fastavro's writer,
the payload's typing, the pin from run records and bronze's lineage, and the whole recompute and
comparison on a lake of DuckDB tables standing for the Iceberg ones, each class made to fail by its
own plant. Synthetic frames only: symbols, prices and times are invented."""
import io
import json
import struct
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import fastavro
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import parity_silver as P  # noqa: E402

import offsets as O  # noqa: E402  (docker/lake, on the path by conftest)

RAW = json.loads((ROOT / "schemas/avro/raw-message.avsc").read_text())
T = "topic.synthetic"
SID = 7  # the registry's id for RAW, in these frames


def body(rec, schema=RAW):
    out = io.BytesIO()
    fastavro.schemaless_writer(out, fastavro.parse_schema(schema), rec)
    return out.getvalue()


def framed(rec, schema=RAW, sid=SID):
    return b"\x00" + struct.pack(">i", sid) + body(rec, schema)


def raw(payload, recv=1767607201000000999, conn="c1", seq=1, stream="trade"):
    if not isinstance(payload, (bytes, type(None))):
        payload = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
    return {"exchange": "kraken", "stream": stream, "symbol": None, "recv_ts_ns": recv, "conn_id": conn,
            "conn_msg_seq": seq, "payload": payload}


def trade(symbol="AAA/ZZZ", trade_id=1, ts="2026-01-05T10:00:00.000001Z", price=10.5, qty=2, side="buy", ord_type="market"):
    t = {"symbol": symbol, "side": side, "price": price, "qty": qty, "ord_type": ord_type, "trade_id": trade_id, "timestamp": ts}
    return {k: v for k, v in t.items() if v is not None}


def frame(*trades_, kind="update"):
    return {"channel": "trade", "type": kind, "data": list(trades_)}


# --- the Avro reader -------------------------------------------------------------------------------


@pytest.mark.parametrize("rec", [
    raw(frame(trade())),
    {**raw(b""), "symbol": "BTC/USD", "conn_id": "ü-☃", "recv_ts_ns": -1, "conn_msg_seq": 2**62},
    raw(b"\x00\xff binary frame"),
])
def test_the_reader_reads_what_fastavro_writes(rec):
    assert P.avro_record(RAW, body(rec)) == rec


def test_a_writer_schema_with_a_field_added_is_read_by_name():
    grown = {**RAW, "fields": [RAW["fields"][0], {"name": "note", "type": ["null", "string"], "default": None},
                               *RAW["fields"][1:], {"name": "flag", "type": "boolean"}, {"name": "w", "type": "double"}]}
    rec = {**raw(frame(trade())), "note": "n", "flag": True, "w": 1.25}
    assert P.avro_record(grown, body(rec, grown)) == rec


@pytest.mark.parametrize("cut", [
    lambda b: b[:-1],                       # the payload's last byte gone
    lambda b: b + b"\x00",                  # a byte after the record
    lambda b: b[:2],                        # inside the second string
])
def test_a_body_that_is_not_one_whole_record_is_refused(cut):
    with pytest.raises(P.Undecodable):
        P.avro_record(RAW, cut(body(raw(frame(trade())))))


def test_a_bad_union_branch_a_bad_string_and_an_unread_type_are_refused():
    b = body(raw(frame(trade())))
    with pytest.raises(P.Undecodable, match="union branch"):
        P.avro_record({"type": "record", "name": "x", "fields": [{"name": "u", "type": ["null", "string"]}]}, b"\x04")
    with pytest.raises(P.Undecodable, match="UTF-8"):
        P.avro_record({"type": "record", "name": "x", "fields": [{"name": "s", "type": "string"}]}, b"\x02\xff")
    with pytest.raises(P.Undecodable, match="not read here"):
        P.avro_record({"type": "record", "name": "x", "fields": [{"name": "a", "type": {"type": "array", "items": "long"}}]}, b)


def test_the_envelope_needs_its_framing_a_served_id_and_silvers_fields():
    asked = []
    fetch = lambda sid: asked.append(sid) or (RAW if sid == SID else None)  # noqa: E731
    schemas = {}
    rec = raw(frame(trade()))
    assert P.envelope(framed(rec), schemas, fetch) == rec
    assert P.envelope(framed(rec), schemas, fetch) == rec and asked == [SID]  # fetched once per id
    with pytest.raises(P.Undecodable, match="magic"):
        P.envelope(b"{" + framed(rec)[1:], schemas, fetch)
    with pytest.raises(P.Undecodable, match="no RawMessage"):
        P.envelope(framed(rec, sid=99), schemas, fetch)
    lacking = {**RAW, "fields": [f for f in RAW["fields"] if f["name"] != "conn_id"]}
    with pytest.raises(P.Undecodable, match="conn_id"):
        P.envelope(framed({k: v for k, v in rec.items() if k != "conn_id"}, lacking, 8), schemas, lambda sid: lacking)
    nullable = {**RAW, "fields": [{**f, "type": ["null", f["type"]]} if f["name"] == "payload" else f for f in RAW["fields"]]}
    assert P.envelope(framed({**rec, "payload": None}, nullable, 9), schemas, lambda sid: nullable)["payload"] is None


# --- the payload's trades ----------------------------------------------------------------------------


def typed(payload):
    return P.trades(payload if isinstance(payload, bytes) else json.dumps(payload).encode())


def test_numbers_keep_every_digit_and_round_half_up_past_eighteen():
    text = ('{"channel":"trade","type":"update","data":[{"price":12345678.123456789012345678,"qty":0.000000000000000001},'
            '{"price":1.0000000000000000005,"qty":1.0000000000000000004},{"price":123456789012345678901,"qty":1e2}]}')
    rows = P.trades(text.encode())
    assert [(r[5], r[6]) for r in rows] == [
        (Decimal("12345678.123456789012345678"), Decimal("0.000000000000000001")),
        (Decimal("1.000000000000000001"), Decimal("1.000000000000000000")),
        (None, Decimal("100.000000000000000000")),  # 21 integer digits: no decimal(38,18) holds it
    ]


def test_each_field_is_read_only_as_the_json_type_the_venue_sends():
    [row] = typed(frame({"symbol": 5, "side": True, "price": "10.5", "qty": False, "ord_type": None,
                         "trade_id": 1.0, "timestamp": 20260105}))
    assert row == (1, None, None, None, None, None, None, None, "update")
    [first, second, third] = typed(frame(trade(trade_id=2**63 - 1), "not an object", trade(trade_id=2**63)))
    assert first == (1, "2026-01-05T10:00:00.000001Z", 2**63 - 1, "AAA/ZZZ", "buy", Decimal("10.5").quantize(P.Q18),
                     Decimal(2).quantize(P.Q18), "market", "update")
    assert second == (2, None, None, None, None, None, None, None, "update")
    assert third[2] is None  # past bigint


@pytest.mark.parametrize("payload, expected", [
    ({"channel": "heartbeat"}, None),
    ({"channel": "trade", "type": "update", "data": {"not": "an array"}}, []),
    ({"channel": "trade", "type": "update"}, []),
    (["channel", "trade"], None),
    (b'{"channel":"trade","data":[{"price":NaN}]}', None),
    (b'{"channel":"trade","data":[]} trailing', None),
    (b'\xff\xfe not json', None),
])
def test_a_frame_that_is_no_trade_frame_has_no_trades(payload, expected):
    assert typed(payload) == expected


def test_invalid_utf8_inside_a_string_reads_as_the_replacement_character():
    [row] = P.trades(b'{"channel":"trade","type":"u\xffx","data":[{"side":"buy"}]}')
    assert row[4] == "buy" and row[8] == "u�x"


# --- the lake: DuckDB tables standing for the Iceberg ones -----------------------------------------

S = {"product": P.PRODUCT, "topic": T, "keep": {"field": "stream", "in": ["trade"]}, "registry": "unused",
     "landing": "lake.kraken_trades.landing", "bronze": "lake.kraken_trades.bronze_trade",
     "silver": "market_capture_build.kraken_trades__base.silver_trade",
     "master": {"contractId": "reference-data.security-master.venue_symbol",
                "relation": "reference_data.security_master__base.venue_symbol"},
     "warehouses": {"lake": "k2", "market_capture_build": "market_capture", "reference_data": "reference_data",
                    "governance_build": "governance"}}
T0 = 1_767_600_000_000_000  # a build's start, microseconds since the epoch (2026-01-05 08:00 UTC)

# The landing: (partition, offset, raw record). Kept frames are trade-stream records; through-offsets
# {0: 16, 1: 3} (bronze's commit before the newest build), partition 2 never staged.
LANDING = [
    (0, 10, raw(frame(trade(trade_id=1), trade(trade_id=2, ts="2026-01-05T10:00:00.000002Z", price=11, qty=3, side="sell", ord_type="limit")))),
    (0, 11, raw({"channel": "heartbeat"}, stream="heartbeat", seq=2)),
    (0, 12, raw(frame(trade(trade_id=1), kind="snapshot"), conn="c2", seq=1, recv=1767607202000000000)),
    (0, 13, raw(frame(trade(trade_id=5), trade(symbol="QQQ/ZZZ", trade_id=1)), seq=3)),   # QQQ resolves to nothing: held
    (0, 14, raw(frame(trade(trade_id=6, side=None)), seq=4)),                              # no side: rejected
    (0, 15, raw(frame(trade(trade_id=7, ts="1999-01-01T00:00:00Z")), seq=5)),            # before the first fetch
    (0, 16, raw(frame(trade(trade_id=8)), seq=6)),                                         # past the through-offset
    (1, 0, raw(frame(trade(symbol="BBB/ZZZ", trade_id=1, ts="2026-01-05T13:00:00Z")), conn="c3", seq=1)),
    (1, 1, raw(frame(trade(symbol="BBB/ZZZ", trade_id=2, ts="2026-01-05T11:00:00Z")), conn="c3", seq=2)),
    (1, 2, raw({"channel": "trade", "type": "update", "data": {}}, conn="c3", seq=3)),    # kept, no trade in it
    (2, 0, raw(frame(trade(symbol="BBB/ZZZ", trade_id=9)), conn="c4", seq=1)),             # bronze never staged it
]
MASTER = [  # venue, venue_symbol, valid_from, valid_to, instrument_id
    ("kraken", "AAA/ZZZ", "1970-01-01 00:00:00+00", "9999-12-31 23:59:59+00", "i-aaa"),
    ("kraken", "BBB/ZZZ", "1970-01-01 00:00:00+00", "2026-01-05 12:00:00+00", "i-bbb-1"),
    ("kraken", "BBB/ZZZ", "2026-01-05 12:00:00+00", "9999-12-31 23:59:59+00", "i-bbb-2"),
    ("other", "QQQ/ZZZ", "1970-01-01 00:00:00+00", "9999-12-31 23:59:59+00", "x-other"),  # another venue's: never read
]


def utc_us(ns):
    us = ns // 1000
    return datetime.fromtimestamp(us // 10**6, timezone.utc).replace(microsecond=us % 10**6).isoformat()


def srow(p, o, n, ts, tid, sym, inst, side, price, qty, ord_type, kind, dup, recv=1767607201000000999, conn="c1", seq=1):
    return (p, o, n, ts, tid, sym, inst, side, Decimal(str(price)), Decimal(str(qty)), ord_type, kind, dup, utc_us(recv), conn, seq)


# silver_trade as its build should have published it, written by hand from the rules
SILVER = [
    srow(0, 10, 1, "2026-01-05 10:00:00.000001+00", 1, "AAA/ZZZ", "i-aaa", "buy", 10.5, 2, "market", "update", False),
    srow(0, 10, 2, "2026-01-05 10:00:00.000002+00", 2, "AAA/ZZZ", "i-aaa", "sell", 11, 3, "limit", "update", False),
    srow(0, 12, 1, "2026-01-05 10:00:00.000001+00", 1, "AAA/ZZZ", "i-aaa", "buy", 10.5, 2, "market", "snapshot", True,
         recv=1767607202000000000, conn="c2"),
    srow(0, 15, 1, "1999-01-01 00:00:00+00", 7, "AAA/ZZZ", "i-aaa", "buy", 10.5, 2, "market", "update", False, seq=5),
    srow(1, 0, 1, "2026-01-05 13:00:00+00", 1, "BBB/ZZZ", "i-bbb-2", "buy", 10.5, 2, "market", "update", False, conn="c3"),
    srow(1, 1, 1, "2026-01-05 11:00:00+00", 2, "BBB/ZZZ", "i-bbb-1", "buy", 10.5, 2, "market", "update", False, conn="c3", seq=2),
]
SILVER_COLS = ("kafka_partition INTEGER, kafka_offset BIGINT, trade_ordinal INTEGER, trade_ts TIMESTAMPTZ, trade_id BIGINT, "
               "venue_symbol VARCHAR, instrument_id VARCHAR, side VARCHAR, price DECIMAL(38,18), quantity DECIMAL(38,18), "
               "order_type VARCHAR, frame_type VARCHAR, is_duplicate BOOLEAN, received_at TIMESTAMPTZ, conn_id VARCHAR, "
               "conn_msg_seq BIGINT")


def table(c, name, cols, rows):
    c.execute(f"CREATE TABLE {name} ({cols})")
    if rows:
        c.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(rows[0]))})", rows)


def run_row(run, table_name, event, snap, started_us, recorded_us, inputs=None):
    return (run, event, table_name, snap, datetime.fromtimestamp(started_us / 1e6, timezone.utc).isoformat(),
            datetime.fromtimestamp(recorded_us / 1e6, timezone.utc).isoformat(), json.dumps(inputs) if inputs else None)


def inputs(master):
    return [{"contractId": S["master"]["contractId"], "relation": S["master"]["relation"], "snapshot": master,
             "state": "active", "lagMinutes": 0.0}]


def commit(sid, ts_us, src, through, parent):
    return {"snapshot-id": sid, "parent-snapshot-id": parent, "timestamp-ms": ts_us // 1000,
            "summary": {"operation": "append", O.JOB: "connector", O.SRC_SNAPSHOT_ID: str(src),
                        O.KAFKA_OFFSETS: O.encode({T: through})}}


SILVER_REL = '"market_capture"."kraken_trades__base"."silver_trade'


def lake(silver=SILVER, landing=LANDING, records=None):
    """The lake as DuckDB tables, `<table>@<snapshot>` for a table read at a snapshot:
    run A built at landing 400 (through {0: 12}), run B at landing 500 (through {0: 16, 1: 3});
    landing 600 holds a frame more, from a bronze commit after run B started."""
    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'")
    for w, ns in (("k2", "kraken_trades"), ("market_capture", "kraken_trades__base"),
                  ("reference_data", "security_master__base"), ("governance", "run_records")):
        c.execute(f"ATTACH ':memory:' AS {w}")
        c.execute(f"CREATE SCHEMA {w}.{ns}")
    rows = [(p, o, framed(r)) for p, o, r in landing]
    extra = [(0, 17, framed(raw(frame(trade(trade_id=9)), seq=7)))]
    for snap, keep in ((400, lambda p, o: (p, o) <= (0, 11)), (500, lambda p, o: True), (600, lambda p, o: True)):
        table(c, f'"k2"."kraken_trades"."landing@{snap}"', "kafka_partition INTEGER, kafka_offset BIGINT, record_value BLOB",
              [r for r in rows if keep(r[0], r[1])] + (extra if snap == 600 else []))
    table(c, '"reference_data"."security_master__base"."venue_symbol@3"',
          "venue VARCHAR, venue_symbol VARCHAR, valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ, instrument_id VARCHAR", MASTER)
    table(c, f'{SILVER_REL}@41"', SILVER_COLS, [r for r in SILVER if (r[0], r[1]) == (0, 10)])
    table(c, f'{SILVER_REL}@42"', SILVER_COLS, list(silver))
    silver_t = "market_capture_build.kraken_trades__base.silver_trade"
    table(c, '"governance"."run_records"."run_record"', "run_id VARCHAR, event VARCHAR, table_name VARCHAR, "
          "snapshot_after BIGINT, started_at TIMESTAMPTZ, recorded_at TIMESTAMPTZ, inputs VARCHAR", records if records is not None else [
              run_row("kraken_trades/a", silver_t, "started", None, T0, T0 + 1, inputs(3)),
              run_row("kraken_trades/a", silver_t, "audited", 41, T0, T0 + 9),
              run_row("kraken_trades/b", silver_t, "started", None, T0 + 100_000_000, T0 + 100_000_001, inputs(3)),
              run_row("kraken_trades/b", silver_t, "audited", 42, T0 + 100_000_000, T0 + 100_000_009),
          ])
    return c


def bronze_meta(relation):
    assert relation == S["bronze"]
    snaps = [commit(301, T0 - 50_000_000, 400, {0: 12}, None),
             commit(302, T0 + 50_000_000, 500, {0: 16, 1: 3}, 301),
             {"snapshot-id": 303, "parent-snapshot-id": 302, "timestamp-ms": (T0 + 60_000_000) // 1000,
              "summary": {"operation": "replace", O.JOB: "maintenance"}},
             commit(304, T0 + 200_000_000, 600, {0: 18, 1: 3}, 303)]  # after run b started
    return {"snapshots": snaps, "refs": {"main": {"snapshot-id": 304, "type": "branch"}}}


@pytest.fixture
def at_snapshot(monkeypatch):
    """`<relation> AT (VERSION => n)` read as the DuckDB table `<relation>@n`."""
    monkeypatch.setattr(P, "at", lambda rel, snap: f'{rel[:-1]}@{int(snap)}"')


def parity(c, tmp_path, capsys, run=None):
    code = P.run(S, c, bronze_meta, lambda sid: RAW if sid == SID else None, str(tmp_path), run)
    return code, capsys.readouterr().out


def failing(out):
    return sorted(line.split()[1] for line in out.splitlines() if line.startswith("FAIL"))


def test_the_silver_built_by_the_rules_passes_every_class(at_snapshot, tmp_path, capsys):
    code, out = parity(lake(), tmp_path, capsys)
    assert code == 0, out
    assert failing(out) == []
    assert "pin run kraken_trades/b" in out and "silver_trade@42" in out and "landing@500 below" in out
    assert "venue_symbol@3" in out and "bronze_trade@302" in out
    # 9 rows read (p0 10..15, p1 0..2): the heartbeat skipped; 7 frames with trades: 5 in silver, 1 held, 1 rejected
    assert "9 row(s) read: 1 skipped by the filter, 8 kept; 7 trade frame(s) with 9 trade(s): 5 frame(s) in silver, 1 held" in out
    assert "1 rejected" in out and "every class empty over 6 row(s)" in out


def test_run_compares_the_snapshots_of_the_build_it_names(at_snapshot, tmp_path, capsys):
    code, out = parity(lake(), tmp_path, capsys, run="kraken_trades/a")
    assert code == 0, out
    assert "silver_trade@41" in out and "landing@400 below" in out and "bronze_trade@301" in out
    assert "every class empty over 2 row(s)" in out


def mutated(sql):
    c = lake()
    c.execute(sql.format(silver=f'{SILVER_REL}@42"'))
    return c


@pytest.mark.parametrize("sql, classes", [
    ("UPDATE {silver} SET price = price + 1 WHERE kafka_offset = 10 AND trade_ordinal = 2", ["price"]),
    ("UPDATE {silver} SET is_duplicate = NOT is_duplicate WHERE kafka_offset = 12", ["is_duplicate"]),
    ("UPDATE {silver} SET received_at = received_at + INTERVAL 1 MICROSECOND WHERE kafka_offset = 15", ["received_at"]),
    ("UPDATE {silver} SET instrument_id = 'i-bbb-1' WHERE kafka_partition = 1 AND kafka_offset = 0", ["instrument_id"]),
    ("UPDATE {silver} SET conn_msg_seq = conn_msg_seq + 1 WHERE kafka_offset = 10", ["conn_msg_seq"]),
    ("UPDATE {silver} SET trade_ts = NULL WHERE kafka_offset = 15", ["trade_ts"]),
    ("DELETE FROM {silver} WHERE kafka_partition = 1 AND kafka_offset = 1", ["missing"]),
    ("INSERT INTO {silver} SELECT 0, 13, 1, trade_ts, 5, venue_symbol, instrument_id, side, price, quantity, order_type, "
     "frame_type, false, received_at, conn_id, 3 FROM {silver} WHERE kafka_offset = 10 AND trade_ordinal = 1", ["extra"]),
    ("INSERT INTO {silver} SELECT * FROM {silver} WHERE kafka_offset = 15", ["duplicate_key_silver"]),
])
def test_each_difference_fails_its_own_class(at_snapshot, tmp_path, capsys, sql, classes):
    code, out = parity(mutated(sql), tmp_path, capsys)
    assert code == 1 and failing(out) == classes, out


def test_a_kept_frame_whose_envelope_does_not_decode_fails_undecoded(at_snapshot, tmp_path, capsys):
    c = lake()
    c.execute("""UPDATE "k2"."kraken_trades"."landing@500" SET record_value = '\\x00\\x00\\x00\\x00\\x07\\xff'::BLOB
                 WHERE kafka_offset = 11 AND kafka_partition = 0""")
    code, out = parity(c, tmp_path, capsys)
    assert code == 1 and failing(out) == ["undecoded"], out
    assert "(0, 11)" in out


def test_a_symbol_the_master_does_not_resolve_is_held_and_so_silver_holding_it_is_extra(at_snapshot, tmp_path, capsys):
    c = lake()
    c.execute("""DELETE FROM "reference_data"."security_master__base"."venue_symbol@3" WHERE venue_symbol = 'BBB/ZZZ'
                 AND instrument_id = 'i-bbb-1'""")
    code, out = parity(c, tmp_path, capsys)
    assert code == 1 and failing(out) == ["extra"], out  # the 11:00 trade's frame is held now
    assert "2 held" in out


def test_a_version_closed_before_the_trade_does_not_resolve_it(at_snapshot, tmp_path, capsys):
    c = lake()
    c.execute("""UPDATE "reference_data"."security_master__base"."venue_symbol@3" SET valid_to = '2026-01-05 12:30:00+00'
                 WHERE instrument_id = 'i-bbb-2'""")  # the 13:00 trade falls after the last version closed
    code, out = parity(c, tmp_path, capsys)
    assert code == 1 and failing(out) == ["extra"], out
    assert "(1, 0, 1)" in out and "2 held" in out


# --- the pin -----------------------------------------------------------------------------------------


def test_bronze_is_the_newest_connector_commit_before_the_build_started():
    meta = bronze_meta(S["bronze"])
    assert P.bronze_pin(meta, T0, T) == {"snapshot": 301, "landing": 400, "through": {0: 12}}
    assert P.bronze_pin(meta, T0 + 100_000_000, T) == {"snapshot": 302, "landing": 500, "through": {0: 16, 1: 3}}
    assert P.bronze_pin(meta, T0 + 300_000_000, T)["snapshot"] == 304
    with pytest.raises(P.Refused, match="no connector commit before"):
        P.bronze_pin(meta, T0 - 60_000_000, T)
    off_main = {**meta, "refs": {"main": {"snapshot-id": 302}}}  # 303 and 304 on no branch main reaches
    assert P.bronze_pin(off_main, T0 + 300_000_000, T)["snapshot"] == 302


def test_the_pin_is_the_audited_run_record_and_its_started_row():
    c = lake()
    runs = '"governance"."run_records"."run_record"'
    silver_t = S["silver"]
    pin = P.build_pin(c, runs, silver_t, None)
    assert (pin["run_id"], pin["snapshot"], pin["started_us"]) == ("kraken_trades/b", 42, T0 + 100_000_000)
    assert P.input_snapshot(pin, S["master"]["contractId"]) == 3
    assert P.build_pin(c, runs, silver_t, "kraken_trades/a")["snapshot"] == 41
    with pytest.raises(P.Refused, match="no audited run record"):
        P.build_pin(c, runs, silver_t, "kraken_trades/none")
    with pytest.raises(P.Refused, match="read no snapshot"):
        P.input_snapshot({**pin, "inputs": [{**inputs(3)[0], "snapshot": None}]}, S["master"]["contractId"])


def test_a_build_with_no_started_row_before_its_audit_is_refused():
    late = [run_row("kraken_trades/c", S["silver"], "audited", 43, T0, T0 + 9),
            run_row("kraken_trades/c", S["silver"], "started", None, T0, T0 + 10, inputs(3))]  # written after it
    with pytest.raises(P.Refused, match="no started row before it"):
        P.build_pin(lake(records=late), '"governance"."run_records"."run_record"', S["silver"], None)


def test_the_lake_needs_the_tools_key_by_name(tmp_path):
    with pytest.raises(P.Refused, match="K2_TOOLS_S3_ACCESS_KEY, K2_TOOLS_S3_SECRET_KEY not set"):
        P.lake(["k2"], {"K2_SPARK_S3_ACCESS_KEY": "x"}, str(tmp_path))


def release(tmp_path, product=P.PRODUCT, catalogs=("lake", "market_capture_build", "reference_data", "governance_build")):
    r = tmp_path / "rel"
    (r / "compiled/airflow").mkdir(parents=True)
    (r / "compiled/trino/etc/catalog").mkdir(parents=True)
    pipe = {"product": product, "connector": {"topic": T, "filter": S["keep"], "registry": "http://r:8081"},
            "landing": {"table": S["landing"]}, "bronze": [{"table": S["bronze"]}],
            "build": {"outputs": [S["silver"], S["silver"] + "_unresolved"]}, "inputs": [S["master"]]}
    (r / "pipe.json").write_text(json.dumps(pipe))
    (r / "compiled/airflow/dags.json").write_text(json.dumps({"dags": [{"product": product, "dagId": "d", "pipeline": "pipe.json"}]}))
    for cat in catalogs:
        (r / f"compiled/trino/etc/catalog/{cat}.properties").write_text(f"iceberg.rest-catalog.warehouse={S['warehouses'][cat]}\n")
    return r


def test_the_subject_is_kraken_trades_silver_in_warehouses_the_release_names(tmp_path):
    s = P.subject(release(tmp_path), "kraken-trades")
    assert {k: s[k] for k in ("silver", "landing", "bronze", "topic", "warehouses")} == {k: S[k] for k in ("silver", "landing", "bronze", "topic", "warehouses")}
    with pytest.raises(P.Refused, match="recomputes market-capture.kraken-trades"):
        P.subject(release(tmp_path / "x", product="market-capture.other-trades"), "other-trades")
    with pytest.raises(P.Refused, match="no catalog file for governance_build"):
        P.subject(release(tmp_path / "y", catalogs=("lake", "market_capture_build", "reference_data")), "kraken-trades")

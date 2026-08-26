import asyncio, json, time
import websockets

URL = "wss://ws.kraken.com/v2"
PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD"]

async def main():
    book_lines = []
    trade_frame = None
    saw_checksum = set()
    saw_timestamp = set()
    instrument_snapshot = None

    async with websockets.connect(URL) as ws:
        # 1. instrument channel
        await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "instrument", "snapshot": True}}))
        # 2. book channel
        await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "book", "symbol": ["BTC/USD"], "depth": 25, "snapshot": True}}))
        # 3. trade channel
        await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}}))

        start = time.time()
        while time.time() - start < 12:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            recv_ts_ns = time.time_ns()
            msg = json.loads(raw)

            channel = msg.get("channel")
            if channel == "instrument" and msg.get("type") == "snapshot" and instrument_snapshot is None:
                instrument_snapshot = msg
            elif channel == "book":
                rec = dict(msg)
                rec["recv_ts_ns"] = recv_ts_ns
                book_lines.append(rec)
                if msg.get("type") == "update":
                    data = msg.get("data", [{}])[0]
                    if "checksum" in data:
                        saw_checksum.add(True)
                    if "timestamp" in data:
                        saw_timestamp.add(True)
            elif channel == "trade":
                if trade_frame is None:
                    trade_frame = msg

    # write book jsonl
    with open("kraken-book.jsonl", "w") as f:
        for rec in book_lines:
            f.write(json.dumps(rec) + "\n")

    print("=== INSTRUMENT SNAPSHOT (subset) ===")
    if instrument_snapshot:
        pairs = instrument_snapshot.get("data", {}).get("pairs", [])
        by_symbol = {p.get("symbol"): p for p in pairs}
        for sym in PAIRS:
            p = by_symbol.get(sym)
            if p:
                print(sym, {
                    "price_precision": p.get("price_precision"),
                    "qty_precision": p.get("qty_precision"),
                    "price_increment": p.get("price_increment"),
                    "qty_increment": p.get("qty_increment"),
                    "status": p.get("status"),
                })
            else:
                print(sym, "NOT FOUND in snapshot")
    else:
        print("NO INSTRUMENT SNAPSHOT RECEIVED")

    print("\n=== BOOK FRAMES ===")
    print("total book frames captured:", len(book_lines))
    types_seen = set(r.get("type") for r in book_lines)
    print("types seen:", types_seen)
    print("every update frame has checksum:", bool(saw_checksum) and all(
        "checksum" in r.get("data", [{}])[0] for r in book_lines if r.get("type") == "update"
    ))
    print("every update frame has timestamp:", bool(saw_timestamp) and all(
        "timestamp" in r.get("data", [{}])[0] for r in book_lines if r.get("type") == "update"
    ))
    updates = [r for r in book_lines if r.get("type") == "update"]
    if updates:
        print("sample update frame:", json.dumps(updates[0])[:500])

    print("\n=== TRADE FRAME ===")
    if trade_frame:
        print(json.dumps(trade_frame, indent=2)[:1000])
        data = trade_frame.get("data", [])
        if data:
            tid = data[0].get("trade_id")
            print("trade_id:", tid, "type:", type(tid).__name__, "is_int:", isinstance(tid, int))
    else:
        print("NO TRADE FRAME RECEIVED")

asyncio.run(main())

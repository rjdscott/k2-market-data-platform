import asyncio, json, time
import websockets

URL = "wss://advanced-trade-ws.coinbase.com"

async def main():
    frames = []
    error_frames = []
    first_frame_type = None

    async with websockets.connect(URL, max_size=None) as ws:
        # subscribe in 3 separate messages, no JWT/auth
        await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD", "ETH-USD"], "channel": "level2"}))
        await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "heartbeats"}))
        await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD", "ETH-USD"], "channel": "market_trades"}))

        start = time.time()
        while time.time() - start < 15:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            recv_ts_ns = time.time_ns()
            msg = json.loads(raw)
            if first_frame_type is None:
                first_frame_type = msg.get("channel", msg.get("type"))
            rec = dict(msg)
            rec["recv_ts_ns"] = recv_ts_ns
            frames.append(rec)
            if msg.get("type") == "error" or msg.get("channel") == "error" or "error" in msg:
                error_frames.append(msg)

    with open("coinbase.jsonl", "w") as f:
        for rec in frames:
            f.write(json.dumps(rec) + "\n")

    print("first frame type/channel:", first_frame_type)
    print("total frames:", len(frames))
    channels_seen = {}
    for r in frames:
        c = r.get("channel", "?")
        channels_seen[c] = channels_seen.get(c, 0) + 1
    print("channels seen (count):", channels_seen)

    print("\n=== ERROR FRAMES ===")
    if error_frames:
        for e in error_frames:
            print(json.dumps(e))
    else:
        print("none")

    # sequence_num gap analysis across ALL frames on connection
    print("\n=== sequence_num analysis ===")
    seq_frames = [r for r in frames if "sequence_num" in r]
    print("frames with sequence_num:", len(seq_frames))
    if seq_frames:
        seqs = [r["sequence_num"] for r in seq_frames]
        gaps = []
        for i in range(1, len(seqs)):
            diff = seqs[i] - seqs[i-1]
            if diff != 1:
                gaps.append((seqs[i-1], seqs[i], diff))
        print("first seq:", seqs[0], "last seq:", seqs[-1], "count:", len(seqs))
        print("num gaps (diff != 1):", len(gaps))
        print("sample gaps (up to 10):", gaps[:10])

    # snapshot frame for BTC-USD level2
    print("\n=== level2 snapshot BTC-USD ===")
    snap = None
    for r in frames:
        if r.get("channel") == "l2_data":
            evts = r.get("events", [])
            for e in evts:
                if e.get("type") == "snapshot" and e.get("product_id") == "BTC-USD":
                    snap = r
                    break
        if snap:
            break
    if snap:
        raw_bytes = json.dumps({k: v for k, v in snap.items() if k != "recv_ts_ns"}).encode()
        n_updates = sum(len(e.get("updates", [])) for e in snap.get("events", []) if e.get("product_id") == "BTC-USD")
        print("snapshot frame size (bytes):", len(raw_bytes))
        print("number of levels (updates entries) in BTC-USD snapshot event:", n_updates)
    else:
        print("no snapshot frame found for BTC-USD")

    # per-update fields
    print("\n=== l2_data update fields ===")
    sides = set()
    sample_update = None
    for r in frames:
        if r.get("channel") == "l2_data":
            for e in r.get("events", []):
                if e.get("type") == "update":
                    for u in e.get("updates", []):
                        sides.add(u.get("side"))
                        if sample_update is None:
                            sample_update = u
    print("side values seen:", sides)
    print("sample update:", json.dumps(sample_update))

    print("\n=== heartbeats sample ===")
    for r in frames:
        if r.get("channel") == "heartbeats":
            print(json.dumps({k: v for k, v in r.items() if k != "recv_ts_ns"})[:400])
            break

    print("\n=== market_trades sample ===")
    for r in frames:
        if r.get("channel") == "market_trades":
            print(json.dumps({k: v for k, v in r.items() if k != "recv_ts_ns"})[:600])
            break

asyncio.run(main())

# Step 01.5.7: Demo Integration

**Status**: ⬜ Not Started  
**Estimated Time**: 6 hours  
**Actual Time**: -  
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Showcase live Binance streaming in demo (terminal + Grafana + API).

---

## Tasks

### 1. Terminal Display (scripts/demo.py)

```python
def demo_live_streaming(self):
    console.print("\n[bold yellow]Live Binance Streaming[/bold yellow]\n")
    console.print("[green]Watching live BTC-USDT trades...[/green]\n")

    consumer = Consumer(...)
    for i in range(10):  # Show 10 trades
        msg = consumer.poll(timeout=2.0)
        if msg:
            console.print(f"  {msg['symbol']} @ ${msg['price']} x {msg['quantity']}")
```

### 2. Grafana Panel

Create panel: "Live BTC Price"
- Query: `SELECT timestamp, price FROM trades_v2 WHERE symbol = 'BTCUSDT' ORDER BY timestamp DESC LIMIT 1000`
- Visualization: Time series line chart
- Auto-refresh: 5 seconds

### 3. API Query Demo

```bash
curl "http://localhost:8000/v1/trades?symbol=BTCUSDT&window_minutes=15"
```

### 4. Update Demo Narrative

Add Binance streaming to Section 2: Ingestion.

---

## Validation

```bash
python scripts/demo.py --section ingestion
# Should show live BTC/ETH trades

# Check Grafana
open http://localhost:3000
# "Live BTC Price" panel should update every 5s

# API query
curl "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=10"
```

---

## Commit Message

```
feat(demo): integrate live binance streaming showcase

- Add live streaming display to demo.py (terminal)
- Create Grafana panel: "Live BTC Price" (auto-refresh 5s)
- Add API query demo for BTC trades
- Update demo narrative (Section 2: Ingestion)
- All three demo modes work (terminal + Grafana + API)

Related: Phase 2 Prep, Step 01.5.7
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

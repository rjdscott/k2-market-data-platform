# Step 01.5.3: Streaming Service

**Status**: ⬜ Not Started  
**Estimated Time**: 6 hours  
**Actual Time**: -  
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Create daemon service that streams Binance → Kafka.

---

## Tasks

### 1. Create scripts/binance_stream.py

```python
"""Binance streaming service daemon."""

import asyncio
import argparse
from k2.ingestion.binance_client import BinanceWebSocketClient
from k2.ingestion.producer import MarketDataProducer

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    producer = MarketDataProducer(schema_version="v2")

    def on_message(msg):
        v2_trade = convert_binance_trade_to_v2(msg)
        producer.produce_trade("crypto", "BINANCE", v2_trade)

    client = BinanceWebSocketClient(symbols=args.symbols, on_message=on_message)
    await client.connect()

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Add Graceful Shutdown

```python
import signal

def signal_handler(sig, frame):
    logger.info("Received shutdown signal")
    producer.flush()
    client.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
```

---

## Validation

```bash
# Start streaming
python scripts/binance_stream.py

# Check Kafka
kafka-console-consumer --topic market.crypto.trades.binance

# Stop with Ctrl+C
```

---

## Commit Message

```
feat(binance): add streaming service daemon

- Create scripts/binance_stream.py
- Integrate BinanceWebSocketClient with MarketDataProducer
- Add CLI arguments (--symbols, --log-level)
- Add graceful shutdown (SIGINT/SIGTERM)
- Test streaming: Binance → Kafka

Related: Phase 2 Prep, Step 01.5.3
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

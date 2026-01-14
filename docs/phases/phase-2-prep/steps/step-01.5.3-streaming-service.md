# Step 01.5.3: Streaming Service

**Status**: ✅ Complete
**Estimated Time**: 6 hours
**Actual Time**: ~2 hours
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

## Completion Notes

**Implemented**: 2026-01-13

### What Was Built:
- ✅ `scripts/binance_stream.py` - Production-ready streaming daemon
- ✅ Integration: BinanceWebSocketClient → on_message → MarketDataProducer → Kafka
- ✅ CLI arguments: `--symbols`, `--log-level`, `--daemon`
- ✅ Graceful shutdown (SIGINT/SIGTERM handlers)
- ✅ Comprehensive logging with structured logs (structlog)
- ✅ Statistics tracking (trades received, produced, errors, retries)
- ✅ Rich console output with progress indicators

### Key Features Delivered:
- Async/await pattern for efficient streaming
- Automatic reconnection via BinanceWebSocketClient (exponential backoff)
- V2 schema conversion with vendor_data
- Idempotent Kafka production (at-least-once with deduplication)
- Graceful shutdown sequence:
  1. Disconnect WebSocket (stop receiving new trades)
  2. Wait for client task to complete (5s timeout)
  3. Flush producer queue (send all buffered messages, 10s timeout)
  4. Close producer and print final statistics
- Daemon mode (no console output, logs only)
- Environment variable configuration (K2_KAFKA_*, K2_BINANCE_*)

### Testing:
```bash
# Test with default symbols
python scripts/binance_stream.py

# Test with custom symbols
python scripts/binance_stream.py --symbols BTCUSDT ETHBTC BNBEUR

# Run as daemon
python scripts/binance_stream.py --daemon --log-level info

# Verify Kafka topic
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic market.crypto.trades \
  --from-beginning
```

### Integration Points:
- Uses `BinanceWebSocketClient` from step 01.5.1
- Uses `MarketDataProducer` with v2 schema support
- Uses `config.binance` for configuration (symbols, websocket_url, reconnect_delay)
- Produces to Kafka topic: `market.crypto.trades` (asset-class-level topic)

### Enhancements Beyond Original Plan:
- Added `--daemon` flag for background operation
- Added comprehensive statistics tracking
- Added Rich console output with color-coded status messages
- Added progress logging (every 100 trades)
- Added timeout handling for client task cancellation
- Added detailed error context (symbol, trade_id)
- Added environment variable documentation in docstring

**Note**: Requires Docker services running (Kafka, Schema Registry) for testing.

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete

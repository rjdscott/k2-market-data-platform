# Step 01.5.8: Documentation

**Status**: ⬜ Not Started  
**Estimated Time**: 4 hours  
**Actual Time**: -  
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Document Binance streaming integration comprehensively.

---

## Tasks

### 1. Update README.md

Add "Live Streaming" section:

```markdown
## Live Streaming

K2 supports both batch and streaming data sources:

**Batch Sources**: CSV files (historical ASX equity data)
**Streaming Sources**: Binance WebSocket API (live crypto trades)

### Starting Binance Stream

\`\`\`bash
python scripts/binance_stream.py
# or
docker compose up -d binance-stream
\`\`\`

### Querying Live Data

\`\`\`bash
curl "http://localhost:8000/v1/trades?symbol=BTCUSDT&window_minutes=15"
\`\`\`
```

### 2. Create docs/architecture/streaming-architecture.md

Document:
- Binance WebSocket → Kafka → Iceberg flow
- Error handling and resilience patterns
- Circuit breaker integration
- Metrics and monitoring

### 3. Update DECISIONS.md

Add Decision #016: Binance Streaming Integration
- Why Binance (most liquid crypto exchange)
- Why WebSocket (push vs. poll efficiency)
- v2 schema benefits for multi-source

### 4. Demo Talking Points

Update `docs/phases/phase-2-prep/reference/demo-talking-points.md`.

---

## Validation

```bash
grep -A 5 "Live Streaming" README.md
ls -la docs/architecture/streaming-architecture.md
grep "Decision #016" docs/phases/phase-1-*/DECISIONS.md
```

---

## Commit Message

```
docs: document binance streaming integration

- Add "Live Streaming" section to README
- Create docs/architecture/streaming-architecture.md
- Add Decision #016 to DECISIONS.md
- Update demo talking points
- Document end-to-end flow

Related: Phase 2 Prep, Step 01.5.8
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started

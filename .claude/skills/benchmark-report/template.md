# Benchmark — YYYY-MM-DD

**Commit** `<short sha>` · **Stack uptime at measurement** <e.g. 27 h> ·
**Window** <e.g. 1 h of trades> · **Host** <cores / RAM>

One paragraph: what moved since the previous benchmark, and anything that
should worry a reader.

## Ingestion

| Metric | Binance | Kraken | Coinbase | Command |
|--------|---------|--------|----------|---------|
| Trades/s | | | | [T1](#t1) |
| Trades in window (n) | | | | [T1](#t1) |
| Gaps in window | | | | [T2](#t2) |

## Latency — exchange timestamp → ClickHouse Silver

Includes internet transit and exchange clock skew. Not a trading-path latency.

| Exchange | n | p50 (ms) | p95 (ms) | p99 (ms) | Command |
|----------|---|----------|----------|----------|---------|
| | | | | | [T3](#t3) |

## Resources vs compose limits

| Service | CPU used | CPU limit | RSS | Mem limit | Command |
|---------|----------|-----------|-----|-----------|---------|
| | | | | | [T4](#t4) |
| **Total** | | | | | |

Budget: 16 CPU / 40 GB. Headroom: … .

## Storage

| Table | Rows | On disk | Compression | Bytes/day | Command |
|-------|------|---------|-------------|-----------|---------|

## Cold tier

| Table | Ingest lag | Rows added | Files | Command |
|-------|------------|------------|-------|---------|

## Query timings

| Query | Rows returned | Median of 3 (ms) | Command |
|-------|---------------|------------------|---------|

## MTTR

From `docs/runbooks/failure-recovery.md`. Re-induced for this
report: yes / no (if no, cite the date they were last measured).

---

## Commands

<a id="t1"></a>**T1**
```bash
```

<a id="t2"></a>**T2**
```bash
```

<a id="t3"></a>**T3**
```bash
```

<a id="t4"></a>**T4**
```bash
```

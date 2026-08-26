# Benchmarks

Dated snapshots of what the stack actually measured: throughput, latency
percentiles, resource use against the compose limits, storage growth, offload
lag, query timings, MTTR.

**Every number published anywhere in this repo — README, architecture docs, ADR
Outcome sections — must be traceable to a row in the latest file here, and every
row carries the exact command that produced it.** A number without a command is
a claim, and claims get audited out.

One file per measurement session, `YYYY-MM-DD.md`.

## Conventions

- Header records the commit, the stack uptime at measurement, the window, and
  the host. A cold-stack measurement is worthless; 24 h burn-in is the bar for
  anything quoted in the README.
- Percentiles always carry their sample size `n` and their window.
- Latency figures state plainly what they include — exchange-timestamp-based
  numbers include internet transit and exchange clock skew. This is not a
  trading-path latency measurement, and the file says so.
- A measurement that couldn't be taken reads "not measured". Numbers are never
  carried forward from an older file.
- Nothing is rounded to a nicer number. 15.1 CPU is not "about 15".
- **Snapshots are immutable.** A new measurement is a new dated file. If a
  published figure moved by more than ~10%, the new file's summary says so.
- Use the `/benchmark-report` skill; it has the commands.

## Index

| Date | Commit | Highlights |
|------|--------|------------|
| _none published yet — v2 numbers currently live in `docs/decisions/README.md` and `docs/operations/`_ | | |

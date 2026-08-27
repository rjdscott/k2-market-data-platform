# tests/parity

The pinned inputs of the last passing three-way OHLCV parity run
(`scripts/parity-ohlcv.sh`; runbook `docs/runbooks/clickhouse-rebuild-from-lake.md` §4).
`pinned.json` names the UTC day, the `lake.gold.ohlcv_1m` snapshot id and the silver
snapshot ids per venue that `gold.trades` was built from — literal snapshot ids, never
`latest`, so the run is repeatable to the same answer. This is the seed of the Phase G CI
parity job (plan 004).

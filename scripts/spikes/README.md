# Spikes

A spike is a short measured experiment run before a design commits to something. Each one
lives in `scripts/spikes/<date>/<name>/`, dated by the UTC day it was first run, so the claim it
backs can be re-run rather than trusted. Its write-up is in `docs/research/`; a number it
produced that anyone quotes is published in `docs/benchmarks/`, which cites the spike's command.

A spike directory holds:

- `run.sh`, which reproduces the spike against the running stack, prints its findings (one
  `ok ...` or `FAIL ...` line each, or the measured table) and exits 0 only when the run
  completed and every finding still holds;
- the configs, SQL, dbt project and scripts it runs, and nothing generated: no virtualenv,
  `target/`, `logs/` or cache;
- optionally `results-<date>/`, the raw output of the original run, which the published
  numbers were computed from.

## Running one

```bash
bash scripts/spikes/2026-11-26/spike-dbt/run.sh
for s in scripts/spikes/*/*/run.sh; do bash "$s" || echo "FAIL $s"; done   # all of them
```

Each `run.sh` names in its header what it needs (the stack's `k2-trino` or `k2-spark-iceberg`,
`uv`, the MinIO keys from `.env`) and how long it takes.

## Rules a spike keeps

- It writes only scratch objects named `spike_*` (a schema, a namespace, a bucket) and
  throwaway containers named `k2-spike-*`, and removes them on exit, pass or fail. Tables are
  dropped through the catalog, and Lakekeeper removes their files through its own expiry queue.
- It never restarts, stops or reconfigures a stack service, and never triggers or pauses a DAG.
- A Spark spike runs in `k2-spark-iceberg` only when no lake job or other Spark driver is
  running there, with a 512m driver, and only while the host has 6 GiB available.
- Synthetic data only. No credential in a file: keys are read from the environment or `.env`
  and passed by name.
- A spike that no longer holds says `FAIL` and exits 1. Its write-up and any benchmark that
  quoted it are dated snapshots; a changed result is a new dated run, not an edit.

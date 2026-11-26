# Spike: the fixed cost of one product run (2026-11-26, 11:08 to 11:15 UTC)
Measured in the running Spark container between scheduled ingest runs, driver heap 512m, scratch namespace dropped afterwards. Seconds, min / median / max, n = 5.

| Step | Small table | Table with 200 snapshots, 200k rows |
|---|---|---|
| Spark session with the catalog loaded | 2.53 / 2.54 / 2.60 | 2.50 / 2.53 / 3.12 |
| Create branch | 0.10 / 0.10 / 0.11 | 0.14 / 0.15 / 0.16 |
| Append 1,000 rows | 1.65 / 1.68 / 1.75 | 1.63 / 1.69 / 2.32 |
| Audit: count | 0.54 / 0.56 / 0.62 | 0.52 / 0.57 / 1.01 |
| Audit: uniqueness | 0.35 / 0.47 / 0.60 | 4.27 / 4.40 / 5.64 |
| Fast-forward | 0.09 / 0.09 / 0.10 | 0.10 / 0.11 / 0.13 |
| Drop branch | 0.03 / 0.03 / 0.04 | 0.04 / 0.05 / 0.06 |
| Stop | 0.36 / 0.44 / 0.64 | 0.53 / 0.65 / 0.72 |
| Total, wall clock | 6.44 / 6.51 / 6.60 | 10.45 / 10.54 / 13.64 |

The growth with table size is the uniqueness audit scanning the whole branch, not metadata: branch steps grew by about 0.07 s.
dbt build of one small incremental model with two tests (dbt-core 1.12.7, dbt-trino 1.12.0): 3.02 / 3.50 / 4.71 s (n = 6); dbt's own work is 0.5 to 2.2 s, the rest is process start and project parse.
Context: `lake_ingest` over the last 24 h: median 189 s, p90 253 s (277 successful runs). Limits: Spark container 3 CPU / 8 GiB, Trino 2 CPU / 5 GiB.
Estimate (arithmetic, not measured): one Spark slot back to back fits about 46 small product runs per five minutes (28 at the larger size); Trino about 85 dbt builds serially. Session start plus the first append is about 65 percent of a Spark run.
Not measured: Airflow's scheduling overhead per run; a product run beside another job; memory per run; the dbt project's on-run-end results write.

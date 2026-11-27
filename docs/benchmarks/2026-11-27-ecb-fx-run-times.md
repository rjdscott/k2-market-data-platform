# Benchmark: 2026-11-27, ECB FX run times

**Commit** `167b93a`, the release `v3.0.0-alpha.4-rc.2` (deployed; `docker-compose.yml`,
`docker/airflow/dags/factory.py` and `contracts/fx-reference/` are unchanged from it to `9e25a32`,
where this file was written) · **Stack uptime at measurement** not recorded · **Window**
2026-11-27 10:06 to 10:38 UTC (the 20 triggered runs); the new-file run is the first scheduled
run after the deploy, `scheduled__2026-11-26T15:30:00+00:00` · **Host** single host, i7-14700K,
28 threads, 62 GiB ([H1](#h1)); the Docker VM's size was not recorded · **Limits** the steady
default profile, 19 services, 15.80 CPU, 27.750 GiB ([B1](#b1))

The wall time of one DAG run of `fx-reference.ecb-fx-rates` (DAG `ecb_fx_rates`), as Airflow
records it: the run's `end_date` minus its `start_date`, so the time a run waited queued before
it started is not in it. Measured on the live stack so plan 002's "Its timeout is set from 20
triggered runs (their maximum times a stated factor)" has its numbers. **Decision: the product's
`schedule.timeoutMinutes` is 10. Factor 5 on the largest run measured, the new-file run at
121.7 s, is 608.5 s, rounded to 10 minutes (600 s, 4.9 times that run, 17.2 times the largest
unchanged-file run).**

Not measured: throughput, query latency, a single task's time, memory, and any run beside
another product's run.

## Scheduler and what else ran

- Airflow 3, `LocalExecutor`, `AIRFLOW__CORE__PARALLELISM` 4: four task slots
  (`docker-compose.yml`).
- Pools `lake_spark` 1, `fetch` 1, `dbt` 2 (`airflow-init` in `docker-compose.yml`); fetch takes
  `fetch`, load, audit, publish, expire and maintain take `lake_spark`, build takes `dbt`
  (`POOLS` in `docker/airflow/dags/factory.py`).
- In the window, nothing else was scheduled but `access_canary`, every 10 minutes, whose build
  takes one of the two `dbt` slots: 10:10, 10:20 and 10:30 UTC fall inside it. Whether a run's
  build shared the pool with the canary's was not recorded. `quality_results` runs at minute 50,
  `calendars` at 04:00 UTC and `currency_master` on Mondays, all outside the window.

## Run times

Seconds, `end_date` minus `start_date`.

| What | n | min | median | mean | max | State | Command |
|---|---|---|---|---|---|---|---|
| Triggered run, unchanged file: resolve, fetch (exit 10, unchanged), load, audit and publish skipped, the dbt build of `silver_rates` and `gold_eur_rates_daily` with their tests through the wrapper, record. 2026-11-27 10:06 to 10:38 UTC | 20 | 31.2 | 32.25 | 32.63 | 34.8 | 20 of 20 success | [A1](#a1) |
| Scheduled run, new file end to end: fetch of the publisher's file, land, load onto the `audit` branch, audits, publish, the first build of silver and gold, record, maintain. `scheduled__2026-11-26T15:30:00+00:00` | 1 | 121.7 | 121.7 | 121.7 | 121.7 | success | [A2](#a2) |
| For context only: `currency_master`, one full run with a new file, `verify__20261127T105125` (started 10:51 UTC, after the window) | 1 | 46.4 | 46.4 | 46.4 | 46.4 | success | [A2](#a2) |

The 20 unchanged-file runs in order, run id `bench__<UTC stamp>`. Each was triggered 60 s after
the one before it reached a final state, so the stamps are 97 to 104 s apart; no two overlapped.

| n | Run id | s | | n | Run id | s |
|---|---|---|---|---|---|---|
| 1 | `bench__20261127T100626` | 31.9 | | 11 | `bench__20261127T102259` | 34.1 |
| 2 | `bench__20261127T100803` | 32.5 | | 12 | `bench__20261127T102442` | 32.3 |
| 3 | `bench__20261127T100940` | 34.8 | | 13 | `bench__20261127T102619` | 31.5 |
| 4 | `bench__20261127T101124` | 32.0 | | 14 | `bench__20261127T102756` | 34.1 |
| 5 | `bench__20261127T101301` | 34.5 | | 15 | `bench__20261127T102939` | 31.8 |
| 6 | `bench__20261127T101444` | 32.8 | | 16 | `bench__20261127T103117` | 32.0 |
| 7 | `bench__20261127T101622` | 31.2 | | 17 | `bench__20261127T103254` | 32.0 |
| 8 | `bench__20261127T101800` | 31.2 | | 18 | `bench__20261127T103431` | 32.2 |
| 9 | `bench__20261127T101938` | 33.5 | | 19 | `bench__20261127T103608` | 33.2 |
| 10 | `bench__20261127T102115` | 33.2 | | 20 | `bench__20261127T103745` | 31.8 |

## The timeout

`timeoutMinutes` sets both the run's `dagrun_timeout` and the fetch task's `execution_timeout`
(`make_dag` in `docker/airflow/dags/factory.py`). Before this file the product had none: no run
timeout, and the fetch at the factory's default, `FETCH_TIMEOUT`, also 10 minutes. So the fetch's
limit does not move; what is new is that a whole run is stopped at 10 minutes. The fetch's own
deadline in `docker/fetch/` is 300 s and still goes first.

The factor is taken on the new-file run, not on the 20, because the 20 skip load, audit and
publish, which a new file runs. A factor on their maximum (34.8 s) would have given a timeout
under the new-file run's 121.7 s.

## Limits of these numbers

- **The new-file run is n = 1.** It is the largest number here and the timeout rests on it. It
  was also the first build of silver and gold on that release, which a later new-file run does
  not repeat; whether a later run is shorter or longer was not measured.
- **The network time to the publisher is inside the fetch** and was not measured apart from it.
  The unchanged-file runs download the whole file too (`docker/fetch/fetch.py` compares its
  sha256 after the download), so their spread, 31.2 to 34.8 s, holds that time as well; how much
  of it is network was not separated.
- **One file size.** The product fetches the publisher's 90-day file; a longer window is a larger
  file and a longer load and build.
- **No concurrency.** Runs went one at a time, on a host where only the canary was scheduled.
  A run that queues for the `fetch` or `lake_spark` pool behind another product is longer, and
  that wait is inside `dagrun_timeout`.
- **Stack uptime was not recorded**, and these are a quiet host's numbers, not a burn-in's.

## Revisit when

- The product's connector URL or the publisher's window changes (a file longer than 90 days, the
  full history file among them).
- Another product is scheduled within 10 minutes of 15:30 UTC on a weekday, so it shares the
  `fetch` or `lake_spark` pool of one with this product.
- Any `ecb_fx_rates` run takes more than 300 s, half the timeout.
- Five scheduled runs that carry a new file have been recorded: the new-file row is then
  re-measured with n = 5 and the factor checked against its maximum.

---

## Commands

<a id="a1"></a>**A1**: on the live stack, each of the 20 runs triggered with an id stamped by the
host's UTC clock, then, once its state was final, the run list read and the duration taken as
`end_date` minus `start_date` of that run id; the next run was triggered 60 s later.
```bash
docker exec k2-airflow-scheduler airflow dags trigger ecb_fx_rates --run-id bench__<UTC stamp>
docker exec k2-airflow-scheduler airflow dags list-runs ecb_fx_rates -o json
```

<a id="a2"></a>**A2**: on the live stack, the same run list, `end_date` minus `start_date` of the
named run id.
```bash
docker exec k2-airflow-scheduler airflow dags list-runs ecb_fx_rates -o json
docker exec k2-airflow-scheduler airflow dags list-runs currency_master -o json
```

<a id="b1"></a>**B1**: the compose limits, read from `docker-compose.yml` with no container; the
steady line is the one quoted above.
```bash
python3 scripts/check-budget.py
```
```text
steady default         19 services   15.80 CPU   27.750 GiB
```

<a id="h1"></a>**H1**: the host.
```bash
lscpu | grep 'Model name'; nproc; free -g | head -2
```

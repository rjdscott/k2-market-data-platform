# Runbook: Recover after the host kills the Docker VM for memory

On Docker Desktop for Linux every container runs inside one virtual machine (VM), a
`qemu-system-x86` process on the host. When the host runs out of memory, the host kernel's
out-of-memory killer (the part of Linux that ends a process to free memory when nothing is
left) picks the largest process, which is that VM. Every container stops at the same instant,
and nothing restarts until someone starts Docker Desktop again. This page covers confirming
that cause, bringing the stack back, the checks after it, recording what was lost, and sizing
the VM so it does not happen again. It does not cover one container killed at its own memory
limit, which is an ordinary out-of-memory kill inside the VM
(`docker inspect -f '{{.State.OOMKilled}}' <container>`, [observability.md](../operations/observability.md)),
nor a plain Docker engine on Linux, which has no VM.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Host out-of-memory killer kills the Docker VM | < 15 min from noticing | 10 min 17 s from the kill to `make health` PASS, 5 min 26 s of it after Docker Desktop was started (2026-11-25, one occurrence, not induced) |

---

## 1. The Docker VM was killed

**Symptom**, every container is gone at once. `docker ps` fails with `Cannot connect to the
Docker daemon` (or similar) while the VM is down. After a restart, every container shows the
same short uptime in `docker ps`, Grafana has a gap in every panel at the same minute, and
the capture metrics show every venue silent for the same window.

**Detection**, manual only. **No alert covers this and none can from inside the stack:**
every container, Prometheus and cAdvisor included, sees the VM's memory, not the host's, so
nothing the stack scrapes knows how close the host is to running out, and at the moment of
the kill Prometheus dies with everything else. The signals are on the host:

- `systemctl --user status docker-desktop` reports `Result: oom-kill`;
- the kernel log has an `Out of memory: Killed process <pid> (qemu-system-x86)` line;
- `free -h` showed swap full before the kill.

**Expected behaviour**, nothing comes back by itself. systemd marks the Docker Desktop unit
failed and does not restart it. Once Docker Desktop is started again:

- every service with `restart: unless-stopped` restarts by itself, in the order Docker starts
  them, and the one-shot services (`restart: "no"`: `redpanda-init`, `lake-init` and the rest)
  do not run again, which is correct, because they finished before the kill;
- on 2026-11-25 one service did not: Prometheus stayed at `Exited (255)` despite
  `unless-stopped`, and had to be started by hand (step 4). Expect that again and check;
- capture reconnects to each venue and resumes, but **the market data of the outage is not
  recoverable**: the venues' public feeds do not replay, so the window between the kill and
  the capture's reconnect is a permanent hole in both the hot tier and the lake;
- the lake ingest resumes from the Kafka offsets in its last committed Iceberg snapshot
  ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), so the run the kill
  interrupted committed nothing or everything, and Airflow retries it once;
- Redpanda runs with `--unsafe-bypass-fsync=true` and `write_caching_default` `true`
  (`--mode dev-container` in `docker-compose.yml`): it acknowledges a record before it is on
  disk. Records still in the VM's memory when the VM died are gone, and ClickHouse and the
  lake may each have read a different part of the last seconds. That is why step 7 compares
  them.

**Recovery**

```bash
# 1. Confirm the cause before anything else. On 2026-11-25 the status line read
#    "Active: failed (Result: oom-kill)"; once started again it reads "active (running)".
systemctl --user status docker-desktop --no-pager | head -3
journalctl -k --utc --no-pager | grep -i -E 'out of memory|oom-kill'
journalctl --user -u docker-desktop --utc --no-pager | grep -E "Failed with result|Started docker-desktop"
#   Run 2026-11-26 (host name replaced):
#   Nov 25 22:33:43 <host> kernel: kworker/u113:2 invoked oom-killer: gfp_mask=0x2cc0(...), order=0, oom_score_adj=0
#   Nov 25 22:33:43 <host> kernel: oom-kill:constraint=CONSTRAINT_NONE,...,global_oom,task_memcg=/user.slice/.../docker-desktop.service,task=qemu-system-x86,...
#   Nov 25 22:33:43 <host> kernel: Out of memory: Killed process 626914 (qemu-system-x86) total-vm:60607640kB, ...
#   Nov 25 22:33:48 <host> systemd[3765]: docker-desktop.service: Failed with result 'oom-kill'.
#   Nov 25 22:39:18 <host> systemd[3765]: Started docker-desktop.service - Docker Desktop.
#   After a host reboot the kernel lines are in the previous boot: add -b -1 to the first journalctl.

# 2. Make room first, or the VM is killed again as it refills: close what was using the
#    host's memory, then check that swap is no longer full.
free -h
```

```bash
# 3. Start Docker Desktop. As run on 2026-11-25 at 22:39:18 UTC: the Docker API answered
#    in about 10 s. Repeat the last line until it prints a version.
systemctl --user reset-failed docker-desktop
systemctl --user start docker-desktop
docker info --format '{{.ServerVersion}}'
```

```bash
# 4. Which long-running services did not come back? Prints nothing when every one is running.
#    Run 2026-11-26 on the healthy stack: no output. On 2026-11-25 it would have listed
#    prometheus (Exited (255)).
comm -23 <(docker compose config --services | grep -v -x -E 'airflow-migrate|lakekeeper-migrate|lake-init|lake-ddl|redpanda-init' | sort) \
         <(docker compose ps --services --status running | sort)

# Start each one it lists, by name. As run on 2026-11-25 for Prometheus. `make up` is not
# the tool here: on this host it refuses until the MinIO credential is replaced
# (docs/operations/README.md, "Read this first: the state of this host").
docker compose up -d --no-deps prometheus
```

```bash
# 5. The whole stack. As run on 2026-11-25: health: PASS at 22:44 UTC. Run 2026-11-26
#    (counts replaced; they are market-derived):
make health
#   (1) health: PASS (all services healthy or have no healthcheck)
#   (2) coinbase: PASS (<n> trades/2min)   ... one line per venue, then one per capture's metrics
#   (3) lake commit age: PASS (last commit 173s ago)
#   (3) last lake_ingest run: PASS (scheduled__2026-11-26T01:16:00+00:00 success)
#   health: PASS
```

```bash
# 6. The lake run the kill interrupted was retried and succeeded. Read the runs around the
#    kill; the one whose end is long after its start is the interrupted one, and its task
#    directory holds two attempts. Run 2026-11-26 for the 2026-11-25 kill:
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs lake_ingest -o plain \
  --start-date 2026-11-25T22:20:00+00:00 --end-date 2026-11-25T22:50:00+00:00
#   dag_id       run_id                                state    run_after  ...  start_date                        end_date
#   lake_ingest  scheduled__2026-11-25T22:41:00+00:00  success  ...             2026-11-25T22:43:36.506452+00:00  2026-11-25T22:47:26.144228+00:00
#   lake_ingest  scheduled__2026-11-25T22:31:00+00:00  success  ...             2026-11-25T22:31:00.679656+00:00  2026-11-25T22:43:35.399218+00:00
#   lake_ingest  scheduled__2026-11-25T22:26:00+00:00  success  ...             2026-11-25T22:26:00.301355+00:00  2026-11-25T22:30:05.662560+00:00
#   The 22:31 run ended 12.5 min after it started. There is no 22:36 run: the scheduler was
#   down for that slot, and the 22:41 run read on from the committed offsets.
docker exec k2-airflow-scheduler ls '/opt/airflow/logs/dag_id=lake_ingest/run_id=scheduled__2026-11-25T22:31:00+00:00/task_id=ingest/'
#   attempt=1.log  attempt=2.log
```

**7. Compare the hot tier with the lake, minute by minute, around the kill.** The procedure
is [failure-recovery.md section 8, "Host or Docker VM killed: the hot tier against the
lake"](./failure-recovery.md#8-host-or-docker-vm-killed-the-hot-tier-against-the-lake); follow
it rather than a copy here. On 2026-11-25 it showed one differing minute, 22:33 UTC, where the
hot tier and the lake hold different partial sets of trades, and no divergence in any other
minute of the day. The owner's decision: leave both as captured, do not repair.

**8. Record the outage** as one row in `lake.audit.checks`, the lake's append-only table of
check results, with `docker/lake/record_check.py`. The row is the permanent record that the
window is missing on purpose, not a bug for the next reader to chase, and the parity gate's
known gap for that minute must name it (`tests/parity/README.md`). The flags:

- `--job operator` marks a row a person filed by hand (the other jobs are the nightly audit,
  the ingest and the replay tool);
- `--check capture_outage` is the check name, the same for every outage like this one;
- `--scope 'stack/<kill>..<capture back>'`, both in UTC to the second, names the window;
- `--observed` is the seconds capture was off, and `--failed` stores `passed = false`;
- `--detail` says the cause and what was left unrepaired, in one or two sentences.

`record_check.py` starts a small Spark session in `k2-spark-iceberg` (the 512m driver below),
beside whatever ingest is running. The 2026-11-25 row exists, recorded at 2026-11-26 01:00:44
UTC by the command in failure-recovery.md section 8 step 5: do not record it again. For the
next outage:

```bash
docker exec -e K2_LAKE_DRIVER_MEMORY=512m k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
  --job operator --check capture_outage --scope 'stack/<kill>Z..<capture back>Z' \
  --observed <seconds> --failed --detail '<cause>; <what differs and was left as captured>'

# Read it back (run 2026-11-26):
docker exec k2-trino trino --user reviewer --output-format TSV_HEADER --execute \
  "SELECT run_ts, job, check_name, scope, passed, observed FROM lake.audit.checks WHERE job = 'operator' ORDER BY run_ts DESC LIMIT 5"
#   run_ts                          job       check_name      scope                                            passed  observed
#   2026-11-26 01:00:44.228723 UTC  operator  capture_outage  stack/2026-11-25T22:33:43Z..2026-11-25T22:39:26Z  false   343
```

**Measured**, one occurrence, 2026-11-25, not induced: the kill at 22:33:43 UTC, Docker
Desktop started at 22:39:18, capture producing again at 22:39:26 (capture off **343 s**), the
interrupted ingest's second attempt from 22:39:31 to 22:43:35, `make health` PASS at 22:44.
That is 10 min 17 s from the kill to PASS, most of it before anyone noticed: no alert can fire
(Detection above).

---

## 2. Size the VM so the host keeps enough memory

**The rule, as arithmetic you can redo for your host:**

```
host RAM  >  VM memory setting  +  everything else the host runs  +  a margin
```

because **the VM grows to its setting and stays there.** The guest kernel inside the VM uses
every free page as file cache (the copies of files it keeps in memory so the next read is
fast), and QEMU does not hand those pages back to the host. After the 2026-11-25 restart the
VM went from about 20 GiB to about 41 GiB in under three hours. So size for the VM at its full
setting, not at what `docker stats` or the K2 Resources dashboard show the containers using:
those count process memory inside the VM, and the host pays for the whole VM.

The numbers on this host, each with the command that reads it:

| Quantity | Value | Command |
|---|---|---|
| Host RAM, usable | 62.6 GiB (67,168,444,416 bytes; the kernel's report at the kill counted 63.8 GiB) | `free -b` (`total`) |
| VM memory setting | 51,712 MiB = 50.5 GiB | Docker Desktop, Settings, Resources, Memory; `MemoryMiB` in `~/.docker/desktop/settings-store.json` |
| What the containers can use | 49.5 GiB (53,149,335,552 bytes; the VM's own kernel keeps the rest) | `docker info --format '{{.MemTotal}} {{.NCPU}}'` |
| The VM's footprint on the host right now | 44.7 GiB, peak 48.1 GiB, 2026-11-26 01:30 UTC | `systemctl --user show docker-desktop -p MemoryCurrent -p MemoryPeak` |
| Left for everything else | 62.6 minus 50.5 = **12.1 GiB** | arithmetic |
| Swap on the host | 8 GiB | `free -h` |

At the kill the kernel's report showed the VM's shared memory at 48.7 GiB of its 51,712 MiB
and all other processes holding 13.1 GiB of anonymous memory (memory a program allocated for
itself, which cannot be dropped like file cache), with the 8 GiB of swap fully used: 61.8 GiB
of the 63.8 GiB the kernel counted. The other processes alone held more than the 12.1 GiB
the VM leaves once it reaches its setting, and swap was what ran out.

The two ways to satisfy the rule:

- **Keep everything else small.** On this host that means the desktop, browser, IDE and any
  other program together stay well under 12.1 GiB. Watch `free -h`: swap filling is the
  warning that comes before the kill. This is the owner's decision of 2026-11-26: keep the VM
  at 51,712 MiB, keep the desktop light, add no swap.
- **Shrink the VM.** The floor is the stack's declared memory limits at the bootstrap peak,
  37.812 GiB (`python3 scripts/check-budget.py`), plus about 1 GiB for the VM's own kernel,
  so about 39 GiB: below that, Docker Desktop silently clips every container limit above the
  VM size ([setup.md](../development/setup.md)). Measured use is far lower (10.12 GiB of
  process memory at the 6-hour peak in [ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md)),
  so a smaller VM runs, but the limits are then not real. Shrinking memory does not touch
  the disk, but never shrink the *disk image* size: that deletes every volume
  ([docker-desktop-disk.md](./docker-desktop-disk.md)).

The reasoning behind the original sizing, and why it was wrong, is
[ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md)'s Outcome of 2026-11-26.

---

## Failure modes / incidents

- **2026-11-25 22:33:43 UTC**, the host's out-of-memory killer killed the Docker VM
  (`qemu-system-x86`); `systemctl --user status docker-desktop` showed `Result: oom-kill`.
  Recovered with `systemctl --user reset-failed docker-desktop; systemctl --user start
  docker-desktop` at 22:39:18. Every container restarted by itself except Prometheus, which
  stayed `Exited (255)` and was started with `docker compose up -d --no-deps prometheus`. The
  interrupted `lake_ingest` run was retried by Airflow and succeeded; `make health` PASS at
  22:44 UTC. Capture was off 343 s (22:33:43 to 22:39:26); that market data is not
  recoverable. Minute 22:33: the hot tier and the lake hold different partial sets of trades,
  left as captured and recorded as `lake.audit.checks` job `operator`, check
  `capture_outage`. Cause: desktop use plus the VM's full 51,712 MiB exceeded host RAM and
  swap; this page and the ADR-045 Outcome record the corrected sizing rule.

**Revisit when** the VM is killed a second time (then the owner's "keep the desktop light"
decision has failed and the VM setting comes down), or the host's RAM or the VM setting
changes (re-do the table above).

---

**Last verified:** 2026-11-26 against `791c77a` on the live stack, read-only: steps 1, 4, 5,
6 and the read-back in 8 were run; step 3, the Prometheus start in 4 and the 2026-11-25 row in
8 were run by the operator on 2026-11-25/26 as recorded above; step 7 is failure-recovery.md
section 8's, verified there.

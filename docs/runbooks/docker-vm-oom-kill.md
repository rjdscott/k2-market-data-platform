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
| 1 | Host out-of-memory killer kills the Docker VM | < 15 min from noticing | 10 min 13 s from the kill (22:33:43 UTC) to `make health` PASS (22:43:56), 4 min 38 s of it after Docker Desktop started (22:39:18); 2026-11-25, one occurrence, not induced |

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
  the capture's reconnect is a permanent hole in Redpanda's topics and in every product later
  read from them;
- no lake job runs until phase 002, so there is no interrupted run to check; on 2026-11-25 the
  v3 lake ingest's interrupted run was retried by Airflow and succeeded (that pipeline is retired
  and archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/));
- Redpanda runs with `--unsafe-bypass-fsync=true` and `write_caching_default` `true`
  (`--mode dev-container` in `docker-compose.yml`): it acknowledges a record before it is on
  disk. Records still in the VM's memory when the VM died are gone. On 2026-11-25 that left the
  hot tier and the v3 lake each holding a different part of the last seconds; with both stood
  down, nothing downstream has read them.

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
#    host's memory (the largest first; RSS in kB), then check that swap is no longer full.
ps -eo rss=,comm= --sort=-rss | head -10
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
#    make health step (1) checks the same thing since 2026-11-26 ("(1) long-running services");
#    on 2026-11-25 it did not, and passed with Prometheus exited.
#    Run 2026-11-26 on the healthy stack: no output. On 2026-11-25 it would have listed
#    prometheus (Exited (255)).
comm -23 <(docker compose config --services | grep -v -x -E 'airflow-migrate|lakekeeper-migrate|lake-init|redpanda-init' | sort) \
         <(docker compose ps --services --status running | sort)

# Start each one it lists, by name. As run on 2026-11-25 for Prometheus. `make up` is not
# the tool here: on this host it refuses until the MinIO credential is replaced
# (docs/operations/README.md, "Read this first: the state of this host").
docker compose up -d --no-deps prometheus
```

```bash
# 5. The whole stack. As run on 2026-11-25: health: PASS at 22:43:56 UTC. Run 2026-11-26
#    (counts replaced; they are market-derived; steps (3) and (4) are as the operator guide's
#    section 3 describes them since the v3 lake retired):
make health
#   (1) health: PASS (all services healthy or have no healthcheck)
#   (1) long-running services: PASS (every one is running)
#   (2) binance: PASS (<n> records delivered to Redpanda in 2 min; healthcheck ok: ...)   ... one line per venue
#   health: PASS
```

**6 and 7, retired.** Step 6 checked that the v3 lake ingest's interrupted run was retried, and
step 7 compared the hot tier with that lake minute by minute. No lake job runs until phase 002
and both stores are stood down (ClickHouse paused, ADR-057; the v3 lake archived), so there is
nothing to check or compare. On 2026-11-25 the comparison showed one differing minute, 22:33
UTC, left as captured by the owner's decision; both steps are in git history.

**8. Record the outage**: the venue (`stack` when all of them), the window in UTC to the second
from the kill to capture producing again, the seconds capture was off, the cause, and what was
left unrepaired. It is the permanent record that the window is missing on purpose, not a bug
for the next reader to chase. The lake's audit table that held these records retired with the
v3 lake; the 2026-11-25 row (`operator`, `capture_outage`, recorded 2026-11-26 01:00:44 UTC)
survives only in the host copies under `~/k2-backup/`. An operator's record of lost data
returns with the "accept data loss" runbook in
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md);
until then, write it in the incident's issue.

**Measured**, one occurrence, 2026-11-25, not induced: the kill at 22:33:43 UTC (kernel log),
Docker Desktop started at 22:39:18 (its unit's journal), capture producing again at 22:39:26
(capture off **343 s**), the interrupted v3 ingest's second attempt from 22:39:31 to 22:43:35
(Airflow; that pipeline is since retired), `make health` PASS at 22:43:56 (the operator's log). That is 10 min 13 s from the
kill to PASS, 5 min 35 s of it before anyone started Docker Desktop: no alert can fire
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
| The VM's guest RAM on the host right now | 41.7 GiB, 2026-11-26 02:09 UTC | `awk '$1 == "shmem" {printf "%.1f GiB\n", $2 / 2^30}' "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/docker-desktop.service/memory.stat"` |
| Docker Desktop's whole footprint | 49.0 GiB at the same minute | `systemctl --user show docker-desktop -p MemoryCurrent` |
| Left for everything else | 62.6 minus 50.5 = **12.1 GiB** | arithmetic |
| Swap on the host | 8 GiB | `free -h` |

The two footprint rows differ on purpose. Docker Desktop backs the VM's guest RAM with shared
memory, which the unit's cgroup reports as `shmem` in its `memory.stat`: that is the VM.
`MemoryCurrent` is the whole cgroup: the guest RAM, plus host file cache charged to the unit
(Docker Desktop's own reads of its disk image and the like), plus its processes' memory, so it
can read above the VM setting without the VM having grown.

At the kill, the kernel's out-of-memory report (`journalctl -k`, the lines after `invoked
oom-killer`) gives:

| Figure | Kernel line | Value | What it is |
|---|---|---|---|
| Host RAM | `16716394 pages RAM`, `317848 pages reserved` | 63.77 GiB, of which 62.56 GiB usable | 4 KiB pages; usable is what `free` calls `total` |
| System shared memory | `shmem:48707136kB` | 46.45 GiB | nearly all of it the VM's guest RAM; the VM had not yet reached its 50.5 GiB setting |
| The VM process's own mapped share | `shmem-rss:27042252kB` on the `Killed process ... (qemu-system-x86)` line | 25.79 GiB | the part of the guest RAM QEMU had mapped and resident at that instant, not the VM's size |
| Everything else's anonymous memory | `active_anon` plus `inactive_anon` minus `shmem`; or each other task's `rss_anon` in the task list | about 13 GiB: 12.9 by the first, 13.3 by the second | memory programs allocated for themselves, which cannot be dropped like file cache; another 3.7 GiB of theirs was in swap |
| Swap | `Total swap = 8388604kB`, `Free swap = 0kB` | 8 GiB, none free | |

So 46.45 GiB of guest RAM and about 13 GiB of everything else filled the 62.56 GiB of usable
RAM, with swap already full: nothing was left to reclaim. With the VM at its full 50.5 GiB
setting, which it reaches within hours, everything else has 12.1 GiB, and it was already
holding about 13 GiB resident and 3.7 GiB more in swap.

The two ways to satisfy the rule:

- **Keep everything else small.** On this host that means the desktop, browser, IDE and any
  other program together stay well under 12.1 GiB. Watch `free -h`: swap filling is the
  warning that comes before the kill. This is the owner's decision of 2026-11-26: keep the VM
  at 51,712 MiB, keep the desktop light, add no swap.
- **Shrink the VM.** The floor is the stack's declared memory limits at the bootstrap peak,
  36.688 GiB with the paused ClickHouse running and 28.688 GiB without it (the `bootstrap
  hot-gold` and `bootstrap default` lines of `python3 scripts/check-budget.py`, computed on
  2026-11-26 after the v3 lake's retirement removed two services;
  [docker-resources.md](../operations/docker-resources.md)), plus about 1 GiB for the VM's own
  kernel, so about 38 GiB, or 30 GiB while ClickHouse stays paused: below that, Docker Desktop silently clips every container limit above the
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
  interrupted run of the v3 lake ingest (since retired) was retried by Airflow and succeeded;
  `make health` PASS at 22:43:56 UTC. Capture was off 343 s (22:33:43 to 22:39:26); that market
  data is not recoverable. Minute 22:33: the hot tier and the v3 lake held different partial sets
  of trades, left as captured and recorded as an operator row of that lake's audit table (since
  archived), check `capture_outage`. Cause: desktop use plus the VM's full 51,712 MiB exceeded host RAM and
  swap; this page and the ADR-045 Outcome record the corrected sizing rule.

**Revisit when** the VM is killed a second time (then the owner's "keep the desktop light"
decision has failed and the VM setting comes down), or the host's RAM or the VM setting
changes (re-do the table above).

---

**Last verified:** 2026-11-26 on the live stack, read-only: steps 1, 2 and 5 were run, and
step 4's check with the service list read from a copy of the compose file without the two
services the v3 lake's retirement removes (it printed nothing). Step 3 and the Prometheus start
in 4 were run by the operator on 2026-11-25 as recorded above. Steps 6 and 7 are retired.

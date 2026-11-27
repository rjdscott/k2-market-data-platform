# Runbook: A file product's fetch failed, or never started

Use this when the `fetch` task of a file product's DAG (`ecb_fx_rates`, `currency_master`) is red,
or skipped when you expected a new file. `fetch` is the one-shot container that downloads the
product's file under the address guard and runs its parser
([ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md) parts 2 and 3,
[`docker/fetch/fetch.py`](../../docker/fetch/fetch.py)); the scheduler starts it through
[`docker/airflow/dags/oneshot.py`](../../docker/airflow/dags/oneshot.py). A file that arrived and
then failed its audit, or a load that waits behind such a batch, is
[file-source-audit-failed.md](./file-source-audit-failed.md). A Spark task (`load`, `audit`,
`publish`) or `build` failing for another reason is [airflow-scheduling.md](./airflow-scheduling.md)
section 2.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** `<dag>` is the
DAG id, `<run>` the Airflow run id, `<try>` its try number; the one-shot's container is
`k2-fetch-<dag>-<try>` (`container_name` in `oneshot.py`).

> **Both file products ran on the live stack on 2026-11-27.** The exit-0 lines below are what their
> fetch logs printed, and section 4 holds the one live failure of that day, a defect of the fetch
> itself. The other messages are the code's own strings, from `fetch.py` and `oneshot.py` at this
> release, and the exits are unit-tested with no network (`make test-connectors`: 74 passed,
> 2026-11-27); sections 1, 2, 3 and 5 have not met a live case yet.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | fetch exited 10: unchanged, not a failure | none | not a failure |
| 2 | fetch exited 20: refused (scheme, host, redirect, address, size) | < 1 day to a decision | not yet measured: the first live refusal, or plan 002's redirect drill |
| 3 | fetch exited 30: the parse failed, the bytes kept | see [file-source-audit-failed.md](./file-source-audit-failed.md) section 2 | not yet measured |
| 4 | fetch exited 1: network, TLS, HTTP status, deadline, or a fetch defect | the next scheduled run; a defect, the next release | a publisher's outage: not yet measured; the fetch defect of 2026-11-27: one release (worked example below) |
| 5 | The one-shot never started: image, network `k2-egress`, spool helper, the release's one-shot spec | < 15 min | not yet measured: the first live occurrence |

---

## What each exit does to the run

`oneshot.py` maps fetch's exit code to the task's state; the run's later tasks follow from it
([`factory.py`](../../docker/airflow/dags/factory.py)'s trigger rules).

| Exit | Task state | Task log, last lines | Then |
|------|-----------|----------------------|------|
| 0 | success | `fetch: spooled <n> bytes, sha256 <sha256>`, `fetch: <n> rows`, one `fetch: audit <name> passed` or `FAILED` per parser audit | load, audit, publish, build |
| 10 | skipped | `fetch: unchanged, sha256 <sha256>`, then `k2-fetch-<dag>-<try> exited 10` | load, audit and publish skipped; build still runs (`none_failed`); the run record has fetch `passed` with `{"exit": 10, "unchanged": true}` |
| 20 | failed, never retried | `fetch refused <scheme>://<host>: <reason>` | every later data task `upstream_failed`; expire and record still run |
| 30 | success, with the warning `k2-fetch-<dag>-<try> exited 30` | `fetch: parse failed, the bytes are kept: <exception>: <message>` | load lands the file and stages nothing; audit fails it (section 3) |
| 1 | failed | `fetch failed: <reason>` | as 20 |
| other | failed | the docker CLI's own error, then `k2-fetch-<dag>-<try> exited <n>` | as 20; section 5 |

Read the task log in the Airflow UI (`http://localhost:${K2_AIRFLOW_PORT:-8090}`, the DAG, the run,
`fetch`) or from the scheduler:

```bash
docker exec k2-airflow-scheduler airflow dags list-runs <dag> -o json
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=<dag>*' -path '*task_id=fetch*' -name '*.log' | sort | tail -1 | xargs tail -40"
```

The log names scheme, host and path, never a query string (`fetch.py`'s docstring). The one-shot
holds no secret, so nothing in it needs scrubbing.

---

## 1. fetch exited 10: unchanged

**Symptom**, `fetch` is skipped (pink in the UI) and load, audit and publish with it; build ran.

**Detection**, none, and none is needed: this is the normal day when the publisher has not changed
its file. ECB's file changes once per TARGET working day; the ISO 4217 list far less often.

**Expected behaviour**, the downloaded file's sha256 equals that of the newest earlier fetch of
this DAG (`newest_sha256` reads every `fetch.json` under the DAG's spool directory, by `fetchedAt`),
so nothing is spooled and nothing lands. The comparison is with the newest fetch, passed or not:
the same bytes as a file that failed its audit are skipped too, so a bad file is not retried until
the publisher's file changes.

**Recovery**, none.

---

## 2. fetch exited 20: refused

**Symptom**, `fetch` failed on its first try, and the log's last line is one of these, from
`check()` and `download()` in `fetch.py` (the host named is the URL's, or the redirect target's
when a redirect was refused):

| Message | What it means |
|---------|---------------|
| `fetch refused http://<host>: not https` | the URL, or a redirect, is plain HTTP |
| `... : userinfo or a port other than 443` | a user or a port in the URL or a redirect |
| `... : not in allowHosts` | the URL's host, or a redirect's, is not in the product's `allowHosts` |
| `... : it resolves to an address that is not public` | the host's DNS answer holds a private, loopback, link-local, unique-local, CGNAT or other non-public address |
| `... : a control character or space in the URL`, `not a valid host or port`, `a path or query that is not ASCII` | a malformed URL or `Location` |
| `... : Content-Length over <cap> bytes`, `more than <cap> bytes` | over the 64 MiB cap, or the connector's lower `maxBytes` |
| `... : more than 5 redirects` | a redirect loop |

**Detection**, `AirflowTaskFailed` (`docker/prometheus/rules/airflow-alerts.yml`), `task_id="fetch"`.
No alert is specific to a refusal: per-product alerts are the
[to-release plan's phase 002](../plans/2026-11-26-unified-product-platform-to-release/002-controls-alerts-release-engineering.md).

**Expected behaviour**, nothing was spooled, nothing landed, bronze did not move. The same request
is refused again, so the task is never retried (`NO_RETRY` in `oneshot.py`). The next scheduled run
tries again.

**Recovery**

1. Read the product's connector as the release compiled it:

   ```bash
   jq .connector "${K2_RELEASES:-$HOME/k2-releases}/pointers/airflow/compiled/products/<domain>.<product>/pipeline.json"
   ```
2. Ask what the publisher does now, from the host (not from the stack): `curl -sSI <url>` shows the
   status and any `Location`. A publisher that moved its file to another host, or behind a CDN, is
   the usual cause of `not in allowHosts`; one that redirects to plain HTTP is the usual cause of
   `not https`.
3. Decide with the product's owner (`contracts/<domain>/<product>/product.odps.yaml`):
   - **The publisher moved the file:** change the connector's `url` and, only if the new host is the
     publisher's own, its `allowHosts`, in a pull request, then tag, `make release` and `make deploy`
     ([deploy-a-release.md](./deploy-a-release.md)). **Never widen `allowHosts` any other way.** It
     is the product's contract, reviewed with the source's terms, and the guard reads it from the
     release only: nothing on the stack can or should change it.
   - **A private or non-public address:** a DNS problem on the host or a hostile answer. Do not
     route around it; check the host's resolver (`getent ahosts <host>` from the host).
   - **Over the cap:** a file over 64 MiB is not a file product this container takes; a lower
     `maxBytes` in the contract is changed the same way as the URL.

**Measured**, not yet measured live; each refusal is a unit test in `tests/test_fetch.py`.

---

## 3. fetch exited 30: the parse failed

**Symptom**, `fetch` is green with the warning `k2-fetch-<dag>-<try> exited 30`; its log has
`fetch: parse failed, the bytes are kept: <exception>: <message>`. `load` prints `load: the parse
failed, so nothing is staged; the audit fails the batch` and `audit` fails with one `parses` row.

**Detection**, `AirflowTaskFailed` on `audit`, not on `fetch`: a file that does not parse still
lands, because the landing is the record of what arrived.

**Recovery**, [file-source-audit-failed.md](./file-source-audit-failed.md) section 2.

---

## 4. fetch exited 1: network, TLS, HTTP status or deadline

**Symptom**, `fetch` failed; its log's last line is one of these (`fetch.py`):

| Message | What it means |
|---------|---------------|
| `fetch failed: https://<host>: <the lookup error>`, Python's own text, such as `[Errno -3] Temporary failure in name resolution` | the host's name did not resolve from network `k2-egress` |
| `fetch failed: https://<host>: <OSError type>: <message>` | the connection or the read failed: `ConnectionRefusedError`, `TimeoutError` (30 s per read, 10 s per connect), `SSLCertVerificationError` (the certificate chain or name did not verify; TLS verification cannot be turned off) |
| `fetch failed: https://<host>: HTTP <status>` | the publisher answered other than 200 or a redirect |
| `fetch failed: https://<host>: past the 300 s deadline` | the whole download, redirects included, took over 300 s |
| `fetch failed: https://<host>: the body ended <n> bytes short of its Content-Length` | the connection closed before the body the publisher announced had arrived |
| `fetch failed: --release, --product or --run-id is not a plain name`, `... has no https-file connector, or the run is not of DAG <dag>` | the task's arguments do not match the pinned release's pipeline: a defect, not the publisher's; report it |

The task's own `execution_timeout` (the product's `timeoutMinutes`, else 10 minutes, `FETCH_TIMEOUT`
in `factory.py`) kills the container (`on_kill`: `docker kill k2-fetch-<dag>-<try>`) if the fetch
itself hangs past it.

**Detection**, `AirflowTaskFailed`, `task_id="fetch"`.

**Expected behaviour**, nothing spooled or landed. The next scheduled run fetches again; nothing
is lost while the publisher keeps the file (ECB's 90-day file holds the last 90 days).

**Recovery**

```bash
# From the host: does the publisher answer at all?
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' <url>
# Then run the DAG again once it does:
docker exec k2-airflow-scheduler airflow dags trigger <dag>
```

A certificate that does not verify from the stack but does from the host is a CA bundle in the
fetch image older than the publisher's chain: rebuild the image under a new tag
([upgrade-a-pinned-component.md](./upgrade-a-pinned-component.md)), never a flag.

**Worked example: the source, or the fetch? (2026-11-27, live).** `currency_master`'s fetch, under
`v3.0.0-alpha.4-rc.2-e2dcf759de67`, exited 1 with

```text
fetch failed: https://<host>: OSError: [Errno 9] Bad file descriptor
```

while `ecb_fx_rates`'s fetch, the same image on the same network, passed. It was a defect of the
fetch, not a problem of the source: a 200 with a `Content-Length` on a connection the server closes
after the body (`Connection: close`, or HTTP/1.0) hands the socket to the response, which closes it
as it reads the last byte, and the read loop then set a timeout on that closed socket (commit
`e3b2bbc`, [`docker/fetch/fetch.py`](../../docker/fetch/fetch.py)'s `download()`). The fix stops
reading once the response is closed; its tests use a fake socket that closes as a real one does
(`tests/test_fetch.py`). It shipped in `v3.0.0-alpha.4-rc.3-48ad71fba732`, and the next run passed.
How to tell the two apart:

- **A fetch defect**: exit 1 with an `OSError` on every run against one host, while another host's
  fetch passes; the host's own `curl` (the Recovery block above) gets a 200; errno 9 (`EBADF`) is a
  local descriptor error, never a network answer. Report it with the task log; the fix arrives as
  a release. Do not touch `allowHosts` or retry the DAG in a loop.
- **A source problem**: the host's `curl` fails too, or the message is a lookup error, a refused or
  timed-out connection, a certificate, or an HTTP status, and it clears when the publisher recovers.

**Measured**, the fetch defect: fixed in the next release the same day; a publisher's outage: not
yet measured live. Each path is a unit test in `tests/test_fetch.py`.

---

## 5. The one-shot never started

**Symptom**, `fetch` failed and its log has no `fetch:` line at all: the container never ran.
Before the one-shot, `oneshot.py` removes any container left by an earlier try, runs a spool helper
(the fetch image, `--network none`, `mkdir -p /spool/<dag>`), then the one-shot. Each failure names
itself:

| Log line | Cause | Fix |
|----------|-------|-----|
| `docker run --rm --pull never --network none ... mkdir -p /spool/<dag> exited 125: ...No such image...` | the fetch image the release pins is not on this host (the helper runs the same image first) | images arrive with the deploy, never at run time (`--pull never`): `make release-status` names the good release; a local build cannot be rebuilt to its id, so `make release TAG=<its tag>` and deploy that ([deploy-a-release.md](./deploy-a-release.md)). Never `docker image prune -a` on this host |
| `docker run ... mkdir -p /spool/<dag> exited <n>: <docker's error>` | the spool helper failed: the volume `<project>_k2-spool`, or Docker's volume subpath support | `docker volume inspect <project>_k2-spool`; the error text says which |
| `k2-fetch-<dag>-<try> exited 125` after `one-shot: docker run ...`, with Docker's `network <project>_k2-egress not found` | network `k2-egress` is missing: no long-running service joins it, so `docker compose up` never makes it; deploy's `recreate` step does (`ensure_networks` in `scripts/deploy.py`) | the command below, which is the one deploy runs |
| `oneshots.json fetch: refused: <problems>` | the release's one-shot spec asks for more than `ALLOWED` gives fetch (another network, an env name, a secret) | a release that does this fails its own tests; do not edit anything on the stack: report it |
| `K2_RELEASES must be an absolute host path ...`, `K2_COMPOSE_PROJECT must name the compose project ...` | the scheduler's environment lacks the host paths or the project name | a defect of the deployed stack, not of one product: every one-shot fails the same way. Never `docker compose up` from the checkout on a stack that runs a release; report it |
| `release_path ... is not /k2/releases/<release id>` | `resolve` pinned nothing usable | `resolve`'s own log: the airflow pointer |

```bash
# The network, as deploy's recreate step creates it (scripts/deploy.py, ensure_networks), when missing:
P=${K2_COMPOSE_PROJECT:-k2-market-data-platform}
docker network inspect "${P}_k2-egress" --format '{{.Name}}' 2>/dev/null ||
  docker network create --driver bridge --label "com.docker.compose.project=$P" \
    --label com.docker.compose.network=k2-egress "${P}_k2-egress"
docker exec k2-airflow-scheduler airflow dags trigger <dag>
```

**Detection**, `AirflowTaskFailed`, `task_id="fetch"`.

**Expected behaviour**, nothing ran, so nothing to undo. The fence check (`make catalog-fence`) dials
from `k2-egress` too, so a missing network also fails a deploy's verify step.

**Measured**, not yet measured: the first live occurrence. `scripts/verify/oneshot-slots.sh`
exercises the one-shot's limits and its spool helper on a scratch project.

---

## Failure modes / incidents

- **2026-11-27**, `currency_master`'s fetch: `OSError: [Errno 9] Bad file descriptor` on a host that
  closes the connection, a fetch defect fixed in the release that followed (section 4's worked
  example).

---

**Last verified:** the exit-0 lines and section 4's worked example on 2026-11-27 on the live stack
(releases `v3.0.0-alpha.4-rc.2-e2dcf759de67` and `v3.0.0-alpha.4-rc.3-48ad71fba732`); sections 1, 2,
3 and 5 and section 4's recovery commands: not yet, no live case. The exits and refusals are
unit-tested (`make test-connectors`, 74 passed, 2026-11-27, no network); the messages are quoted from
`docker/fetch/fetch.py` and `docker/airflow/dags/oneshot.py` at this release.

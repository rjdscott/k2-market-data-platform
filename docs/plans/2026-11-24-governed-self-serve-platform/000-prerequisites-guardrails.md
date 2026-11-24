# Phase P0: prerequisites and guardrails

**Depends on:** nothing
**Decision it implements:** none. It repairs what the later phases stand on. Umbrella: [ADR-031](../../adr/ADR-031-governed-self-serve-platform.md).
**Delivers:** a running stack with one command for the six existing gates, a CI gate on the resource sum, a lock exit code that cannot be confused with an error, a patched catalog, and the stale facts found while planning corrected.
**Exit:** `make gates` passes on the live stack; the CI `compose` job prints three sums and fails above the ceiling; an ingest that finds the lock held exits 75 and a test asserts it; `/release-check` smoke queries match the stack that exists today.

## Scope

**Pull request 1: gates and stale facts**

- `make gates`: `test`, `lake-verify`, `parity-ohlcv`, `parity-bars`, `check-alerts`,
  `check-docs`, in that order. Every later phase runs it before every merge.
- CI `compose` job (`.github/workflows/ci.yml:141-156`): print three sums in GiB, steady
  default, bootstrap default (one-shots included), bootstrap with `--profile governance`.
  Fail when bootstrap default exceeds 20 CPU / 36 GiB. Run the limits-present check with
  `--profile governance` as well, so a profiled service cannot ship without limits. The
  ceiling stays at today's 16 CPU until ADR-034 raises it in P2.
- `scripts/check-docs.sh`: a gate rejecting U+2014 on lines added relative to `origin/main`
  in Markdown, Python, shell, SQL and YAML. The `docs` job checks out with full history so
  the comparison exists. Gate count updated in `README.md`, `docs/development/testing.md`
  and `Makefile`.
- `Makefile:74-78`: `prom/prometheus` gets the tag the compose file uses.
- Stale facts: the `spark-iceberg` row in the compose summary comment; the ADR count at
  `README.md:86`; the GiB figures at `docs/adr/README.md:5`; one required engine memory value
  in both `docs/development/setup.md` and `docs/runbooks/fresh-install.md`; the six drift
  rows listed in the plan README.
- `.claude/skills/release-check/SKILL.md`: lake and `gold` smoke queries in place of the
  dropped `k2.silver_trades`; the test description corrected.

**Pull request 2: lock exit code**

- `docker/lake/ingest.py`: lock held exits 75 (`EX_TEMPFAIL`) in place of 2. The Prefect flow
  raises on any non-zero exit (`lake_flows.py:72-79`), so its behaviour does not change.
- Chaos scripts and `docker/lake/README.md` that mention exit 2 updated. A unit test holds
  the lock and asserts 75.

**Pull request 3: catalog patch**

- Lakekeeper `v0.13.3` to the newest `v0.13.x`, both the service and the migrate one-shot.
  No minor upgrade: 0.15 changes view behaviour and gets its own decision if wanted.

**Stack.** No `k2-*` container is running at the time of writing. Bring it up with
`make up`. If the first ingest reports evicted offsets, follow
[lake-ingest-lag](../../runbooks/lake-ingest-lag.md) rather than forcing it from a script.

## Verification

```bash
make up && make health
make gates                                   # includes the lock exit-code test
docker compose --env-file .env.example --profile governance config -q
docker inspect --format '{{.Config.Image}}' k2-lakekeeper
```

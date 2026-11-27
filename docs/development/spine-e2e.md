# The spine end to end (`make spine-e2e`)

This is a page of its own, linked from [testing.md](testing.md), because it is a practice
of the project and not one more suite: after every merge into a phase branch and before
every tag, one command proves that the release and rules spine works together.

## What it is

`make spine-e2e` is one command that runs the whole release and rules spine, from the tree
as it is on disk, and prints one line per stage. It starts throwaway containers only (their
names carry a prefix of their own) and never touches the running stack. It exits 0 when
every stage passed. The script is [`scripts/verify/spine-e2e.sh`](../../scripts/verify/spine-e2e.sh).

Terms used below:

- **The spine**: the path a product's definition takes to a deployed, access-controlled
  service: `k2gen` (the contract compiler, `platform/k2gen/`) compiles a git tag into a
  sealed *release* (a directory of generated configuration, rules and SQL); the deploy
  ([ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md), chapter
  [21](../architecture/21-releases-and-deploy.md)) puts a release on the stack; Trino's
  access rules in it decide who reads what.
- **A throwaway lake**: Postgres, MinIO, Lakekeeper and Trino started for one test on a
  Docker network of their own, from a compiled release, and removed afterwards
  ([`scripts/lib/throwaway-lake.sh`](../../scripts/lib/throwaway-lake.sh)). It is the lake a
  deploy leaves: made by the real `docker/lake/init-lake.sh`, with one MinIO user and policy per
  engine on this run's own keys and Trino on its own pair; the release's Spark tables (each
  connector product's bronze, `landing_files` and `audit_checks`) made by Spark on its key while
  Trino starts; and, when a row of the release's expectations runs `via: rest`, k2-api built
  from the tree and joined to it, with a key for each such row's principal.
- **A merge point**: a merge into a phase branch (for example
  `phase/001-release-and-rules-spine`), or the moment before a tag.

## How it works

The script copies every tracked and untracked, non-ignored file of the current tree into a
scratch git repository under `$HOME` (`k2-spine-e2e.*`; Docker Desktop shares only the home
directory), commits it, and tags it `v0.0.0-e2e.1`. That scratch tag is compiled with
placeholder image digests, so the release it makes can be tested and can never be deployed.
Every later stage reads that one release. Nothing is committed or tagged in your checkout.

| Stage | What it starts | What it proves | A failure means |
|-------|----------------|----------------|-----------------|
| `release` | nothing (k2gen runs in `uv`) | `k2gen docset` and `k2gen release` compile the tree's contracts at the scratch tag | the tree does not compile; every later stage is meaningless |
| `access-verify` | a throwaway lake, prefix `spine-e2e-av` | every row of [`tests/access/expectations.yaml`](../../tests/access/expectations.yaml) holds on that release | a role sees what it should not, or cannot see what it should |
| `test-access` | a throwaway lake, prefix `access-test` | the proofs behind that suite ([`tests/access/prove.sh`](../../tests/access/prove.sh)): a loosened rule fails exactly its row, the drift and marker checks catch their plants, a rules-only release takes effect without a restart, each principal lands in its resource group, and a builder that replaces its base with a view of constants is caught | the verifier itself can no longer fail when it should, which is worse than a failing row |
| `rules` | one throwaway lake, prefix `spine-e2e` | in order, [`trino_rules_test.py`](../../scripts/trino_rules_test.py) `suite` and `negative`, [`trino_rules_c1_test.py`](../../scripts/trino_rules_c1_test.py) (a builder tampering with its base), `control` (destructive, so after C1), then [`identity.sh --throwaway`](../../scripts/verify/identity.sh) (HTTPS, passwords, certificates, rotations) | a rule, a mask, the fence or the login path is wrong on Trino 484 |
| `run` | one throwaway lake, prefix `run-path`, with one long-lived Spark container on compose's Spark image and the `dbt-runner` image built from the tree ([`run-path.sh`](../../scripts/verify/run-path.sh)) | one product's run path, each task the release's own code in the shape the DAG factory gives it: calendars built through the dbt wrapper; ECB's synthetic good file landed, audited and published to bronze; ECB built on calendars' tested snapshot and read through the reader view, its rates exact; the same rows through the lake's k2-api as the same reader, with no key refused (401) and bronze and silver refused (403); the same bytes again landing and staging nothing; the corrupt file failing its audit and a third file refused behind it until the operator resets `audit` to `main`; a failing build restored, its snapshots expired by id, maintenance clean; every build's run records under its release; then every dbt product of the release built through the wrapper, each after the products it reads, a table of no rows among them | the named claim broke; the stage stops there, since each later claim builds on the lake the one before left |
| `rehearsal` | compose project `k2-rehearsal` ([`deploy_rehearsal.py`](../../scripts/verify/deploy_rehearsal.py)), with a Spark service built from the tag, so deploy's `ddl` step runs its real Spark path | by default `make deploy-rehearsal FAST=1`, for merge points: a first deploy that fails verification then succeeds, a planted view the drift check names, a rules-only release that restarts nothing, and kills after `swap` and `recreate` only, the skipped sections named in its summary. With `FULL=1`, before a tag, the whole chain: also one image changed, a forced mismatch that restores the previous release, `make rollback`, a dropped masked column polled every 50 ms, a kill after each of the deploy's steps then a rerun, two deploys at once, the backup-set refusal | a deploy claim of ADR-058 does not hold |

The `run` stage does not run the fetch itself: a local fixture server would sit on a private
address, which the fetch must refuse, so `make test-connectors` proves the fetch, and the stage
writes each spool entry as the fetch leaves it after its download (the raw file and `fetch.json`
with its sha256), then runs the fetch's own parse, which runs the release's parser and its
audits. It needs real local images, never the manifest's placeholders: Spark's image as the
compose file pins it, and `dbt-runner` built from the tree. The same machinery
(`scripts/lib/run-path-lib.sh`) runs plan 002's `fx-idempotent.sh`, `fx-bad-file.sh`,
`dbt-failing-test.sh` and `trino-stop-mid-build.sh`, each on a lake of its own.

Times, each from one recorded run, 2026-11-27, `make spine-e2e`: the fast rehearsal 416 s, then
605 to 615 s after Spark joined the rehearsal's stack; a full run 1085 s. The first recorded run,
before the fast rehearsal existed, printed `PASS rules 261s`, `PASS rehearsal 1347s` and a total
of `1609 s`. The `run` stage, from recorded runs of 2026-11-27: `run-path: 7 PASS, 0 FAIL in 232 s` before its REST claim
was added, then `run-path: 8 PASS, 0 FAIL in 227 s`, and inside `ONLY="run" make spine-e2e` the same day
`PASS run 232s  run-path: 8 PASS, 0 FAIL in 230 s`; with claim 9 added, one recorded run printed
`run-path: 9 PASS, 0 FAIL in 263 s`. No file in the
repository records the other stages' times, so none is given. Memory: each throwaway lake refuses
to start under 6 GiB available (`MIN_GIB` in `throwaway-lake.sh`), and the `run` stage and the
rehearsal under 10 GiB (`run-path-lib.sh`, `deploy_rehearsal.py`).

## How to use it

```bash
make spine-e2e                      # all six stages, the fast rehearsal: after every merge into a phase branch
FULL=1 make spine-e2e               # the whole rehearsal: before every tag
ONLY="rules" make spine-e2e         # one stage again (several: ONLY="rules rehearsal")
KEEP=1 make spine-e2e               # keep the logs
```

`ONLY` works through `make` because make passes its environment to the recipe, which is
`bash scripts/verify/spine-e2e.sh`; the bash form `ONLY="rules" bash scripts/verify/spine-e2e.sh`
is the same run. The release is always rebuilt first, since the other stages read it.

Each stage's output goes to `<log dir>/<stage>.log`, where the log dir is a `k2-spine-e2e.*`
directory under `$HOME`. **The directory is removed when the run ends unless `KEEP=1`** (the
run then prints `spine-e2e: logs in <dir>`). So a run that printed a `FAIL` line is read like
this: the line shows only the last line of that stage's log; rerun the one stage with
`KEEP=1 ONLY="<stage>" make spine-e2e`, open `<log dir>/<stage>.log` and read from the first
`FAIL` line, not the last. The suites' own rows are named (`h1.*`, `c1.*`, `I01` to `I17`), and
the name says which claim broke.

If a run is killed (a closed terminal, `kill -9`), the cleanup trap does not run. Remove what
it left:

```bash
bash scripts/lib/throwaway-lake.sh down spine-e2e        # also spine-e2e-av, access-test and run-path
docker rmi dbt-runner:run-path                            # the run stage's image
docker compose -p k2-rehearsal down -v                    # the rehearsal's project
docker ps -a --filter name=k2-rehearsal --filter name=spine-e2e   # what is still there
```

then delete the stale `k2-spine-e2e.*`, `.k2-run-path.*` and `s19-rehearsal-*` directories under `$HOME`.

## When to run it

The rule of this project, also in [`CLAUDE.md`](../../CLAUDE.md):

- **Run it** after every merge into a phase branch, and before every tag with `FULL=1`. The
  last line says which ran: `(fast rehearsal; FULL=1 before a tag)` or `(full rehearsal)`.
- **Do not run it per step.** For a step, run the tests of what the step touches.
- **It is not in CI.** CI runs the half that is fast enough for every pull request: the
  validation runner (`scripts/verify/ci-validate.sh`: docset, check, trial compile), the rules
  suite (`scripts/trino-rules-test.sh`), `make access-verify` on that release, the dbt trees,
  and the pytest run, which includes the compose fence test
  ([`tests/test_catalog_fence_compose.py`](../../tests/test_catalog_fence_compose.py)).
  `make test-access` and the deploy rehearsal run only here (a comment in
  [`ci.yml`](../../.github/workflows/ci.yml) says so).

The test pyramid of this repository as it is:

```mermaid
flowchart TB
  A["After a deploy, on the live stack<br/>make release-verify, make health"]
  B["At merge points and before a tag<br/>make spine-e2e: throwaway containers, six stages"]
  C["Per pull request, in CI<br/>validation runner, rules suite, access-verify,<br/>dbt trees, pytest, compose, docs"]
  D["Per step, by the author<br/>the unit gates of what the step touches"]
  D --> C --> B --> A
```

## Why it is the practice

On 2026-11-27, with every unit suite green, it caught three integration faults before they
reached the live stack:

1. The destructive control step ran before the C1 rows, so the schema C1 reads was gone: a
   stage-order fault, fixed by the order the `rules` stage now has
   ([`spine-e2e.sh`](../../scripts/verify/spine-e2e.sh), the comment on C1).
2. Three new network-probe rows in [`tests/access/expectations.yaml`](../../tests/access/expectations.yaml)
   made `tests/access/prove.sh` stop at its second step.
3. A catalog fence check ([`scripts/verify/catalog-fence.sh`](../../scripts/verify/catalog-fence.sh))
   failed every deploy's verify.

And the first live deploy ([first-cutover-to-releases.md](../runbooks/first-cutover-to-releases.md):
144 s from the command to verified) worked at the first attempt, after the same path had run
on the rehearsal.

Later the same day, building phase 002's run path, every unit suite again green, it caught four
more:

1. The rehearsal's scratch repository held no `docker/lake/`, so the lake it built could not run
   the real `init-lake.sh` and its engine users: the scratch repository now carries the lake-init
   files and makes its lake with them.
2. `access-verify` could not stage the tables Spark builds (a connector product's bronze, its
   `landing_files` and `audit_checks`): the throwaway lake had no Spark, and stage refused them.
   The throwaway lake now makes the release's Spark tables itself, by Spark on its own key, and
   stage checks they exist; the rules suite, which had made them by REST, now reads them too (its
   create answered 409 once they existed).
3. Every product's dbt build but one failed at `dbt parse`, because dbt parses every model of the
   release's one project whatever the selector, and ECB's gold model's input had no pin in the
   other products' builds.
4. The cause of 3: the input pin check in `k2_input` ran at parse for the whole project, not at
   compile for the selected models. It now refuses a missing pin only when the model compiles,
   and `make test-dbt` builds the products in the order their input ports give.

It also missed one. The first deploy of a release that added products failed on the live stack on
2026-11-27, at `views-narrow`, and its rollback's verify exited 3: no release the rehearsal had ever
deployed added a product, so neither path was run. The rehearsal now carries a release that adds a product,
forced to fail first and then deployed (`scripts/verify/deploy_rehearsal.py`, section 7b), so the next
change to either path fails here before it reaches the live stack. One recorded run of 2026-11-27 printed
`PASS rehearsal 848s` after that section was added.

It missed two more on the live stack the same day, after that deploy:

1. **The publish guard on a table that builds empty.** `governance/quality-results` built its table
   with no rows, and the wrapper refused a clean build: dbt-trino builds a contract-enforced table as a
   `CREATE OR REPLACE` and then an `INSERT`, reports only the `INSERT`, and an insert of no rows commits
   nothing, so `main` was the `CREATE`'s snapshot, a query dbt never named. The `run` stage had built
   calendars and ECB only, each with rows. The guard now knows every statement of the run by a Trino
   client tag ([ADR-062](../adr/ADR-062-dbt-builds-silver-and-gold-per-product-through-one-wrapper.md)'s
   Outcome), and `run-path.sh` claim 9 builds every dbt product of the release through the wrapper,
   `currency-master`'s bronze empty there, so a materialization shape the guard does not know fails on
   the throwaway lake first.
2. **The fetch on a connection the server closes.** `currency_master`'s fetch exited 1 with `OSError:
   [Errno 9] Bad file descriptor`: the response closed its socket as it read the body's last byte, and
   the read loop set a timeout on it. The `run` stage never runs the fetch (above: a local server would
   sit on a private address, which the fetch refuses), so no throwaway lake can meet this. What covers it
   now is `tests/test_fetch.py`, whose fake socket closes as a real one does (after the close,
   `settimeout` and `recv` raise `EBADF` while a `makefile()` reference keeps the descriptor), across
   every response shape: length with keep-alive and with close, HTTP/1.0, chunked, read to the close,
   empty, and each after a redirect that closes.

## Alternatives considered

| Alternative | Why not |
|-------------|---------|
| Unit tests and CI only | Cheap, and what runs per step, but each suite sees one piece; all three faults above passed them |
| Testing on the live stack | Finds the fault where it costs most: it can lock out readers or leave a half-deployed release |
| A permanent staging environment | Another stack to keep alive and in step with the first on a single host with a stated 25 CPU / 48 GiB ceiling ([ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md)) |
| All of it in CI on every pull request | The rehearsal alone is ten minutes of a run even in its fast form, too slow for every pull request; so the cheap half is there and this is the rest |
| A container test framework | One more dependency to pin and learn over what a short shell script and the suites' own named rows already give |

## Known limits

- It takes about 18 minutes (a full run, 1085 s on 2026-11-27), and the rehearsal is most of it.
- Nothing runs it automatically: a person or the build orchestrator types it.
- It does not cover a fresh install from an empty host (that is the
  `/release-check` skill's fresh-clone gate), and it deploys nothing real: the release's images are
  placeholders (the `run` stage runs the local images it builds or the compose file pins instead).

Revisit when: the rehearsal alone passes 30 minutes (split or trim it), a second engineer joins
(a required pre-merge check, then), or a nightly runner exists on the host (run it there
instead of by hand).

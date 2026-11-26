# Runbook: Verify access after a release

Use this to prove that a release's access rules do what the product owners promised, before the
release serves anyone and on the running stack after it does, and to act when the proof fails.
`make access-verify` runs every hand-written row of
[`tests/access/expectations.yaml`](../../tests/access/expectations.yaml) as the principal the row
names. The catalog drift check and the tested-data marker check prove that readers reach only
tested data ([ADR-061](../adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md)).
How a row is written, and why the rows are never generated, is in
[`tests/access/README.md`](../../tests/access/README.md). The rules themselves are
[ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md).
Not covered: deploying or rolling back a release (step S19's `make deploy`, `make release-verify`,
`make rollback`), and the network fence probes (step S22's `scripts/verify/catalog-fence.sh`).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Target state: where these commands ran.** On 2026-11-26, on throwaway lakes
> (`scripts/lib/throwaway-lake.sh`: Trino 484, Lakekeeper, Postgres and MinIO at the stack's pins,
> HTTPS, a generated password per run), started from a release compiled from this repository's
> documents at commit `eed362d`. The live Trino does not use HTTPS or passwords yet: step S22
> turns them on and mounts `${K2_SECRETS}`. Until then, `make access-verify LIVE=1` exits 2
> (section 2), and its choices were run only on a throwaway lake standing in for the stack
> (section 6, the `live` step).

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Verify a release before it serves anyone | <2 min | 27 s, 63 of 63 rows PASS (2026-11-26, throwaway lake) |
| 2 | Verify the running stack | <2 min | not yet run on the stack (S22); exits 2 today |
| 3 | A row fails | <10 min to a verdict | the negative control: one loosened rule, live after 0.1 s, failed exactly its role's row (2026-11-26) |
| 4 | The drift check names a difference | <10 min to a verdict | 6 plants each named, each clean once undone (2026-11-26) |
| 5 | The tested-data marker fails | <10 min to a verdict | a wider view, an aliased mask and a base turned view each failed it (2026-11-26) |
| 6 | Prove the checks can fail | <5 min | 1 min 58 s for every proof (2026-11-26) |

---

## 1. Verify a release before it serves anyone

**Symptom**, none: run this on every release, before or as deploy moves the pointers to it.
Step S19's deploy runs `make access-verify` and `scripts/verify/catalog_drift.py`, both with the
exit codes below.

**Detection**, manual only: no alert covers this. The exit code is the verdict: 0 every row passed
or was skipped with a reason, 1 a row failed, 2 a usage error, or Trino or the lake could not be
reached.

**Expected behaviour**, the target starts a throwaway lake from the release's own compiled Trino
configuration, rules and groups. It gives every principal of the release a generated password. It
stages the release's products as their build would: seeds and models built by the builder, the
`audited` tag moved, the view created by the publisher. Then one untested write follows, which no
reader may see. Every row runs, and the lake is removed. Rows run in file order. The
planted-view row plants its view through the catalog API and removes it after.

**Recovery**

```bash
make access-verify RELEASE=$HOME/k2-releases/<release id>
# throwaway-lake: up in 7 s: https://trino:8443 and http://lakekeeper:8181 inside access-verify-net; ...
# stage: <release id>: 1 output(s) built, tagged and viewed, each with one untested write after its tag; 1 platform table(s)
# PASS 01 p.canary-full reads governance.access_canary.events: columns ['event_ts', 'id', 'note', 'region']; ...
# ...
# access-verify: 63 rows: 63 PASS, 0 FAIL, 0 SKIP; exit 0
# access-verify: result /home/<you>/k2-releases/verify/<release id>.access-release.json
```

The JSON result is `{release, setSha256, scope, trino, at, exit, counts, rows}`, with each row as
written and its verdict and reason. `OUT=<file>` writes it elsewhere, and `PREFIX=<name>` names the
lake (default `access-verify`). The target needs 6 GiB of memory free.

**Measured**, 27 s on 2026-11-26, 63 of 63 rows PASS. A run near the canary's limits fails rather
than passes: its delayed rows need a build less than 19 minutes old, and the lake is built at
the start of the run.

---

## 2. Verify the running stack

**Symptom**, none: run after a deploy, and whenever the access rules or the catalog are in doubt.

**Detection**, manual only, as in section 1.

**Expected behaviour**, the target reads the release Trino runs (`$K2_RELEASES/pointers/trino`)
and logs in to `https://${K2_TRINO_HOST:-localhost}:${K2_TRINO_PORT:-8443}` with
`${K2_SECRETS}/tls/ca.pem`. Each principal uses its own `${K2_SECRETS}/principals/<id>.password`.
No row impersonates anyone. Only rows whose principal's password the platform holds run: the
services (`svc.*`, `dbt_*`, `pub_*`) and the access canary's readers. They run only when the
row names nothing but the canary, so a loosened rule can damage nothing real. Every other row
prints a SKIP naming the release-time run (section 1) where it did run. So do the
planted-view row (it would write the live catalog) and the drift row: the catalog answers only
from inside `k2-catalog`, so run section 4 there. A principal that should run with no password
file fails its rows.

**Recovery**

```bash
make access-verify LIVE=1
# today, before step S22 and the first deploy:
# access-verify: no release at /home/<you>/k2-releases/pointers/trino (deploy points pointers/trino at one)
# make: *** [mk/access.mk:23: access-verify] Error 2
```

The canary's readers are people (`p.canary-*`). `make password TO_FILE=1` writes a password file
for a platform service only ([create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md)),
so until that changes their live rows fail with `no password for p.canary-...`.

**Measured**, not yet run on the stack. On a throwaway lake with `${K2_SECRETS}` laid out from
its passwords: exit 0, the planted-view row (51) and the drift row (63) skipped and nothing else.
With one canary reader's file gone, exactly that reader's six rows failed (2026-11-26).

---

## 3. A row fails

**Symptom**, a `FAIL <n>` line and exit 1. Each line names the row, the principal and what the
row asserts, then what Trino did instead:

```text
FAIL 02 p.canary-delayed reads governance.access_canary.events: 5 row(s) show masked note; 2 row(s) younger than 20 minutes by event_ts; 2 row(s) match mustNotAppear (id NOT IN (1, 2, 4))
FAIL 10 p.canary-delayed is denied governance.access_canary__base.events$snapshots: expected an access denial, got TABLE_NOT_FOUND: line 1:22: Table 'governance.access_canary__base."events$snapshots"' does not exist
FAIL 03 p.canary-scoped reads governance.access_canary.events: no password for p.canary-scoped: no /work/live-secrets/principals/p.canary-scoped.password
```

**Detection**, manual only.

**Expected behaviour**, nothing heals itself: a failed row means the release does not keep a
promise a person wrote down. Read the reason:

| The reason says | What it means |
|---|---|
| rows show a masked column, rows younger than the delay, rows match `mustNotAppear` | the reader saw more than its policy allows: the rule is wrong, or the view serves more than the tag (the first line above is one loosened rule, induced) |
| `0 row(s) match mustAppear` | the reader saw too little: no tested build, a canary older than its schedule, or a view over something else |
| `expected an access denial, got TABLE_NOT_FOUND` (or `SCHEMA_NOT_FOUND`, `COLUMN_NOT_FOUND`, `SYNTAX_ERROR`, `NOT_SUPPORTED`) | the row names something the lake does not hold, so it proves nothing. The second line above came after a base was replaced by a view (section 5) |
| `names something that does not exist` | the row names a principal or relation the running release lacks: the row or the release is wrong |
| `coverage (access.expectation-...)` | a product, role, base or seed has no row; write it ([`tests/access/README.md`](../../tests/access/README.md)) |
| `no password for` | live only: the principal's password file is missing (section 2) |

**Recovery**

1. If the release is not live yet, do not deploy it. The fix is a pull request to the documents
   (the policy, the grant) or to the row, reviewed by the product's owner.
2. If it is live and a reader sees more than the row allows, roll back to the previous release:
   `make rollback` (step S19, built in parallel with this runbook and not run here). Then run
   `make access-verify LIVE=1` again.
3. To reproduce a row, bring up and stage a throwaway lake as section 4, step 1 does (it sets
   `RELEASE` and `ID`), then run the suite on it:

```bash
bash scripts/lib/throwaway-lake.sh py av-debug scripts/access_verify.py run --scope release --release "/work/releases/$ID" --env /work/env.json
# access-verify: 63 rows: 63 PASS, 0 FAIL, 0 SKIP; exit 0
```

**Measured**, the negative control (section 6) loosened the delayed reader's rule in the running
copy. It was live after 0.1 s, and access-verify failed row 2 and no other (2026-11-26).

---

## 4. The drift check names a difference

**Symptom**, `scripts/verify/catalog_drift.py` exits 1 with one `DRIFT` line per difference, or
access-verify's drift row fails with the same lines.

**Detection**, manual only. Step S19's `release-verify` runs the check, and the dbt wrapper's
reconcile will run it from phase 002.

**Expected behaviour**, the catalog must hold exactly what the release generates. That means the
storage plan's namespaces, `retention.json`'s tables and the seeds, as Iceberg tables. It also
means each view of `views.json`, with one `trino` representation and SQL equal by token to what
the lake emitter generates over the tagged columns. Each view's owner must be the product's
publisher, and its base's `audited` tag must be a tag on a snapshot that was once the table's
main and whose files are its own. A product that has not published yet prints `PENDING`, not
drift. Each tag prints a `SKIP` for the run-record comparison until phase 002 writes run records.

| Line | Meaning |
|---|---|
| `DRIFT view <w>.<ns>.<v>: the release declares no such view (it claims owner ...)` | a view planted by the catalog API; its owner property can name anyone |
| `DRIFT table <w>.<ns>.<t>: in a reader namespace, which holds views only` | a table planted where readers look |
| `DRIFT view <relation>: SQL '...', the release says '...'` | the view was replaced: wider (over `main`), a column aliased past its mask, or edited |
| `DRIFT view <relation>: trino.run-as-owner ...` | the owner was changed, or the view is an invoker view |
| `DRIFT tag <w>.<ns>.<t>: 'audited' names snapshot ..., never this table's main` and `... manifest list is outside the table's location` | the tag was moved or a snapshot grafted from another table |
| `DRIFT base <w>.<ns>.<t>: a VIEW where the release's TABLE must be` | the builder dropped its base and created a view at its name; readers are served the view's rows |
| `DRIFT namespace ...`, `... missing` | the storage plan was not applied, or something was dropped |

**Recovery**

1. Keep the evidence. The `--out` file is it:

```bash
RELEASE=$HOME/k2-releases/<release id>; ID=$(basename "$RELEASE")
bash scripts/lib/throwaway-lake.sh up av-debug "$RELEASE"
bash scripts/lib/throwaway-lake.sh py av-debug scripts/access_verify.py stage --release "/work/releases/$ID" --env /work/env.json
bash scripts/lib/throwaway-lake.sh py av-debug scripts/verify/catalog_drift.py --release "/work/releases/$ID" --out /work/drift.json
# SKIP tag governance.access_canary__base.events audited = <id> against its newest audited run record: run records are written from phase 002
# catalog_drift: <release id>: clean, the catalog equals the release; 0 pending, 1 skipped
```

   This is the throwaway form, with the result in `~/.k2-throwaway/av-debug/drift.json`. On the
   stack the same script runs in the `tools` one-shot, the one place inside `k2-catalog`, as
   `scripts/verify/catalog_drift.py --release /k2/releases/pointers/trino --out <file>`. That is
   step S19's wiring, not run here.
2. A planted object, a moved or grafted tag, or a base turned view means something wrote to the
   catalog or to a base that should not have. Escalate it as a security incident to the platform
   owner, with the JSON, before changing anything.
3. Roll back to the previous release with `make rollback` (step S19), which does not repair the
   catalog. A view or tag stays wrong until its publisher recreates it from the release, which is
   the wrapper's reconcile (phase 002). Then run the drift check again.
4. Remove the throwaway lake: `bash scripts/lib/throwaway-lake.sh down av-debug`.

**Measured**, on 2026-11-26 the check named each plant of section 6 and was clean once each was
undone. The grafted tag drew two lines, the base turned view two (the base, and the view with no
tag to read).

---

## 5. The tested-data marker fails

**Symptom**, `scripts/verify/tested-data-marker.sh <prefix>` exits 1 with `FAIL` lines.

**Detection**, manual only.

**Expected behaviour**, for each dbt output, the base is an Iceberg table carrying `audited`, and
the stored view equals the generated one by token. The view is a definer view owned by the
publisher. With an untagged commit planted, the view serves exactly the tagged rows, and each
grantee reads tagged keys only. Unless `--no-schema-change` is given, one contract column is
dropped and added back, each published by the lake emitter's `view_update`, while a reader polls.
Every poll must answer with the same rows. That step empties the column, so it runs last, on a
throwaway lake only.

**Recovery**

```bash
bash scripts/verify/tested-data-marker.sh av-debug --no-schema-change
# PASS governance.access_canary.events base is a table: governance.access_canary__base.events is an Iceberg table
# PASS governance.access_canary.events tag: audited = <id>, once main, its own files
# ...
# SKIP schema change: --no-schema-change
# tested-data-marker: PASS
```

`base is a table: ... is a VIEW` means readers are served whatever that view returns: act as
section 4, step 2. `SQL` or `serves the tag` means the view was replaced: section 4, steps 2
and 3.

**Measured**, on 2026-11-26 a view over `main` failed the SQL, served-rows and grantee checks.
An aliased mask failed the SQL check. A base turned view failed `base is a table`. The schema
change step passed, with 25 polls answered and none in error.

---

## 6. Prove the checks can fail

**Symptom**, none: run after a change to these checks, the rules emitter or the expectations,
before trusting a PASS.

**Detection**, manual only.

**Expected behaviour**, `make test-access` stages a throwaway lake and runs
[`tests/access/prove.sh`](../../tests/access/prove.sh). First the suite passes. Then one loosened
rule fails exactly its row. The live scope's choices hold. The drift check names a view with a
forged owner, a grafted tag, a planted table, a wider view and an aliased mask, and is clean once
each is undone; the last two also fail the marker. The marker passes with its schema change. A
rules-only release takes effect without a restart. Each principal lands in its resource group.
Last, the base is replaced by a view of constants, and the drift check, the marker and
access-verify all fail.

**Recovery**

```bash
make test-access RELEASE=$HOME/k2-releases/<release id>
# prove: ok: 'py tests/access/prove.py negative' exited 0, as expected
# ...
# grant-refresh: grant removed, refused after (s, n=5): 5.1, 5.0, 5.1, 5.0, 5.1; median 5.1, max 5.1
# ...
# prove: PASS (<release id>)
```

A `prove: FAIL` line names the step and what it printed. The checks are not trustworthy until it
passes again. `K2_ACCESS_TEST_PREFIX` names the lake (default `access-test`).

**Measured**, 1 min 58 s on 2026-11-26. A removed grant was refused after 5.0 to 5.1 s and a
restored one answered after 5.0 to 5.1 s (n=5 each, Trino's refresh period 5 s, polled every
0.1 s), with no restart. Eleven principals landed in the groups interface A8 states.

---

## Failure modes / incidents

- **2026-11-26**, a chmod from the tools container on a release file the host copied into a
  read-only directory was refused by Docker Desktop's file sharing. `prove.sh` makes the running
  copy of `rules.json` writable from the host before the negative control writes it in place.

---

**Last verified:** 2026-11-26 against `eed362d`, on throwaway lakes only (the stack has no HTTPS
or passwords before step S22).

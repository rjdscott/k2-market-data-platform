# Runbook: Create or rotate a principal's password or API key

Use this to give a declared principal a Trino password or a k2-api key, to rotate one that is
lost, leaked or due, to remove the credentials of one no longer declared (section 5), and to see
who has which. Declaring a principal is a pull request to
[`contracts/access/principals.yaml`](../../contracts/access/principals.yaml), and its role on a
product a pull request to that product's `grants.yaml`; neither file ever holds a credential or
a hash of one. Not covered: the release canary `k2_release_<setSha256[:12]>`, whose line deploy
writes (phase 001 step S19); the TLS files (`make tls`, step S13). The
layout of `${K2_SECRETS}` is the secrets table of
[`docs/development/phase-001-interfaces.md`](../development/phase-001-interfaces.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**
`K2_SECRETS` defaults to `$HOME/k2-secrets`; it must be outside every git checkout (the script
refuses one inside) and under a path Docker Desktop shares with its VM, which `$HOME` is and
`/tmp` is not (`docker run` answered `mounts denied: The path /tmp/... is not shared from the
host` on 2026-11-26).

> **Target state: where these commands ran.** On 2026-11-26, with `K2_SECRETS` set to a
> temporary directory under `$HOME`, against a throwaway `trinodb/trino:484` named `s14-trino`
> (1 CPU, 1.5 GiB, HTTPS on 8443 with a throwaway certificate, the file password authenticator
> reading `/run/k2-secrets/trino/password.db` through a read-only mount of `${K2_SECRETS}/trino/`,
> refresh 5 s). The live Trino does not read `${K2_SECRETS}` yet: step S22 mounts it and turns
> on HTTPS. Until then, every `s14-trino` below is the container you log in to, and the live one
> will be `k2-trino`. k2-api, which reads `api/keys.json`, is phase 003's.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Give a person or a consumer service a password or an API key | <2 min | 0.18 s a run; the throwaway Trino took the new line at its next 5 s refresh (2026-11-26) |
| 2 | Give a platform service its password file | <2 min | 0.18 s a run (2026-11-26) |
| 3 | Rotate a password or key | <2 min | old password refused and new accepted 0.5 s after the rotation returned (2026-11-26, throwaway) |
| 4 | A login fails though the hash is there | <5 min | a file renamed over `password.db` was never seen in 12 s; the next in-place write was seen at once (2026-11-26, throwaway) |
| 5 | Remove a principal | <2 min | 0.19 s a run (2026-11-26, temporary secrets, no Trino); Trino drops the login at its next 5 s refresh (section 1) |

---

## What each command writes

| Command | Printed once on the terminal | Written under `${K2_SECRETS}` |
|---|---|---|
| `make password PRINCIPAL=<id>` | the password | the line `<id>:<bcrypt>` in `trino/password.db` |
| `make password PRINCIPAL=<id> TO_FILE=1` (service principals: `svc.*`, `dbt_*`, `pub_*`) | nothing | that line, and the password in `principals/<id>.password` |
| `make api-key PRINCIPAL=<id>` | the key, `k2_` and 43 characters | `"<sha256 of the whole key>": "<id>"` in `api/keys.json` |
| `make principal-remove PRINCIPAL=<id> [FORCE=1]` | the names of what it removed | `<id>`'s lines of `trino/password.db` and entries of `api/keys.json` removed, `principals/<id>.password` emptied and deleted |
| `make principals-status` | names and yes or no, and a `STALE` line for each unknown principal still holding one | nothing |

The password is 32 random bytes (43 characters); its bcrypt has cost 10 and the `$2y$` prefix
Trino's file authenticator requires. Every file is mode 600 in a directory of mode 700, and is
rewritten in place under a lock, so concurrent runs cannot lose a line and a running container
sees the change (section 4 says why that matters). A principal that already has a password or
key keeps it unless `ROTATE=1` is given. Without `TO_FILE=1` the command refuses to run unless
its output is a terminal, so a secret is never captured by a pipe or a log; no secret is ever
taken from the command line. An API key exists only for a principal k2-api may act as, which is
every declared principal but `dbt_*`, `pub_*`, `svc.runs`, `svc.platform`, `svc.catalog` and
`svc.api` (`compiled/api/principals.json`).

---

## 1. Give a person or a consumer service a password or an API key

**Symptom**, a principal is declared in `contracts/access/principals.yaml` (merged) and has no
password or key yet; `make principals-status` shows `no` in its column.

**Detection**, manual only: nothing alerts on a missing credential.

**Expected behaviour**, the secret is printed once and nowhere else. Trino rereads
`password.db` every 5 seconds, so the login works within 5 seconds with no restart.

**Recovery**

```bash
make principals-status
# principal                    password  file  api-key
# dbt_access_canary            no        no    -
# p.canary-delayed             no        -     no
# ...

make password PRINCIPAL=p.canary-full
# <the password, 43 characters>
# principal-secret: that password for p.canary-full is shown this once; store it now

make api-key PRINCIPAL=p.canary-full
# <the key, k2_ and 43 characters>
# principal-secret: that API key for p.canary-full is shown this once; store it now
```

Store each in the principal's password manager at once: nothing can show it again, and a lost
one is a rotation (section 3). Check the login, typing the password at the hidden prompt:

```bash
read -rs TRINO_PASSWORD && export TRINO_PASSWORD
docker exec -e TRINO_PASSWORD s14-trino trino --server https://localhost:8443 --insecure \
  --user p.canary-full --password --execute 'SELECT current_user'
# "p.canary-full"
unset TRINO_PASSWORD
```

A wrong password prints `Authentication failed: Access Denied: Invalid credentials`.

**Measured**, 0.18 s for one run (`time make -s password PRINCIPAL=svc.runs TO_FILE=1`, a warm
uv cache), 2026-11-26. Each of `svc.runs`, `svc.api`, `dbt_access_canary`, `pub_access_canary`
and `p.canary-full` logged in to the throwaway Trino with its new password; a wrong password, an
empty one, another principal's, and a principal with no line were each answered `401`.

---

## 2. Give a platform service its password file

**Symptom**, a platform container (dbt-runner, k2-api, tools) needs to log in as `svc.runs`,
`svc.catalog`, `svc.platform`, `svc.api`, a product's builder `dbt_<fence>` or its publisher
`pub_<fence>`; `make principals-status` shows `no` under `file`.

**Detection**, manual only.

**Expected behaviour**, the password goes to `${K2_SECRETS}/principals/<id>.password` and is
printed nowhere; the container finds it read-only at `/run/k2-secrets/<role>.password`
(`builder`, `publisher`, or the name after `svc.`), as the secrets table says.

**Recovery**

```bash
make password PRINCIPAL=svc.runs TO_FILE=1
# principal-secret: svc.runs: bcrypt line in .../trino/password.db, password in .../principals/svc.runs.password (mode 600)

TRINO_PASSWORD="$(cat "$K2_SECRETS/principals/svc.runs.password")" docker exec -e TRINO_PASSWORD s14-trino \
  trino --server https://localhost:8443 --insecure --user svc.runs --password --execute 'SELECT current_user'
# "svc.runs"
```

A consumer service outside the platform (say a dashboard, `svc.<name>`) takes section 1's form:
its owner stores the printed password in their own secret store.

**Measured**, 2026-11-26: four service passwords made at once, by four concurrent runs, gave
four well-formed lines, each the hash of the password its file holds.

---

## 3. Rotate a password or key

**Symptom**, a secret is lost, may have leaked, or is due.

**Detection**, manual only.

**Expected behaviour**, the principal's one line (or key) is replaced in place; every other
line, the release canary's included, stays byte for byte where it was. The old password stops
working at Trino's next refresh, within 5 seconds. A service with a password file must be
rotated with `TO_FILE=1`: the command refuses otherwise, since its containers would keep the old
password.

**Recovery**

```bash
make password PRINCIPAL=svc.runs TO_FILE=1
# principal-secret: svc.runs already has a password; ROTATE=1 replaces it
make password PRINCIPAL=svc.runs TO_FILE=1 ROTATE=1

make password PRINCIPAL=p.canary-full ROTATE=1
make api-key PRINCIPAL=p.canary-full ROTATE=1
```

Check the login as in section 1 or 2. A one-shot container (dbt-runner) reads its password file
at each run; a long-running reader of one is phase 003's k2-api, whose restart rule lands with it.

**Measured**, 2026-11-26, on the throwaway Trino: 0.5 s after `make password PRINCIPAL=svc.runs
TO_FILE=1 ROTATE=1` returned, the old password was answered `401` and the new one `200`;
`password.db` kept its inode and its users in order, one line changed, and the other principals
still logged in. An API key rotation left `api/keys.json` holding the new key's sha256 alone.

---

## 4. A login fails though the hash is there

**Symptom**, `make principals-status` shows `yes` for the principal, the password is right,
and Trino answers `Invalid credentials`; or `docker exec s14-trino cat
/run/k2-secrets/trino/password.db | cut -d: -f1` lists fewer users than
`cut -d: -f1 "$K2_SECRETS/trino/password.db"` on the host.

**Detection**, manual only.

**Expected behaviour**, this happens when `password.db` was replaced by a rename (`sed -i`, an
editor's save, `mv`, `cp` to a temporary name and back) instead of written in place: through
Docker Desktop's file sharing a container keeps reading the old file after a rename, even under
a directory mount. `scripts/principal-secret.py` never renames. Do not edit the file by hand.

**Recovery**, any in-place write makes the container see the current file. Give the affected
principal its password again, which is one:

```bash
make password PRINCIPAL=svc.catalog TO_FILE=1 ROTATE=1
docker exec s14-trino cat /run/k2-secrets/trino/password.db | cut -d: -f1
```

**Measured**, 2026-11-26, on the throwaway Trino: after a file holding a sixth line was renamed
over `password.db`, the container still read five lines and refused the new principal for 12 s
(the test stopped there). The next `make password` wrote in place, and both the renamed-in
principal and the new one logged in.

---

## 5. Remove a principal

**Symptom**, a principal was taken out of `contracts/access/principals.yaml`, or the product
whose builder `dbt_<fence>` or publisher `pub_<fence>` it was is gone, and `make
principals-status` prints a `STALE` line for it: its `password.db` line still logs in to Trino,
and a stale `dbt_*` or `pub_*` line still matches the platform's pattern rules (`dbt_.*` and
`pub_.*` in `model.PIPELINE_PRINCIPALS`), which carry writer rights. Or a declared principal's
secret must be revoked before the pull request that undeclares it merges.

**Detection**, manual only: run `make principals-status` after any pull request that removes a
principal or a product.

**Expected behaviour**, `make principal-remove` drops the principal's lines of `password.db`,
its entries of `api/keys.json` and its `principals/<id>.password`, each rewritten in place under
its lock as section 4 requires (the password file is emptied in place, then deleted, so a
container's stale mount of it reads nothing). Every other line, the release canary's included,
stays byte for byte. It prints the names of what it removed and exits 0; with nothing there it
says so and exits 0 too, since the state asked for already holds and a rerun is harmless. It
refuses a principal still known (declared, or a dbt or code product's builder or publisher)
unless `FORCE=1`, and always refuses the release canary, whose line deploy owns. A refusal also
prints make's `Error 1` line and exits 2. Trino drops the login at its next refresh, within 5
seconds (section 1's measured refresh, not re-measured here); a principal with no line is
answered `401` (section 1).

**Recovery**

```bash
make principals-status
# principal                    password  file  api-key
# ...
# STALE dbt_gone in trino/password.db, principals/dbt_gone.password: neither declared nor a dbt or code product's builder or publisher, yet Trino still logs it in and the platform's pattern rules still match it; make principal-remove PRINCIPAL=dbt_gone

make principal-remove PRINCIPAL=dbt_gone
# principal-secret: dbt_gone: removed 1 line(s) of trino/password.db, principals/dbt_gone.password under ...
make principal-remove PRINCIPAL=dbt_gone
# principal-secret: dbt_gone: nothing to remove under ...

make principal-remove PRINCIPAL=svc.api
# principal-secret: svc.api is still known (declared in contracts/access/principals.yaml, or a dbt or code product's builder or publisher); undeclare it first, or FORCE=1 removes its credentials anyway
make principal-remove PRINCIPAL=svc.api FORCE=1
# principal-secret: svc.api: removed 1 line(s) of trino/password.db, principals/svc.api.password under ...

make principal-remove PRINCIPAL=k2_release_0123456789ab FORCE=1
# principal-secret: k2_release_0123456789ab: a release canary; deploy owns its line (S19)
```

A principal removed with `FORCE=1` is still declared: `make principals-status` shows it with
`no`, and `make password` would give it a new secret. Undeclare it in the pull request that
follows.

**Measured**, 2026-11-26, against temporary secrets and no Trino: `svc.runs` and `svc.api` given
passwords with `TO_FILE=1`, then a `dbt_gone` line and password file planted by hand; the
commands above printed what is shown. One run took 0.19 s (`time make principal-remove
PRINCIPAL=dbt_gone`); `password.db` kept its inode and the other users in order.

---

## Failure modes / incidents

None yet.

---

**Last verified:** 2026-11-26 against `step/s14-principals-keys` on `phase/001-release-and-rules-spine`,
with temporary secrets and the throwaway Trino 484 `s14-trino`; not yet against the live Trino (step S22).
Section 5 on 2026-11-26 against `step/s00d-interface-patch`, with temporary secrets and no Trino.

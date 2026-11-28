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

> **Target state: where these commands ran.** On 2026-11-26 against a throwaway Trino 484 with
> a temporary `K2_SECRETS` under `$HOME`, and again later that day (after the identity review) against a throwaway lake of
> `scripts/lib/throwaway-lake.sh` (Trino 484 started from a compiled release, HTTPS only, on its
> own network), with `NET=<prefix>-net`, `TRINO=<prefix>-trino` and `K2_SECRETS` the lake's own
> `<state>/secrets/k2`. The live Trino does not read `${K2_SECRETS}` yet: step S22 mounts it and
> turns on HTTPS; from then the live values are `NET=k2-net` and `TRINO=k2-trino`. k2-api, which
> reads `api/keys.json`, is phase 003's.

Every login check below runs Trino's own CLI in a one-off container on Trino's network, which
reaches it as `trino`, by name, and verifies it against `ca.pem`; never by address and never with
`--insecure`. Define the two helpers once per shell:

```bash
NET=k2-net TRINO=k2-trino
# A throwaway lake instead: P=<prefix>; NET=$P-net TRINO=$P-trino K2_SECRETS=$(bash scripts/lib/throwaway-lake.sh state "$P")/secrets/k2
login_typed() {  # login_typed <principal>: SELECT current_user as it, its password typed at a hidden prompt
  local TRINO_PASSWORD; printf 'password for %s: ' "$1" >&2; read -rs TRINO_PASSWORD; echo >&2; export TRINO_PASSWORD
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e TRINO_PASSWORD \
    -v "$K2_SECRETS/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" --entrypoint trino trinodb/trino:484 \
    --server https://trino:8443 --truststore-path /run/k2-secrets/tls/ca.pem --user "$1" --password --execute 'SELECT current_user'
}
login_file() {  # login_file <principal>: the same, with its principals/<id>.password, read inside the container
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e K2_USER="$1" \
    -v "$K2_SECRETS/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
    -v "$K2_SECRETS/principals/$1.password:/run/k2-secrets/login.password:ro" --entrypoint bash trinodb/trino:484 \
    -c 'TRINO_PASSWORD=$(cat /run/k2-secrets/login.password) exec trino --server https://trino:8443 \
          --truststore-path /run/k2-secrets/tls/ca.pem --user "$K2_USER" --password --execute "SELECT current_user"'
}
```

A password is never on a command line: `-e TRINO_PASSWORD` passes the variable by name, and the
file is read inside the container.

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
| `make password PRINCIPAL=<id> TO_FILE=1` (service principals: `svc.*`, `dbt_*`, `pub_*`; and test principals, below) | nothing | that line, and the password in `principals/<id>.password` |
| `make api-key PRINCIPAL=<id>` | the key, `k2_` and 43 characters | `"<sha256 of the whole key>": "<id>"` in `api/keys.json`; with `ROTATE=1`, an `api-keys/<id>.key` that held the old key deleted |
| `make api-key PRINCIPAL=<id> TO_FILE=1` (a reader whose REST rows the access suite runs) | nothing | that entry, and the key in `api-keys/<id>.key`, never in `api/`, which k2-api mounts |
| `make principal-remove PRINCIPAL=<id> [FORCE=1]` | the names of what it removed | `<id>`'s lines of `trino/password.db` and entries of `api/keys.json` removed, `principals/<id>.password` and `api-keys/<id>.key` emptied and deleted |
| `make principals-status` | names and yes or no (`stale` for a key file whose sha256 is not its principal's in `api/keys.json`), a `STALE` line for each such key file and each unknown principal still holding one, a `MALFORMED` line for each line of `password.db` Trino would refuse; exit 1 on any | nothing (it puts the directories back to 700) |

The password is 32 random bytes (43 characters); its bcrypt has cost 10 and the `$2y$` prefix
Trino's file authenticator requires. Every file is mode 600 in a directory of mode 700 (every run
puts `${K2_SECRETS}`, `trino/`, `api/`, `api-keys/` and `principals/` back to 700), and is rewritten in place
under a lock, so concurrent runs cannot lose a line and a running container sees the change
(section 4 says why that matters). `password.db` is a file of fixed 128-byte lines: a rotation
rewrites its own line where it is, a removal blanks it with spaces (Trino skips a blank line),
and a new principal takes a blank line or one more at the end, so no other line ever moves under
Trino's reread. A change that would leave a line Trino refuses (a malformed hash, a cost below
10, a user twice), or that finds one already there, is refused before anything is written,
naming the line: one such line makes Trino answer every login with HTTP 500.

A **test principal** is a person granted a role on the access canary
(`contracts/governance/access-canary/grants.yaml`) and on no other product: `p.canary-full`,
`p.canary-delayed` and `p.canary-scoped` today. Its password is the platform's, not a person's,
so `TO_FILE=1` writes it to `principals/<id>.password` for `make access-verify LIVE=1` to log in
with (`access_verify.platform_held`, the same rule the live suite runs by). A person with a grant
on any other product is refused `TO_FILE=1`: their password is printed once and is theirs. A principal that already has a password or
key keeps it unless `ROTATE=1` is given. Without `TO_FILE=1` the command refuses to run unless
its output is a terminal, so a secret is never captured by a pipe or a log (a redirect to a file
is refused too: use `TO_FILE=1`); no secret is ever taken from the command line. An API key
exists only for a principal k2-api may act as, which is every declared principal but `dbt_*`,
`pub_*`, `svc.runs`, `svc.platform`, `svc.catalog` and `svc.api` (`compiled/api/principals.json`).
`make api-key PRINCIPAL=<id> TO_FILE=1` writes a reader's key to `api-keys/<id>.key`, mode 600,
instead of printing it: the operator's copy for the access suite's REST rows (`make access-verify
LIVE=1`). The directory is `api-keys/`, never `api/`: k2-api mounts `api/` whole for `keys.json`
and must only ever hold hashes, so no compose service mounts `api-keys/`; deploy mounts each key
file by name, read-only, into the live suite's one-shot alone.

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
# principal                    password  file  api-key  key-file
# dbt_access_canary            no        no    -        -
# p.analyst-a                  no        -     no       no
# p.canary-delayed             no        no    no       no
# ...

make password PRINCIPAL=p.canary-full
# <the password, 43 characters>
# principal-secret: that password for p.canary-full is shown this once; store it now

make api-key PRINCIPAL=p.canary-full
# <the key, k2_ and 43 characters>
# principal-secret: that API key for p.canary-full is shown this once; store it now

# or a reader's key the access suite reads, written to a file and printed nowhere:
make api-key PRINCIPAL=p.canary-full TO_FILE=1
# principal-secret: p.canary-full: sha256 in <K2_SECRETS>/api/keys.json, key in <K2_SECRETS>/api-keys/p.canary-full.key (mode 600)
```

Store each in the principal's password manager at once: nothing can show it again, and a lost
one is a rotation (section 3). Check the login, typing the password at the hidden prompt:

```bash
login_typed p.canary-full
# password for p.canary-full:
# "p.canary-full"
```

A wrong password prints `Authentication failed: Access Denied: Invalid credentials`.

**Measured**, 0.18 s for one run (`time make -s password PRINCIPAL=svc.runs TO_FILE=1`, a warm
uv cache), 2026-11-26. Each of `svc.runs`, `svc.api`, `dbt_access_canary`, `pub_access_canary`
and `p.canary-full` logged in to the throwaway Trino with its new password; a wrong password, an
empty one, another principal's, and a principal with no line were each answered `401`. Later that
day on throwaway lake `fxid-rb`, `login_typed p.canary-delayed` (its password fed on stdin from
the lake's kept file in place of the keyboard) printed `"p.canary-delayed"`, and a wrong password
`Authentication failed: Access Denied: Invalid credentials`.

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
login_file svc.runs
# "svc.runs"

make password PRINCIPAL=p.canary-full TO_FILE=1 ROTATE=1     # a test principal (above)
login_file p.canary-full
# "p.canary-full"
```

A consumer service outside the platform (say a dashboard, `svc.<name>`) takes section 1's form:
its owner stores the printed password in their own secret store. Declared `kind: partner` (another
organisation's service, `svc.partner-dashboard` today), it is refused `TO_FILE=1`, since the platform
never holds a partner's credential.

**Measured**, 2026-11-26: four service passwords made at once, by four concurrent runs, gave
four well-formed lines, each the hash of the password its file holds. Later that day on
throwaway lake `fxid-rb`: `make -s password PRINCIPAL=svc.runs TO_FILE=1 ROTATE=1` in 0.24 s,
then `make -s password PRINCIPAL=p.canary-full TO_FILE=1 ROTATE=1`; after the 5 s refresh,
`login_file svc.runs` and `login_file p.canary-full` printed their own names.

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
# with a key file, which would no longer match keys.json:
# principal-secret: p.canary-full: removed <K2_SECRETS>/api-keys/p.canary-full.key, which held the old key; TO_FILE=1 ROTATE=1 writes a key there
make api-key PRINCIPAL=p.canary-full TO_FILE=1 ROTATE=1   # the hash and the key file both replaced
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
and Trino answers `Invalid credentials`; or `docker exec "$TRINO" cat
/run/k2-secrets/trino/password.db | cut -d: -f1` lists fewer users than
`cut -d: -f1 "$K2_SECRETS/trino/password.db"` on the host. Or every login, of every principal,
is answered HTTP 500 (the CLI: `Error starting query at https://trino:8443/v1/statement returned
an invalid response: JsonResponse{statusCode=500, ...`) and `make principals-status` prints a
`MALFORMED` line.

**Detection**, manual only.

**Expected behaviour**, this happens when `password.db` was replaced by a rename (`sed -i`, an
editor's save, `mv`, `cp` to a temporary name and back) instead of written in place: through
Docker Desktop's file sharing a container keeps reading the old file after a rename, even under
a directory mount. `scripts/principal-secret.py` never renames. Do not edit the file by hand.
Every login answered HTTP 500 is a line Trino refuses (a hand edit, or a file from elsewhere):
Trino fails the whole file, and its answer, a stack trace naming only the line number, reaches
the client and not Trino's log.

**Recovery**, any in-place write makes the container see the current file. Give the affected
principal its password again, which is one:

```bash
make password PRINCIPAL=svc.catalog TO_FILE=1 ROTATE=1
docker exec "$TRINO" cat /run/k2-secrets/trino/password.db | cut -d: -f1
```

For a `MALFORMED` line, the status line says which and what to run. A line of a named user is
removed with `make principal-remove PRINCIPAL=<that user> FORCE=1` (then give it a password
again). A line with no user (no `:`, which could be anything, a pasted password included) is
never printed; blank it in place, under the file's lock, keeping its length, with the line
number from the status line:

```bash
make principals-status
# MALFORMED trino/password.db line 7: no `:` between a user and a hash; Trino refuses every login while it stands; blank it in place (...)
python3 - "$K2_SECRETS/trino/password.db" 7 <<'PY'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_NOFOLLOW)
fcntl.flock(fd, fcntl.LOCK_EX)
lines = os.pread(fd, os.fstat(fd).st_size, 0).split(b"\n")
lines[int(sys.argv[2]) - 1] = b" " * len(lines[int(sys.argv[2]) - 1])
os.pwrite(fd, b"\n".join(lines), 0)
os.fsync(fd)
PY
make principals-status
```

**Measured**, 2026-11-26, on the throwaway Trino: after a file holding a sixth line was renamed
over `password.db`, the container still read five lines and refused the new principal for 12 s
(the test stopped there). The next `make password` wrote in place, and both the renamed-in
principal and the new one logged in. Later that day, on throwaway lake `fxid-rb`: a line with no
`:` appended in place made `login_file svc.runs` fail with the HTTP 500 above; `make
principals-status` named it (`MALFORMED trino/password.db line 11`, exit 1); the snippet above
blanked it, status exited 0, and `svc.runs` logged in again after the 5 s refresh.

The fixed lines were measured by `scripts/verify/password_db_hammer.py` (its docstring has the
command): a throwaway Trino 484 rereading `password.db` every second, logins every 50 ms across 40
users, while the file is rewritten as fast as it can be. With `--old`, the former write (a line
added and removed at the top, the whole file rewritten each time): 214,316 rewrites in 176 s;
1,618 logins with the right password, 7 of them HTTP 500, and 405 with another user's, 2 of them
HTTP 500, exit 1. With the slots (a rotation, an addition and a removal in turn): 150,703
rewrites in 176 s; 1,594 logins with the right password, all 200, and 399 with another user's,
all 401, exit 0.

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

**Expected behaviour**, `make principal-remove` blanks the principal's lines of `password.db`
where they are, drops its entries of `api/keys.json` and its `principals/<id>.password`, each
rewritten in place under its lock as section 4 requires (the password file is emptied in place,
then deleted, so a container's stale mount of it reads nothing). Every other line, the release
canary's included, stays byte for byte where it was. `make principals-status` exits 1 while any
`STALE` line remains. It prints the names of what it removed and exits 0; with nothing there it
says so and exits 0 too, since the state asked for already holds and a rerun is harmless. It
refuses a principal still known (declared, or a dbt or code product's builder or publisher)
unless `FORCE=1`, and always refuses the release canary, whose line deploy owns. A refusal also
prints make's `Error 1` line and exits 2. Trino drops the login at its next refresh, within 5
seconds (section 1's measured refresh, not re-measured here); a principal with no line is
answered `401` (section 1).

**Recovery**

```bash
make principals-status
# principal                    password  file  api-key  key-file
# ...
# STALE dbt_gone in trino/password.db, principals/dbt_gone.password: neither declared nor a dbt or code product's builder or publisher, yet Trino still logs it in and the platform's pattern rules still match it; make principal-remove PRINCIPAL=dbt_gone
# STALE p.canary-full in api-keys/p.canary-full.key: its sha256 is not p.canary-full's in api/keys.json, so k2-api refuses the key; make api-key PRINCIPAL=p.canary-full TO_FILE=1 ROTATE=1
# make: *** [mk/principals.mk:25: principals-status] Error 1

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
with temporary secrets and the throwaway Trino 484 `s14-trino`; sections 1, 2 and 4 again the same day
against `step/fix-identity-review` on throwaway lake `fxid-rb`, by name with the CA; not yet against the
live Trino (step S22).
Section 5 on 2026-11-26 against `step/s00d-interface-patch`, with temporary secrets and no Trino.

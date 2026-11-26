# Runbook: Set up and rotate Trino's TLS certificates and passwords

Use this to set up Trino's identity from nothing (or again, after `${K2_SECRETS}` is lost), to rotate a
service's password, to reissue Trino's server certificate, to replace the local certificate authority, and to
recognise an expired or wrong certificate from each client. Why it is built this way:
[ADR-060](../adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md). A person's password, an API key,
removing a principal and a login refused because `password.db` was renamed over are in
[`create-or-rotate-a-principal.md`](create-or-rotate-a-principal.md); the release canary's line is deploy's (S19).
The layout of `${K2_SECRETS}` is the secrets table of
[`docs/development/phase-001-interfaces.md`](../development/phase-001-interfaces.md#local-secrets).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** `K2_SECRETS` defaults to
`$HOME/k2-secrets`, outside every git checkout (both scripts refuse one inside) and under `$HOME`, which Docker
Desktop shares.

> **Target state: where these commands ran.** On 2026-11-26, against throwaway lakes of
> `scripts/lib/throwaway-lake.sh` (Trino 484 started from a compiled release, HTTPS only, on its own Docker
> network) with `NET=<prefix>-net` and `K2_SECRETS` the lake's own `<state>/secrets/k2`, and once on a fresh
> temporary `K2_SECRETS` (section 1). The live Trino serves plain HTTP until step S22 mounts these files and turns
> HTTPS on; from then the live values are `NET=k2-net` and the container `k2-trino`.

Every check below runs a client in a one-off container on Trino's network, which reaches it as `trino`, by name.
Define the two helpers once per shell (the Trino CLI and curl come from Trino's own image):

```bash
NET=k2-net
trino_cli() {  # trino_cli <svc.* principal> <SQL>: Trino's own CLI, by name, verified against ca.pem
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e K2_USER="$1" -e K2_SQL="$2" \
    -v "$K2_SECRETS/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
    -v "$K2_SECRETS/principals/$1.password:/run/k2-secrets/login.password:ro" --entrypoint bash trinodb/trino:484 \
    -c 'TRINO_PASSWORD=$(cat /run/k2-secrets/login.password) exec trino --server https://trino:8443 \
          --truststore-path /run/k2-secrets/tls/ca.pem --user "$K2_USER" --password --execute "$K2_SQL"'
}
served() {  # the certificate Trino shows a client that asks for `trino`, and whether ca.pem verifies it
  docker run --rm --network "$NET" -v "$K2_SECRETS/tls/ca.pem:/ca.pem:ro" --entrypoint curl trinodb/trino:484 \
    -sSv --cacert /ca.pem -o /dev/null https://trino:8443/v1/info 2>&1 | grep -E 'expire date|issuer|verify result|curl: '
}
```

The password is read inside the client's container from its file, so it is never on a command line or in the
host shell's environment. Never connect by IP address and never pass `--insecure`: Trino shows a client that
connects by address its internal certificate, which no client trusts (section 5).

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Set up from nothing: the CA, Trino's certificate, every service's password | <5 min | 0.10 s for `make tls-init`, about 0.2 s a password (2026-11-26) |
| 2 | Rotate a service's password | <2 min | 0.18 s; taken at Trino's next 5 s refresh, no restart (2026-11-26) |
| 3 | Reissue Trino's server certificate | <5 min | 43 s until Trino served it, no restart (2026-11-26) |
| 4 | Replace the CA | <5 min | 50 s of refused connections, then served, no restart (2026-11-26) |
| 5 | An expired or wrong certificate | <5 min | expired one fixed by section 3 in 34 s (2026-11-26) |

---

## 1. Set up from nothing

**Symptom**, a new host, or `${K2_SECRETS}` lost: no `tls/` or `trino/password.db` under it, so Trino has no
certificate to serve and nobody a password.

**Detection**, manual only; `make tls-status` exits 1 and says which file is missing.

**Expected behaviour**, `make tls-init` makes a CA (ECDSA P-256, 5 years, allowed to sign only `trino` and
`localhost`) and Trino's certificate (90 days, exactly those two names) under `${K2_SECRETS}/tls/`: `ca.pem`, `ca.key`
(mode 600, never mounted) and `trino.pem` (key and certificate in one file, mode 600), in a directory of mode 700. A
rerun changes nothing. `make password ... TO_FILE=1` gives a service its password file and its `password.db` line.
`${K2_SECRETS}` is in no backup, so this section is also the recovery from losing it: every client then needs the
new `ca.pem` and every principal a new password.

**Recovery**

```bash
make tls-init
# tls: CA .../tls/ca.pem (sha256 F4:B6:...), its key .../tls/ca.key (mode 600), server certificate .../tls/trino.pem (mode 600) for DNS:trino,DNS:localhost
make tls-init
# tls: the CA and the server certificate exist under .../tls; kept (FORCE=1 replaces the CA, make tls-rotate the server certificate)
make tls-status
# CA: .../tls/ca.pem
#   subject   CN=K2 local CA 2026-11-26T20:43:51Z
#   may sign  DNS:trino,DNS:localhost
#   expires   Nov 26 20:43:51 2031 GMT (1826 days left)
# server: .../tls/trino.pem
#   subject   CN=trino
#   names     DNS:trino,DNS:localhost
#   expires   Feb 24 20:43:51 2027 GMT (89 days left)
# tls: PASS (each certificate valid for more than 30 days)

for p in svc.runs svc.catalog svc.platform svc.api dbt_access_canary pub_access_canary; do
  make -s password PRINCIPAL=$p TO_FILE=1
done
# principal-secret: svc.runs: bcrypt line in .../trino/password.db, password in .../principals/svc.runs.password (mode 600)
# ...
make -s principals-status
```

Each person (`p.*`) then gets `make password PRINCIPAL=<id>` in a terminal
([`create-or-rotate-a-principal.md`](create-or-rotate-a-principal.md) section 1). Trino's internal shared secret goes
into `.env` once, never printed: `printf 'K2_TRINO_SHARED_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env`. Then
prove the whole path, row by row (18 rows: refusals, a login, both rotations, the CLI and dbt):

```bash
bash scripts/verify/identity.sh                          # the live Trino, from S22
bash scripts/verify/identity.sh --throwaway <prefix>     # a throwaway lake, as make test-trino runs it
# PASS I01.no-credential: a statement with no password, X-Trino-User svc.catalog, is refused 401
# ...
# identity: PASS
```

**Measured**, 2026-11-26 on a fresh temporary `K2_SECRETS`: `make tls-init` 0.10 s, the files and modes as above,
six service passwords in about 0.2 s each; the `.env` line on a scratch file, 64 hex characters, never printed.
`identity.sh --throwaway` on a throwaway lake: 18 of 18 PASS, 80 s.

---

## 2. Rotate a service's password

**Symptom**, a service's password may have leaked, its file was copied somewhere it should not be, or it is due.

**Detection**, manual only.

**Expected behaviour**, `ROTATE=1 TO_FILE=1` replaces the service's one line of `password.db` and its
`principals/<id>.password`, both in place; every other line is untouched. Trino rereads `password.db` every 5 s, so
for up to 5 s the old password still works and the new one is refused; then the reverse, with no restart. A
container that mounts the password file reads the new one at its next start (the one-shots read it every run).

**Recovery**

```bash
make -s password PRINCIPAL=svc.catalog TO_FILE=1 ROTATE=1
# principal-secret: svc.catalog: bcrypt line in .../trino/password.db, password in .../principals/svc.catalog.password (mode 600)
sleep 5
trino_cli svc.catalog 'SELECT current_user'
# "svc.catalog"
```

Without `ROTATE=1` the command refuses (`svc.catalog already has a password; ROTATE=1 replaces it`). Logging in
within the 5 s prints `Authentication failed: Access Denied: Invalid credentials`; wait and repeat.

**Measured**, 2026-11-26 on a throwaway lake: the rotation ran in 0.18 s; the CLI logged in with the new password 1 s
later. Logging in at once, while the CLI had used the old password under 5 s before, was refused, and accepted after
the refresh. `identity.sh` measures the same on a probe user: a rotation taken in 5.05 s, a removal refused in
4.98 s (`I11`, `I12`).

---

## 3. Reissue Trino's server certificate

**Symptom**, `make tls-status` exits 1 with `FAIL: expires within 30 days` under `server:`, or the server's key may
have leaked. The certificate lives 90 days, so this is a monthly to quarterly habit, not an event.

**Detection**, manual only until the certificate expiry alert lands (a later step): `make tls-status` exits 1 under
`MIN_DAYS` (default 30) days left.

**Expected behaviour**, `make tls-rotate` writes a new key and certificate into `trino.pem` in place, under the same
CA; `ca.pem` does not change, so no client needs anything. Trino rereads its keystore every minute
(`http-server.https.ssl-context.refresh-time`, default 1m), so within a minute it serves the new certificate, with no
restart; until then it serves the old one, which is still valid.

**Recovery**

```bash
make tls-status
docker inspect -f '{{.State.StartedAt}}' k2-trino
make -s tls-rotate
# tls: server certificate reissued in place, serial 3D42446A... -> 477AB77C..., CA unchanged
new=$(openssl x509 -in "$K2_SECRETS/tls/trino.pem" -noout -enddate | cut -d= -f2)
until served | grep -qF "expire date: $new"; do sleep 5; done
served
# *   expire date: Feb 24 20:45:00 2027 GMT
# *   issuer: CN=K2 local CA 2026-11-26T20:43:28Z
# * OpenSSL verify result: 0
docker inspect -f '{{.State.StartedAt}}' k2-trino      # unchanged
```

**Measured**, 2026-11-26 on throwaway lakes: served after 43 s, `StartedAt` unchanged; `identity.sh`'s `I17` 41.1 s
and 45.3 s, and once 54 s by hand. A file renamed over `trino.pem` (an editor's save, `mv`) is never seen by a
container that mounts it; only `make tls-rotate` and `make tls-init` write it.

---

## 4. Replace the CA

**Symptom**, `ca.key` may have leaked; `make tls-status` shows `FAIL` under `CA:`; or the CA is within a year of its
5-year expiry and you choose the hour.

**Detection**, manual only, as section 3.

**Expected behaviour**, `make tls-init FORCE=1` makes a new CA and a new server certificate under it, each written in
place. Every container that mounts `ca.pem` sees the new CA at once, while Trino keeps serving the old certificate
for up to a minute: in that minute every client is refused (`unable to get local issuer certificate`), then all
connect, with no restart. A copy of `ca.pem` outside `${K2_SECRETS}` (another host, a laptop) must be replaced by
hand. Without `FORCE=1` the command keeps the CA.

**Recovery**

```bash
make -s tls-init FORCE=1
# tls: FORCE=1: replacing the CA F2:50:...; every client needs the new ca.pem
# tls: CA .../tls/ca.pem (sha256 A1:D9:...), its key .../tls/ca.key (mode 600), server certificate .../tls/trino.pem (mode 600) for DNS:trino,DNS:localhost
served
# * SSL certificate OpenSSL verify result: unable to get local issuer certificate (20)
until served | grep -q 'verify result: 0'; do sleep 5; done
trino_cli svc.catalog 'SELECT current_user'
# "svc.catalog"
```

**Measured**, 2026-11-26 on throwaway lakes: 50 s and 59.5 s of refused connections, then verified; the CLI in the
window printed `PKIX path building failed: ... unable to find valid certification path to requested target`.

---

## 5. An expired or wrong certificate

**Symptom**, every client fails at the TLS handshake, before any password is checked:

| Client | Expired server certificate | Connected by IP address, or a name the certificate lacks |
|---|---|---|
| Trino CLI | `javax.net.ssl.SSLHandshakeException: (certificate_expired) PKIX path validation failed: java.security.cert.CertPathValidatorException: validity check failed` | `(certificate_unknown) PKIX path building failed: ... unable to find valid certification path to requested target` |
| curl | `curl: (60) SSL certificate OpenSSL verify result: certificate has expired (10)` | `curl: (60) SSL certificate OpenSSL verify result: self-signed certificate (18)` |
| Python (`ssl`, `urllib`) | `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired` | `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate` |
| dbt-trino (`dbt debug`) | `Connection test: [ERROR]`, then `SSLCertVerificationError(... certificate has expired ...)` | `Connection test: [ERROR]`, then `SSLCertVerificationError(... self-signed certificate ...)` |

A wrong password, by contrast, gets through the handshake and is answered 401: the CLI prints `Authentication
failed: Access Denied: Invalid credentials`.

**Detection**, manual only, as section 3; `make tls-status` shows `FAIL: expired`.

**Expected behaviour**, nothing recovers on its own. An expired server certificate is fixed by section 3 under the
same CA, an expired CA by section 4. A "self-signed certificate" refusal is a client connecting by address or by a
name the certificate lacks: Trino then shows its internal certificate, made from the shared secret for its own
addresses. Connect as `trino` on the network or `localhost` on the host.

**Recovery**

```bash
served
# * SSL certificate OpenSSL verify result: certificate has expired (10)
make tls-status
# server: .../tls/trino.pem
#   expires   Jan  2 00:00:00 2026 GMT (-328 days left)
#   FAIL: expired
make -s tls-rotate
until served | grep -q 'verify result: 0'; do sleep 5; done
trino_cli svc.catalog 'SELECT current_user'
# "svc.catalog"
```

**Measured**, 2026-11-26 on throwaway lakes, induced by writing a certificate from the lake's own CA that expired on
2026-01-02 into `trino.pem` in place: Trino served it 43 s later, each client failed as in the table (the Python and
dbt lines from a client container on the network), and `make tls-rotate` restored service 34 s later, Trino never
restarted. The by-address column is each client pointed at Trino's IP address on the network; a name the
certificate lacks gives Python the same (`identity.sh` rows `I06`, `I07`).

---

## Failure modes / incidents

None yet.

---

**Last verified:** 2026-11-26 against branch `step/s13-identity` on throwaway lakes and temporary secrets; not yet
on the live stack (step S22).

#!/usr/bin/env bash
# Identity, row by row (ADR-060): Trino takes a password only over HTTPS, every client verifies it
# against the platform's CA by name, and a password or a certificate changes with no restart.
#
#   bash scripts/verify/identity.sh                        # the live Trino (step S22 onwards)
#   bash scripts/verify/identity.sh --throwaway <prefix>   # a throwaway lake (scripts/lib/throwaway-lake.sh)
#
# Live, it checks K2_TRINO_CONTAINER (default k2-trino) on K2_IDENTITY_NETWORK (default k2-net)
# with ${K2_SECRETS:-$HOME/k2-secrets}; on a throwaway, <prefix>-trino on <prefix>-net with the
# run's own secrets, and adds I17, a server certificate rotation. Every client runs in a one-off
# container on Trino's network and reaches it as https://trino:8443, by name, as every client on
# the network does. It logs in as K2_IDENTITY_USER (default svc.catalog, a service that can only
# read run records) with that principal's password file, mounted where model.password_mount puts
# it; no password is ever on a command line, in this script's environment or in its output.
#
#   I01-I13   scripts/verify/identity.py, over Trino's client protocol: no password, a wrong one,
#             plain HTTP, a login, impersonation, by address and by a wrong name, a certificate
#             from another CA, an expired one and one for another name (on a local server, from a
#             test CA made here, so the platform's CA key is never used), a password rotation and
#             a removal measured against one refresh period, on a probe user no rule names: its one
#             password.db line is written and removed in place under the file's lock (I13 checks).
#   I14       the internal shared secret: config.properties names ${ENV:K2_TRINO_SHARED_SECRET},
#             and the container's value is set, at least 32 characters and no placeholder.
#   I15, I16  the Trino CLI (from Trino's own image) and dbt-trino (dbt/profiles.yml, the `trino`
#             Python client underneath) log in by name, verified against ca.pem.
#   I17       throwaway only: `scripts/tls.sh rotate` in place; Trino serves the new certificate
#             within its ssl-context refresh (1 minute) with no restart.
#   I18       Trino was not restarted while any of the above ran.
#
# Exit 1 on any FAIL. Needs Docker and openssl; pulls nothing but the pinned uv image.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD
UV_IMAGE=$(sed -n 's/^UV_IMAGE=//p' scripts/lib/throwaway-lake.sh)
USER_=${K2_IDENTITY_USER:-svc.catalog}
URL=https://trino:8443

if [ "${1:-}" = --throwaway ]; then
  P=${2:?usage: $0 --throwaway <prefix>}
  C=$P-trino NET=$P-net THROWAWAY=1
  SECRETS=$(bash scripts/lib/throwaway-lake.sh state "$P")/secrets/k2
else
  C=${K2_TRINO_CONTAINER:-k2-trino} NET=${K2_IDENTITY_NETWORK:-k2-net} THROWAWAY=0
  SECRETS=${K2_SECRETS:-$HOME/k2-secrets}
  # the live stack: the .env file's mode first, as every live-touching path checks it (scripts/check-env.py)
  python3 scripts/check-env.py --mode-only || { echo "identity: .env is readable beyond its owner (scripts/check-env.py)" >&2; exit 2; }
fi
[[ "$USER_" == svc.* ]] || { echo "identity: K2_IDENTITY_USER must be a svc.* principal with a password file" >&2; exit 2; }
PASSWORD_FILE=$SECRETS/principals/$USER_.password
MOUNT=/run/k2-secrets/${USER_#svc.}.password  # model.password_mount, for a svc.* login
[ "$(docker inspect -f '{{.State.Running}}' "$C" 2>/dev/null)" = true ] || { echo "identity: $C is not running" >&2; exit 2; }
for f in "$SECRETS/tls/ca.pem" "$PASSWORD_FILE" "$SECRETS/trino/password.db"; do
  [ -s "$f" ] || { echo "identity: $f is missing (make tls-init; make password PRINCIPAL=$USER_ TO_FILE=1)" >&2; exit 2; }
done
IMAGE=$(docker inspect -f '{{.Config.Image}}' "$C")
started=$(docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}' "$C")

fails=0
row() {  # row <id> <passed: 0|1> <assertion> <detail>
  if [ "$2" = 1 ]; then echo "PASS $1: $3"; else echo "FAIL $1: $3"; fails=$((fails + 1)); fi
  echo "      $4"
}

# Test certificates, none of them Trino's: a CA that is not the platform's, and under it a valid
# certificate for Trino's names, an expired one, and a valid one for another name. Under $HOME,
# which Docker Desktop shares, and gone at exit.
F=$(mktemp -d "$HOME/.k2-identity.XXXXXX")
trap 'rm -rf "$F"' EXIT
key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; }
key "$F/other-ca.key"
openssl req -x509 -new -key "$F/other-ca.key" -subj "/CN=identity.sh test CA, not the platform's" -days 1 -out "$F/other-ca.pem" 2>/dev/null
leaf() {  # leaf <name> <SAN> [openssl ca dates ...]: <name>.pem, a key and its certificate under the test CA
  key "$F/$1.key"
  openssl req -new -key "$F/$1.key" -subj "/CN=$1" -out "$F/$1.csr" 2>/dev/null
  printf 'basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n' "$2" > "$F/$1.ext"
  if [ $# -gt 2 ]; then  # openssl x509 cannot set a start date in the past; openssl ca can
    printf '[ca]\ndefault_ca=t\n[t]\ndatabase=%s/index\nnew_certs_dir=%s\nserial=%s/serial\ndefault_md=sha256\npolicy=p\nunique_subject=no\n[p]\ncommonName=supplied\n' \
      "$F" "$F" "$F" > "$F/ca.cnf"
    : > "$F/index"; echo 01 > "$F/serial"
    openssl ca -batch -notext -config "$F/ca.cnf" -cert "$F/other-ca.pem" -keyfile "$F/other-ca.key" -in "$F/$1.csr" \
      -out "$F/$1.crt" -extfile "$F/$1.ext" "${@:3}" 2>/dev/null
  else
    openssl x509 -req -in "$F/$1.csr" -CA "$F/other-ca.pem" -CAkey "$F/other-ca.key" -set_serial "0x$(openssl rand -hex 8)" \
      -days 1 -extfile "$F/$1.ext" -out "$F/$1.crt" 2>/dev/null
  fi
  cat "$F/$1.key" "$F/$1.crt" > "$F/$1.pem"
}
leaf other-valid DNS:trino,DNS:localhost
leaf other-wrong-name DNS:elsewhere.invalid
leaf other-expired DNS:trino,DNS:localhost -startdate 20200101000000Z -enddate 20200102000000Z
chmod 644 "$F"/*.pem

# A one-off client container on Trino's network, as the invoking user.
client() { docker run --rm --network "$NET" --user "$(id -u):$(id -g)" --memory 768m --cpus 1 \
  -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache -e UV_PYTHON_DOWNLOADS=never -e PYTHONDONTWRITEBYTECODE=1 -e UV_NO_PROGRESS=1 \
  -v "$SECRETS/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" -v "$PASSWORD_FILE:$MOUNT:ro" "$@"; }

echo "identity: $C on $NET, logged in as $USER_, $URL"
client -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e K2_TRINO_URL="$URL" -e K2_SECRETS=/k2s -e K2_IDENTITY_USER="$USER_" \
  -e K2_IDENTITY_FIXTURES=/fixtures -v "$REPO/scripts:/src/scripts:ro" -v "$REPO/platform:/src/platform:ro" -v "$SECRETS/trino:/k2s/trino" \
  -v "$SECRETS/tls/ca.pem:/k2s/tls/ca.pem:ro" -v "$PASSWORD_FILE:/k2s/principals/$USER_.password:ro" -v "$F:/fixtures:ro" \
  -w /src "$UV_IMAGE" uv run --project /src/platform/k2gen --locked -q --with bcrypt==5.0.0 python -B scripts/verify/identity.py \
  || fails=$((fails + 1))

etc=$(docker inspect -f '{{range .Args}}{{.}} {{end}}' "$C" | sed -n 's/.*--etc-dir[= ]\([^ ]*\).*/\1/p')
etc=${etc:-/etc/trino}
named=$(docker exec "$C" cat "$etc/config.properties" | grep -cxF 'internal-communication.shared-secret=${ENV:K2_TRINO_SHARED_SECRET}' || true)
# The value never leaves the container: only the test's exit status does.
if docker exec "$C" bash -c 'v=${K2_TRINO_SHARED_SECRET-}; [ ${#v} -ge 32 ] || exit 1
  case ${v,,} in *change-me*|*changeme*|*example*|*secret*|*password*) exit 1 ;; esac' 2>/dev/null; then value=ok; else value=bad; fi
row I14.shared-secret "$([ "$named" = 1 ] && [ "$value" = ok ] && echo 1 || echo 0)" \
  "the internal shared secret is read from the environment (\${ENV:K2_TRINO_SHARED_SECRET}) and is no placeholder" \
  "$etc/config.properties names it: $([ "$named" = 1 ] && echo yes || echo no); the value is set, >= 32 characters, no placeholder: $value"

cli=$(client --entrypoint bash "$IMAGE" -c "TRINO_PASSWORD=\$(cat $MOUNT) trino --server $URL \
  --truststore-path /run/k2-secrets/tls/ca.pem --user '$USER_' --password --execute 'SELECT current_user'" 2>&1 || true)
row I15.cli "$([ "$cli" = "\"$USER_\"" ] && echo 1 || echo 0)" \
  "the Trino CLI logs in by name with --truststore-path ca.pem and --password (TRINO_PASSWORD): current_user is $USER_" "$cli"

dbt=$(client -e UV_PROJECT_ENVIRONMENT=/tmp/dbt-venv -e DBT_SEND_ANONYMOUS_USAGE_STATS=false -e K2_TRINO_HOST=trino \
  -e K2_TRINO_USER="$USER_" -v "$REPO/dbt:/src/dbt:ro" -w /src/dbt --entrypoint sh "$UV_IMAGE" -c \
  "export DBT_ENV_SECRET_K2_TRINO_PASSWORD=\$(cat $MOUNT); uv run --locked -q dbt debug --connection --profiles-dir /src/dbt \
   --project-dir /src/dbt --log-path /tmp/dbt-logs" 2>&1 || true)
row I16.dbt "$(grep -q 'All checks passed!' <<< "$dbt" && echo 1 || echo 0)" \
  "dbt-trino with dbt/profiles.yml (method ldap, cert ca.pem, the password from DBT_ENV_SECRET_K2_TRINO_PASSWORD) connects" \
  "$(grep -E 'Connection test|All checks passed|Error|error' <<< "$dbt" | sed 's/\x1b\[[0-9;]*m//g' | head -3 | tr '\n' ' ')"

if [ "$THROWAWAY" = 1 ]; then
  serial() { openssl x509 -in "$SECRETS/tls/trino.pem" -noout -serial | cut -d= -f2; }
  old=$(serial)
  K2_SECRETS=$SECRETS bash scripts/tls.sh rotate >/dev/null
  new=$(serial)
  # Polls the certificate Trino serves to a client that asks for `trino`, verified against ca.pem.
  seen=$(client "$UV_IMAGE" python3 -c "
import socket, ssl, sys, time
ctx, t0 = ssl.create_default_context(cafile='/run/k2-secrets/tls/ca.pem'), time.monotonic()
while time.monotonic() - t0 < 150:
    with socket.create_connection(('trino', 8443), 5) as s, ctx.wrap_socket(s, server_hostname='trino') as t:
        if t.getpeercert()['serialNumber'] == sys.argv[1]:
            print(round(time.monotonic() - t0, 1)); break
    time.sleep(1)
else:
    print('never')" "$new" 2>&1 || true)
  row I17.server-certificate-rotation "$([ "$seen" != never ] && awk -v s="$seen" 'BEGIN { exit !(s + 0 > 0 && s <= 75) }' && echo 1 || echo 0)" \
    "make tls-rotate writes trino.pem in place under the same CA; Trino serves the new certificate within its 1-minute ssl-context refresh" \
    "serial $old -> $new, served after $seen s (limit 75 s)"
fi

now=$(docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}' "$C")
row I18.no-restart "$([ "$now" = "$started" ] && echo 1 || echo 0)" \
  "Trino was not restarted while the rows ran (StartedAt and RestartCount unchanged)" "$started -> $now"

[ "$fails" = 0 ] && echo "identity: PASS" || { echo "identity: FAIL ($fails)"; exit 1; }

#!/usr/bin/env bash
# The local certificate authority and Trino's server certificate (ADR-060). Every file is under
# ${K2_SECRETS:-$HOME/k2-secrets}/tls/, named as model.SECRET_FILES names them, outside any git
# checkout (refused inside one), and never committed, compiled or hashed into a release:
#
#   ca.pem      the CA's certificate, public: every client verifies Trino against it alone
#   ca.key      the CA's private key, mode 600, never mounted into any container
#   trino.pem   Trino's private key and its certificate in one PEM file, mode 600
#               (http-server.https.keystore.path)
#
#   scripts/tls.sh init [--force]    make tls-init [FORCE=1]   (make tls is the same)
#   scripts/tls.sh rotate            make tls-rotate
#   scripts/tls.sh status            make tls-status [MIN_DAYS=30]
#
# init: makes the CA and the server certificate if either is missing, and leaves both alone if
#   present (a rerun is a no-op, exit 0). An existing CA is replaced only with --force, which
#   also reissues the server certificate under the new CA; every client then needs the new
#   ca.pem (docs/runbooks/tls-and-passwords.md, section 4).
# rotate: a new server key and certificate under the same CA; the CA is untouched.
# status: subject, names, expiry and days left of both certificates, never a key; exit 1 if
#   either expires within MIN_DAYS days (default 30), a file is missing or looser than its
#   mode, the server certificate is not the CA's, its key is not its certificate's, or its
#   names are not exactly NAMES. Until the expiry alert lands (a later step), this exit code is
#   the alert.
#
# Keys are ECDSA P-256. The CA lives 1826 days (5 years) and may sign only NAMES (a critical
# name constraint, so its key signs nothing else a client would accept); the server certificate
# lives 90 days, short enough that rotating it is a habit, not an event. Its names are exactly
# NAMES: `trino`, the service's name inside the compose network (the throwaway lake's and the
# rehearsal's network alias too), and `localhost`, for a client on the host or inside the
# container. No IP address: Trino picks its certificate by the name a client asks for (SNI), so
# a client that connects by address is shown Trino's internal certificate and refuses it
# (step S11); an IP name here could never be served.
#
# Files are rewritten in place under an exclusive flock on the file, never renamed over: a
# container that mounts one keeps reading the old file after a rename (Docker Desktop's file
# sharing; CLAUDE.md's bind-mount note). Trino rereads its keystore while it runs (measured in
# the runbook), so a rotation needs no restart. New keys are made in a directory of mode 700
# under tls/ and removed at exit; nothing here prints a key.
set -euo pipefail

NAMES=(trino localhost)
CA_DAYS=1826
SERVER_DAYS=90
MIN_DAYS=${MIN_DAYS:-30}

die() { echo "tls: $*" >&2; exit 2; }

ROOT=${K2_SECRETS:-$HOME/k2-secrets}
[[ "$ROOT" = /* ]] || ROOT=$PWD/$ROOT
probe=$ROOT
while [ ! -d "$probe" ]; do probe=$(dirname "$probe"); done
if [ "$(git -C "$probe" rev-parse --is-inside-work-tree 2>/dev/null)" = true ]; then
  die "$ROOT is inside a git checkout; K2_SECRETS must name a directory outside any"
fi
TLS=$ROOT/tls
CA_PEM=$TLS/ca.pem CA_KEY=$TLS/ca.key SERVER=$TLS/trino.pem

# write_in_place <target> <source> [mode]: <target>'s content becomes <source>'s, under an
# exclusive flock on <target>, by one write padded to the old length (a PEM reader skips the
# trailing newlines) and then a truncate: same inode, and a reader never meets an empty file.
write_in_place() {
  python3 - "$@" <<'PY'
import fcntl, os, sys
target, source = sys.argv[1], sys.argv[2]
mode = int(sys.argv[3], 8) if len(sys.argv) > 3 else 0o600
new = open(source, "rb").read()
fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, mode)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.fchmod(fd, mode)
    padded = new.ljust(os.fstat(fd).st_size, b"\n")
    if os.pwrite(fd, padded, 0) != len(padded):
        sys.exit(f"tls: a short write to {target}; rerun")
    os.ftruncate(fd, len(new))
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

workdir() {  # a private directory for new keys, under tls/ (mode 700), gone at exit
  (umask 077; mkdir -p "$TLS")
  chmod 700 "$TLS"
  WORK=$(mktemp -d "$TLS/.new.XXXXXX")
  trap 'rm -rf "$WORK"' EXIT
}

new_key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; }
serial() { echo "0x$(openssl rand -hex 16)"; }
san() { local IFS=,; echo "${NAMES[*]/#/DNS:}"; }
permitted() { local n s=""; for n in "${NAMES[@]}"; do s+="${s:+,}permitted;DNS:$n"; done; echo "$s"; }

issue_ca() {
  new_key "$WORK/ca.key"
  printf '%s\n' "basicConstraints=critical,CA:TRUE,pathlen:0" "keyUsage=critical,keyCertSign,cRLSign" \
    "nameConstraints=critical,$(permitted)" "subjectKeyIdentifier=hash" > "$WORK/ca.ext"
  openssl req -new -key "$WORK/ca.key" -subj "/CN=K2 local CA $(date -u +%FT%TZ)" -out "$WORK/ca.csr"
  openssl x509 -req -in "$WORK/ca.csr" -signkey "$WORK/ca.key" -days "$CA_DAYS" -sha256 -set_serial "$(serial)" \
    -extfile "$WORK/ca.ext" -out "$WORK/ca.pem" 2>/dev/null
  write_in_place "$CA_KEY" "$WORK/ca.key" 600
  write_in_place "$CA_PEM" "$WORK/ca.pem" 644
}

issue_server() {
  new_key "$WORK/trino.key"
  printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" "extendedKeyUsage=serverAuth" \
    "subjectAltName=$(san)" "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid" > "$WORK/trino.ext"
  openssl req -new -key "$WORK/trino.key" -subj "/CN=${NAMES[0]}" -out "$WORK/trino.csr"
  openssl x509 -req -in "$WORK/trino.csr" -CA "$CA_PEM" -CAkey "$CA_KEY" -days "$SERVER_DAYS" -sha256 \
    -set_serial "$(serial)" -extfile "$WORK/trino.ext" -out "$WORK/trino.crt" 2>/dev/null
  openssl verify -CAfile "$CA_PEM" "$WORK/trino.crt" >/dev/null || die "the new server certificate does not verify against $CA_PEM"
  cat "$WORK/trino.key" "$WORK/trino.crt" > "$WORK/trino.pem"
  write_in_place "$SERVER" "$WORK/trino.pem" 600
}

fingerprint() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }
# names <file> <extension>: the DNS names one extension of a certificate lists, comma-separated.
names() { openssl x509 -in "$1" -noout -ext "$2" 2>/dev/null | grep -oE 'DNS:[^, ]+' | paste -sd, || true; }

init() {
  local force=${1:-}
  if [ -e "$CA_PEM" ] || [ -e "$CA_KEY" ]; then
    if [ "$force" != --force ]; then
      [ -s "$CA_PEM" ] && [ -s "$CA_KEY" ] || die "$TLS holds only one of ca.pem and ca.key; FORCE=1 makes a new CA"
      if [ -s "$SERVER" ]; then
        echo "tls: the CA and the server certificate exist under $TLS; kept (FORCE=1 replaces the CA, make tls-rotate the server certificate)"
        return
      fi
      workdir; issue_server
      echo "tls: server certificate for $(san) issued under the existing CA, $SERVER (mode 600)"
      return
    fi
    echo "tls: FORCE=1: replacing the CA $(fingerprint "$CA_PEM" 2>/dev/null || echo '(unreadable)'); every client needs the new ca.pem" >&2
  fi
  workdir; issue_ca; issue_server
  echo "tls: CA $CA_PEM (sha256 $(fingerprint "$CA_PEM")), its key $CA_KEY (mode 600), server certificate $SERVER (mode 600) for $(san)"
}

rotate() {
  [ -s "$CA_PEM" ] && [ -s "$CA_KEY" ] || die "no CA under $TLS: make tls-init first"
  local old; old=$( [ -s "$SERVER" ] && openssl x509 -in "$SERVER" -noout -serial | cut -d= -f2 || echo none)
  workdir; issue_server
  echo "tls: server certificate reissued in place, serial $old -> $(openssl x509 -in "$SERVER" -noout -serial | cut -d= -f2), CA unchanged"
}

status() {
  local bad=0 f
  report() {  # report <label> <file>
    local label=$1 file=$2 end left
    if ! openssl x509 -in "$file" -noout >/dev/null 2>&1; then echo "$label: FAIL (no certificate in $file)"; bad=1; return; fi
    end=$(openssl x509 -in "$file" -noout -enddate | cut -d= -f2)
    left=$(( ($(date -u -d "$end" +%s) - $(date -u +%s)) / 86400 ))
    echo "$label: $file"
    echo "  subject   $(openssl x509 -in "$file" -noout -subject -nameopt RFC2253 | cut -d= -f2-)"
    if [ "$label" = CA ]; then echo "  may sign  $(names "$file" nameConstraints)"; else echo "  names     $(names "$file" subjectAltName)"; fi
    echo "  expires   $end ($left days left)"
    if ! openssl x509 -in "$file" -noout -checkend 0 >/dev/null; then
      echo "  FAIL: expired"; bad=1
    elif ! openssl x509 -in "$file" -noout -checkend $((MIN_DAYS * 86400)) >/dev/null; then
      echo "  FAIL: expires within $MIN_DAYS days"; bad=1
    fi
  }
  for f in "$CA_PEM" "$CA_KEY" "$SERVER"; do
    [ -s "$f" ] || { echo "tls: FAIL ($f is missing: make tls-init)"; exit 1; }
  done
  report "CA" "$CA_PEM"
  report "server" "$SERVER"
  [ "$(stat -c %a "$TLS")" = 700 ] || { echo "  FAIL: $TLS is mode $(stat -c %a "$TLS"), not 700"; bad=1; }
  for f in "$CA_KEY" "$SERVER"; do
    [ "$(stat -c %a "$f")" = 600 ] || { echo "  FAIL: $f is mode $(stat -c %a "$f"), not 600"; bad=1; }
  done
  if ! openssl verify -no_check_time -CAfile "$CA_PEM" "$SERVER" >/dev/null 2>&1; then  # dates are checked above
    echo "  FAIL: the server certificate is not signed by $CA_PEM"; bad=1
  fi
  if [ "$(openssl pkey -in "$SERVER" -pubout 2>/dev/null)" != "$(openssl x509 -in "$SERVER" -noout -pubkey)" ]; then
    echo "  FAIL: the key in $SERVER is not its certificate's"; bad=1
  fi
  local want; want=$(san)
  [ "$(names "$SERVER" subjectAltName)" = "$want" ] \
    || { echo "  FAIL: the server certificate's names are not exactly $want"; bad=1; }
  [ "$bad" = 0 ] && echo "tls: PASS (each certificate valid for more than $MIN_DAYS days)" || echo "tls: FAIL"
  return "$bad"
}

case "${1:-}" in
  init) init "${2:-}" ;;
  rotate) rotate ;;
  status) status ;;
  *) die "usage: $0 init [--force] | rotate | status" ;;
esac

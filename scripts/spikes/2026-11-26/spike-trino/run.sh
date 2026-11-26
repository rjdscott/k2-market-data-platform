#!/usr/bin/env bash
# Spike 2026-11-26: Trino 484 column masks and row filters on Iceberg through Lakekeeper,
# table procedures, and rule reloads. Write-up:
# docs/research/2026-11-26-unified-platform-architecture/SPIKE-ACCESS.md
#
#   bash scripts/spikes/2026-11-26/spike-trino/run.sh
#
# A throwaway Trino, the stack's image by digest, named k2-spike-trino-<pid>, on the
# stack's network with etc/ from here: catalog `governance` (the generated domain catalog)
# and `spike_ro` (the same warehouse with iceberg.security=read_only), under the file
# rules in etc/rules.json. It writes only the scratch schema governance.spike_acl_20261126
# (setup.sql, synthetic rows), which is dropped on exit through the stack's k2-trino as
# the writer dbt_governance; the container goes too. MINIO_ROOT_USER and
# MINIO_ROOT_PASSWORD come from the environment, else from the repo's .env, and reach the
# container by name only; nothing prints them. Takes about four minutes: two Trino starts
# and a 90 s wait. Prints one line per finding; exits 1 if a finding no longer holds.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
IMAGE=trinodb/trino:484@sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f
NAME=k2-spike-trino-$$
NET=$(docker inspect k2-trino -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
S=governance.spike_acl_20261126
# Under $HOME, not /tmp: Docker Desktop bind-mounts only the paths it shares, and /tmp is not one.
mkdir -p "$HOME/.cache"; WORK=$(mktemp -d "$HOME/.cache/k2-spike-trino.XXXXXX")
for v in MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
  [ -n "${!v:-}" ] || export "$v=$(set +u; . "$ROOT/.env" >/dev/null 2>&1; printf '%s' "${!v:-}")"
  [ -n "${!v}" ] || { echo "FAIL $v is neither set nor in $ROOT/.env"; exit 1; }
done
cp -r "$HERE/etc" "$WORK/etc"; mkdir "$WORK/rules"; cp "$HERE/etc/rules.json" "$WORK/rules/rules.json"

q() { docker exec "$NAME" trino --user "$1" --output-format TSV --execute "$2" 2>&1; }
rows() { q "$1" "$2" | tr '\t' ',' | paste -sd';'; }
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
same() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1: want [$2], have [$3]"; fi; }
refused() {  # refused <finding> <user> <statement> [<text the refusal must carry>]
  local out
  if out=$(q "$2" "$3"); then bad "$1: it ran"; return; fi
  case "$out" in *"${4:-}"*) ok "$1" ;; *) bad "$1: refused, but not for [${4:-}]: $out" ;; esac
}
drop() {
  docker exec k2-trino trino --user dbt_governance --execute "DROP VIEW IF EXISTS $S.v_definer;
    DROP VIEW IF EXISTS $S.v_invoker; DROP VIEW IF EXISTS $S.v_ruled; DROP TABLE IF EXISTS $S.t;
    DROP TABLE IF EXISTS $S.k; DROP SCHEMA IF EXISTS $S" >/dev/null 2>&1 || true
}
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; drop; rm -rf "$WORK"; }
trap cleanup EXIT
# start <access-control.properties>: a fresh container, waited on for up to 120 s
start() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  printf '%s\n' "$1" > "$WORK/etc/access-control.properties"
  docker run -d --name "$NAME" --network "$NET" --cpus 2 --memory 2g -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
    -v "$WORK/etc:/etc/trino:ro" -v "$WORK/rules:/spike-etc:ro" "$IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    q trino 'SELECT 1' >/dev/null && return 0
    sleep 2
  done
  echo "FAIL Trino did not start"; docker logs --tail 40 "$NAME" 2>&1; exit 1
}
RULES="access-control.name=file
security.config-file=/spike-etc/rules.json"

drop
start "$RULES
security.refresh-period=5s"
q dbt_governance "$(grep -v '^--' "$HERE/setup.sql")" >/dev/null
first=$(q dbt_governance "SELECT snapshot_id FROM $S.\"t\$snapshots\" WHERE summary['total-records'] = '3'")

# Reads as spike_reader: rows at least 20 minutes old (ids 1 to 3), `secret` masked.
same "direct read: the mask and the row filter apply" "1,****;2,****;3,****" \
  "$(rows spike_reader "SELECT id, secret FROM $S.t ORDER BY id")"
same "definer view: the reader's mask and filter do NOT apply" "1,alpha;2,bravo;3,charlie;4,delta;5,echo" \
  "$(rows spike_reader "SELECT id, secret FROM $S.v_definer ORDER BY id")"
same "a rule on the view itself applies" "1,####;2,####;3,####" \
  "$(rows spike_reader "SELECT id, secret FROM $S.v_ruled ORDER BY id")"
same "invoker view: the reader's own rules apply" "1,****;2,****;3,****" \
  "$(rows spike_reader "SELECT id, secret FROM $S.v_invoker ORDER BY id")"
refused "allow:false column: SELECT * is denied" spike_hider "SELECT * FROM $S.t" "Access Denied"
same "allow:false column: DESCRIBE hides it" "id,sym,secret,price,ts" \
  "$(q spike_hider "DESCRIBE $S.t" | cut -f1 | paste -sd,)"
same "delay rule holds under count, a join, SELECT * and a WHERE on newer rows" "3;3;3;0" \
  "$(rows spike_reader "SELECT count(*) FROM $S.t; SELECT count(*) FROM $S.t JOIN $S.k USING (id);
    SELECT count(*) FROM (SELECT * FROM $S.t); SELECT count(*) FROM $S.t WHERE id >= 4")"
same "a masked column in WHERE or JOIN sees the mask" "0;0" \
  "$(rows spike_reader "SELECT count(*) FROM $S.t WHERE secret = 'alpha';
    SELECT count(*) FROM $S.t JOIN $S.k ON t.secret = k.secret")"
files=$(q spike_reader "SELECT json_format(readable_metrics) FROM $S.\"t\$files\"")
hfiles=$(q spike_hider "SELECT json_format(readable_metrics) FROM $S.\"t\$files\"")
case "$files|$hfiles" in
  *echo*\|*h5*) ok "LEAK: \"t\$files\" shows the masked column's bounds (echo) to the reader and the denied column's (h5) to the hider" ;;
  *) bad "the \$files leak is closed: reader [$files], hider [$hfiles]" ;;
esac

# Table procedures.
for p in optimize "rollback_to_snapshot($first)" "expire_snapshots(retention_threshold => '7d')"; do
  refused "read_only catalog refuses $p, even for the writer" dbt_governance "ALTER TABLE spike_ro.spike_acl_20261126.t EXECUTE $p"
done
refused "a user with a row filter is refused optimize, as a side effect" spike_reader \
  "ALTER TABLE $S.t EXECUTE optimize" "row filter"
for p in optimize "expire_snapshots(retention_threshold => '7d')" "rollback_to_snapshot($first)"; do
  if out=$(q spike_plain "ALTER TABLE $S.t EXECUTE $p"); then ok "writable catalog: a plain reader runs $p"
  else bad "writable catalog: a plain reader was refused $p: $out"; fi
done
same "the plain reader's rollback changed what every reader sees" "3" "$(q dbt_governance "SELECT count(*) FROM $S.t")"

# Rule reload, with security.refresh-period=5s: poll until the changed mask is served.
cp "$HERE/rules-changed.json" "$WORK/rules/rules.json"
t0=$SECONDS
until [ "$(q spike_reader "SELECT secret FROM $S.t WHERE id = 1")" = CHANGED ] || (( SECONDS - t0 > 60 )); do sleep 1; done
(( SECONDS - t0 <= 60 )) && ok "with a 5 s refresh period a changed rules file is live within $(( SECONDS - t0 )) s" \
  || bad "with a 5 s refresh period the change was not live after 60 s"

# Without a refresh period: the changed file is not read until a restart.
cp "$HERE/etc/rules.json" "$WORK/rules/rules.json"
start "$RULES"
cp "$HERE/rules-changed.json" "$WORK/rules/rules.json"
sleep 90
same "without security.refresh-period the old mask is still served 90 s after the change" "****" \
  "$(q spike_reader "SELECT secret FROM $S.t WHERE id = 1")"

[ "$fail" = 0 ] && echo "spike-trino: every finding of SPIKE-ACCESS.md holds" || echo "spike-trino: a finding changed, see FAIL above"
exit "$fail"

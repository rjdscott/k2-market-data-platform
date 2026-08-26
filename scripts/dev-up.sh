#!/usr/bin/env bash
# The maintainer's bring-up checklist, as code. See docs/development/setup.md.
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1 || true

step()   { printf '\n== %s ==\n' "$*"; }
result() { printf '%s: %s\n' "$1" "$2"; }
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# (a) refuse to run outside the repo root
step "(a) preflight"
if [ ! -f docker-compose.yml ] || [ ! -f .env ]; then
  result "(a) preflight" "FAIL (run from the repo root — needs docker-compose.yml and .env)"
  exit 1
fi
result "(a) preflight" "PASS (docker-compose.yml + .env present)"

# (b) prefect-server port override, idempotent
step "(b) prefect-server port 4200 override"
if [ -f docker-compose.override.yml ]; then
  result "(b) override" "SKIP (docker-compose.override.yml already present)"
else
  busy=0
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ':4200$' && busy=1 || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:4200 -sTCP:LISTEN >/dev/null 2>&1 && busy=1 || true
  fi
  if [ "$busy" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      echo "[dry-run] write docker-compose.override.yml (prefect-server -> 14200:4200)"
      echo "[dry-run] append docker-compose.override.yml to .git/info/exclude"
    else
      cat > docker-compose.override.yml <<'EOF'
services:
  prefect-server:
    ports: !override
      - "14200:4200"
EOF
      grep -qxF 'docker-compose.override.yml' .git/info/exclude 2>/dev/null \
        || echo 'docker-compose.override.yml' >> .git/info/exclude
    fi
    result "(b) override" "WROTE (host port 4200 busy -> prefect-server moved to 14200)"
  else
    result "(b) override" "SKIP (port 4200 free)"
  fi
fi

# (c) build + start — daemon caches, so plain --build is cheap on a no-op
# K2_GIT_SHA is the capture image's build arg; it lands in
# k2_capture_build_info's `git_sha` label. `git describe --always --dirty`, not
# `rev-parse`: an image built from a dirty tree must not claim to be the commit
# it was started from. Unset (outside a checkout) ships `unknown`, which is
# honest — compose's default.
step "(c) docker compose up -d --build"
K2_GIT_SHA="$(git describe --always --dirty 2>/dev/null || echo unknown)"
export K2_GIT_SHA
result "(c) build sha" "$K2_GIT_SHA"
run docker compose up -d --build
result "(c) up --build" "$( [ "$DRY_RUN" = "1" ] && echo 'SKIPPED (dry-run)' || echo DONE )"

# (d) recreate holders of any directory bind mount that changed last commit
step "(d) recreate stale mount holders"
if ! git rev-parse HEAD~1 >/dev/null 2>&1; then
  result "(d) mount holders" "SKIP (no previous commit)"
else
  changed=$(git diff --name-only HEAD~1 -- . 2>/dev/null || true)

  services=()
  printf '%s\n' "$changed" | grep -q '^docker/lake/'     && services+=(spark-iceberg lake-init) || true
  printf '%s\n' "$changed" | grep -q '^docker/redpanda/' && services+=(redpanda-init) || true
  printf '%s\n' "$changed" | grep -q '^config/'          && services+=(capture-binance capture-kraken capture-coinbase) || true
  printf '%s\n' "$changed" | grep -q '^schemas/'         && services+=(redpanda-init) || true

  if [ "${#services[@]}" -eq 0 ]; then
    result "(d) mount holders" "SKIP (no docker/lake, docker/redpanda, config/, or schemas/ changes in HEAD~1..HEAD)"
  else
    mapfile -t services < <(printf '%s\n' "${services[@]}" | sort -u)
    # read-only lookup — run even under --dry-run so the preview is accurate
    defined=$(docker compose config --services 2>/dev/null || true)
    to_run=()
    for svc in "${services[@]}"; do
      printf '%s\n' "$defined" | grep -qxF "$svc" && to_run+=("$svc") || true
    done
    if [ "${#to_run[@]}" -eq 0 ]; then
      result "(d) mount holders" "SKIP (mapped services not defined in this compose file: ${services[*]})"
    else
      run docker compose up -d --force-recreate --no-deps "${to_run[@]}"
      result "(d) mount holders" "RECREATED:${to_run[*]}"
    fi
  fi
fi

# (e) wait for health, <= 5 minutes
step "(e) wait for health (<= 5 min)"
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] loop: docker compose ps --format '{{.Name}} {{.Health}}' until all healthy or no healthcheck, 5 min timeout"
else
  deadline=$((SECONDS + 300))
  while :; do
    unhealthy=$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -v ' healthy$' | grep -v ' $' || true)
    if [ -z "$unhealthy" ]; then
      result "(e) health" "PASS (all services healthy or have no healthcheck)"
      break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      result "(e) health" "FAIL (still unhealthy after 5 min)"
      echo "$unhealthy"
      break
    fi
    sleep 15
  done
fi

# (f) data-flow probe
step "(f) data-flow probe"
if [ "$DRY_RUN" = "1" ]; then
  # shellcheck disable=SC2016 # literal preview text — $CLICKHOUSE_PASSWORD is not meant to expand here
  echo '[dry-run] docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD -q "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 2 MINUTE GROUP BY exchange"'
  echo '[dry-run] for each running capture-* service: docker run --rm --network <its-network> curlimages/curl:8.14.1 -s http://<service>:8082/metrics | grep k2_capture_last_message_ts_seconds'
else
  set -a
  # shellcheck disable=SC1091 # .env is generated locally from .env.example, not a static repo file to follow
  . ./.env
  set +a

  ch_out=$(docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
    "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 2 MINUTE GROUP BY exchange" 2>&1) || true
  if [ -z "$ch_out" ]; then
    result "(f) clickhouse silver_trades" "WARN (no rows in the last 2 minutes — handlers may be warming up)"
  else
    echo "$ch_out" | while IFS=$'\t' read -r exch cnt; do
      if [ -n "${cnt:-}" ] && [ "$cnt" -gt 0 ] 2>/dev/null; then
        result "(f) $exch" "PASS ($cnt trades/2min)"
      else
        result "(f) $exch" "WARN (0 trades/2min)"
      fi
    done
  fi

  for svc in capture-binance capture-kraken capture-coinbase; do
    cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
    [ -z "$cid" ] && continue || true
    running=$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)
    [ "$running" != "true" ] && continue || true
    net=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$cid" 2>/dev/null || true)
    [ -z "$net" ] && continue || true
    metrics=$(docker run --rm --network "$net" curlimages/curl:8.14.1 -s "http://${svc}:8082/metrics" 2>/dev/null || true)
    lastts=$(printf '%s\n' "$metrics" | grep '^k2_capture_last_message_ts_seconds' || true)
    if [ -n "$lastts" ]; then
      result "(f) $svc metrics" "PASS ($lastts)"
    else
      result "(f) $svc metrics" "WARN (no k2_capture_last_message_ts_seconds yet — may be warming up)"
    fi
  done
fi

# (g) final status
step "(g) final status"
run docker compose ps

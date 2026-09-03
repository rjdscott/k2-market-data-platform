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

# (b) prefect-server host port, idempotent
# One variable, not an override file: compose interpolates K2_PREFECT_PORT into
# both the `ports` mapping and PREFECT_UI_API_URL, and a UI published on a port
# its own API URL does not name is a UI that loads and then fails every request.
step "(b) prefect-server host port"
if grep -q '^K2_PREFECT_PORT=' .env; then
  result "(b) prefect port" "SKIP (.env already sets K2_PREFECT_PORT=$(grep -m1 '^K2_PREFECT_PORT=' .env | cut -d= -f2))"
else
  busy=0
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ':4200$' && busy=1 || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:4200 -sTCP:LISTEN >/dev/null 2>&1 && busy=1 || true
  fi
  if [ "$busy" = "1" ]; then
    run sh -c 'printf "\n# host 4200 was busy at bring-up (scripts/dev-up.sh)\nK2_PREFECT_PORT=14200\n" >> .env'
    result "(b) prefect port" "WROTE (host port 4200 busy -> K2_PREFECT_PORT=14200 in .env)"
  else
    result "(b) prefect port" "SKIP (port 4200 free, compose default)"
  fi
fi
if [ -f docker-compose.override.yml ] && grep -q '4200' docker-compose.override.yml; then
  result "(b) legacy override" "WARN (docker-compose.override.yml still remaps 4200 — delete it, K2_PREFECT_PORT replaces it)"
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

# (e) + (f) health and data-flow probe — scripts/health.sh, the same one
# `make health` runs. Non-zero from it is reported, not fatal: the rest of the
# checklist (and `docker compose ps`) is worth printing either way.
step "(e) health + data-flow probe (scripts/health.sh)"
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] bash scripts/health.sh"
elif bash scripts/health.sh; then
  result "(e) health.sh" "PASS"
else
  result "(e) health.sh" "FAIL (see the FAIL lines above)"
fi

# (g) final status
step "(g) final status"
run docker compose ps

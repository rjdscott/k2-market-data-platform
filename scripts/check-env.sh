#!/usr/bin/env bash
# Refuses an .env that would start the stack on a credential anyone can guess.
# `make up` and scripts/dev-up.sh run it before `docker compose up`; a bare
# `docker compose up -d` does not (SECURITY.md).
#
#   bash scripts/check-env.sh [ENV_FILE] [EXAMPLE_FILE]    # defaults: .env .env.example
#
# Exit 1, naming each variable and never printing a value, when:
#   - MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD. The user is MinIO's access key,
#     which logs and consoles print, so a password equal to it is no password;
#   - a variable whose name ends in PASSWORD, SECRET or KEY is empty, or still
#     holds the value .env.example ships for it.
# The file is parsed, not sourced: NAME=value, the last assignment wins, one
# pair of surrounding quotes stripped.
set -euo pipefail

# value_of FILE NAME
value_of() {
  local v
  v=$(sed -n "s/^$2=//p" "$1" | tail -n 1)
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  printf '%s' "$v"
}

# check_env ENV_FILE EXAMPLE_FILE
check_env() {
  local env=$1 example=$2 fail=0 name
  if [ "$(value_of "$env" MINIO_ROOT_USER)" = "$(value_of "$env" MINIO_ROOT_PASSWORD)" ]; then
    echo "check-env: FAIL $env: MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD; set the password to \`openssl rand -hex 24\`" >&2
    fail=1
  fi
  for name in $(sed -n 's/^\([A-Z0-9_]*\(PASSWORD\|SECRET\|KEY\)\)=.*/\1/p' "$example"); do
    local v
    v=$(value_of "$env" "$name")
    if [ -z "$v" ] || [ "$v" = "$(value_of "$example" "$name")" ]; then
      echo "check-env: FAIL $env: $name is empty or still the $example placeholder" >&2
      fail=1
    fi
  done
  [ "$fail" = 0 ] && echo "check-env: PASS $env (MinIO user and password differ; no placeholder secrets)"
  return "$fail"
}

check_env "${1:-.env}" "${2:-.env.example}"

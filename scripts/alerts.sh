#!/usr/bin/env bash
# Alertmanager to Slack: install the webhook, test delivery, and prove the path on a
# throwaway copy. docs/runbooks/where-an-alert-goes.md is the procedure around it.
#
#   bash scripts/alerts.sh install-webhook   # make alerts-install-webhook: the URL from stdin, never argv
#   bash scripts/alerts.sh test              # make alert-test: one labelled K2TestAlert through k2-alertmanager
#   bash scripts/alerts.sh standin           # make alert-test-standin: the whole path, nothing live touched
#
# `test` exits 1 on any FAIL, 0 on PASS or SKIP. It prints SKIP, not PASS, while no
# webhook is installed: Alertmanager runs and takes alerts, and every notification
# fails until the file exists. It never prints the URL.
#
# `standin` runs Alertmanager exactly as docker-compose.yml defines it (`docker
# compose run`, project k2-alerttest, so no port and no name clashes with the live
# one), a Prometheus with the repository's prometheus.yml and the Alertmanager rules,
# and a stand-in for Slack that records what it receives (busybox httpd in the same
# Alertmanager image, so no image of its own). Then: no webhook file (SKIP, and a
# forced send fails visibly), install, deliver, a repository rule's own alert
# arrives, inhibition, rotation without a restart, a resolved message, uninstall.
# The stand-in tokens are random and are not secrets; the run asserts they reach no
# payload body and no log line all the same. Its files live under $HOME, which Docker
# Desktop shares and /tmp is not, outside the checkout (install-webhook refuses a
# K2_SECRETS inside one), and are removed at exit. K2_ALERT_TEST_PREFIX names its
# containers (default k2-alerttest).
set -uo pipefail
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

WEBHOOK_FILE=/run/k2-secrets/slack-webhook-url   # inside the container; docker-compose.yml mounts it
RUNBOOK=docs/runbooks/where-an-alert-goes.md
AM=${K2_ALERTMANAGER:-k2-alertmanager}
PROM=${K2_PROMETHEUS:-k2-prometheus}

fail=0
pass() { printf 'PASS %s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fail=1; }
die()  { printf 'FAIL %s\n' "$*" >&2; exit 1; }

# ── install-webhook ─────────────────────────────────────────────────────────
# Refused under xtrace (bash -x, set -x), which would print the URL, and for a K2_SECRETS inside
# a git checkout. Puts ${K2_SECRETS} and alertmanager/ back to mode 700 on every run, a
# directory Docker made for the mount (mode 755) included.
cmd_install_webhook() {
  case $- in *x*) { set +x; } 2>/dev/null; die "xtrace is on (bash -x or set -x), which would print the URL; run it without";; esac
  local root=${K2_SECRETS:-$HOME/k2-secrets} url
  local dir=$root/alertmanager
  [[ "$root" = /* ]] || root=$PWD/$root dir=$PWD/$dir
  # shellcheck source=lib/outside-checkout.sh
  . "$ROOT/scripts/lib/outside-checkout.sh"
  outside_checkout "$root" alerts
  if [ -t 0 ]; then
    printf 'Slack incoming-webhook URL (input hidden): ' >&2
    IFS= read -rs url; echo >&2
  else
    IFS= read -r url
  fi
  url=${url%$'\r'}
  case "$url" in http://*|https://*) ;; *) die "not a URL: paste the https://hooks.slack.com/services/... webhook";; esac
  case "$url" in *[[:space:]]*) die "the URL contains whitespace";; esac
  mkdir -p "$root" && chmod 700 "$root" || die "cannot create $root"
  mkdir -p "$dir" && chmod 700 "$dir" || die "cannot create $dir"
  # Written in place, not renamed over the old file: through Docker Desktop's file
  # sharing the container kept reading the old file after a rename, and saw an
  # in-place write at once (both measured 2026-11-26). The file is empty only between
  # the truncate and one write() of under 200 bytes; a notification that read it then
  # fails once and goes again at its group's next flush.
  (umask 077 && printf '%s\n' "$url" > "$dir/slack-webhook-url" && chmod 600 "$dir/slack-webhook-url") \
    || die "cannot write $dir/slack-webhook-url"
  case "$url" in https://hooks.slack.com/*) ;; *) echo "NOTE: not a hooks.slack.com URL; installed anyway" >&2;; esac
  echo "installed $dir/slack-webhook-url (mode 600); Alertmanager reads it at the next notification, no restart. Test it: make alert-test"
}

# ── test ────────────────────────────────────────────────────────────────────
amtool() { docker exec "$AM" amtool --alertmanager.url=http://localhost:9093 "$@"; }

# "<notifications> <given up> <requests done> <requests failed>" for the Slack integration.
counters() {
  docker exec "$AM" wget -qO- http://localhost:9093/metrics | awk '
    !/integration="slack"/ { next }
    /^alertmanager_notifications_total\{/          { n += $NF }
    /^alertmanager_notifications_failed_total\{/   { nf += $NF }
    /^alertmanager_notification_requests_total\{/  { r += $NF }
    /^alertmanager_notification_requests_failed_total\{/ { rf += $NF }
    END { printf "%d %d %d %d\n", n, nf, r, rf }'
}

# Sends one K2TestAlert and waits for Alertmanager's attempt to finish. 0 when Slack
# took it (a request completed and nothing failed), 1 otherwise. TEST_ID is the
# alert's id, for the caller.
send_test_alert() {
  local n0 nf0 r0 rf0 n nf r rf start end
  read -r n0 nf0 r0 rf0 < <(counters) || { bad "alert delivery to Slack: no metrics from $AM"; return 1; }
  TEST_ID=$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM
  # Both ends given: with --end alone Alertmanager starts the alert at its end. A
  # value with spaces is quoted inside the flag, or amtool warns about its parser.
  start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  end=$(date -u -d '+1 minute' +%Y-%m-%dT%H:%M:%SZ)
  amtool alert add K2TestAlert severity=info component=alert-test test_id="$TEST_ID" \
    --annotation="summary=\"Delivery test $TEST_ID from make alert-test: not an incident, nothing to do\"" \
    --annotation=runbook="$RUNBOOK" --start="$start" --end="$end" >/dev/null \
    || { bad "alert delivery to Slack: amtool could not add the test alert to $AM"; return 1; }
  for _ in $(seq 1 45); do
    read -r n nf r rf < <(counters)
    if [ "$r" -gt "$r0" ]; then
      sleep 1; read -r n nf r rf < <(counters)
      if [ "$nf" -eq "$nf0" ] && [ "$rf" -eq "$rf0" ]; then
        pass "alert delivery to Slack: K2TestAlert $TEST_ID sent (notifications $n0 -> $n, failed $nf0 -> $nf)"
        return 0
      fi
      bad "alert delivery to Slack: K2TestAlert $TEST_ID failed (failed $nf0 -> $nf): docker logs --since 2m $AM 2>&1 | grep 'Notify for alerts failed'"
      return 1
    fi
    sleep 2
  done
  bad "alert delivery to Slack: no attempt within 90 s (notifications $n0 -> $n): docker logs --since 2m $AM"
  return 1
}

cmd_test() {
  if [ "$(docker inspect -f '{{.State.Running}}' "$AM" 2>/dev/null)" != true ]; then
    bad "alertmanager: $AM is not running (docker compose up -d --no-deps alertmanager)"
    return
  fi
  # Prometheus reads prometheus.yml through a file mount, so a changed alerting block
  # needs a recreate, not a restart; this is where a stale one shows.
  # A Prometheus started seconds ago may not have resolved it yet: up to 30 s.
  local active
  for _ in $(seq 1 15); do
    active=$(docker exec "$PROM" wget -qO- http://localhost:9090/api/v1/alertmanagers 2>/dev/null \
      | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["data"]["activeAlertmanagers"]))' 2>/dev/null) || active=""
    case "$active" in ""|0) sleep 2 ;; *) break ;; esac
  done
  case "$active" in
    "") bad "prometheus to alertmanager: no answer from $PROM" ;;
    0)  bad "prometheus to alertmanager: $PROM has no Alertmanager (docker compose up -d --force-recreate --no-deps prometheus)" ;;
    *)  pass "prometheus to alertmanager: $PROM sends to $active Alertmanager" ;;
  esac
  if ! docker exec "$AM" test -s "$WEBHOOK_FILE"; then
    echo "SKIP alert delivery to Slack: webhook not installed (make alerts-install-webhook; $RUNBOOK)"
    return
  fi
  send_test_alert
}

# ── standin ─────────────────────────────────────────────────────────────────
P=${K2_ALERT_TEST_PREFIX:-k2-alerttest}
received() { docker exec "$P-slack" sh -c 'cat /tmp/received/* 2>/dev/null'; }

# Waits up to $1 seconds for a received payload matching the Python predicate $2
# (over `q`, the query string the stand-in saw, and `b`, the parsed body); prints
# the newest match's query string and attachment text.
wait_payload() {
  local secs=$1 pred=$2 out
  for _ in $(seq 1 $((secs / 3))); do
    out=$(received | PRED="$pred" python3 -c '
import json, os, sys
lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
hits = []
for q, raw in zip(lines[0::2], lines[1::2]):
    b = json.loads(raw)
    a = b["attachments"][0]
    if eval(os.environ["PRED"]):
        hits.append((q, a["title"] + "\n" + a["text"]))
if hits:
    print(hits[-1][0]); print(hits[-1][1])') && [ -n "$out" ] && { printf '%s\n' "$out"; return 0; }
    sleep 3
  done
  return 1
}

cmd_standin() {
  local tmp secrets img prom_img net t1 t2 out link started
  tmp=$(mktemp -d "$HOME/.k2-alert-test.XXXXXX") || die "mktemp under $HOME"
  secrets=$tmp/k2-secrets
  # shellcheck disable=SC2064  # expand now: the names are fixed for this run
  trap "docker rm -f $P-am $P-prom $P-slack >/dev/null 2>&1; docker network rm ${P}_k2-net >/dev/null 2>&1; docker volume rm ${P}_alertmanager-data >/dev/null 2>&1; rm -rf '$tmp'" EXIT
  dc() { K2_SECRETS=$secrets docker compose -p "$P" --env-file .env.example "$@"; }
  img=$(dc config --format json alertmanager | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["alertmanager"]["image"])')
  prom_img=$(dc config --format json prometheus | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["prometheus"]["image"])')
  net=${P}_k2-net
  t1=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
  t2=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
  AM=$P-am PROM=$P-prom

  echo "== (1) Alertmanager as compose defines it, no webhook file =="
  out=$(dc run -d --name "$P-am" --no-deps alertmanager 2>&1) || die "docker compose run alertmanager: $out"
  # The stand-in for Slack: one file per request, the query string then the body, and
  # the reply Slack's webhook gives, a plain "ok".
  local hook
  hook=$(cat <<'HOOK'
#!/bin/sh
f=$(mktemp /tmp/received/XXXXXX)
{ printf '%s\n' "$QUERY_STRING"; head -c "$CONTENT_LENGTH"; printf '\n'; } > "$f"
printf 'Content-Type: text/plain\r\n\r\nok'
HOOK
)
  local serve='mkdir -p /tmp/www/cgi-bin /tmp/received && printf "%s\n" "$HOOK" > /tmp/www/cgi-bin/hook &&
    chmod 755 /tmp/www/cgi-bin/hook && exec httpd -f -p 8080 -h /tmp/www'
  docker run -d --name "$P-slack" --network "$net" --cpus 0.1 -m 32m -e HOOK="$hook" --entrypoint sh "$img" -c "$serve" \
    >/dev/null || die "start the stand-in"
  docker run -d --name "$P-prom" --network "$net" --cpus 0.25 -m 256m \
    -v "$ROOT/docker/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    -v "$ROOT/docker/prometheus/rules/alertmanager-alerts.yml:/etc/prometheus/rules/alertmanager-alerts.yml:ro" \
    "$prom_img" --config.file=/etc/prometheus/prometheus.yml >/dev/null || die "start Prometheus"
  for _ in $(seq 1 30); do docker exec "$AM" wget -qO- http://localhost:9093/-/ready >/dev/null 2>&1 && break; sleep 1; done
  docker exec "$AM" wget -qO- http://localhost:9093/-/ready >/dev/null 2>&1 \
    && pass "alertmanager starts with no webhook file" || bad "alertmanager not ready without a webhook file: docker logs $AM"
  started=$(docker inspect -f '{{.State.StartedAt}}' "$AM")
  # Prometheus must hold a sample of the failed counter at 0 before the failure, for
  # increase() to see it: wait for its first scrape of Alertmanager.
  for _ in $(seq 1 40); do
    docker exec "$PROM" wget -qO- 'http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22alertmanager%22%7D' 2>/dev/null \
      | grep -q '"value":\[[0-9.]*,"1"\]' && break
    sleep 1
  done
  out=$(cmd_test 2>&1); printf '%s\n' "$out" | sed 's/^/    | /'
  grep -q '^FAIL' <<<"$out" && bad "make alert-test failed before the webhook check"
  grep -qx "SKIP alert delivery to Slack: webhook not installed (make alerts-install-webhook; $RUNBOOK)" <<<"$out" \
    && pass "make alert-test says SKIP while no webhook is installed" || bad "no SKIP line without a webhook"

  echo "== (2) a forced send with no webhook file fails, visibly =="
  if out=$(send_test_alert); then bad "a send with no webhook file passed: $out"
  else pass "a forced send fails: ${out#FAIL alert delivery to Slack: }"; fi
  docker logs "$AM" 2>&1 | grep 'Notify for alerts failed' | grep -q "open $WEBHOOK_FILE: no such file or directory" \
    && pass "alertmanager logs the missing webhook file at ERROR" || bad "no log line names the missing file"
  docker exec "$AM" wget -qO- http://localhost:9093/-/healthy >/dev/null && pass "alertmanager stays healthy" || bad "alertmanager unhealthy"

  echo "== (3) install the webhook from stdin, then deliver =="
  out=$(printf 'http://%s-slack:8080/cgi-bin/hook?%s\n' "$P" "$t1" | K2_SECRETS=$secrets bash "$ROOT/scripts/alerts.sh" install-webhook 2>&1)
  printf '%s\n' "$out" | sed "s/$t1/<token>/g"
  grep -q "$t1" <<<"$out" && bad "install-webhook printed the URL" || pass "install-webhook printed no URL"
  [ "$(stat -c %a "$secrets/alertmanager/slack-webhook-url")" = 600 ] && [ "$(stat -c %a "$secrets/alertmanager")" = 700 ] \
    && pass "webhook file mode 600, directory 700" || bad "webhook file or directory mode is wrong"
  send_test_alert
  link="https://github.com/rjdscott/k2-market-data-platform/blob/main/$RUNBOOK"
  if out=$(wait_payload 15 "\"$TEST_ID\" in a[\"text\"]"); then
    sed -n 1p <<<"$out" | grep -qx "$t1" && pass "delivered to the installed URL" || bad "delivered to another URL"
    grep -q "^FIRING: K2TestAlert (info)$" <<<"$out" && grep -q "^\*K2TestAlert\* (info): " <<<"$out" \
      && pass "payload: alert name and severity" || bad "payload lacks the name or severity"
    grep -qF "Runbook: <$link|$RUNBOOK>" <<<"$out" && pass "payload: the runbook as a GitHub link" || bad "payload lacks the runbook link"
    grep -qE "^Started [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} UTC" <<<"$out" && pass "payload: start time in UTC" || bad "payload lacks a UTC start time"
    sed -n '2,$p' <<<"$out" | sed 's/^/    | /'
  else
    bad "the stand-in received no K2TestAlert"
  fi

  echo "== (4) a repository rule's alert, through Prometheus, arrives =="
  # The forced send in (2) failed, so the rule AlertmanagerNotificationsFailing fires
  # in this Prometheus and reaches the stand-in now the webhook is installed.
  if out=$(wait_payload 120 'a["title"] == "FIRING: AlertmanagerNotificationsFailing (critical)"'); then
    pass "AlertmanagerNotificationsFailing: rule to Prometheus to Alertmanager to the stand-in"
    grep -qF "Runbook: <$link|$RUNBOOK>" <<<"$out" && pass "payload: the rule's own runbook annotation as a link" || bad "rule payload lacks its runbook link"
    sed -n '2,$p' <<<"$out" | sed 's/^/    | /'
  else
    bad "AlertmanagerNotificationsFailing never arrived (docker exec $PROM wget -qO- localhost:9090/api/v1/alerts)"
  fi

  echo "== (5) a critical silences its warning twin, and only it =="
  amtool alert add DockerDiskUsageCritical severity=critical component=storage job=redpanda-public instance=redpanda:9644 \
    --annotation='summary="stand-in test"' --annotation=runbook=docs/runbooks/docker-desktop-disk.md >/dev/null
  amtool alert add DockerDiskUsageHigh severity=warning component=storage job=redpanda-public instance=redpanda:9644 \
    --annotation='summary="stand-in test"' --annotation=runbook=docs/runbooks/docker-desktop-disk.md >/dev/null
  amtool alert add CaptureResyncStorm severity=warning component=capture exchange=kraken \
    --annotation='summary="stand-in test"' --annotation=runbook=docs/runbooks/capture-sequence-gaps.md >/dev/null
  sleep 2
  out=$(docker exec "$AM" wget -qO- http://localhost:9093/api/v2/alerts | python3 -c '
import json, sys
for a in json.load(sys.stdin):
    print(a["labels"]["alertname"], a["status"]["state"], len(a["status"]["inhibitedBy"]))')
  grep -qx "DockerDiskUsageHigh suppressed 1" <<<"$out" && grep -qx "DockerDiskUsageCritical active 0" <<<"$out" \
    && grep -qx "CaptureResyncStorm active 0" <<<"$out" \
    && pass "DockerDiskUsageHigh inhibited by DockerDiskUsageCritical; an unrelated warning is not" \
    || bad "inhibition: $(tr '\n' ';' <<<"$out")"

  echo "== (6) rotate the webhook: the next message uses the new URL, no restart =="
  printf 'http://%s-slack:8080/cgi-bin/hook?%s\n' "$P" "$t2" | K2_SECRETS=$secrets bash "$ROOT/scripts/alerts.sh" install-webhook >/dev/null 2>&1
  send_test_alert
  out=$(wait_payload 15 "\"$TEST_ID\" in a[\"text\"]") && [ "$(sed -n 1p <<<"$out")" = "$t2" ] \
    && pass "rotated: delivered to the new URL" || bad "rotation: not delivered to the new URL"
  [ "$(docker inspect -f '{{.State.StartedAt}}' "$AM")" = "$started" ] \
    && pass "alertmanager was not restarted by install or rotation" || bad "alertmanager restarted"

  echo "== (7) resolved messages are sent =="
  wait_payload 120 'a["title"] == "RESOLVED: K2TestAlert (info)"' >/dev/null \
    && pass "a resolved K2TestAlert arrived" || bad "no resolved message"

  echo "== (8) uninstall: SKIP again =="
  rm -f "$secrets/alertmanager/slack-webhook-url"
  cmd_test | grep -q '^SKIP alert delivery to Slack: webhook not installed' && pass "SKIP once the file is gone" || bad "no SKIP after removing the file"

  echo "== (9) no URL anywhere it should not be =="
  if received | grep '^{' | grep -q -e "$t1" -e "$t2"; then bad "a token reached a payload body"; else pass "no token in any payload body ($(received | grep -c '^{') received)"; fi
  if docker logs "$AM" 2>&1 | grep -q -e "$t1" -e "$t2"; then bad "a token reached the log"; else pass "no token in the Alertmanager log"; fi
  if docker exec "$AM" wget -qO- http://localhost:9093/api/v2/status | grep -q -e "$t1" -e "$t2"; then bad "a token is in /api/v2/status"; else pass "no token in /api/v2/status (the config names the file only)"; fi
}

case "${1:-test}" in
  install-webhook) cmd_install_webhook ;;
  test)            cmd_test ;;
  standin)         cmd_standin ;;
  *) die "usage: $0 install-webhook|test|standin" ;;
esac
exit "$fail"

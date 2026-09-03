#!/usr/bin/env bash
# The doc gates run by hand before every release/PR, as code.
# Scope: docs/ + README.md + the per-service READMEs (the "published docs"
# surface) unless a check says otherwise, plus the Prometheus rules, which carry
# runbook paths and alert semantics the docs quote.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Every markdown file the link and forbidden-word gates cover. The per-service
# READMEs are published on GitHub exactly as docs/ is, and
# services/capture-rust/README.md is 12 KB dense with relative `docs/plans/...`
# links — scanning docs/ and README.md alone left it ungated. So is
# legacy/v2-kotlin/README.md, which is the retired tier's entry point and was
# written after the archive, not with it.
#
# legacy/v1 is deliberately NOT scanned: it is archived unmodified (CLAUDE.md)
# and its READMEs already point at v1-era paths that no longer exist. Gating it
# would only be satisfiable by editing the archive.
published_docs() {
  find docs -name '*.md'
  printf 'README.md\n'
  find services docker schemas scripts legacy/v2-kotlin -name 'README.md' 2>/dev/null
}

fail=0
pass() { printf 'PASS: %s\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1"; fail=1; }
note() { printf 'NOTE: %s\n' "$1"; }
warn() { printf 'WARN: %s\n' "$1"; }

# (a) relative markdown links resolve
out=$(mktemp)
published_docs | while IFS= read -r file; do
  [ "$file" = "docs/adr/template.md" ] && continue || true
  dir=$(dirname "$file")
  grep -oE '\]\([^)]+\)' "$file" 2>/dev/null | sed -E 's/^\]\(([^)]+)\)$/\1/' | while IFS= read -r link; do
    case "$link" in
      http://*|https://*|mailto:*|\#*) continue ;;
    esac
    link_nofrag="${link%%#*}"
    [ -z "$link_nofrag" ] && continue || true
    case "$link_nofrag" in
      /*) target=".$link_nofrag" ;;
      *)  target="$dir/$link_nofrag" ;;
    esac
    [ -e "$target" ] || echo "$file -> $link"
  done || true
done > "$out" || true
if [ -s "$out" ]; then
  bad "(a) broken relative markdown links:"
  sed 's/^/  /' "$out"
else
  pass "(a) relative markdown links resolve"
fi
rm -f "$out"

# (b) forbidden words in published docs (audits may quote them)
out=$(mktemp)
# shellcheck disable=SC2046  # word-splitting the file list is the point
grep -niE "interview|optiver|hiring|feedback from" $(published_docs) 2>/dev/null \
  | grep -v '^docs/audits/' > "$out" || true
if [ -s "$out" ]; then
  bad "(b) forbidden words found:"
  sed 's/^/  /' "$out"
else
  pass "(b) no forbidden words in published docs"
fi
rm -f "$out"

# (c) promtool check rules (needs Docker)
if command -v docker >/dev/null 2>&1; then
  if docker run --rm --entrypoint sh -v "$PWD/docker/prometheus/rules:/r" prom/prometheus:v3.2.0 \
      -c 'promtool check rules /r/*.yml'; then
    pass "(c) promtool check rules"
  else
    bad "(c) promtool check rules failed"
  fi
else
  note "(c) docker not found — skipping promtool check rules"
fi

# (c2) promtool test rules — the alert unit tests (needs Docker)
# `check rules` only says the YAML parses. These say the thresholds mean what
# the comments claim: that a stopped stream crosses CaptureFeedStale on its own
# per-stream bound, that a frozen gauge still crosses LakeIngestFailed, and that
# a healthy small-file day fires nothing. Two directories, one gate: the capture
# tests were written as `tests/*.test.yml`, the lake ones as
# `rules/tests/*_test.yml`, and both are run rather than renamed because every
# runbook and FMEA row that cites one cites it by path.
if command -v docker >/dev/null 2>&1; then
  if docker run --rm --entrypoint sh -v "$PWD/docker/prometheus:/p" prom/prometheus:v3.2.0 \
      -c 'promtool test rules /p/tests/*.test.yml /p/rules/tests/*_test.yml'; then
    pass "(c2) promtool test rules"
  else
    bad "(c2) promtool test rules failed"
  fi
else
  note "(c2) docker not found — skipping promtool test rules"
fi

# (d) every runbook: annotation path in rules resolves to a file
out=$(mktemp)
grep -rhoE 'runbook: *\S+' docker/prometheus/rules/*.yml 2>/dev/null \
  | sed -E 's/^runbook: *//' | sort -u | while IFS= read -r path; do
  [ -e "$path" ] || echo "$path"
done > "$out" || true
if [ -s "$out" ]; then
  bad "(d) runbook annotation paths that don't resolve:"
  sed 's/^/  /' "$out"
else
  pass "(d) runbook annotation paths resolve"
fi
rm -f "$out"

# (d2) every alert HAS a runbook: annotation
# Gate (d) only checks that the annotations present resolve, so an alert with no
# annotation at all passed it — which is how 5 of the 6 ClickHouse rules shipped
# with no runbook while docs/architecture/11-observability.md claimed "checked by
# check-docs.sh gate (d)". A rule you cannot act on at 3am is a page, not an
# alert. Recording rules are exempt: nothing pages on them.
out=$(mktemp)
for f in docker/prometheus/rules/*.yml; do
  awk -v f="$f" '
    function flush() { if (name != "" && !seen) print f " -> " name }
    /^[[:space:]]*-[[:space:]]*alert:[[:space:]]/ { flush(); name = $NF; seen = 0; next }
    /^[[:space:]]*-[[:space:]]*record:[[:space:]]/ { flush(); name = ""; seen = 0; next }
    /^[[:space:]]*runbook:[[:space:]]/ { seen = 1 }
    END { flush() }
  ' "$f"
done > "$out" || true
if [ -s "$out" ]; then
  bad "(d2) alerts with no runbook: annotation:"
  sed 's/^/  /' "$out"
else
  pass "(d2) every alert carries a runbook: annotation"
fi
rm -f "$out"

# (e) capacity-model gate: predicted-only until a benchmark file covers capacity
cm=docs/architecture/15-capacity-model.md
if [ ! -f "$cm" ]; then
  bad "(e) $cm not found"
elif [ "$(grep -c "predicted" "$cm")" -eq 0 ]; then
  bad "(e) $cm has no 'predicted' rows"
# A table CELL that is exactly "measured" — i.e. the measured column's header,
# which is the thing this gate guards against appearing unbacked. The previous
# form (`^| *measured`) required `measured` to be the row's FIRST cell, so it
# could never match `| Metric | predicted | measured |` and returned 0 both
# before and after the column it exists to catch.
elif [ "$(grep -ciE '\|[[:space:]]*measured[[:space:]]*\|' "$cm")" -gt 0 ]; then
  if [ -z "$(grep -l capacity docs/benchmarks/*.md 2>/dev/null || true)" ]; then
    bad "(e) $cm has a 'measured' column but no docs/benchmarks/*.md mentions capacity"
  else
    pass "(e) capacity-model gate (measured column backed by a benchmark file)"
  fi
else
  pass "(e) capacity-model gate (predicted-only, no measured column yet)"
fi

# (f) no status tables/checkboxes in docs/plans
out=$(mktemp)
grep -rn '\- \[ \]\|\- \[x\]' docs/plans 2>/dev/null > "$out" || true
if [ -s "$out" ]; then
  bad "(f) status checkboxes found in docs/plans:"
  sed 's/^/  /' "$out"
else
  pass "(f) no status tables/checkboxes in docs/plans"
fi
rm -f "$out"

# (g) mermaid diagram width — WARN only, never fails the gate
out=$(mktemp)
grep -rl '```mermaid' docs 2>/dev/null | while IFS= read -r file; do
  awk -v f="$file" '
    /^```mermaid/ { inblock=1; next }
    /^```/        { inblock=0 }
    inblock && length($0) > 110 { print f ":" NR ": " length($0) " chars" }
  ' "$file"
done > "$out" || true
if [ -s "$out" ]; then
  while IFS= read -r line; do warn "(g) mermaid line >110 chars: $line"; done < "$out"
else
  pass "(g) no mermaid lines over 110 chars"
fi
rm -f "$out"

exit "$fail"

#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Corrupt-frame injection — SKIPPED, deliberately, until Phase G.
#
# Exits 0 and records a SKIP line. It is a script rather than a missing file so
# that `make chaos` reports the gap on every run instead of quietly not covering
# it, and so the FMEA's proof column has something honest to point at.
#
# Why it cannot run yet
# --------------------
# The fault this row wants is: a frame arrives malformed and the adapter neither
# crashes nor silently discards it — it archives the bytes verbatim, counts the
# parse failure, and carries on (exchanges/mod.rs obligation 1: "a frame we
# failed to understand is precisely the one worth keeping").
#
# There are two ways to inject that, and neither exists today:
#
#   1. Corrupt the wire. Every venue connection is TLS, so there is no seam
#      between the socket and the parser to flip a byte in without terminating
#      the connection instead — which tests the reconnect path, not the parser.
#   2. Replay a fixture with a flipped byte through the live binary. That is
#      exactly `k2-replay`, and `k2-replay` is Phase G
#      (006-phase-g-replay-parity.md). Until it exists there is no way to push
#      chosen bytes through the *running* process.
#
# A third option is available today and is not chaos:
# services/capture-rust/src/exchanges/kraken.rs::unparseable_frames_are_archived_not_dropped
# asserts the behaviour in a unit test. That is a stronger guarantee than an
# injection would be — it runs on every commit — but it proves the adapter, not
# the deployed container, so it does not close this row.
#
# Revisit when: k2-replay lands (Phase G). The script then becomes: take
# tests/fixtures/kraken-20s.jsonl, flip one byte in one frame, replay the file
# through the running binary, and assert the RawMessage for that frame is
# byte-identical to the corrupted input while the parse-failure counter moved
# by exactly one.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

cat >&2 <<'EOF'

── capture-corrupt-frame.sh ─────────────────────────────────────
  SKIP — not automatable until k2-replay (Phase G).

  TLS leaves no seam to corrupt a live frame, and replaying chosen bytes
  through the running binary is precisely what k2-replay is for.

  Covered today by a unit test instead, which proves the adapter but not
  the deployed container:
    services/capture-rust/src/exchanges/kraken.rs
      ::unparseable_frames_are_archived_not_dropped

  Runbook for the failure this would prove:
    docs/runbooks/capture-checksum-failure.md

EOF

report "capture-corrupt-frame.sh" SKIP skip skip
exit 0

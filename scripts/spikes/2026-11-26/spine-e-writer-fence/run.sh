#!/usr/bin/env bash
# N0-4 (e): writer A against writer B's table in one writable Trino catalog under file rules.
# Throwaway Trino 484 on k2-net through the live Lakekeeper, scratch schemas spike_n04plat_*
# in warehouses market_capture and fx_reference (no live namespace in either), synthetic rows;
# drops everything and removes the scratch prefixes from k2-lake afterwards.
set -euo pipefail
exec python3 -B "$(dirname "$0")/spike.py" "$@"  # -B: no __pycache__ in the checkout

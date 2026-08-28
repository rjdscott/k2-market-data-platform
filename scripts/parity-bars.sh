#!/usr/bin/env bash
# Three-way event-bar parity at the pinned snapshots (scripts/parity_bars.py).
#   scripts/parity-bars.sh                       # uses tests/parity/pinned.json 'bars'
#   scripts/parity-bars.sh --pin-current [DAY]   # pin the lake's current gold.bars / gold.trades snapshots
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
PIN=tests/parity/pinned.json
if [ "${1:-}" = "--pin-current" ]; then
  P=$(curl -s "localhost:18181/catalog/v1/config?warehouse=k2" | python3 -c "import sys,json; print(json.load(sys.stdin)['defaults']['prefix'])")
  snap() { curl -s "localhost:18181/catalog/v1/$P/namespaces/gold/tables/$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['metadata']['current-snapshot-id'])"; }
  DAY=${2:-$(date -u -d yesterday +%F)}
  set -- --day "$DAY" --bars-snapshot "$(snap bars)" --trades-snapshot "$(snap trades)" --write-pin
else
  set -- --day "$(python3 -c "import json; print(json.load(open('$PIN'))['bars']['day'])")" \
         --bars-snapshot "$(python3 -c "import json; print(json.load(open('$PIN'))['bars']['bars_snapshot'])")" \
         --trades-snapshot "$(python3 -c "import json; print(json.load(open('$PIN'))['bars']['trades_snapshot'])")" "$@"
fi
exec uv run --no-project --with duckdb==1.4.4 --with pytz --with pyyaml python scripts/parity_bars.py "$@"

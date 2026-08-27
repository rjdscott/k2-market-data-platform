#!/usr/bin/env bash
# Three-way OHLCV parity at the pinned snapshots (scripts/parity_ohlcv.py).
#   scripts/parity-ohlcv.sh                # uses tests/parity/pinned.json
#   scripts/parity-ohlcv.sh --pin-current  # pin the lake's current gold snapshots for today's run
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
PIN=tests/parity/pinned.json
if [ "${1:-}" = "--pin-current" ]; then
  P=$(curl -s "localhost:18181/catalog/v1/config?warehouse=k2" | python3 -c "import sys,json; print(json.load(sys.stdin)['defaults']['prefix'])")
  meta() { curl -s "localhost:18181/catalog/v1/$P/namespaces/gold/tables/$1" | python3 -c "import sys,json; d=json.load(sys.stdin)['metadata']; print(json.dumps({'snapshot': d['current-snapshot-id'], 'summary': next(s for s in d['snapshots'] if s['snapshot-id']==d['current-snapshot-id'])['summary']}))"; }
  T=$(meta trades); O=$(meta ohlcv_1m)
  SILVER=$(echo "$T" | python3 -c "import sys,json; s=json.load(sys.stdin)['summary']; print(json.dumps({k.split('.')[-1]: int(v) for k,v in s.items() if k.startswith('k2.src-snapshot-id.')}))")
  OHLCV=$(echo "$O" | python3 -c "import sys,json; print(json.load(sys.stdin)['snapshot'])")
  DAY=${2:-$(date -u -d yesterday +%F)}
  shift; set -- --day "$DAY" --ohlcv-snapshot "$OHLCV" --silver-snapshots "$SILVER" --write-pin "${@:2}"
else
  set -- --day "$(python3 -c "import json; print(json.load(open('$PIN'))['day'])")" \
         --ohlcv-snapshot "$(python3 -c "import json; print(json.load(open('$PIN'))['ohlcv_1m_snapshot'])")" \
         --silver-snapshots "$(python3 -c "import json; print(json.dumps(json.load(open('$PIN'))['silver_snapshots']))")" "$@"
fi
exec uv run --no-project --with duckdb==1.4.4 --with clickhouse-connect==0.8.18 python scripts/parity_ohlcv.py "$@"

#!/usr/bin/env bash
# Principals land in the resource groups interface A8 states (step S20): `pipeline` for builders,
# publishers, svc.runs, svc.platform and svc.catalog; `interactive` for everyone else, people,
# svc.api, svc.api acting as a person, and the release canary. On a throwaway lake that
# `make access-verify` or tests/access/prove.sh started:
#
#   scripts/verify/resource-groups.sh <prefix>
#
# Each principal of the release runs one marked query; svc.platform, the one reader of query
# history, then reads each one's resource group from system.runtime.queries. The expected group is
# written here from A8's words, by principal kind, not read from the compiled resource-groups.json
# it tests. One PASS or FAIL line per principal; exit 0 when all pass.
set -euo pipefail
cd "$(dirname "$0")/../.."
P=${1:?usage: scripts/verify/resource-groups.sh <throwaway prefix>}
exec bash scripts/lib/throwaway-lake.sh py "$P" - <<'PY'
import re, sys, uuid
from pathlib import Path
sys.path.insert(0, "/src/scripts")
import access_verify as av
from k2gen.emit import trino_rules

trino, env = av.connect(Path("/work/env.json"))
m = av.load(Path(env["release"]))
PIPELINE = re.compile(r"(dbt|pub)_.+|svc\.(runs|platform|catalog)")  # A8's pipeline group, by name
run = uuid.uuid4().hex[:8]
users = trino_rules.known_principals(m)
people = sorted(p.id for p in m.principals if p.id.startswith("p."))
cases = [(u, None) for u in users] + ([("svc.api", people[0])] if people else [])
for u, as_user in cases:
    trino.ok(u, f"SELECT 'rgprobe-{run}-{as_user or u}-{'as' if as_user else 'own'}'", as_user=as_user)
rows = trino.ok("svc.platform", "SELECT query, \"user\", resource_group_id FROM system.runtime.queries "
                                f"WHERE query LIKE 'SELECT ''rgprobe-{run}-%'")
seen = {q.split("'")[1]: (user, ".".join(g or [])) for q, user, g in rows}
failed = 0
for u, as_user in cases:
    label = f"rgprobe-{run}-{as_user or u}-{'as' if as_user else 'own'}"
    who = as_user or u
    want = "pipeline" if PIPELINE.fullmatch(who) else "interactive"
    user, group = seen.get(label, (None, None))
    ok = group == want and user == who
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'} {u}{' as ' + as_user if as_user else ''}: resource group {group!r}, A8 says {want!r} (query user {user})")
print(f"resource-groups: {len(cases)} principals, {failed} FAIL")
sys.exit(1 if failed else 0)
PY

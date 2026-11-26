#!/usr/bin/env bash
# A rules-only release takes effect within Trino's refresh period, with no restart, and a removed
# grant is refused after it (A1, ADR-059; step S20). On a throwaway lake that `make access-verify`
# or tests/access/prove.sh started and staged:
#
#   scripts/verify/grant-refresh.sh <prefix> [n, default 5]
#
# Inside the lake's tools container (the one read-write mount, as A1 swaps pointers): release B is
# compiled from the running release A's documents with one grant removed (the first grant of the
# first product with a dbt or code output), and every file that differs between the two manifests
# must need nothing (model.need_of: no restart, recreate, DDL or DAG change), so B is rules-only;
# then n times, pointers/trino is swapped to B the way deploy swaps it
# (a new symlink renamed over the old, mv -T), and the time until the removed grantee's read of
# its view is refused is measured; five more reads must all be refused; then back to A, and the
# time until the read answers again. Each time must be within two refresh periods (2 x 5 s, the
# wait release-verify makes, since rules.json and groups.txt reload on separate timers). The
# Trino container's start time is the same before and after. Prints n, each time, the median and
# the maximum; exit 0 on a pass, 1 otherwise.
set -euo pipefail
cd "$(dirname "$0")/../.."
P=${1:?usage: scripts/verify/grant-refresh.sh <throwaway prefix> [n]}
N=${2:-5}
L=scripts/lib/throwaway-lake.sh
started() { docker inspect -f '{{.State.StartedAt}}' "$P-trino"; }
before=$(started)
code=0
bash "$L" py "$P" - "$N" <<'PY' || code=$?
import hashlib, json, os, statistics, sys, time, yaml
from pathlib import Path
sys.path.insert(0, "/src/scripts")
import access_verify as av
from k2gen import docset, model, release
from k2gen.emit import trino_etc

n = int(sys.argv[1])
trino, env = av.connect(Path("/work/env.json"))
a = Path(env["release"])
pointers = a.parent / "pointers"
docs = docset.read(a / model.DOCUMENTS_DIR)
m = model.load(docs)
p = next(p for p in sorted(m.products, key=lambda p: p.id) if p.grants and any(t.build.kind in ("dbt", "code") for t in p.tables))
gone = p.grants[0]
t = next(t for t in p.tables if t.build.kind in ("dbt", "code") and gone.role in {r.role for r in t.roles})
path = f"{p.path}/grants.yaml"
kept = [{"principal": g.principal, "role": g.role} for g in p.grants[1:]]
documents = {**docs.documents, path: yaml.safe_dump({"grants": kept}, sort_keys=False).encode()}
prov = docs.provenance
listed = [{"path": d.path, "sha256": d.sha256, "lastCommit": d.last_commit, "pr": d.pr} for d in prov.documents]
for d in listed:
    d["sha256"] = hashlib.sha256(documents[d["path"]]).hexdigest()
work = Path("/work/grant-refresh")
os.system(f"chmod -R u+w {work} 2>/dev/null; rm -rf {work}")
set_dir = work / "set"
for rel, data in documents.items():
    (set_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (set_dir / rel).write_bytes(data)
(set_dir / model.PROVENANCE).write_text(json.dumps({"source": prov.source, "tag": prov.tag, "commit": prov.commit,
    "tagger": prov.tagger, "taggedAt": prov.tagged_at, "setSha256": model.set_sha256(documents), "documents": listed}) + "\n")
manifest = json.loads((a / model.MANIFEST).read_text())
(work / "images.json").write_text(json.dumps(manifest["images"]) + "\n")
# No previous: the running copy of A carries the throwaway lake's additions, which release.read
# refuses; the needs are taken from the two manifests instead, as release.needs takes them.
b = release.release(set_dir, None, work / "images.json", a.parent, a / model.RELEASE_COMPOSE)
fa, fb = manifest["files"], json.loads((a.parent / b / model.MANIFEST).read_text())["files"]
changed = sorted(f for f in fa.keys() | fb.keys() if fa.get(f) != fb.get(f))
needs = sorted({str(model.need_of(f)) for f in changed if model.need_of(f)})
print(f"grant-refresh: release B {b}: {path} without {gone.principal} ({gone.role}); {len(changed)} files differ "
      f"({', '.join(f for f in changed if f.startswith('compiled/trino'))} among them); needs {needs or 'nothing'}")
rules_only = not needs

def swap(target: str) -> None:
    """deploy's swap (A1): a new relative symlink, renamed over the pointer."""
    tmp = pointers / f".trino.{os.getpid()}"
    os.symlink(f"../{target}", tmp)
    os.replace(tmp, pointers / "trino")

read = f"SELECT count(*) FROM {model.quote_relation(model.reader_relation(t))}"
def refused() -> bool:
    return trino.run(gone.principal, read).error == av.DENIED
def answers() -> bool:
    return trino.run(gone.principal, read).error is None

def until(pred) -> float:
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > 60:
            return float("inf")
        time.sleep(0.1)
    return time.monotonic() - t0

assert answers(), f"{gone.principal} cannot read {t.name} before the swap"
period = float(trino_etc.REFRESH.removesuffix("s"))
to_b, to_a, stays = [], [], []
for _ in range(n):
    swap(b)
    to_b.append(until(refused))
    stays.append(all(refused() for _ in range(5)))
    swap(a.name)
    to_a.append(until(answers))
fmt = lambda xs: ", ".join(f"{x:.1f}" for x in xs)
print(f"grant-refresh: grant removed, refused after (s, n={n}): {fmt(to_b)}; median {statistics.median(to_b):.1f}, max {max(to_b):.1f}")
print(f"grant-refresh: grant back, answering after (s, n={n}): {fmt(to_a)}; median {statistics.median(to_a):.1f}, max {max(to_a):.1f}")
print(f"grant-refresh: refused on each of 5 reads after every removal: {all(stays)}; refresh period {period:.0f} s, bound {2 * period:.0f} s")
ok = rules_only and all(stays) and max(to_b + to_a) <= 2 * period
sys.exit(0 if ok else 1)
PY
after=$(started)
[ "$before" = "$after" ] && echo "grant-refresh: Trino not restarted (started $before)" || { echo "grant-refresh: FAIL: Trino restarted ($before, then $after)"; code=1; }
[ "$code" = 0 ] && echo "grant-refresh: PASS" || echo "grant-refresh: FAIL"
exit "$code"

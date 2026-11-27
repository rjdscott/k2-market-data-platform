#!/usr/bin/env bash
# W0-4 (phase 002): one MinIO key per engine means something, on a lake made by the real
# docker/lake/init-lake.sh. A throwaway lake (scripts/lib/throwaway-lake.sh, prefix storage-keys)
# from a release of this tree, in the live layout, then:
#
#   1. lake-init's users: one per engine, each holding only its own policy; every warehouse unsigned
#   2. a warehouse made the old way (no remote-signing field, so Lakekeeper signs) is switched off
#      by lake-init in place, and two more runs change nothing: same users, policies, warehouse ids
#   3. Spark on its key writes a table in warehouse k2, whose LoadTable hands out no signer and no
#      credential; the maintenance key expires its snapshots by id (spike (b): older_than the
#      epoch, deleted_manifest_lists_count at least the ids) and cannot write the table
#   4. every storage-key row of tests/access/expectations.yaml (scripts/access_verify.py), then
#      Trino's key on that k2 table: its metadata file read, a write under its location refused
#   5. Trino on its scoped key, through Lakekeeper: CREATE TABLE AS and SELECT in a domain warehouse
#
#   bash scripts/verify/storage-keys.sh
#
# One PASS or FAIL line per claim, then `<n> PASS, <m> FAIL`; exit 0 when nothing failed. No secret
# is printed: the keys are this run's, read from the lake's state by name. Never touches the
# running stack; the lake and the scratch directory go in the trap (KEEP=1 keeps the directory).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
REPO=$PWD
L=scripts/lib/throwaway-lake.sh
P=${PREFIX:-storage-keys}
W=$(mktemp -d "$HOME/k2-storage-keys.XXXXXX")  # under $HOME, the one place Docker Desktop shares
cleanup() { bash "$L" down "$P" >/dev/null 2>&1; [ "${KEEP:-}" = 1 ] && echo "storage-keys: logs in $W" || { chmod -R u+w "$W"; rm -rf "$W"; }; }
trap cleanup EXIT
T0=$(date +%s)
claim() { if [ "$1" = 0 ]; then echo "PASS $2"; else echo "FAIL $2"; fi | tee -a "$W/claims"; }
memory() {  # at least 10 GiB free before a container starts, or stop here
  local gib; gib=$(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo)
  [ "$gib" -ge 10 ] || { claim 1 "MemAvailable ${gib} GiB, under 10 GiB: $1 not started"; finish; }
}
finish() {
  local fail pass
  fail=$(grep -c '^FAIL' "$W/claims" 2>/dev/null); pass=$(grep -c '^PASS' "$W/claims" 2>/dev/null)
  echo "storage-keys: ${pass:-0} PASS, ${fail:-0} FAIL in $(( $(date +%s) - T0 )) s"
  [ "${fail:-0}" = 0 ] && [ "${pass:-0}" -gt 0 ]; exit $?
}

# A release of the current tree, as spine-e2e compiles one (placeholder image digests: never deployed).
mkdir -p "$W/clone" "$W/releases"
git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$W/clone"
git -C "$W/clone" init -q -b main && git -C "$W/clone" add -A
git -C "$W/clone" -c user.name=storage-keys -c user.email=storage-keys@example.org commit -q -m "storage-keys: the current tree"
git -C "$W/clone" -c user.name=storage-keys -c user.email=storage-keys@example.org tag -a v0.0.0-keys.1 -m storage-keys
python3 - "$W/clone/docker-compose.yml" > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "storage-keys/placeholder@sha256:" + "0" * 64 for n in names}))
PY
k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
"${k2gen[@]}" docset --tag v0.0.0-keys.1 --repo "$W/clone" --out "$W/set" > "$W/release.log" 2>&1 &&
  "${k2gen[@]}" release --set "$W/set" --previous none --images "$W/images.json" --out "$W/releases" 2>>"$W/release.log" | tail -1 > "$W/id"
claim $? "a release of this tree compiled ($(cat "$W/id"))"
[ -s "$W/id" ] || finish

memory "the throwaway lake"
bash "$L" up "$P" "$W/releases/$(cat "$W/id")" > "$W/up.log" 2>&1
claim $? "a throwaway lake up, made by docker/lake/init-lake.sh"
[ -f "$(bash "$L" state "$P")/secrets/s3.env" ] || { tail -n 20 "$W/up.log"; finish; }
STATE=$(bash "$L" state "$P")
# shellcheck source=/dev/null
set -a; . "$STATE/secrets/s3.env"; set +a   # this run's keys, to pass to Spark by name

# snapshot <name>: what lake-init made, with no secret in it: users and their policies, each policy
# document, and each warehouse's id and signing.
snapshot() {
  docker exec "$P-minio" mc admin user list local --json > "$W/$1.users"
  for f in docker/lake/minio/policies/*.json; do
    e=${f##*/}; docker exec "$P-minio" mc admin policy info local "k2-${e%.json}" --json
  done > "$W/$1.policies"
  bash "$L" py "$P" - > "$W/$1.warehouses" <<'PY'
import json, urllib.request
for w in sorted(json.load(urllib.request.urlopen("http://lakekeeper:8181/management/v1/warehouse", timeout=10))["warehouses"],
                key=lambda w: w["name"]):
    print(w["name"], w["warehouse-id"], json.dumps(w["storage-profile"].get("remote-signing-enabled", "absent")))
PY
}
same() {  # same <a> <b>: two snapshots' users and policies are equal, timestamps and order aside
  python3 - "$W/$1" "$W/$2" <<'PY'
import json, sys
def norm(x):  # MinIO lists users and a policy's actions in no fixed order
    if isinstance(x, dict):
        return {k: norm(v) for k, v in sorted(x.items()) if "date" not in k.lower()}
    return sorted((norm(v) for v in x), key=json.dumps) if isinstance(x, list) else x
docs = [sorted((norm(json.loads(line)) for line in open(f) if line.strip()), key=json.dumps) for f in sys.argv[1:]]
sys.exit(0 if docs[0] == docs[1] and docs[0] else 1)
PY
}

# 1. What the first run made.
snapshot a
python3 - "$W/a.users" <<'PY'
import json, os, sys
from pathlib import Path
users = {(d := json.loads(line))["accessKey"]: d.get("policyName") for line in open(sys.argv[1]) if line.strip()}
want = {os.environ[f"K2_{f.stem.upper()}_S3_ACCESS_KEY"]: f"k2-{f.stem}" for f in Path("docker/lake/minio/policies").glob("*.json")}
sys.exit(0 if users == want else 1)
PY
claim $? "one MinIO user per engine (spark, trino, maintenance, backup, tools), each holding only k2-<engine>; no other user"
awk '$3 != "false" {bad = 1} END {exit bad || NR < 2}' "$W/a.warehouses"
claim $? "every warehouse of the storage plan reads back remote signing off ($(wc -l < "$W/a.warehouses") warehouses)"

# 2. A warehouse made the old way: deleted (it is empty) and created again with no signing field.
OLD=fx_reference
bash "$L" py "$P" - "$OLD" > "$W/old" <<'PY'
import json, os, sys, urllib.request
LK, name = "http://lakekeeper:8181/management/v1", sys.argv[1]
def call(method, path, body=None):
    req = urllib.request.Request(LK + path, json.dumps(body).encode() if body else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status
wh = {w["name"]: w for w in json.load(urllib.request.urlopen(LK + "/warehouse", timeout=10))["warehouses"]}
call("DELETE", f"/warehouse/{wh[name]['warehouse-id']}")
call("POST", "/warehouse", {"warehouse-name": name, "project-id": "00000000-0000-0000-0000-000000000000",
     "storage-profile": {"type": "s3", "bucket": "k2-lake", "key-prefix": f"warehouse/{name}", "endpoint": "http://minio:9000",
                         "region": "local-01", "path-style-access": True, "flavor": "s3-compat", "sts-enabled": False},
     "storage-credential": {"type": "s3", "credential-type": "access-key", "aws-access-key-id": os.environ["MINIO_ROOT_USER"],
                            "aws-secret-access-key": os.environ["MINIO_ROOT_PASSWORD"]}})
w = next(w for w in json.load(urllib.request.urlopen(LK + "/warehouse", timeout=10))["warehouses"] if w["name"] == name)
print(w["warehouse-id"], json.dumps(w["storage-profile"].get("remote-signing-enabled", "absent")))
PY
read -r OLD_ID OLD_SIGNING < "$W/old"
ok=1; [ "$OLD_SIGNING" != true ] || ok=0
claim $ok "warehouse $OLD made the old way (no remote-signing field) signs: remote-signing-enabled $OLD_SIGNING"

bash "$L" init "$P" >> "$W/init.log" 2>&1; claim $? "lake-init run a second time"
snapshot b
bash "$L" init "$P" >> "$W/init.log" 2>&1; claim $? "lake-init run a third time"
snapshot c
ok=1; [ "$(awk -v n="$OLD" '$1 == n {print $2, $3}' "$W/b.warehouses")" != "$OLD_ID false" ] || ok=0
claim $ok "lake-init turned remote signing off on $OLD in place: same warehouse id, now false"
grep -q "exists with remote signing on: turning it off" "$STATE/lake-init.log"
claim $? "lake-init said it switched a signing warehouse off (POST /management/v1/warehouse/<id>/storage)"
diff -q "$W/b.warehouses" "$W/c.warehouses" >/dev/null && diff -q <(grep -v "^$OLD " "$W/a.warehouses") <(grep -v "^$OLD " "$W/c.warehouses") >/dev/null
claim $? "three runs recreate nothing: every warehouse keeps its id, all unsigned"
same a.users b.users && same b.users c.users
claim $? "three runs give the same users and policy attachments"
same a.policies b.policies && same b.policies c.policies
claim $? "three runs give the same policy documents"

# 3. Spark on its own key writes a table in k2; maintenance's key expires it by id and cannot write it.
memory "Spark"
SPARK_IMAGE=$(grep -oE 'image: *k2-spark-iceberg:[^ ]*' docker-compose.yml | head -1 | sed 's/image: *//')
docker run --rm -i --name "$P-spark" --label "k2.throwaway=$P" --network "$P-net" --memory 1536m --cpus 1 \
  -e K2_SPARK_S3_ACCESS_KEY -e K2_SPARK_S3_SECRET_KEY -e K2_MAINTENANCE_S3_ACCESS_KEY -e K2_MAINTENANCE_S3_SECRET_KEY \
  --entrypoint python3 "$SPARK_IMAGE" - > "$W/spark.out" 2> "$W/spark.err" <<'PY'
import json, os
from pyspark.sql import SparkSession
b = (SparkSession.builder.master("local[1]").appName("storage-keys").config("spark.driver.memory", "512m")
     .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC")
     .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"))
for cat, engine in (("sp", "SPARK"), ("mt", "MAINTENANCE")):  # one catalog per key, same warehouse
    c = f"spark.sql.catalog.{cat}"
    for k, v in {"": "org.apache.iceberg.spark.SparkCatalog", ".type": "rest", ".uri": "http://lakekeeper:8181/catalog",
                 ".warehouse": "k2", ".cache-enabled": "false", ".io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
                 ".s3.endpoint": "http://minio:9000", ".s3.path-style-access": "true", ".s3.region": "local-01",
                 ".s3.access-key-id": os.environ[f"K2_{engine}_S3_ACCESS_KEY"],
                 ".s3.secret-access-key": os.environ[f"K2_{engine}_S3_SECRET_KEY"]}.items():
        b = b.config(c + k, v)
spark = b.getOrCreate()
T = "k2_storage_probe.t"
spark.sql("CREATE NAMESPACE IF NOT EXISTS sp.k2_storage_probe")
spark.sql(f"CREATE TABLE sp.{T} (id bigint, v string) USING iceberg")
for i in range(4):
    spark.sql(f"INSERT INTO sp.{T} VALUES ({i}, 'row {i}')")
snaps = [r[0] for r in spark.sql(f"SELECT snapshot_id FROM sp.{T}.snapshots ORDER BY committed_at").collect()]
ids = snaps[:-1]  # every snapshot but the current one
out = {"snapshots": len(snaps), "ids": len(ids)}
row = spark.sql(f"CALL mt.system.expire_snapshots(table => '{T}', snapshot_ids => array({', '.join(f'{i}L' for i in ids)}), "
                "older_than => TIMESTAMP '1970-01-01 00:00:00')").collect()[0].asDict()
out["deleted_manifest_lists_count"] = row["deleted_manifest_lists_count"]
out["left"] = [r[0] for r in spark.sql(f"SELECT snapshot_id FROM sp.{T}.snapshots").collect()] == snaps[-1:]
out["rows"] = spark.sql(f"SELECT count(*) FROM sp.{T}").collect()[0][0]
try:
    spark.sql(f"INSERT INTO mt.{T} VALUES (99, 'maintenance')")
    out["maintenance_write"] = "ok"
except Exception as e:  # the refusal is the point; keep its class only
    out["maintenance_write"] = "refused" if "Forbidden" in str(e) or "AccessDenied" in str(e) or "403" in str(e) else type(e).__name__
out["rows_after"] = spark.sql(f"SELECT count(*) FROM sp.{T}").collect()[0][0]
print("RESULT " + json.dumps(out), flush=True)
spark.stop()
PY
claim $? "Spark ran on its own key and the maintenance key (warehouse k2)"
python3 - "$W/spark.out" > "$W/spark.claims" <<'PY'
import json, sys
r = next((json.loads(l[7:]) for l in open(sys.argv[1]) if l.startswith("RESULT ")), None)
if r is None:
    print("FAIL Spark printed no result"); sys.exit()
ok = lambda c, what: print(("PASS " if c else "FAIL ") + what)
ok(r["snapshots"] == 4 and r["rows"] == 4, f"Spark's key wrote table k2.k2_storage_probe.t: {r['snapshots']} snapshots, {r['rows']} rows")
ok(r["deleted_manifest_lists_count"] >= r["ids"] and r["left"],
   f"maintenance's key expired {r['ids']} snapshots by id (older_than the epoch): deleted_manifest_lists_count "
   f"{r['deleted_manifest_lists_count']}, only the current snapshot left")
ok(r["maintenance_write"] == "refused" and r["rows_after"] == 4,
   f"maintenance's key cannot write the table: INSERT {r['maintenance_write']}, still {r['rows_after']} rows")
PY
rc=$?  # a crash after some PASS lines must not pass
tee -a "$W/claims" < "$W/spark.claims"
[ "$rc" = 0 ] || claim 1 "the Spark claims read Spark's result to the end (exit $rc)"
grep -q '^RESULT ' "$W/spark.out" || tail -n 5 "$W/spark.err"

# 4 and 5, from the tools container, which holds every key by name.
bash "$L" py "$P" - > "$W/tools.claims" 2> "$W/tools.err" <<'PY'
import json, os, sys, urllib.error, urllib.request
from pathlib import Path
sys.path.insert(0, "/src/scripts")
import access_verify as av
from k2gen import model

def ok(c, what):
    print(("PASS " if c else "FAIL ") + what, flush=True)

LK = "http://lakekeeper:8181/catalog/v1"
trino, env = av.connect(Path("/work/env.json"))
m = av.load(Path(env["release"]))

# The k2 table as a client sees it: LoadTable hands out no signer and no credential.
prefix = json.load(urllib.request.urlopen(f"{LK}/config?warehouse=k2", timeout=10))["defaults"]["prefix"]
req = urllib.request.Request(f"{LK}/{prefix}/namespaces/k2_storage_probe/tables/t",
                             headers={"X-Iceberg-Access-Delegation": "vended-credentials,remote-signing"})
doc = json.load(urllib.request.urlopen(req, timeout=10))
cfg = doc.get("config", {})
ok(str(cfg.get("s3.remote-signing-enabled", "false")).lower() == "false" and "s3.secret-access-key" not in cfg
   and not doc.get("storage-credentials"), "LoadTable of k2.k2_storage_probe.t asks for both and gets no signer and no credential")

rows = [e for e in m.expectations if isinstance(e, model.StorageKeyExpectation)]
for e in rows:
    passed, why = av.storage_key(e)
    ok(passed, f"row: the {e.engine} key's {e.operation} of s3://{e.target} is {e.expect} ({why})")
ok(len(rows) >= 20, f"{len(rows)} storage-key rows in the release's expectations")

# Trino's key on the k2 table, at the storage layer: its metadata file read, a write under it refused.
end = os.environ.get("K2_S3_ENDPOINT") or av.S3_ENDPOINT
pair = (os.environ["K2_TRINO_S3_ACCESS_KEY"], os.environ["K2_TRINO_S3_SECRET_KEY"])
bucket, _, meta = doc["metadata-location"].removeprefix("s3://").partition("/")
loc = doc["metadata"]["location"].removeprefix(f"s3://{bucket}/")
got = av.s3(end, pair, "GET", bucket, meta)
ok(got[0] in (200, 206), f"Trino's key reads the k2 table's metadata file: HTTP {got[0]}")
got = av.s3(end, pair, "PUT", bucket, f"{loc}/data/k2-access-probe")
ok((got[0], got[1]) == (403, "AccessDenied"), f"Trino's key cannot write under the k2 table's location: HTTP {got[0]} {got[1]}")

# Trino on its scoped key, through Lakekeeper: a CTAS and a SELECT in a domain warehouse, as the builder.
p = next(p for p in m.products if p.name == "access-canary")
cat, ns, who = model.build_catalog(p.domain), model.base_namespace(p.name), model.builder(p.name)
wp = json.load(urllib.request.urlopen(f"{LK}/config?warehouse={model.warehouse(p.domain)}", timeout=10))["defaults"]["prefix"]
req = urllib.request.Request(f"{LK}/{wp}/namespaces", json.dumps({"namespace": [ns]}).encode(), {"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10)
except urllib.error.HTTPError as x:
    assert x.code == 409, x.code
a = trino.run(who, f'CREATE TABLE "{cat}"."{ns}".k2_storage_probe AS SELECT 7 AS id')
ok(a.error is None, f"Trino on its scoped key: {who} CREATE TABLE {cat}.{ns}.k2_storage_probe AS SELECT: {a.says()}")
a = trino.run(who, f'SELECT sum(id) FROM "{cat}"."{ns}".k2_storage_probe')
ok(a.error is None and a.rows == [[7]], f"and SELECT reads it back: {a.says()} {a.rows}")
trino.run(who, f'DROP TABLE IF EXISTS "{cat}"."{ns}".k2_storage_probe')
PY
rc=$?  # its exit, not a Traceback in its stderr: a kill leaves none
tee -a "$W/claims" < "$W/tools.claims"
[ "$rc" = 0 ] && [ -s "$W/tools.claims" ] || { claim 1 "the tools checks ran to their end (exit $rc)"; tail -n 8 "$W/tools.err"; }
finish

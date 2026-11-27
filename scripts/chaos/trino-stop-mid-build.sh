#!/usr/bin/env bash
# Plan 002's Trino stop (Verification: "the next run writes reconciled before it builds"; F25:
# "captures the environment names of the running dbt runner before it stops Trino, since the
# container is removed after a run"): on ONE throwaway lake (prefix trino-stop) from <release>,
# calendars and ECB built and published through the dbt wrapper, then ECB's wrapper again with the
# lake's Trino stopped under it once its `started` rows are written (dbt then still parses the
# project, so the stop lands inside the build), Trino started again, and the wrapper run once more.
# Each task the release's own code in the DAG factory's shape (scripts/lib/run-path-lib.sh).
#
#   bash scripts/chaos/trino-stop-mid-build.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 calendars builds through the wrapper and its audited tag moves
#   2 the good file lands, passes every check and is published to bronze main
#   3 the wrapper builds ECB on calendars' tested snapshot; a reader reads gold
#   4 the running dbt-runner's environment names are exactly the release's seven for the kind
#     (compiled/oneshots.json) and the image's own, no storage, Airflow, Docker or secret name, and
#     no value of it is a secret of the lake. Names are read, never printed with their values.
#   5 with Trino stopped mid-build the wrapper exits non-zero, its records are its two started
#     rows, and both tags stay where they were
#   6 the next run's first records are reconciled (one per output, under the stopped run's id, as
#     k2_dbt.reconcile writes them), then it builds and publishes
# On the throwaway the container's flags are run-path-lib.sh's, which mirror oneshot.py; the
# operator's own argv is held to the same names by docker/airflow/tests/test_oneshot.py. A killed
# run leaves the lake to `bash scripts/lib/throwaway-lake.sh down trino-stop`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start trino-stop "${1:-}" 6
rp_image
rp_lake
rp_calendars
rp_good
rp_build
build "$ID" "$ECB" rp-stop & builder=$!
probe wait-started ecb_fx_rates/rp-stop || die "the build wrote no started row"
# The names (never the values) of the running container's environment, against the release's list and the image's own.
python3 - "$REL/compiled/oneshots.json" "$S" "$STATE/secrets/s3.env" "$RP/env-check.json" \
  <(docker inspect --format '{{json .Config.Env}}' "$P-dbt-runner-ecb_fx_rates") \
  <(docker image inspect --format '{{json .Config.Env}}' "$IMAGE") <<'PY'
import json, re, sys
from pathlib import Path
oneshots, secrets, s3env, out, running, image = sys.argv[1:7]
env = dict(x.split("=", 1) for x in json.loads(Path(running).read_text() or "[]"))
own = {x.split("=", 1)[0] for x in json.loads(Path(image).read_text() or "[]")}
kind = set(json.loads(Path(oneshots).read_text())["oneshots"]["dbt-runner"]["env"])
values = [p.read_text().strip() for p in Path(secrets).rglob("*") if p.is_file() and p.suffix in (".password", ".key")]
values += [line.split("=", 1)[1] for line in Path(s3env).read_text().splitlines() if "=" in line]
Path(out).write_text(json.dumps({"names": sorted(env), "kind": sorted(kind), "image": sorted(own),
                                 # the names the run adds to the image's own (the base image's GPG_KEY is the public
                                 # fingerprint of Python's release-signing key, not a secret)
                                 "forbidden": sorted(n for n in set(env) - own if re.search(r"S3|AWS|AIRFLOW|DOCKER|MINIO|SECRET|PASSWORD|KEY", n)),
                                 "secretValues": sum(1 for v in env.values() for s in values if s and s in v)}))
PY
docker stop -t 5 "$P-trino" > /dev/null && echo "$P: Trino stopped under rp-stop's build"
wait "$builder"
{ docker start "$P-trino" > /dev/null && probe wait-trino; } || die "Trino did not come back"
echo "$P: Trino started again"
probe state stopped  # the tags are the catalog's; Trino is back for the counts
claim env
claim stopped
build "$ID" "$ECB" rp-rerun
probe state rerun
claim reconciled
finish

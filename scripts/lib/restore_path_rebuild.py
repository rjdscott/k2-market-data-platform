"""`make rebuild PRODUCT=<product>` on scripts/verify/restore-path.sh's throwaway lake: scripts/rebuild.py's
own rebuild(), on the host, with what differs on a throwaway put in two stand-ins.

    python3 scripts/lib/restore_path_rebuild.py <product>     # env P, NET, STATE, IMAGE, ID (the script's)

- The holds: rebuild() speaks to Trino through a DB-API connection; the throwaway publishes no Trino
  port, so each statement goes, with its parameters, to the helper container <P>-sql (the tools image
  on the lake's network, svc.operator's password and the CA mounted read-only), which runs it through
  k2runs.connect as svc.operator, the holds' one writer, as hold.py does from the live host.
- Airflow: there is none, so no DAG is paused, drained or unpaused (rebuild()'s calls are answered
  "paused already", "nothing runs"); the Spark steps are rebuild.Live's own, aimed at <P>-spark-iceberg;
  the full refresh (and the wrapper check before the lock) is the dbt-runner image built from this tree, run as run-path-lib.sh's build() runs
  a DAG build, with --full-refresh (rebuild.BUILD_PY's command), in place of the scheduler's oneshot.py.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rebuild as rb  # noqa: E402

E = os.environ
P, NET, STATE, IMAGE, RID = E["P"], E["NET"], Path(E["STATE"]), E["IMAGE"], E["ID"]
HELPER = f"{P}-sql"
SQL_PY = """\
import json, sys
from datetime import datetime
sys.path.insert(0, "/opt/k2/platform/runs")
import k2runs
d = json.load(sys.stdin)
params = [datetime.fromisoformat(p) if i in d["dt"] else p for i, p in enumerate(d["params"])]
conn = k2runs.connect(host="trino", port=8443, user="svc.operator", password_file="/run/k2-secrets/operator.password",
                      ca="/run/k2-secrets/tls/ca.pem")
cur = conn.cursor()
cur.execute(d["sql"], params)
print(json.dumps(cur.fetchall(), default=str))
"""


class Cursor:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        params = list(params)
        payload = json.dumps({"sql": sql, "dt": [i for i, p in enumerate(params) if isinstance(p, datetime)],
                              "params": [p.isoformat() if isinstance(p, datetime) else p for p in params]})
        done = subprocess.run(["docker", "exec", "-i", HELPER, "/opt/k2/venv/bin/python", "-c", SQL_PY], input=payload,
                              capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"svc.operator's statement failed: {done.stderr.strip()[-300:]}")
        self.rows = json.loads(done.stdout.strip().splitlines()[-1])

    def fetchall(self):
        return self.rows


class Conn:
    def cursor(self):
        return Cursor()


class IO(rb.Live):
    def _run(self, argv, stdin=None):
        return super()._run([f"{P}-spark-iceberg" if a == rb.SPARK else a for a in argv], stdin)

    def paused(self, dag):
        return True  # no Airflow: nothing to pause, and so nothing rebuild() unpauses

    def busy(self, dag):
        return []

    def running(self, name):
        return False

    def wrapper_help(self, image):
        return super().wrapper_help(IMAGE)  # the image this lake's builds run: the scratch release pins a placeholder

    def build(self, release_path, dag, product, run):
        rid = release_path.rsplit("/", 1)[1]
        rel = STATE / "releases" / rid
        doc = json.loads((rel / f"compiled/products/{product}/pipeline.json").read_text())["build"]
        k = json.loads((rel / "compiled/oneshots.json").read_text())["oneshots"]["dbt-runner"]
        s = STATE / "secrets/k2"
        mounts = [(rel, f"/k2/releases/{rid}"), (s / f"principals/{doc['writer']}.password", "/run/k2-secrets/builder.password"),
                  (s / f"principals/{doc['publisher']}.password", "/run/k2-secrets/publisher.password"),
                  (s / "principals/svc.runs.password", "/run/k2-secrets/runs.password"), (s / "tls/ca.pem", "/run/k2-secrets/tls/ca.pem")]
        argv = ["docker", "run", "--rm", "--name", rb.oneshot_name(dag).replace("k2-", f"{P}-", 1), "--label", f"k2.throwaway={P}",
                "--pull", "never", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "256", "--user", f"{os.getuid()}:{os.getgid()}",
                "--cpus", str(k["cpus"]), "--memory", str(k["memory"]), "--network", NET,
                "-e", "K2_TRINO_HOST=trino", "-e", "K2_TRINO_INTERNAL_PORT=8443", "-e", f"K2_TRINO_USER={doc['writer']}",
                "-e", f"K2_PUB_USER={doc['publisher']}", "-e", "K2_RUNS_USER=svc.runs", "-e", "K2_CATALOG_URI=http://lakekeeper:8181/catalog",
                "-e", f"K2_RELEASE={rid}", *(x for src, dst in mounts for x in ("--mount", f"type=bind,source={src},target={dst},readonly")),
                IMAGE, "k2-dbt", "build", "--release", rid, "--product", product, "--run-id", run, "--full-refresh"]
        done = subprocess.run(argv, stdout=subprocess.PIPE, text=True, errors="replace")
        print(done.stdout, end="")
        return done.returncode, rb.last_json(done.stdout)


def main(product):
    rel = STATE / "releases" / RID
    by_dag = {d["dagId"]: d["product"] for d in json.loads((rel / "compiled/airflow/dags.json").read_text())["dags"]}
    dag = next(d for d, p in by_dag.items() if p == product)
    try:
        return rb.rebuild(Conn(), rel, product, dag, "restore-path", IO(), datetime.now(timezone.utc))
    except rb.Refused as e:
        print(f"rebuild: REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

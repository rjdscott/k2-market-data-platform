"""Logins every 50 ms while trino/password.db is rewritten as fast as it can be: does a reader
ever see a torn file (HTTP 500 for everyone) or another user's hash beside a name (a login with
the wrong password taken)? The proof behind scripts/principal-secret.py's fixed slots.

    uv run --project platform/k2gen --locked -q --with bcrypt==5.0.0 \\
      python scripts/verify/password_db_hammer.py <prefix> [seconds] [--old]

Starts a throwaway Trino 484 (`<prefix>-trino` on `<prefix>-net`, 1.5 GiB, no port published)
whose file authenticator rereads password.db every second, with a CA and certificate from
scripts/tls.sh and 40 users, under $HOME/.k2-hammer-<prefix> (Docker Desktop shares $HOME). A
client container logs in every 50 ms as each user with its own password, and every fourth time
with the next user's, while this process rewrites the file through principal-secret's write():
a rotation, an addition and a removal in turn, each under the file's lock as every writer does.
--old rewrites it the former way instead (a line added and removed at the top, the whole file
written each time), the review's hammer, to show the client can see a torn read. Prints one JSON
line: rewrites, seconds, and the logins counted by kind and HTTP status. Removes everything it
made. Exit 1 when a right login was not 200 or a wrong one was not 401.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bcrypt

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("principal_secret", REPO / "scripts/principal-secret.py")
assert spec and spec.loader
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

TRINO = "trinodb/trino:484"
UV_IMAGE = next(line.split("=", 1)[1] for line in (REPO / "scripts/lib/throwaway-lake.sh").read_text().splitlines()
                if line.startswith("UV_IMAGE="))
CONFIG = """coordinator=true
node-scheduler.include-coordinator=true
http-server.http.enabled=false
http-server.https.enabled=true
http-server.https.port=8443
http-server.https.keystore.path=/run/k2-secrets/tls/trino.pem
http-server.authentication.type=PASSWORD
internal-communication.shared-secret=${ENV:K2_TRINO_SHARED_SECRET}
internal-communication.https.required=true
discovery.uri=https://127.0.0.1:8443
web-ui.enabled=false
"""
AUTH = "password-authenticator.name=file\nfile.password-file=/run/k2-secrets/trino/password.db\nfile.refresh-period=1s\n"
CLIENT = """
import base64, json, ssl, sys, time, urllib.error, urllib.request
ctx, users, counts = ssl.create_default_context(cafile="/ca.pem"), json.load(open("/users.json")), {}
def code(u, p):
    req = urllib.request.Request("https://trino:8443/v1/query", headers={"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError as e:
        return type(e).__name__
until, i = time.monotonic() + float(sys.argv[1]), 0
while time.monotonic() < until:
    u, p = users[i % len(users)]
    k = f"right {code(u, p)}"; counts[k] = counts.get(k, 0) + 1
    if i % 4 == 0:
        k = f"wrong-user {code(u, users[(i + 1) % len(users)][1])}"; counts[k] = counts.get(k, 0) + 1
    i += 1
    time.sleep(0.05)
print(json.dumps(counts, sort_keys=True))
"""


def docker(*args: str, **kw: object) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)  # type: ignore[call-overload]


def hashed(password: str) -> str:
    return "$2y$" + bcrypt.hashpw(password.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode().removeprefix("$2b$")


def main(prefix: str, seconds: float, old: bool) -> int:
    work, net, trino = Path.home() / f".k2-hammer-{prefix}", f"{prefix}-net", f"{prefix}-trino"
    if work.exists() or docker("inspect", trino).returncode == 0:
        sys.exit(f"hammer: {work} or {trino} exists; remove them first")
    secrets_dir, etc = work / "k2-secrets", work / "etc"
    try:
        etc.mkdir(parents=True)
        subprocess.run(["bash", str(REPO / "scripts/tls.sh"), "init"], env={**os.environ, "K2_SECRETS": str(secrets_dir)},
                       check=True, capture_output=True)
        (etc / "config.properties").write_text(CONFIG)
        (etc / "password-authenticator.properties").write_text(AUTH)
        (etc / "jvm.config").write_text("-server\n-Xmx768M\n-XX:+UseG1GC\n-XX:+ExitOnOutOfMemoryError\n-Djdk.attach.allowAttachSelf=true\n")
        (etc / "node.properties").write_text("node.environment=hammer\nnode.id=hammer\nnode.data-dir=/data/trino\n")
        users = [(f"u{i:02d}.hammer", secrets.token_urlsafe(16)) for i in range(40)]  # 40 slots: past one 4 KiB page
        db = secrets_dir / ps.PASSWORD_DB
        text = "".join(f"{u}:{hashed(p)}\n" for u, p in users)
        with ps.locked(db) as fd:
            ps.write(fd, text) if not old else ps.rewrite(fd, text.encode())
        (work / "users.json").write_text(json.dumps(users))
        (work / "client.py").write_text(CLIENT)
        docker("network", "create", net, check=True)
        docker("run", "-d", "--name", trino, "--network", net, "--network-alias", "trino", "--memory", "1536m", "--cpus", "2",
               "--no-healthcheck", "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/data/trino:mode=1777",
               "-e", f"K2_TRINO_SHARED_SECRET={secrets.token_hex(32)}", "-v", f"{etc}:/etc/hammer:ro",
               "-v", f"{secrets_dir}/tls/trino.pem:/run/k2-secrets/tls/trino.pem:ro", "-v", f"{secrets_dir}/trino:/run/k2-secrets/trino:ro",
               TRINO, "/usr/lib/trino/bin/launcher", "run", "--etc-dir", "/etc/hammer", check=True)
        for _ in range(90):
            if '"starting":false' in docker("exec", trino, "curl", "-sk", "https://localhost:8443/v1/info").stdout:
                break
            time.sleep(2)
        else:
            sys.exit("hammer: Trino did not start")
        rot, add = [hashed("rotate-a"), hashed("rotate-b")], hashed("add")
        client = subprocess.Popen(["docker", "run", "--rm", "--network", net, "-v", f"{secrets_dir}/tls/ca.pem:/ca.pem:ro",
                                   "-v", f"{work}/client.py:/client.py:ro", "-v", f"{work}/users.json:/users.json:ro", UV_IMAGE,
                                   "python3", "/client.py", str(seconds)], stdout=subprocess.PIPE, text=True)
        time.sleep(2)
        t0, n = time.monotonic(), 0
        while time.monotonic() - t0 < seconds - 4:
            with ps.locked(db) as fd:
                lines = ps.read(fd).splitlines(keepends=True)
                if old:
                    body = [x for x in lines if not x.startswith("churn-add:")]
                    ps.rewrite(fd, "".join(([f"churn-add:{add}\n"] if n % 2 == 0 else []) + body).encode())
                elif n % 3 == 0:  # a rotation
                    ps.write(fd, "".join([x for x in lines if not x.startswith("churn-rot:")] + [f"churn-rot:{rot[n // 3 % 2]}\n"]))
                elif n % 3 == 1:  # an addition
                    ps.write(fd, "".join(lines + [f"churn-add:{add}\n"]))
                else:  # a removal
                    ps.write(fd, "".join(x for x in lines if not x.startswith("churn-add:")))
            n += 1
        took = round(time.monotonic() - t0, 1)
        logins = json.loads(client.communicate()[0])
        print(json.dumps({"write": "old" if old else "slots", "rewrites": n, "seconds": took, "logins": logins}))
        return 0 if set(logins) <= {"right 200", "wrong-user 401"} else 1
    finally:
        docker("rm", "-f", "-v", trino)
        docker("network", "rm", net)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--old"]
    if not args:
        sys.exit(__doc__)
    sys.exit(main(args[0], float(args[1]) if len(args) > 1 else 180, "--old" in sys.argv))

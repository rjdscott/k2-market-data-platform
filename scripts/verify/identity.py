"""The rows of scripts/verify/identity.sh that speak to Trino itself, over its client protocol.

Run by identity.sh in a one-off container on Trino's network, which reaches it by name as
K2_TRINO_URL (https://trino:8443), verified against ${K2_SECRETS}/tls/ca.pem alone. Mounted, and
nothing else of ${K2_SECRETS}: tls/ca.pem and the login principal's password file read-only,
trino/ read-write (the probe's line). K2_IDENTITY_FIXTURES holds identity.sh's test
certificates; none is Trino's, and the CA that signed `other-*` is not the platform's.

Besides identity.sh's I01 to I13, this file adds I19: the Web UI is off, so its form login gives
no session (a session outlived a password's removal, ADR-060's Outcome).

The probe, `identity-probe`, is a user no document can declare (a principal id is p.*, svc.*,
dbt_* or pub_*), so no rule names it and it can run nothing; its password.db line is written as
scripts/principal-secret.py writes one (in place, under the file's lock, by that script's own
functions) and removed by that script's `remove`, so a rotation and a removal are measured on a
live Trino without touching any principal's password. A leftover line is removed first.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import bcrypt
from k2gen import model
from k2gen.emit import trino_identity

URL = os.environ["K2_TRINO_URL"]
HOST = urllib.parse.urlsplit(URL).hostname or ""
PORT = urllib.parse.urlsplit(URL).port or 443
ROOT = Path(os.environ["K2_SECRETS"])
CA = ROOT / "tls/ca.pem"
USER = os.environ["K2_IDENTITY_USER"]
FIXTURES = Path(os.environ["K2_IDENTITY_FIXTURES"])
PROBE = "identity-probe"
REFRESH = float(trino_identity.REFRESH.removesuffix("s"))
WITHIN = REFRESH + 1.5  # one refresh period, and the polling and request time around it
CTX = ssl.create_default_context(cafile=str(CA))

spec = importlib.util.spec_from_file_location("principal_secret", Path(__file__).resolve().parents[1] / "principal-secret.py")
assert spec and spec.loader
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

rows: list[tuple[str, bool, str, str]] = []


def record(rid: str, passed: bool, assertion: str, detail: object) -> None:
    rows.append((rid, passed, assertion, str(detail)))
    print(f"{'PASS' if passed else 'FAIL'} {rid}: {assertion}\n      {detail}", flush=True)


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def statement(auth: str | None, user: str, sql: str = "SELECT current_user", ctx: ssl.SSLContext = CTX) -> tuple[int, str | None, list]:
    """(HTTP status of the first request, Trino's error or None, rows) for one statement."""
    headers = {"X-Trino-User": user} | ({"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{URL}/v1/statement", sql.encode(), headers), context=ctx, timeout=15) as r:
            status, doc = r.status, json.load(r)
        rows: list = []
        deadline = time.monotonic() + 60
        while True:
            rows += doc.get("data") or []
            if "error" in doc:
                return status, doc["error"].get("message"), rows
            if not doc.get("nextUri") or time.monotonic() > deadline:
                return status, None, rows
            next_headers = {"Authorization": auth} if auth else {}
            with urllib.request.urlopen(urllib.request.Request(doc["nextUri"], headers=next_headers), context=ctx, timeout=15) as r:
                doc = json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:120], []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def web_ui_login(user: str, password: str) -> str:
    """What the Web UI's form login answers: `<status> -> <Location>`, and `session` when it sets
    the UI's session cookie."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=CTX))
    body = urllib.parse.urlencode({"username": user, "password": password, "redirectPath": ""}).encode()
    req = urllib.request.Request(f"{URL}/ui/legacy/login", body, {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = opener.open(req, timeout=15)
    except urllib.error.HTTPError as e:
        r = e
    session = "Trino-UI-Token" in (r.headers.get("Set-Cookie") or "")
    return f"{r.status} -> {r.headers.get('Location')}" + (", session" if session else "")


def handshake(address: tuple[str, int], name: str, ctx: ssl.SSLContext = CTX) -> str:
    """`ok <serial>`, or the client's verification error."""
    try:
        with socket.create_connection(address, 5) as s, ctx.wrap_socket(s, server_hostname=name) as t:
            return f"ok {t.getpeercert()['serialNumber']}"
    except ssl.SSLCertVerificationError as e:
        return f"refused: {e.verify_message}"


@contextlib.contextmanager
def tls_server(pem: Path):
    """A local TLS server that presents `pem` (a key and its certificate) to whoever connects."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pem))
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            with contextlib.suppress(OSError), ctx.wrap_socket(conn, server_side=True) as t:
                t.recv(1)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()
    finally:
        stop.set()
        thread.join()
        listener.close()


def probe_line(password: str | None) -> None:
    """The probe's password.db line set to `password`, or removed by principal-secret's `remove`."""
    if password is None:
        with contextlib.redirect_stderr(io.StringIO()):
            ps.remove(ROOT, PROBE)
        return
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode()
    with ps.locked(ROOT / ps.PASSWORD_DB) as fd:
        lines = [x for x in ps.read(fd).splitlines(keepends=True) if x.split(":", 1)[0] != PROBE]
        ps.write(fd, "".join(lines) + f"{PROBE}:$2y${hashed.removeprefix('$2b$')}\n")


def logs_in(password: str) -> bool:
    """The probe is authenticated (its statement may still be refused: no rule names it)."""
    return statement(basic(PROBE, password), PROBE, "SELECT 1")[0] != 401


def until(cond, limit: float = 30) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if cond():
            return round(time.monotonic() - t0, 2)
        time.sleep(0.2)
    return None


def main() -> int:
    password = (ROOT / model.password_file(USER)).read_text().strip()
    other = "svc.platform" if USER != "svc.platform" else "svc.catalog"

    st, err, _ = statement(None, USER)
    record("I01.no-credential", st == 401, f"a statement with no password, X-Trino-User {USER}, is refused 401", f"HTTP {st}: {err}")
    wrong, unknown = statement(basic(USER, secrets.token_urlsafe(32)), USER), statement(basic("nobody-here", password), "nobody-here")
    record("I02.wrong-password", wrong[0] == 401 and unknown[0] == 401,
           f"{USER} with a wrong password, and a user with no line, are refused 401", f"{wrong[:2]}; {unknown[:2]}")

    plain = []
    for port in (PORT, 8080):
        try:
            with socket.create_connection((HOST, port), 5) as s:
                s.sendall(f"GET /v1/info HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode())
                head = s.recv(64)
            plain.append((port, "answered " + (head.split(b"\r\n")[0].decode(errors="replace") or "nothing")))
        except OSError as e:
            plain.append((port, f"{type(e).__name__}"))
    record("I03.plain-http", all(not d.startswith("answered HTTP/") for _, d in plain),
           f"plain HTTP gets no HTTP answer: not on the TLS port {PORT}, nothing listening on 8080", plain)

    st, err, data = statement(basic(USER, password), USER)
    record("I04.login", st == 200 and err is None and data == [[USER]],
           f"{USER} with its own password, by name, verified against the CA: current_user is {USER}", f"HTTP {st}, {err or data}")
    ui = web_ui_login(USER, password)
    record("I19.web-ui-off", ui.endswith("/ui/static/disabled.html"),
           f"the Web UI is off: its form login, with {USER}'s right password, sends to Trino's disabled page and gives no session",
           ui)

    probe = secrets.token_urlsafe(32)
    probe_line(None)
    probe_line(probe)
    t_add = until(lambda: logs_in(probe))
    as_other = statement(basic(USER, password), other)
    as_user = statement(basic(PROBE, probe), USER) if t_add is not None else (0, "probe never logged in", [])
    record("I05.impersonation", all(e and "cannot impersonate" in e and not d for _, e, d in (as_other, as_user)),
           f"X-Trino-User other than the login is refused: {USER} as {other}, the probe as {USER}",
           f"{as_other[1]}; {as_user[1]}")

    ip = socket.gethostbyname(HOST)
    by_ip, by_other_name = handshake((ip, PORT), ip), handshake((ip, PORT), "not-a-trino-name.invalid")
    record("I06.by-address", by_ip.startswith("refused"),
           "a client that connects by IP address refuses what Trino shows it (its internal certificate)", f"{ip}: {by_ip}")
    record("I07.wrong-name", by_other_name.startswith("refused"),
           "a client asking for a name the certificate does not carry refuses what Trino shows it", by_other_name)

    other_ca = ssl.create_default_context(cafile=str(FIXTURES / "other-ca.pem"))
    with tls_server(FIXTURES / "other-valid.pem") as a:
        rogue, control = handshake(a, HOST), handshake(a, HOST, other_ca)
    with tls_server(FIXTURES / "other-expired.pem") as a:
        expired = handshake(a, HOST, other_ca)
    with tls_server(FIXTURES / "other-wrong-name.pem") as a:
        wrong_name = handshake(a, HOST, other_ca)
    record("I08.other-ca", rogue.startswith("refused") and control.startswith("ok"),
           f"a server with a valid certificate for {HOST} from another CA is refused; the control, trusting that CA, connects",
           f"{rogue}; control: {control}")
    record("I09.expired", expired == "refused: certificate has expired",
           f"an expired certificate for {HOST}, from a CA the client trusts, is refused", expired)
    record("I10.name-mismatch", wrong_name.startswith("refused: Hostname mismatch"),
           f"a valid certificate for another name, from a CA the client trusts, is refused when it asks for {HOST}", wrong_name)

    rotated = secrets.token_urlsafe(32)
    if t_add is not None:
        probe_line(rotated)
        t_rot = until(lambda: not logs_in(probe) and logs_in(rotated))
    else:
        t_rot = None
    record("I11.password-rotation", t_add is not None and t_rot is not None and t_add <= WITHIN and t_rot <= WITHIN,
           f"a password line written in place is taken, and its rotation (old refused, new taken) within one {trino_identity.REFRESH} refresh",
           f"new line taken after {t_add} s, rotation after {t_rot} s (limit {WITHIN} s)")
    probe_line(None)
    t_rm = until(lambda: not logs_in(rotated))
    record("I12.removal", t_rm is not None and t_rm <= WITHIN,
           f"a principal removed by principal-secret.py's remove is refused within one {trino_identity.REFRESH} refresh",
           f"refused after {t_rm} s (limit {WITHIN} s)")

    left = [x for x in (ROOT / ps.PASSWORD_DB).read_text().splitlines() if x.split(":", 1)[0] == PROBE]
    record("I13.probe-gone", not left, "no probe line is left in password.db", f"{len(left)} line(s)")
    return 1 if [r for r in rows if not r[1]] else 0


if __name__ == "__main__":
    sys.exit(main())

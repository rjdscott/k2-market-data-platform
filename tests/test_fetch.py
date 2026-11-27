"""docker/fetch/fetch.py: the fetch container's address guard and run contract (ADR-036 parts 2
and 3; docs/development/phase-001-interfaces.md, "The fetch container").

No network, by construction: the autouse fixture `no_network` makes every real lookup and
socket connect raise AssertionError, which nothing in fetch.py catches, so a path that reached
the network fails the test loudly. `web` then fakes the two things the guard touches, the
resolver (socket.getaddrinfo) and the TCP connect (socket.create_connection), plus the TLS wrap.
http.client itself is real: it writes the request into a fake socket and parses the response
bytes the fake serves. A local server would sit on loopback, which the guard must refuse, so the
fake is injected at the connection boundary instead. Addresses are public ones nothing dials.
"""

import errno
import hashlib
import importlib.util
import io
import ipaddress
import itertools
import json
import os
import socket
import ssl
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fetch", ROOT / "docker" / "fetch" / "fetch.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

HOST, MIRROR = "files.example.invalid", "mirror.example.invalid"
PUBLIC, PUBLIC_2, PUBLIC_6 = "93.184.215.14", "1.1.1.1", "2606:4700:4700::1111"
URL = f"https://{HOST}/rates-{{{{ds}}}}.xml?v=QUERYMARK"
FIRST = f"https://{HOST}/rates-2026-11-27.xml?v=QUERYMARK"
RUN = "scheduled__2026-11-27T16:00:00+00:00"
BODY = b"<rates><rate currency='AAA' value='1.5'/></rates>"
PARSER = "def parse(data):\n    return [{'bytes': len(data), 'head': data[:7].decode()}]\n"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a real lookup or socket connect was attempted")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


class Stream(io.RawIOBase):
    """Bytes from an iterable of chunks, pulled lazily: a 64 MiB body is never in memory. As the
    file socket.makefile() returns, it holds a reference on its wire: closing it drops that."""

    def __init__(self, chunks, wire=None):
        self.chunks, self.left, self.wire = iter(chunks), memoryview(b""), wire

    def readable(self):
        return True

    def close(self):
        if not self.closed and self.wire:
            self.wire.decref()
        super().close()

    def readinto(self, buf):
        if self.wire:
            self.wire.alive()  # recv on a closed descriptor
        while not self.left:
            chunk = next(self.chunks, None)
            if chunk is None:
                return 0
            self.left = memoryview(chunk)
        n = min(len(buf), len(self.left))
        buf[:n], self.left = self.left[:n], self.left[n:]
        return n


class Wire:
    """One fake TCP connection to `address`: records the request, then serves the page for the
    URL its request line and Host header name. It closes as a real socket does: close() with a
    makefile() file still open only marks it, the descriptor goes when the last of the two goes,
    and settimeout or recv on it after that raises EBADF."""

    def __init__(self, web, address):
        self.web, self.address, self.sent, self.timeouts, self.tls_name = web, address, b"", [], None
        self.closed, self.refs, self.fd = False, 0, True

    def alive(self):
        if not self.fd:
            raise OSError(errno.EBADF, "Bad file descriptor")

    def decref(self):
        self.refs -= 1
        if self.closed and self.refs <= 0:
            self.fd = False

    def sendall(self, data):
        self.alive()
        self.sent += data

    def settimeout(self, value):
        self.alive()
        self.timeouts.append(value)

    def setsockopt(self, *args):
        pass

    def close(self):
        self.closed = True
        if self.refs <= 0:
            self.fd = False

    def makefile(self, mode, *args, **kwargs):
        self.alive()
        line, *headers = self.sent.split(b"\r\n\r\n")[0].decode().split("\r\n")
        host = next(h.split(":", 1)[1].strip() for h in headers if h.lower().startswith("host:"))
        url = f"https://{host}{line.split()[1]}"
        self.web.asked.append(url)
        self.web.requests.append({"url": url, "address": self.address, "tls_name": self.tls_name,
                                  "headers": headers})
        self.refs += 1
        return io.BufferedReader(Stream(self.web.pages[url], self))


class FakeTLS:
    """Stands in for fetch.TLS: records the name each connection verifies against."""

    def wrap_socket(self, sock, server_hostname=None):
        sock.tls_name = server_hostname
        return sock


class Web:
    def __init__(self):
        self.dns, self.pages, self.asked, self.requests, self.lookups, self.connects, self.wires = {}, {}, [], [], [], [], []
        self.down = set()  # addresses whose connect is refused

    def host(self, name, *answers):
        """`name` answers each lookup with the next list of addresses, the last one repeating."""
        self.dns[name] = [list(a) if isinstance(a, (list, tuple)) else [a] for a in answers]

    def getaddrinfo(self, host, port, *args, **kwargs):
        n = self.lookups.count(host)
        self.lookups.append(host)
        answers = self.dns[host]
        out = []
        for a in answers[min(n, len(answers) - 1)]:
            v6 = ":" in a
            out.append((socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                        (a, port, 0, 0) if v6 else (a, port)))
        return out

    def create_connection(self, address, timeout=None, *args, **kwargs):
        host, port = address
        try:
            ipaddress.ip_address(host)
        except ValueError:  # a name, as a library that resolves again would pass: resolve it, as it would
            host = self.getaddrinfo(host, port)[0][4][0]
        self.connects.append((host, port, timeout))
        if host in self.down:
            raise ConnectionRefusedError(111, "Connection refused")
        self.wires.append(Wire(self, host))
        return self.wires[-1]


@pytest.fixture
def web(monkeypatch, tmp_path):
    w = Web()
    monkeypatch.setattr(socket, "getaddrinfo", w.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", w.create_connection)
    monkeypatch.setattr(F, "TLS", FakeTLS())
    monkeypatch.setattr(F, "RELEASES", tmp_path / "releases")
    monkeypatch.setattr(F, "SPOOL", tmp_path / "spool")
    w.host(HOST, PUBLIC)
    w.host(MIRROR, PUBLIC_2)
    w.pages[FIRST] = ok()
    release(tmp_path)
    return w


def ok(body=BODY, headers=None):
    head = {"Content-Length": len(body), "Content-Type": "application/xml", **(headers or {})}
    return [("HTTP/1.1 200 OK\r\n" + "".join(f"{k}: {v}\r\n" for k, v in head.items()) + "\r\n").encode(), body]


def moved(location, status=302):
    return [f"HTTP/1.1 {status} Moved\r\nLocation: {location}\r\nContent-Length: 0\r\n\r\n".encode()]


def streamed(chunks):
    """A 200 with no Content-Length, its body read to the close: only a running count can cap it."""
    return itertools.chain([b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"], chunks)


def release(tmp_path, parser=PARSER, audits=(), **connector):
    root = tmp_path / "releases" / "r1"
    pipeline = root / "compiled" / "products" / "fx-test.rates" / "pipeline.json"
    pipeline.parent.mkdir(parents=True, exist_ok=True)
    spec = {"kind": "https-file", "url": URL, "allowHosts": [HOST, MIRROR], "format": "xml", "parser": "rates",
            **connector}
    pipeline.write_text(json.dumps({"product": "fx-test.rates", "fence": "rates", "domain": "fx-test",
                                    "dagId": "rates", "connector": spec,
                                    "bronze": [{"table": "lake.rates.bronze_rates", "audits": list(audits)}]}))
    module = root / "platform" / "domains" / "fx-test" / "parsers" / "rates.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(parser)


def run(run_id=RUN) -> int:
    return F.main(["--release", "r1", "--product", "fx-test.rates", "--run-id", f"rates/{run_id}",
                   "--ds", "2026-11-27"])


def spool(tmp_path, run_id=RUN) -> Path:
    return tmp_path / "spool" / "rates" / run_id


def nothing_left(tmp_path):
    """The DAG's spool holds no entry and no half-written temp directory."""
    dag = tmp_path / "spool" / "rates"
    return not dag.exists() or list(dag.iterdir()) == []


# ── the network guard of these tests ──────────────────────────────────────


def test_the_guard_of_these_tests_fails_loudly_on_a_real_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "RELEASES", tmp_path / "releases")
    monkeypatch.setattr(F, "SPOOL", tmp_path / "spool")
    release(tmp_path)
    with pytest.raises(AssertionError, match="real lookup or socket"):
        run()


def test_tls_verification_is_on_in_the_real_context():
    assert F.TLS.verify_mode == ssl.CERT_REQUIRED and F.TLS.check_hostname is True


# ── a good fetch ──────────────────────────────────────────────────────────


def test_a_fetch_spools_raw_and_record_then_rows(web, tmp_path, capsys):
    web.pages[FIRST] = ok(headers={"ETag": '"v7"', "Last-Modified": "Thu, 26 Nov 2026 16:00:00 GMT"})
    assert run() == 0
    entry = spool(tmp_path)
    raw = (entry / "raw.xml").read_bytes()
    record = json.loads((entry / "fetch.json").read_text())
    assert raw == BODY
    assert record == {"url": FIRST, "finalUrl": FIRST, "sha256": hashlib.sha256(BODY).hexdigest(), "bytes": len(BODY),
                      "fetchedAt": record["fetchedAt"], "httpStatus": 200, "address": PUBLIC,
                      "contentType": "application/xml", "etag": '"v7"',
                      "lastModified": "Thu, 26 Nov 2026 16:00:00 GMT"}
    assert datetime.fromisoformat(record["fetchedAt"]).utcoffset().total_seconds() == 0
    assert [json.loads(line) for line in (entry / "rows.jsonl").read_text().splitlines()] == \
        [{"bytes": len(BODY), "head": "<rates>"}]
    assert sorted(p.name for p in entry.iterdir()) == ["audits.json", "fetch.json", "raw.xml", "rows.jsonl"]
    assert json.loads((entry / "audits.json").read_text()) == {"audits": []}  # none declared
    assert entry.stat().st_mode & 0o777 == 0o755  # the Spark task reads it as another user
    out = capsys.readouterr()
    assert "files.example.invalid/rates-2026-11-27.xml" in out.out
    assert "QUERYMARK" not in out.out + out.err  # host and path logged, never the query


def chunked(body, close=False):
    head = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n" + ("Connection: close\r\n" if close else "")
    half = len(body) // 2
    return [f"{head}\r\n".encode(), *(b"%x\r\n%s\r\n" % (len(c), c) for c in (body[:half], body[half:]) if c),
            b"0\r\n\r\n"]


SHAPES = {
    "length, keep-alive": lambda b: ok(b),
    "length, Connection: close": lambda b: ok(b, {"Connection": "close"}),  # the live failure of 2026-11-27
    "HTTP/1.0, length": lambda b: [ok(b)[0].replace(b"HTTP/1.1", b"HTTP/1.0", 1), b],  # closes by default
    "chunked, keep-alive": lambda b: chunked(b),
    "chunked, Connection: close": lambda b: chunked(b, close=True),
    "no length, read to the close": lambda b: streamed([b]),
}


@pytest.mark.parametrize("body", [BODY, b""], ids=["body", "empty"])
@pytest.mark.parametrize("shape", SHAPES)
def test_every_response_shape_downloads_whole(web, tmp_path, shape, body):
    """A response that closes its connection hands the socket to the response, which closes it
    at the body's end: the read loop must stop there, never set a timeout on a closed socket."""
    web.pages[FIRST] = SHAPES[shape](body)
    assert run() == 0
    assert (spool(tmp_path) / "raw.xml").read_bytes() == body
    assert json.loads((spool(tmp_path) / "fetch.json").read_text())["bytes"] == len(body)


@pytest.mark.parametrize("shape", SHAPES)
def test_a_redirect_that_closes_then_any_shape_on_a_second_connection(web, tmp_path, shape):
    web.pages[FIRST] = [moved(f"https://{MIRROR}/rates.xml")[0].replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n")]
    web.pages[f"https://{MIRROR}/rates.xml"] = SHAPES[shape](BODY)
    assert run() == 0
    assert len(web.wires) == 2 and (spool(tmp_path) / "raw.xml").read_bytes() == BODY


@pytest.mark.parametrize("close", [False, True])
def test_a_body_short_of_its_content_length_is_a_task_error(web, tmp_path, capsys, close):
    web.pages[FIRST] = ok(BODY, {"Content-Length": len(BODY) + 10, **({"Connection": "close"} if close else {})})
    assert run() == F.EXIT_ERROR
    assert nothing_left(tmp_path) and "short of its Content-Length" in capsys.readouterr().err


def test_a_socket_error_mid_body_is_still_a_task_error(web, tmp_path, capsys):
    def reset():
        yield BODY[:5]
        raise ConnectionResetError(104, "Connection reset by peer")
    web.pages[FIRST] = streamed(reset())
    assert run() == F.EXIT_ERROR
    assert nothing_left(tmp_path) and "ConnectionResetError" in capsys.readouterr().err


def test_the_connection_goes_to_the_checked_address_with_tls_and_host_on_the_name(web):
    assert run() == 0
    assert web.connects == [(PUBLIC, 443, F.CONNECT_TIMEOUT_S)]
    (request,) = web.requests
    assert request["address"] == PUBLIC and request["tls_name"] == HOST
    assert f"Host: {HOST}" in request["headers"]
    assert set(web.wires[0].timeouts) == {F.READ_TIMEOUT_S}  # at connect, then before each read with time to spare


def test_a_rebind_between_check_and_connect_cannot_move_the_connection(web):
    web.host(HOST, PUBLIC, "10.0.0.7")  # public for the first lookup, private for any after it
    assert run() == 0
    assert web.lookups == [HOST]  # resolved once
    assert web.connects == [(PUBLIC, 443, F.CONNECT_TIMEOUT_S)]  # to the address checked, never 10.0.0.7


def test_the_next_checked_address_is_tried_when_one_refuses(web, tmp_path):
    web.host(HOST, (PUBLIC_6, PUBLIC))
    web.down.add(PUBLIC_6)
    assert run() == 0
    assert [c[0] for c in web.connects] == [PUBLIC_6, PUBLIC]
    assert json.loads((spool(tmp_path) / "fetch.json").read_text())["address"] == PUBLIC


def test_proxy_variables_are_ignored(web, monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "http://10.0.0.9:3128")
    assert run() == 0
    assert web.connects == [(PUBLIC, 443, F.CONNECT_TIMEOUT_S)]
    assert not [n for n in os.environ if n.lower().endswith("_proxy")]  # gone before the parser runs


def test_an_idna_host_is_normalised_before_the_allow_list(web, tmp_path):
    puny = "xn--bcher-kva.example.invalid"
    release(tmp_path, url="https://Bücher.example.invalid/f.xml", allowHosts=[puny])
    web.host(puny, PUBLIC)
    web.pages[f"https://{puny}/f.xml"] = ok()
    assert run() == 0
    assert web.requests[0]["tls_name"] == puny


# ── refusals: exit 20, nothing requested past the refusal, nothing spooled ──


@pytest.mark.parametrize("target, why", [
    (f"http://{MIRROR}/rates.xml", f"http://{MIRROR}: not https"),
    ("https://minio/k2-landing/x?X-Amz-Signature=secret", "https://minio: not in allowHosts"),
    ("https://lakekeeper.invalid/catalog", "https://lakekeeper.invalid: not in allowHosts"),
    (f"https://user:secret@{MIRROR}/x?token=secret", f"https://{MIRROR}: userinfo or a port other than 443"),
    (f"https://{MIRROR}:9000/x", f"https://{MIRROR}: userinfo or a port other than 443"),
    ("//minio/x", "https://minio: not in allowHosts"),  # scheme-relative
])
def test_a_redirect_off_the_rules_is_refused(web, tmp_path, capsys, target, why):
    web.pages[FIRST] = moved(target)
    assert run() == 20
    assert web.asked == [FIRST] and web.lookups == [HOST]  # the target never resolved, never asked
    assert nothing_left(tmp_path)
    err = capsys.readouterr().err
    assert f"fetch refused {why}" in err
    assert "secret" not in err and "k2-landing" not in err  # scheme and host only


NON_PUBLIC = ["10.0.0.7", "127.0.0.1", "169.254.169.254", "192.168.1.1", "172.16.0.1", "100.64.0.1", "0.0.0.0",
              "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1",
              "fec0::1", "ff02::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1",
              "2001::1", "192.0.2.1"]


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_a_host_resolving_to_a_non_public_address_is_refused(web, tmp_path, address):
    web.host(HOST, address)
    assert run() == 20
    assert web.connects == [] and nothing_left(tmp_path)


@pytest.mark.parametrize("address", ["10.0.0.7", "fd00::7", "::ffff:192.168.0.1"])
def test_a_redirect_to_an_allowed_host_on_a_non_public_address_is_refused(web, tmp_path, address):
    web.host(MIRROR, address)
    web.pages[FIRST] = moved(f"https://{MIRROR}/rates.xml")
    assert run() == 20
    assert [c[0] for c in web.connects] == [PUBLIC] and nothing_left(tmp_path)


def test_one_private_address_among_public_ones_refuses_the_host(web, tmp_path):
    web.host(HOST, (PUBLIC, PUBLIC_6, "10.0.0.7"))
    assert run() == 20
    assert web.connects == [] and nothing_left(tmp_path)


def test_a_v4_mapped_public_address_is_connected_as_ipv4(web):
    web.host(HOST, f"::ffff:{PUBLIC}")
    assert run() == 0
    assert web.connects == [(PUBLIC, 443, F.CONNECT_TIMEOUT_S)]


@pytest.mark.parametrize("url", [
    f"http://{HOST}/f.xml",
    f"https://{HOST}@evil.invalid/f.xml",
    f"https://{HOST}:8443/f.xml",
    f"https://{PUBLIC}/f.xml",  # an IP literal not listed
    "https://other.example.invalid/f.xml",
    f"https://{HOST}/f.xml\r\nX-Injected: 1",
])
def test_a_source_url_off_the_rules_is_refused_before_any_lookup(web, tmp_path, url):
    release(tmp_path, url=url)
    assert run() == 20
    assert web.lookups == [] and web.connects == [] and nothing_left(tmp_path)


def test_an_ip_literal_listed_in_allow_hosts_is_still_address_checked(web, tmp_path):
    release(tmp_path, url="https://10.0.0.7/f.xml", allowHosts=["10.0.0.7"])
    web.host("10.0.0.7", "10.0.0.7")
    assert run() == 20 and web.connects == []


def test_five_redirects_are_followed(web, tmp_path):
    hops = [f"https://{MIRROR}/hop{i}" for i in range(1, 6)]
    web.pages[FIRST] = moved(hops[0])
    for here, there in zip(hops, hops[1:]):
        web.pages[here] = moved(there.removeprefix(f"https://{MIRROR}"), 301)  # relative Location
    web.pages[hops[-1]] = ok()
    assert run() == 0
    assert web.asked == [FIRST, *hops]
    assert json.loads((spool(tmp_path) / "fetch.json").read_text())["finalUrl"] == hops[-1]


def test_the_sixth_redirect_is_refused(web, tmp_path, capsys):
    hops = [f"https://{MIRROR}/hop{i}" for i in range(1, 7)]
    web.pages[FIRST] = moved(hops[0])
    for here, there in zip(hops, hops[1:]):
        web.pages[here] = moved(there, 307)
    assert run() == 20
    assert web.asked == [FIRST, *hops[:5]]  # the sixth target never requested
    assert nothing_left(tmp_path)
    assert f"fetch refused https://{MIRROR}: more than 5 redirects" in capsys.readouterr().err


# ── size, time, status ────────────────────────────────────────────────────


def test_a_body_over_64_mib_is_refused_mid_stream(web, tmp_path):
    web.pages[FIRST] = streamed(itertools.chain(itertools.repeat(bytes(2**16), 2**10), [b"x"]))  # 64 MiB + 1
    assert run() == 20
    assert nothing_left(tmp_path)


def test_a_content_length_over_the_cap_is_refused_before_the_body(web, tmp_path):
    pulled = []

    def body():
        pulled.append(1)
        yield b"x"

    web.pages[FIRST] = itertools.chain([f"HTTP/1.1 200 OK\r\nContent-Length: {F.MAX_BYTES + 1}\r\n\r\n".encode()],
                                       body())
    assert run() == 20
    assert pulled == [] and nothing_left(tmp_path)


@pytest.mark.parametrize("size, code", [(1000, 0), (1001, 20)])
def test_the_connector_max_bytes_caps_lower(web, tmp_path, size, code):
    release(tmp_path, maxBytes=1000)
    web.pages[FIRST] = streamed([bytes(size)])
    assert run() == code
    assert (spool(tmp_path) / "raw.xml").exists() == (code == 0)


def test_the_overall_deadline_stops_a_slow_body(web, tmp_path, monkeypatch, capsys):
    clock = itertools.count(0, F.DEADLINE_S // 2 + 1)
    monkeypatch.setattr(F, "monotonic", lambda: next(clock))
    web.pages[FIRST] = streamed(itertools.repeat(bytes(2**16), 8))
    assert run() == 1
    assert nothing_left(tmp_path) and "deadline" in capsys.readouterr().err


def test_a_trickling_body_stops_at_the_deadline_not_after_a_full_chunk(web, tmp_path, monkeypatch, capsys):
    """F7: one byte every 29 s never trips the 30 s read timeout. A blocking read(64 KiB) took
    65536 of them (about 22 days) before the deadline was looked at; now every read is one
    read1 under a socket timeout cut to the time left, so the fetch stops at the deadline."""
    clock = [0.0]
    monkeypatch.setattr(F, "monotonic", lambda: clock[0])

    def trickle():
        for _ in range(2**16 + 10):
            clock[0] += 29
            yield b"x"
    web.pages[FIRST] = streamed(trickle())
    assert run() == F.EXIT_ERROR
    assert clock[0] <= F.DEADLINE_S + F.READ_TIMEOUT_S, f"stopped at {clock[0]} s"
    assert nothing_left(tmp_path) and "deadline" in capsys.readouterr().err
    timeouts = web.wires[-1].timeouts
    assert max(timeouts) == F.READ_TIMEOUT_S and timeouts[-1] == F.DEADLINE_S - 10 * 29  # the time left, 10 s


def test_an_http_error_is_a_task_error(web, tmp_path):
    web.pages[FIRST] = [b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"]
    assert run() == 1
    assert nothing_left(tmp_path)


# ── the spool: kept before the parse, never overwritten ───────────────────


def test_raw_and_record_are_spooled_before_the_parser_runs_and_kept_when_it_fails(web, tmp_path):
    seen = tmp_path / "seen.txt"
    entry = spool(tmp_path)
    release(tmp_path, parser=(
        "import os, pathlib\n"
        "def parse(data):\n"
        f"    pathlib.Path({str(seen)!r}).write_text(' '.join(sorted(os.listdir({str(entry)!r}))))\n"
        "    raise ValueError('corrupt file')\n"))
    assert run() == 30
    assert seen.read_text() == "fetch.json raw.xml"
    assert sorted(p.name for p in entry.iterdir()) == ["fetch.json", "raw.xml"]  # no rows.jsonl
    record = json.loads((entry / "fetch.json").read_text())
    assert record["sha256"] == hashlib.sha256((entry / "raw.xml").read_bytes()).hexdigest()


@pytest.mark.parametrize("parser", [
    "def parse(data):\n    return {'not': 'a list'}\n",
    "def parse(data):\n    return [float('nan')]\n",
    "def parse(data):\n    return [{'x': float('nan')}]\n",
    "import sys\ndef parse(data):\n    sys.exit(0)\n",
    "this is not python\n",
])
def test_a_parser_that_misbehaves_is_a_parse_failure(web, tmp_path, parser):
    release(tmp_path, parser=parser)
    assert run() == 30
    assert sorted(p.name for p in spool(tmp_path).iterdir()) == ["fetch.json", "raw.xml"]


def test_the_same_bytes_again_are_unchanged_and_spool_nothing(web, tmp_path):
    assert run("run-a") == 0
    assert run("run-b") == 10
    assert not spool(tmp_path, "run-b").exists()
    web.pages[FIRST] = ok(b"<rates><rate currency='AAA' value='1.6'/></rates>")
    assert run("run-c") == 0


def test_an_existing_spool_entry_is_never_overwritten(web, tmp_path):
    entry = spool(tmp_path)
    entry.mkdir(parents=True)
    (entry / "raw.xml").write_bytes(b"first")
    assert run() == 1
    assert sorted(p.name for p in entry.iterdir()) == ["raw.xml"]
    assert (entry / "raw.xml").read_bytes() == b"first"
    assert [p.name for p in entry.parent.iterdir()] == [RUN]  # no temp directory left


@pytest.mark.parametrize("run_id", ["other/x", "rates/..", "rates/.hidden", "rates/a/b"])
def test_a_run_id_outside_the_dags_spool_is_a_task_error(web, tmp_path, run_id):
    assert F.main(["--release", "r1", "--product", "fx-test.rates", "--run-id", run_id, "--ds", "2026-11-27"]) == 1
    assert web.connects == []


# ── the parser's audits: run here, on the rows as they land ───────────────

AUDITED = (PARSER + "def has_rows(rows):\n    return [] if rows else ['no rows']\n"
           "def small(rows):\n    return [f\"row {i}: {r['bytes']} bytes\" for i, r in enumerate(rows) if r['bytes'] > 1]\n"
           "def boom(rows):\n    raise ValueError('audit broke')\n"
           "def counted(rows):\n    return 0\n"
           "def mutates(rows):\n    rows.clear()\n    return []\n")


def test_each_declared_audit_is_run_and_recorded(web, tmp_path):
    release(tmp_path, parser=AUDITED, audits=["mutates", "has_rows", "small", "boom", "counted", "missing"])
    assert run() == 0  # a failing audit fails the batch at bronze, not the fetch
    got = {a["name"]: a for a in json.loads((spool(tmp_path) / "audits.json").read_text())["audits"]}
    assert list(got) == ["mutates", "has_rows", "small", "boom", "counted", "missing"]
    assert got["mutates"]["passed"] and got["has_rows"] == {"name": "has_rows", "passed": True, "failures": 0, "detail": ""}
    assert got["small"] == {"name": "small", "passed": False, "failures": 1, "detail": f"row 0: {len(BODY)} bytes"}
    assert not got["boom"]["passed"] and "ValueError: audit broke" in got["boom"]["detail"]
    assert not got["counted"]["passed"] and "list of str" in got["counted"]["detail"]
    assert not got["missing"]["passed"] and "no function missing" in got["missing"]["detail"]


def test_an_audits_detail_is_short(web, tmp_path):
    release(tmp_path, parser=PARSER + "def noisy(rows):\n    return ['x' * 900] * 40\n", audits=["noisy"])
    assert run() == 0
    [a] = json.loads((spool(tmp_path) / "audits.json").read_text())["audits"]
    assert a["failures"] == 40 and len(a["detail"]) <= F.MAX_DETAIL and a["detail"].endswith("and 35 more")


def test_a_parse_failure_writes_no_audits(web, tmp_path):
    release(tmp_path, parser="def parse(data):\n    raise ValueError('x')\ndef has_rows(rows):\n    return []\n",
            audits=["has_rows"])
    assert run() == 30
    assert not (spool(tmp_path) / "audits.json").exists()


def test_a_spool_file_is_never_overwritten(tmp_path):
    F.write_new(tmp_path / "audits.json", "first")
    with pytest.raises(FileExistsError):
        F.write_new(tmp_path / "audits.json", "second")
    assert (tmp_path / "audits.json").read_text() == "first" and sorted(p.name for p in tmp_path.iterdir()) == ["audits.json"]

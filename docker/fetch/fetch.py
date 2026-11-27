"""The fetch container's program: one https-file fetch into the run's spool, then its parse
(ADR-036 parts 2 and 3; docs/development/phase-001-interfaces.md, "The fetch container").

    fetch.py --release <id> --product <domain>.<product> --run-id <dagId>/<runId> --ds <YYYY-MM-DD>

--release defaults to $K2_RELEASE, the one variable compiled/oneshots.json passes it. It reads
the product's connector from the release's compiled/products/<product>/pipeline.json, renders
{{ds}} in its url, and downloads the file under the guard below into /spool/<dagId>/<runId>/ as
raw.<format> and fetch.json. Only then does it load the product's registered parser,
platform/domains/<domain>/parsers/<parser>.py in the release, and write its rows to rows.jsonl.

Exits (phase-001-interfaces.md, the fetch container's table):
  0   fetched and parsed: raw.<ext>, fetch.json, rows.jsonl
  10  unchanged: the sha256 equals that of this DAG's newest earlier fetch.json; nothing new
  20  refused (ADR-036 part 3: scheme, host, redirect, address, size); the message names
      scheme and host only; nothing spooled
  30  parse failed: raw.<ext> and fetch.json kept, no rows.jsonl
  1   anything else is a task error: an unreachable publisher, an HTTP error, the deadline, a
      spool entry that already exists (never overwritten), a bad argument

The guard, on the source URL and on each of at most five redirects, followed by hand: https on
port 443, no userinfo, no control character; the host, IDNA-normalised, exactly one of
allowHosts (an IP literal only if listed as written); resolved once, refused if any address it
returns is not public unicast (public()); the socket then opens to an address from that one
answer while SNI, the certificate's name check and the Host header use the host name
(Pinned.connect), so a name that answers differently a moment later cannot move the connection.
TLS is the default context, chain and name verified, with no way to turn it off. http.client
reads no proxy variable; main() deletes them too, so neither the fetch nor the parser uses one.
This container holds no secret. Logs name scheme, host and path, never a query string.
"""

import argparse
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic

RELEASES = Path("/k2/releases")  # k2gen.model.RELEASES_MOUNT, read-only
SPOOL = Path("/spool")
PARSER = "platform/domains/{domain}/parsers/{parser}.py"  # the release's run code (k2gen model.PLATFORM_CODE)
MAX_BYTES = 64 * 2**20  # the container's cap; a connector's maxBytes may only lower it
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_S, READ_TIMEOUT_S, DEADLINE_S = 10, 30, 300  # per connect, per read, the whole download
CHUNK = 2**16
EXIT_OK, EXIT_UNCHANGED, EXIT_REFUSED, EXIT_PARSE_FAILED, EXIT_ERROR = 0, 10, 20, 30, 1
REDIRECTS = {301, 302, 303, 307, 308}
TLS = ssl.create_default_context()
# Refused whatever this Python's ipaddress flags say: CGNAT, and every IPv6 form that carries an
# IPv4 address the check would not see (NAT64, local NAT64, 6to4, Teredo). v4-mapped is unwrapped.
BLOCKED = tuple(map(ipaddress.ip_network, ("100.64.0.0/10", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16",
                                           "2001::/32")))
RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PRODUCT_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z][a-z0-9]*(-[a-z0-9]+)*")  # pipeline.schema.json
RUN_NAME = re.compile(r"[A-Za-z0-9_~:+-][A-Za-z0-9_.~:+-]*")  # Airflow's run id characters; never . or ..
CONTROL = re.compile(r"[\x00-\x20\x7f]")
PROXY_VARS = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}


class Refused(Exception):
    """ADR-036 part 3 refuses the fetch: exit 20. The message names scheme and host only."""


class Failed(Exception):
    """A task error: exit 1."""


def where(url: str) -> str:
    """Scheme and host: all a refusal names."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}"


def public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Public unicast: not private, loopback, link-local, unique-local, site-local, multicast,
    reserved, unspecified, CGNAT or an IPv4-carrying IPv6 form."""
    return (ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                  or ip.is_reserved or ip.is_unspecified or getattr(ip, "is_site_local", False))
            and not any(ip in net for net in BLOCKED))


def check(url: str, allow: set[str]) -> tuple[str, str, list[str]]:
    """Refused unless `url` may be fetched; else its host, its request target, and the addresses
    its one resolution returned, every one of them checked."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise Refused(f"{where(url)}: not https")
    if CONTROL.search(url):  # urlsplit quietly drops tab, CR and LF; nothing here does
        raise Refused(f"{where(url)}: a control character or space in the URL")
    try:
        port = parts.port
        host = (parts.hostname or "").encode("idna").decode("ascii")
    except ValueError:  # UnicodeError included
        raise Refused(f"{where(url)}: not a valid host or port") from None
    if "@" in parts.netloc or port not in (None, 443):
        raise Refused(f"{where(url)}: userinfo or a port other than 443")
    if host not in allow:
        raise Refused(f"{where(url)}: not in allowHosts")
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if not target.isascii():
        raise Refused(f"{where(url)}: a path or query that is not ASCII")
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise Failed(f"{where(url)}: {e}") from None
    addresses = []
    for *_, sockaddr in found:
        ip = ipaddress.ip_address(sockaddr[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip  # judged, and dialled, as the IPv4 it is
        if not public(ip):
            raise Refused(f"{where(url)}: it resolves to an address that is not public")
        addresses.append(str(ip))
    return host, target, list(dict.fromkeys(addresses))


class Pinned(http.client.HTTPSConnection):
    """HTTPS to `host` over a socket opened to one of `addresses`, those check() resolved and
    passed. No second lookup happens: SNI, the certificate's name check and the Host header all
    use `host`, the TCP connection goes only to a checked address."""

    def __init__(self, host: str, addresses: list[str]):
        super().__init__(host, 443, timeout=READ_TIMEOUT_S, context=TLS)
        self.addresses, self.address = addresses, None

    def connect(self) -> None:
        error: OSError = OSError("no address")
        for address in self.addresses:  # every one checked; the next is tried only if one refuses
            try:
                sock = socket.create_connection((address, 443), CONNECT_TIMEOUT_S)
            except OSError as e:
                error = e
                continue
            sock.settimeout(READ_TIMEOUT_S)
            self.address = address
            self.sock = TLS.wrap_socket(sock, server_hostname=self.host)
            return
        raise error


def download(url: str, allow: set[str], cap: int, out: Path) -> dict:
    """Fetches `url` under the guard, following at most MAX_REDIRECTS redirects, into `out`
    (created, never replaced); the fetch record."""
    first, deadline = url, monotonic() + DEADLINE_S
    for _ in range(MAX_REDIRECTS + 1):
        if monotonic() > deadline:
            raise Failed(f"{where(url)}: past the {DEADLINE_S} s deadline")
        host, target, addresses = check(url, allow)
        parts = urllib.parse.urlsplit(url)
        print(f"fetch: GET https://{host}{parts.path or '/'}", flush=True)  # never the query
        conn = Pinned(host, addresses)
        try:
            conn.request("GET", target, headers={"User-Agent": "k2-fetch/1", "Connection": "close"})
            resp = conn.getresponse()
            if resp.status in REDIRECTS:
                if not (location := resp.getheader("Location")):
                    raise Failed(f"{where(url)}: HTTP {resp.status} with no Location")
                url = urllib.parse.urljoin(url, location)
                continue
            if resp.status != 200:
                raise Failed(f"{where(url)}: HTTP {resp.status}")
            if resp.length is not None and resp.length > cap:
                raise Refused(f"{where(url)}: Content-Length over {cap} bytes")
            digest, size = hashlib.sha256(), 0
            with out.open("xb") as f:
                while chunk := resp.read(min(CHUNK, cap + 1 - size)):
                    size += len(chunk)
                    if size > cap:
                        raise Refused(f"{where(url)}: more than {cap} bytes")
                    if monotonic() > deadline:
                        raise Failed(f"{where(url)}: past the {DEADLINE_S} s deadline")
                    digest.update(chunk)
                    f.write(chunk)
            record = {"url": first, "finalUrl": url, "sha256": digest.hexdigest(), "bytes": size,
                      "fetchedAt": datetime.now(UTC).isoformat(timespec="microseconds"),
                      "httpStatus": resp.status, "address": conn.address}
            for header, key in (("Content-Type", "contentType"), ("ETag", "etag"), ("Last-Modified", "lastModified")):
                if (value := resp.getheader(header)) is not None:
                    record[key] = value
            return record
        except (OSError, http.client.HTTPException) as e:
            raise Failed(f"{where(url)}: {type(e).__name__}: {e}") from None
        finally:
            conn.close()
    raise Refused(f"{where(url)}: more than {MAX_REDIRECTS} redirects")


def newest_sha256(dag_dir: Path) -> str | None:
    """The sha256 of this DAG's newest earlier fetch, by fetchedAt."""
    records = [json.loads(p.read_text()) for p in dag_dir.glob("*/fetch.json") if not p.parent.name.startswith(".")]
    return max(records, key=lambda r: r["fetchedAt"])["sha256"] if records else None


def parse(module_path: Path, entry: Path, raw: str) -> int:
    """Runs the registered parser over the spooled bytes and writes rows.jsonl; exit 0 or 30."""
    try:
        spec = importlib.util.spec_from_file_location("k2_parser", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = module.parse((entry / raw).read_bytes())
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise TypeError("parse() must return a list of dicts")
        lines = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
    except (Exception, SystemExit) as e:  # a parser's sys.exit(0) is a failure too, never exit 0
        print(f"fetch: parse failed, the bytes are kept: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_PARSE_FAILED
    tmp = entry / ".rows.jsonl.tmp"
    tmp.write_text(lines, encoding="utf-8")
    os.link(tmp, entry / "rows.jsonl")  # refuses an existing rows.jsonl
    tmp.unlink()
    print(f"fetch: {len(rows)} rows")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for name in [n for n in os.environ if n.lower() in PROXY_VARS]:
        del os.environ[name]
    ap = argparse.ArgumentParser(prog="fetch.py")
    ap.add_argument("--release", default=os.environ.get("K2_RELEASE"))
    ap.add_argument("--product", required=True)
    ap.add_argument("--run-id", required=True, help="<dagId>/<runId>")
    ap.add_argument("--ds", required=True, type=lambda s: date.fromisoformat(s).isoformat())
    a = ap.parse_args(argv)
    dag_id, _, run = a.run_id.partition("/")
    if not (a.release and RELEASE_ID.fullmatch(a.release) and PRODUCT_ID.fullmatch(a.product)
            and RUN_NAME.fullmatch(run)):
        print("fetch failed: --release, --product or --run-id is not a plain name", file=sys.stderr)
        return EXIT_ERROR
    root = RELEASES / a.release
    pipeline = json.loads((root / "compiled" / "products" / a.product / "pipeline.json").read_text())
    connector = pipeline.get("connector") or {}
    if connector.get("kind") != "https-file" or dag_id != pipeline["dagId"]:
        print(f"fetch failed: {a.product} has no https-file connector, or the run is not of DAG "
              f"{pipeline['dagId']}", file=sys.stderr)
        return EXIT_ERROR
    raw, dag_dir = f"raw.{connector['format']}", SPOOL / dag_id
    entry = dag_dir / run
    dag_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dag_dir))  # same filesystem, so the rename is atomic
    try:
        tmp.chmod(0o755)  # mkdtemp's 0700 would hide the entry from the Spark task's user
        record = download(connector["url"].replace("{{ds}}", a.ds), set(connector["allowHosts"]),
                          min(connector.get("maxBytes", MAX_BYTES), MAX_BYTES), tmp / raw)
        if record["sha256"] == newest_sha256(dag_dir):
            print(f"fetch: unchanged, sha256 {record['sha256']}")
            return EXIT_UNCHANGED
        (tmp / "fetch.json").write_text(json.dumps(record, indent=1) + "\n")
        tmp.rename(entry)  # raw and record appear together; a non-empty entry refuses the rename
    except Refused as e:
        print(f"fetch refused {e}", file=sys.stderr)
        return EXIT_REFUSED
    except (Failed, OSError, ValueError) as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # gone already once renamed
    print(f"fetch: spooled {record['bytes']} bytes, sha256 {record['sha256']}")
    return parse(root / PARSER.format(domain=pipeline["domain"], parser=connector["parser"]), entry, raw)


if __name__ == "__main__":
    sys.exit(main())

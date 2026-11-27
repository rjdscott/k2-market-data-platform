"""The fetch container's program: one https-file or public-api fetch into the run's spool, then
its parse (ADR-036 parts 2 and 3; docs/development/phase-001-interfaces.md, "The fetch container").

    fetch.py --release <id> --product <domain>.<product> --run-id <dagId>/<runId> --ds <YYYY-MM-DD>

--release defaults to $K2_RELEASE, the one variable compiled/oneshots.json passes it. It reads
the product's connector from the release's compiled/products/<product>/pipeline.json, renders
{{ds}} in its url, and downloads the file under the guard below into /spool/<dagId>/<runId>/ as
raw.<format> and fetch.json. Only then does it load the product's registered parser,
platform/domains/<domain>/parsers/<parser>.py in the release, and write its rows to rows.jsonl.

A public-api connector fetches pages instead (fetch_api()): its url with {{ds}} rendered and its
params added, then by pagination `none` (that one page), `next-link` (the rel="next" target of each
page's Link header) or `page` (params plus page=1, 2, ... until a page's `records` field is empty),
at most maxPages pages, a page beyond that failing the run, and minIntervalS seconds after each page
before the next is asked for. Every request is download()'s, under the guard below, and the run's
pages share the 64 MiB cap. The spool then holds raw.<n>.json, the bytes as they arrived, for each
page n (from 1) whose content changed from page n of this DAG's newest earlier fetch, and fetch.json,
{"fetchedAt", "pages": [{"page", <the fetch record>, "contentSha256", "raw": "raw.<n>.json" | null}]},
every page listed. contentSha256 is the page's canonical JSON with every volatileFields key dropped,
used for that comparison alone: the bytes written keep every field. A run in which no page changed
is unchanged (exit 10); one that only lost trailing pages keeps its last page. The parser runs on
each kept page in order, and rows.jsonl holds their rows.

Then it runs the parser's audits, the bronze tables' `audits` in pipeline.json (ODCS custom checks
with engine k2-connector): product code, so they run here and never in Spark beside a storage key.
Each is a function of that name in the same parser module:

    def <audit>(rows: list[dict]) -> list[str]   # one description per failing row; [] passes

called on the rows as rows.jsonl holds them (a fresh copy each). The results go to audits.json,
{"audits": [{"name", "passed", "failures", "detail"}]}, one entry per declared audit in order,
which connector.py's audit task records in audit_checks. An audit that is missing, raises or
returns anything but a list of str is recorded as passed: false with the reason; no audit changes
the exit code, so the batch still lands and fails at bronze.

Exits (phase-001-interfaces.md, the fetch container's table):
  0   fetched and parsed: raw.<ext>, fetch.json, rows.jsonl, audits.json
  10  unchanged: the sha256 equals that of this DAG's newest earlier fetch.json, or no API page
      changed but for its volatileFields; nothing new
  20  refused (ADR-036 part 3: scheme, host, redirect, address, size); the message names
      scheme and host only; nothing spooled
  30  parse failed: raw.<ext> and fetch.json kept, no rows.jsonl and no audits.json
  1   anything else is a task error: an unreachable publisher, an HTTP error, the deadline, a
      spool entry that already exists (never overwritten), a bad argument, more pages than
      maxPages, a `page` page with no records field

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
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep

RELEASES = Path("/k2/releases")  # k2gen.model.RELEASES_MOUNT, read-only
SPOOL = Path("/spool")
PARSER = "platform/domains/{domain}/parsers/{parser}.py"  # the release's run code (k2gen model.PLATFORM_CODE)
MAX_BYTES = 64 * 2**20  # the container's cap; a connector's maxBytes may only lower it
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_S, READ_TIMEOUT_S, DEADLINE_S = 10, 30, 300  # per connect, per read, the whole download
CHUNK = 2**16
MAX_DETAIL, DETAIL_ITEMS = 1000, 5  # an audit's detail: audit_checks.detail's bound, failures quoted
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
PAGINATION = {"none", "next-link", "page"}
LINK = re.compile(r"<([^>]*)>([^<]*)")  # an RFC 8288 Link header's target and its parameters


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
        # tls: the socket itself, kept because getresponse() drops self.sock on a closing response;
        # set its timeout only while the response is open (download())
        self.addresses, self.address, self.tls = addresses, None, None

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
            self.sock = self.tls = TLS.wrap_socket(sock, server_hostname=self.host)
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
                # A response that closes its connection owns the socket: it closes it as it reads
                # the body's last byte, so a closed response is the end of the body and its socket
                # is never touched again (settimeout on it is EBADF). While open, its file keeps
                # the descriptor alive, so the timeout below always lands on a live socket.
                while not resp.isclosed():
                    # read1: what has arrived, one recv at most, under a timeout cut to the time
                    # left, so a host trickling bytes cannot hold a read past the deadline
                    if (left := deadline - monotonic()) <= 0:
                        raise Failed(f"{where(url)}: past the {DEADLINE_S} s deadline")
                    conn.tls.settimeout(min(READ_TIMEOUT_S, left))
                    if not (chunk := resp.read1(min(CHUNK, cap + 1 - size))):
                        break
                    size += len(chunk)
                    if size > cap:
                        raise Refused(f"{where(url)}: more than {cap} bytes")
                    digest.update(chunk)
                    f.write(chunk)
            if resp.length:  # read1 ends at an early EOF quietly; length is what never came
                raise Failed(f"{where(url)}: the body ended {resp.length} bytes short of its Content-Length")
            record = {"url": first, "finalUrl": url, "sha256": digest.hexdigest(), "bytes": size,
                      "fetchedAt": datetime.now(UTC).isoformat(timespec="microseconds"),
                      "httpStatus": resp.status, "address": conn.address}
            for header, key in (("Content-Type", "contentType"), ("ETag", "etag"), ("Last-Modified", "lastModified"),
                                ("Link", "link")):
                if (value := resp.getheader(header)) is not None:
                    record[key] = value
            return record
        except (OSError, http.client.HTTPException) as e:
            raise Failed(f"{where(url)}: {type(e).__name__}: {e}") from None
        finally:
            conn.close()
    raise Refused(f"{where(url)}: more than {MAX_REDIRECTS} redirects")


def newest(dag_dir: Path) -> dict:
    """This DAG's newest earlier fetch record, by fetchedAt; {} if there is none."""
    records = [json.loads(p.read_text()) for p in dag_dir.glob("*/fetch.json") if not p.parent.name.startswith(".")]
    return max(records, key=lambda r: r["fetchedAt"]) if records else {}


def newest_sha256(dag_dir: Path) -> str | None:
    """The sha256 of this DAG's newest earlier https-file fetch (scripts/lib/run_path_probe.py too)."""
    return newest(dag_dir).get("sha256")


def strip(value, names: set[str]):
    """`value` with every object key in `names` dropped, at any depth."""
    # ponytail: any depth, so a volatile name also hides a same-named field elsewhere; dotted paths
    # in volatileFields when a source needs one name kept in one place and ignored in another
    if isinstance(value, dict):
        return {k: strip(v, names) for k, v in value.items() if k not in names}
    if isinstance(value, list):
        return [strip(v, names) for v in value]
    return value


def content_sha256(data: bytes, volatile: set[str]) -> str:
    """A page's fingerprint for the change test, never for what is written: the sha256 of its JSON
    without the volatile keys, canonical (sorted keys, numbers as written, so 1.10 is not 1.1);
    of its bytes as they are if they are not JSON."""
    try:
        value = strip(json.loads(data, parse_float=Decimal), volatile)
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr).encode()
    except (ValueError, RecursionError):
        pass
    return hashlib.sha256(data).hexdigest()


def next_link(header: str | None) -> str | None:
    """The rel="next" target of a Link header, as written; None if it names none."""
    for target, params in LINK.findall(header or ""):
        rel = re.search(r'\brel\s*=\s*"?([^";,]*)', params, re.IGNORECASE)
        if rel and "next" in rel.group(1).lower().split():
            return target
    return None


def has_records(data: bytes, field: str) -> bool:
    """Whether a page holds records in `field`; `page` pagination stops at the first that holds none."""
    try:
        return bool(json.loads(data)[field])
    except (ValueError, RecursionError, LookupError, TypeError):
        raise Failed(f"a page is not a JSON object with field {field!r}") from None


def fetch_api(connector: dict, ds: str, tmp: Path, before: list[dict]) -> dict:
    """Fetches a public-api connector's pages into `tmp`, every request through download(), and
    keeps raw.<n>.json only where page n's content changed from page n of `before`, the pages of
    this DAG's newest earlier fetch; the fetch record, every page listed, raw null if not kept."""
    mode, most = connector["pagination"], connector.get("maxPages", 1)
    if mode not in PAGINATION:
        raise Failed(f"pagination {mode!r} is not one of {sorted(PAGINATION)}")
    first = connector["url"].replace("{{ds}}", ds)
    query = urllib.parse.urlencode({k: v.replace("{{ds}}", ds) for k, v in connector.get("params", {}).items()})

    def numbered(n: int) -> str:
        q = "&".join(filter(None, (query, f"page={n}" if mode == "page" else "")))
        return f"{first}{'&' if '?' in first else '?'}{q}" if q else first

    allow, volatile = set(connector["allowHosts"]), set(connector.get("volatileFields", []))
    pages: list[dict] = []
    url: str | None = numbered(1)
    while url:
        n = len(pages) + 1
        if n > most:  # a failure, never a silent truncation
            raise Failed(f"{where(first)}: more pages than maxPages, {most}")
        if pages:
            # ponytail: the whole interval after a page ends, so two page requests are always that far
            # apart; a redirect within one page's request follows at once
            sleep(connector["minIntervalS"])
        out = tmp / f"raw.{n}.json"
        record = download(url, allow, MAX_BYTES - sum(p["bytes"] for p in pages), out)
        data = out.read_bytes()
        pages.append({"page": n, **record, "contentSha256": content_sha256(data, volatile)})
        print(f"fetch: page {n}, {record['bytes']} bytes, sha256 {record['sha256']}", flush=True)
        if mode == "next-link" and (target := next_link(record.get("link"))):
            url = urllib.parse.urljoin(record["finalUrl"], target)
        elif mode == "page" and has_records(data, connector["records"]):
            url = numbered(n + 1)
        else:
            url = None
    keys, was = [p["contentSha256"] for p in pages], [p.get("contentSha256") for p in before]
    keep = {i for i, key in enumerate(keys) if was[i:i + 1] != [key]}
    if not keep and keys != was:  # only trailing pages are gone: the last page carries that change
        keep = {len(keys) - 1}
    for i, page in enumerate(pages):
        page["raw"] = f"raw.{page['page']}.json" if i in keep else None
        if i not in keep:
            (tmp / f"raw.{page['page']}.json").unlink()
    return {"fetchedAt": pages[0]["fetchedAt"], "pages": pages}


def write_new(path: Path, text: str) -> None:
    """Writes `path` whole or not at all, and never over an existing file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.link(tmp, path)  # refuses an existing path
    finally:
        tmp.unlink()


def audit(module, name: str, lines: str) -> dict:
    """One declared audit over the rows as they land, as its audits.json entry."""
    rows = [json.loads(line) for line in lines.splitlines()]
    try:
        function = getattr(module, name, None)
        if not callable(function):
            raise LookupError(f"the parser has no function {name}")
        failures = function(rows)
        if not isinstance(failures, list) or not all(isinstance(f, str) for f in failures):
            raise TypeError(f"{name}() must return a list of str, not {type(failures).__name__}")
    except (Exception, SystemExit) as e:  # an audit that breaks fails, and the fetch goes on
        return {"name": name, "passed": False, "failures": 0, "detail": f"{type(e).__name__}: {e}"[:MAX_DETAIL]}
    more = f"; and {len(failures) - DETAIL_ITEMS} more" if len(failures) > DETAIL_ITEMS else ""
    detail = "; ".join(failures[:DETAIL_ITEMS])[:MAX_DETAIL - len(more)] + more
    return {"name": name, "passed": not failures, "failures": len(failures), "detail": detail}


def parse(module_path: Path, entry: Path, raws: str | list[str], audits: list[str]) -> int:
    """Runs the registered parser over each spooled file in order and writes their rows to
    rows.jsonl, then its declared audits and audits.json; exit 0 or 30."""
    if isinstance(raws, str):  # one file's name, as scripts/lib/run_path_probe.py passes it
        raws = [raws]
    try:
        spec = importlib.util.spec_from_file_location("k2_parser", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = []
        for raw in raws:
            got = module.parse((entry / raw).read_bytes())
            if not isinstance(got, list) or not all(isinstance(r, dict) for r in got):
                raise TypeError("parse() must return a list of dicts")
            rows += got
        lines = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
    except (Exception, SystemExit) as e:  # a parser's sys.exit(0) is a failure too, never exit 0
        print(f"fetch: parse failed, the bytes are kept: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_PARSE_FAILED
    write_new(entry / "rows.jsonl", lines)
    print(f"fetch: {len(rows)} rows")
    results = [audit(module, name, lines) for name in audits]
    write_new(entry / "audits.json", json.dumps({"audits": results}, indent=1) + "\n")
    for a in results:
        print(f"fetch: audit {a['name']} {'passed' if a['passed'] else 'FAILED'} {a['detail']}")
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
    kind = connector.get("kind")
    # authHeaderEnv is refused, not ignored: this container holds no secret
    if kind not in ("https-file", "public-api") or "authHeaderEnv" in connector or dag_id != pipeline["dagId"]:
        print(f"fetch failed: {a.product} has no https-file or public-api connector without authHeaderEnv, "
              f"or the run is not of DAG {pipeline['dagId']}", file=sys.stderr)
        return EXIT_ERROR
    dag_dir = SPOOL / dag_id
    entry = dag_dir / run
    dag_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dag_dir))  # same filesystem, so the rename is atomic
    try:
        tmp.chmod(0o755)  # mkdtemp's 0700 would hide the entry from the Spark task's user
        if kind == "public-api":
            record = fetch_api(connector, a.ds, tmp, newest(dag_dir).get("pages", []))
            raws = [p["raw"] for p in record["pages"] if p["raw"]]
            done = f"{', '.join(raws)} of {len(record['pages'])} pages"
            if not raws:
                print(f"fetch: unchanged, {len(record['pages'])} pages as before but for their volatileFields")
                return EXIT_UNCHANGED
        else:
            raws = [f"raw.{connector['format']}"]
            record = download(connector["url"].replace("{{ds}}", a.ds), set(connector["allowHosts"]),
                              min(connector.get("maxBytes", MAX_BYTES), MAX_BYTES), tmp / raws[0])
            done = f"{record['bytes']} bytes, sha256 {record['sha256']}"
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
    print(f"fetch: spooled {done}")
    audits = list(dict.fromkeys(a for b in pipeline.get("bronze", []) for a in b["audits"]))
    return parse(root / PARSER.format(domain=pipeline["domain"], parser=connector["parser"]), entry, raws, audits)


if __name__ == "__main__":
    sys.exit(main())

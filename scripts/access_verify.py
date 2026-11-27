#!/usr/bin/env python3
"""access-verify (A9, step S20): every hand-written row of tests/access/expectations.yaml, run
against a RUNNING Trino as the principal it names. The rows are written and reviewed by a person
and never generated from the rules they test, so a pass is not the compiler agreeing with itself
(tests/access/README.md). make access-verify drives it (mk/access.mk).

    access_verify.py run   --release <dir> --scope release|live [--env <env.json>] [--out <result.json>]
    access_verify.py stage --release <dir> --env <env.json>

The release is the one the Trino under test runs (a pointer resolves once): its documents give the
rows, the principals and the products, so a row naming something the running release does not
know fails. Every row prints one line, `PASS|FAIL|SKIP <n> <what it asserts>: <evidence>`, and a
skipped row always says why. Exit 0 when no row fails and at least one passed, 1 otherwise, 2 on a
usage error or an unreachable Trino. --out writes the JSON result a deploy record can cite:
{release, setSha256, scope, trino, at, exit, counts, rows: [{n, row, verdict, reason}]}.

A row passes only on the exact outcome it names, never on "some error". `denied` is Trino's
PERMISSION_DENIED and nothing else: a syntax error, a missing catalog, schema, table or column
(SCHEMA_NOT_FOUND, TABLE_NOT_FOUND, COLUMN_NOT_FOUND), NOT_SUPPORTED, or a refused login fails the
row, since none of them is the rules at work. `error` is NOT_SUPPORTED and nothing else, the one
error the schema gives it (a version on a view: "Views do not support versioning"); a row needing
another class needs a field the frozen schema lacks. A read row reads the relation, gets exactly its `visible` columns, finds every `masked`
column null in every row, no row younger than `minRowAgeMinutes` by the table's delay column, no
row matching `mustNotAppear`, and at least `mustAppear.minRows` matching its `where`. A storage-key
row (phase 002) sends its S3 request from this process to $K2_S3_ENDPOINT (default minio:9000, so
it runs inside a container on the lake's network) with K2_<ENGINE>_S3_*: `refused` is 403
AccessDenied on a bucket some key shows exists, never a connection error or a missing bucket, and a
refused read is refused a real object too (one another key lists, else a probe another key puts).
A read or denial row with `rest` in its `via` (phase 003, W3-7) is held again through k2-api at
$K2_API_URL (default http://k2-api:8095) as its principal, with its own key from
<K2_API_KEY_DIR>/<principal>.key: a denial is 403 forbidden and nothing else, a read 200 with
exactly `visible` and SQL's rows (tests/access/README.md); the row prints each leg's verdict.

Where the endpoint and each principal's password come from:
- scope release (a throwaway lake, scripts/lib/throwaway-lake.sh, run in its tools container):
  --env names its env.json, read by scripts/lib/lake_env.py, the module the lake writes it with;
  a principal's password is the file its `passwords` map names, else <secrets>/principals/<id>.password.
  Every principal of the release has a line, so every row runs; a planted-view row plants its
  view by REST first and removes it after, and the drift row runs the drift check.
- scope live (the running stack, from the host or the tools one-shot): Trino at
  https://${K2_TRINO_HOST:-localhost}:${K2_TRINO_PORT:-8443}, its CA ${K2_TRINO_CA:-${K2_SECRETS}/tls/ca.pem},
  each password ${K2_SECRETS:-$HOME/k2-secrets}/principals/<id>.password. No row impersonates
  anyone and no verification principal exists: a principal that may become consumers would be a
  key to every consumer's rows. Live runs only the rows whose principal's password the platform
  holds, a service principal (svc.*, dbt_*, pub_*) or a grantee of the access canary only (it
  exists to be tested; platform_held, which scripts/principal-secret.py's TO_FILE=1 obeys too),
  and only when every product the row names is the canary, so a loosened
  rule can damage nothing real. Every other row is a SKIP naming the throwaway run at release
  time, where generated passwords stand in for the consumers'. A planted-view row is a SKIP (it
  would write the live catalog), and so is the drift row unless the catalog API answers at
  $K2_CATALOG_URI (it does only from inside k2-catalog). An eligible principal with no password
  file fails its row.

stage (throwaway lake only) makes the lake the release describes, as its build and deploy would:
every namespace of the storage plan by REST; Spark's tables checked present (the lake made them);
each dbt or code product's seeds and models, built as its builder through `<domain>_build` (only
ref() and config() are rendered; a model with any other Jinja is dbt's, and its output is made in
its contract's shape with one placeholder row, the shape checked by DESCRIBE, refused if a read row
asserts its rows: a columns-only read row proves the view's columns and the permission, never the
model's rows); one tag commit per warehouse; each view as its
publisher (lake.create_view); then one untested write per output, every row again with its key
moved (+1000 or a zz-untested- prefix), which no reader may ever see; and the platform's Trino DDL
as svc.platform.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/: verify.catalog_drift
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))  # scripts/lib/: lake_env
import lake_env  # noqa: E402
from verify import catalog_drift  # noqa: E402

CANARY_PRODUCT = "governance.access-canary"  # contracts/governance/access-canary: exists to be tested on live
DENIED = "PERMISSION_DENIED"  # an `expect: denied` row passes on this errorName alone
UNSUPPORTED = "NOT_SUPPORTED"  # and an `expect: error` row on this one
API_URL = "http://k2-api:8095"  # $K2_API_URL overrides: k2-api's name on the throwaway lake's network and on k2-net
REST_PAGE = 10_000  # k2-api's largest `limit` (openapi.yaml): a REST read asserting rows reads one page
SKIP_PROBE = "a network probe: scripts/verify/catalog-fence.sh (step S22) runs it on the stack"
SKIP_DRIFT = "the catalog API answers only inside k2-catalog: scripts/verify/catalog_drift.py runs there (release-verify)"
S3_ENDPOINT = "http://minio:9000"  # $K2_S3_ENDPOINT overrides, as docker/lake/init-lake.sh and spark_conf.py read it
PROBE_DIR = "k2-access-probe"  # a write row's object: <prefix>/k2-access-probe/<random hex>, deleted at once
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# ---- Trino ---------------------------------------------------------------------------------------

@dataclass
class Answer:
    rows: list[list[Any]] = field(default_factory=list)
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, Trino type)
    error: str | None = None  # Trino's errorName, or HTTP_<code>
    message: str = ""

    def says(self) -> str:
        return f"{self.error}: {self.message[:160]}" if self.error else f"ok, {len(self.rows)} row(s)"


class NoPassword(Exception):
    pass


class Trino:
    """Trino's client protocol over HTTPS, logged in as each principal with its own password."""

    def __init__(self, url: str, ca: str, password: Callable[[str], str]):
        self.url, self.ctx, self.password = url.rstrip("/"), ssl.create_default_context(cafile=ca), password

    def run(self, user: str, sql: str, session: dict[str, str] | None = None, as_user: str | None = None,
            zone: str | None = None) -> Answer:
        auth = "Basic " + base64.b64encode(f"{user}:{self.password(user)}".encode()).decode()
        # SESSION_AUTHORIZATION lets SET SESSION AUTHORIZATION reach the impersonation rules.
        headers = {"Authorization": auth, "X-Trino-User": as_user or user, "X-Trino-Client-Capabilities": "SESSION_AUTHORIZATION"}
        if zone:  # the session time zone, as k2-api's client sets UTC
            headers["X-Trino-Time-Zone"] = zone
        if session:
            headers["X-Trino-Session"] = ",".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in session.items())
        out = Answer()
        try:
            req: urllib.request.Request | None = urllib.request.Request(f"{self.url}/v1/statement", sql.encode(), headers)
            deadline = time.monotonic() + 300
            while req is not None:
                with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
                    doc = json.load(r)
                out.rows += doc.get("data", [])
                out.columns = out.columns or [(c["name"], c["type"]) for c in doc.get("columns", [])]
                if "error" in doc:
                    out.error, out.message = doc["error"].get("errorName", "ERROR"), doc["error"].get("message", "")
                    return out
                if time.monotonic() > deadline:
                    out.error, out.message = "CLIENT_TIMEOUT", "no answer in 300 s"
                    return out
                req = urllib.request.Request(doc["nextUri"], headers={"Authorization": auth}) if doc.get("nextUri") else None
        except urllib.error.HTTPError as e:
            out.error, out.message = f"HTTP_{e.code}", "the login was refused" if e.code == 401 else e.reason
        return out

    def ok(self, user: str, sql: str, **kw: Any) -> list[list[Any]]:
        a = self.run(user, sql, **kw)
        if a.error:
            raise RuntimeError(f"{user}: {sql[:160]}: {a.says()}")
        return a.rows


def connect(env_file: Path | None) -> tuple[Trino, dict[str, Any]]:
    """The endpoint and passwords of a throwaway lake (its env.json) or of the live stack."""
    if env_file:
        env = lake_env.read(env_file)

        def lake_password(u: str) -> str:
            try:
                return lake_env.password(env, u)
            except KeyError as x:
                raise NoPassword(f"{x.args[0]} ({env_file})") from None

        # A REST row's key, mode 600, where `make api-key TO_FILE=1` writes it: the lake made one per
        # principal of a `via: rest` row and gave k2-api only its sha256 (throwaway-lake.sh, api/keys.json).
        return Trino(env["trino"], env["ca"], lake_password), env | {"apiKeys": os.environ.get("K2_API_KEY_DIR") or f"{env['secrets']}/api-keys"}
    root = Path(os.environ.get("K2_SECRETS") or Path.home() / "k2-secrets")
    url = f"https://{os.environ.get('K2_TRINO_HOST') or 'localhost'}:{os.environ.get('K2_TRINO_PORT') or 8443}"

    def live_password(u: str) -> str:
        f = root / "principals" / f"{u}.password"  # model.password_file
        if not f.is_file():
            raise NoPassword(f"no {f}")
        return f.read_text().strip()

    # <principal>.key, each the operator's `make api-key TO_FILE=1`; never api/, which k2-api mounts
    keys = os.environ.get("K2_API_KEY_DIR") or str(root / "api-keys")
    return Trino(url, os.environ.get("K2_TRINO_CA") or str(root / "tls/ca.pem"), live_password), {"trino": url, "apiKeys": keys}


# ---- storage keys (phase 002, W0-3) --------------------------------------------------------------
# One S3 request per step, path style, signed with AWS Signature V4 by the stdlib: neither the tools
# image nor the throwaway lake's carries an S3 client, and the request runs in this process, so a key
# is read from its variable and sent in a header, never on a command line, printed or in a result.

def sigv4(method: str, host: str, path: str, query: str, amz_date: str, pair: tuple[str, str]) -> dict[str, str]:
    """The signed headers of a bodiless request (AWS Signature V4, region us-east-1: MinIO with no
    region set takes any). `path` and `query` are already in canonical form."""
    headers = {"host": host, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": amz_date}
    names = ";".join(headers)
    canonical = "\n".join([method, path, query, *(f"{k}:{v}" for k, v in headers.items()), "", names, EMPTY_SHA256])
    scope = f"{amz_date[:8]}/us-east-1/s3/aws4_request"
    key = f"AWS4{pair[1]}".encode()
    for part in scope.split("/"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return headers | {"Authorization": f"AWS4-HMAC-SHA256 Credential={pair[0]}/{scope}, SignedHeaders={names}, Signature={signature}"}


def s3(endpoint: str, pair: tuple[str, str], method: str, bucket: str, key: str = "",
       query: Mapping[str, str] | None = None) -> tuple[int, str, bytes]:
    """(HTTP status, S3 error code or "", body); status 0 when nothing answered, the code then the reason."""
    path = urllib.parse.quote(f"/{bucket}/{key}" if key else f"/{bucket}", safe="/-_.~")
    q = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted((query or {}).items()))
    url = urllib.parse.urlsplit(endpoint)
    headers = sigv4(method, url.netloc, path, q, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), pair)
    if method == "GET" and key:
        headers["Range"] = "bytes=0-0"  # a read proves access with one byte
    req = urllib.request.Request(f"{endpoint.rstrip('/')}{path}{'?' + q if q else ''}", b"" if method == "PUT" else None,
                                 headers | ({"Content-Type": "application/octet-stream"} if method == "PUT" else {}), method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, "", r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        code = re.search(rb"<Code>([A-Za-z]+)</Code>", body)
        return e.code, code.group(1).decode() if code else "", body
    except (OSError, http.client.HTTPException) as x:  # URLError, a refused or timed-out connection, an unresolved name, a garbled answer
        return 0, type(x).__name__, str(getattr(x, "reason", x))[:160].encode()


def outcome(status: int, code: str) -> str | None:
    """allowed (2xx), refused (403 AccessDenied and nothing else), or None for any other answer."""
    return "allowed" if 200 <= status < 300 else "refused" if (status, code) == (403, "AccessDenied") else None


def answered(status: int, code: str, body: bytes) -> str:
    return f"cannot reach the storage ({code}: {body.decode(errors='replace')})" if status == 0 else f"HTTP {status} {code or ''}".strip()


def key_pair(engine: str, environ: Mapping[str, str]) -> tuple[str, str] | list[str]:
    """The engine's (access, secret) from K2_<ENGINE>_S3_*, or the names of the variables not set."""
    names = (f"K2_{engine.upper()}_S3_ACCESS_KEY", f"K2_{engine.upper()}_S3_SECRET_KEY")
    missing = [n for n in names if not environ.get(n)]
    return missing or (environ[names[0]], environ[names[1]])


def bucket_shown(endpoint: str, bucket: str, environ: Mapping[str, str], first: tuple[str, str]) -> tuple[bool, str]:
    """Whether some key shows the bucket exists (GetBucketLocation answered 2xx), and whose, or why not.
    MinIO checks a policy before the bucket, so a refusal alone cannot tell a missing bucket from a fenced one."""
    pairs = {"its own": first}
    for engine in ("spark", "trino", "maintenance", "backup", "tools"):
        if isinstance(pair := key_pair(engine, environ), tuple):
            pairs.setdefault(engine, pair)
    if environ.get("MINIO_ROOT_USER") and environ.get("MINIO_ROOT_PASSWORD"):
        pairs["the root"] = (environ["MINIO_ROOT_USER"], environ["MINIO_ROOT_PASSWORD"])
    for who, pair in pairs.items():
        status, code, body = s3(endpoint, pair, "GET", bucket, query={"location": ""})
        if 200 <= status < 300:
            return True, f"bucket {bucket} shown by {who} key"
        if status != 403:
            return False, f"bucket {bucket}: {answered(status, code, body)} to {who} key"
    return False, f"no key here shows bucket {bucket} exists, so a refusal proves nothing"


def other_pairs(environ: Mapping[str, str], own: tuple[str, str]) -> dict[str, tuple[str, str]]:
    """Every other pair here, by whose: the engines' (as bucket_shown asks them), then the root's."""
    pairs = {e: p for e in ("spark", "trino", "maintenance", "backup", "tools")
             if isinstance(p := key_pair(e, environ), tuple) and p != own}
    if environ.get("MINIO_ROOT_USER") and environ.get("MINIO_ROOT_PASSWORD"):
        pairs["root"] = (environ["MINIO_ROOT_USER"], environ["MINIO_ROOT_PASSWORD"])
    return pairs


def refused_get(endpoint: str, bucket: str, prefix: str, environ: Mapping[str, str],
                own: tuple[str, str]) -> tuple[str | None, str]:
    """The own key's read of a real object under the target, and what was done: an object another
    key lists, else a probe another key puts there for this read alone and removes after (an empty
    landing on a fresh lake). None and why when there is neither: a refusal of nothing proves nothing."""
    where, others = (f"{prefix}/" if prefix else ""), other_pairs(environ, own)
    for who, p in others.items():
        status, _, body = s3(endpoint, p, "GET", bucket, query={"list-type": "2", "max-keys": "1", "prefix": where})
        if 200 <= status < 300 and (first := re.search(rb"<Key>([^<]+)</Key>", body)):
            obj, how = first.group(1).decode(), f"listed by the {who} key"
            break
    else:
        obj = f"{where}{PROBE_DIR}/{uuid.uuid4().hex}"
        who = next((w for w, p in others.items() if 200 <= s3(endpoint, p, "PUT", bucket, obj)[0] < 300), None)
        if who is None:
            return None, f"; no key here shows an object under s3://{bucket}/{where} or can put one, so the refusal proves nothing"
        how = f"put by the {who} key for this read"
    status, code, body = s3(endpoint, own, "GET", bucket, obj)
    did = f"; reading {obj} ({how}): {answered(status, code, body)}"
    if how.startswith("put"):  # its writer first, then maintenance and the rest, as a write row's probe goes
        gone = next((w for w, p in {who: others[who], **others}.items() if 200 <= s3(endpoint, p, "DELETE", bucket, obj)[0] < 300), None)
        if gone is None:
            return None, f"{did}; left s3://{bucket}/{obj} behind: no key here removed it"
        did += f"; removed with the {gone} key"
    return outcome(status, code), did


def storage_key(e: Any, environ: Mapping[str, str] = os.environ) -> tuple[bool, str]:
    """A storage-key row: the operation, signed with the engine's key, ends as the row expects."""
    pair = key_pair(e.engine, environ)
    if isinstance(pair, list):
        return False, f"no {e.engine} storage key: {' and '.join(pair)} not set"
    endpoint = environ.get("K2_S3_ENDPOINT") or S3_ENDPOINT
    bucket, _, prefix = e.target.partition("/")
    if e.operation == "read":
        status, code, body = s3(endpoint, pair, "GET", bucket, query={"list-type": "2", "max-keys": "1", "prefix": prefix and f"{prefix}/"})
        got, did = outcome(status, code), f"list s3://{e.target}/: {answered(status, code, body)}"
        if got == "allowed" and (first := re.search(rb"<Key>([^<]+)</Key>", body)):
            obj = first.group(1).decode()
            status, code, body = s3(endpoint, pair, "GET", bucket, obj)
            got = "allowed" if outcome(status, code) == "allowed" else None
            did += f"; 1 object listed and read: {answered(status, code, body)}" if got else f"; listed, but reading {obj}: {answered(status, code, body)}"
        elif got == "allowed":
            did += "; 0 objects listed"
        elif got == "refused" and e.expect == "refused":
            # F11: a key with GetObject and no ListBucket is refused the list and still reads any object
            # by its key (a landing's keys are in landing_files), so it must be refused a real object too.
            there, shown = bucket_shown(endpoint, bucket, environ, pair)
            if not there:
                return False, f"{did}; {shown}"
            got, more = refused_get(endpoint, bucket, prefix, environ, pair)
            return got == "refused", f"{'' if got == 'refused' else 'expected refused, '}{did}; {shown}{more}"
    else:
        obj = f"{prefix}/{PROBE_DIR}/{uuid.uuid4().hex}" if prefix else f"{PROBE_DIR}/{uuid.uuid4().hex}"
        status, code, body = s3(endpoint, pair, "PUT", bucket, obj)
        got, did = outcome(status, code), f"put s3://{bucket}/{obj}: {answered(status, code, body)}"
        if got == "allowed":
            gone = s3(endpoint, pair, "DELETE", bucket, obj)
            if 200 <= gone[0] < 300:
                did += "; deleted"
            else:
                # The probe goes with the first key that can delete it: the maintenance key (it deletes
                # in every warehouse and the landings, so a live run needs no root pair), then the root.
                others = {"maintenance": key_pair("maintenance", environ)}
                if environ.get("MINIO_ROOT_USER") and environ.get("MINIO_ROOT_PASSWORD"):
                    others["root"] = (environ["MINIO_ROOT_USER"], environ["MINIO_ROOT_PASSWORD"])
                who = next((w for w, p in others.items() if isinstance(p, tuple) and p != pair
                            and 200 <= s3(endpoint, p, "DELETE", bucket, obj)[0] < 300), None)
                if who is None:
                    return False, (f"{did}; left s3://{bucket}/{obj} behind: its delete answered {answered(*gone)}, "
                                   "and neither the maintenance pair nor a root pair removed it")
                did += f"; its own delete answered {answered(*gone)}, removed with the {who} key"
    if got != e.expect:
        return False, f"expected {e.expect}, {did}"
    if got == "refused":
        there, shown = bucket_shown(endpoint, bucket, environ, pair)
        if not there:
            return False, f"{did}; {shown}"
        did += f"; {shown}"
    return True, did


# ---- k2-api (phase 003, W3-7) --------------------------------------------------------------------
# A row with `via: rest` holds its expectation again through k2-api, as its principal, with that
# principal's own key in `Authorization: Bearer`. The key is read from its file in this process and
# sent in that header alone: never on a command line, printed or in a result.

def api_key(env: dict[str, Any], principal: str) -> str | None:
    f = Path(env["apiKeys"]) / f"{principal}.key"
    return f.read_text().strip() if f.is_file() else None


def rest_skip(e: Any, scope: str, env: dict[str, Any]) -> str | None:
    """Why a REST leg does not run here: on live, a principal whose key file the operator never
    made, as live skips a password the platform does not hold. A lake makes every key it needs,
    so there a missing one fails the leg."""
    if scope == "live" and api_key(env, e.principal) is None:
        return (f"no {env['apiKeys']}/{e.principal}.key: make api-key PRINCIPAL={e.principal} TO_FILE=1 "
                "(ROTATE=1 too when it has a key already) runs it")
    return None


def route(release: Path, relation: str) -> tuple[str, list[str]] | None:
    """k2-api's data route of the output whose reader relation is `relation`, and its primary key in
    order, from the release's catalog documents (k2gen.model.CATALOG_INDEX and catalog_path, as
    k2-api finds them); None when no output has it."""
    for entry in json.loads((release / "compiled/catalog/index.json").read_text())["products"]:
        doc = json.loads((release / f"compiled/catalog/products/{entry['id']}.json").read_text())
        for out in doc["outputs"]:
            if out["relation"] == relation:
                domain, product = entry["id"].split(".", 1)
                key = sorted((c.get("primaryKeyPosition", len(out["columns"]) + 1), i, c["name"])
                             for i, c in enumerate(out["columns"]) if c["primaryKey"])
                return f"/v1/data/{domain}/{product}/{out['contractId'].removeprefix(entry['id'] + '.')}", [k[2] for k in key]
    return None


def api_get(url: str, key: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """(HTTP status, JSON body) of one GET as the key's principal; status 0 when nothing answered."""
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(query)}", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as x:
        try:
            return x.code, json.load(x)
        except ValueError:
            return x.code, {}
    except (OSError, http.client.HTTPException, ValueError) as x:
        return 0, {"error": type(x).__name__, "message": str(getattr(x, "reason", x))}


def said(status: int, body: dict[str, Any]) -> str:
    if status == 0:
        return f"k2-api did not answer ({body['error']}: {body['message'][:160]})"
    text = f"HTTP {status} {body.get('error', '')}: {str(body.get('message', ''))[:160]}"
    return text + ("; k2-api refused the harness's key, a fault of the harness and never a pass" if status == 401 else "")


def norm(type_: str, v: Any) -> Any:
    """A value in one form whichever leg read it: SQL (Trino's JSON, session zone UTC) or REST
    (k2-api's: a decimal its exact string, a time ISO 8601 in UTC, a bigint a JSON number)."""
    if v is None:
        return None
    if type_.startswith("decimal"):
        return Decimal(str(v))
    if type_ in ("tinyint", "smallint", "integer", "bigint"):
        return int(v)
    if type_ in ("real", "double"):
        return float(v)
    if type_.startswith("timestamp"):
        s = str(v)
        if type_.endswith("with time zone") and s[10:11] == " ":  # Trino's '2026-01-02 15:00:00.000000 UTC'
            head, _, zone = s.rpartition(" ")
            s = head + ("+00:00" if zone == "UTC" else zone)  # a named zone other than UTC fails to parse: the leg fails
        at = datetime.fromisoformat(s)
        return (at.astimezone(UTC) if at.tzinfo else at).isoformat(timespec="microseconds")
    return v


def rest_denied(api: str, path: str, key: str) -> tuple[bool, str]:
    """A denial through k2-api: 403 forbidden, Trino having refused the caller, and nothing else. A
    404 (no route, no reader relation, not yet published) is no refusal: the relation must exist and be refused."""
    status, body = api_get(api + path, key, {"limit": "1"})
    if (status, body.get("error")) == (403, "forbidden"):
        return True, f"GET {path}: 403 forbidden"
    return False, f"expected 403 forbidden, GET {path}: {said(status, body)}" + \
        ("; a 404 is no refusal: the relation must exist and be refused" if status == 404 else "")


def rest_read(trino: Any, e: Any, api: str, path: str, pk: list[str], key: str) -> tuple[bool, str]:
    """A read through k2-api, against the same read over SQL as the same principal in session zone
    UTC. A row asserting rows: one page of every row, equal to SQL's as a multiset. A columns-only
    row: one row, asked by the primary key of the row SQL reads first (a page larger than `limit`
    is a 422 until phase 007's cursors), equal to it. Either way 200, with exactly `visible` as its
    columns; with no row the response states no column, so only the 200 is proven, and it says so."""
    rel = ".".join(f'"{x}"' for x in e.table.split("."))  # the schema's relation pattern holds no quote
    whole = asserts_rows(e)
    try:
        sql = trino.run(e.principal, f"SELECT * FROM {rel}" + ("" if whole else " LIMIT 1"), zone="UTC")
    except NoPassword as x:
        return False, f"no password for {e.principal}: {x}"
    if sql.error:
        return False, f"the SQL read to compare with: {sql.says()}"
    types = dict(sql.columns)
    query = {"limit": str(REST_PAGE if whole else 1)}
    if sql.rows and not whole:
        first = dict(zip(types, sql.rows[0], strict=True))
        # A key value in the form k2-api's filter takes: a boolean in words, a zoned time ISO 8601.
        query |= {f"filter.{c}": ("true" if first[c] else "false") if types[c] == "boolean" else
                  str(norm(types[c], first[c]) if types[c].endswith("with time zone") else first[c])
                  for c in pk if first.get(c) is not None}
    status, body = api_get(api + path, key, query)
    asked = f"GET {path}?{'&'.join(sorted(query))}"
    if status != 200:
        return False, f"expected 200, {asked}: {said(status, body)}"
    rows = body.get("rows", [])
    if not rows:
        if sql.rows:
            return False, f"{asked}: 200 with no row, where SQL reads {len(sql.rows)}"
        return True, f"{asked}: 200, no row, as SQL reads none: an empty answer states no column, so only the 200 is proven"
    shown = sorted({c for r in rows for c in r})
    if shown != sorted(e.visible) or any(sorted(r) != shown for r in rows):
        return False, f"{asked}: 200, columns {shown}, the row says {sorted(e.visible)}"
    # ponytail: a tag moving between the two reads fails the row; read both at one snapshot if a live REST row ever flakes so
    got = Counter(tuple(norm(types.get(c, ""), r[c]) for c in shown) for r in rows)
    want = Counter(tuple(norm(types[c], v) for c, v in sorted(zip(types, row, strict=True))) for row in sql.rows)
    if got != want:
        return False, f"{asked}: 200, columns as visible, but its {len(rows)} row(s) and SQL's {len(sql.rows)} differ"
    return True, f"{asked}: 200, columns {shown}; " + \
        (f"{len(rows)} row(s) equal the SQL leg's as a multiset" if whole else "its row equals the SQL leg's row of that key")


# ---- the release ---------------------------------------------------------------------------------

def load(release: Path) -> Any:
    from k2gen import docset, model
    return model.load(docset.read(release / model.DOCUMENTS_DIR))


def relations(m: Any) -> dict[str, Any]:
    """Every relation the release declares, reader names, bases and their names through the
    writer catalog alike, to its table (a seed's and a connector's platform tables to None)."""
    from k2gen import model
    out: dict[str, Any] = {}
    for p in m.products:
        if p.connector:  # its landing and audit tables in warehouse k2 (emit/lake.py), as checks/access names the landing
            for name in (model.KAFKA_LANDING if p.connector.kind == "kafka" else model.LANDING_FILES, model.AUDIT_CHECKS):
                out[model.lake_relation(p.name, name)] = None
        for t in p.tables:
            if t.build.kind in model.LAKE_BUILDS:
                out[model.reader_relation(t)] = t
                out[model.input_relation(t)] = t
            if t.build.kind in ("dbt", "code"):
                out[model.build_relation(p.domain, p.name, t.name)] = t
                out[model.view_build_relation(p.domain, p.name, t.name)] = t
        if any(t.build.kind in ("dbt", "code") for t in p.tables):
            for path in p.seeds:
                stem = path.removesuffix(".csv")
                out[model.base_relation(p.domain, p.name, stem)] = out[model.build_relation(p.domain, p.name, stem)] = None
    return out


def planted(e: Any, m: Any) -> Any:
    """The product whose reader namespace a denied row names under a name no table has: the
    foreign definer view the run plants (gate h1.31). None for any other row."""
    from k2gen import model
    if not isinstance(e, model.DeniedExpectation):
        return None
    catalog, ns, name = e.table.split("$")[0].split(".")
    for p in m.products:
        built = [t for t in p.tables if t.build.kind in ("dbt", "code")]
        if built and catalog == model.reader_catalog(p.domain) and ns == model.reader_namespace(p.name) \
                and name not in {t.name for t in p.tables}:
            return p
    return None


def unknown(e: Any, m: Any) -> str | None:
    """Why a row names something the running release does not have, or None."""
    from k2gen.emit import trino_rules
    if getattr(e, "principal", None) and e.principal not in trino_rules.known_principals(m):
        return f"{e.principal} is no principal of the running release"
    table = getattr(e, "table", None)
    if table and not table.startswith("system.") and table.split("$")[0] not in relations(m) and not planted(e, m):
        return f"{table} is no relation of the running release (no reader name, base or seed of it)"
    return None


def platform_held(m: Any) -> set[str]:
    """The test principals: granted a role on the access canary and on no other product. Their
    password is the platform's (principals/<id>.password, scripts/principal-secret.py TO_FILE=1),
    so the live suite can log in as them; anyone granted on a real product holds their own."""
    granted: dict[str, set[str]] = {}
    for p in m.products:
        for g in p.grants:
            granted.setdefault(g.principal, set()).add(p.id)
    return {who for who, products in granted.items() if products == {CANARY_PRODUCT}}


def live_skip(e: Any, m: Any) -> str | None:
    """Why a row does not run on the live system, or None when it does."""
    from k2gen import model
    if isinstance(e, model.ProbeExpectation | model.DriftExpectation | model.StorageKeyExpectation):
        return None
    if planted(e, m):
        return "it plants a view, which would write the live catalog; it ran on the throwaway lake at release time"
    who = e.principal
    service = re.fullmatch(r"(svc\.|dbt_|pub_).+", who)
    if not service and who not in platform_held(m):
        return f"{who}'s password is its owner's, not the platform's; its row ran on the throwaway lake at release time"
    named = set(re.findall(r"[a-z0-9_]+", json.dumps(asdict(e))))
    others = sorted(p.id for p in m.products if p.id != CANARY_PRODUCT
                    and {model.fence(p.name), model.base_namespace(p.name)} & named)
    if others:
        return f"it names {', '.join(others)}, not the canary, and a live run risks nothing real"
    return None


# ---- one row -------------------------------------------------------------------------------------

def denial(a: Answer) -> tuple[bool, str]:
    if a.error == DENIED:
        return True, a.says()
    return False, f"expected an access denial, got {a.says()}"


def unsupported(a: Answer) -> tuple[bool, str]:
    if a.error == UNSUPPORTED:
        return True, a.says()
    return False, f"expected {UNSUPPORTED}, got {a.says()}"


def succeeded(a: Answer) -> tuple[bool, str]:
    if a.error:
        return False, f"expected the statement to complete, got {a.says()}"
    return True, "completed without error"


def no_bounds(a: Answer) -> tuple[bool, str]:
    if a.error:
        return False, f"expected SHOW STATS with no bound, got {a.says()}"
    # SHOW STATS: column_name, data_size, distinct_values_count, nulls_fraction, row_count, low_value, high_value
    bounds = [r for r in a.rows if r[5] is not None or r[6] is not None or (r[0] is None and r[4] is not None)]
    if not a.rows or bounds:
        return False, f"a bound or row count shows: {bounds[:3]}" if bounds else "SHOW STATS returned no row"
    return True, f"{len(a.rows)} stats rows, no low, high or row count"


def delay_column(m: Any, e: Any) -> str | None:
    t = relations(m).get(e.table)
    if t is None:
        return None
    role = next((g.role for p in m.products for g in p.grants if g.principal == e.principal and t in p.tables), None)
    policies = [t.policies[role]] if role in t.policies else list(t.policies.values())
    return next((pol.delay.column for pol in policies if pol.delay),
                next((c.name for c in t.columns if c.time == "event"), None))


def read_row(trino: Trino, m: Any, e: Any) -> tuple[bool, str]:
    from k2gen.model import quote_ident, quote_relation
    rel = quote_relation(e.table)
    first = trino.run(e.principal, f"SELECT * FROM {rel} LIMIT 1")
    if first.error:
        return False, f"expected to read it, got {first.says()}"
    bad, seen = [], {}
    cols = sorted(c for c, _ in first.columns)
    if cols != sorted(e.visible):
        bad.append(f"columns {cols}, the row says {sorted(e.visible)}")

    def count(where: str) -> int:
        return int(trino.ok(e.principal, f"SELECT count(*) FROM {rel} WHERE {where}")[0][0])

    for c in e.masked:
        if (n := count(f"{quote_ident(c)} IS NOT NULL")) != 0:
            bad.append(f"{n} row(s) show masked {c}")
    if e.min_row_age_minutes is not None:
        col = delay_column(m, e)
        if col is None:
            bad.append("minRowAgeMinutes, but the table has no delay or event-time column")
        elif (n := count(f"{quote_ident(col)} > current_timestamp - INTERVAL '{int(e.min_row_age_minutes)}' MINUTE")) != 0:
            bad.append(f"{n} row(s) younger than {e.min_row_age_minutes} minutes by {col}")
    if e.must_not_appear and (n := count(e.must_not_appear)) != 0:
        bad.append(f"{n} row(s) match mustNotAppear ({e.must_not_appear})")
    if e.must_appear:
        n = seen["mustAppear"] = count(e.must_appear.where or "true")
        if n < e.must_appear.min_rows:
            bad.append(f"{n} row(s) match mustAppear ({e.must_appear.where or 'any row'}), at least {e.must_appear.min_rows} wanted")
    return not bad, "; ".join(bad) or f"columns {cols}; masked {list(e.masked) or 'none'} null; " + \
        f"min age {e.min_row_age_minutes}; mustNotAppear none; mustAppear {seen.get('mustAppear', 'n/a')}"


def plant(env: dict[str, Any], m: Any, p: Any, name: str) -> Callable[[], None]:
    """A definer view no release declares, put in p's reader namespace by REST (the catalog fence's
    forgery, gate h3.04): p's real publisher as owner, SQL over the base's main. Returns its undo."""
    from k2gen import model
    view = next(t for t in p.tables if t.build.kind in ("dbt", "code"))
    cat = catalog_drift.Catalog(f"{env['lakekeeper']}/catalog", model.warehouse(p.domain))
    ns = model.reader_namespace(p.name)
    meta = cat.view(ns, view.name)
    if meta is None:
        raise RuntimeError(f"no view {ns}.{view.name} to copy for the planted view; stage the lake first")
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    schema = next(x for x in meta["schemas"] if x["schema-id"] == v["schema-id"])
    sql = f"SELECT * FROM {model.quote_relation(model.input_relation(view))}"  # main: the untested rows too
    body = {"name": name, "schema": schema, "properties": {catalog_drift.OWNER_PROPERTY: model.publisher(p.name)},
            "view-version": {"version-id": 1, "timestamp-ms": int(time.time() * 1000), "schema-id": schema["schema-id"],
                             "summary": v["summary"], "representations": [{"type": "sql", "sql": sql, "dialect": "trino"}],
                             "default-namespace": [ns]}}
    st, doc = catalog_drift.call("POST", f"{cat.base}/namespaces/{ns}/views", body)
    if st != 200:
        raise RuntimeError(f"planting {ns}.{name}: HTTP {st} {doc}")

    def undo() -> None:
        st, _ = catalog_drift.call("DELETE", f"{cat.base}/namespaces/{ns}/views/{name}")
        if st not in (200, 204):
            raise RuntimeError(f"removing the planted view {ns}.{name}: HTTP {st}")

    return undo


def catalog_uri(env: dict[str, Any]) -> str | None:
    return env.get("lakekeeper") and f"{env['lakekeeper']}/catalog" or os.environ.get("K2_CATALOG_URI")


def legs(e: Any, m: Any, scope: str, env: dict[str, Any]) -> list[tuple[str, str | None]]:
    """What a row runs here: its legs, each with why it is skipped (None: it runs). A row with `via`
    has one leg per endpoint, `sql` and `rest`; any other row one leg, `row`. The one rule run()
    skips by and tests/access/prove.py expects by, so the two cannot disagree on a skip."""
    from k2gen import model
    if scope == "live" and (why := live_skip(e, m)):
        return [("row", why)]
    if isinstance(e, model.ProbeExpectation):
        return [("row", SKIP_PROBE)]
    if isinstance(e, model.DriftExpectation) and not catalog_uri(env):
        return [("row", SKIP_DRIFT)]
    via = getattr(e, "via", None)
    return [(v, None if v == "sql" else rest_skip(e, scope, env)) for v in via] if via else [("row", None)]


def verdict(trino: Trino, env: dict[str, Any], m: Any, release: Path, scope: str, e: Any) -> tuple[str, str]:
    """The row's verdict: FAIL when a leg fails, else SKIP when one is skipped, else PASS; a row of
    two legs says each one's."""
    if why := unknown(e, m):
        return "FAIL", f"names something that does not exist: {why}"
    plan = legs(e, m, scope, env)
    done = [("SKIP", skip) if skip else rest(trino, env, release, e) if leg == "rest" else check(trino, env, m, release, e)
            for leg, skip in plan]
    if len(done) == 1 and plan[0][0] != "rest":
        return done[0]
    worst = next(v for v in ("FAIL", "SKIP", "PASS") if v in {d[0] for d in done})
    return worst, "; ".join(f"{leg} {v}: {why}" for (leg, _), (v, why) in zip(plan, done, strict=True))


def rest(trino: Trino, env: dict[str, Any], release: Path, e: Any) -> tuple[str, str]:
    """The row's REST leg: its relation's route, as its principal with that principal's key."""
    from k2gen import model
    if (found := route(release, e.table)) is None:
        return "FAIL", f"k2-api has no route for {e.table}: no output of the release's catalog has it as its relation"
    if (key := api_key(env, e.principal)) is None:
        return "FAIL", f"no API key {env['apiKeys']}/{e.principal}.key: the lake makes one per principal of a REST row"
    api, (path, pk) = (os.environ.get("K2_API_URL") or API_URL).rstrip("/"), found
    passed, why = rest_denied(api, path, key) if isinstance(e, model.DeniedExpectation) else rest_read(trino, e, api, path, pk, key)
    return ("PASS" if passed else "FAIL"), why


def check(trino: Trino, env: dict[str, Any], m: Any, release: Path, e: Any) -> tuple[str, str]:
    """The row's own check, over SQL (a read, a denial, a statement), S3 or the catalog API."""
    from k2gen import model
    if isinstance(e, model.StorageKeyExpectation):
        passed, why = storage_key(e)
        return ("PASS" if passed else "FAIL"), why
    if isinstance(e, model.DriftExpectation):
        found = catalog_drift.drift(catalog_uri(env), catalog_drift.expected(release))
        return ("FAIL" if found["differences"] else "PASS"), "; ".join(found["differences"]) or \
            f"clean; {len(found['pending'])} pending, {len(found['skipped'])} skipped (run records, phase 002)"
    try:
        if isinstance(e, model.TableExpectation):
            passed, why = read_row(trino, m, e)
        elif isinstance(e, model.DeniedExpectation):
            product = planted(e, m)
            undo = plant(env, m, product, e.table.split(".")[2]) if product else None
            try:
                passed, why = denial(trino.run(e.principal, f"SELECT count(*) FROM {model.quote_relation(e.table)}"))
            finally:
                if undo:
                    undo()
            why += " (view planted by REST for this row, then removed)" if undo else ""
        else:
            a = trino.run(e.principal, e.sql, session=dict(e.session) or None)
            passed, why = {"denied": denial, "error": unsupported, "noBounds": no_bounds, "ok": succeeded}[e.expect](a)
    except NoPassword as x:
        return "FAIL", f"no password for {e.principal}: {x}"
    return ("PASS" if passed else "FAIL"), why


def what(n: int, e: Any) -> str:
    from k2gen import model
    if isinstance(e, model.TableExpectation):
        return f"{n:02d} {e.principal} reads {e.table}"
    if isinstance(e, model.DeniedExpectation):
        return f"{n:02d} {e.principal} is denied {e.table}"
    if isinstance(e, model.StatementExpectation):
        return f"{n:02d} {e.principal}: {e.sql[:100]}{' (session header)' if e.session else ''} is {e.expect}"
    if isinstance(e, model.ProbeExpectation):
        return f"{n:02d} probe from {e.origin} to {e.target} is {e.expect}"
    if isinstance(e, model.StorageKeyExpectation):
        return f"{n:02d} the {e.engine} storage key's {e.operation} of s3://{e.target} is {e.expect}"
    return f"{n:02d} catalog drift is {e.expect}"


def run(release: Path, scope: str, env_file: Path | None, out: Path | None) -> int:
    from k2gen import docset, model
    from k2gen.checks import access
    m = load(release)
    trino, env = connect(env_file)
    try:  # one request first, so an unreachable Trino is exit 2 and not a row of failures
        urllib.request.urlopen(f"{trino.url}/v1/info", context=trino.ctx, timeout=10).read()
    except OSError as x:
        print(f"access-verify: Trino is unreachable at {trino.url}: {x}", file=sys.stderr)
        return 2
    rows = []
    print(f"access-verify: release {release.name}, scope {scope}, Trino {trino.url}")
    for f in access.check(docset.read(release / model.DOCUMENTS_DIR), None):
        if f.code.startswith("access.expectation-"):
            rows.append({"n": None, "row": None, "verdict": "FAIL", "reason": f"coverage ({f.code}): {f.message}"})
            print(f"FAIL coverage {f.code}: {f.message}", flush=True)
    for n, e in enumerate(m.expectations, 1):
        v, why = verdict(trino, env, m, release, scope, e)
        rows.append({"n": n, "row": {k: v_ for k, v_ in asdict(e).items() if v_ not in (None, (), {})} if is_dataclass(e) else None,
                     "verdict": v, "reason": why})
        print(f"{v} {what(n, e)}: {why}", flush=True)
    counts = {k: sum(r["verdict"] == k for r in rows) for k in ("PASS", "FAIL", "SKIP")}
    code = 1 if counts["FAIL"] or not counts["PASS"] else 0
    print(f"access-verify: {len(rows)} rows: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP; exit {code}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"release": release.name, "setSha256": m.provenance.set_sha256, "scope": scope,
                                   "trino": trino.url, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                   "exit": code, "counts": counts, "rows": rows}, indent=2, default=list) + "\n")
    return code


# ---- stage: the lake the release describes, on a throwaway lake ----------------------------------

_REF = re.compile(r"\{\{\s*ref\(\s*'([a-z0-9_]+)'\s*\)\s*\}\}")
_CONFIG = re.compile(r"\{\{-?\s*config\(.*?\)\s*-?\}\}", re.S)


def ref_order(tables: list[Any], sql: dict[str, str], names: dict[str, str]) -> list[Any]:
    """The tables with each after the product's own models its SQL names by ref(): a model is staged
    by CREATE TABLE AS, so one that reads a sibling (security-master's instrument reads
    venue_symbol_claim) needs it made first. Contract order is kept among the rest; a cycle is dbt's
    to refuse (make test-dbt-tree), so it falls back to contract order here."""
    by_name = {t.name: t for t in tables}
    needs = {t.name: {names[r] for r in _REF.findall(sql[t.name]) if names.get(r) in by_name} - {t.name} for t in tables}
    out: list[Any] = []
    while len(out) < len(tables):
        ready = [t for t in tables if t not in out and needs[t.name] <= {o.name for o in out}]
        out += ready or [t for t in tables if t not in out]
    return out


def stage(release: Path, env_file: Path) -> int:
    from k2gen import model
    from k2gen.emit import lake
    m = load(release)
    trino, env = connect(env_file)
    uri = f"{env['lakekeeper']}/catalog"
    # Spark's tables (warehouse k2) are made by Spark when the lake comes up (throwaway-lake.sh spark_ddl), as deploy's ddl step makes them.
    spark = [f.stem.split(".") for f in sorted((release / "compiled/lake/ddl/spark").glob("*.sql"))]
    if missing := [".".join(s) for s in spark if catalog_drift.Catalog(uri, s[0]).table(s[1], s[2]) is None]:
        raise SystemExit(f"stage: the release's Spark tables {missing} are not in the catalog; scripts/lib/throwaway-lake.sh up makes them")
    reads = {e.table for e in m.expectations if isinstance(e, model.TableExpectation) and asserts_rows(e)}
    for w in json.loads((release / model.STORAGE_PLAN).read_text())["warehouses"]:
        cat = catalog_drift.Catalog(uri, w["name"])
        for ns in w["namespaces"]:
            st, _ = catalog_drift.call("POST", f"{cat.base}/namespaces", {"namespace": [ns]})
            assert st in (200, 409), f"namespace {w['name']}.{ns}: HTTP {st}"
    commits: dict[str, list[dict[str, Any]]] = {}
    built = []
    for p in sorted(m.products, key=lambda p: p.id):
        tables = [t for t in p.tables if t.build.kind in ("dbt", "code")]
        if not tables:
            continue
        dbt, cat = model.builder(p.name), catalog_drift.Catalog(uri, model.warehouse(p.domain))
        names = {model.dbt_seed(p.name, s.removesuffix(".csv")): s.removesuffix(".csv") for s in p.seeds}
        names |= {model.dbt_model(p.name, t.name): t.name for t in tables}
        for path, data in sorted(p.seeds.items()):
            rows = list(csv.reader(io.StringIO(data.decode())))
            head, body = rows[0], rows[1:]
            types = ["bigint" if all(re.fullmatch(r"-?[0-9]+", r[i]) for r in body) else "varchar" for i in range(len(head))]
            rel = model.build_relation(p.domain, p.name, path.removesuffix(".csv"))
            trino.ok(dbt, f"CREATE TABLE {model.quote_relation(rel)} ({', '.join(f'{model.quote_ident(h)} {ty}' for h, ty in zip(head, types, strict=True))})")
            values = ", ".join("(" + ", ".join(v if ty == "bigint" else model.sql_literal(v) for v, ty in zip(r, types, strict=True)) + ")" for r in body)
            trino.ok(dbt, f"INSERT INTO {model.quote_relation(rel)} VALUES {values}")
        for t in ref_order(tables, {t.name: p.models[f"{t.name}.sql"] for t in tables}, names):
            sql = _CONFIG.sub("", p.models[f"{t.name}.sql"])
            refs = {name: model.quote_relation(model.build_relation(p.domain, p.name, table)) for name, table in names.items()}
            sql = _REF.sub(lambda r, refs=refs: refs[r.group(1)], sql)
            stand_in = "{{" in sql or "{%" in sql
            if stand_in:
                # Jinja beyond ref() and config() (source(), a K2 macro, is_incremental()) is dbt's to render
                # (make test-dbt builds every model): here the output is its contract's columns with one row,
                # its key a literal and the rest null, enough for the rows that deny it, the drift check, the
                # marker and a read row that names columns alone. A read row asserting rows needs the
                # model's own rows, so one is refused.
                if {model.reader_relation(t), model.input_relation(t)} & reads:
                    raise SystemExit(f"stage: {p.id} {t.name}: a read row asserts its rows, and only dbt renders its model")
                sql = "SELECT " + ", ".join(f"CAST({placeholder(c) if c is key_column(t) else 'NULL'} AS {c.physical_type}) "
                                            f"AS {model.quote_ident(c.name)}" for c in t.columns)
            rel = model.build_relation(p.domain, p.name, t.name)
            trino.ok(dbt, f"CREATE TABLE {model.quote_relation(rel)} AS {sql}")
            if stand_in:  # its shape is the contract's, as Trino describes the table it made: names, order, types
                got = [r[:2] for r in trino.ok(dbt, f"DESCRIBE {model.quote_relation(rel)}")]
                if got != (want := [[c.name, c.physical_type] for c in t.columns]):
                    raise SystemExit(f"stage: {p.id} {t.name}: the placeholder is {got}, its contract {want}")
                print(f"stage: {p.id} {t.name}: a placeholder of one row in its contract's columns and types (DESCRIBE); "
                      "a columns-only read row of it proves the reader view's columns and the reader's permission "
                      "under the release's rules, never the model's rows (make test-dbt builds the model)")
            meta = cat.table(model.base_namespace(p.name), t.name)
            tested = meta["refs"]["main"]["snapshot-id"]
            commits.setdefault(model.warehouse(p.domain), []).append({
                "identifier": {"namespace": [model.base_namespace(p.name)], "name": t.name},
                "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested},
                                 {"type": "assert-ref-snapshot-id", "ref": model.AUDITED_TAG, "snapshot-id": None}],
                "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": tested}]})
            built.append((p, t))
    for w, changes in commits.items():
        cat = catalog_drift.Catalog(uri, w)
        st, doc = catalog_drift.call("POST", f"{cat.base}/transactions/commit", {"table-changes": changes})
        assert st in (200, 204), f"tag commit {w}: HTTP {st} {doc}"
    for v in json.loads((release / model.VIEWS).read_text())["views"]:
        view = model.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
        trino.ok(view.owner, lake.create_view(view))
    for p, t in built:
        rel = model.quote_relation(model.build_relation(p.domain, p.name, t.name))
        trino.ok(model.builder(p.name), f"INSERT INTO {rel} SELECT {moved_key(t)} FROM {rel}")
    for ddl in sorted((release / "compiled/lake/ddl/trino").glob("*.sql")):
        text = "\n".join(line for line in ddl.read_text().splitlines() if not line.startswith("--")).strip().rstrip(";")
        trino.ok("svc.platform", text)
    print(f"stage: {release.name}: {len(built)} output(s) built, tagged and viewed, each with one untested write after its tag; "
          f"{len(list((release / 'compiled/lake/ddl/trino').glob('*.sql')))} platform table(s)")
    return 0


def asserts_rows(e: Any) -> bool:
    """Whether a read row asserts anything of the rows (a mask, an age, mustNotAppear, mustAppear),
    not only the column set and the permission to read: what a placeholder output cannot show."""
    return bool(e.masked or e.min_row_age_minutes is not None or e.must_not_appear or e.must_appear)


def key_column(t: Any) -> Any:
    """t's primary key's first column, else its first column."""
    return min((c for c in t.columns if c.primary_key_position), key=lambda c: c.primary_key_position, default=t.columns[0])


def placeholder(c: Any) -> str:
    """A literal of c's type for a stand-in output's one row (stage): what moved_key can move."""
    return ("1" if c.physical_type in ("bigint", "integer") else "DATE '2000-01-01'" if c.physical_type == "date"
            else "'stage'")


def moved_key(t: Any, by: int = 1000) -> str:
    """Every column of t, its primary key's first column moved so that no row of the result is a
    tested one: an integer key plus `by`, a date key `by` years on, any other key prefixed zz-untested-."""
    from k2gen.model import quote_ident
    key = key_column(t)
    q = quote_ident(key.name)
    moved = (f"{q} + {by}" if key.physical_type in ("bigint", "integer") else f"date_add('year', {by}, {q})"
             if key.physical_type == "date" else f"'zz-untested-' || CAST({q} AS varchar)")
    return ", ".join(f"{moved} AS {quote_ident(c.name)}" if c is key else quote_ident(c.name) for c in t.columns)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="every expectation row against a running Trino")
    r.add_argument("--release", required=True, type=Path)
    r.add_argument("--scope", required=True, choices=("release", "live"))
    r.add_argument("--env", type=Path, help="a throwaway lake's env.json (scope release)")
    r.add_argument("--out", type=Path, help="write the JSON result here")
    s = sub.add_parser("stage", help="build, tag and view the release's dbt products on a throwaway lake")
    s.add_argument("--release", required=True, type=Path)
    s.add_argument("--env", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        return stage(a.release.resolve(), a.env)
    if a.scope == "release" and not a.env:
        ap.error("scope release runs on a throwaway lake: --env <its env.json>")
    return run(a.release.resolve(), a.scope, a.env, a.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

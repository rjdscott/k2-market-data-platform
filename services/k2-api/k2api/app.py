"""k2-api: read-only REST over the data products of the release it runs. Its routes, parameters
and error bodies are services/k2-api/openapi.yaml, frozen in phase 001 and served as it is.

Every request reads the release the pointer names at that moment, so a deploy needs no restart.
k2-api decides no access: what a caller may read is asked of Trino as the caller (db.run), and
every name a statement holds comes from the release's catalog document, validated and quoted.
It never writes, accepts SQL, evaluates a rule, reads storage or Lakekeeper, or keeps a row.
"""

import base64
import json
import logging
import os
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from k2api import db, keys

RELEASES = Path("/k2/releases")  # k2gen.model.RELEASES_MOUNT, mounted read-only
POINTER = "pointers/k2-api"  # scripts/deploy.py POINTERS: deploy moves it inside `tools`
PRINCIPALS = "compiled/api/principals.json"  # k2gen.model.API_PRINCIPALS: whom k2-api may act as
INDEX = "compiled/catalog/index.json"  # k2gen.model.CATALOG_INDEX
VIEWS = "compiled/trino/views.json"  # k2gen.model.VIEWS: each reader view's base
MAX_LIMIT = 10_000  # openapi.yaml `limit`: its maximum and its default
REF_TYPE = {"audited": "TAG", "main": "BRANCH"}  # k2gen.model.tested_ref, as `$refs` types it
NOT_YET = {"TABLE_NOT_FOUND", "SCHEMA_NOT_FOUND"}  # a base before its first build
# The column types an equality filter takes (k2gen.model.PHYSICAL_TYPE's scalars but varbinary and time).
FILTERABLE = re.compile(r"varchar|boolean|integer|bigint|real|double|date|uuid|decimal\([0-9]+,[0-9]+\)"
                        r"|timestamp\(6\)|timestamp\(6\) with time zone")
SLUG = re.compile(r"[a-z][a-z0-9-]*")  # openapi.yaml Domain, Product
TABLE = re.compile(r"[a-z][a-z0-9_]*")  # openapi.yaml Table
NAME = re.compile(r"[a-z][a-z0-9_]*")  # a relation part or a column (catalog-product.schema.json)
SITE = Path("/srv/k2-site")  # the k2-site volume, read-only: <name>/current/ of each generated site
CURRENT = "current"  # the link each generator renames onto its newest tree (k2site.CURRENT)
# The catalog site is static HTML with one inline <style> and no script, image or font.
CATALOG_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# dbt's docs app: one index.html of inline script and style (AngularJS, which falls back to its
# no-eval mode under a CSP), data: fonts and images, manifest.json and catalog.json fetched from
# this origin. Nothing else: its usage tracker and any external asset are refused.
DBT_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'")
log = logging.getLogger("k2api")
SPEC = yaml.safe_load((Path(__file__).parent.parent / "openapi.yaml").read_text())

# The frozen document is the one served; FastAPI's generated one and its doc pages are off.
app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


class Site(StaticFiles):
    """A generated site, <SITE>/<name>/current/, as files: not an API route, so not in openapi.yaml.
    `current` is read at every lookup, so a new tree is served from the next request on; before the
    first one the answer is 503. Every answer carries the site's headers."""

    def __init__(self, name: str, csp: str) -> None:
        super().__init__(html=True, check_dir=False)
        self.name = name
        self.headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": csp}

    def tree(self) -> str | None:
        """The real directory `current` names, only when it is a directory directly beside it:
        `current -> /` would otherwise serve this container's files, its secrets among them."""
        site = os.path.join(os.path.realpath(SITE), self.name)
        tree = os.path.realpath(os.path.join(site, CURRENT))
        return tree if os.path.dirname(tree) == site and os.path.isdir(tree) else None

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        """Starlette's lookup with follow_symlink=False, over the tree resolved once for this lookup:
        the path is resolved, links and all, and found only inside the tree, so a planted
        current/evil -> /etc is not followed."""
        tree = self.tree()
        if tree is None or path.startswith(("/", "\\")):
            return "", None
        full = os.path.realpath(os.path.join(tree, path))
        if os.path.commonpath([full, tree]) != tree:
            return "", None
        try:
            return full, os.stat(full)
        except (FileNotFoundError, NotADirectoryError):
            return "", None

    async def check_config(self) -> None:
        """No check at the first request: a site not generated yet is get_response's 503."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if self.tree() is None:
            response: Response = PlainTextResponse("not generated yet\n", 503)
        else:
            try:
                response = await super().get_response(path, scope)
            except HTTPException as e:  # 404, 405: answered here, so they carry the headers too
                response = PlainTextResponse(f"{e.detail}\n", e.status_code, e.headers)
        response.headers.update(self.headers)
        return response


app.mount("/catalog", Site("catalog", CATALOG_CSP))  # platform/catalog-site/k2site.py --out /site/catalog
app.mount("/dbt-docs", Site("dbt-docs", DBT_DOCS_CSP))  # k2-dbt docs --out /site/dbt-docs


class Refused(Exception):
    """An answer in the frozen Error shape, {"error": <stable code>, "message": <text>}."""

    def __init__(self, status: int, error: str, message: str) -> None:
        self.status, self.error, self.message = status, error, message


@app.exception_handler(Refused)
def _refused(request: Request, e: Refused) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None  # RFC 6750
    return JSONResponse({"error": e.error, "message": e.message}, e.status, headers)


@app.exception_handler(HTTPException)
def _http(request: Request, e: HTTPException) -> JSONResponse:  # no route, or not a GET
    error = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
    return JSONResponse({"error": error, "message": str(e.detail)}, e.status_code)


def release() -> Path:
    """The directory of the release the pointer names now, a child of the releases root."""
    try:
        path = (RELEASES / POINTER).resolve(strict=True)
        named = path.parent == RELEASES.resolve() and read(path, "manifest.json")["release"] == path.name
    except (OSError, ValueError, KeyError):
        named = False
    if not named:
        raise Refused(503, "no_release", "the k2-api pointer names no release to serve")
    return path


def read(rel: Path, name: str) -> Any:
    return json.loads((rel / name).read_text())


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/openapi.json")
def openapi() -> dict[str, Any]:
    return SPEC


@app.get("/v1/release")
def get_release() -> dict[str, str]:
    manifest = read(release(), "manifest.json")
    return {k: manifest[k] for k in ("release", "tag", "commit", "builtAt")}


def caller(request: Request, rel: Path, required: bool) -> str | None:
    """The principal of the request's key, or None without one where a key is optional. A key
    the map does not know, or whose principal the release does not list, is 401: k2-api acts
    only as a principal the release's rules let svc.api become (k2gen.emit.api)."""
    header = request.headers.get("authorization")
    if header is None:
        if required:
            raise Refused(401, "unauthorized", "this route needs Authorization: Bearer <key>")
        return None
    principal = keys.principal(header)
    if principal is None or principal not in read(rel, PRINCIPALS)["principals"]:
        raise Refused(401, "unauthorized", "the key is not one this release serves")
    return principal


def product_doc(rel: Path, domain: str, product: str) -> dict[str, Any]:
    """The product's catalog document, found through the release's index: the path is built only
    from an id the index lists, never from the request."""
    pid = f"{domain}.{product}"
    if SLUG.fullmatch(domain) and SLUG.fullmatch(product):
        for entry in read(rel, INDEX)["products"]:
            if entry["id"] == pid:
                return read(rel, f"compiled/catalog/products/{entry['id']}.json")  # k2gen.model.catalog_path
    raise Refused(404, "not_found", "no such product in the release")


def output(doc: dict[str, Any], table: str) -> dict[str, Any]:
    if TABLE.fullmatch(table):
        for out in doc["outputs"]:
            if out["contractId"] == f"{doc['id']}.{table}":
                return out
    raise Refused(404, "not_found", f"no such table in {doc['id']}")


def ident(name: str) -> str:
    """A name from the release, held to the catalog's grammar and double-quoted."""
    if not NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not a name the catalog allows")
    return f'"{name}"'


def quoted(relation: str, suffix: str = "") -> str:
    """`a.b.c` as `"a"."b"."c"`; with suffix `$refs`, that table's metadata table `"c$refs"`."""
    parts = relation.split(".")
    if len(parts) != 3:
        raise ValueError(f"{relation!r} is not <catalog>.<schema>.<table>")
    return ".".join([ident(parts[0]), ident(parts[1]), ident(parts[2])[:-1] + suffix + '"'])


def columns(out: dict[str, Any]) -> list[str]:
    return [c["name"] for c in out["columns"]]


def primary_key(out: dict[str, Any]) -> list[str]:
    """The declared key, by primaryKeyPosition, then declaration order: what a page is sorted by,
    so SQL and REST answers compare row for row and a phase 007 cursor has an order."""
    key = [(c.get("primaryKeyPosition", len(out["columns"]) + 1), i, c["name"])
           for i, c in enumerate(out["columns"]) if c["primaryKey"]]
    return [name for _, _, name in sorted(key)]


def ask(user: str, sql: str, params: list[Any]) -> list[list[Any]]:
    try:
        return db.run(user, sql, params)
    except db.Unavailable as e:
        log.warning("trino did not answer: %s", e)
        raise Refused(503, "trino_unavailable", "Trino did not answer; try again") from None


def probe(principal: str, out: dict[str, Any]) -> None:
    """A query as the caller that reads no row of out's reader relation: db.Denied when Trino
    refuses the caller, db.Failed on another error."""
    ask(principal, f"SELECT {', '.join(map(ident, columns(out)))} FROM {quoted(out['relation'])} LIMIT 0", [])


def unpublished(e: db.Failed) -> bool:
    """Whether a probe failed only for want of a tested snapshot, so `$refs` decides 404 or a read.
    Taken from Trino 484's source, unproven against a running Trino: a guarded view over a base with
    no `audited` tag is INVALID_VIEW (Trino wraps any error in a view's body; Iceberg's own is
    INVALID_ARGUMENTS), a base or connector table before its first build TABLE_NOT_FOUND or
    SCHEMA_NOT_FOUND. The same source checks the caller's SELECT only after the whole statement is
    analysed, so a denied caller may meet these before PERMISSION_DENIED: the REST leg decides."""
    return e.name in {"INVALID_VIEW", "INVALID_ARGUMENTS", *NOT_YET}


def access(principal: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Per output with a reader relation, whether Trino lets the caller read it, asked with a
    query that reads no row. Any refusal, a rule or a relation not yet published, is False."""
    answer = []
    for out in doc["outputs"]:
        if out["relation"] is None:  # build serving: only its serving copy holds it
            continue
        try:
            probe(principal, out)
            allowed = True
        except (db.Denied, db.Failed):
            allowed = False
        answer.append({"relation": out["relation"], "allowed": allowed})
    return answer


@app.get("/v1/products")
def list_products() -> list[dict[str, Any]]:
    return read(release(), INDEX)["products"]


@app.get("/v1/products/{domain}/{product}")
def get_product(domain: str, product: str, request: Request) -> dict[str, Any]:
    rel = release()
    principal = caller(request, rel, required=False)
    doc = product_doc(rel, domain, product)
    return doc if principal is None else doc | {"access": access(principal, doc)}


@app.get("/v1/products/{domain}/{product}/contracts/{table}.odcs.yaml")
def get_contract(domain: str, product: str, table: str) -> Response:
    rel = release()
    out = output(product_doc(rel, domain, product), table)
    path = (rel / out["contractFile"]).resolve()
    if not path.is_relative_to(rel / "documents" / "contracts") or not path.is_file():
        raise Refused(404, "not_found", "the release holds no such contract")
    return Response(path.read_bytes(), media_type="application/yaml")


def typed(value: str, type_: str) -> Any:
    """A filter value as the Python value the Trino client binds as a literal of the column's
    declared type; ValueError when it is not one."""
    if any(c < " " for c in value):
        raise ValueError("a control character")
    if type_ == "varchar":
        return value
    if type_ == "boolean":
        return {"true": True, "false": False}[value]
    if type_ in ("integer", "bigint"):
        bound = 2**31 if type_ == "integer" else 2**63
        if not re.fullmatch(r"-?[0-9]{1,19}", value) or not -bound <= int(value) < bound:
            raise ValueError("out of range")
        return int(value)
    if type_.startswith("decimal("):
        if not re.fullmatch(r"-?[0-9]{1,38}(\.[0-9]{1,38})?", value):
            raise ValueError("not a decimal")
        return Decimal(value)
    if type_ in ("real", "double"):
        if not re.fullmatch(r"-?[0-9]{1,38}(\.[0-9]{1,38})?([eE][-+]?[0-9]{1,3})?", value):
            raise ValueError("not a number")
        return float(value)
    if type_ == "date":
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("not a date")
        return date.fromisoformat(value)
    if type_ == "uuid":
        return UUID(value)
    at = datetime.fromisoformat(value)  # a timestamp: with an offset exactly when the type has a time zone
    if (at.tzinfo is None) != (type_ == "timestamp(6)"):
        raise ValueError("a time zone where the type has none, or none where it has one")
    return at if at.tzinfo is None else at.astimezone(UTC)


def parameters(request: Request, out: dict[str, Any]) -> tuple[list[str], int, list[tuple[str, Any]]]:
    """fields, limit and the equality filters, held to the output's declared columns."""
    items = request.query_params.multi_items()
    given = dict(items)
    if len(given) != len(items):
        raise Refused(400, "bad_request", "a parameter is given more than once")
    for name in given:
        if name not in ("fields", "limit", "cursor", "as_of") and not name.startswith("filter."):
            raise Refused(400, "unknown_parameter", f"{name[:64]!r} is not a parameter of this route")
    if "as_of" in given:
        raise Refused(422, "as_of_unavailable", "as_of is not offered (the owner decides it in phase 007)")
    if "cursor" in given:
        raise Refused(400, "bad_cursor", "no page carries a cursor until phase 007")
    declared = {c["name"]: c["type"] for c in out["columns"]}
    fields = given["fields"].split(",") if "fields" in given else list(declared)
    if len(set(fields)) != len(fields) or not set(fields) <= set(declared):
        raise Refused(400, "unknown_column", "fields names a column the contract does not declare, or one twice")
    limit = given.get("limit", str(MAX_LIMIT))
    if not re.fullmatch(r"[0-9]{1,5}", limit) or not 1 <= int(limit) <= MAX_LIMIT:
        raise Refused(400, "bad_limit", f"limit is an integer from 1 to {MAX_LIMIT}")
    filters = []
    for name, value in given.items():
        if not name.startswith("filter."):
            continue
        column = name.removeprefix("filter.")
        if column not in declared:
            raise Refused(400, "unknown_column", f"{name[:64]!r} names a column the contract does not declare")
        if not FILTERABLE.fullmatch(declared[column]):
            raise Refused(400, "bad_filter", f"{name}: a {declared[column]} column takes no equality filter")
        try:
            filters.append((column, typed(value, declared[column])))
        except (ValueError, KeyError):
            raise Refused(400, "bad_filter", f"{name}: not a {declared[column]} value") from None
    return fields, int(limit), filters


def tested_snapshot(rel: Path, out: dict[str, Any]) -> int | None:
    """The id of the snapshot the output's tested ref names, read as svc.api from the `$refs` of
    the table the ref is on: a view's base (views.json), else the relation itself. None when
    the table was never tested."""
    source = out["relation"]
    if out["build"] in ("dbt", "code"):
        source = {v["relation"]: v["base"] for v in read(rel, VIEWS)["views"]}[source]
    ref = out["testedRef"]
    sql = f'SELECT "snapshot_id" FROM {quoted(source, "$refs")} WHERE "name" = ? AND "type" = ?'
    try:
        rows = ask(db.API_USER, sql, [ref, REF_TYPE[ref]])
    except db.Failed as e:
        if e.name in NOT_YET:
            return None
        log.error("reading %s$refs failed: %s", source, e.name)
        raise Refused(503, "trino_unavailable", "Trino could not read the tested snapshot") from None
    except db.Denied:
        log.error("svc.api was refused %s$refs: the release's rules and catalog disagree", source)
        raise Refused(503, "trino_unavailable", "Trino could not read the tested snapshot") from None
    return rows[0][0] if rows else None


def read_as(principal: str, sql: str, values: list[Any], out: dict[str, Any]) -> list[list[Any]]:
    try:
        return ask(principal, sql, values)
    except db.Denied:
        raise Refused(403, "forbidden", f"Trino denied the caller {out['relation']}") from None
    except db.Failed as e:
        log.error("reading %s failed: %s", out["relation"], e.name)
        raise Refused(503, "query_failed", "Trino could not run the read") from None


def _json(value: Any) -> Any:
    """What JSON has no type for: a decimal as its exact string, never a float; a time in ISO 8601."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)  # uuid


@app.get("/v1/data/{domain}/{product}/{table}")
def get_data(domain: str, product: str, table: str, request: Request) -> Response:
    rel = release()
    principal = caller(request, rel, required=True)
    out = output(product_doc(rel, domain, product), table)
    if out["relation"] is None:
        raise Refused(404, "not_found", f"{out['contractId']} has no reader relation: only its serving copy holds it")
    fields, limit, filters = parameters(request, out)
    where = " AND ".join(f"{ident(column)} = ?" for column, _ in filters)
    # ponytail: a table that declares no key is read in Trino's order; give it a key to sort it
    order = ", ".join(map(ident, primary_key(out)))
    sql = (f"SELECT {', '.join(map(ident, fields))} FROM {quoted(out['relation'])}"
           f"{' WHERE ' + where if filters else ''}{' ORDER BY ' + order if order else ''}"
           f" LIMIT {limit + 1}")  # one row past the limit: result_too_large
    values = [value for _, value in filters]
    # The caller first: a relation's publication state is told only to a caller Trino lets read it.
    try:
        probe(principal, out)
    except db.Denied:
        raise Refused(403, "forbidden", f"Trino denied the caller {out['relation']}") from None
    except db.Failed as e:
        if not unpublished(e):
            log.error("probing %s failed: %s", out["relation"], e.name)
            raise Refused(503, "query_failed", "Trino could not run the read") from None
    snapshot = tested_snapshot(rel, out)
    for _ in range(2):  # the tag read before and after; when it moved, once more
        if snapshot is None:
            raise Refused(404, "not_published", f"{out['contractId']} has no tested snapshot yet")
        rows = read_as(principal, sql, values, out)
        after = tested_snapshot(rel, out)
        if after == snapshot:
            break
        snapshot = after
    else:
        raise Refused(503, "snapshot_moving", "the tested snapshot moved during two reads; try again")
    if len(rows) > limit:
        raise Refused(422, "result_too_large", f"more than {limit} rows match; narrow the filters (cursors arrive in phase 007)")
    log.info("%s read %s at snapshot %s: %d rows", principal, out["contractId"], snapshot, len(rows))
    page = {"release": rel.name, "snapshot": str(snapshot), "rows": [dict(zip(fields, row, strict=True)) for row in rows], "next": None}
    return Response(json.dumps(page, default=_json), media_type="application/json")

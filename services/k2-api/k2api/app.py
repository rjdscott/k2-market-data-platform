"""k2-api: read-only REST over the data products of the release it runs. Its routes, parameters
and error bodies are services/k2-api/openapi.yaml, frozen in phase 001 and served as it is.

Every request reads the release the pointer names at that moment, so a deploy needs no restart.
k2-api decides no access: what a caller may read is asked of Trino as the caller (db.run), and
every name a statement holds comes from the release's catalog document, validated and quoted.
It never writes, accepts SQL, evaluates a rule, reads storage or Lakekeeper, or keeps a row.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from k2api import db, keys

RELEASES = Path("/k2/releases")  # k2gen.model.RELEASES_MOUNT, mounted read-only
POINTER = "pointers/k2-api"  # scripts/deploy.py POINTERS: deploy moves it inside `tools`
PRINCIPALS = "compiled/api/principals.json"  # k2gen.model.API_PRINCIPALS: whom k2-api may act as
INDEX = "compiled/catalog/index.json"  # k2gen.model.CATALOG_INDEX
SLUG = re.compile(r"[a-z][a-z0-9-]*")  # openapi.yaml Domain, Product
TABLE = re.compile(r"[a-z][a-z0-9_]*")  # openapi.yaml Table
NAME = re.compile(r"[a-z][a-z0-9_]*")  # a relation part or a column (catalog-product.schema.json)
log = logging.getLogger("k2api")
SPEC = yaml.safe_load((Path(__file__).parent.parent / "openapi.yaml").read_text())

# The frozen document is the one served; FastAPI's generated one and its doc pages are off.
app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


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
    except OSError:
        raise Refused(503, "no_release", "k2-api has no release to serve") from None
    if path.parent != RELEASES.resolve() or read(path, "manifest.json")["release"] != path.name:
        raise Refused(503, "no_release", "the k2-api pointer does not name a release")
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


def ask(user: str, sql: str, params: list[Any]) -> list[list[Any]]:
    try:
        return db.run(user, sql, params)
    except db.Unavailable as e:
        log.warning("trino did not answer: %s", e)
        raise Refused(503, "trino_unavailable", "Trino did not answer; try again") from None


def access(principal: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Per output with a reader relation, whether Trino lets the caller read it, asked with a
    query that reads no row. Any refusal, a rule or a relation not yet published, is False."""
    answer = []
    for out in doc["outputs"]:
        if out["relation"] is None:  # build serving: only its serving copy holds it
            continue
        sql = f"SELECT {', '.join(map(ident, columns(out)))} FROM {quoted(out['relation'])} LIMIT 0"
        try:
            ask(principal, sql, [])
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


def _later(*args: Any, **kwargs: Any) -> None:
    raise Refused(501, "not_implemented", "built in a later step of phase 003")


@app.get("/v1/data/{domain}/{product}/{table}")
def get_data(domain: str, product: str, table: str, request: Request) -> None:
    caller(request, release(), required=True)
    _later()

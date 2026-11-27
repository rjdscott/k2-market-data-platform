"""k2-api: read-only REST over the data products of the release it runs. Its routes, parameters
and error bodies are services/k2-api/openapi.yaml, frozen in phase 001 and served as it is.

Every request reads the release the pointer names at that moment, so a deploy needs no restart.
"""

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from k2api import keys

RELEASES = Path("/k2/releases")  # k2gen.model.RELEASES_MOUNT, mounted read-only
POINTER = "pointers/k2-api"  # scripts/deploy.py POINTERS: deploy moves it inside `tools`
PRINCIPALS = "compiled/api/principals.json"  # k2gen.model.API_PRINCIPALS: whom k2-api may act as
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


def _later(*args: Any, **kwargs: Any) -> None:
    raise Refused(501, "not_implemented", "built in a later step of phase 003")


for _path in ("/v1/products", "/v1/products/{domain}/{product}/contracts/{table}.odcs.yaml"):
    app.get(_path)(_later)


@app.get("/v1/products/{domain}/{product}")
def get_product(domain: str, product: str, request: Request) -> None:
    caller(request, release(), required=False)
    _later()


@app.get("/v1/data/{domain}/{product}/{table}")
def get_data(domain: str, product: str, table: str, request: Request) -> None:
    caller(request, release(), required=True)
    _later()

"""W3-8 (phase 003): the catalog documents k2-api serves, or a release holds, against the schema frozen in
phase 001 (contracts/schema/catalog-product.schema.json, its $defs/index for the index). Run by
scripts/verify/api-schema.sh, which pins jsonschema and referencing:

    api_schema.py [<base url>]        GET /v1/products, each /v1/products/{domain}/{product}, /v1/openapi.json
    api_schema.py --release <dir>     the release's compiled/catalog/ documents, no k2-api needed

One PASS or FAIL line per document, then `<n> PASS, <m> FAIL`; exit 1 on any FAIL. Anonymous requests
only: `access` is added for a keyed caller and is not part of the catalog document.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts/schema"
CATALOG = json.loads((SCHEMAS / "catalog-product.schema.json").read_text())


def validator(pointer: str = "") -> jsonschema.Draft202012Validator:
    """The catalog schema at `pointer`, its $refs into the other schemas resolved by their $id."""
    docs = [json.loads(p.read_text()) for p in sorted(SCHEMAS.glob("*.schema.json"))]
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs if "$id" in d)
    return jsonschema.Draft202012Validator({"$ref": f"{CATALOG['$id']}#{pointer}"}, registry=registry)


PRODUCT, INDEX = validator(), validator("/$defs/index")
SUMMARIES = validator("/$defs/index/properties/products")


def check(results: list[bool], name: str, v: jsonschema.Draft202012Validator, doc: Any) -> None:
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in v.iter_errors(doc)]
    results.append(not errors)
    print(f"PASS {name}" if not errors else f"FAIL {name}: {'; '.join(errors[:3])}")


def offline(release: Path) -> list[bool]:
    results: list[bool] = []
    index = json.loads((release / "compiled/catalog/index.json").read_text())
    check(results, "compiled/catalog/index.json", INDEX, index)
    files = {p.stem: p for p in sorted((release / "compiled/catalog/products").glob("*.json"))}
    for pid in sorted({e["id"] for e in index.get("products", [])} | set(files)):
        if pid not in files or pid not in {e["id"] for e in index.get("products", [])}:
            results.append(False)
            print(f"FAIL {pid}: in {'the index' if pid not in files else 'products/'} alone")
            continue
        doc = json.loads(files[pid].read_text())
        check(results, f"compiled/catalog/products/{pid}.json", PRODUCT, doc)
        if doc.get("id") != pid:
            results.append(False)
            print(f"FAIL compiled/catalog/products/{pid}.json: its id is {doc.get('id')!r}")
    return results


def get(base: str, path: str) -> Any:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as r:
        return json.load(r)


def online(base: str) -> list[bool]:
    results: list[bool] = []
    try:
        products = get(base, "/v1/products")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"FAIL GET {base}/v1/products: {e}")
        return [False]
    check(results, "GET /v1/products", SUMMARIES, products)
    for entry in products if isinstance(products, list) else []:
        domain, _, name = str(entry.get("id", "")).partition(".")
        path = f"/v1/products/{domain}/{name}"
        try:
            doc = get(base, path)
        except (urllib.error.URLError, OSError, ValueError) as e:
            results.append(False)
            print(f"FAIL GET {path}: {e}")
            continue
        check(results, f"GET {path}", PRODUCT, doc)
    try:
        version = get(base, "/v1/openapi.json").get("openapi")
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as e:
        version = f"unreadable ({e})"
    results.append(version == "3.1.0")
    print(f"{'PASS' if version == '3.1.0' else 'FAIL'} GET /v1/openapi.json declares OpenAPI {version}")
    return results


def main(argv: list[str]) -> int:
    if argv[:1] == ["--release"] and len(argv) == 2:
        results = offline(Path(argv[1]))
    elif len(argv) <= 1 and not (argv and argv[0].startswith("-")):
        results = online(argv[0] if argv else "http://127.0.0.1:8095")
    else:
        print(__doc__)
        return 2
    print(f"{sum(results)} PASS, {len(results) - sum(results)} FAIL")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Kraken's public AssetPairs response (JSON) -> one row per pair, the listing attributes as sent.

fetch calls parse(bytes) -> list of dicts; any raise is a parse failure (exit 30, bytes kept,
nothing staged), so each check below fails the fetch, as iso4217.py's do:
  - the response's `error` array is not empty (the venue answered, but with an error);
  - `result` holds no pair;
  - a pair has no base or no quote;
  - two pairs claim one websocket name (the trades stream could not tell them apart);
  - an object repeats a key (json would keep the last one without a word), or a listing
    attribute is missing or not of its type.
The response is {"error": [...], "result": {<pair code>: {<attributes>}}}. A row keeps the pair
code (the result key, as REST names the pair) and wsname (the websocket name), base and quote as
the venue spells them, and tick_size and ordermin as the published strings; silver types them.
Every other attribute (fees, leverage, margin, position limits) stays in the landed bytes only.
Standard library only; a parser cannot import a sibling module.
"""
import json
import re

DECIMAL = re.compile(r"[0-9]{1,20}(\.[0-9]{1,18})?")  # fits silver_listing's decimal(38,18) exactly


def _object(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"key {key!r} twice in one object")
        out[key] = value
    return out


def _constant(name: str) -> None:
    raise ValueError(f"{name} is not JSON")


def _text(pair: str, attrs: dict, name: str) -> str:
    value = attrs.get(name)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"pair {pair!r}: {name} missing, empty or not a string")
    return value


def _count(pair: str, attrs: dict, name: str) -> int:
    value = attrs.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"pair {pair!r}: {name} is not a whole number of digits")
    return value


def _decimal(pair: str, attrs: dict, name: str) -> str:
    value = _text(pair, attrs, name)
    if not DECIMAL.fullmatch(value):
        raise ValueError(f"pair {pair!r}: {name} {value!r} is not a plain decimal")
    return value


def parse(raw: bytes) -> list[dict]:
    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(doc, dict) or not isinstance(doc.get("error"), list):
        raise ValueError("not an AssetPairs response: no error array")
    if doc["error"]:
        raise ValueError(f"the venue answered with errors: {doc['error'][:5]}")
    result = doc.get("result")
    if not isinstance(result, dict):
        raise ValueError("not an AssetPairs response: no result object")
    if not result:
        raise ValueError("no pairs")
    rows, claimed = [], {}
    for code, attrs in sorted(result.items()):
        if not isinstance(attrs, dict) or not code.strip() or code != code.strip():
            raise ValueError(f"pair {code!r} is not an object under a plain pair code")
        if not all(isinstance(attrs.get(k), str) and attrs[k].strip() for k in ("base", "quote")):
            raise ValueError(f"pair {code!r} has no base or no quote")
        row = {"pair_code": code, "ws_name": _text(code, attrs, "wsname"), "base": _text(code, attrs, "base"),
               "quote": _text(code, attrs, "quote"), "tick_size": _decimal(code, attrs, "tick_size"),
               "pair_decimals": _count(code, attrs, "pair_decimals"),
               "lot_decimals": _count(code, attrs, "lot_decimals"),
               "order_min": _decimal(code, attrs, "ordermin"), "status": _text(code, attrs, "status")}
        if (other := claimed.setdefault(row["ws_name"], code)) != code:
            raise ValueError(f"websocket name {row['ws_name']!r} is claimed by {other!r} and {code!r}")
        rows.append(row)
    return rows

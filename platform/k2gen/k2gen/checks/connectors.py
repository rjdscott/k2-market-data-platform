"""Connector checks (W1-2): what a kafka or public-api connector must say before k2gen compiles it
into a pipeline the Spark connector and the fetch container can run (docs/development/
phase-001-interfaces.md, "A Kafka product's pipeline" and the public-API spool). Runs after the
documents check, so every document here is schema-valid. Each rule a code in CODES:

- kafka-bronze-shape: a kafka product has exactly one connector table, staged from the landing
  by offset (docker/lake/kafka.py stages into one), whose columns are the landing's nine by name
  and type (model.PLATFORM_TABLES["landing"]) keyed (kafka_partition, kafka_offset), in that order;
- envelope-unresolved: the connector's envelope.contract is a table contract of the set;
- host-outside-stack: every broker host and the registry's host is a service of the set's compose
  file on a network of LOADER, the service the load runs in, so a topic is read from the stack's
  own Redpanda and never from a host outside it (the connector's fields carry no credential: the
  schema's patterns hold no userinfo);
- pagination-unsupported: a public-api connector paginates `none`. fetch.py runs `next-link` (the
  Link header only) and `page` (`page=<n>` from 1), but the contract cannot yet say where a next
  link sits in a body or name the page parameter and its start, so either would run on a guess;
- auth-unsupported: no authHeaderEnv, which the fetch container refuses (it holds no secret);
- url-host-not-allowed: a fetch kind's url host is exactly one of its allowHosts (fetch.py's guard
  compares the IDNA host with each, as written), else every run is refused at the first request.

Failing fixtures: tests/test_checks_connectors.py, each case one change to the kinds overlay
(tests/fixtures/v2/kinds/).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator
from typing import Any

import yaml

from k2gen import model
from k2gen.checks.docs import _build, _prop_at, _props, _read
from k2gen.model import DocumentSet, Finding

CODES = (
    "connectors.kafka-bronze-shape",
    "connectors.envelope-unresolved",
    "connectors.host-outside-stack",
    "connectors.pagination-unsupported",
    "connectors.auth-unsupported",
    "connectors.url-host-not-allowed",
)
KAFKA_KEY = ("kafka_partition", "kafka_offset")
LOADER = "spark-iceberg"  # the compose service connector.py's load runs in (the factory's SPARK container)


def _finding(code: str, path: str, message: str) -> Finding:
    assert code in CODES, code
    return Finding(code=code, severity="error", path=path, message=message)


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    _, dirs, found = _read(docs.documents)
    if found:
        return []  # the documents check reports them, and stops the run before this one
    contracts = {d.contract_id(t) for d in dirs.values() for t in d.contracts}
    hosts = _stack_hosts(docs.documents.get(model.COMPOSE))
    out: list[Finding] = []
    for d in dirs.values():
        connector = _props(d.product).get("connector") if d.product is not None else None
        if connector:
            at, path = _prop_at(d.product, "connector"), d.at(model.V2_PRODUCT_FILE)
            out += _kafka(d, connector, at, path, contracts, hosts) if connector["kind"] == "kafka" else _fetch(connector, at, path)
    return out


def _stack_hosts(compose: bytes | None) -> set[str] | None:
    """The compose services that share a network with LOADER; None without a compose file."""
    if compose is None:
        return None
    services = (yaml.safe_load(compose) or {}).get("services") or {}
    networks = {name: set((s or {}).get("networks") or ()) for name, s in services.items()}
    return {name for name, nets in networks.items() if nets & networks.get(LOADER, set())}


def _kafka(d: Any, c: dict[str, Any], at: str, path: str, contracts: set[str], hosts: set[str] | None) -> Iterator[Finding]:
    tables = [t for t, contract in d.contracts.items() if _build(contract) == "connector"]
    landing = {col.name: col.type for col in model.PLATFORM_TABLES[model.KAFKA_LANDING].columns}
    if len(tables) != 1:
        yield _finding("connectors.kafka-bronze-shape", path, f"{at}: a kafka product stages one bronze table from its landing, and this one builds {len(tables)} by connector ({', '.join(tables) or 'none'}); keep exactly one")
    for t in tables:
        columns = d.contracts[t]["schema"][0]["properties"]
        typed = {col["name"]: col["physicalType"] for col in columns}
        key = [col["name"] for col in sorted((col for col in columns if col.get("primaryKey")), key=lambda col: col.get("primaryKeyPosition") or 0)]
        if typed != landing or tuple(key) != KAFKA_KEY:
            want = ", ".join(f"{n} {ty}" for n, ty in landing.items())
            yield _finding("connectors.kafka-bronze-shape", d.at(f"{t}.odcs.yaml"), f"$.schema[0].properties: a kafka product's bronze table holds exactly the landing's columns ({want}), primaryKey kafka_partition then kafka_offset, as kafka.py stages them; it has {', '.join(f'{n} {ty}' for n, ty in typed.items())} keyed by {', '.join(key) or 'nothing'}")
    if c["envelope"]["contract"] not in contracts:
        yield _finding("connectors.envelope-unresolved", path, f"{at}.envelope.contract: no contract in the set has id {c['envelope']['contract']}; name the table contract that declares the envelope's fields")
    named = [(f"{at}.brokers", b.rsplit(":", 1)[0]) for b in c["brokers"].split(",")]
    named.append((f"{at}.registry", urllib.parse.urlsplit(c["registry"]).hostname or ""))
    for pointer, host in named:
        if hosts is None:
            yield _finding("connectors.host-outside-stack", path, f"{pointer}: the set has no {model.COMPOSE} to find {host} in; a kafka connector reads the stack's own broker")
        elif host not in hosts:
            yield _finding("connectors.host-outside-stack", path, f"{pointer}: {host} is no service of {model.COMPOSE} on a network of {LOADER}, where the load runs ({', '.join(sorted(hosts)) or 'none'}); a kafka connector reads the stack's own broker and registry")


def _fetch(c: dict[str, Any], at: str, path: str) -> Iterator[Finding]:
    if c["kind"] == "public-api" and c["pagination"] != "none":
        yield _finding("connectors.pagination-unsupported", path, f"{at}.pagination: {c['pagination']} cannot be described yet: the contract has no field for where the next link sits in a body (fetch.py reads the Link header only) or for the page parameter's name and first value (fetch.py sends page=1, 2, ...); use none, one page per run, until it does")
    if "authHeaderEnv" in c:
        yield _finding("connectors.auth-unsupported", path, f"{at}.authHeaderEnv: the fetch container holds no secret and refuses a connector that names one; fetch a public source, or leave it out")
    if "url" in c and "allowHosts" in c:
        host = urllib.parse.urlsplit(c["url"].replace("{{ds}}", "1970-01-01")).hostname
        if host not in c["allowHosts"]:
            yield _finding("connectors.url-host-not-allowed", path, f"{at}.allowHosts: the url's host {host} is not one of them ({', '.join(c['allowHosts'])}), and the fetch's guard refuses every host not listed exactly; list it")

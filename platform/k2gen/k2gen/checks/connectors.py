"""Connector checks (W1-2): what a kafka or public-api connector must say before k2gen compiles it
into a pipeline the Spark connector and the fetch container can run (docs/development/
phase-001-interfaces.md, "A Kafka product's pipeline" and the public-API spool). Runs after the
documents check, so every document here is schema-valid. Each rule a code in CODES:

- kafka-bronze-shape: a kafka product has exactly one connector table, staged from the landing
  by offset (docker/lake/kafka.py stages into one), keyed (kafka_partition, kafka_offset), whose
  columns are, in order, KAFKA_LEAD, at least one column of the decoded envelope by its field
  name, and KAFKA_LINEAGE, by name and type (I-1, 2026-11-28: Trino decodes no Avro, so bronze
  carries the envelope decoded); schema_id and the envelope columns are not required, so a frame
  that does not decode reaches bronze, null there, for the audit's parses to fail;
- envelope-unresolved: the connector's envelope.contract is a table contract of the set;
- topic-claimed-twice: a topic is read by one product of the set, and declared by one domain of
  contracts/domains.yaml; topic-not-owned: a kafka product's topic is one its own domain declares
  (A2 of the 2026-11-28 review). The broker has no ACLs, and every load shares the Spark key and
  network, so this is the one place a product is tied to its topic: a second product reading the
  raw topic could build a copy under its own policies and grants. The declaration sits with the
  domain, in the file the platform team owns (.github/CODEOWNERS), not in the product's own
  directory, which its author edits. A compile-time rule: what stops a product the documents do
  not describe is the review of its contract, as for every other source;
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
    "connectors.topic-claimed-twice",
    "connectors.topic-not-owned",
    "connectors.pagination-unsupported",
    "connectors.auth-unsupported",
    "connectors.url-host-not-allowed",
)
KAFKA_KEY = ("kafka_partition", "kafka_offset")
_LANDING = {col.name: col.type for col in model.PLATFORM_TABLES[model.KAFKA_LANDING].columns}
# The landing's offset key and timestamp, then the frame's Confluent writer schema id (kafka.py BRONZE_LEAD)
KAFKA_LEAD = (*((n, _LANDING[n]) for n in ("kafka_partition", "kafka_offset", "kafka_timestamp")), ("schema_id", "integer"))
KAFKA_LINEAGE = tuple((n, _LANDING[n]) for n in ("batch_id", "landed_at", "run_id", "release_id"))  # the landing batch's
LOADER = "spark-iceberg"  # the compose service connector.py's load runs in (the factory's SPARK container)


def _finding(code: str, path: str, message: str) -> Finding:
    assert code in CODES, code
    return Finding(code=code, severity="error", path=path, message=message)


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    shared, dirs, found = _read(docs.documents)
    if found:
        return []  # the documents check reports them, and stops the run before this one
    contracts = {d.contract_id(t) for d in dirs.values() for t in d.contracts}
    hosts = _stack_hosts(docs.documents.get(model.COMPOSE))
    domains: dict[str, Any] = shared.get("domains", {}).get("domains", {})
    owner: dict[str, str] = {}
    out: list[Finding] = []
    for name, domain in sorted(domains.items()):
        for topic in domain.get("topics", ()):
            if topic in owner:
                out.append(_finding("connectors.topic-claimed-twice", model.DOMAINS, f"$.domains.{name}.topics: {topic} is declared by {owner[topic]} too; one domain's capture writes a topic, so declare it once"))
            owner.setdefault(topic, name)
    readers: dict[str, str] = {}
    for d in sorted(dirs.values(), key=lambda d: d.at("")):
        connector = _props(d.product).get("connector") if d.product is not None else None
        if connector:
            at, path = _prop_at(d.product, "connector"), d.at(model.V2_PRODUCT_FILE)
            if connector["kind"] == "kafka":
                out += _kafka(d, connector, at, path, contracts, hosts)
                out += _topic(d, connector["topic"], f"{at}.topic", path, owner, readers)
            else:
                out += _fetch(connector, at, path)
    return out


def _topic(d: Any, topic: str, at: str, path: str, owner: dict[str, str], readers: dict[str, str]) -> Iterator[Finding]:
    pid = model.product_id(d.domain, d.name)
    if topic in readers:
        yield _finding("connectors.topic-claimed-twice", path, f"{at}: {topic} is read by {readers[topic]} too; a topic is read by one product, which owns its landing and bronze, and every other product reads that product's tables through an input port under its policies")
    readers.setdefault(topic, pid)
    if owner.get(topic) != d.domain:
        whose = f"domain {owner[topic]} declares it" if topic in owner else "no domain declares it"
        yield _finding("connectors.topic-not-owned", path, f"{at}: {topic} is not a topic of domain {d.domain} ({whose} in {model.DOMAINS}); a kafka product reads only a topic its own domain's capture writes, declared under the domain's topics")


def _stack_hosts(compose: bytes | None) -> set[str] | None:
    """The compose services that share a network with LOADER; None without a compose file."""
    if compose is None:
        return None
    services = (yaml.safe_load(compose) or {}).get("services") or {}
    networks = {name: set((s or {}).get("networks") or ()) for name, s in services.items()}
    return {name for name, nets in networks.items() if nets & networks.get(LOADER, set())}


def _kafka(d: Any, c: dict[str, Any], at: str, path: str, contracts: set[str], hosts: set[str] | None) -> Iterator[Finding]:
    tables = [t for t, contract in d.contracts.items() if _build(contract) == "connector"]
    if len(tables) != 1:
        yield _finding("connectors.kafka-bronze-shape", path, f"{at}: a kafka product stages one bronze table from its landing, and this one builds {len(tables)} by connector ({', '.join(tables) or 'none'}); keep exactly one")
    for t in tables:
        columns = d.contracts[t]["schema"][0]["properties"]
        typed = [(col["name"], col["physicalType"]) for col in columns]
        key = [col["name"] for col in sorted((col for col in columns if col.get("primaryKey")), key=lambda col: col.get("primaryKeyPosition") or 0)]
        lead, envelope, lineage = typed[:len(KAFKA_LEAD)], typed[len(KAFKA_LEAD):len(typed) - len(KAFKA_LINEAGE)], typed[len(typed) - len(KAFKA_LINEAGE):]
        reserved = {n for n, _ in (*KAFKA_LEAD, *KAFKA_LINEAGE)} | set(_LANDING)
        required = [col["name"] for col in columns[len(KAFKA_LEAD) - 1:len(columns) - len(KAFKA_LINEAGE)] if col.get("required")]
        if (tuple(lead) != KAFKA_LEAD or tuple(lineage) != KAFKA_LINEAGE or not envelope or tuple(key) != KAFKA_KEY
                or any(n in reserved for n, _ in envelope) or required):
            want = ", ".join(f"{n} {ty}" for n, ty in KAFKA_LEAD) + ", then each envelope column, then " + ", ".join(f"{n} {ty}" for n, ty in KAFKA_LINEAGE)
            yield _finding("connectors.kafka-bronze-shape", d.at(f"{t}.odcs.yaml"), f"$.schema[0].properties: a kafka product's bronze table holds {want}: at least one envelope column, named as its field and never a landing or lineage column, schema_id and each envelope column not required (a frame that does not decode is staged with them null and fails parses); primaryKey kafka_partition then kafka_offset; as kafka.py stages them; it has {', '.join(f'{n} {ty}' for n, ty in typed)} keyed by {', '.join(key) or 'nothing'}{', required ' + ', '.join(required) if required else ''}")
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

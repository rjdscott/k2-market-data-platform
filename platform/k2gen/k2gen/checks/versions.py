"""Versions and transitions against the previous release (ADR-055 part 4, ARCHITECTURE-V2 4.5).

A product's `status` moves only as ALLOWED says: a new product enters `active` or `paused`;
`retired` follows `deprecated` only, on or after the sunset it announced, and is final; only a
retired product's documents may leave the set.

Every versioned document, a product file and each table contract, bumps its Semantic Version
by the largest change it carries against the previous release:

- major: a column dropped or renamed, retyped, re-keyed, its `required` changed, or added as
  required (rows already published have no value for it, and Iceberg refuses either move of
  nullability in place); a role no longer offered; a policy tightened for a role still
  offered; an output removed;
- minor: a column added nullable; a role offered; a policy loosened; an output added;
- patch: any other change of bytes, so a version never repeats with different content.

A product's version covers every document in its directory and takes its tables' changes as
its own. Across the set, previous release or not: a live product's input port names the major
its input table is at (the port's `version`, the frozen schema's one input constraint;
`inputContracts` repeats it as lineage and is left to the documents check); no live product
reads a table that is retired or gone; a deprecated product takes no new reader; a paused
product builds nothing, so its columns and outputs stay as they were. `previous` None (a first
release): every product is new.

A finding's message starts with the JSON pointer, in the finding's document, of what to fix.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from k2gen.model import Delay, DocumentSet, Finding, RolePolicy, RowScope, document_kind, parse

PATCH, MINOR, MAJOR = 1, 2, 3
BUMP_CODE = {MAJOR: "versions.major-bump-missing", MINOR: "versions.minor-bump-missing", PATCH: "versions.version-reused"}
# The status in the previous release (None: a new product) -> what it may be now (None: gone).
ALLOWED: Mapping[str | None, frozenset[str | None]] = {
    None: frozenset({"active", "paused"}),
    "active": frozenset({"active", "paused", "deprecated"}),
    "paused": frozenset({"paused", "active", "deprecated"}),
    "deprecated": frozenset({"deprecated", "active", "retired"}),
    "retired": frozenset({"retired", None}),
}


@dataclass(frozen=True)
class _Change:
    level: int
    structural: bool  # columns, keys or outputs: what only a build can apply to the lake
    text: str  # `<pointer>: what changed, old -> new`


@dataclass(frozen=True)
class _Doc:
    path: str
    doc: Any


@dataclass(frozen=True)
class _Set:
    """A document set's products and table contracts, parsed, by id."""

    documents: Mapping[str, bytes]
    products: Mapping[str, _Doc]
    tables: Mapping[str, _Doc]

    def producer(self, table: _Doc) -> _Doc | None:
        """The product whose directory holds the table's contract."""
        home = posixpath.dirname(table.path)
        return next((p for p in self.products.values() if posixpath.dirname(p.path) == home), None)

    def tables_of(self, product: _Doc) -> dict[str, _Doc]:
        home = posixpath.dirname(product.path)
        return {cid: t for cid, t in self.tables.items() if posixpath.dirname(t.path) == home}

    def files_of(self, product: _Doc) -> dict[str, bytes]:
        home = posixpath.dirname(product.path) + "/"
        return {p.removeprefix(home): b for p, b in self.documents.items() if p.startswith(home)}


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    new = _index(docs)
    old = _index(previous) if previous is not None else None
    found = _transitions(new, old) + _inputs(new, old)
    return found + _versions(new, old) if old else found


def _index(docs: DocumentSet) -> _Set:
    products: dict[str, _Doc] = {}
    tables: dict[str, _Doc] = {}
    for path in sorted(docs.documents):
        kind = document_kind(path)
        if kind in ("product", "contract"):
            doc = parse(path, docs.documents[path])
            (products if kind == "product" else tables)[doc["id"]] = _Doc(path, doc)
    return _Set(docs.documents, products, tables)


def _property(doc: Any, name: str) -> tuple[int | None, Any]:
    """(index in customProperties, value) of a K2 custom property; (None, {}) when absent."""
    for k, prop in enumerate(doc.get("customProperties", [])):
        if prop["property"] == name:
            return k, prop["value"]
    return None, {}


def _semver(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in version.split("."))
    return major, minor, patch


# --- Lifecycle -------------------------------------------------------------------------


def _transitions(new: _Set, old: _Set | None) -> list[Finding]:
    found = []
    before = old.products if old else {}
    for pid in sorted(new.products.keys() | before.keys()):
        was = before[pid].doc["status"] if pid in before else None
        now = new.products[pid].doc["status"] if pid in new.products else None
        path = new.products[pid].path if pid in new.products else before[pid].path
        if now not in ALLOWED[was]:
            found.append(Finding("versions.transition-not-allowed", "error", path, _transition_text(pid, was, now)))
        elif was == "deprecated" and now == "retired":
            sunset = _property(before[pid].doc, "lifecycleChange")[1].get("sunset")
            k, change = _property(new.products[pid].doc, "lifecycleChange")
            if sunset and change.get("since", sunset) < sunset:
                found.append(Finding("versions.retired-before-sunset", "error", path,
                                     f"/customProperties/{k}/value/since: retired since {change['since']}, before the sunset "
                                     f"{sunset} the previous release announced; retire on or after {sunset}"))
    return found


def _transition_text(pid: str, was: str | None, now: str | None) -> str:
    if was is None:
        return f"/status: a new product enters active or paused, not {now}"
    if now is None:
        return (f"/status: {pid} is gone from the set while {was}; only a retired product's documents may go, "
                "so deprecate it, then retire it")
    if was == "retired":
        return f"/status: retired -> {now} is not allowed: retired is final"
    may = " or ".join(sorted(s for s in ALLOWED[was] if s not in (was, None)))
    return f"/status: {was} -> {now} is not allowed; from {was} a product may become {may}" + (
        "; retired follows deprecated only" if now == "retired" else "")


# --- Inputs across the set -------------------------------------------------------------


def _inputs(new: _Set, old: _Set | None) -> list[Finding]:
    found = []
    for consumer in sorted(new.products.values(), key=lambda p: p.path):
        if consumer.doc["status"] == "retired":  # builds nothing, so reads nothing
            continue
        cons = consumer.doc["id"]
        before = old.products.get(cons) if old else None
        read_before = {p["contractId"] for p in before.doc.get("inputPorts", [])} if before else set()
        for i, port in enumerate(consumer.doc.get("inputPorts", [])):
            cid, name, have = port["contractId"], port["name"], port["version"]
            where = f"{consumer.path} /inputPorts/{i}"
            table = new.tables.get(cid)
            if table is None:
                gone = old.tables.get(cid) if old else None
                if gone is not None:  # else an unresolved port, which is not a version question
                    owner = new.producer(gone) or (old.producer(gone) if old else None)
                    found.append(Finding("versions.removed-while-input", "error", owner.path if owner else gone.path,
                                         f"/outputPorts: {cid} {gone.doc['version']} is gone from the set, but {cons} still "
                                         f"declares it as input port {name} ({where}); remove that port first, or keep the output"))
                continue
            producer = new.producer(table)
            status = producer.doc["status"] if producer else None
            if producer and status == "retired":
                found.append(Finding("versions.retired-while-input", "error", producer.path,
                                     f"/status: retired, but {cons} still declares {cid} as input port {name} ({where}); "
                                     "remove that port first, or keep this product deprecated"))
            elif producer and status == "deprecated" and cid not in read_before:
                change = _property(producer.doc, "lifecycleChange")[1]
                successor = change.get("successor")
                found.append(Finding("versions.new-reader-of-deprecated", "error", consumer.path,
                                     f"/inputPorts/{i}: {producer.doc['id']} is deprecated (sunset {change.get('sunset')}, "
                                     f"successor {successor}) and takes no new reader; read {successor} instead"))
            want = table.doc["version"]
            if _semver(have)[0] != _semver(want)[0]:
                was = old.tables.get(cid) if old else None
                history = f", {was.doc['version']} in the previous release" if was and was.doc["version"] != want else ""
                found.append(Finding("versions.input-major-unacknowledged", "error", consumer.path,
                                     f"/inputPorts/{i}/version: input port {name} was written against {cid} {have}, but the "
                                     f"set has it at {want}{history}; read what major {_semver(want)[0]} changed, then set "
                                     f"this version to {want}"))
    return found


# --- Versions --------------------------------------------------------------------------


def _versions(new: _Set, old: _Set) -> list[Finding]:
    found = []
    for pid, product in sorted(new.products.items()):
        was = old.products.get(pid)
        if was is None:
            continue
        changes = _outputs(was.doc, product.doc)
        before, after = old.tables_of(was), new.tables_of(product)
        for cid in sorted(before.keys() & after.keys()):
            t_old, t_new = before[cid], after[cid]
            t_changes = _columns(t_old.doc, t_new.doc) + _access(t_old.doc, t_new.doc)
            if old.documents[t_old.path] != new.documents[t_new.path]:
                t_changes.append(_Change(PATCH, False, "the contract changed"))
            found += _bump(t_new.path, t_old.doc["version"], t_new.doc["version"], t_changes)
            changes += [replace(c, text=f"{posixpath.basename(t_new.path)} {c.text}") for c in t_changes if c.level > PATCH]
        files_old, files_new = old.files_of(was), new.files_of(product)
        changes += [_Change(PATCH, False, f"{f} {'added' if f not in files_old else 'removed' if f not in files_new else 'changed'}")
                    for f in sorted(files_old.keys() | files_new.keys()) if files_old.get(f) != files_new.get(f)]
        found += _bump(product.path, was.doc["version"], product.doc["version"], changes)
        moved = [c.text for c in changes if c.structural]
        if product.doc["status"] == "paused" and moved:
            found.append(Finding("versions.paused-contract-changed", "error", product.path,
                                 f"/status: paused, so nothing builds and its tables cannot follow: {'; '.join(moved)}; "
                                 "keep its columns and outputs while paused, or make it active in this release"))
    return found


def _bump(path: str, was: str, now: str, changes: list[_Change]) -> list[Finding]:
    """The finding, if any, of a document whose version moved from `was` to `now` across `changes`."""
    o, n = _semver(was), _semver(now)
    need = max((c.level for c in changes), default=0)
    fix = {MAJOR: f"{o[0] + 1}.0.0", MINOR: f"{o[0]}.{o[1] + 1}.0", PATCH: f"{o[0]}.{o[1]}.{o[2] + 1}"}.get(need, was)
    if n < o:
        return [Finding("versions.version-backwards", "error", path,
                        f"/version: {was} -> {now}: a version never goes backwards; make it {fix} or higher")]
    have = 0 if n == o else MAJOR if n[0] > o[0] else MINOR if n[1] > o[1] else PATCH
    if have >= need:
        return []
    kind = {MAJOR: "a major change", MINOR: "a minor change", PATCH: "a change"}[need]
    why = "; ".join(c.text for c in changes if c.level == need)
    moved = f"{was} -> {now}" if have else f"{now} again"
    return [Finding(BUMP_CODE[need], "error", path, f"/version: {moved}, but {kind}: {why}; make it {fix} or higher")]


def _outputs(old: Any, new: Any) -> list[_Change]:
    was = {p["contractId"]: p["version"] for p in old.get("outputPorts", [])}
    now = {p["contractId"]: j for j, p in enumerate(new.get("outputPorts", []))}
    return ([_Change(MAJOR, True, f"/outputPorts: output {cid} {was[cid]} removed") for cid in sorted(was.keys() - now.keys())]
            + [_Change(MINOR, True, f"/outputPorts/{now[cid]}: output {cid} added") for cid in sorted(now.keys() - was.keys())])


def _key(columns: list[Any]) -> str:
    keyed = sorted((c for c in columns if c.get("primaryKey")), key=lambda c: c.get("primaryKeyPosition", 0))
    return "(" + (", ".join(c["name"] for c in keyed) or "none") + ")"


def _columns(old: Any, new: Any) -> list[_Change]:
    was_list, now_list = old["schema"][0]["properties"], new["schema"][0]["properties"]
    was = {c["name"]: c for c in was_list}
    now = {c["name"]: (j, c) for j, c in enumerate(now_list)}
    out = []
    for name, c in was.items():
        if name not in now:
            out.append(_Change(MAJOR, True, f"/schema/0/properties: column {name} ({c['physicalType']}) is gone, dropped or renamed"))
            continue
        j, d = now[name]
        if c["physicalType"] != d["physicalType"]:
            out.append(_Change(MAJOR, True, f"/schema/0/properties/{j}/physicalType: column {name} retyped "
                                            f"{c['physicalType']} -> {d['physicalType']}"))
        r_old, r_new = bool(c.get("required")), bool(d.get("required"))
        if r_old != r_new:
            out.append(_Change(MAJOR, True, f"/schema/0/properties/{j}/required: column {name} required "
                                            f"{str(r_old).lower()} -> {str(r_new).lower()}"))
    for name, (j, d) in now.items():
        if name not in was:
            required = bool(d.get("required"))
            how = "as required, which the rows already published cannot meet" if required else "nullable"
            out.append(_Change(MAJOR if required else MINOR, True, f"/schema/0/properties/{j}: column {name} ({d['physicalType']}) added {how}"))
    if _key(was_list) != _key(now_list):
        out.append(_Change(MAJOR, True, f"/schema/0/properties: primary key {_key(was_list)} -> {_key(now_list)}"))
    return out


def _role_policy(raw: Any) -> RolePolicy:
    raw = raw or {}
    delay, scope = raw.get("delay"), raw.get("rowScope")
    return RolePolicy(Delay(delay["column"], delay["minutes"]) if delay else None, tuple(raw.get("mask", ())),
                      RowScope(scope["column"], tuple(scope["values"])) if scope else None)


def _show_delay(d: Delay | None) -> str:
    return f"{d.column} {d.minutes} min" if d else "none"


def _show_scope(s: RowScope | None) -> str:
    return f"{s.column} in [{', '.join(map(str, s.values))}]" if s else "every row"


def _policy_moves(old: RolePolicy, new: RolePolicy) -> tuple[list[str], list[str]]:
    """What tightened and what loosened, each `part old -> new`: a reader of the role sees
    fewer rows or columns, or more."""
    tighter: list[str] = []
    looser: list[str] = []
    od, nd = old.delay, new.delay
    if od != nd:
        tight = nd is not None and (od is None or nd.column != od.column or nd.minutes > od.minutes)
        (tighter if tight else looser).append(f"delay {_show_delay(od)} -> {_show_delay(nd)}")
    masked, unmasked = sorted(set(new.mask) - set(old.mask)), sorted(set(old.mask) - set(new.mask))
    if masked:
        tighter.append(f"mask adds {', '.join(masked)}")
    if unmasked:
        looser.append(f"mask drops {', '.join(unmasked)}")
    os_, ns = old.row_scope, new.row_scope
    if (os_ and (os_.column, set(os_.values))) != (ns and (ns.column, set(ns.values))):
        narrowed = ns is not None and (os_ is None or ns.column != os_.column or not set(os_.values) <= set(ns.values))
        (tighter if narrowed else looser).append(f"rowScope {_show_scope(os_)} -> {_show_scope(ns)}")
    return tighter, looser


def _access(old: Any, new: Any) -> list[_Change]:
    was = {r["role"] for r in old.get("roles", [])}
    now = {r["role"]: j for j, r in enumerate(new.get("roles", []))}
    out = [_Change(MAJOR, False, f"/roles: role {r} no longer offered") for r in sorted(was - now.keys())]
    out += [_Change(MINOR, False, f"/roles/{now[r]}: role {r} offered") for r in sorted(now.keys() - was)]
    _, before = _property(old, "policies")
    k, after = _property(new, "policies")
    for role in sorted(was & now.keys()):
        tighter, looser = _policy_moves(_role_policy(before.get(role)), _role_policy(after.get(role)))
        at = f"/customProperties/{k}/value/{role}" if k is not None else "/customProperties"
        if tighter:
            out.append(_Change(MAJOR, False, f"{at}: role {role}'s policy tightened: {'; '.join(tighter + looser)}"))
        elif looser:
            out.append(_Change(MINOR, False, f"{at}: role {role}'s policy loosened: {'; '.join(looser)}"))
    return out

"""`k2gen diff`, filled by step S08: what changes between two releases.

CI posts the output as the validation runner's job summary against the newest `v3.*` tag. The
needs are worked out afresh from the old release to the new one by k2gen.release.needs; the new
manifest's own needs were computed against whatever release it named as previous, which may be
neither. Every list is sorted: one pair of releases always prints one text.
"""

import json
from pathlib import Path

from k2gen import model, release
from k2gen.model import Release


def _plan(r: Release) -> set[tuple[str, str]]:
    """The storage plan as (kind, name) rows; empty when the release has none (S15 a stub)."""
    if model.STORAGE_PLAN not in r.manifest.files:
        return set()
    plan = json.loads((r.root / model.STORAGE_PLAN).read_bytes())
    rows = {("landing prefix", f"{plan['landing']['bucket']}/{p}") for p in plan["landing"]["prefixes"]}
    for w in plan["warehouses"]:
        rows |= {("warehouse", w["name"])} | {("namespace", f"{w['name']}.{ns}") for ns in w["namespaces"]}
    return rows


def _names(items: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items) or "none"


def diff(old: Path, new: Path) -> str:
    """The file-level and storage-plan difference of two release directories, as Markdown."""
    a, b = release.read(old), release.read(new)
    head = [f"## Release `{a.id}` to `{b.id}`", ""]
    fa, fb, ia, ib = a.manifest.files, b.manifest.files, a.manifest.images, b.manifest.images
    files = sorted(p for p in fa.keys() | fb.keys() if fa.get(p) != fb.get(p))
    images = sorted(s for s in ia.keys() | ib.keys() if ia.get(s) != ib.get(s))
    if not files and not images:
        return "\n".join([*head, "Nothing changes: both releases hold the same files and images.", ""])

    new_files = {p: (b.root / p).read_bytes() for p in fb}
    need = release.needs(a, new_files, ib)
    old_compose = (a.root / model.RELEASE_COMPOSE).read_bytes()
    held = [s for s in release.changed_services(old_compose, new_files[model.RELEASE_COMPOSE], ia, ib) if not release.recreatable(s)]
    out = [*head, "### What deploying it needs", "", "| Need | |", "|---|---|",
           f"| Recreate | {_names(need.recreate)} |",
           f"| Trino restart | {'yes' if need.trino_restart else 'no'} |",
           f"| DAG drain (a DAG's tasks changed) | {'yes' if need.dag_shape_change else 'no'} |",
           f"| DDL | {_names(need.ddl)} |", ""]
    if not (need.recreate or need.trino_restart or need.dag_shape_change or need.ddl):
        out += ["Nothing beyond the pointer swap: every changed file is read through the pointer.", ""]
    if held:
        out += [f"Changed but never recreated by deploy (A1), so recreated by hand: {_names(held)}.", ""]

    counts = {c: sum(1 for p in files if (p in fa, p in fb) == k) for c, k in (("added", (False, True)), ("removed", (True, False)), ("changed", (True, True)))}
    out += [f"### Files: {counts['added']} added, {counts['removed']} removed, {counts['changed']} changed", ""]
    if files:
        out += ["| Change | Path | Need |", "|---|---|---|"]
        for p in files:
            change = "added" if p not in fa else "removed" if p not in fb else "changed"
            out.append(f"| {change} | `{p}` | {model.need_of(p) or 'none'} |")
        out.append("")
    if images:
        out += ["### Images", "", "| Service | Old | New |", "|---|---|---|"]
        out += [f"| `{s}` | {_names([ia[s]] if s in ia else [])} | {_names([ib[s]] if s in ib else [])} |" for s in images]
        out.append("")

    pa, pb = _plan(a), _plan(b)
    rows = sorted([("added", *r) for r in pb - pa] + [("removed", *r) for r in pa - pb], key=lambda r: (r[1], r[2], r[0]))
    out += ["### Storage plan", ""]
    if not rows:
        return "\n".join([*out, "No change to the storage plan.", ""])
    out += ["| Change | Kind | Name |", "|---|---|---|", *[f"| {c} | {k} | `{n}` |" for c, k, n in rows], ""]
    if pa - pb:
        out += ["Deploy only adds: a removed row stays until `scripts/drop.sh` removes it.", ""]
    return "\n".join(out)

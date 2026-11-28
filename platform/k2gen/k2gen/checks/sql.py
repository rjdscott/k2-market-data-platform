"""The SQL lint of product models and their config (A3, A7; ADR-061 part 7 and its gate).

What it reads: in each v2 product directory (one holding `product.odps.yaml`), every `.sql` file
(models, and any other the product ships), its one `{{ config(...) }}` through
`model.model_config`, and the hooks in that config. Any other file under `models/` is an error,
and any other YAML file outside `seeds/` is searched for hook keys. A finding's message starts
with `line <n>:` where the line is known.

**Errors.**

- `sql.env-var`: `env_var` anywhere in Jinja code, a string naming it included. The dbt process's
  environment holds the builder's credentials, and what a model renders lands in SQL, a table or a log.
- `sql.definer-view`, `sql.materialized-view`: `materialized='view'` without
  `view_security='invoker'` (dbt-trino's default is definer), or `'materialized_view'`.
  Readers see a product only through the view k2 generates (ADR-061 condition 1).
- `sql.incremental-predicates`: it narrows the rows a merge compares, so a row outside them is
  inserted again and the unique key (A7) no longer holds.
- `sql.config-key`: a config key other than `materialized`, `unique_key`,
  `incremental_strategy`, `on_schema_change` and `view_security` (and the keys with codes of their
  own), or a materialization other than table, incremental, ephemeral and an invoker view. The
  compiler sets the rest, or k2 does not use them: `grants` runs GRANT as the builder,
  `properties` can move the table's files, `on_table_exists` decides whether its tags survive.
- `sql.config-unreadable`: a config that `model.model_config` refuses, or a `config(` call it
  would not see (`{% do config(...) %}`, `{{ config (...) }}`, a second one): dbt reads those.
- `sql.foreign-read`: another product's table read outside `k2_input`, which pins each input at
  the snapshot the wrapper resolved from its tested tag. That is `source('<other fence>', ...)`,
  a `ref()` of a model or seed that is not the product's own (`<fence>__<table>`, a seed of
  `seeds/`), a `source()` or `ref()` whose name is not a literal, and a relation written by hand
  whose namespace is another product's `<fence>` or `<fence>__base`. Trino lets a builder read
  its inputs' bases at `main` (gate h1.17): only `k2_input` and this lint hold it to tested rows.
- `sql.time-travel`: `FOR VERSION AS OF`, `FOR TIMESTAMP AS OF` or `table_changes` written by
  hand, in any case and spacing, comments between the words included.
- `sql.write-outside-base`: config `database`, `schema` or `alias`, which move where dbt writes,
  or an INSERT, DELETE, UPDATE or MERGE in a hook or a statement whose target is not `{{ this }}`,
  the model it belongs to. Another of the product's own tables is refused too, by `ref()` or in
  full: `dbt build` runs a downstream model's hook after the upstream model's tests, so the write
  would land on rows the publish then tags, which no test read.
- `sql.statement-ddl`: SQL run from a model by `{% call statement %}`, `run_query()` or
  `adapter.execute()` that is not a read (SELECT, WITH, VALUES, SHOW, DESCRIBE, EXPLAIN) or a
  write as above: DDL, CALL, GRANT, SET SESSION; or SQL the lint cannot read (Jinja other than
  `this`, `ref`, `source`, `k2_input` and string literals); or an `adapter` method that can
  change a relation or call a macro (all but the read-only ones below).
- `sql.hook`: a `pre_hook` or `post_hook` running anything but a read or a write as above, or SQL
  the lint cannot read; a hook key outside a model's config (`on-run-start`, `on-run-end`, YAML
  `+pre-hook`). A hook runs as the builder, outside the build's tests and the publish, and one
  that touches a view, a tag or a grant changes what readers see: the gate showed a reader view
  replaced under the wrong owner (h1.33) and a dropped table taking its tag with it (h1.34). This
  lint is one layer. **The control is ownership**: the builder owns only `<fence>__base`, the
  publisher alone owns the views and passes SELECT on (ADR-061 part 2), and Trino refused the
  builder every view change (h1.12).
- `sql.model-not-sql`: a file under `models/` that is not `.sql`: dbt would read its config,
  hooks or properties, which neither the compiler nor this lint reads.
- `sql.as-of`: a `k2_as_of(fact_time, target_alias, mode)` (dbt/macros/k2_as_of.sql, step I-6)
  whose arguments are not three string literals, a column (`<alias>.<column>` or `<column>`), an
  unquoted identifier and `'valid'` or `'known'`; a `k2_known_to(keys)` whose argument is not a
  non-empty list of unquoted column names; or a `target_alias` that the model's SQL does not bind
  after FROM, JOIN or a comma, as a CTE name or a subquery's alias, or that it binds straight to a
  `k2_input` whose contract lacks `valid_from` and `valid_to` (mode valid) or at all (mode known,
  whose `known_to` only `k2_known_to` in a CTE computes). An alias bound to a CTE, a subquery or the
  product's own table is accepted: the lint does not follow a CTE's columns, and Trino refuses a
  missing one at build.

**Warnings.** `sql.adapter`: a read-only adapter call (`get_relation`,
`get_columns_in_relation`, `get_missing_columns`, `check_schema_exists`, `quote`), which reads
the catalog outside `source`, `ref` and `k2_input`. `sql.relation-unmanaged`: a relation in a FROM,
JOIN, comma join, `TABLE(...)` or MERGE USING that is not reached through them (a CTE name
aside), or one the lint cannot read (`{{ var('t') }}`).

**How it reads, and its limits.** The package pins no SQL parser, and the SQL is a Jinja template,
so the lint works on tokens. Jinja comments are dropped; every other Jinja block is code, inside a
SQL comment too, since Jinja renders there. A `{{ }}` made of string literals joined by `~` or
`+`, `this`, and `ref`, `source` or `k2_input` with literal arguments is folded into the SQL, and so
are `k2_as_of` and `k2_known_to` with valid arguments, which render a predicate and read no
relation; any other expression is opaque. The SQL is then tokenized (comments, strings, quoted identifiers,
lower-cased names), and relations are found after FROM in a clause that has a SELECT at its
level (not `extract(... FROM x)` or `IS DISTINCT FROM`), after JOIN, after a comma in a FROM list,
in `TABLE(<name>)`, and after MERGE ... USING. Not followed, each pinned by a test:

- a name assembled through a variable, a filter or a dynamic lookup (`{% set v = 'VERSION' %}FOR
  {{ v }} AS OF`, `context[n ~ 'var']`): Jinja would need to run, with the wrapper's vars;
- a relation built by `api.Relation.create(...)` or rendered by a macro: an opaque relation in
  FROM, so a warning, not `foreign-read`;
- a macro defined in the file and called: its body is linted as SQL where it stands, which finds
  hand-written relations after a FROM but not ones its caller's FROM introduces;
- `{% raw %}`: its content is linted as Jinja, so `env_var` there is flagged though not run;
- a hook string holding a Jinja call (`post_hook="... {{ ref('x') }}"`) is read, since
  `model.model_config` skips quoted strings whole, and its SQL linted like any hook's;
- a model with no `materialized` takes the compiled project's default (S17), which must not be a
  view.

The lint holds honest authors to the rules and makes a dishonest one's intent explicit in a
reviewed diff; it is not a sandbox. What stops a builder is Trino's rules (S12): it writes only
its base, reads only its declared inputs' bases, and cannot touch a view or a grant.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from k2gen import model
from k2gen.model import DocumentSet, Finding, Severity

ALLOWED_CONFIG = ("materialized", "unique_key", "incremental_strategy", "on_schema_change", "view_security")
MATERIALIZATIONS = ("table", "incremental", "ephemeral", "view")
CONFIG_REASONS = {
    "grants": "dbt's grants config runs GRANT as the builder; who reads a product is its grants.yaml, compiled into Trino's rules",
    "properties": "table properties come from the contract through the compiler, and a model's could move the table's files (location)",
    "on_table_exists": "how dbt replaces a table decides whether its audited tag survives (gate h1.34); the compiler sets it",
}
READ_ONLY_ADAPTER = ("get_relation", "get_columns_in_relation", "get_missing_columns", "check_schema_exists", "quote")
_RELATION_MACROS = ("ref", "source", "k2_input")
_AS_OF_MACROS = ("k2_as_of", "k2_known_to")  # a predicate and a window: no relation read
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FACT_TIME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
_READ = ("select", "with", "values", "show", "describe", "table")
_DML = {"insert": "into", "delete": "from", "update": None, "merge": "into"}
_NOT_RELATION = ("lateral", "unnest", "table", "values", "select", "with")
_FROM_LIST_ENDS = ("where", "group", "having", "order", "limit", "offset", "fetch", "union", "intersect", "except",
                   "window", "for", "returning")
_TIME_TRAVEL = ("version", "timestamp", "system_time", "system_version")
_HOOK_KEY = re.compile(r"(?<![\w-])\+?(pre[-_]hook|post[-_]hook|on[-_]run[-_](?:start|end))(?![\w-])")


# --- Jinja ---------------------------------------------------------------------------------

class _J(NamedTuple):
    kind: str  # str, name, num, op
    value: str


class _Block(NamedTuple):
    kind: str  # "{{", "{%" or "{#"
    code: str  # without the delimiters and whitespace-control marks
    start: int
    end: int


class _Ph(NamedTuple):
    """A folded `{{ }}` that names a relation, or an opaque one."""

    kind: str  # this, ref, source, k2_input, k2_as_of, k2_known_to, opaque
    args: tuple[str | None, ...]
    code: str


_JTOKEN = re.compile(r"""\s*(?:(?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(?P<name>[A-Za-z_]\w*)|(?P<num>\d[\w.]*)|(?P<op>\S))""", re.S)
_CLOSE = {"{{": "}}", "{%": "%}", "{#": "#}"}


def _jtokens(code: str) -> list[_J]:
    out = []
    for m in _JTOKEN.finditer(code):
        kind = m.lastgroup or "op"
        text = m.group(kind)
        out.append(_J(kind, re.sub(r"\\(.)", r"\1", text[1:-1]) if kind == "str" else text))
    return out


def _blocks(text: str) -> list[_Block]:
    """Every Jinja block, a quoted `}}` inside one included."""
    out, i = [], 0
    while (i := text.find("{", i)) != -1:
        opener = text[i:i + 2]
        if opener not in _CLOSE:
            i += 1
            continue
        j, quote = i + 2, ""
        while j < len(text) and (quote or not text.startswith(_CLOSE[opener], j)):
            if quote and text[j] == "\\":
                j += 1
            elif quote and text[j] == quote:
                quote = ""
            elif not quote and opener != "{#" and text[j] in "'\"":
                quote = text[j]
            j += 1
        code = text[i + 2:j]
        code = code[1:] if code[:1] in "-+" else code
        out.append(_Block(opener, (code[:-1] if code[-1:] == "-" else code).strip(), i, min(j + 2, len(text))))
        i = min(j + 2, len(text))
    return out


def _close(tokens: list[Any], k: int) -> int:
    """The index of the bracket closing the one at k (len(tokens) when unclosed)."""
    depth = 0
    for i in range(k, len(tokens)):
        if tokens[i].value in ("(", "[", "{") and tokens[i].kind in ("op", "sym"):
            depth += 1
        elif tokens[i].value in (")", "]", "}") and tokens[i].kind in ("op", "sym"):
            depth -= 1
            if depth == 0:
                return i
    return len(tokens)


def _split(tokens: list[_J], seps: tuple[str, ...]) -> list[list[_J]]:
    out: list[list[_J]] = [[]]
    depth = 0
    for t in tokens:
        if t.kind == "op" and t.value in "([{":
            depth += 1
        elif t.kind == "op" and t.value in ")]}":
            depth -= 1
        elif t.kind == "op" and t.value in seps and depth == 0:
            out.append([])
            continue
        out[-1].append(t)
    return out if tokens else []


def _calls(tokens: list[_J], names: tuple[str, ...]) -> Iterator[tuple[str, list[list[_J]]]]:
    """(name, argument token lists) for each call of one of `names`, `builtins.<name>(` included."""
    for i, t in enumerate(tokens[:-1]):
        if t.kind == "name" and t.value in names and tokens[i + 1] == _J("op", "("):
            yield t.value, _split(tokens[i + 2:_close(tokens, i + 1)], (",",))


def _positional(args: list[list[_J]]) -> list[str | None]:
    """Each positional argument's literal value, or None when it is not a string literal."""
    return [a[0].value if len(a) == 1 and a[0].kind == "str" else None
            for a in args if a and not (len(a) > 1 and a[0].kind == "name" and a[1] == _J("op", "="))]


def _as_of_args(name: str, args: list[list[_J]]) -> tuple[str, ...] | str:
    """k2_as_of's or k2_known_to's literal arguments, or what is wrong with them."""
    if name == "k2_known_to":
        keys = args[0] if len(args) == 1 else []
        items = _split(keys[1:-1], (",",)) if keys[:1] == [_J("op", "[")] and _close(keys, 0) == len(keys) - 1 else []
        names = [a[0].value for a in items if len(a) == 1 and a[0].kind == "str" and _IDENT.fullmatch(a[0].value)]
        if not items or len(names) != len(items):
            return "k2_known_to(keys) takes one list of the history's key columns, unquoted names as string literals"
        return tuple(names)
    values = _positional(args)
    if len(args) != 3 or len(values) != 3 or None in values:
        return "k2_as_of(fact_time, target_alias, mode) takes three string literals"
    fact_time, alias, mode = (str(v) for v in values)
    if not _FACT_TIME.fullmatch(fact_time):
        return f"k2_as_of's fact_time {fact_time!r} is not a column, <alias>.<column> or <column>; compute it in a CTE first"
    if not _IDENT.fullmatch(alias):
        return f"k2_as_of's target_alias {alias!r} is not an unquoted identifier"
    if mode not in ("valid", "known"):
        return f"k2_as_of's mode {mode!r} is 'valid' or 'known'"
    return fact_time, alias, mode


def _fold(tokens: list[_J], code: str) -> list[str | _Ph] | None:
    """A `{{ }}` as SQL text and relation placeholders, or None when it is anything else."""
    parts: list[str | _Ph] = []
    for term in _split(tokens, ("~", "+")):
        if (len(term) > 2 and term[0].kind == "name" and term[0].value in _AS_OF_MACROS and term[1] == _J("op", "(")
                and _close(term, 1) == len(term) - 1):
            as_of = _as_of_args(term[0].value, _split(term[2:-1], (",",)))
            if isinstance(as_of, str):
                return None
            parts.append(_Ph(term[0].value, as_of, code))
        elif len(term) == 1 and term[0].kind == "str":
            parts.append(term[0].value)
        elif term == [_J("name", "this")]:
            parts.append(_Ph("this", (), code))
        elif (len(term) > 2 and term[0].kind == "name" and term[0].value in _RELATION_MACROS and term[1] == _J("op", "(")
              and _close(term, 1) == len(term) - 1):
            args = _positional(_split(term[2:-1], (",",)))
            if None in args:
                return None
            parts.append(_Ph(term[0].value, tuple(args), code))
        else:
            return None
    return parts


def model_reads(text: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """What a model's Jinja reads, for checks.access (A1 of the 2026-11-28 review): each
    `k2_input('<fence>', '<table>')`, each `ref()` by its last argument, and each expression that
    may render a relation this module cannot name: a `{{ }}` that does not fold (a variable, a
    macro, `api.Relation.create`), or a `k2_input` or `ref` whose arguments are not literals. A
    string holding Jinja (a hook's SQL) is read the same way, since dbt renders it."""
    inputs: list[tuple[str, str]] = []
    refs: list[str] = []
    opaque: list[str] = []
    for b in _blocks(text):
        if b.kind == "{#":
            continue
        tokens = _jtokens(b.code)
        for t in tokens:
            if t.kind == "str" and ("{{" in t.value or "{%" in t.value):
                i, r, o = model_reads(t.value)
                inputs, refs, opaque = inputs + i, refs + r, opaque + o
        for name, args in _calls(tokens, ("k2_input", "ref")):
            values = _positional(args)
            if name == "k2_input" and len(args) == 2 and len(values) == 2 and None not in values:
                inputs.append((str(values[0]), str(values[1])))
            elif name == "ref" and values and None not in values:
                refs.append(str(values[-1]))
            else:
                opaque.append(b.code)
        if b.kind == "{{" and tokens[:2] != [_J("name", "config"), _J("op", "(")] and _fold(tokens, b.code) is None:
            opaque.append(b.code)
    return inputs, refs, opaque


# --- The product a file belongs to ---------------------------------------------------------

@dataclass(frozen=True)
class _Ctx:
    path: str
    product: str
    fence: str
    build_catalog: str
    base: str
    seeds: frozenset[str]
    foreign: Mapping[str, str]  # another product's namespace (<fence>, <fence>__base) -> its id
    columns: Mapping[tuple[str, str], frozenset[str]]  # (<fence>, <table>) -> its contract's column names

    def own_ref(self, name: str | None) -> bool:
        return name is not None and (name.startswith(self.fence + "__") or name in self.seeds)


def _f(ctx: _Ctx, code: str, line: int, message: str, severity: Severity = "error") -> Finding:
    return Finding(f"sql.{code}", severity, ctx.path, f"line {line}: {message}")


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    products = {p.rsplit("/", 1)[0]: p.split("/")[1:3] for p in docs.documents if model.document_kind(p) == "product"}
    namespaces = {ns: model.product_id(d, n) for d, n in products.values() for ns in (model.reader_namespace(n), model.base_namespace(n))}
    columns: dict[tuple[str, str], frozenset[str]] = {}
    for path, data in docs.documents.items():
        folder, _, file = path.rpartition("/")
        if model.document_kind(path) == "contract" and folder in products:
            try:
                schema = (model.parse(path, data) or {}).get("schema") or [{}]
                columns[(model.fence(products[folder][1]), file.removesuffix(".odcs.yaml"))] = frozenset(
                    str(c["name"]) for c in schema[0].get("properties") or [] if isinstance(c, dict) and "name" in c)
            except (model.DocumentError, AttributeError, IndexError, KeyError, TypeError):
                continue  # the contract checks report a contract that does not read
    out = []
    for path, data in sorted(docs.documents.items()):
        directory = next((d for d in products if path.startswith(d + "/")), None)
        if directory is None or model.document_kind(path) is not None:
            continue
        domain, name = products[directory]
        rel = path[len(directory) + 1:]
        seeds = frozenset(PurePosixPath(p).stem for p in docs.documents if p.startswith(f"{directory}/seeds/") and p.endswith(".csv"))
        ctx = _Ctx(path, name, model.fence(name), model.build_catalog(domain), model.base_namespace(name), seeds,
                   {ns: pid for ns, pid in namespaces.items() if pid != model.product_id(domain, name)}, columns)
        text = data.decode("utf-8", errors="replace")
        if rel.startswith("models/") and not rel.endswith(".sql"):
            out.append(_f(ctx, "model-not-sql", 1, "models/ holds SQL only: dbt would read this file's config, hooks or properties, "
                                                   "which neither the compiler nor this lint reads; put a model's config in its "
                                                   "{{ config(...) }}"))
        elif rel.endswith(".sql"):
            out += _lint(text, ctx)
        elif rel.endswith((".yml", ".yaml")) and not rel.startswith("seeds/"):
            out += [_f(ctx, "hook", text.count("\n", 0, m.start()) + 1,
                       f"{m.group(1)} outside a model's config(): the compiled project runs no product hook, and this lint cannot "
                       f"read one; a model's writes belong in its SQL")
                    for m in _HOOK_KEY.finditer(text)]
    return out


# --- One file --------------------------------------------------------------------------------

@dataclass
class _Flat:
    """A template with its Jinja folded: the SQL text, each character's line, the placeholders."""

    chars: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    phs: list[_Ph] = field(default_factory=list)

    def emit(self, part: str | _Ph, line: int) -> None:
        if isinstance(part, _Ph):  # a placeholder is \x00<index>\x00, one SQL token
            self.phs.append(part)
            part = f"\x00{len(self.phs) - 1}\x00"
        else:
            part = part.replace("\x00", " ")  # so the file's own bytes never read as one
        self.chars += part
        self.lines += [line] * len(part)


def _liner(text: str) -> Callable[[int], int]:
    breaks = [i for i, c in enumerate(text) if c == "\n"]
    return lambda i: bisect.bisect_left(breaks, i) + 1


def _flatten(text: str, blocks: list[_Block], line: Callable[[int], int]) -> tuple[_Flat, list[_Flat]]:
    """The template's SQL with comments dropped, tags as spaces and each {{ }} folded; and the
    body of each `{% call statement %}`, flattened the same way, as SQL the model runs."""
    flat, runs = _Flat(), []
    pos, k = 0, 0
    while k < len(blocks):
        b = blocks[k]
        for i in range(pos, b.start):
            flat.emit(text[i], line(i))
        pos, k = b.end, k + 1
        tokens = _jtokens(b.code)
        if b.kind == "{{" and tokens[:2] != [_J("name", "config"), _J("op", "(")]:
            for part in _fold(tokens, b.code) or [_Ph("opaque", (), b.code)]:
                flat.emit(part, line(b.start))
        elif b.kind == "{%" and tokens[:1] == [_J("name", "call")] and _J("name", "statement") in tokens:
            end = next((m for m in range(k, len(blocks)) if blocks[m].kind == "{%" and blocks[m].code.split()[:1] == ["endcall"]), len(blocks))
            inner = [_Block(x.kind, x.code, x.start - b.end, x.end - b.end) for x in blocks[k:end]]
            stop = blocks[end].start if end < len(blocks) else len(text)

            def shifted(i: int, offset: int = b.end) -> int:
                return line(i + offset)

            body, nested = _flatten(text[b.end:stop], inner, shifted)
            runs += [body, *nested]
            pos, k = (blocks[end].end, end + 1) if end < len(blocks) else (len(text), len(blocks))
        elif b.kind == "{%":
            flat.emit(" ", line(b.start))
    for i in range(pos, len(text)):
        flat.emit(text[i], line(i))
    return flat, runs


def _lint(text: str, ctx: _Ctx) -> list[Finding]:
    line = _liner(text)
    blocks = _blocks(text)
    out, configs = [], []
    for b in blocks:
        if b.kind == "{#":
            continue
        tokens = _jtokens(b.code)
        out += _jinja(tokens, line(b.start), ctx, env=True)
        configs += [(text[b.start:b.end], line(b.start)) for _ in _calls(tokens, ("config",))]
    flat, runs = _flatten(text, blocks, line)
    sql_tokens = _stokens(flat)
    out += _sql(sql_tokens, flat.phs, ctx) + _as_of(sql_tokens, flat.phs, ctx)
    for body in runs:
        out += _execute(body, "statement", "a {% call statement %} block", ctx)
    return out + _config(text, configs, ctx)


def _jinja(tokens: list[_J], line: int, ctx: _Ctx, env: bool) -> list[Finding]:
    """What one Jinja block's code calls: env_var, source, ref, run_query, adapter."""
    out = []
    strings = "".join(t.value for t in tokens if t.kind == "str")  # 'env_' ~ 'var' joined too
    if env and (_J("name", "env_var") in tokens or "env_var" in strings):
        out.append(_f(ctx, "env-var", line, "env_var() reads the dbt process's environment, which holds the builder's credentials, and "
                                            "what it renders lands in SQL, a table or a log; a model takes nothing from the environment"))
    for name, args in _calls(tokens, ("source", "ref", "run_query")):
        values = _positional(args)
        if name == "source" and (not values or values[0] != ctx.fence):
            src = values[0] if values else None
            out.append(_f(ctx, "foreign-read", line,
                          f"source('{src}', ...) reads product {src}'s table at main, untested rows included (ADR-061 part 7); read "
                          f"another product only as {{{{ k2_input('{src}', '<table>') }}}}, declared as an input port"
                          if src else "a source() whose name is not a literal: the lint cannot tell whose table it reads; "
                                      "write source('<fence>', '<table>') of this product, or k2_input() for another's"))
        elif name == "ref" and (not values or None in values or not ctx.own_ref(values[-1])):
            out.append(_f(ctx, "foreign-read", line,
                          f"ref({', '.join(repr(v) for v in values)}) is not a model ({ctx.fence}__<table>) or seed of {ctx.product}: "
                          f"another product's model is its base at main, untested rows included; read it with k2_input(), declared "
                          f"as an input port"))
        elif name == "run_query":
            out += _run(args[0] if args else [], "run_query()", line, ctx)
    for name, args in _calls(tokens, _AS_OF_MACROS):
        if isinstance(problem := _as_of_args(name, args), str):
            out.append(_f(ctx, "as-of", line, f"{problem} (dbt/macros/k2_as_of.sql), so the lint can read what it renders"))
    for i, t in enumerate(tokens[:-2]):
        if t == _J("name", "adapter") and tokens[i + 1] == _J("op", ".") and tokens[i + 2].kind == "name":
            method = tokens[i + 2].value
            if method == "execute":
                after = tokens[i + 3:]
                args = _split(after[1:_close(after, 0)], (",",)) if after[:1] == [_J("op", "(")] else []
                out += _run(args[0] if args else [], "adapter.execute()", line, ctx)
            elif method not in READ_ONLY_ADAPTER:
                out.append(_f(ctx, "statement-ddl", line, f"adapter.{method}() can create, change or drop a relation, or call a macro "
                                                          f"the lint cannot read; a model runs only reads"))
            else:
                out.append(_f(ctx, "adapter", line, f"adapter.{method}() reads the catalog as the builder, outside source(), ref() "
                                                    f"and k2_input(), so k2 cannot pin or check what it reads; prefer those", "warning"))
    return out


def _run(arg: list[_J], what: str, line: int, ctx: _Ctx) -> list[Finding]:
    parts = _fold(arg, " ".join(t.value for t in arg))
    if parts is None:
        return [_f(ctx, "statement-ddl", line, f"{what} runs SQL the lint cannot read; write it as literal SQL, with no Jinja but "
                                               f"this, ref(), source() and k2_input()")]
    flat = _Flat()
    for part in parts:
        flat.emit(part, line)
    return _execute(flat, "statement", what, ctx)


# --- SQL tokens --------------------------------------------------------------------------------

class _T(NamedTuple):
    kind: str  # word (lower-cased), qid (quoted, unquoted and lower-cased), str, num, ph, sym
    value: str
    line: int


_SQL = re.compile(r"""(?P<ws>\s+)|(?P<com>--[^\n]*|/\*.*?(?:\*/|\Z))|(?P<str>'(?:[^']|'')*'?)|(?P<qid>"(?:[^"]|"")*"?)"""
                  r"|(?P<ph>\x00\d+\x00)|(?P<word>[A-Za-z_][A-Za-z0-9_$]*)|(?P<num>[0-9][0-9.]*)|(?P<sym>.)", re.S)


def _stokens(flat: _Flat) -> list[_T]:
    text, out = "".join(flat.chars), []
    for m in _SQL.finditer(text):
        kind = m.lastgroup or "sym"
        if kind in ("ws", "com"):
            continue
        value = m.group()
        if kind == "word":
            value = value.lower()
        elif kind == "qid":
            value = value[1:].removesuffix('"').replace('""', '"').lower()
        elif kind == "ph":
            value = value[1:-1]
        out.append(_T(kind, value, flat.lines[m.start()]))
    return out


def _is(t: _T | None, kind: str, *values: str) -> bool:
    return t is not None and t.kind == kind and (not values or t.value in values)


def _at(tokens: list[_T], i: int) -> _T | None:
    return tokens[i] if 0 <= i < len(tokens) else None


def _chain(tokens: list[_T], j: int) -> list[_T]:
    """The dotted name starting at j: words, quoted identifiers and placeholders."""
    parts = []
    while _is(_at(tokens, j), "word") or _is(_at(tokens, j), "qid") or _is(_at(tokens, j), "ph"):
        parts.append(tokens[j])
        if not _is(_at(tokens, j + 1), "sym", "."):
            break
        j += 2
    return parts


def _relations(tokens: list[_T]) -> Iterator[tuple[list[_T], int]]:
    """Every relation a statement reads, with its first token's index: after FROM in a clause with
    a SELECT at its level, JOIN, a comma in a FROM list, TABLE(<name>), MERGE ... USING."""
    selects, lists, merge = [False], [False], False
    for i, t in enumerate(tokens):
        expect = False
        if t.kind == "sym":
            if t.value == "(":
                selects.append(False)
                lists.append(False)
            elif t.value == ")" and len(selects) > 1:
                selects.pop()
                lists.pop()
            elif t.value == ";":
                selects, lists, merge = [False], [False], False
            elif t.value == ",":
                expect = lists[-1]
        elif t.kind == "word":
            if t.value == "select":
                selects[-1], lists[-1] = True, False
            elif t.value == "merge":
                merge = True
            elif t.value in _FROM_LIST_ENDS:
                lists[-1] = False
            elif t.value == "from" and selects[-1] and not _is(_at(tokens, i - 1), "word", "distinct"):
                lists[-1] = expect = True
            elif t.value == "join":
                lists[-1] = expect = True
            elif t.value == "using" and merge:
                expect = True
            elif t.value == "table" and _is(_at(tokens, i + 1), "sym", "("):
                parts = _chain(tokens, i + 2)
                if parts and _is(_at(tokens, i + 2 + 2 * len(parts) - 1), "sym", ")"):
                    yield parts, i + 2
        if expect:
            parts = _chain(tokens, i + 1)
            if parts and not _is(parts[0], "word", *_NOT_RELATION):
                yield parts, i + 1


def _ctes(tokens: list[_T]) -> set[str]:
    out = set()
    for i in range(1, len(tokens)):
        if (tokens[i].kind in ("word", "qid") and (_is(tokens[i - 1], "word", "with", "recursive") or _is(tokens[i - 1], "sym", ","))):
            j = _close(tokens, i + 1) + 1 if _is(_at(tokens, i + 1), "sym", "(") else i + 1
            if _is(_at(tokens, j), "word", "as") and _is(_at(tokens, j + 1), "sym", "("):
                out.add(tokens[i].value)
    return out


def _sql(tokens: list[_T], phs: list[_Ph], ctx: _Ctx) -> list[Finding]:
    """Hand-written time travel, and every relation not reached through source, ref or k2_input."""
    out = []
    for i, t in enumerate(tokens):
        words = [x.value for x in tokens[i + 1:i + 4] if x.kind == "word"]
        if _is(t, "word", "for") and len(words) == 3 and words[0] in _TIME_TRAVEL and words[1:] == ["as", "of"]:
            out.append(_f(ctx, "time-travel", t.line, f"FOR {words[0].upper()} AS OF written by hand: a snapshot picked by hand may be "
                                                      f"one no test passed; k2_input() pins each input at the snapshot the wrapper "
                                                      f"resolved from its tested tag (A7)"))
        elif _is(t, "word", "table_changes"):
            out.append(_f(ctx, "time-travel", t.line, "table_changes reads a table between snapshots picked by hand, untested ones "
                                                      "included; read inputs through k2_input()"))
    ctes = _ctes(tokens)
    for parts, _ in _relations(tokens):
        if len(parts) == 1 and parts[0].kind == "ph":
            ph = phs[int(parts[0].value)]
            if ph.kind == "opaque":
                out.append(_f(ctx, "relation-unmanaged", parts[0].line, f"a relation the lint cannot read, {{{{ {ph.code} }}}}: read "
                                                                        f"through source(), ref() or k2_input()", "warning"))
            continue
        names = [p.value if p.kind != "ph" else "{{...}}" for p in parts]
        relation, namespace = ".".join(names), names[-2] if len(names) > 1 else ""
        if namespace in ctx.foreign:
            out.append(_f(ctx, "foreign-read", parts[0].line,
                          f"{relation} names product {ctx.foreign[namespace]}'s tables by hand, outside k2_input(): that reads its "
                          f"main snapshot, untested rows included (ADR-061 part 7); declare it an input port and read "
                          f"{{{{ k2_input('{namespace.removesuffix(model.BASE_SUFFIX)}', '<table>') }}}}"))
        elif not (len(names) == 1 and names[0] in ctes):
            out.append(_f(ctx, "relation-unmanaged", parts[0].line,
                          f"{relation} is read by name, not through source(), ref() or k2_input(): k2 cannot pin it to a tested "
                          f"snapshot or check whose it is", "warning"))
    return out


def _as_of(tokens: list[_T], phs: list[_Ph], ctx: _Ctx) -> list[Finding]:
    """Each k2_as_of's alias, bound in this model's SQL, and never straight to a k2_input of a
    table that lacks the columns its mode reads."""
    calls = [(t, phs[int(t.value)]) for t in tokens if t.kind == "ph" and phs[int(t.value)].kind == "k2_as_of"]
    if not calls:
        return []
    ctes = _ctes(tokens)
    bound: dict[str, list[_Ph | str | None]] = {}  # alias -> what binds it: a placeholder, a relation name, None for a subquery
    for parts, start in _relations(tokens):
        j = start + 2 * len(parts) - 1
        j += 1 if _is(_at(tokens, j), "word", "as") else 0
        if _is(_at(tokens, j), "word") or _is(_at(tokens, j), "qid"):
            bound.setdefault(tokens[j].value, []).append(phs[int(parts[0].value)] if len(parts) == 1 and parts[0].kind == "ph" else parts[-1].value)
    for i, t in enumerate(tokens):
        j = i + 2 if _is(_at(tokens, i + 1), "word", "as") else i + 1
        if _is(t, "sym", ")") and (_is(_at(tokens, j), "word") or _is(_at(tokens, j), "qid")):
            bound.setdefault(tokens[j].value, []).append(None)
    out = []
    for t, ph in calls:
        _, alias, mode = ph.args
        binders = bound.get(str(alias).lower(), []) + ([str(alias).lower()] if str(alias).lower() in ctes else [])
        if not binders:
            out.append(_f(ctx, "as-of", t.line, f"k2_as_of's target_alias {alias!r} is bound nowhere in this model: alias the history "
                                                 f"after FROM or JOIN, read through k2_input(), or name a CTE over it"))
        for b in binders:
            if not (isinstance(b, _Ph) and b.kind == "k2_input"):
                continue
            have = ctx.columns.get((str(b.args[0]), str(b.args[1])))
            if mode == "known":
                out.append(_f(ctx, "as-of", t.line, f"k2_as_of(..., {alias!r}, 'known') over k2_input('{b.args[0]}', '{b.args[1]}') "
                                                     f"itself: known_to is no column of a contract, so compute it with "
                                                     f"k2_known_to(<keys>) in a CTE and alias that"))
            elif have is not None and not {"valid_from", "valid_to"} <= have:
                out.append(_f(ctx, "as-of", t.line, f"k2_as_of(..., {alias!r}, 'valid') over k2_input('{b.args[0]}', '{b.args[1]}'), "
                                                     f"whose contract has no valid_from and valid_to: it keeps no history (ADR-030)"))
    return out


def _statements(tokens: list[_T]) -> Iterator[list[_T]]:
    current: list[_T] = []
    for t in tokens:
        if _is(t, "sym", ";"):
            yield current
            current = []
        else:
            current.append(t)
    yield current


def _execute(flat: _Flat, locus: str, what: str, ctx: _Ctx) -> list[Finding]:
    """SQL a model runs besides its SELECT: reads pass, a write must be to {{ this }}, anything
    else is the locus's error (`hook` or `statement-ddl`)."""
    tokens, out = _stokens(flat), []
    code = "hook" if locus == "hook" else "statement-ddl"
    for stmt in _statements(tokens):
        i = 0
        while _is(_at(stmt, i), "sym", "("):
            i += 1
        if _is(_at(stmt, i), "word", "explain"):
            i += 1
            while _is(_at(stmt, i), "word", "analyze", "verbose"):
                i += 1
            i = _close(stmt, i) + 1 if _is(_at(stmt, i), "sym", "(") else i
        first = _at(stmt, i)
        if first is None:
            continue
        opaque = [flat.phs[int(t.value)].code for t in stmt if t.kind == "ph" and flat.phs[int(t.value)].kind == "opaque"]
        if opaque:
            out.append(_f(ctx, code, first.line, f"{what} runs SQL the lint cannot read ({{{{ {opaque[0]} }}}}); write it as literal SQL, "
                                                 f"with no Jinja but this, ref(), source() and k2_input()"))
        elif _is(first, "word", *_DML):
            keyword = _DML[first.value]
            j = i + 2 if keyword and _is(_at(stmt, i + 1), "word", keyword) else i + 1
            target = _chain(stmt, j)
            if not (len(target) == 1 and target[0].kind == "ph" and flat.phs[int(target[0].value)].kind == "this"):
                shown = ".".join(p.value if p.kind != "ph" else flat.phs[int(p.value)].code for p in target) or "?"
                out.append(_f(ctx, "write-outside-base", first.line,
                              f"{what} runs {first.value.upper()} on {shown}: it may write only {{{{ this }}}}, the model it "
                              f"belongs to. Any other table, one of {ctx.build_catalog}.{ctx.base} included, would change "
                              f"after its tests ran, and the publish would tag rows no test read"))
        elif not _is(first, "word", *_READ):
            out.append(_f(ctx, code, first.line, _hook_message(what, first, ctx) if locus == "hook" else
                          f"{what} runs {first.value.upper()}: a model runs only reads, and DDL, CALL, grants and session "
                          f"changes belong to the platform (deploy, the wrapper, maintenance)"))
    return out + _sql(tokens, flat.phs, ctx)


def _hook_message(what: str, first: _T, ctx: _Ctx) -> str:
    return (f"{what} runs {first.value.upper()} as the builder, outside the build's tests and the publish; a hook that touches a "
            f"view, a tag or a grant changes what readers see, and the gate showed a reader view replaced under the wrong owner "
            f"(h1.33). A hook may only read, or write {{{{ this }}}}. This lint is one layer: the control is ownership, the builder "
            f"owning only {ctx.base} and the publisher the views (ADR-061 part 2)")


# --- config() --------------------------------------------------------------------------------

# The one form model.model_config reads, as a whole block; a config( call in any other is unseen by it.
_CANONICAL = re.compile(r"\{\{-?\s*config\(.*\)\s*-?\}\}", re.S)


def _config(text: str, configs: list[tuple[str, int]], ctx: _Ctx) -> list[Finding]:
    if not configs:
        return []
    block, line = configs[0]
    try:
        config = model.model_config(text)
    except ValueError as e:  # a Jinja operator (~, |) included: model_config raises nothing else
        return [_f(ctx, "config-unreadable", line, f"config() is not one call of literal keyword arguments ({e}); the compiler "
                                                   f"and this lint read only that form (model.model_config)")]
    if len(configs) > 1 or not _CANONICAL.fullmatch(block):
        return [_f(ctx, "config-unreadable", line, "config() must be written once, as {{ config(...) }} with nothing else in the "
                                                   "block: dbt reads this call, and the compiler and this lint (model.model_config) "
                                                   "would not")]
    out = []
    for key, value in config.items():
        if key == "materialized":
            if value == "materialized_view":
                out.append(_f(ctx, "materialized-view", line, "materialized='materialized_view': no product declares one (ADR-061 "
                                                              "part 3), and the Iceberg REST catalog has none (gate h1.16); use "
                                                              "'table' or 'incremental'"))
            elif value == "view" and config.get("view_security") != "invoker":
                out.append(_f(ctx, "definer-view", line, "materialized='view' without view_security='invoker' is a definer view: it "
                                                         "reads as the builder and shows its base with no policy and no tag; readers "
                                                         "see a product only through the view k2 generates (ADR-061 condition 1)"))
            elif value not in MATERIALIZATIONS:
                out.append(_f(ctx, "config-key", line, f"materialized={value!r} is not one k2 builds: {', '.join(MATERIALIZATIONS)}"))
        elif key == "incremental_predicates":
            out.append(_f(ctx, "incremental-predicates", line, "incremental_predicates narrows the rows a merge compares, so a row "
                                                               "outside them is inserted again and the unique key (A7) no longer "
                                                               "holds; drop it, the unique key decides what a merge matches"))
        elif key in ("database", "schema", "alias"):
            out.append(_f(ctx, "write-outside-base", line, f"config({key}=...) moves where dbt writes; a product writes only its base, "
                                                           f"{ctx.build_catalog}.{ctx.base}, under the names the compiler gives"))
        elif key in ("pre_hook", "post_hook"):
            for sql in _hook_sql(value):
                out += _hook(sql, key, line, ctx)
        elif key not in ALLOWED_CONFIG:
            out.append(_f(ctx, "config-key", line, f"config({key}=...) is not one a product sets: "
                                                   f"{CONFIG_REASONS.get(key, 'the compiler sets it, or k2 does not use it')}; a "
                                                   f"product's config() takes {', '.join(ALLOWED_CONFIG)}"))
    return out


def _hook_sql(value: Any) -> list[str]:
    """A hook's SQL: a string, a {sql, transaction} map, or a list of either."""
    items = [x.get("sql") if isinstance(x, dict) else x for x in (value if isinstance(value, list) else [value])]
    return [x for x in items if isinstance(x, str)]


def _hook(sql: str, key: str, line: int, ctx: _Ctx) -> list[Finding]:
    blocks = _blocks(sql)
    out = [f for b in blocks if b.kind != "{#" for f in _jinja(_jtokens(b.code), line, ctx, env=False)]
    flat, runs = _flatten(sql, blocks, lambda i: line)
    for body in (flat, *runs):
        out += _execute(body, "hook", f"a {key}", ctx)
    return out

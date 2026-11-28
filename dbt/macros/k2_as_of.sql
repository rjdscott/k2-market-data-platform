{#- k2_as_of(fact_time, target_alias, mode): the join predicate that picks, for one fact, the version of
    a history row that held at the fact's time (phases 004 and 005, step I-6, frozen in
    docs/development/phase-001-interfaces.md). It renders a predicate only, over an alias the model
    has already bound: the model reads the history with k2_input and aliases it, so the history is
    pinned at its tested snapshot and this macro reads no relation of its own.
    History is half-open (ADR-030): a version holds from valid_from up to but not including
    valid_to; the open row's valid_to is 9999-12-31 23:59:59 UTC, a first version opens at
    1970-01-01 00:00:00 UTC, and neither is ever null. So exactly one version holds at any instant,
    and a fact exactly on valid_from picks the new version.
      mode 'valid' -> (<alias>.valid_from <= <fact_time> and <fact_time> < <alias>.valid_to)
                      the version true at the fact's time, as the history now states it.
      mode 'known' -> (<alias>.recorded_at <= <fact_time> and <fact_time> < <alias>.known_to)
                      the newest version known at the fact's time, so nothing recorded later is
                      looked ahead to: a 10:00 UTC candle gets the previous day's 16:00 Frankfurt
                      fixing, though that day's is valid for it. [recorded_at, known_to) is each
                      version's interval in knowledge time; known_to is not a stored column but
                      k2_known_to(keys) below, computed over the history in a CTE that the alias
                      then names. An alias without known_to fails the build at Trino, never a
                      fan-out: the predicate holds for at most one version per key.
    Arguments are SQL text, checked here at compile and by k2gen's lint (sql.as-of):
      fact_time    a column, <alias>.<column> or <column>, unquoted: a time computed from
                   others is a column of a CTE first
      target_alias the alias of the history in this model's FROM, an unquoted identifier
      mode         'valid' or 'known'
    anything else is a compiler error. dbt/tests/k2_as_of/ holds the render cases (make test-dbt)
    and the unit tests of every boundary. -#}
{% macro k2_as_of(fact_time, target_alias, mode) -%}
  {%- set ident = '[A-Za-z_][A-Za-z0-9_]*' -%}
  {%- if mode not in ('valid', 'known') -%}
    {{ exceptions.raise_compiler_error("k2_as_of: mode is 'valid' or 'known', not " ~ mode) }}
  {%- endif -%}
  {%- if target_alias is not string or not modules.re.fullmatch(ident, target_alias) -%}
    {{ exceptions.raise_compiler_error("k2_as_of: target_alias is the alias the model bound to the history, an unquoted identifier, not " ~ target_alias) }}
  {%- endif -%}
  {%- if fact_time is not string or not modules.re.fullmatch(ident ~ '([.]' ~ ident ~ ')?', fact_time) -%}
    {{ exceptions.raise_compiler_error("k2_as_of: fact_time is a column, <alias>.<column> or <column>, not " ~ fact_time ~ "; compute any other time as a column of a CTE first") }}
  {%- endif -%}
  {%- if mode == 'valid' -%}
    ({{ target_alias }}.valid_from <= {{ fact_time }} and {{ fact_time }} < {{ target_alias }}.valid_to)
  {%- else -%}
    ({{ target_alias }}.recorded_at <= {{ fact_time }} and {{ fact_time }} < {{ target_alias }}.known_to)
  {%- endif -%}
{%- endmacro %}

{#- k2_known_to(keys): the end of a history row's interval in knowledge time, for k2_as_of's mode
    'known': the recorded_at of the next version of the same key, or 9999-12-31 23:59:59 UTC on the
    newest, never null. keys is the history's key without valid_from, a list of unquoted column
    names. Two versions recorded the same instant are ordered by valid_from, so the earlier one's
    interval is empty and the later one is known. Selected beside the history's columns, in the CTE
    whose name the k2_as_of alias is:
      with known as (select h.*, {{ k2_known_to(['venue', 'instrument_id']) }} as known_to
                     from {{ k2_input('security_master', 'instrument') }} as h) -#}
{% macro k2_known_to(keys) -%}
  {#- a loop's set does not leave the loop in Jinja, a namespace's does -#}
  {%- set ns = namespace(bad=keys is string or keys is not iterable or keys | length == 0) -%}
  {%- for k in (keys if not ns.bad else []) if k is not string or not modules.re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', k) -%}
    {%- set ns.bad = true -%}
  {%- endfor -%}
  {%- if ns.bad -%}
    {{ exceptions.raise_compiler_error("k2_known_to: keys is a list of the history's key columns without valid_from, unquoted, not " ~ keys) }}
  {%- endif -%}
  coalesce(lead(recorded_at) over (partition by {{ keys | join(', ') }} order by recorded_at, valid_from), timestamp '9999-12-31 23:59:59 UTC')
{%- endmacro %}

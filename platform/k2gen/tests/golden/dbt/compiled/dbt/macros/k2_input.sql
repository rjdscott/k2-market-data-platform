{#- k2_input(product_fence, table): an upstream product's table at the snapshot the wrapper resolved
    (phase 001 interface A7, docs/development/phase-001-interfaces.md). Spike d's macro
    (scripts/spikes/2026-11-26/spine-d-k2-input/, docs/research/2026-11-26-release-spine-spikes.md),
    its parameter named as A7 freezes it. The shape that holds in one dbt build:
      k2_inputs absent                           -> source(), unpinned (a developer's run)
      k2_inputs set, no "<product_fence>/<table>" -> a compiler error when the model compiles (at
                                                    parse source(): the project holds every product, and
                                                    the wrapper pins one product's inputs)
      a unit test                                -> source(), which dbt has already replaced with the
                                                    fixture's CTE (__dbt__cte__<source>__<table>), and a
                                                    CTE takes no FOR VERSION AS OF
      {"absent": true}                           -> zero rows of the source's declared columns, each
                                                    cast to its data_type, in its order:
                                                    (select cast(null as <type>) as "<column>", ... where false)
      {"absent": ...} any other way              -> a compiler error, never a guess between the two
      otherwise                                  -> source() FOR VERSION AS OF <snapshot>
      a snapshot missing, null or not an integer -> a compiler error, never a guessed id
    The var is {"k2_inputs": {"<product_fence>/<table>": {"snapshot": <id>} or {"absent": true}}}, set
    by the wrapper only. The snapshot must be an integer (a JSON number, not a string, a float or a
    boolean), so the var can only ever add a number to the SQL; it is typed BIGINT, since an id under
    2^31 is an integer literal, which Trino refuses as a table version.
    Absent (added 2026-11-28, step W0-7): the wrapper passes {"absent": true} only for an optional
    input whose port says whenAbsent empty or emptyIntoTable (contracts/schema/k2-v2.schema.json),
    and only while that input is paused in the release or was never audited; it exits 4 before dbt
    runs when a required one has no snapshot, and leaves an optional whenAbsent fail one out (the
    compiler error above). The columns are the source's in compiled/dbt/models/_sources.yml, which
    k2gen writes from the input's contract, so the empty relation has the contract's names and
    types. It is a subquery with no alias: a model that qualifies the input's columns aliases it,
    as it would a pinned relation. A k2_input_relationships test against it finds no parent row.
    k2gen check refuses whenAbsent empty in a product with a model that is not incremental, which
    the zero rows would rebuild and publish empty (docs.empty-input-into-table).
    dbt/tests/k2_input/ proves each case with no warehouse (make test-dbt). -#}
{% macro k2_input(product_fence, table) -%}
  {%- set rel = source(product_fence, table) -%}
  {%- set inputs = var('k2_inputs', none) -%}
  {%- set key = product_fence ~ '/' ~ table -%}
  {%- if inputs is none -%}
    {{ rel }}
  {%- elif key not in inputs -%}
    {#- Only when the model compiles: dbt parses every model of the project, and another product's
        models name inputs this run's wrapper (one product's) does not pin. -#}
    {%- if execute -%}
      {{ exceptions.raise_compiler_error("k2_input: k2_inputs is set but has no entry " ~ key) }}
    {%- endif -%}
    {{ rel }}
  {%- elif model.resource_type == 'unit_test' -%}
    {{ rel }}
  {%- elif inputs[key] is mapping and 'absent' in inputs[key] -%}
    {%- set entry = inputs[key] -%}
    {%- if entry | length != 1 or entry['absent'] is not sameas true -%}
      {{ exceptions.raise_compiler_error('k2_input: k2_inputs entry ' ~ key ~ ' is absent only as {"absent": true}: ' ~ entry) }}
    {%- endif -%}
    {%- if execute -%}
      {#- The graph holds the sources only once dbt compiles; at parse, source() above has already
          recorded the dependency, and what renders is never run. -#}
      {%- set found = graph.sources.values() | selectattr('source_name', 'equalto', product_fence)
                      | selectattr('name', 'equalto', table) | list -%}
      {%- set columns = found[0].columns.values() | list if found else [] -%}
      {%- if not columns or columns | rejectattr('data_type') | list -%}
        {{ exceptions.raise_compiler_error("k2_input: " ~ key ~ " is absent, and its source does not declare every column with a data_type") }}
      {%- endif -%}
      {%- set parts = [] -%}
      {%- for c in columns -%}
        {%- do parts.append('cast(null as ' ~ c.data_type ~ ') as ' ~ adapter.quote(c.name)) -%}
      {%- endfor -%}
      (select {{ parts | join(', ') }} where false)
    {%- else -%}
      {{ rel }}
    {%- endif -%}
  {%- else -%}
    {%- set entry = inputs[key] -%}
    {%- set snapshot = entry.get('snapshot') if entry is mapping else none -%}
    {%- if snapshot is not integer -%}
      {{ exceptions.raise_compiler_error("k2_input: k2_inputs entry " ~ key ~ " has no integer snapshot: " ~ entry) }}
    {%- endif -%}
    {{ rel }} FOR VERSION AS OF BIGINT '{{ snapshot }}'
  {%- endif -%}
{%- endmacro %}

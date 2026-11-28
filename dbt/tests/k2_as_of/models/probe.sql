-- The argument cases (scripts/dbt-test.sh): each var replaces one argument, and a bad one is a
-- compiler error at parse.
select i.version, {{ k2_known_to(var('keys', ['venue', 'instrument_id'])) }} as known_to
from {{ k2_input('ref', 'instrument') }} as i
where {{ k2_as_of(var('fact_time', 'i.recorded_at'), var('target_alias', 'i'), var('mode', 'valid')) }}

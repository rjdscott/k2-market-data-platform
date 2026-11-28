-- Each fact's instrument version by valid time and by knowledge time (dbt/macros/k2_as_of.sql).
with known as (
  select h.*, {{ k2_known_to(['venue', 'instrument_id']) }} as known_to
  from {{ k2_input('ref', 'instrument') }} as h
)

select f.fact_id, i.version as valid_version, k.version as known_version
from {{ k2_input('up', 'facts') }} as f
left join {{ k2_input('ref', 'instrument') }} as i
  on i.venue = f.venue and i.instrument_id = f.instrument_id
  and {{ k2_as_of('f.fact_time', 'i', 'valid') }}
left join known as k
  on k.venue = f.venue and k.instrument_id = f.instrument_id
  and {{ k2_as_of('f.fact_time', 'k', 'known') }}

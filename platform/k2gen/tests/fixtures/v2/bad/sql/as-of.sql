-- expect: sql.as-of
-- A point-in-time join over an input that keeps no history: silver_readings has no valid_from.
{{ config(materialized='table') }}
select cast(d.read_at as date) as load_date, d.station_id, count(*) as readings
from {{ k2_input('station_readings', 'silver_readings') }} as d
join {{ k2_input('station_readings', 'silver_readings') }} as h
  on h.station_id = d.station_id and {{ k2_as_of('d.read_at', 'h', 'valid') }}
group by 1, 2

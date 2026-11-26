-- expect: sql.hook
-- A hook passing SELECT on the base to a reader.
{{ config(materialized='table', post_hook='grant select on {{ this }} to user "p.analyst-full"') }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}

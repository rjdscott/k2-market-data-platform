-- expect: sql.foreign-read
-- Another product's table through source(): its main snapshot and untested rows.
{{ config(materialized='table') }}
select station_id from {{ source('station_readings', 'silver_readings') }}

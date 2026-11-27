-- expect: sql.relation-unmanaged
-- A relation by its bare name, which no macro resolves or pins: a warning.
{{ config(materialized='table') }}
select station_id from silver_readings

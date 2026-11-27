-- expect: sql.write-outside-base
-- A post-hook writing another of the product's own models: dbt build runs it after that model's
-- tests, so the publish would tag rows no test read. A hook writes only {{ this }}.
{{ config(materialized='table', post_hook="delete from {{ ref('daily_load__silver_load') }} where true") }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}

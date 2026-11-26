-- Synthetic: the seed's rows, each event time the build's clock minus the row's age, so a
-- build on any day holds rows younger and older than delayed_reader's 20-minute delay.
-- Trino fixes current_timestamp once per query, so the ages are exact within a build.
-- The seed is seeds/event_offsets.csv, named <fence>__<seed> as model.dbt_model names a model.
{{ config(materialized='table') }}
select
  cast(id as bigint) as id,
  cast(region as varchar) as region,
  cast(note as varchar) as note,
  cast(current_timestamp - age_minutes * interval '1' minute as timestamp(6) with time zone) as event_ts
from {{ ref('access_canary__event_offsets') }}

-- What this does and why: TARGET, the euro payment system, is closed on Saturdays, Sundays and
-- six holidays a year, and the ECB publishes its reference rates only on the days TARGET is open.
-- A person writes the holidays that fall on weekdays into seeds/target_closing_days.csv. This
-- model lays out every calendar day from the seed's first closing day (1 January of its first
-- year) to 31 December of its last year, and marks each day open (is_target_day) or closed, with
-- the reason. A table rebuilt whole on every run: about 1,100 rows, so incremental buys nothing.
-- The seed is named <fence>__<seed> (model.dbt_seed), as a model is.
{{ config(materialized='table') }}
with closing as (
  select
    cast(closing_day as date) as closing_day,
    cast(name as varchar) as name
  from {{ ref('calendars__target_closing_days') }}
),

span as (
  -- first_day: the seed's first closing day; last_day: 31 December of its last closing day's year
  select
    min(closing_day) as first_day,
    date_add('day', -1, date_add('year', 1, date_trunc('year', max(closing_day)))) as last_day
  from closing
),

days as (
  -- sequence() steps one day at a time between two dates (Trino caps it at 10,000 days)
  select t.calendar_date
  from span
  cross join unnest(sequence(span.first_day, span.last_day)) as t (calendar_date)
)

select
  d.calendar_date,
  -- day_of_week: Monday is 1, Saturday 6, Sunday 7
  cast(day_of_week(d.calendar_date) < 6 and c.closing_day is null as boolean) as is_target_day,
  cast(case
    when day_of_week(d.calendar_date) = 6 then 'Saturday'
    when day_of_week(d.calendar_date) = 7 then 'Sunday'
    else c.name
  end as varchar) as closing_reason
from days as d
left join closing as c on c.closing_day = d.calendar_date

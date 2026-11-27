-- What this does and why: a reader wants one rate per currency on every day the euro payment
-- system (TARGET) is open, even when the ECB published no fixing that day. This model lays every
-- currency out on the TARGET days from the calendars product (read through k2_input, so at its
-- tested snapshot), from the currency's first fixing to the newest fixing of any currency. A day
-- with no fixing takes the last published one for at most five TARGET days, flagged is_carried
-- with carried_days; after five, rate is null. Days are counted on TARGET days only, so a weekend
-- adds none. rate_available_at is 16:00 Frankfurt time on the day, in UTC: the ECB's usual
-- publication time. A table rebuilt whole on every run: tens of thousands of rows at most.
{{ config(materialized='table') }}
with target_days as (
  -- the open days, numbered in date order: the distance between two numbers counts TARGET days
  select
    calendar_date,
    row_number() over (order by calendar_date) as day_number
  from {{ k2_input('calendars', 'target_days') }}
  where is_target_day
),

rates as (
  select rate_date, currency, rate
  from {{ ref('ecb_fx_rates__silver_rates') }}
),

newest as (
  select max(rate_date) as last_day
  from rates
),

spans as (
  -- each currency from its own first fixing to the newest fixing of any currency
  select r.currency, min(r.rate_date) as first_day, max(n.last_day) as last_day
  from rates as r
  cross join newest as n
  group by r.currency
),

grid as (
  select d.calendar_date as rate_date, d.day_number, s.currency
  from spans as s
  join target_days as d on d.calendar_date between s.first_day and s.last_day
),

filled as (
  -- last_value ... ignore nulls: the newest non-null value so far in the currency's day order,
  -- the last published rate and the number of the day it was published
  select
    g.rate_date,
    g.currency,
    g.day_number,
    last_value(r.rate) ignore nulls over (
      partition by g.currency order by g.day_number rows between unbounded preceding and current row
    ) as last_rate,
    last_value(case when r.rate is not null then g.day_number end) ignore nulls over (
      partition by g.currency order by g.day_number rows between unbounded preceding and current row
    ) as last_published_day
  from grid as g
  left join rates as r on r.rate_date = g.rate_date and r.currency = g.currency
),

aged as (
  select rate_date, currency, last_rate, day_number - last_published_day as carried_days
  from filled
)

select
  rate_date,
  currency,
  cast(case when carried_days <= 5 then last_rate end as decimal(20, 10)) as rate,
  cast(coalesce(carried_days between 1 and 5, false) as boolean) as is_carried,
  cast(carried_days as integer) as carried_days,
  case
    when carried_days <= 5
      then at_timezone(with_timezone(cast(rate_date as timestamp(6)) + interval '16' hour, 'Europe/Berlin'), 'UTC')
  end as rate_available_at
from aged

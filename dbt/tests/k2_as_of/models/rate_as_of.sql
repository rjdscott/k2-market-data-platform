-- A daily fixing read as a history: valid for its day from 00:00 UTC, known from its publication.
-- Each candle's rate by valid time and by knowledge time (dbt/macros/k2_as_of.sql).
with history as (
  select
    currency,
    rate_id,
    with_timezone(cast(rate_date as timestamp(6)), 'UTC') as valid_from,
    coalesce(
      lead(with_timezone(cast(rate_date as timestamp(6)), 'UTC')) over (partition by currency order by rate_date),
      timestamp '9999-12-31 23:59:59 UTC'
    ) as valid_to,
    rate_available_at as recorded_at
  from {{ k2_input('ref', 'rates') }}
),

known as (
  select history.*, {{ k2_known_to(['currency']) }} as known_to
  from history
)

select c.candle_id, v.rate_id as valid_rate_id, k.rate_id as known_rate_id
from {{ k2_input('up', 'candles') }} as c
left join history as v on v.currency = c.currency and {{ k2_as_of('c.window_end', 'v', 'valid') }}
left join known as k on k.currency = c.currency and {{ k2_as_of('c.window_end', 'k', 'known') }}

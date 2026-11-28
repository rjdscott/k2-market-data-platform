{{ config(materialized='table') }}
-- A fixture only: the checks read which inputs it reads, never its rows.
select c.venue, c.instrument_id, c.window_start, c.window_end, i.quote_asset as quote_currency,
  cast(c.close / r.rate as decimal(38, 18)) as close_eur, r.rate is null as rate_missing
from {{ k2_input('candles', 'ohlcv_1d') }} as c
left join {{ k2_input('security_master', 'instrument') }} as i
  on i.venue = c.venue and i.instrument_id = c.instrument_id
  and i.valid_from <= c.window_end and c.window_end < i.valid_to
left join {{ k2_input('ecb_fx_rates', 'gold_eur_rates_daily') }} as r
  on r.currency = i.quote_asset and r.rate_date = cast(c.window_start as date)

-- market_analytics.candles.ohlcv_1m without the newest 20 minutes: ADR-035's delayed
-- product, a view, so it needs no schedule of its own and is as current as the 1m mart's
-- last build. A candle is shown once its whole minute, [window_start, window_start + 1
-- minute), ended at least 20 minutes ago, so no trade in it is younger than 20 minutes.
-- The predicate is on window_start alone, so it prunes the table's month partitions.
{{ config(materialized='view') }}

select
    exchange,
    canonical_symbol,
    window_start,
    open_e8,
    high_e8,
    low_e8,
    close_e8,
    volume,
    quote_volume,
    trade_count,
    open_time,
    close_time
from {{ ref('ohlcv_1m') }}
where window_start <= {{ current_timestamp() }} - interval '21' minute

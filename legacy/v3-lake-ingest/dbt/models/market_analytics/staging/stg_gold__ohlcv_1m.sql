-- lake.gold.ohlcv_1m as market_analytics reads it: the columns its marts use, unchanged.
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
from {{ source('gold', 'ohlcv_1m') }}

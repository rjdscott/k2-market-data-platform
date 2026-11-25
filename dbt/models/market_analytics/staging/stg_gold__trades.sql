-- lake.gold.trades as market_analytics reads it: the columns its marts use, unchanged.
select
    exchange,
    canonical_symbol,
    trade_id,
    trade_seq,
    price_e8,
    qty_e8,
    exchange_ts,
    recv_ts_ns,
    seq_gap,
    missing_before
from {{ source('gold', 'trades') }}

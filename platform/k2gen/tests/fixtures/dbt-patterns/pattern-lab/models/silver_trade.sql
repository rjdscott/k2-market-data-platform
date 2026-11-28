-- Synthetic, scripts/verify/dbt-patterns.sh pattern (b): each trade whose symbol resolves, once.
-- Incremental merge on trade_id (the project's strategy, its key the contract's): a build adds
-- the trades not yet here that resolve now, new ones and held ones alike, so a held trade moves
-- in on the first build after its symbol is known, and the merge key keeps it to one row.
-- ponytail: the not-in reads all of silver; bound it by a watermark plus the held keys at volume.
{{ config(materialized='incremental') }}
select
  cast(t.trade_id as bigint) as trade_id,
  cast(t.symbol as varchar) as symbol,
  cast(s.instrument_id as bigint) as instrument_id,
  cast(t.price as decimal(18, 8)) as price,
  cast(t.quantity as decimal(18, 8)) as quantity
from {{ ref('pattern_lab__trades') }} as t
join {{ ref('pattern_lab__symbols') }} as s on s.symbol = t.symbol
{% if is_incremental() %}
where cast(t.trade_id as bigint) not in (select trade_id from {{ this }})
{% endif %}

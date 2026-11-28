-- What this does and why: every venue's trades in one shape. Each venue's silver table is an input
-- port, read through k2_input at the snapshot its own tests passed, so a venue build that failed
-- its tests never reaches here; one union all branch per venue gives the same columns in the same
-- order, the venue a constant, and the venue row's key carried as lineage. The capture's
-- connection columns are not carried (the venue masks them from delayed readers). Every delivery
-- stays a row with the venue's is_duplicate flag; candles excludes the flagged ones.
--
-- Incremental, which venue rows a build takes: every venue row with no row here of the same key
-- and the same flag, an anti-join. A held frame the venue takes back later lies below offsets
-- already here, so a high-water mark per partition would skip it; the anti-join takes it. A row
-- whose flag the venue flipped (the taken-back frame held an earlier delivery of a trade) differs
-- in is_duplicate, the one column the venue rewrites, so it is taken again and the merge on the
-- contract's key updates it. A paused or never audited venue reads as zero rows (whenAbsent
-- empty) and adds nothing.
--
-- load_seq numbers the rows a build writes after the highest number already here: the watermark
-- candles reads, a number that only grows, never a clock. A full refresh (make rebuild) numbers
-- every row after it too, so each is past every watermark candles holds and candles' next build
-- recomputes every window with no step to remember; numbered from 1 again, every row at or below
-- candles' watermark would never be read. The highest number is read as dbt renders this file,
-- before the build replaces the table: the contract's table is made by CREATE OR REPLACE, then
-- INSERT, so a read inside the SQL would find it empty. A dropped table starts from 1, as the
-- products built on it do when a restore drops and rebuilds them with it.
-- ponytail: the anti-join reads every key of every venue's silver and of this table on each
-- build; read only the venue's changes since the snapshot the last build read (an incremental read
-- k2_input would resolve from the run record) when the soak's run times say so.
{{ config(materialized='incremental') }}
{#- `this` is none in a unit test without a `this` input; with one, the test overrides the reads. -#}
{%- set numbered_after = 0 -%}
{%- if execute and not is_incremental() and this is not none and should_full_refresh() and load_relation(this) is not none -%}
  {%- set numbered_after = run_query('select coalesce(max(load_seq), 0) from ' ~ this).rows[0][0] | int -%}
{%- endif %}
with venues as (
  -- one branch per venue input port; a new venue is a port and a union all here
  select
    cast('kraken' as varchar) as venue,
    s.kafka_partition, s.kafka_offset, s.trade_ordinal, s.trade_ts, s.instrument_id, s.venue_symbol,
    s.trade_id, s.side, s.price, s.quantity, s.is_duplicate
  from {{ k2_input('kraken_trades', 'silver_trade') }} as s
),

taken as (
  select v.*
  from venues as v
  {% if is_incremental() %}
  where not exists (
    select 1 from {{ this }} as c
    where c.venue = v.venue
      and c.kafka_partition = v.kafka_partition
      and c.kafka_offset = v.kafka_offset
      and c.trade_ordinal = v.trade_ordinal
      and c.is_duplicate = v.is_duplicate
  )
  {% endif %}
)

select
  venue,
  kafka_partition,
  kafka_offset,
  trade_ordinal,
  trade_ts,
  instrument_id,
  venue_symbol,
  trade_id,
  side,
  price,
  quantity,
  is_duplicate,
  {% if is_incremental() %}(select coalesce(max(load_seq), 0) from {{ this }}) + {% elif numbered_after %}{{ numbered_after }} + {% endif %}row_number() over (
    order by venue, kafka_partition, kafka_offset, trade_ordinal
  ) as load_seq
from taken

{{ config(materialized='incremental', incremental_strategy='append') }}
select id, batch_id, ts, symbol, price, qty from {{ source('spike', 'src_tiny') }}
{% if is_incremental() %}
where id > (select coalesce(max(id), -1) from {{ this }})
{% endif %}

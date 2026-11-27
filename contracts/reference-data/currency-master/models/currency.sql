-- What this does and why: every landed ISO 4217 list adds its rows to bronze, so a code appears
-- once per file that held it. The bronze audit refuses a file that changes a published code's
-- values (values_unchanged), so every bronze row of one code carries the same values, and one
-- DISTINCT gives one row per code. The unique test on code (from the contract's primaryKey) is
-- the guard: if two rows of a code ever differed, the build fails instead of publishing both.
-- A table rebuilt whole on every run: a few hundred rows.
{{ config(materialized='table') }}
select distinct
  code,
  numeric_code,
  name,
  minor_units,
  is_fund
from {{ source('currency_master', 'bronze_currencies') }}

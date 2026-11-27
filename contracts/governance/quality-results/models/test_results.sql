-- Every builder's test results in one union. The union is the compiler's, generated from the
-- release's products: k2_test_results (platform/k2gen/k2gen/emit/dbt.py, model.test_results_writers).
{{ config(materialized='table') }}
{{ k2_test_results() }}

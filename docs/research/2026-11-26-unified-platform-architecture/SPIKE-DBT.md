# Spike: dbt-core on Trino through Lakekeeper (2026-11-26)
dbt-core 1.12.7, dbt-trino 1.12.0 (from the repo's `dbt/uv.lock`), Trino 484, writer `dbt_reference_data`, catalog `reference_data`, scratch schema dropped afterwards. Synthetic data.
1. Incremental `merge` on a unique key: works. Second run logged MERGE; no duplicates; overlapping rows updated.
2. `on_schema_change='append_new_columns'`: works; dbt ran ALTER TABLE ADD COLUMN; old rows null.
3. Enforced model contract: a type mismatch fails before the table changes. Caveat: a contracted table is rebuilt as create-or-replace then a separate insert, two commits; the table was empty for about 0.1 s between them.
4. Snapshots (type-2 history): work with no override; validity columns `timestamp(6)`. The check strategy and the timestamp strategy produce different timestamp types.
5. Publish, test, roll back: works. A failing data test exits 1; `ALTER TABLE ... EXECUTE rollback_to_snapshot(<id>)` as the writer restores the previous rows. Bad rows were visible for 2.6 s in the scripted run. The rollback needs no privilege beyond the writer's (any user can run table procedures today: ADR-044's open gap).
6. One `dbt build --select <model>` with two tests: 3.0 to 3.9 s wall clock via uv; 2.6 s calling dbt directly.
Not tested: snapshot hard deletes, composite or partitioned merge keys, scale, the Airflow and cosmos path.

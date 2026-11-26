# Spike: Trino 484 column masks and row filters on Iceberg through Lakekeeper (2026-11-26)
Throwaway Trino 484 (same image digest) on the compose network, scratch schema in the `governance` catalog plus a `read_only` copy of the same warehouse; everything dropped afterwards. Synthetic data.
- Direct read: mask and row filter apply (same as on a memory catalog).
- Definer view (runs with its owner's rights): the reader's masks and filters do NOT apply; all rows unmasked.
- A rule placed on the view itself applies. An invoker view applies the reader's own rules.
- `allow: false` column: `SELECT *` fails with Access Denied; DESCRIBE hides the column.
- Delay rule (`ts < now - 20 min`): holds under count, joins, `SELECT *`, and a WHERE on newer rows.
- A masked column in WHERE or JOIN sees the mask, so the real value cannot be probed.
- LEAK: Iceberg metadata tables are not covered by table rules. `"t$files"` showed, in its per-file column bounds, the timestamp of rows the delay rule hides and the min and max of the masked column and of the denied column. Not tested: other metadata tables beyond `$files` and `$snapshots`.
- Table procedures: on a catalog with `iceberg.security=read_only`, optimize, rollback_to_snapshot and expire_snapshots are all refused. On the writable catalog a plain reader ran all three; a rollback moved the table's current snapshot and changed what every reader sees. A user who has a row filter is refused, but as a side effect ("not supported for table with row filter"), not as a permission check.
- Rule reload: without `security.refresh-period` a changed rules file is not picked up (old mask still served after 93 s) until restart; with a 5 s refresh period the change was live within 12 s.
Not tested: masks on non-varchar columns; a mask alone versus table procedures; a definer view whose owner has masks; the `lake` catalog itself.

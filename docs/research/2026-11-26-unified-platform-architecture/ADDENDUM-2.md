# Addendum 2 (owner's answers, 2026-11-26). Overrides the brief and addendum 1 where they differ.

F. **Latency.** Scheduled micro-batches now. The design reserves a second run mode (an always-on
   consumer) for a later product that needs seconds, without building it. Show where it would plug in.

G. **The ETL design is now the centre of your entry.** The owner's words: "please help me design
   the ETL process for this. I would like medallion architecture and config driven transforms.
   Please help me work out how to best do this." Design it concretely:
   - The layers a product's data passes through (the repo's medallion is landing/raw, bronze,
     silver, gold: see ADR-026 and `docker/lake/`), what each layer guarantees, and which layers a
     connector fills versus which a transform fills.
   - **Config-driven transforms**: what a user writes in the contract (or beside it) to get from
     landed data to bronze, silver and gold without pipeline code. Be exact about the vocabulary:
     for example select / rename / cast, flatten or explode nested data, parse timestamps, key and
     dedupe, filter, quality flags, type-2 history, incremental merge, aggregate. Say what the
     expression language is (SQL fragments? a fixed set of operations?), which engine runs each
     layer (Spark, or dbt on Trino) and why, how a transform is incremental and idempotent, how
     schema change is handled, how each transform is tested, and how lineage falls out of it.
   - **Where config ends and code begins.** Some logic is real code (the order-book replay and
     Kraken checksum in `docker/lake/books.py`, event bars in `docker/lake/bars.py`). Define the
     escape hatch (a registered, tested, named transform a contract can call) so it does not
     become the coupled pipeline again.
   - **Build versus adopt**, honestly, for this single host inside 25 CPU / 48 GiB with pinned
     versions: at least dbt (already here), Spark SQL driven by config, Kafka Connect / Redpanda
     Connect, dlt, Airbyte, Meltano, SQLMesh. Verify current versions and licences on the web
     before you rely on them; say what you could not verify.
   - Three full worked examples from contract to a queryable gold table: a public API returning
     JSON, a Redpanda trade topic, and a CSV over SFTP. Show every piece of config a user writes.

H. **Crypto migrates onto the framework inside this plan** (this replaces fixed decision 4's
   "regenerate later"). The existing Spark job is split, in steps: generic topic connector for
   landing, venue logic as config-driven transforms or registered code transforms. The existing
   parity and verify gates (`scripts/parity_ohlcv.py`, `make lake-verify`, bars parity) must pass
   at every step; old and new run side by side until they agree. Give the step order, the proof at
   each step, and the rollback. This is the riskiest part of the plan: treat it so.

I. **Captures**: same repository, their own area, outside product contracts.

Length limit rises to 1,200 lines. Section 7 (the new plan) must still be complete.

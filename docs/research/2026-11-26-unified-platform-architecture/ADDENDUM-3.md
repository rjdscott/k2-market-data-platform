# Addendum 3 (owner, 2026-11-26). Final. Overrides earlier text where they differ.

The owner's words: "the connector to Redpanda is one form of ingestion, which will be our crypto
trades, and we will have an ETL pipeline for crypto for each of the venues. Each venue will be its
own product and its own contract, because we will need to define the schema and enable
permissioning and everything else through the catalog and query layer. Another pipeline will be the
end of day FX extraction from the ECB. This too will need to be defined in the ingestion, product,
catalog, and query layer. This is just going to be another form of data set and data product that
we need to map and be able to onboard for our users of the platform."

J. **Two acceptance cases.** Your design must walk each one through all four layers, showing every
   file a user writes and everything generated: (1) one venue's crypto trades from its Redpanda
   topic (do Kraken in full, say what differs for Binance and Coinbase); (2) ECB end-of-day FX
   reference rates (see `docs/plans/2026-11-24-governed-self-serve-platform/004-fx-data-product.md`
   for the source's conventions; its file-source design is input, not a constraint). These replace
   the trade-topic and file examples asked for earlier; keep the SFTP CSV and public JSON API
   examples short.
K. **Product grain.** Each venue's trades is its own product with its own contract (schema, owner,
   permissions). The cross-venue unified trades table and the candles are SEPARATE derived products
   that declare the venue products as inputs, with their own contracts and permissions. Lineage
   shows venue product, then unified trades, then candles.
L. **Pipeline grain.** One generated Airflow pipeline per product: connector, its medallion
   transforms, audits, publish. Products run, fail, pause and are permissioned independently. Show
   that this fits the single host (25 CPU / 48 GiB, ADR-045): how many pipelines, what each costs
   to start (a Spark session per run is expensive: say how you avoid or bound that), how they share
   the table writer lock (`docker/lake/lock.py`), what the scheduler load is.
M. **Permissions.** Granted per product by role, plus column rules and row rules a contract can
   declare (for example hide a column from a role, or serve a role only delayed rows). Say how
   these are generated into the query layer (Trino access control: check what the pinned Trino 484
   file-based rules support for column masks and row filters, and say what you verified), how the
   REST API enforces the same rules, and how the catalog shows who can read what.

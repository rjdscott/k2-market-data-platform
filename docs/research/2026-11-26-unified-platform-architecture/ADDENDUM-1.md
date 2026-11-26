# Addendum 1 to the brief (owner, 2026-11-26, later the same day). It overrides the brief where they differ.

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

The owner's requirement: the crypto captures stay a standalone component of their own. The
Redpanda message bus, or Kafka, is treated as a connector, configured through the unified ingestion
framework, which is Airflow-backed and config-driven. The focus for now is defining products end to
end using public APIs, Kafka or Redpanda, FTP, and files.

What this changes:
A. **The Rust captures are outside the platform's ingestion framework.** They are standalone
   producers that publish to topics. The platform does not generate, configure or lifecycle them
   through product contracts. The boundary between a producer and the platform is the topic and its
   schema. (This replaces the brief's idea that capture "obeys" a product's state. Say what a
   paused product means at that boundary: the platform stops consuming; the producer is its
   owner's business. The existing `book_capture` switch of ADR-050 stays the producer's own.)
B. **Kafka / Redpanda is a connector kind** of the unified ingestion framework, alongside public
   API, FTP / SFTP, and files (HTTP, S3). A product on a topic is declared in its contract
   (brokers, topic, format and schema, where to start, schedule) and ingested by the framework.
C. **The framework is Airflow-backed and config-driven**: connectors are configured from contracts
   via generated config; Airflow schedules and runs them. Say plainly what this implies for
   latency (scheduled micro-batches, not continuous streaming) and whether any connector needs an
   always-on consumer.
D. **Focus now**: defining a product end to end (contract, connector, landing, audit, publish,
   catalog entry, served through SQL) for exactly four connector kinds: public API, Kafka /
   Redpanda, FTP / SFTP, files. Database pulls, internal-lake sources and derived SQL products
   stay in the model but need less depth. The REST data API and the catalog UI are still designed
   and placed in the plan, but the first phases of your plan must deliver these four connectors.
E. **The existing Spark ingest** (`docker/lake/ingest.py`: Redpanda to raw, then bronze, silver,
   gold per venue) is today's hard-coded Kafka connector plus venue-specific product logic in one
   job. Your design must say how those two things are separated over time (what is the generic
   connector, what becomes downstream product logic), still under fixed decision 4: declared now,
   not rewritten now, parity must keep passing.

Your worked examples in section 2 become: a Kafka / Redpanda topic product (use a trade topic),
a scheduled-file product, an FTP / SFTP product, a public-API product, one derived SQL product, and
ClickHouse as a paused serving product. Drop the paused order-book stream example.

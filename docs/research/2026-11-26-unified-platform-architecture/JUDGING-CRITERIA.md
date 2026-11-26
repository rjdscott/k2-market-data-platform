# Judging criteria (owner, 2026-11-26)

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

The owner's requirement: the platform follows current best practice and the latest thinking,
while being robust, easy to manage and easy for stakeholders to use, and its technology is modern
yet tried and tested.

Score every design, and every technology it adds, against these, in this order:
1. **Robust.** Fails loudly, recovers without hand repair, never publishes unaudited data; the
   crypto migration keeps parity at every step and can be rolled back.
2. **Easy to manage.** Few moving parts for one operator on one host; one way to do each thing;
   every state visible in one place; upgrades and pins tractable.
3. **Easy for stakeholders to use.** Onboarding a product is a contract and a pull request;
   finding, understanding, getting access to and querying a product needs no tribal knowledge.
4. **Current best practice.** Data products with contracts, open table format and open catalog
   protocol, declarative pipelines, lineage and access as code, shift-left testing: consistent
   with how well-run data platforms are built today, not a private invention where a standard exists.
5. **Modern yet tried and tested.** For each technology ADDED: first stable release at least about
   two years ago or backed by a major foundation or vendor with wide production use; active
   releases in the last six months; a licence that permits this use; a current stable version to
   pin; and a named fallback. Anything newer than that needs an explicit reason and an exit.
   Prefer what the stack already runs (Airflow 3, Spark, Iceberg, Trino, dbt, Redpanda, Lakekeeper,
   Prometheus, Grafana) over adding a tool, and a boring proven tool over a fashionable one.
   Verify versions, release dates and licences on the web; mark what could not be verified.

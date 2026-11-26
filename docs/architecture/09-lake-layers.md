# 09. Lake layers (retired)

This chapter described the four layers of the v3 lake: raw, every frame verbatim with its Kafka
coordinates; bronze, one table per venue and message in the venue's own vocabulary; silver, typed
and flagged per venue with every delivery kept; and gold, canonical and cross-venue, one row per
logical trade, with the candles, bars, book products and security master built from it. The layers
retired with the v3 lake ingest on 2026-11-26
([ADR-056](../adr/ADR-056-unified-data-product-platform.md), phase 000 of the
[platform plan](../plans/2026-11-26-unified-product-platform/README.md)): their tables were dropped
after a backup, and the chapter is archived unchanged at
[`legacy/v3-lake-ingest/docs/architecture/09-lake-layers.md`](../../legacy/v3-lake-ingest/docs/architecture/09-lake-layers.md),
beside the code that built them under [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/).
[Chapter 20](20-target-data-product-platform.md) is the target that replaces it, phase by phase:
each product keeps only the layers that add a guarantee, in a namespace of its own.

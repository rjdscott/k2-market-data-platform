# Release spine spikes (b), (c), (e): findings, 2026-11-26

Experiments (b), (c) and (e) of "The remaining spikes" in
`docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md` (step N0-4). Each ran from
its `run.sh` in this directory on the build host (Docker Desktop for Linux, linuxkit 6.12.54 VM,
28 CPU), in throwaway containers named `spike-n04plat-*`, on synthetic rows, and cleaned up after
itself; every number below is printed by that script. Images: `trinodb/trino:484`; the live Spark
image, read from the `k2-spark-iceberg` container (`k2-spark-iceberg:3.5.9_1.8.1`, Spark 3.5.9 with
Iceberg 1.8.1 since N0-13; the first (b) runs used `3.5.5_1.8.1`, with the same results);
`k2-airflow:3.3.2`; `quay.io/lakekeeper/catalog:v0.13.9`; `minio/minio:RELEASE.2025-09-07T16-13-09Z`;
`alpine:3.20`.

The findings for (b), (c) and (e) are in [`docs/research/2026-11-26-release-spine-spikes.md`](../../../docs/research/2026-11-26-release-spine-spikes.md).

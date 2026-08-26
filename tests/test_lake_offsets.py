"""
Unit tests for docker/lake/offsets.py — the exactly-once bookkeeping.

Pure python: no Spark, no catalog, no network. Every test here corresponds to a
way the lake could silently lose or duplicate records, so they are written as
"what breaks if this is wrong" rather than as coverage.
"""

import pytest

import offsets as O


class TestEncodeDecode:
    def test_round_trips_with_int_partition_keys(self):
        original = {"market.crypto.v3.raw.kraken": {0: 667850, 11: 193}}
        assert O.decode(O.encode(original)) == original

    def test_encodes_partitions_as_strings(self):
        # Kafka's startingOffsets JSON keys partitions by string. Emitting ints
        # produces valid JSON that Spark's kafka source rejects at plan time.
        assert O.encode({"t": {0: 5}}) == '{"t":{"0":5}}'

    def test_sentinels_survive(self):
        assert O.decode(O.encode({"t": {0: O.EARLIEST}})) == {"t": {0: -2}}


class TestNextStartingOffsets:
    def test_next_start_is_the_committed_end_verbatim(self):
        # Kafka's endingOffsets are exclusive, so the next start is a copy. Any
        # arithmetic here is a duplicate (-1) or a hole (+1) on every cycle.
        committed = {"t": {0: 100, 1: 250}}
        start = O.next_starting_offsets(committed, ["t"], partitions=2)
        assert start == {"t": {0: 100, 1: 250}}

    def test_unseen_partitions_start_at_earliest(self):
        # A partition that has never carried a record produces no row and so no
        # committed offset. For an archive that is never expired, the start of
        # the topic is the correct place to begin.
        start = O.next_starting_offsets({"t": {0: 100}}, ["t"], partitions=3)
        assert start == {"t": {0: 100, 1: O.EARLIEST, 2: O.EARLIEST}}

    def test_a_brand_new_topic_starts_at_earliest_everywhere(self):
        start = O.next_starting_offsets({}, ["a", "b"], partitions=2)
        assert start == {"a": {0: -2, 1: -2}, "b": {0: -2, 1: -2}}

    def test_every_declared_partition_is_present(self):
        # The map must be complete: Spark decides for itself where a partition
        # absent from the JSON begins, and "Spark decides" is not a contract.
        start = O.next_starting_offsets({}, ["t"], partitions=O.DEFAULT_PARTITIONS)
        assert sorted(start["t"]) == list(range(12))


class TestEndOffsets:
    def test_max_offset_becomes_the_exclusive_end(self):
        assert O.end_offsets([("t", 0, 99)]) == {"t": {0: 100}}

    def test_groups_by_topic_and_partition(self):
        rows = [("a", 0, 9), ("a", 1, 4), ("b", 0, 0)]
        assert O.end_offsets(rows) == {"a": {0: 10, 1: 5}, "b": {0: 1}}


class TestMergeCommitted:
    def test_quiet_partitions_are_carried_forward(self):
        # The bug this exists to prevent: partition 1 produced no rows this
        # cycle, so it is absent from `produced`. Dropping it would send the
        # next run back to EARLIEST and re-ingest the whole partition.
        merged = O.merge_committed({"t": {0: 100, 1: 250}}, {"t": {0: 140}})
        assert merged == {"t": {0: 140, 1: 250}}

    def test_new_topics_are_added(self):
        assert O.merge_committed({}, {"t": {0: 1}}) == {"t": {0: 1}}

    def test_previous_is_not_mutated(self):
        previous = {"t": {0: 100}}
        O.merge_committed(previous, {"t": {0: 140}})
        assert previous == {"t": {0: 100}}


class TestLatestSummary:
    INGEST_A = {O.JOB: O.JOB_INGEST, O.KAFKA_OFFSETS: '{"t":{"0":10}}'}
    INGEST_B = {O.JOB: O.JOB_INGEST, O.KAFKA_OFFSETS: '{"t":{"0":20}}'}
    COMPACTION = {"operation": "replace"}

    def test_picks_the_newest_matching_job(self):
        snapshots = [(1, self.INGEST_A), (2, self.INGEST_B)]
        assert O.latest_summary(snapshots, O.JOB_INGEST) is self.INGEST_B

    def test_a_later_compaction_does_not_hide_the_ingest(self):
        # The whole reason the job is stamped as a property. After a nightly
        # rewrite the newest snapshot on raw.messages carries no offsets, and
        # taking it would restart the next ingest from the beginning of time.
        snapshots = [(1, self.INGEST_B), (2, self.COMPACTION)]
        assert O.latest_summary(snapshots, O.JOB_INGEST) is self.INGEST_B

    def test_returns_none_when_no_run_of_that_job_exists(self):
        assert O.latest_summary([(1, self.COMPACTION)], O.JOB_INGEST) is None
        assert O.latest_summary([], O.JOB_INGEST) is None

    def test_ingest_and_decode_do_not_collide(self):
        decode = {O.JOB: O.JOB_DECODE, O.SRC_SNAPSHOT_ID: "77"}
        snapshots = [(1, self.INGEST_B), (2, decode)]
        assert O.latest_summary(snapshots, O.JOB_DECODE) is decode
        assert O.latest_summary(snapshots, O.JOB_INGEST) is self.INGEST_B

    def test_tolerates_an_empty_summary(self):
        assert O.latest_summary([(1, None), (2, {})], O.JOB_INGEST) is None


class TestOffsetGaps:
    def test_a_contiguous_run_passes(self):
        assert O.offset_gaps([("t", 0, 101, 0, 100)]) == []

    def test_a_single_row_partition_passes(self):
        assert O.offset_gaps([("t", 3, 1, 42, 42)]) == []

    def test_missing_records_are_reported_positive(self):
        # 0..100 spans 101 offsets but only 90 rows are present: 11 missing.
        (failure,) = O.offset_gaps([("t", 0, 90, 0, 100)])
        assert failure["scope"] == "t/0"
        assert failure["observed"] == 11
        assert "missing" in failure["detail"]

    def test_duplicated_records_are_reported_negative(self):
        # More rows than the offset span can hold means the same offset was
        # written twice — the exactly-once contract broke, not the topic.
        (failure,) = O.offset_gaps([("t", 0, 105, 0, 100)])
        assert failure["observed"] == -4
        assert "duplicated" in failure["detail"]

    def test_each_failing_partition_gets_its_own_row(self):
        failures = O.offset_gaps([("t", 0, 90, 0, 100), ("t", 1, 5, 0, 4), ("u", 2, 1, 0, 5)])
        assert [f["scope"] for f in failures] == ["t/0", "u/2"]


@pytest.mark.parametrize("partitions", [1, 12, 40])
def test_a_full_cycle_neither_skips_nor_repeats(partitions):
    """One cycle end to end: read, commit ends, resume — no overlap, no hole.

    This is the property the whole module exists for, so it is asserted against
    the real functions in sequence rather than one at a time. `read` below is
    the only stub, standing in for the Kafka source.
    """
    committed = {}
    consumed = {p: [] for p in range(partitions)}
    per_cycle = 7

    for cycle in range(4):
        start = O.next_starting_offsets(committed, ["t"], partitions)
        produced_rows = []
        for partition in range(partitions):
            first = 0 if start["t"][partition] == O.EARLIEST else start["t"][partition]
            offsets = list(range(first, first + per_cycle))
            consumed[partition].extend(offsets)
            produced_rows.append(("t", partition, offsets[-1]))
        committed = O.merge_committed(committed, O.end_offsets(produced_rows))

        expected = per_cycle * (cycle + 1)
        for partition in range(partitions):
            seen = consumed[partition]
            assert seen == list(range(expected)), f"partition {partition} cycle {cycle}"

    # And the continuity audit agrees with the run it just produced.
    rows = [("t", p, len(o), min(o), max(o)) for p, o in consumed.items()]
    assert O.offset_gaps(rows) == []

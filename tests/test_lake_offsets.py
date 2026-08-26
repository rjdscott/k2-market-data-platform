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
        start = O.next_starting_offsets(committed, {"t": 2})
        assert start == {"t": {0: 100, 1: 250}}

    def test_unseen_partitions_start_at_earliest(self):
        # A partition that has never carried a record produces no row and so no
        # committed offset. For an archive that is never expired, the start of
        # the topic is the correct place to begin.
        start = O.next_starting_offsets({"t": {0: 100}}, {"t": 3})
        assert start == {"t": {0: 100, 1: O.EARLIEST, 2: O.EARLIEST}}

    def test_a_brand_new_topic_starts_at_earliest_everywhere(self):
        start = O.next_starting_offsets({}, {"a": 2, "b": 2})
        assert start == {"a": {0: -2, 1: -2}, "b": {0: -2, 1: -2}}

    def test_every_declared_partition_is_present(self):
        # The map must be complete: Spark decides for itself where a partition
        # absent from the JSON begins, and "Spark decides" is not a contract.
        start = O.next_starting_offsets({}, {"t": 12})
        assert sorted(start["t"]) == list(range(12))

    def test_a_short_partition_count_cannot_drop_a_committed_offset(self):
        # The bug that made this a broker lookup instead of a `--partitions`
        # flag. Building the map from `range(count)` alone dropped every
        # committed offset above the count, and Spark then restarted those
        # partitions at EARLIEST — a full silent re-ingest, i.e. duplicates,
        # from one wrong number on a command line.
        start = O.next_starting_offsets({"t": {0: 100, 5: 900}}, {"t": 2})
        assert start == {"t": {0: 100, 1: O.EARLIEST, 5: 900}}


class TestBoundedOffsets:
    """The per-run bound. This is what makes a cold start survivable.

    Before it, run 1 read every partition to `latest` — 41.5 M records / 9.5 GB
    on the live stack — and the driver died with an OutOfMemoryError. The end
    offsets are now decided here, in arithmetic, before Spark reads anything.
    """

    EARLIEST = {"t": {0: 0, 1: 500}}
    LATEST = {"t": {0: 1_000_000, 1: 900}}

    def test_the_bound_caps_the_end_at_start_plus_n(self):
        starts, ends, _ = O.bounded_offsets(
            {"t": {0: 100, 1: 500}}, self.EARLIEST, self.LATEST, 1000
        )
        assert starts == {"t": {0: 100, 1: 500}}
        assert ends == {"t": {0: 1100, 1: 900}}

    def test_zero_means_unbounded(self):
        # The escape hatch, and the pre-bound behaviour: read to latest.
        _, ends, backlog = O.bounded_offsets({"t": {0: 100, 1: 500}}, self.EARLIEST, self.LATEST, 0)
        assert ends == self.LATEST
        assert backlog == {"t": 0}

    def test_the_earliest_sentinel_is_resolved_before_the_arithmetic(self):
        # The bug this test exists for: EARLIEST is -2, and -2 + 1000 = 998 is
        # an offset, not a bound. On a partition whose log starts at 500 that
        # end is BELOW the start, and the run either reads nothing forever or
        # commits an end that silently skips the first 500 records.
        starts, ends, _ = O.bounded_offsets(
            {"t": {0: O.EARLIEST, 1: O.EARLIEST}}, self.EARLIEST, self.LATEST, 1000
        )
        assert starts == {"t": {0: 0, 1: 500}}
        assert ends == {"t": {0: 1000, 1: 900}}

    def test_the_end_never_runs_past_latest(self):
        _, ends, backlog = O.bounded_offsets(
            {"t": {0: 999_500, 1: 500}}, self.EARLIEST, self.LATEST, 1000
        )
        assert ends == {"t": {0: 1_000_000, 1: 900}}
        assert backlog == {"t": 0}

    def test_backlog_is_what_this_run_leaves_behind(self):
        _, _, backlog = O.bounded_offsets({"t": {0: 100, 1: 500}}, self.EARLIEST, self.LATEST, 1000)
        # partition 0: 1_000_000 - 1100 left; partition 1 drained.
        assert backlog == {"t": 998_900}

    def test_an_end_can_never_rewind_below_the_start(self):
        # Committed offsets past `latest` mean the topic was recreated under the
        # lake (ADR-022 "Risks"). An end below the start would be a negative
        # range Spark rejects, and a negative backlog on the gauge; the honest
        # answer is an empty read, which `failOnDataLoss` then reports.
        starts, ends, backlog = O.bounded_offsets(
            {"t": {0: 2_000_000}}, self.EARLIEST, self.LATEST, 1000
        )
        assert ends == starts
        assert backlog == {"t": 0}

    def test_caught_up_makes_starts_and_ends_equal(self):
        # How `stage_raw` decides "no new records" without touching Kafka.
        starts, ends, _ = O.bounded_offsets(
            {"t": {0: 1_000_000, 1: 900}}, self.EARLIEST, self.LATEST, 1000
        )
        assert starts == ends

    def test_until_bounds_the_end_and_stays_in_the_backlog(self):
        # `--end-timestamp`, resolved to offsets by the broker rather than
        # handed to Spark as `endingTimestamp`. Records past the instant are
        # still a backlog, not a hole.
        _, ends, backlog = O.bounded_offsets(
            {"t": {0: 100, 1: 500}}, self.EARLIEST, self.LATEST, 0, until={"t": {0: 400, 1: 700}}
        )
        assert ends == {"t": {0: 400, 1: 700}}
        assert backlog == {"t": (1_000_000 - 400) + (900 - 700)}

    def test_until_minus_one_means_no_record_at_or_after_the_instant(self):
        # Kafka's answer for "nothing at or after that timestamp" is -1, and it
        # means the whole partition is older than the bound — read all of it.
        _, ends, _ = O.bounded_offsets(
            {"t": {0: 100, 1: 500}}, self.EARLIEST, self.LATEST, 0, until={"t": {0: -1, 1: -1}}
        )
        assert ends == self.LATEST

    def test_the_bound_still_applies_under_until(self):
        _, ends, _ = O.bounded_offsets(
            {"t": {0: 100}}, self.EARLIEST, self.LATEST, 50, until={"t": {0: 400}}
        )
        assert ends == {"t": {0: 150}}


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
def test_a_bounded_cold_start_drains_without_skipping_or_repeating(partitions):
    """A backlog, drained over successive runs: no overlap, no hole, ends at 0.

    This is the property the whole module exists for, so it is asserted against
    the real functions in sequence rather than one at a time. The broker is the
    only stub — a topic holding `total` records per partition, none expiring.
    """
    total, per_run = 100, 7
    earliest = {"t": {p: 0 for p in range(partitions)}}
    latest = {"t": {p: total for p in range(partitions)}}

    committed, consumed, runs = {}, {p: [] for p in range(partitions)}, 0
    while True:
        starting = O.next_starting_offsets(committed, {"t": partitions})
        starts, ends, backlog = O.bounded_offsets(starting, earliest, latest, per_run)
        if starts == ends:  # what stage_raw calls "no new records"
            break
        runs += 1
        for partition in range(partitions):
            consumed[partition].extend(range(starts["t"][partition], ends["t"][partition]))
        committed = O.merge_committed(committed, ends)
        # The gauge has to fall monotonically, or "draining" is not what it says.
        assert backlog["t"] == partitions * (total - min(runs * per_run, total))
        assert runs <= total, "the bound is not advancing — this would loop forever"

    assert backlog["t"] == 0
    for partition in range(partitions):
        assert consumed[partition] == list(range(total)), f"partition {partition}"

    # And the continuity audit agrees with the run sequence it just produced.
    rows = [("t", p, len(o), min(o), max(o)) for p, o in consumed.items()]
    assert O.offset_gaps(rows) == []

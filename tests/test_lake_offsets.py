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


class TestEvicted:
    """Retention overtaking the archive — ADR-022's "topic truncated below the
    stored offset" row, detected before the read instead of as a Kafka stack
    trace 384 lines into a Spark job.

    Real, on 2026-08-26: a cold start drained `market.crypto.v3.raw.kraken`
    partition 0 at 50,000 offsets per run while the 512 MiB-per-partition cap
    evicted faster, and the committed offset ended up below LOG-START.
    """

    def test_a_start_below_the_log_start_is_reported(self):
        (loss,) = O.evicted({"t": {0: 1_615_463}}, {"t": {0: 2_784_417}})
        assert loss == ("t", 0, 1_615_463, 2_784_417, 1_168_954)

    def test_a_start_at_the_log_start_is_fine(self):
        assert O.evicted({"t": {0: 500}}, {"t": {0: 500}}) == []

    def test_a_start_above_the_log_start_is_fine(self):
        assert O.evicted({"t": {0: 900}}, {"t": {0: 500}}) == []

    def test_an_empty_partition_is_not_a_loss(self):
        # earliest == latest == 0 on a partition nothing ever produced to.
        assert O.evicted({"t": {0: 0}}, {"t": {0: 0}}) == []

    def test_every_affected_partition_is_named(self):
        # The runbook's step 1 is "establish exactly what was lost", per
        # partition. Reporting only the first one Spark happened to fetch makes
        # that a manual sweep.
        losses = O.evicted(
            {"t": {0: 10, 1: 900, 2: 20}}, {"t": {0: 100, 1: 500, 2: 200}}
        )
        assert [(topic, p, n) for topic, p, _, _, n in losses] == [("t", 0, 90), ("t", 2, 180)]


class TestSkipEvicted:
    """`--accept-data-loss`, as the pure decision it is made of.

    Live on 2026-08-26: `market.crypto.v3.raw.kraken/0` committed 1,615,463
    against a broker LOG-START of 2,784,417, so every cron run failed at plan
    time. The repair resumes that ONE partition at the log start and leaves the
    other eleven where they were committed.
    """

    STARTS = {"t": {0: 1_615_463, 1: 152_000, 2: 2_674}}
    EARLIEST = {"t": {0: 2_784_417, 1: 62_436, 2: 0}}

    def repaired(self):
        return O.skip_evicted(self.STARTS, O.evicted(self.STARTS, self.EARLIEST))

    def test_an_evicted_partition_resumes_at_the_log_start(self):
        assert self.repaired()["t"][0] == 2_784_417

    def test_healthy_partitions_are_untouched(self):
        # The failure this guards: a repair aimed at partition 0 that also moves
        # its neighbours is a hole (forward) or a duplicate (backward) created by
        # the fix rather than by the fault. Partition 1 sits ABOVE its log start
        # and must stay exactly where it was committed.
        assert self.repaired()["t"][1] == 152_000
        assert self.repaired()["t"][2] == 2_674

    def test_no_losses_is_the_identity(self):
        assert O.skip_evicted(self.STARTS, []) == self.STARTS

    def test_the_input_is_not_mutated(self):
        self.repaired()
        assert self.STARTS["t"][0] == 1_615_463

    def test_the_repaired_start_is_no_longer_a_loss(self):
        # What lets the run proceed: re-running the detector over the repaired
        # map comes back empty, so the ingest does not fail on the partition it
        # has just repaired.
        assert O.evicted(self.repaired(), self.EARLIEST) == []

    def test_rebounding_gives_a_forward_range(self):
        # Why the repaired map goes back through `bounded_offsets` rather than
        # being patched into the `starts` it already produced: that end was
        # computed from the OLD start (1,615,463 + 200,000) and is BELOW the new
        # one. Reusing it would hand Spark a negative range.
        latest = {"t": {0: 7_672_112, 1: 980_135, 2: 2_802}}
        starts, ends, backlog = O.bounded_offsets(self.repaired(), self.EARLIEST, latest, 200_000)
        assert starts["t"][0] == 2_784_417
        assert ends["t"][0] == 2_984_417
        assert backlog["t"] > 0

    def test_every_evicted_partition_moves_and_only_those(self):
        starts = {"t": {0: 10, 1: 900, 2: 20}}
        earliest = {"t": {0: 100, 1: 500, 2: 200}}
        assert O.skip_evicted(starts, O.evicted(starts, earliest)) == {
            "t": {0: 100, 1: 900, 2: 200}
        }


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


# The row ingest.py filed at 21:48:59Z on 2026-08-26, verbatim — the one
# recorded gap this netting exists for (docs/runbooks/lake-ingest-lag.md §3).
KRAKEN_GAP_ROW = (
    "market.crypto.v3.raw.kraken/0",
    "--accept-data-loss: market.crypto.v3.raw.kraken partition 0 committed 1615463, "
    "broker LOG-START 2784417, 1168954 records evicted by Redpanda retention and "
    "permanently gone; resumed at 2784417 by run local-1787780940821",
)


class TestRecordedGaps:
    def test_parses_the_row_the_repair_actually_wrote(self):
        # log_start 2784417 is the first offset that SURVIVED, so the
        # acknowledged hole ends at 2784416 — off by one here and the netting
        # leaves a single-offset remainder that fails the audit forever.
        assert O.recorded_gaps([KRAKEN_GAP_ROW]) == [
            ("market.crypto.v3.raw.kraken", 0, 1_615_463, 2_784_416)
        ]

    def test_the_format_ingest_writes_is_the_format_this_reads(self):
        # ingest._accept_data_loss builds its detail from GAP_OFFSETS. If the
        # two ever drift, netting silently stops and a critical alert relights.
        detail = "--accept-data-loss: " + O.GAP_OFFSETS.format(committed=10, log_start=20)
        assert O.recorded_gaps([("t/3", detail)]) == [("t", 3, 10, 19)]

    def test_an_unparseable_row_is_skipped_not_raised(self):
        # A hand-filed row in some other wording must degrade to "not covered"
        # — which fails the audit — never to a check that raises.
        assert O.recorded_gaps([("t/0", "operator: we lost some records"), ("t/0", None)]) == []

    def test_a_scope_without_a_partition_is_skipped(self):
        assert O.recorded_gaps([("lake.raw.messages", KRAKEN_GAP_ROW[1])]) == []


class TestUncoveredHoles:
    """Which observed holes a recorded `offset_gap` accounts for.

    A hole that is exactly acknowledged is not news; anything else is, and the
    audit has to keep failing on it. Getting this backwards either relights an
    alert nobody can act on or hides real loss behind an old incident.
    """

    KRAKEN_HOLE = ("market.crypto.v3.raw.kraken", 0, 1_615_463, 2_784_416)

    def test_the_live_incident_is_netted_out(self):
        assert O.uncovered_holes([self.KRAKEN_HOLE], O.recorded_gaps([KRAKEN_GAP_ROW])) == []

    def test_nothing_recorded_covers_nothing(self):
        assert O.uncovered_holes([self.KRAKEN_HOLE], []) == [self.KRAKEN_HOLE]

    @pytest.mark.parametrize("hole", [("t", 0, 99, 200), ("t", 0, 100, 201), ("t", 0, 99, 201)])
    def test_a_hole_wider_than_the_record_still_fails(self, hole):
        # Partial coverage is not coverage: the offsets outside the recorded
        # range are records nobody wrote down.
        assert O.uncovered_holes([hole], [("t", 0, 100, 200)]) == [hole]

    def test_a_hole_inside_the_record_is_covered(self):
        assert O.uncovered_holes([("t", 0, 120, 180)], [("t", 0, 100, 200)]) == []

    def test_two_abutting_records_cover_one_merged_hole(self):
        # Two evictions with no successful ingest between them leave one hole
        # in the data and two rows in audit.checks. Neither row contains it.
        gaps = [("t", 0, 100, 200), ("t", 0, 201, 300)]
        assert O.uncovered_holes([("t", 0, 100, 300)], gaps) == []
        assert O.uncovered_holes([("t", 0, 100, 301)], gaps) == [("t", 0, 100, 301)]

    def test_records_do_not_leak_across_partitions_or_topics(self):
        holes = [("t", 1, 100, 200), ("u", 0, 100, 200)]
        assert O.uncovered_holes(holes, [("t", 0, 100, 200)]) == holes

    def test_only_the_uncovered_holes_come_back(self):
        holes = [("t", 0, 100, 200), ("t", 0, 400, 500)]
        assert O.uncovered_holes(holes, [("t", 0, 100, 200)]) == [("t", 0, 400, 500)]


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


def test_ack_window_parses_the_runbook_format_and_nothing_else():
    import offsets as O

    assert O.ack_window("from 2026-08-26T16:00:00Z to 2026-08-26T18:00:00Z: chaos runs, 31,464 records dropped") == (
        "2026-08-26 16:00:00",
        "2026-08-26 18:00:00",
    )
    assert O.ack_window("from 2026-08-26T16:00:00Z to 2026-08-26T18:00:00Z") == ("2026-08-26 16:00:00", "2026-08-26 18:00:00")
    assert O.ack_window("acknowledged, see ticket") is None
    assert O.ack_window(None) is None

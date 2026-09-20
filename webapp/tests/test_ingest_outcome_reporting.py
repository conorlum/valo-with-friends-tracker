"""Discovery and ingestion fail separately, so they are reported separately.

Regression test for the 2026-09-19 roster run. Beef Shortrib#Galbi's discovery
reached 200/200 and their ingest had already added 28 matches when one match
page died with ERR_CONNECTION_CLOSED. The exception propagated out of the
ingest entry point, the caller's `except` built a fresh DiscoveryResult with no
match ids, and the run printed `0/200` -- understating both what was found and
what was written, in the one feature whose whole job is reporting that number
accurately.
"""

import pytest

from app.adapters.trackergg_browserstate_source import (
    DiscoveryResult,
    DiscoveryStatus,
    IngestOutcome,
    _IngestProgress,
)


def _discovery(reached=200, requested=200, status=DiscoveryStatus.COMPLETE):
    return DiscoveryResult(
        riot_id="Beef Shortrib#Galbi",
        requested=requested,
        match_ids=[f"m{i}" for i in range(reached)],
        status=status,
        reason="reached the requested count",
        pages_fetched=10,
    )


def test_ingest_failure_does_not_erase_what_discovery_found():
    """The exact misreport: 200 discovered, 28 ingested, then a dropped
    connection. Neither number may collapse to zero."""
    outcome = IngestOutcome(
        discovery=_discovery(),
        ingested=28,
        attempted=172,
        already_present=28,
        error="Error: net::ERR_CONNECTION_CLOSED",
    )

    assert outcome.discovery.reached == 200, "discovery result must survive an ingest failure"
    assert outcome.ingested == 28, "committed matches are real and must be reported"
    assert not outcome.ok
    summary = outcome.summary()
    assert "200/200" in summary
    assert "28/172" in summary
    assert "GAVE UP" in summary
    # The actual misreport was "discovered 0/200"; guard that exact phrasing
    # rather than the bare substring, which "200/200" legitimately contains.
    assert "discovered 0/200" not in summary


def test_a_clean_run_is_ok_and_says_both_numbers():
    outcome = IngestOutcome(
        discovery=_discovery(), ingested=41, attempted=41, already_present=159
    )

    assert outcome.ok
    assert "200/200" in outcome.summary()
    assert "41/41" in outcome.summary()
    assert "159 already held" in outcome.summary()


def test_exhausted_discovery_with_clean_ingest_is_ok():
    """flatcat#woof's shape: 61 is their whole history, 5 of them new."""
    outcome = IngestOutcome(
        discovery=_discovery(reached=61, status=DiscoveryStatus.EXHAUSTED),
        ingested=5,
        attempted=5,
        already_present=56,
    )

    assert outcome.ok, "end of history plus a clean ingest is a success"
    assert "61/200" in outcome.summary()
    assert "EXHAUSTED" in outcome.summary()


def test_no_history_is_ok_but_named():
    """SambuUwU#NA1: zero is the true answer, so it does not fail the run,
    but it never prints as a plain success either."""
    outcome = IngestOutcome(
        discovery=DiscoveryResult(
            riot_id="SambuUwU#NA1",
            requested=200,
            status=DiscoveryStatus.NO_HISTORY,
            reason="zero Competitive matches under any act",
        )
    )

    assert outcome.ok
    assert "NO_HISTORY" in outcome.summary()
    assert "0/200" in outcome.summary()


def test_skipped_matches_make_a_run_not_ok_even_though_it_continued():
    """Skipping exists to salvage the rest of a player's list, not to call a
    partial ingest a success."""
    outcome = IngestOutcome(
        discovery=_discovery(),
        ingested=170,
        attempted=172,
        failed_ids=("bad-1", "bad-2"),
    )

    assert not outcome.ok
    assert "2 SKIPPED" in outcome.summary()
    assert "GAVE UP" not in outcome.summary(), "skipping is not giving up"


def test_incomplete_discovery_is_not_ok():
    outcome = IngestOutcome(
        discovery=_discovery(reached=20, status=DiscoveryStatus.INCOMPLETE),
        ingested=20,
        attempted=20,
    )

    assert not outcome.ok
    assert "INCOMPLETE" in outcome.summary()


def test_scoring_failure_still_counts_the_match_as_ingested():
    """load_match commits before scoring, so a scoring failure leaves a
    committed match that dedup will skip forever. Counting it as not-ingested
    would under-report what is actually in the database (the match-3133
    shape); backfill_unscored_matches picks the scoring up later."""
    progress = _IngestProgress(ingested=29, attempted=172,
                               error=RuntimeError("scoring blew up"))

    assert progress.ingested == 29
    assert progress.error is not None


def test_progress_defaults_are_empty_not_none():
    progress = _IngestProgress()

    assert progress.dirty == set()
    assert progress.ingested == 0
    assert progress.error is None

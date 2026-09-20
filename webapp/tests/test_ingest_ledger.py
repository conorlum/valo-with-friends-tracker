"""A run has to be reversible, which means the record of what it added has to
survive the run dying halfway through.

The ledger is written per match and flushed, not assembled in memory and
dumped at the end, because the case where it matters most -- a crashed or
killed run -- is exactly the case where an end-of-run dump writes nothing.
"""

import json

from app.adapters.trackergg_browserstate_source import IngestLedger


class FakeMatch:
    def __init__(self, id, external_id, map_name="Haven", played_at=None):
        self.id = id
        self.external_id = external_id
        self.map_name = map_name
        self.played_at = played_at


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ledger_records_each_match_as_it_is_ingested(tmp_path):
    path = tmp_path / "run.jsonl"
    ledger = IngestLedger(path, run_label="test")

    ledger.record_match(FakeMatch(1, "ext-a"), "A#1")
    ledger.record_match(FakeMatch(2, "ext-b", map_name="Abyss"), "B#2")
    ledger.record_end("done")

    records = read(path)
    assert [r["event"] for r in records] == ["run_start", "ingested", "ingested", "run_end"]
    assert [r["external_id"] for r in records if r["event"] == "ingested"] == ["ext-a", "ext-b"]
    assert [r["match_id"] for r in records if r["event"] == "ingested"] == [1, 2]
    assert records[2]["map_name"] == "Abyss"
    assert records[2]["discovered_via"] == "B#2"
    assert records[-1]["ingested"] == 2
    assert ledger.count == 2


def test_each_line_is_on_disk_before_the_run_ends(tmp_path):
    """The crash case: whatever was ingested is readable even though
    record_end() never ran."""
    path = tmp_path / "run.jsonl"
    ledger = IngestLedger(path, run_label="test")

    ledger.record_match(FakeMatch(7, "ext-mid"), "A#1")
    # No record_end() -- simulating a killed run.

    records = read(path)
    ingested = [r for r in records if r["event"] == "ingested"]
    assert len(ingested) == 1
    assert ingested[0]["external_id"] == "ext-mid"
    assert not any(r["event"] == "run_end" for r in records)


def test_ledger_creates_its_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "run.jsonl"

    IngestLedger(path, run_label="test")

    assert path.exists()


def test_run_label_is_recorded(tmp_path):
    path = tmp_path / "run.jsonl"

    IngestLedger(path, run_label="refresh_tracked_players --count 200")

    assert read(path)[0]["label"] == "refresh_tracked_players --count 200"


def test_appending_to_an_existing_ledger_keeps_prior_lines(tmp_path):
    path = tmp_path / "run.jsonl"
    first = IngestLedger(path, run_label="one")
    first.record_match(FakeMatch(1, "ext-a"), "A#1")

    second = IngestLedger(path, run_label="two")
    second.record_match(FakeMatch(2, "ext-b"), "B#2")

    externals = [r["external_id"] for r in read(path) if r["event"] == "ingested"]
    assert externals == ["ext-a", "ext-b"], "a ledger is append-only, never truncated"


def test_played_at_is_serialised_when_present(tmp_path):
    from datetime import datetime, timezone

    path = tmp_path / "run.jsonl"
    ledger = IngestLedger(path, run_label="test")

    ledger.record_match(
        FakeMatch(1, "ext-a", played_at=datetime(2026, 9, 19, 3, 7, tzinfo=timezone.utc)), "A#1"
    )

    row = [r for r in read(path) if r["event"] == "ingested"][0]
    assert row["played_at"].startswith("2026-09-19T03:07")


def test_missing_played_at_is_null_not_a_crash(tmp_path):
    path = tmp_path / "run.jsonl"
    ledger = IngestLedger(path, run_label="test")

    ledger.record_match(FakeMatch(1, "ext-a", played_at=None), "A#1")

    assert [r for r in read(path) if r["event"] == "ingested"][0]["played_at"] is None

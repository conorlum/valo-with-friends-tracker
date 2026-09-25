"""The fixture maker's allowlist and identity scan, and a scan of every committed fixture."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import make_replay_fixture as mrf  # noqa: E402
from replay_synthetic import MATCH_UUID, SyntheticMatch, subject  # noqa: E402

from app.replays.condense import condense_export_dir  # noqa: E402

FIXTURES = HERE.parent / "fixtures" / "replay"
# Realistic-looking identities the fixture must never contain.
REAL_SUBJECTS = [f"3f2a{i:04x}-9c1d-4e6b-a7f0-{i:04x}5b8c2d1e" for i in range(10)]
REAL_MATCH = "d1e2f3a4-b5c6-4d7e-8f90-a1b2c3d4e5f6"


def realistic_export(tmp_path, plant=None):
    match = SyntheticMatch(manifest_extra={"source_file": f"{REAL_MATCH}.vrf"})
    text = "\n".join(json.dumps(e) for e in match.events())
    for i, real in enumerate(REAL_SUBJECTS):
        text = text.replace(subject(i), real)
    text = text.replace(MATCH_UUID.upper(), REAL_MATCH.upper())
    events = [json.loads(line) for line in text.splitlines()]
    if plant:
        plant(events)
    directory = match.write(tmp_path / "export", events=events)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["diagnostics"] = [{"code": "partial_sequence_error", "message": r"C:\Users\someone\x.vrf"}]
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def test_a_fixture_keeps_the_structure_and_drops_every_identity(tmp_path):
    out = tmp_path / "fixture"
    listing = mrf.make_fixture(realistic_export(tmp_path), out, rounds=2)
    assert [line.split(":")[0] for line in listing] == ["events.ndjson", "manifest.json", "movement.ndjson"]
    text = "".join(p.read_text() for p in out.iterdir())
    for real in [*REAL_SUBJECTS, REAL_MATCH]:
        assert real not in text.lower()
    assert "Users" not in text
    assert mrf.scan_dir(out, {s.lower() for s in REAL_SUBJECTS}) == []

    fixture = condense_export_dir(out, source_sha256=None)
    original = condense_export_dir(realistic_export(tmp_path / "again"), source_sha256=None)
    assert fixture.round_count == 2
    assert fixture.match_uuid == mrf.SYNTHETIC_MATCH
    assert [p["agent"] for p in fixture.players] == [p["agent"] for p in original.players]
    assert [p["side_group"] for p in fixture.players] == [p["side_group"] for p in original.players]
    assert fixture.rounds[1] == original.rounds[1], "the kept rounds condense identically"
    assert all(p["subject"].startswith(mrf.SYNTHETIC_PREFIX) for p in fixture.players)


def test_a_planted_identity_in_an_allowlisted_field_is_caught_and_the_output_deleted(tmp_path):
    def plant(events):
        for e in events:
            if "RoundResults" in (e.get("payload") or {}):
                e["payload"]["RoundResults"][0]["WinningTeam"] = REAL_SUBJECTS[3]
                break

    out = tmp_path / "fixture"
    with pytest.raises(mrf.FixtureRefused, match="identity scan failed"):
        mrf.make_fixture(realistic_export(tmp_path, plant), out, rounds=2)
    assert not out.exists()


def test_too_few_rounds_refuse(tmp_path):
    with pytest.raises(mrf.FixtureRefused):
        mrf.make_fixture(realistic_export(tmp_path), tmp_path / "fixture", rounds=9)


def test_an_existing_output_is_never_overwritten(tmp_path):
    out = tmp_path / "fixture"
    out.mkdir()
    with pytest.raises(mrf.FixtureRefused, match="exists"):
        mrf.make_fixture(realistic_export(tmp_path), out, rounds=1)


@pytest.mark.parametrize("planted", [
    "3f2a0001-9c1d-4e6b-a7f0-00015b8c2d1e",
    r"C:\\Users\\someone\\ValorantReplayArchive",
    r"C:\Users\someone",
    "C:/Users/someone",
    "%USERPROFILE%\\rp",
    "SomePlayer#NA1",
])
def test_the_scanner_catches_planted_identities(planted):
    assert mrf.scan_text(json.dumps({"k": 1, "x": planted}))


def test_the_scanner_passes_synthetic_rows():
    row = {"Subject": mrf.synthetic_subject(3), "MatchID": mrf.SYNTHETIC_MATCH,
           "archetype_path": "/Game/Characters/Wushu/Character", "position": {"x": 1.5}}
    assert mrf.scan_text(json.dumps(row)) == []


def test_every_committed_fixture_is_identity_free():
    if not FIXTURES.exists():
        pytest.skip("no replay fixtures committed yet (the Swiftplay export is pending)")
    assert mrf.scan_dir(FIXTURES) == []

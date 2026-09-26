"""The fixture maker's allowlist and identity scan, and a scan of every committed fixture."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import make_replay_fixture as mrf  # noqa: E402
from replay_synthetic import MATCH_UUID, SyntheticMatch, subject, vrf_header  # noqa: E402

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


def _round_results_rows(out):
    rows = [json.loads(line) for line in (out / "events.ndjson").read_text().splitlines()]
    return [r["payload"]["RoundResults"] for r in rows if "RoundResults" in (r.get("payload") or {})]


def test_a_planted_identity_in_a_round_results_field_is_replaced_never_copied(tmp_path):
    # Pass 6 (P-h): a value that fails its shape makes the RoundResults opaque, and an opaque
    # RoundResults becomes the synthetic marker. Pass 5 copied it and relied on the scanner.
    def plant(events):
        for e in events:
            if "RoundResults" in (e.get("payload") or {}):
                e["payload"]["RoundResults"][0]["WinningTeam"] = REAL_SUBJECTS[3]
                break

    out = tmp_path / "fixture"
    mrf.make_fixture(realistic_export(tmp_path, plant), out, rounds=2)
    text = "".join(p.read_text() for p in out.iterdir())
    assert REAL_SUBJECTS[3] not in text.lower()
    assert mrf.FALLBACK_MARKER in _round_results_rows(out)
    assert mrf.scan_dir(out, {s.lower() for s in REAL_SUBJECTS}) == []


def test_an_identity_hidden_in_an_opaque_fallback_payload_is_replaced(tmp_path):
    # The pass-5 review probe: a raw fallback dict with an encoded identity inside.
    def plant(events):
        for e in events:
            if "RoundResults" in (e.get("payload") or {}):
                e["payload"]["RoundResults"] = {"raw": "M2YyYTAwMDMtOWMxZC00ZTZiLWE3ZjAtMDAwMzViOGMyZDFl",
                                                "bits": 184}
                break

    out = tmp_path / "fixture"
    mrf.make_fixture(realistic_export(tmp_path, plant), out, rounds=2)
    text = "".join(p.read_text() for p in out.iterdir())
    assert "M2YyYTAw" not in text and "raw" not in text
    assert mrf.FALLBACK_MARKER in _round_results_rows(out)


def test_base64_in_an_allowlisted_field_outside_round_results_refuses(tmp_path):
    def plant(events):
        events.append({"type": "actor_closed", "time_ms": 1, "actor_net_guid": 1000 + 20 + 3, "channel": 13,
                       "reason": "U29tZVBsYXllciNOQTE="})

    out = tmp_path / "fixture"
    with pytest.raises(mrf.FixtureRefused, match="shape"):
        mrf.make_fixture(realistic_export(tmp_path, plant), out, rounds=2)
    assert not out.exists()


def test_the_shape_scan_catches_a_nested_unknown_key_and_an_opaque_string(tmp_path):
    out = tmp_path / "fixture"
    mrf.make_fixture(realistic_export(tmp_path), out, rounds=1)
    assert mrf.scan_dir(out) == []
    events = out / "events.ndjson"
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    group = next(r for r in rows if r["type"] == "export_group_received")
    group["payload"]["Hidden"] = {"nested": 1}
    rows.append({"type": "actor_closed", "time_ms": 5, "actor_net_guid": 7, "reason": "QWxpY2UjTkEx"})
    events.write_text("".join(json.dumps(r) + "\n" for r in rows))
    hits = mrf.scan_dir(out)
    assert any("Hidden: unexpected key" in hit for hit in hits)
    assert any(".reason: unexpected value shape" in hit for hit in hits)


def test_every_committed_fixture_passes_the_shape_check():
    if not FIXTURES.exists():
        pytest.skip("no replay fixtures committed")
    for manifest in FIXTURES.rglob("manifest.json"):
        assert mrf.shape_hits(manifest.parent) == [], manifest.parent.name


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


def test_a_swiftplay_shape_fixture_takes_the_map_from_the_vrf_and_thins_movement(tmp_path):
    vrf_bytes = vrf_header(REAL_MATCH) + f"/Game/Maps/Ascent/Ascent {REAL_MATCH}".encode("ascii")
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"source_file": f"{REAL_MATCH}.vrf"}, vrf_bytes=vrf_bytes)
    directory = match.write(tmp_path / "export")
    vrf = match.write_vrf(tmp_path / f"{REAL_MATCH}.vrf")
    out = tmp_path / "fixture"
    mrf.make_fixture(directory, out, rounds=2, vrf=vrf, movement_step=4)
    assert mrf.scan_dir(out, {REAL_MATCH}) == []
    fixture = condense_export_dir(out, source_sha256=None)
    assert fixture.map_name == "Ascent" and fixture.round_count == 2
    assert fixture.match_uuid == mrf.SYNTHETIC_MATCH
    assert fixture.hz == 4, "every 4th row of 16 Hz"
    assert [p["agent"] for p in fixture.players] == [p["agent"] for p in condense_export_dir(
        directory, source_sha256=None, vrf_path=vrf).players]

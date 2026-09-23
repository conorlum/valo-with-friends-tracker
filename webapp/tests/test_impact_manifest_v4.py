"""The manifest side of Impact v4 (plan 2026-09-21-impact-v4, section 2.4).

- The v4 flags are serialised ONLY WHEN TRUE, so rc3's frozen comparator dict
  is reproduced byte for byte, and a v4 manifest that omits either key fails
  the exact comparison against the declared comparator.
- The manifest's formula description is generated from the release
  comparator's flags, never frozen as "unchanged legacy time factor" for v4.
- The source fingerprint's contract is v2: it covers the assistants payload
  and the match's players (R1), and player.py joins HASHED_SOURCES.
"""
import json
from pathlib import Path

import pytest

from app.models import KillEvent, MatchPlayer, Player
from app.scoring import impact, impact_manifest
from app.scoring.impact_manifest import (
    COMPARATORS,
    RC3,
    SOURCE_FINGERPRINT_VERSION,
    V4,
    V4_N,
    ManifestMismatchError,
    build_manifest,
    config_from_dict,
    config_to_dict,
    match_source_fingerprint,
    verify_manifest,
    verify_source_snapshots,
)
from tests.buy_disruption_fixtures import build_match, session

RC3_MANIFEST = Path(__file__).resolve().parents[2] / "docs/superpowers/impact-rc3/candidate-manifest.json"


def _manifest(release_comparator=V4, **overrides):
    kwargs = dict(candidate_id="test-v4", created="2026-09-21", scorer_revision="deadbeef",
                  activation_impact_calculation_version=4, source_snapshots={"matches": {}},
                  release_comparator=release_comparator)
    kwargs.update(overrides)
    return build_manifest(**kwargs)


# -- serialisation -----------------------------------------------------------------------

def test_rc3_serialises_exactly_as_it_was_frozen():
    frozen = json.loads(RC3_MANIFEST.read_text(encoding="utf-8"))["comparators"][RC3]
    assert config_to_dict(COMPARATORS[RC3]) == frozen


def test_every_pre_v4_comparator_serialises_without_the_new_keys():
    for name, config in COMPARATORS.items():
        if name in (V4, V4_N):
            continue
        data = config_to_dict(config)
        assert "enable_decided_only_time" not in data
        assert "remove_post_decided_assists" not in data


def test_v4_is_rc3_plus_both_flags_and_v4_n_is_rc3_plus_the_time_flag():
    rc3 = config_to_dict(COMPARATORS[RC3])
    v4 = config_to_dict(COMPARATORS[V4])
    v4_n = config_to_dict(COMPARATORS[V4_N])
    assert v4 == {**rc3, "config_id": V4, "enable_decided_only_time": True,
                  "remove_post_decided_assists": True}
    assert v4_n == {**rc3, "config_id": V4_N, "enable_decided_only_time": True}


def test_config_from_dict_reads_a_missing_flag_as_false():
    rc3 = config_to_dict(COMPARATORS[RC3])
    config = config_from_dict(rc3)
    assert config.enable_decided_only_time is False
    assert config.remove_post_decided_assists is False
    assert config_from_dict(config_to_dict(COMPARATORS[V4])) == COMPARATORS[V4]


@pytest.mark.parametrize("key", ["enable_decided_only_time", "remove_post_decided_assists"])
def test_a_v4_manifest_omitting_either_flag_fails_verification(key):
    manifest = _manifest()
    verify_manifest(manifest)
    del manifest["comparators"][V4][key]
    with pytest.raises(ManifestMismatchError, match="impact_v4.*declared identity"):
        verify_manifest(manifest)


def test_omitting_the_time_flag_from_the_diagnostic_comparator_fails_too():
    manifest = _manifest()
    del manifest["comparators"][V4_N]["enable_decided_only_time"]
    with pytest.raises(ManifestMismatchError, match="impact_v4_n.*declared identity"):
        verify_manifest(manifest)


# -- the description ----------------------------------------------------------------------

def _timing_line(manifest):
    (line,) = [c for c in manifest["formula_changes_vs_live_legacy"] if c.startswith("timing:")]
    return line


def test_a_v4_manifest_describes_the_v4_timing_and_assists():
    manifest = _manifest()
    assert "unchanged legacy time factor" not in _timing_line(manifest)
    assert "decided" in _timing_line(manifest)
    assert any(c.startswith("assists:") and "decided" in c
               for c in manifest["formula_changes_vs_live_legacy"])


def test_the_diagnostic_n_manifest_describes_time_but_not_assists():
    manifest = _manifest(release_comparator=V4_N)
    assert "decided" in _timing_line(manifest)
    assert not any(c.startswith("assists:") for c in manifest["formula_changes_vs_live_legacy"])


def test_an_rc3_manifest_keeps_the_frozen_rc3_description():
    frozen = json.loads(RC3_MANIFEST.read_text(encoding="utf-8"))["formula_changes_vs_live_legacy"]
    built = _manifest(release_comparator=RC3)["formula_changes_vs_live_legacy"]
    assert built == frozen


# -- provenance: fingerprint contract v2 --------------------------------------------------

def test_the_contract_is_v2_and_player_py_is_hashed():
    assert SOURCE_FINGERPRINT_VERSION == 2
    assert "app/models/player.py" in impact_manifest.HASHED_SOURCES
    assert "app/models/player.py" in _manifest()["source_digests"]


def test_a_new_manifest_records_the_fingerprint_contract():
    assert _manifest()["source_snapshots"]["fingerprint_version"] == SOURCE_FINGERPRINT_VERSION


def test_snapshots_under_another_contract_are_refused():
    db = session()
    match, _, _ = build_match(db, "fpv", kills={7: [("A1", "B1", 10.0)]})
    manifest = _manifest(source_snapshots={"matches": {str(match.id): match_source_fingerprint(db, match.id)}})
    verify_source_snapshots(db, manifest)
    old = dict(manifest, source_snapshots={"matches": manifest["source_snapshots"]["matches"]})
    with pytest.raises(ManifestMismatchError, match="fingerprint contract"):
        verify_source_snapshots(db, old)
    with pytest.raises(ManifestMismatchError, match="fingerprint contract"):
        verify_manifest(old)


def _match_with_an_assist():
    db = session()
    match, players, _ = build_match(db, "fpa", kills={7: [("A1", "B1", 10.0)]})
    kill = db.query(KillEvent).one()
    kill.source_meta = {"assistants": ["fpa-A2"]}
    db.commit()
    return db, match, players, kill


def test_an_assistant_payload_edit_moves_the_fingerprint():
    db, match, _, kill = _match_with_an_assist()
    before = match_source_fingerprint(db, match.id)
    kill.source_meta = {"assistants": ["fpa-A3"]}
    db.commit()
    assert match_source_fingerprint(db, match.id) != before


def test_only_the_assistants_payload_is_read_from_source_meta():
    db, match, _, kill = _match_with_an_assist()
    before = match_source_fingerprint(db, match.id)
    kill.source_meta = {"assistants": ["fpa-A2"], "unrelated": 1}
    db.commit()
    assert match_source_fingerprint(db, match.id) == before


def test_no_assistants_and_an_empty_list_are_distinguished():
    db, match, _, kill = _match_with_an_assist()
    kill.source_meta = None
    db.commit()
    none = match_source_fingerprint(db, match.id)
    kill.source_meta = {"assistants": []}
    db.commit()
    assert match_source_fingerprint(db, match.id) != none


def test_a_display_name_change_moves_the_fingerprint():
    db, match, players, _ = _match_with_an_assist()
    before = match_source_fingerprint(db, match.id)
    db.get(Player, players["A2"].player_id).display_name = "renamed#0000"
    db.commit()
    assert match_source_fingerprint(db, match.id) != before


def test_a_match_player_remap_moves_the_fingerprint():
    db, match, players, _ = _match_with_an_assist()
    before = match_source_fingerprint(db, match.id)
    old = db.get(Player, players["A2"].player_id)
    twin = Player(display_name=old.display_name)       # same name: only the id moves
    db.add(twin)
    db.flush()
    db.get(MatchPlayer, players["A2"].id).player_id = twin.id
    db.commit()
    assert match_source_fingerprint(db, match.id) != before


def test_another_matchs_player_rename_does_not_move_the_fingerprint():
    db, match, _, _ = _match_with_an_assist()
    before = match_source_fingerprint(db, match.id)
    _, others, _ = build_match(db, "fpo", kills={3: [("B1", "A1", 5.0)]})
    db.get(Player, others["A1"].player_id).display_name = "elsewhere#1"
    db.commit()
    assert match_source_fingerprint(db, match.id) == before

"""The decomposition check, parameterised on the manifest's release comparator
(plan 2026-09-21-impact-v4, section 3.4, review finding R5).

Borrowing rc3's command unchanged would silently review rc3: it hard-selected
the RC3 comparator and checked D x RAW assists. Under v4 it must check the
release comparator, D x ELIGIBLE assists, and the decided-only time factor,
each rebuilt independently -- so these tests break the scorer and require the
check to disagree.
"""
from dataclasses import replace

import pytest

from app.models import KillEvent, Round, RoundPlayerStat
from app.scoring import impact
from app.scoring.impact_manifest import COMPARATORS, RC3, V2_30_80_BONUS, V4, V4_N, build_manifest
from scripts import compare_rc3_decomposition as decomposition
from tests.buy_disruption_fixtures import build_match, session

V4_CONFIG = COMPARATORS[V4]
V4_N_CONFIG = COMPARATORS[V4_N]
RC2_CONFIG = COMPARATORS[V2_30_80_BONUS]

# Round 3 is planted at 20 and defused at 50. B1 kills A1 at 40 (live), A2
# trades B1 at 40.5 (live); after the defuse A3 kills B2 at 52 with A4
# assisting, and B3 kills itself at 53.
KILLS = {3: [("B1", "A1", 40.0), ("A2", "B1", 40.5), ("A3", "B2", 52.0), ("B3", "B3", 53.0)]}


def _match():
    db = session(all_tables=True)
    match, players, events = build_match(db, "v4decomp", kills=KILLS, count_stats=True)
    rnd = db.query(Round).filter_by(match_id=match.id, round_number=3).one()
    rnd.planted, rnd.plant_time, rnd.defused, rnd.defuse_time = True, 20.0, True, 50.0
    rnd.outcome = "Team B Defuse Win"
    post_defuse = db.get(KillEvent, events[3][2])
    post_defuse.source_meta = {"assistants": ["v4decomp-A4", "nobody#0000"]}
    live = db.get(KillEvent, events[3][1])
    live.source_meta = {"assistants": ["v4decomp-A5"]}
    for name, n in (("A4", 2), ("A5", 1)):
        (db.query(RoundPlayerStat)
         .filter(RoundPlayerStat.match_player_id == players[name].id,
                 RoundPlayerStat.round_id == rnd.id).update({"assists": n}))
    db.commit()
    return db, match, players


@pytest.mark.parametrize("config", [V4_CONFIG, V4_N_CONFIG])
def test_correct_v4_scoring_decomposes_exactly(config):
    db, match, _ = _match()
    checks, mismatches = decomposition.check_match(db, match.id, config, RC2_CONFIG)
    assert checks > 0
    assert mismatches == []


def test_the_fixture_really_removes_an_assist_and_zeroes_a_decided_kill():
    db, match, players = _match()
    rows = impact.build_impact_rows_for_match(db, match.id, **V4_CONFIG.build_kwargs())
    rc3 = impact.build_impact_rows_for_match(db, match.id, **COMPARATORS[RC3].build_kwargs())
    a4 = players["A4"].id
    assert [r.assists_component for r in rows if r.match_player_id == a4 and r.assists_component] == [100]
    assert [r.assists_component for r in rc3 if r.match_player_id == a4 and r.assists_component] == [200]
    a3 = players["A3"].id
    assert sum(r.time_impact for r in rows if r.match_player_id == a3) == 0
    assert sum(r.time_impact for r in rc3 if r.match_player_id == a3) > 0


def test_a_scorer_that_ignores_the_assists_flag_is_caught(monkeypatch):
    db, match, _ = _match()
    real = decomposition.build_impact_rows_for_match

    def keeps_assists(database, match_id, **kwargs):
        kwargs.pop("remove_post_decided_assists", None)
        return real(database, match_id, **kwargs)

    monkeypatch.setattr(decomposition, "build_impact_rows_for_match", keeps_assists)
    _, mismatches = decomposition.check_match(db, match.id, V4_CONFIG, RC2_CONFIG)
    assert any("D*eligible assists" in m for m in mismatches)


def test_a_scorer_that_keeps_the_legacy_time_factor_is_caught(monkeypatch):
    db, match, _ = _match()
    real = decomposition.build_impact_rows_for_match

    def legacy_time(database, match_id, **kwargs):
        kwargs.pop("enable_decided_only_time", None)
        return real(database, match_id, **kwargs)

    monkeypatch.setattr(decomposition, "build_impact_rows_for_match", legacy_time)
    _, mismatches = decomposition.check_match(db, match.id, V4_CONFIG, RC2_CONFIG)
    assert any("time factor" in m for m in mismatches)


def test_a_planted_round_with_no_plant_time_is_not_a_crash():
    """Code review, finding 3. rounds.plant_time is nullable, and the scorer's
    own round_decided routes that case through effective_plant_time() is None
    to the Time-Win branch. The independent restatement must agree, not raise."""
    no_time = {"planted": True, "plant_time": None, "defused": False, "defuse_time": None,
               "outcome": "Team A Elimination Win"}
    assert decomposition.independent_round_decided(no_time, 150.0) is False
    time_win = dict(no_time, outcome="Team B Time Win")
    assert decomposition.independent_round_decided(time_win, 150.0) is True
    assert decomposition.independent_round_decided(time_win, 99.0) is False
    # and it agrees with the scorer's own predicate on the same inputs
    from app.models.round import Round
    from app.scoring.plant_window import round_decided
    for row in (no_time, time_win):
        r = Round(round_number=5, **row)
        for t in (0.0, 99.0, 150.0):
            assert decomposition.independent_round_decided(row, t) == round_decided(r, t)


def test_the_independent_decided_rule_matches_declaration_12():
    decided = decomposition.independent_round_decided
    defused = {"planted": True, "plant_time": 20.0, "defused": True, "defuse_time": 50.0,
               "outcome": "Team B Defuse Win"}
    assert not decided(defused, 49.9) and decided(defused, 50.0)
    exploded = {"planted": True, "plant_time": 20.0, "defused": False, "defuse_time": None,
                "outcome": "Team A Elimination Win"}
    assert not decided(exploded, 64.9) and decided(exploded, 65.0)
    phantom = {"planted": True, "plant_time": 101.0, "defused": False, "defuse_time": None,
               "outcome": "Team B Time Win"}
    assert not decided(phantom, 100.0) and decided(phantom, 100.5)
    late_elim = {"planted": True, "plant_time": 101.0, "defused": False, "defuse_time": None,
                 "outcome": "Team A Elimination Win"}
    assert not decided(late_elim, 103.0)


def test_malformed_pairs_are_reported_not_a_traceback():
    """Code review, finding 6: `--pairs P0` unpacked to a 1-tuple and raised
    ValueError before the STOP message it has for exactly this."""
    from scripts import compare_v4_reference

    for bad in ("P0", "P0:", ":exante", "P0:exante:extra", "N+A:sometime"):
        with pytest.raises(SystemExit, match="unknown pair|malformed pair"):
            compare_v4_reference.parse_pairs(bad)
    assert compare_v4_reference.parse_pairs("P0:exante,N:realized") == [("P0", "exante"), ("N", "realized")]
    assert len(compare_v4_reference.parse_pairs("")) == 6


# -- the command follows the manifest -----------------------------------------------------

def _manifest(release):
    return build_manifest(candidate_id="t", created="2026-09-21", scorer_revision="x",
                          activation_impact_calculation_version=4,
                          source_snapshots={"matches": {}}, release_comparator=release)


def test_the_release_comparator_is_read_from_the_manifest():
    release, rc2 = decomposition.configs_from_manifest(_manifest(V4))
    assert release == V4_CONFIG
    assert rc2 == RC2_CONFIG
    release, _ = decomposition.configs_from_manifest(_manifest(RC3))
    assert release == COMPARATORS[RC3]


def test_a_diagnostic_comparator_can_be_named_explicitly():
    release, _ = decomposition.configs_from_manifest(_manifest(V4), comparator=V4_N)
    assert release == V4_N_CONFIG


def test_rc3s_thirteen_matches_are_never_a_default_for_another_release():
    assert decomposition.default_matches(COMPARATORS[RC3]) == list(decomposition.DECLARED_MATCHES)
    with pytest.raises(SystemExit, match="--matches"):
        decomposition.default_matches(V4_CONFIG)


def test_the_report_is_named_for_the_release():
    assert decomposition.report_name(COMPARATORS[RC3]) == "rc3-decomposition.md"
    assert decomposition.report_name(V4_CONFIG) == "impact_v4-decomposition.md"

"""tracker.gg spentCredits is stored when present, never invented (spec 2026-09-12 section 10)."""
from app.adapters.trackergg_browserstate_source import load_match
from app.models import Round, RoundPlayerStat
from app.models.round_player_spend import RoundPlayerSpend
from tests.buy_disruption_fixtures import build_match, session

IDS = [f"P{i}#T" for i in range(10)]


def _stat(value):
    return {"value": value}


def _payload(match_id="m-spend", spent_for=lambda r, i: 100 * r + i):
    segments = [
        {"type": "team-summary", "attributes": {"teamId": "Red"}, "stats": {"roundsWon": _stat(2)}},
        {"type": "team-summary", "attributes": {"teamId": "Blue"}, "stats": {"roundsWon": _stat(0)}},
    ]
    for i, pid in enumerate(IDS):
        segments.append({"type": "player-summary", "attributes": {"platformUserIdentifier": pid},
                         "metadata": {"agentName": "Jett", "teamId": "Red" if i < 5 else "Blue"}})
    for r in (1, 2):
        segments.append({"type": "round-summary", "attributes": {"round": r}, "metadata": {},
                         "stats": {"winningTeam": _stat("Red"), "roundResult": _stat("Elimination")}})
        for i, pid in enumerate(IDS):
            stats = {k: _stat(0) for k in ("score", "kills", "deaths", "assists")}
            stats["loadoutValue"], stats["remainingCredits"] = _stat(800), _stat(0)
            spent = spent_for(r, i)
            if spent is not None:
                stats["spentCredits"] = _stat(spent)
            segments.append({"type": "player-round",
                             "attributes": {"round": r, "platformUserIdentifier": pid},
                             "stats": stats})
    return {"attributes": {"id": match_id}, "metadata": {"mapName": "Bind"}, "segments": segments}


def test_spent_credits_are_stored_per_player_round():
    db = session(all_tables=True)
    load_match(db, _payload())
    values = sorted(s.spent for s in db.query(RoundPlayerSpend))
    assert len(values) == 20
    assert values[:3] == [100, 101, 102] and values[-1] == 209


def test_absent_spent_credits_write_no_row_not_zero():
    db = session(all_tables=True)
    load_match(db, _payload(spent_for=lambda r, i: None if i == 3 else 0))
    assert db.query(RoundPlayerSpend).count() == 18
    assert {s.spent for s in db.query(RoundPlayerSpend)} == {0}


def test_ingesting_into_a_populated_database_leaves_old_matches_unknown():
    db = session(all_tables=True)
    old, _, _ = build_match(db, "old")
    old_ids = {s.id for s in db.query(RoundPlayerStat).join(Round).filter(Round.match_id == old.id)}
    assert old_ids
    load_match(db, _payload("m-new"))
    linked = {s.round_player_stat_id for s in db.query(RoundPlayerSpend)}
    assert len(linked) == 20
    assert not (linked & old_ids)

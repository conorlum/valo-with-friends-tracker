"""Building a replacement scores table and renaming it in, then back out.

The forward path and the rollback are the same operation in opposite
directions, and both must be all-or-nothing: at no point may a reader see a
half-replaced table. The operation log binds the steps: nothing is swapped in
that was not verified, and nothing is rolled back over matches that arrived
after the swap. Needs a disposable PostgreSQL database (tests/_postgres.py).
"""

import hashlib
import json
import types
import uuid

import pytest
from sqlalchemy import text

from app.models import Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact_manifest import match_source_fingerprint
from app.scoring.write_gate import install_write_identity, read_gate
from scripts import swap_impact_scores as swap_tool
from scripts.export_impact_artifact import canonical_json, load_columns, write_load_artifact
from tests._postgres import postgres_session_or_skip

ADMIN = "rc3-runbook"
#: Stands in for K1's comparison hash: the export under test must carry it.
CHAIN = "c" * 64


def _empty(session):
    """Every table these tests fill, emptied. Run before each test as well as
    after it: the verification fingerprints EVERY match in the database, so a
    row another test module committed would otherwise change the result."""
    session.rollback()
    for leftover in (swap_tool.BUILT, swap_tool.PREVIOUS, swap_tool.ROLLED_BACK):
        if swap_tool._table_exists(session, leftover):
            session.execute(text(f"DROP TABLE {leftover}"))
    for table in ("impact_scores", "round_player_stats", "round_player_spend", "kill_events", "rounds",
                  "match_players", "matches", swap_tool.LOG):
        session.execute(text(f"DELETE FROM {table}"))
    session.execute(text("UPDATE scoring_gate SET state = 'closed' WHERE id"))
    session.commit()


@pytest.fixture
def db():
    session = postgres_session_or_skip(empties_tables=True)
    if read_gate(session) is None:
        pytest.skip("the write gate is not installed on the test database")
    assert swap_tool._table_exists(session, swap_tool.LOG), (
        "the gate is installed without scoring_release_log: re-run scripts/install_release_write_gate.py")
    install_write_identity(session, ADMIN)
    _empty(session)
    try:
        yield session
    finally:
        _empty(session)
        session.close()


def _corpus(db, rounds=2, players=2):
    """A handful of real stat rows, so key-set checks have something to match."""
    match = Match(external_id=f"swap-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    match_players = []
    for index in range(players):
        player = Player(display_name=f"swap-{uuid.uuid4()}")
        db.add(player)
        db.flush()
        match_player = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett",
                                   team=Team.TEAM_1 if index % 2 == 0 else Team.TEAM_2)
        db.add(match_player)
        match_players.append(match_player)
    db.flush()
    keys = []
    for number in range(1, rounds + 1):
        round_row = Round(match_id=match.id, round_number=number, outcome="Team A Elimination Win",
                          planted=False, exploded=False, defused=False)
        db.add(round_row)
        db.flush()
        for match_player in match_players:
            db.add(RoundPlayerStat(round_id=round_row.id, match_player_id=match_player.id,
                                   score=100, kills=1, deaths=0, assists=0, loadout=3900,
                                   remaining=1000))
            keys.append((round_row.id, match_player.id))
    db.flush()
    return keys


def _row(round_id, match_player_id, *, impact, scoring_version):
    values = {name: 0 for name in load_columns()}
    values.update(round_id=round_id, match_player_id=match_player_id, impact=impact,
                  trade_detail=None, scoring_version=scoring_version)
    return types.SimpleNamespace(**values)


def _install_v1(db, keys, *, impact=10):
    """The rows the live table starts with. Every column is written: they are
    NOT NULL with no default, exactly as production has them."""
    columns = list(load_columns())
    statement = text(f"INSERT INTO impact_scores ({', '.join(columns)}) "
                     f"VALUES ({', '.join(':' + name for name in columns)})")
    for round_id, match_player_id in keys:
        values = {name: 0 for name in columns}
        values.update(round_id=round_id, match_player_id=match_player_id, impact=impact,
                      trade_detail=None, scoring_version=1)
        db.execute(statement, values)
    db.flush()


def _export(tmp_path, db, keys, *, impact, scoring_version=3, comparison_sha256=CHAIN):
    """A load artifact and the sidecar an export would write beside it."""
    rows = [_row(r, m, impact=impact, scoring_version=scoring_version) for r, m in keys]
    path = tmp_path / "K5.load.csv"
    written = write_load_artifact(rows, path)
    fingerprints = {str(m): match_source_fingerprint(db, m)
                    for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()}
    sidecar = {
        "artifact": {"sha256": comparison_sha256},
        "load_artifact": {"sha256": written["sha256"]},
        "configuration": {"impact_calculation_version": 3},
        "inputs": {
            "database": db.execute(text("SELECT current_database()")).scalar(),
            "match_source_fingerprints": fingerprints,
            "cohort_fingerprint": hashlib.sha256(canonical_json(fingerprints).encode("utf-8")).hexdigest(),
        },
    }
    sidecar_path = tmp_path / "K5.json"
    sidecar_path.write_text(canonical_json(sidecar), encoding="utf-8")
    return str(path), str(sidecar_path)


def _verify(db, path, sidecar, **overrides):
    kwargs = dict(sidecar_path=sidecar, expect_scoring_version=3, expect_comparison_sha256=CHAIN,
                  approved_path=None)
    kwargs.update(overrides)
    return swap_tool.verify_build(db, path, **kwargs)


def _verify_and_record(db, path, sidecar):
    return swap_tool.verify_and_record(db, path, sidecar_path=sidecar, expect_scoring_version=3,
                                       expect_comparison_sha256=CHAIN, approved_path=None)


def _built_and_verified(db, tmp_path, *, v1=10, rc3=77):
    keys = _corpus(db)
    _install_v1(db, keys, impact=v1)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=rc3)
    swap_tool.build(db, path)
    db.commit()
    facts = _verify_and_record(db, path, sidecar)
    assert facts["problems"] == []
    return keys, path, sidecar


def _impacts(db, table="impact_scores"):
    return [v for (v,) in db.execute(text(f"SELECT DISTINCT impact FROM {table}"))]


def _log(db):
    return [(operation, outcome) for operation, outcome in db.execute(text(
        f"SELECT operation, outcome FROM {swap_tool.LOG} ORDER BY id")).all()]


# ---- the forward path and the rollback ------------------------------------------------

def test_build_then_verify_then_swap_replaces_every_row(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)

    swap_tool.swap(db)
    db.commit()

    assert _impacts(db) == [77]
    assert _impacts(db, swap_tool.PREVIOUS) == [10]
    assert db.execute(text(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'impact_scores_pkey'")).scalar() == 1
    assert _log(db) == [("build", "built"), ("verify-build", "clean"), ("swap", "swapped")]


def test_the_swapped_table_keeps_the_canonical_not_null_names(db, tmp_path):
    """The script renames only index and foreign-key names, relying on
    PostgreSQL 18 copying NOT NULL constraint names unchanged. If that ever
    stops being true, this is where it shows."""
    def not_null_names():
        return sorted(name for (name,) in db.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'impact_scores'::regclass "
            "AND contype = 'n'")).all())

    before = not_null_names()
    _built_and_verified(db, tmp_path)
    swap_tool.swap(db)
    db.commit()

    assert before and not_null_names() == before


def test_rollback_restores_the_previous_rows(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db)
    db.commit()

    swap_tool.rollback(db)
    db.commit()

    assert _impacts(db) == [10]
    assert read_gate(db).state == "closed", "a rollback must leave scoring frozen"
    assert swap_tool._table_exists(db, swap_tool.ROLLED_BACK), "keep the rc3 rows for inspection"
    assert _log(db)[-1] == ("rollback", "rolled back")


def test_an_interrupted_swap_changes_nothing_and_logs_nothing(db, tmp_path):
    """Everything the swap does, its log entry included, is inside one
    transaction, so a process that dies mid-swap leaves no trace of a swap."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)

    swap_tool.swap(db)
    db.rollback()  # the process dies before COMMIT

    assert _impacts(db) == [10]
    assert not swap_tool._table_exists(db, swap_tool.PREVIOUS)
    assert swap_tool._table_exists(db, swap_tool.BUILT), "the built table survives for a retry"
    assert ("swap", "swapped") not in _log(db)


def test_the_built_table_is_gated_too(db, tmp_path):
    """Nothing may edit the replacement between verification and the swap."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, _ = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    db.commit()

    other = postgres_session_or_skip()
    try:
        with pytest.raises(Exception) as caught:
            other.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 1"))
        assert "release write gate refused" in str(caught.value)
    finally:
        other.rollback()
        other.close()


# ---- what the log refuses ------------------------------------------------------------

def test_a_swap_without_a_verification_is_refused(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, _ = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_failed_verification_does_not_authorize_a_swap(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys[:-1], impact=77)  # one player-round short
    swap_tool.build(db, path)
    db.commit()
    assert _verify_and_record(db, path, sidecar)["problems"]

    with pytest.raises(swap_tool.Refused, match="verify-build failed"):
        swap_tool.swap(db)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_rebuild_after_verification_must_be_verified_again(db, tmp_path):
    _, path, _ = _built_and_verified(db, tmp_path)
    swap_tool.build(db, path)  # same file, but a new table the verification never saw
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db)
    db.rollback()


def test_a_swap_refuses_while_the_gate_is_open(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()

    with pytest.raises(swap_tool.Refused, match="gate is open"):
        swap_tool.swap(db)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_gate_opened_after_the_early_check_still_stops_the_swap(db, tmp_path, monkeypatch):
    """The early check runs before the locks; the gate is read again under them."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()
    monkeypatch.setattr(swap_tool, "_require_gate_closed", lambda database, operation: None)

    with pytest.raises(swap_tool.Refused, match="gate is open under lock"):
        swap_tool.swap(db)
    db.rollback()
    assert _impacts(db) == [10]


def test_rollback_refuses_while_the_gate_is_open(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db)
    db.commit()
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()

    with pytest.raises(swap_tool.Refused, match="gate is open"):
        swap_tool.rollback(db)
    db.rollback()
    assert _impacts(db) == [77]


def test_rollback_refuses_once_a_match_arrived_after_the_swap(db, tmp_path):
    """D11: restoring the pre-activation table would silently drop the scores of
    anything ingested since, so R1 expires with the first new match."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db)
    db.commit()
    _corpus(db)  # a match ingested after activation
    db.commit()

    with pytest.raises(swap_tool.Refused, match="ingested after the swap"):
        swap_tool.rollback(db)
    db.rollback()
    assert _impacts(db) == [77]


def test_a_refused_second_swap_does_not_hide_the_real_one_from_rollback(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db)
    db.commit()
    with pytest.raises(swap_tool.Refused):
        swap_tool.swap(db)
    db.rollback()
    swap_tool.record(db, "swap", "refused", {"reason": "already swapped"})  # as main() logs it
    db.commit()

    swap_tool.rollback(db)
    db.commit()
    assert _impacts(db) == [10]


# ---- what verification refuses -------------------------------------------------------

def test_verify_refuses_an_artifact_that_misses_a_player_round(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys[:-1], impact=77)

    swap_tool.build(db, path)
    facts = _verify(db, path, sidecar)
    assert any("key set differs" in problem for problem in facts["problems"])


def test_verify_refuses_the_wrong_scoring_version(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77, scoring_version=2)

    swap_tool.build(db, path)
    facts = _verify(db, path, sidecar)
    assert any("scoring_version is [2]" in problem for problem in facts["problems"])


def test_verify_catches_a_single_edited_row(db, tmp_path):
    """The built table is compared against the artifact by reading it back, so a
    row changed after the load cannot pass."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    db.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 78 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})

    facts = _verify(db, path, sidecar)
    assert any("does not read back" in problem for problem in facts["problems"])


def test_verify_refuses_inputs_that_changed_since_the_export(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    db.execute(text("UPDATE round_player_stats SET kills = 4 WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})

    facts = _verify(db, path, sidecar)
    match_id = db.execute(text("SELECT match_id FROM rounds WHERE id = :r"), {"r": keys[0][0]}).scalar()
    assert facts["problems"] == [f"1 matches' source rows changed since the export (first ['{match_id}'])"]


def test_verify_refuses_a_match_ingested_since_the_export(db, tmp_path):
    """A bare match row, so the key-set check passes and only the input check can catch it."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    db.add(Match(external_id=f"late-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Ascent"))
    db.flush()

    facts = _verify(db, path, sidecar)
    assert len(facts["problems"]) == 1
    assert facts["problems"][0].startswith("the match set changed since the export: 1 added")


def test_verify_refuses_a_sidecar_that_describes_another_file(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, path)
    edited = json.load(open(sidecar, encoding="utf-8"))
    edited["load_artifact"]["sha256"] = "0" * 64
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(edited, handle)

    facts = _verify(db, path, sidecar)
    assert any("the sidecar describes load artifact 000" in p for p in facts["problems"])


def test_verify_refuses_an_export_that_is_not_the_chain(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, sidecar = _export(tmp_path, db, keys, impact=77, comparison_sha256="d" * 64)
    swap_tool.build(db, path)

    facts = _verify(db, path, sidecar)
    assert any("is not the chain's" in p for p in facts["problems"])


def test_verify_live_proves_what_the_site_reads_after_the_swap(db, tmp_path):
    keys, path, sidecar = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db)
    db.commit()
    assert _verify(db, path, sidecar, table=swap_tool.LIVE)["problems"] == []

    db.execute(text("UPDATE impact_scores SET impact = 78 WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    facts = _verify(db, path, sidecar, table=swap_tool.LIVE)
    assert facts["problems"] == ["impact_scores does not read back as the artifact that was loaded"]


def test_a_clean_live_verification_never_authorizes_a_swap(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, _ = _export(tmp_path, db, keys, impact=77)
    built = swap_tool.build(db, path)
    db.commit()
    swap_tool.record(db, "verify-live", "clean", {"built_oid": built["built_oid"],
                                                  "max_match_id": db.execute(text("SELECT max(id) FROM matches")).scalar()})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db)
    db.rollback()


def test_the_cli_refuses_a_database_it_was_not_told_to_expect(db, monkeypatch, capsys):
    monkeypatch.setattr(swap_tool, "SessionLocal", postgres_session_or_skip)
    assert swap_tool.main(["state", "--expect-database", "valo_somewhere_else"]) == swap_tool.EXIT_REFUSED
    assert "REFUSED: connected to" in capsys.readouterr().out

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
from app.scoring.impact_manifest import (
    RC3,
    SOURCE_FINGERPRINT_VERSION,
    lf_sha256,
    match_source_fingerprint,
)
from app.scoring.write_gate import install_write_identity, read_gate
from scripts import freeze_impact_candidate as freezer
from scripts import swap_impact_scores as swap_tool
from scripts.export_impact_artifact import (
    COMPARISON_HEADER,
    canonical_json,
    load_columns,
    write_artifact,
    write_load_artifact,
)
from tests._postgres import postgres_session_or_skip

ADMIN = "rc3-runbook"
#: rc3's layout, which these tests were written against; the tool itself has no
#: default (Impact v4 plan, section 4.1). tests/test_swap_parameterised_names.py
#: covers v4's names.
NAMES = swap_tool.SwapNames.validated(previous="impact_scores_v1",
                                      rolled_back="impact_scores_rc3_rolled_back")
V4_LEFTOVERS = ("impact_scores_v3", "impact_scores_v4_rolled_back")
#: A hash that belongs to no artifact here, for the export-off-the-chain test.
NOT_THE_CHAIN = "c" * 64


def _empty(session):
    """Every table these tests fill, emptied. Run before each test as well as
    after it: the verification fingerprints EVERY match in the database, so a
    row another test module committed would otherwise change the result."""
    session.rollback()
    for leftover in (swap_tool.BUILT, NAMES.previous, NAMES.rolled_back, *V4_LEFTOVERS):
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
    """Carries both projections' fields: the table's columns, and the two the
    comparison header renders but the table never stores."""
    values = {name: 0 for name in (*load_columns(), *COMPARISON_HEADER)}
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


def _export(tmp_path, db, keys, *, impact, scoring_version=3, comparison_sha256=None,
            comparison_impact=None):
    """Both projections of one export, and the sidecar written beside them.

    `comparison_impact` scores the comparison artifact differently from the
    load artifact, which is the shape of a load projection that lost or moved a
    value: everything else still agrees."""
    rows = [_row(r, m, impact=impact, scoring_version=scoring_version) for r, m in keys]
    compared = rows if comparison_impact is None else [
        _row(r, m, impact=comparison_impact, scoring_version=scoring_version) for r, m in keys]
    comparison_path = tmp_path / "K5.csv"
    comparison = write_artifact(compared, comparison_path)
    path = tmp_path / "K5.load.csv"
    written = write_load_artifact(rows, path)
    fingerprints = {str(m): match_source_fingerprint(db, m)
                    for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()}
    sidecar = {
        "artifact": {"sha256": comparison_sha256 or comparison["sha256"]},
        "load_artifact": {"sha256": written["sha256"]},
        "configuration": {"impact_calculation_version": 3},
        "inputs": {
            "database": db.execute(text("SELECT current_database()")).scalar(),
            # As the exporter records it: fingerprints are only comparable
            # within one contract (Impact v4 plan, section 2.4).
            "fingerprint_version": SOURCE_FINGERPRINT_VERSION,
            "match_source_fingerprints": fingerprints,
            "cohort_fingerprint": hashlib.sha256(canonical_json(fingerprints).encode("utf-8")).hexdigest(),
        },
    }
    sidecar_path = tmp_path / "K5.json"
    sidecar_path.write_text(canonical_json(sidecar), encoding="utf-8")
    return str(path), str(sidecar_path), str(comparison_path)


def _chain_of(sidecar):
    """What the runbook would pass as the chain's hash for this export."""
    with open(sidecar, encoding="utf-8") as handle:
        return json.load(handle)["artifact"]["sha256"]


def _verify(db, export, **overrides):
    path, sidecar, comparison = export
    kwargs = dict(sidecar_path=sidecar, comparison_path=comparison, expect_scoring_version=3,
                  expect_comparison_sha256=_chain_of(export[1]), approved_path=None)
    kwargs.update(overrides)
    return swap_tool.verify_build(db, path, **kwargs)


def _verify_and_record(db, export):
    path, sidecar, comparison = export
    return swap_tool.verify_and_record(db, path, sidecar_path=sidecar, comparison_path=comparison,
                                       expect_scoring_version=3,
                                       expect_comparison_sha256=_chain_of(export[1]), approved_path=None)


def _built_and_verified(db, tmp_path, *, v1=10, rc3=77):
    keys = _corpus(db)
    _install_v1(db, keys, impact=v1)
    db.commit()
    export = _export(tmp_path, db, keys, impact=rc3)
    swap_tool.build(db, export[0])
    db.commit()
    facts = _verify_and_record(db, export)
    assert facts["problems"] == []
    return keys, export


def _impacts(db, table="impact_scores"):
    return [v for (v,) in db.execute(text(f"SELECT DISTINCT impact FROM {table}"))]


def _log(db):
    return [(operation, outcome) for operation, outcome in db.execute(text(
        f"SELECT operation, outcome FROM {swap_tool.LOG} ORDER BY id")).all()]


# ---- the forward path and the rollback ------------------------------------------------

def test_build_then_verify_then_swap_replaces_every_row(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)

    swap_tool.swap(db, NAMES)
    db.commit()

    assert _impacts(db) == [77]
    assert _impacts(db, NAMES.previous) == [10]
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
    swap_tool.swap(db, NAMES)
    db.commit()

    assert before and not_null_names() == before


def test_rollback_restores_the_previous_rows(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()

    swap_tool.rollback(db, NAMES)
    db.commit()

    assert _impacts(db) == [10]
    assert read_gate(db).state == "closed", "a rollback must leave scoring frozen"
    assert swap_tool._table_exists(db, NAMES.rolled_back), "keep the rc3 rows for inspection"
    assert _log(db)[-1] == ("rollback", "rolled back")


def test_an_interrupted_swap_changes_nothing_and_logs_nothing(db, tmp_path):
    """Everything the swap does, its log entry included, is inside one
    transaction, so a process that dies mid-swap leaves no trace of a swap."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)

    swap_tool.swap(db, NAMES)
    db.rollback()  # the process dies before COMMIT

    assert _impacts(db) == [10]
    assert not swap_tool._table_exists(db, NAMES.previous)
    assert swap_tool._table_exists(db, swap_tool.BUILT), "the built table survives for a retry"
    assert ("swap", "swapped") not in _log(db)


def test_the_built_table_is_gated_too(db, tmp_path):
    """Nothing may edit the replacement between verification and the swap."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
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
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_failed_verification_does_not_authorize_a_swap(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    export = _export(tmp_path, db, keys[:-1], impact=77)  # one player-round short
    swap_tool.build(db, export[0])
    db.commit()
    assert _verify_and_record(db, export)["problems"]

    with pytest.raises(swap_tool.Refused, match="verify-build failed"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_rebuild_after_verification_must_be_verified_again(db, tmp_path):
    _, export = _built_and_verified(db, tmp_path)
    swap_tool.build(db, export[0])  # same file, but a new table the verification never saw
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db, NAMES)
    db.rollback()


def test_a_swap_refuses_while_the_gate_is_open(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()

    with pytest.raises(swap_tool.Refused, match="gate is open"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_gate_opened_after_the_early_check_still_stops_the_swap(db, tmp_path, monkeypatch):
    """The early check runs before the locks; the gate is read again under them."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()
    monkeypatch.setattr(swap_tool, "_require_gate_closed", lambda database, operation: None)

    with pytest.raises(swap_tool.Refused, match="gate is open under lock"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_rollback_refuses_while_the_gate_is_open(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()

    with pytest.raises(swap_tool.Refused, match="gate is open"):
        swap_tool.rollback(db, NAMES)
    db.rollback()
    assert _impacts(db) == [77]


def test_rollback_refuses_once_a_match_arrived_after_the_swap(db, tmp_path):
    """D11: restoring the pre-activation table would silently drop the scores of
    anything ingested since, so R1 expires with the first new match."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    _corpus(db)  # a match ingested after activation
    db.commit()

    with pytest.raises(swap_tool.Refused, match="ingested after the swap"):
        swap_tool.rollback(db, NAMES)
    db.rollback()
    assert _impacts(db) == [77]


def test_rollback_refuses_when_the_sources_changed_since_the_swap(db, tmp_path):
    """External review round 2, finding 1. R1's only evidence used to be
    max(matches.id), which an edit to an EXISTING row does not move.

    The admin identity may correct a round during the 48-hour hold -- that is
    what it is for. If it does, the scores in impact_scores_v1 were computed
    from rows that no longer exist as they were, and restoring them puts back
    numbers that describe different inputs while logging `rollback rolled back`
    as though nothing were odd.
    """
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    db.execute(text("UPDATE round_player_stats SET kills = kills + 1 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="round_player_stats changed since the swap"):
        swap_tool.rollback(db, NAMES)
    db.rollback()
    assert _impacts(db) == [77], "the rollback must not have happened"


def test_rollback_accepts_source_drift_when_told_to_and_records_it(db, tmp_path):
    """A recovery path that can refuse outright is its own hazard: if the rc3
    scores are the emergency, drifted sources must not strand production on
    them. The override performs the rollback and writes the drift into the log,
    so the decision survives the incident."""
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    db.execute(text("UPDATE round_player_stats SET kills = kills + 1 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    db.commit()

    result = swap_tool.rollback(db, NAMES, accept_source_drift=True)
    db.commit()
    assert _impacts(db) == [10], "the v1 scores must be restored"
    assert result["source_drift"] == ["round_player_stats"]
    assert result["accepted_source_drift"] is True
    logged = swap_tool._latest(db, ("rollback", "rolled back")).details
    assert logged["source_drift"] == ["round_player_stats"]
    assert logged["accepted_source_drift"] is True


def test_rollback_refuses_a_retained_table_that_is_not_the_one_set_aside(db, tmp_path):
    """The swap records the oid the live table had, which is the oid
    impact_scores_v1 carries afterwards. A table dropped and recreated under
    that name has the right name and the wrong contents."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    db.execute(text(f"ALTER TABLE {NAMES.previous} RENAME TO impact_scores_v1_moved"))
    db.execute(text(f"CREATE TABLE {NAMES.previous} "
                    f"(LIKE impact_scores_v1_moved INCLUDING ALL)"))
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not the one the swap set aside"):
        swap_tool.rollback(db, NAMES)
    db.rollback()
    db.execute(text(f"DROP TABLE {NAMES.previous}"))
    db.execute(text(f"ALTER TABLE impact_scores_v1_moved RENAME TO {NAMES.previous}"))
    db.commit()


def test_a_refused_second_swap_does_not_hide_the_real_one_from_rollback(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    with pytest.raises(swap_tool.Refused):
        swap_tool.swap(db, NAMES)
    db.rollback()
    swap_tool.record(db, "swap", "refused", {"reason": "already swapped"})  # as main() logs it
    db.commit()

    swap_tool.rollback(db, NAMES)
    db.commit()
    assert _impacts(db) == [10]


# ---- what verification refuses -------------------------------------------------------

def test_verify_refuses_an_artifact_that_misses_a_player_round(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys[:-1], impact=77)

    swap_tool.build(db, export[0])
    facts = _verify(db, export)
    assert any("key set differs" in problem for problem in facts["problems"])


def test_verify_refuses_the_wrong_scoring_version(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77, scoring_version=2)

    swap_tool.build(db, export[0])
    facts = _verify(db, export)
    assert any("scoring_version is [2]" in problem for problem in facts["problems"])


def test_verify_catches_a_single_edited_row(db, tmp_path):
    """The built table is compared against the artifact by reading it back, so a
    row changed after the load cannot pass."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    db.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 78 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})

    facts = _verify(db, export)
    assert any("does not read back" in problem for problem in facts["problems"])


def test_verify_refuses_inputs_that_changed_since_the_export(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    db.execute(text("UPDATE round_player_stats SET kills = 4 WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})

    facts = _verify(db, export)
    match_id = db.execute(text("SELECT match_id FROM rounds WHERE id = :r"), {"r": keys[0][0]}).scalar()
    assert facts["problems"] == [f"1 matches' source rows changed since the export (first ['{match_id}'])"]


def test_verify_refuses_a_match_ingested_since_the_export(db, tmp_path):
    """A bare match row, so the key-set check passes and only the input check can catch it."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    db.add(Match(external_id=f"late-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Ascent"))
    db.flush()

    facts = _verify(db, export)
    assert len(facts["problems"]) == 1
    assert facts["problems"][0].startswith("the match set changed since the export: 1 added")


def test_verify_refuses_a_sidecar_that_describes_another_file(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    with open(export[1], encoding="utf-8") as handle:
        edited = json.load(handle)
    edited["load_artifact"]["sha256"] = "0" * 64
    with open(export[1], "w", encoding="utf-8") as handle:
        json.dump(edited, handle)

    facts = _verify(db, export)
    assert any("the sidecar describes load artifact 000" in p for p in facts["problems"])


def test_verify_refuses_an_export_that_is_not_the_chain(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])

    facts = _verify(db, export, expect_comparison_sha256=NOT_THE_CHAIN)
    assert any("is not the chain's" in p for p in facts["problems"])


def test_verify_refuses_a_comparison_artifact_that_was_edited(db, tmp_path):
    """The approved scoring is identified by the artifact's own bytes, never by
    the sidecar's word for them."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    with open(export[2], "ab") as handle:
        handle.write(b"0,0\n")

    facts = _verify(db, export)

    assert len(facts["problems"]) == 1
    assert facts["problems"][0].startswith("the comparison artifact hashes to")
    assert facts["problems"][0].endswith(f"not the chain's {_chain_of(export[1])}")


def test_verify_refuses_a_table_that_is_not_the_approved_scoring(db, tmp_path):
    """C2: the load file matches its sidecar, and the sidecar carries the
    chain's hash, but the rows loaded are not the rows the chain approved. Only
    comparing the approved artifact against the table catches it."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=78, comparison_impact=77)
    swap_tool.build(db, export[0])

    facts = _verify(db, export)

    assert facts["problems"] == [f"{len(keys)} rows differ from the approved scoring "
                                 f"(first {[f'{r}:{m}' for r, m in sorted(keys)[:3]]})"]
    assert facts["approved_rows_compared"] == len(keys)
    assert facts["columns_the_table_never_stores"] == ["leverage_component", "assists_component"]


def test_verify_live_proves_what_the_site_reads_after_the_swap(db, tmp_path):
    keys, export = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    assert _verify(db, export, table=swap_tool.LIVE)["problems"] == []

    db.execute(text("UPDATE impact_scores SET impact = 78 WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    facts = _verify(db, export, table=swap_tool.LIVE)
    assert facts["problems"] == [
        "impact_scores does not read back as the artifact that was loaded",
        f"1 rows differ from the approved scoring (first ['{keys[0][0]}:{keys[0][1]}'])",
    ]


def test_a_clean_live_verification_never_authorizes_a_swap(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    built = swap_tool.build(db, export[0])
    db.commit()
    swap_tool.record(db, "verify-live", "clean", {"built_oid": built["built_oid"],
                                                  "max_match_id": db.execute(text("SELECT max(id) FROM matches")).scalar()})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="not a clean verify-build"):
        swap_tool.swap(db, NAMES)
    db.rollback()


def test_the_cli_refuses_a_database_it_was_not_told_to_expect(db, monkeypatch, capsys):
    monkeypatch.setattr(swap_tool, "SessionLocal", postgres_session_or_skip)
    assert swap_tool.main(["state", "--expect-database", "valo_somewhere_else", "--identity", ADMIN,
                           "--previous-table", NAMES.previous,
                           "--rolled-back-table", NAMES.rolled_back]) == swap_tool.EXIT_REFUSED
    assert "REFUSED: connected to" in capsys.readouterr().out


# ---- the approved review must be complete, and be this candidate's -------------------

def _approved(tmp_path, db, keys, *, impact, drop_rows=0, manifest_sha=None, candidate=None,
              matches=None):
    """A frozen manifest and the review results that belong to it."""
    manifest_path = tmp_path / "candidate-manifest.json"
    match_id = db.execute(text("SELECT match_id FROM rounds WHERE id = :r"), {"r": keys[0][0]}).scalar()
    freezer.freeze(db, candidate_id="impact-rc3-test", release_comparator=RC3, activation_version=3,
                   match_ids=[match_id], out_path=manifest_path, scorer_revision="f" * 40, packages=[])
    fields = [c for c in load_columns() if c not in ("round_id", "match_player_id")]
    row = {name: 0 for name in fields}
    row.update(impact=impact, trade_detail=None, scoring_version=2)
    rows = {f"{r}:{m}": [row[name] for name in fields] for r, m in sorted(keys)}
    for key in sorted(rows)[:drop_rows]:
        del rows[key]
    approved = {
        "manifest_lf_sha256": manifest_sha or lf_sha256(manifest_path),
        "candidate_id": candidate or "impact-rc3-test",
        "release_comparator": RC3,
        "fields": fields,
        "matches": {str(match_id): {"source_fingerprint": match_source_fingerprint(db, match_id),
                                    "rows": rows}} if matches is None else matches,
    }
    approved_path = tmp_path / "review-results.json"
    approved_path.write_text(canonical_json(approved), encoding="utf-8")
    return str(approved_path), str(manifest_path)


def test_a_complete_approval_for_this_manifest_passes(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    approved, manifest = _approved(tmp_path, db, keys, impact=77)

    facts = _verify(db, export, approved_path=approved, manifest_path=manifest)

    assert facts["problems"] == []
    assert facts["approved_rows_checked"] == len(keys)
    assert facts["approved_review_scoring_versions"] == [2], "the review ran before activation"


def test_an_approval_covering_no_matches_is_refused(db, tmp_path):
    """C3: checking only the rows a file happens to carry is no check at all."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    approved, manifest = _approved(tmp_path, db, keys, impact=77, matches={})

    facts = _verify(db, export, approved_path=approved, manifest_path=manifest)

    assert any("cover 0 of the manifest's 1 declared matches" in p for p in facts["problems"])


def test_an_approval_missing_rows_of_a_match_is_refused(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    approved, manifest = _approved(tmp_path, db, keys, impact=77, drop_rows=1)

    facts = _verify(db, export, approved_path=approved, manifest_path=manifest)

    assert any("do not cover their rows" in p for p in facts["problems"])


def test_an_approval_from_another_manifest_is_refused(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    approved, manifest = _approved(tmp_path, db, keys, impact=77, manifest_sha="0" * 64)

    facts = _verify(db, export, approved_path=approved, manifest_path=manifest)

    assert any("were written against manifest 000" in p for p in facts["problems"])


def test_an_approval_without_its_manifest_is_refused(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    export = _export(tmp_path, db, keys, impact=77)
    swap_tool.build(db, export[0])
    approved, _ = _approved(tmp_path, db, keys, impact=77)

    facts = _verify(db, export, approved_path=approved)

    assert facts["problems"] == ["approved results were given without the manifest they belong to"]


def test_a_page_holding_the_cache_open_does_not_stall_the_swap(db, tmp_path):
    """C1: a page load reads player_view_cache and keeps that lock while its
    own second connection writes the cache through. Clearing the cache by
    TRUNCATE would queue behind the first and block the second, and no
    database deadlock exists to break it. The swap clears by DELETE instead."""
    keys, export = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    reader = postgres_session_or_skip()
    try:
        reader.execute(text("SELECT count(*) FROM player_view_cache")).scalar()  # holds ACCESS SHARE

        swap_tool.swap(db, NAMES)
        db.commit()

        assert _impacts(db) == [77]
    finally:
        reader.rollback()
        reader.close()


def test_the_swap_empties_the_player_cache(db, tmp_path):
    """Every cached page was computed from the old scores."""
    keys, export = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    player_id = db.execute(text(
        "SELECT player_id FROM match_players WHERE id = :m"), {"m": keys[0][1]}).scalar()
    db.execute(text("INSERT INTO player_view_cache (player_id, scope, data, version, updated_at) "
                    "VALUES (:p, 'recent', '{}'::jsonb, 1, now())"), {"p": player_id})
    db.commit()

    swap_tool.swap(db, NAMES)
    db.commit()

    assert db.execute(text("SELECT count(*) FROM player_view_cache")).scalar() == 0


def test_an_edit_after_a_clean_verification_is_refused(db, tmp_path):
    """C6: the admin identity may correct rows at any time, and neither the
    staged table's oid nor the highest match id would show that it did.

    NOTE the absence of pg_stat_force_next_flush(). This test used to call it
    before swapping, which made the statistics current and so tested only the
    case where the old counter-based guard could work at all -- see
    test_an_edit_the_statistics_have_not_caught_up_with_is_refused below.
    """
    keys, export = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 79 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="changed after verification"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_row_digest_moves_on_an_edit_that_leaves_the_row_count_alone(db, tmp_path):
    """External review, finding 2: the guard must read the ROWS.

    The old guard compared pg_stat_all_tables' n_tup_ins/upd/del, which are
    collected asynchronously: a write can be committed and durable while the
    statistics still report the pre-write totals, and taking the table lock
    does not make another backend's statistics current. So the guard could pass
    over a real edit and swap it in reporting success.

    That lag cannot be forced from a test here -- pg_stat_reset_* needs
    privileges this database's role does not have -- so what is pinned instead
    is a necessary property: the digest is computed from the row contents, and
    moves for an edit that changes NO row count and no row identity.

    What this test does NOT establish, and an earlier version of this docstring
    wrongly claimed (external review round 2): independence from the statistics.
    A counter-based guard would also notice THIS edit, once its statistics
    caught up -- n_tup_upd moves for any update. Independence comes from the
    implementation reading rows rather than pg_stat_all_tables, not from this
    assertion. What the assertion rules out is a count-only guard.
    """
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    before = swap_tool._row_digests(db, (swap_tool.BUILT,))
    db.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 4242 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    db.commit()
    after = swap_tool._row_digests(db, (swap_tool.BUILT,))

    assert before[swap_tool.BUILT][0] == after[swap_tool.BUILT][0], \
        "the row count is unchanged -- that is the point of this test"
    assert before[swap_tool.BUILT][1] != after[swap_tool.BUILT][1], \
        "the digest must move when a single value changes"


def test_a_row_digest_ignores_the_session_rendering_settings(db, tmp_path):
    """The digest hashes each row's TEXT form, so a setting that changes how a
    value prints would change the digest with no row changing -- and these
    tables hold double precision and timestamptz. A verification taken under
    one setting and a swap under another would then disagree, and the refusal
    ("verify again") would reproduce the disagreement rather than clear it.

    _row_digests pins the rendering itself. Hostile settings here must make no
    difference to the value it returns.
    """
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    tables = (swap_tool.BUILT, *swap_tool.SOURCE_TABLES)
    baseline = swap_tool._row_digests(db, tables)

    db.execute(text("SET LOCAL extra_float_digits = 0"))
    db.execute(text("SET LOCAL DateStyle = 'SQL, DMY'"))
    db.execute(text("SET LOCAL TimeZone = 'America/New_York'"))
    assert swap_tool._row_digests(db, tables) == baseline, \
        "the digest must not depend on how the session happens to render values"


def test_a_verification_without_row_digests_is_refused(db, tmp_path):
    """A log entry written before the digest guard cannot show the rows are
    unchanged, so it must not be accepted as if it had.

    This is a DIAGNOSTIC guard, not a safety one, and the mutation check says
    so: with the explicit branch removed the swap still refuses, because an
    absent recorded digest compares unequal to every table's real one. What the
    branch changes is the message -- "the rows themselves differ", which sends
    the operator hunting for an edit that never happened, becomes "this entry
    predates the digest guard: verify again".
    """
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    latest = swap_tool._latest(db, ("verify-build", "clean"))
    details = dict(latest.details)
    details.pop("row_digests", None)
    db.execute(text(f"UPDATE {swap_tool.LOG} SET details = :d WHERE id = :i"),
               {"d": json.dumps(details), "i": latest.id})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="recorded no row digests"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_source_row_edited_after_verification_is_refused(db, tmp_path):
    """The verification says those source rows produced these scores."""
    keys, export = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    db.execute(text("UPDATE round_player_stats SET kills = kills + 1 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})
    db.commit()

    with pytest.raises(swap_tool.Refused, match="round_player_stats changed after verification"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]

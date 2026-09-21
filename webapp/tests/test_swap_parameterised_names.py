"""The swap tool with no rc3 defaults (plan 2026-09-21-impact-v4, section 4.1, R6).

It hardcoded PREVIOUS = impact_scores_v1, ROLLED_BACK = impact_scores_rc3_rolled_back,
--expect-scoring-version 3 and --identity rc3-runbook. With impact_scores_v1 kept
until 2026-10-03 an unmodified v4 swap is refused, and a default that silently
names rc3 is how a v4 release would verify the wrong version. Every one of them
is now explicit, validated, quoted where it reaches SQL, and recorded.
"""
import inspect

import pytest
from sqlalchemy import text

from scripts import swap_impact_scores as swap_tool
from scripts.swap_impact_scores import SwapNames

V4_NAMES = dict(previous="impact_scores_v3", rolled_back="impact_scores_v4_rolled_back")


# -- validation (offline) -----------------------------------------------------------------

def test_valid_v4_names():
    names = SwapNames.validated(**V4_NAMES)
    assert names.previous == "impact_scores_v3"
    assert names.rolled_back == "impact_scores_v4_rolled_back"


@pytest.mark.parametrize("previous, rolled_back, why", [
    ("impact_scores_v3", "impact_scores_v3", "differ"),
    ("impact_scores", "impact_scores_x", "live table"),
    ("impact_scores_new", "impact_scores_x", "staged table"),
    ("impact_scores_v3", "scoring_release_log", "impact_scores_"),
    ("matches", "impact_scores_x", "impact_scores_"),
    ("impact_scores_V3", "impact_scores_x", "identifier"),
    ('impact_scores_v3"; DROP TABLE matches; --', "impact_scores_x", "identifier"),
    ("impact_scores_v3 ", "impact_scores_x", "identifier"),
    ("impact_scores_" + "x" * 40, "impact_scores_x", "long"),
    ("", "impact_scores_x", "identifier"),
])
def test_invalid_or_conflicting_names_are_rejected(previous, rolled_back, why):
    with pytest.raises(ValueError, match=why):
        SwapNames.validated(previous=previous, rolled_back=rolled_back)


def test_no_rc3_defaults_remain():
    assert not hasattr(swap_tool, "PREVIOUS")
    assert not hasattr(swap_tool, "ROLLED_BACK")
    for fn in (swap_tool.swap, swap_tool.rollback, swap_tool.state):
        param = inspect.signature(fn).parameters["names"]
        assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("argv, missing", [
    (["verify-build", "--expect-database", "x", "--identity", "v4-runbook"], "--expect-scoring-version"),
    (["verify-live", "--expect-database", "x", "--identity", "v4-runbook"], "--expect-scoring-version"),
    (["swap", "--expect-database", "x", "--identity", "v4-runbook", "--yes",
      "--rolled-back-table", "impact_scores_v4_rolled_back"], "--previous-table"),
    (["rollback", "--expect-database", "x", "--identity", "v4-runbook", "--yes",
      "--previous-table", "impact_scores_v3"], "--rolled-back-table"),
    (["state", "--expect-database", "x", "--identity", "v4-runbook"], "--previous-table"),
])
def test_the_cli_requires_every_release_specific_value(argv, missing, capsys):
    # Refused before any connection is made: no database is reachable here.
    with pytest.raises(SystemExit):
        swap_tool.main(argv)
    assert missing in capsys.readouterr().err


def test_the_cli_requires_an_identity(capsys):
    with pytest.raises(SystemExit):
        swap_tool.main(["state", "--expect-database", "x", "--previous-table", "impact_scores_v3",
                        "--rolled-back-table", "impact_scores_v4_rolled_back"])
    assert "--identity" in capsys.readouterr().err


def test_the_cli_rejects_bad_names_before_connecting(capsys):
    with pytest.raises(SystemExit):
        swap_tool.main(["state", "--expect-database", "x", "--identity", "v4-runbook",
                        "--previous-table", "impact_scores_v3", "--rolled-back-table", "impact_scores_v3"])
    assert "differ" in capsys.readouterr().err


# -- against the scratch database ------------------------------------------------------

from tests.test_swap_impact_scores import (  # noqa: E402  (fixture and helpers)
    _built_and_verified,
    _impacts,
    _log,
    db,
)


def _v1_retained(db):
    """rc3's retained table, still present as it will be until 2026-10-03."""
    db.execute(text("CREATE TABLE impact_scores_v1 (LIKE impact_scores INCLUDING DEFAULTS)"))
    db.execute(text("INSERT INTO impact_scores_v1 SELECT * FROM impact_scores"))
    db.commit()


def test_a_v4_swap_with_impact_scores_v1_present_proceeds_and_leaves_it_alone(db, tmp_path):
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    _v1_retained(db)
    names = SwapNames.validated(**V4_NAMES)
    result = swap_tool.swap(db, names)
    db.commit()
    assert _impacts(db) == [77]
    assert _impacts(db, "impact_scores_v3") == [10]
    assert _impacts(db, "impact_scores_v1") == [10]
    assert result["previous_table"] == "impact_scores_v3"
    constraints = {c for (c,) in db.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'impact_scores_v3'::regclass"))}
    assert "impact_scores_v3_pkey" in constraints
    assert swap_tool.state(db, names)["tables"] == {
        "impact_scores": True, "impact_scores_new": False, "impact_scores_v3": True,
        "impact_scores_v4_rolled_back": False}


def test_a_v4_rollback_restores_from_its_own_previous_table(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    _v1_retained(db)
    names = SwapNames.validated(**V4_NAMES)
    swap_tool.swap(db, names)
    db.commit()
    result = swap_tool.rollback(db, names)
    db.commit()
    assert _impacts(db) == [10]
    assert _impacts(db, "impact_scores_v4_rolled_back") == [77]
    assert _impacts(db, "impact_scores_v1") == [10]
    assert result["previous_table"] == "impact_scores_v3"
    assert _log(db)[-1] == ("rollback", "rolled back")


def test_a_swap_entry_without_a_players_digest_is_not_read_as_drift(db, tmp_path):
    """Code review, finding 2. A swap recorded before `players` joined
    SOURCE_TABLES has no digest for it, and comparing None against the current
    digest made every such rollback refuse with a drift that never happened --
    during exactly the incident rollback exists for."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    names = SwapNames.validated(**V4_NAMES)
    swap_tool.swap(db, names)
    db.commit()
    entry = db.execute(text(
        f"SELECT id, details FROM {swap_tool.LOG} WHERE operation = 'swap' AND outcome = 'swapped' "
        "ORDER BY id DESC LIMIT 1")).one()
    details = dict(entry.details)
    details["row_digests"] = {k: v for k, v in details["row_digests"].items() if k != "players"}
    db.execute(text(f"UPDATE {swap_tool.LOG} SET details = CAST(:d AS jsonb) WHERE id = :i"),
               {"d": swap_tool.canonical_json(details), "i": entry.id})
    db.commit()

    result = swap_tool.rollback(db, names)
    db.commit()
    assert result["source_drift"] == []
    assert result["source_digests_not_recorded"] == ["players"]
    assert _impacts(db) == [10]


def test_a_real_change_to_players_is_still_drift(db, tmp_path):
    """The back-compat above must not swallow a rename the swap DID record."""
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    names = SwapNames.validated(**V4_NAMES)
    swap_tool.swap(db, names)
    db.commit()
    db.execute(text("UPDATE players SET display_name = display_name || '-x' "
                    "WHERE id = (SELECT player_id FROM match_players WHERE id = :m)"),
               {"m": keys[0][1]})
    db.commit()
    with pytest.raises(swap_tool.Refused, match="players changed since the swap"):
        swap_tool.rollback(db, names)
    db.rollback()


def test_state_lists_every_impact_scores_table_it_was_not_told_about(db, tmp_path):
    """Code review, finding 4. `state` is how an operator finds out whether a
    database was already swapped, so a table under a name they did not pass
    must still be visible."""
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, SwapNames.validated(**V4_NAMES))
    db.commit()
    wrong = SwapNames.validated(previous="impact_scores_v1",
                                rolled_back="impact_scores_rc3_rolled_back")
    reported = swap_tool.state(db, wrong)
    assert reported["tables"]["impact_scores_v1"] is False
    assert "impact_scores_v3" in reported["other_impact_scores_tables"]


def test_a_rollback_naming_a_different_previous_table_than_the_swap_is_refused(db, tmp_path):
    _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, SwapNames.validated(**V4_NAMES))
    db.commit()
    db.execute(text("CREATE TABLE impact_scores_v9 (LIKE impact_scores INCLUDING DEFAULTS)"))
    db.commit()
    other = SwapNames.validated(previous="impact_scores_v9", rolled_back="impact_scores_v4_rolled_back")
    with pytest.raises(swap_tool.Refused, match="impact_scores_v3"):
        swap_tool.rollback(db, other)
    db.rollback()
    db.execute(text("DROP TABLE impact_scores_v9"))
    db.commit()

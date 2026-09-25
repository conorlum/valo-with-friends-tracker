from types import SimpleNamespace

from app.services.match_streaks import FormEntry, compute_form, form_entry


def _entries(results: str) -> list[FormEntry]:
    return [
        FormEntry(result=r, external_id=f"m{i}", map_name="Haven", own_rounds=13, enemy_rounds=9, played_at=None)
        for i, r in enumerate(results)
    ]


def test_empty_history():
    form = compute_form([])
    assert (form.current_result, form.current_length, form.longest_win, form.longest_loss) == (None, 0, 0, 0)
    assert form.recent == []


def test_current_and_longest_streaks():
    form = compute_form(_entries("WWWLLWLLLLWW"))
    assert (form.current_result, form.current_length) == ("W", 2)
    assert form.longest_win == 3
    assert form.longest_loss == 4
    assert form.total_matches == 12


def test_draw_breaks_every_streak():
    form = compute_form(_entries("WWDWW"))
    assert form.longest_win == 2
    assert (form.current_result, form.current_length) == ("W", 2)
    form = compute_form(_entries("LLD"))
    assert (form.current_result, form.current_length) == (None, 0)
    assert form.longest_loss == 2


def test_recent_is_newest_first_and_capped():
    form = compute_form(_entries("L" + "W" * 11), recent_count=10)
    assert [e.external_id for e in form.recent] == [f"m{i}" for i in range(11, 1, -1)]
    assert compute_form(_entries("WL"), recent_count=0).recent == []


def test_form_entry_scores_from_the_players_side():
    match = SimpleNamespace(external_id="x", map_name="Bind", played_at=None, team1_rounds_won=9, team2_rounds_won=13)
    assert (form_entry(match, "team-2", True).own_rounds, form_entry(match, "team-2", True).enemy_rounds) == (13, 9)
    entry = form_entry(match, "team-1", False)
    assert (entry.result, entry.own_rounds, entry.enemy_rounds) == ("L", 9, 13)
    assert form_entry(match, "team-1", None).result == "D"

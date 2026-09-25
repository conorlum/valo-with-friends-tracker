"""Match win/loss streaks ("form"): current streak, longest win and loss
streaks, and the last few results, from a chronological list of matches.

Not to be confused with app.services.map_streaks, which tracks how long a
map has gone unplayed. A draw ends any streak without starting one."""
from dataclasses import dataclass, field
from datetime import datetime

# The Form card lists this many recent matches (the first 10 show until
# "Show all" is clicked -- see _recent_match_rows.html). Matches the
# stand-out badges' baseline window, recent_match_rows.BASELINE_MATCHES.
RECENT_FORM_COUNT = 30


@dataclass
class FormEntry:
    result: str  # "W", "L", or "D"
    external_id: str
    map_name: str | None
    own_rounds: int
    enemy_rounds: int
    played_at: datetime | None


@dataclass
class Form:
    # "W"/"L" for the streak the latest match extends, None when there are
    # no matches or the latest one was a draw.
    current_result: str | None = None
    current_length: int = 0
    longest_win: int = 0
    longest_loss: int = 0
    total_matches: int = 0
    # Newest first, at most RECENT_FORM_COUNT.
    recent: list[FormEntry] = field(default_factory=list)


def form_entry(match, team: str, win: bool | None) -> FormEntry:
    """`match` is a live Match or a CachedMatchRef -- both expose
    external_id, map_name, played_at and team1/team2_rounds_won."""
    own, enemy = (
        (match.team1_rounds_won, match.team2_rounds_won)
        if team == "team-1"
        else (match.team2_rounds_won, match.team1_rounds_won)
    )
    return FormEntry(
        result="D" if win is None else "W" if win else "L",
        external_id=match.external_id,
        map_name=match.map_name,
        own_rounds=own,
        enemy_rounds=enemy,
        played_at=match.played_at,
    )


def compute_form(entries: list[FormEntry], recent_count: int = RECENT_FORM_COUNT) -> Form:
    """`entries` must be oldest first."""
    form = Form(total_matches=len(entries))
    run_result: str | None = None
    run_length = 0
    for entry in entries:
        if entry.result == "D":
            run_result, run_length = None, 0
            continue
        if entry.result == run_result:
            run_length += 1
        else:
            run_result, run_length = entry.result, 1
        if run_result == "W":
            form.longest_win = max(form.longest_win, run_length)
        else:
            form.longest_loss = max(form.longest_loss, run_length)
    form.current_result = run_result
    form.current_length = run_length
    form.recent = list(reversed(entries[-recent_count:])) if recent_count else []
    return form

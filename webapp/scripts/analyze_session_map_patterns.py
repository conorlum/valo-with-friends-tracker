"""
Tests a path not yet explored for the map-diversity algorithm: does map
repetition get suppressed much more strongly WITHIN a single continuous
session (the same roster queuing multiple games back to back) than what
analyze_map_diversity.py found by pooling one account's entire history
across many different, mostly solo-queue lobbies? If Riot's diversity
mechanism tracks the *party* rather than the individual account, that
signal would be almost completely diluted in the per-account analysis but
should show up clearly here.

Reuses the app's own session-grouping logic (app/services/sessions.py) --
"session" means exactly what the Sessions page means: consecutive matches
sharing 3+ players within a same-night window. Uses ALL matches in the DB
(not just deep-crawled players), since this only needs roster overlap +
timing, not a gap-free individual history.

Run:
    .venv\\Scripts\\python.exe scripts\\analyze_session_map_patterns.py
"""

import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import Match, MatchPlayer, Player
from app.services.sessions import SessionMatchPlayer, group_matches_into_sessions


def main() -> None:
    db = SessionLocal()
    try:
        matches = (
            db.query(Match).filter(Match.played_at.isnot(None)).order_by(Match.played_at).all()
        )
        match_ids = [m.id for m in matches]
        rows = (
            db.query(MatchPlayer.match_id, MatchPlayer.player_id, MatchPlayer.team, Player.display_name)
            .join(Player, Player.id == MatchPlayer.player_id)
            .filter(MatchPlayer.match_id.in_(match_ids))
            .all()
        )
        match_players_by_match: dict[int, list[SessionMatchPlayer]] = {}
        for match_id, player_id, team, display_name in rows:
            match_players_by_match.setdefault(match_id, []).append(
                SessionMatchPlayer(
                    player_id=player_id,
                    team=team.value if hasattr(team, "value") else team,
                    display_name=display_name,
                )
            )

        roster_sessions = group_matches_into_sessions(matches, match_players_by_match)
        multi = [s for s in roster_sessions if s.is_multi_match]
        print(f"total sessions: {len(roster_sessions)}, multi-match sessions: {len(multi)}")

        lengths = Counter(len(s.matches) for s in multi)
        print("session length distribution (matches per multi-match session):", dict(sorted(lengths.items())))

        session_gaps: list[int] = []
        adjacent_repeats = 0
        adjacent_total = 0
        for s in multi:
            maps = [m.map_name for m in s.matches]
            last_seen: dict[str, int] = {}
            for i, map_name in enumerate(maps):
                if map_name in last_seen:
                    session_gaps.append(i - last_seen[map_name])
                last_seen[map_name] = i
            for i in range(1, len(maps)):
                adjacent_total += 1
                if maps[i] == maps[i - 1]:
                    adjacent_repeats += 1

        print(
            f"\nadjacent (gap=1 WITHIN a session) repeat rate: "
            f"{adjacent_repeats}/{adjacent_total}"
            + (f" = {adjacent_repeats / adjacent_total:.4f}" if adjacent_total else " (no adjacent pairs)")
        )
        print("(compare to ~0.063 found pooling individual accounts' full history in analyze_map_diversity.py)")
        print("\nwithin-session repeat-gap distribution:", dict(sorted(Counter(session_gaps).items())))

    finally:
        db.close()


if __name__ == "__main__":
    main()

"""
Follow-up to analyze_session_map_patterns.py: for every within-session
adjacent-match map repeat, shows exactly how many players were shared
between the two matches (not just that the session's grouping threshold
of 3+ was met) -- a "repeat" between two games sharing only 3 of 5 players
is a much less surprising event than one where the exact same 5-stack got
the same map twice.

Run:
    .venv\\Scripts\\python.exe scripts\\inspect_session_repeats.py
"""

import sys
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

        from collections import Counter

        available_by_shared = Counter()
        repeat_by_shared = Counter()

        for s in multi:
            for i in range(1, len(s.matches)):
                prev, cur = s.matches[i - 1], s.matches[i]
                prev_ids = {mp.player_id for mp in match_players_by_match.get(prev.id, [])}
                cur_ids = {mp.player_id for mp in match_players_by_match.get(cur.id, [])}
                shared_count = len(prev_ids & cur_ids)
                available_by_shared[shared_count] += 1
                if prev.map_name == cur.map_name:
                    repeat_by_shared[shared_count] += 1

                    shared = prev_ids & cur_ids
                    shared_names = sorted(
                        mp.display_name
                        for mp in match_players_by_match.get(cur.id, [])
                        if mp.player_id in shared
                    )
                    print(f"REPEAT: {cur.map_name}")
                    print(f"  {prev.played_at} (match {prev.id}) -> {cur.played_at} (match {cur.id})")
                    print(f"  shared players ({len(shared)}): {shared_names}")
                    print()

        print("=" * 60)
        print("adjacent-pair repeat rate by number of shared players:")
        for shared_count in sorted(available_by_shared):
            n = available_by_shared[shared_count]
            k = repeat_by_shared.get(shared_count, 0)
            print(f"  {shared_count} shared: {k}/{n} = {k / n:.4f}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

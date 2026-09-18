"""Do the cached numbers still agree with the scores they came from?

`swap_impact_scores.py verify-live` proves the TABLE. `verify_player_cache_coverage.py`
proves each cache row exists, carries the running code's `cache_version()` and decodes.
Neither compares the cached NUMBERS to the scores behind them, so a blob that decodes
cleanly and disagrees with `impact_scores` is served to a reader without complaint
(open item 1; runbook 6.4b).

This reads each player's cache rows back and re-derives the same projection straight
from the score rows, in ONE repeatable-read snapshot, then compares. It never writes.

WHAT IT PROVES
    For every requested player and both scopes: the ordered set of matches in the
    cached profile, each match's average impact / kill impact / death impact, and the
    overall round-weighted average impact and death impact, all equal what the score
    rows say right now.

WHAT IT DOES NOT PROVE -- do not describe this as whole-blob equality:
    - offsetting differences that leave an average unchanged;
    - the state diagrams, fight-EV data, econ products, highlights or trade detail;
    - differences below --tolerance;
    - anything that changes after this snapshot is taken.

Run it from the checkout that is deployed: `decode_cache_row` rejects any row whose
version is not the one the running code computes, so a mismatched checkout reports
every player as missing rather than silently comparing the wrong generation.

    DATABASE_URL=... python scripts/verify_cache_matches_scores.py --ids-file roster-ids.txt

Exit codes: 0 every player-scope agrees; 1 a disagreement, a missing row, or a
requested player that does not exist; 3 connected to a database other than
--expect-database.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal
from app.models import ImpactScore, Match, MatchPlayer, Player, Round
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.player_view_cache import PlayerViewCache, cache_version, decode_cache_row

#: Averages are compared pre-display, so the only difference tolerated is float
#: summation order. Anything a real scoring change would produce is far larger.
DEFAULT_TOLERANCE = 1e-9

SCOPES = ("recent", "career")


def _scope_match_player_ids(ordered: list[tuple], scope: str) -> list[tuple]:
    """The scope's window, OLDEST FIRST, taken before unscored matches are dropped.

    Two separate conventions, and getting either backwards produces a difference
    that looks real:

    1. The window is chosen NEWEST first -- `ordered` arrives
       `played_at DESC NULLS FIRST, id DESC`, and the recent scope is its first
       `RECENT_MATCH_LIMIT` entries. It is taken BEFORE unscored matches are
       dropped, because `build_player_profile_from_match_data` skips a
       match_player with no score rows (`if not scores: continue`) only after the
       window exists. Filtering first would pull an older match in.
    2. The result is then REVERSED, because `PlayerProfile.matches` is oldest
       first -- `player_profile_types.build_player_profile_from_match_data`'s
       docstring pins that ("callers must pass oldest-first"), and the router
       displays `reversed(profile.matches)`. `get_player_profile` does exactly
       this: newest N, then `reversed(...)`.
    """
    window = ordered[:RECENT_MATCH_LIMIT] if scope == "recent" else ordered
    return list(reversed(window))


def _ordered_match_players(db, player_ids: list[int]) -> dict[int, list[tuple]]:
    """Per player: (match_player_id, match_id, external_id) newest match first.

    The ORDER BY is `played_at DESC NULLS FIRST, id DESC`, matching
    `load_player_match_data`'s pinned convention that an unknown `played_at` counts
    as the newest match. Derived here independently of that function, on purpose:
    if this agreed with the page by calling the page's own loader, it would prove
    nothing about the page.
    """
    rows = db.execute(
        sa.select(MatchPlayer.player_id, MatchPlayer.id, Match.id, Match.external_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(MatchPlayer.player_id.in_(player_ids))
        .order_by(MatchPlayer.player_id,
                  Match.played_at.desc().nullsfirst(),
                  Match.id.desc())
    ).all()
    out: dict[int, list[tuple]] = defaultdict(list)
    for player_id, mp_id, match_id, external_id in rows:
        out[player_id].append((mp_id, match_id, external_id))
    return out


def _score_aggregates(db, match_player_ids: list[int]) -> dict[int, tuple]:
    """Per match_player: (rows, sum impact, sum kill_impact, sum death_impact).

    Sums rather than averages, so the overall figures can be pooled across matches
    row-weighted -- which is what the profile does (it extends one flat list and
    divides once at the end), not a mean of per-match means.
    """
    if not match_player_ids:
        return {}
    rows = db.execute(
        sa.select(ImpactScore.match_player_id,
                  sa.func.count(),
                  sa.func.sum(ImpactScore.impact),
                  sa.func.sum(ImpactScore.kill_impact),
                  sa.func.sum(ImpactScore.death_impact))
        .join(Round, Round.id == ImpactScore.round_id)
        .where(ImpactScore.match_player_id.in_(match_player_ids))
        .group_by(ImpactScore.match_player_id)
    ).all()
    return {mp_id: (n, float(si or 0.0), float(sk or 0.0), float(sd or 0.0))
            for mp_id, n, si, sk, sd in rows}


def _compare_player_scope(player, scope, cached, ordered, aggregates, tolerance):
    """Every difference for one player-scope, as a list of readable strings."""
    problems = []
    window = _scope_match_player_ids(ordered, scope)

    expected = []           # (external_id, avg impact, avg kill, avg death)
    rows = imp = dth = 0.0
    total_rows = 0
    for mp_id, _match_id, external_id in window:
        agg = aggregates.get(mp_id)
        if not agg:
            continue        # unscored: the profile skips it too
        n, s_impact, s_kill, s_death = agg
        expected.append((external_id, s_impact / n, s_kill / n, s_death / n))
        total_rows += n
        imp += s_impact
        dth += s_death
    rows = total_rows

    got = [(m.match.external_id, m.average_impact, m.average_kill_impact, m.average_death_impact)
           for m in cached.profile.matches]

    exp_ids = [e[0] for e in expected]
    got_ids = [g[0] for g in got]
    if exp_ids != got_ids:
        missing = [i for i in exp_ids if i not in set(got_ids)]
        extra = [i for i in got_ids if i not in set(exp_ids)]
        detail = f"{len(got_ids)} cached vs {len(exp_ids)} from scores"
        if missing:
            detail += f"; absent from cache: {missing[:3]}"
        if extra:
            detail += f"; cached but not in scope: {extra[:3]}"
        if not missing and not extra:
            detail += "; same matches in a different order"
        problems.append(f"{player.display_name} [{scope}] match list differs -- {detail}")
        return problems     # per-match comparison below would be meaningless

    for (ext, e_imp, e_kill, e_death), (_g, g_imp, g_kill, g_death) in zip(expected, got):
        for field, e, g in (("average_impact", e_imp, g_imp),
                            ("average_kill_impact", e_kill, g_kill),
                            ("average_death_impact", e_death, g_death)):
            if abs(e - g) > tolerance:
                problems.append(
                    f"{player.display_name} [{scope}] match {ext} {field}: "
                    f"cached {g!r} != scores {e!r} (delta {abs(e - g):.3e})")

    for field, e, g in (
            ("overall_average_impact", imp / rows if rows else 0.0,
             cached.profile.overall_average_impact),
            ("overall_average_death_impact", dth / rows if rows else 0.0,
             cached.profile.overall_average_death_impact)):
        if abs(e - g) > tolerance:
            problems.append(
                f"{player.display_name} [{scope}] {field}: cached {g!r} != scores {e!r} "
                f"(delta {abs(e - g):.3e}, over {rows} score rows)")
    return problems


def verify(db, player_ids: list[int], tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """{'problems': [...], 'checked': n} -- never raises on a disagreement."""
    players = {p.id: p for p in db.query(Player).filter(Player.id.in_(player_ids)).all()}
    problems = [f"player id {pid} does not exist" for pid in player_ids if pid not in players]

    ordered_by_player = _ordered_match_players(db, list(players))
    all_mp_ids = [mp_id for rows in ordered_by_player.values() for mp_id, _m, _e in rows]
    aggregates = _score_aggregates(db, all_mp_ids)

    cache_rows = {
        (row.player_id, row.scope): row
        for row in db.query(PlayerViewCache).filter(PlayerViewCache.player_id.in_(list(players))).all()
    }

    checked = 0
    for pid in player_ids:
        player = players.get(pid)
        if player is None:
            continue
        for scope in SCOPES:
            cached = decode_cache_row(cache_rows.get((pid, scope)), player)
            if cached is None:
                problems.append(
                    f"{player.display_name} [{scope}]: no usable cache row "
                    f"(absent, or not version {cache_version()} -- run this from the deployed checkout)")
                continue
            problems.extend(_compare_player_scope(
                player, scope, cached, ordered_by_player.get(pid, []), aggregates, tolerance))
            checked += 1
    return {"problems": problems, "checked": checked}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids-file", required=True, help="one player id per line")
    parser.add_argument("--expect-database", help="refuse any other database")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    with open(args.ids_file, encoding="utf-8") as handle:
        player_ids = [int(line) for line in handle if line.strip()]

    db = SessionLocal()
    try:
        # The snapshot comes FIRST, before any other statement. SET TRANSACTION
        # ISOLATION LEVEL must be a transaction's first statement, and a query as
        # innocuous as SELECT current_database() opens one -- which is exactly how
        # verify-build broke (see write_gate.py's checkout listener). Reading the
        # database name inside the snapshot is equally good: it is still a read,
        # and the name cannot change under us.
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        database = db.execute(sa.text("SELECT current_database()")).scalar()
        print(f"database {database}")
        if args.expect_database and database != args.expect_database:
            print(f"REFUSED: connected to {database}, but this step expects {args.expect_database}")
            return 3
        result = verify(db, player_ids, args.tolerance)
    finally:
        db.rollback()
        db.close()

    for problem in result["problems"]:
        print(f"  {problem}")
    if result["problems"]:
        print(f"CACHE DISAGREES WITH SCORES: {len(result['problems'])} problem(s), "
              f"{result['checked']} player-scope(s) compared")
        return 1
    print(f"cache agrees with scores: {result['checked']} player-scopes, "
          f"{len(player_ids)} players, tolerance {args.tolerance:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

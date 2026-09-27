"""Links a stored replay to a crawled match, or refuses: never misaligns.

Pure: it works on plain objects (the stored replay's players and `link_inputs`, and the DB
rows as dataclasses), not on a session. Stage 2 adds the DB layer that loads those rows
under the advisory lock and writes the result. The steps follow
docs/replay-viewer-plan.md, "Linking":

0. the replay's eligibility (pass 6, P-f): no link-blocking diagnostic, coverage and gap
   limits met, the phase cycle and lifecycle validated;
1. exactly one match with `external_id == match_uuid`, else unlinked (none) or refused;
2. equal played-round sets, complete for the mode's score or a detected surrender (P-e);
   when the replay decoded round winners, each equal to the DB's;
3. exactly one assignment of slots to match players that respects agents, the DB's teams
   (every replay kill cross-team) and the kills; proximity (spawn points, tight round-start
   clusters, the condenser's side groups) only checks the survivor (pass 6, P-d);
4. per-round kill sequences identical after the excluded DB kill classes, paired out of
   time order only within MAX_RESIDUAL_S on both clocks (P-g);
5. one match-wide clock offset within the limits, with an anchor in every round;
6. the `riot_subject` backfill, refused on any conflict, for the slots that have a Subject.

The report holds IDs, counts, agents and reasons only: never a Subject or a name.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median

from app.replays.condense import SPAWN_CLUSTER_RADIUS

# Clock limits, frozen at the Stage 1b gate from six competitive matches (2026-09-27).
MAX_ABS_OFFSET_S = 2.0
MIN_ANCHORS = 20
MAX_RESIDUAL_S = 0.75
MAX_MEDIAN_RESIDUAL_S = 0.25
# The decoded RoundResults WinningTeam FName per DB team
# (tracker.gg's Red is team-1: trackergg_browserstate_source._team_for).
WINNER_FNAME_TO_TEAM = {"Red": "team-1", "Blue": "team-2"}
# A legal final score per mode (winner's rounds, loser's rounds). The crawl
# keeps Competitive only; Swiftplay is here for completeness and the synthetic tests.
MODES = ("competitive", "swiftplay")


def is_legal_end(mode: str, winner: int, loser: int) -> bool:
    if mode == "competitive":
        return (winner == 13 and loser <= 11) or (winner >= 14 and loser >= 12 and winner - loser == 2)
    if mode == "swiftplay":
        return winner == 5 and loser <= 4
    raise Refused("completeness", "no completeness rule for this mode", mode=mode)


@dataclass(frozen=True)
class DbPlayer:
    match_player_id: int
    player_id: int
    agent: str
    team: str                 # 'team-1' | 'team-2'
    riot_subject: str | None = None


@dataclass(frozen=True)
class DbRound:
    number: int
    outcome: str | None       # "Team A Elimination Win" (A = team-1), "... Surrendered Win"
    plant_time: float | None = None
    defuse_time: float | None = None


@dataclass(frozen=True)
class DbKill:
    id: int
    round_number: int
    t: float                  # kill_events.event_time_seconds
    killer: int | None        # match_player_id
    victim: int | None


@dataclass
class DbMatch:
    match_id: int
    external_id: str
    team1_rounds_won: int
    team2_rounds_won: int
    rounds: list[DbRound]
    players: list[DbPlayer]
    kills: list[DbKill]
    mode: str = "competitive"  # the tracker.gg crawl keeps Competitive matches only


@dataclass
class ReplayLinkView:
    """What the linker reads from a stored replay: `replay_players` rows and `link_inputs`."""
    match_uuid: str
    round_count: int
    dropped_final_round: bool
    players: list[dict]       # {slot, subject, agent, side_group}
    link_inputs: dict


@dataclass
class LinkResult:
    status: str               # 'linked' | 'unlinked' | 'refused'
    report: dict
    match_id: int | None = None
    clock_offset: float | None = None
    kill_map: dict[str, list[int | None]] = field(default_factory=dict)
    db_deaths: dict[str, list[dict]] = field(default_factory=dict)
    slot_to_match_player: dict[int, int] = field(default_factory=dict)
    side_to_team: dict[str, str] = field(default_factory=dict)
    backfills: list[tuple[int, str]] = field(default_factory=list)  # (player_id, subject); private


class Refused(Exception):
    def __init__(self, check: str, detail: str, **facts):
        super().__init__(f"{check}: {detail}")
        self.check, self.detail, self.facts = check, detail, facts


def is_surrender_round(outcome: str | None) -> bool:
    return bool(outcome) and outcome.endswith("Surrendered Win")


def outcome_winner(outcome: str | None) -> str | None:
    """The adapter writes "Team A ..." for team-1 (Red) and "Team B ..." for team-2."""
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


# ---------------------------------------------------------------- step 2: rounds


def check_completeness(match: DbMatch) -> bool:
    """P-e: the DB's played rounds against its awarded score and mode. Returns whether the match
    is a detected surrender; refuses anything else that isn't a complete match.

    The adapter drops tracker.gg's surrender padding rows but keeps its padded `roundsWon`, so a
    surrender shows rows that reach no legal end under an awarded score that does.
    The surrender shape is confirmed by finding 26.
    """
    wins = Counter(outcome_winner(r.outcome) for r in match.rounds)
    if wins[None]:
        raise Refused("completeness", "a DB round has no winner", rounds=wins[None])
    rows = (wins["team-1"], wins["team-2"])
    awarded = (match.team1_rounds_won, match.team2_rounds_won)
    if rows[0] != rows[1] and is_legal_end(match.mode, max(rows), min(rows)):
        if rows != awarded:
            raise Refused("score", "the played rounds reach a final score the DB's awarded score differs from",
                          rows=list(rows), awarded=list(awarded))
        return False
    if awarded[0] == awarded[1] or not is_legal_end(match.mode, max(awarded), min(awarded)):
        raise Refused("completeness", "the played rounds are not a complete match and the awarded score is no "
                                      "legal end", rows=list(rows), awarded=list(awarded))
    winner = 0 if awarded[0] > awarded[1] else 1
    if awarded[1 - winner] != rows[1 - winner] or awarded[winner] < rows[winner]:
        raise Refused("completeness", "the played rounds don't fit a surrender under the awarded score",
                      rows=list(rows), awarded=list(awarded))
    return True


def check_rounds(replay: ReplayLinkView, match: DbMatch) -> tuple[list[int], bool]:
    """(the played rounds, whether the DB match is a detected surrender)."""
    db_played = sorted(r.number for r in match.rounds)
    replay_played = list(range(1, replay.round_count + 1))
    if db_played != replay_played:
        raise Refused("rounds", "played round sets differ", replay_rounds=len(replay_played), db_rounds=len(db_played))
    surrender = check_completeness(match)
    if replay.dropped_final_round and not surrender:
        raise Refused("rounds", "the replay's final round has no RoundEnding, and the DB match is no surrender")
    if replay.link_inputs.get("round_results_fallback"):
        raise Refused("round_results", "RoundResults fell back to a raw payload; no winner source")
    if not replay.link_inputs.get("round_results"):
        # No decoded winners at all (the mode's game state isn't decoded): nothing to compare.
        return replay_played, surrender
    db_rounds = {r.number: r for r in match.rounds}
    wins = Counter()
    for n in replay_played:
        decoded = (replay.link_inputs.get("round_results") or {}).get(str(n)) or {}
        replay_winner = WINNER_FNAME_TO_TEAM.get(decoded.get("WinningTeam"))
        if replay_winner is None:
            raise Refused("round_results", "a round has no decoded winner", round=n,
                          fname=decoded.get("WinningTeam"))
        if replay_winner != outcome_winner(db_rounds[n].outcome):
            raise Refused("round_results", "a round's winner differs from the DB", round=n)
        wins[replay_winner] += 1
    rows = Counter(outcome_winner(r.outcome) for r in match.rounds)
    if (wins["team-1"], wins["team-2"]) != (rows["team-1"], rows["team-2"]):
        raise Refused("score", "the replay's round winners give another score than the DB's rounds",
                      replay=[wins["team-1"], wins["team-2"]], db=[rows["team-1"], rows["team-2"]])
    return replay_played, surrender


def check_eligibility(replay: ReplayLinkView) -> None:
    """P-f step 0: the condenser's eligibility record must be clean (the same check `--dry-run`
    makes before it loads a row)."""
    eligibility = replay.link_inputs.get("eligibility")
    if not eligibility:
        raise Refused("eligibility", "the replay carries no eligibility record")
    if not eligibility.get("eligible"):
        raise Refused("eligibility", "the replay is not eligible to link", reasons=eligibility.get("reasons", []))


# ---------------------------------------------------------------- step 3: teams and players


def check_players(replay: ReplayLinkView) -> None:
    named = [p["subject"] for p in replay.players if p.get("subject")]
    if len(replay.players) != 10 or len(set(named)) != len(named) or any(not p.get("agent") for p in replay.players):
        raise Refused("players", "expected 10 players with one agent each and no shared Subject")


def assignment_candidates(replay: ReplayLinkView, match: DbMatch) -> list[dict[int, int]]:
    """Every slot -> match_player_id that respects agents, anchors and the DB's teams.

    Teams come from the DB only (P-d): every agent-respecting bijection over all ten players,
    both teams at once, in which every replay kill (self-kills aside) is cross-team. The
    condenser's side groups and spawn proximity are not constraints, so they can't exclude
    the true assignment; `check_proximity` tests the survivor against them instead.
    """
    anchors = {p.riot_subject: p.match_player_id for p in match.players if p.riot_subject}
    team_of = {p.match_player_id: p.team for p in match.players}
    subject_of = {p["slot"]: p["subject"] for p in replay.players}
    kill_pairs = {(killer, victim) for kills in (replay.link_inputs.get("kills") or {}).values()
                  for _, killer, victim in kills if killer != victim}
    candidates = []
    for mapping in _bijections(replay.players, match.players):
        if any(team_of[mapping[killer]] == team_of[mapping[victim]] for killer, victim in kill_pairs):
            continue
        # A slot without a Subject is free; one with a Subject must sit on its anchor, if any.
        if all(subject_of[slot] is None or anchors.get(subject_of[slot], mp) == mp
               for slot, mp in mapping.items()) and \
                all(subject_of[slot] is None or mp not in anchors.values() or anchors.get(subject_of[slot]) == mp
                    for slot, mp in mapping.items()):
            candidates.append(mapping)
    return candidates


def pinning(replay: ReplayLinkView, survivors: list[dict[int, int]]) -> dict:
    """Which slots every survivor maps to the same match player (pinned), and which not."""
    agent_of = {p["slot"]: p["agent"] for p in replay.players}
    unpinned = sorted(slot for slot in agent_of if len({m[slot] for m in survivors}) > 1) if survivors else []
    return {"pinned": sorted(set(agent_of) - set(unpinned)) if survivors else [],
            "unpinned": [{"slot": slot, "agent": agent_of[slot]} for slot in unpinned]}


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def check_proximity(replay: ReplayLinkView, match: DbMatch, mapping: dict[int, int]) -> dict[str, str]:
    """Checks the surviving assignment against the spatial evidence; returns side -> team.

    - match-start spawn points, partitioned by the survivor's teams: each team within
      SPAWN_CLUSTER_RADIUS of its own centroid, and every slot nearer its own team's centroid;
    - each tight round-start cluster: every slot nearer its own team's centroid;
    - the condenser's side groups, when resolved: one side per team.
    Any disagreement refuses. None of it ever removed a candidate.
    """
    team_of_slot = {slot: next(p.team for p in match.players if p.match_player_id == mp)
                    for slot, mp in mapping.items()}

    def centroid_rule(points: dict[int, tuple[float, float]], where: dict, radius: float | None) -> None:
        by_team: dict[str, list[int]] = defaultdict(list)
        for slot in points:
            by_team[team_of_slot[slot]].append(slot)
        if len(by_team) != 2:
            raise Refused("proximity", "the spatial evidence doesn't hold both teams", **where)
        centres = {team: _centroid([points[s] for s in slots]) for team, slots in by_team.items()}
        for team, slots in by_team.items():
            other = next(t for t in centres if t != team)
            for slot in slots:
                own = math.dist(points[slot], centres[team])
                if radius is not None and own > radius:
                    raise Refused("proximity", "a team's spawn points are not one tight group", slot=slot, **where)
                if own > math.dist(points[slot], centres[other]):
                    raise Refused("proximity", "a slot sits nearer the other team", slot=slot, **where)

    spawn = replay.link_inputs.get("spawn_points") or {}
    if len(spawn) == 10:
        centroid_rule({int(s): tuple(xy) for s, xy in spawn.items()}, {"evidence": "match_start_spawns"},
                      SPAWN_CLUSTER_RADIUS)
    for n, positions in (replay.link_inputs.get("start_positions") or {}).items():
        if len(positions) == 10:
            centroid_rule({int(s): tuple(uv) for s, uv in positions.items()},
                          {"evidence": "round_start_cluster", "round": int(n)}, None)
    sides = {p["slot"]: p.get("side_group") for p in replay.players}
    if all(sides.values()):
        side_to_team: dict[str, str] = {}
        for slot, side in sides.items():
            if side_to_team.setdefault(side, team_of_slot[slot]) != team_of_slot[slot]:
                raise Refused("proximity", "the condenser's side groups disagree with the teams", slot=slot)
        if len(set(side_to_team.values())) != len(side_to_team):
            raise Refused("proximity", "both side groups map to one team")
        return dict(sorted(side_to_team.items()))
    return {}


def _bijections(slots: list[dict], players: list[DbPlayer]) -> list[dict[int, int]]:
    """Every agent-respecting slot -> match_player_id bijection."""
    if len(slots) != len(players):
        return []
    if Counter(s["agent"] for s in slots) != Counter(p.agent for p in players):
        return []
    by_agent_slots = defaultdict(list)
    by_agent_players = defaultdict(list)
    for s in slots:
        by_agent_slots[s["agent"]].append(s["slot"])
    for p in players:
        by_agent_players[p.agent].append(p.match_player_id)
    options = [[dict(zip(by_agent_slots[a], perm)) for perm in itertools.permutations(by_agent_players[a])]
               for a in sorted(by_agent_slots)]
    return [{k: v for part in combo for k, v in part.items()} for combo in itertools.product(*options)]


# ---------------------------------------------------------------- step 4: kills


def split_db_kills(match: DbMatch) -> tuple[dict[int, list[DbKill]], dict[int, list[DbKill]]]:
    """(kills the replay must match, excluded kills that become db_deaths), per round."""
    team = {p.match_player_id: p.team for p in match.players}
    matched, excluded = defaultdict(list), defaultdict(list)
    for kill in sorted(match.kills, key=lambda k: (k.round_number, k.t, k.id)):
        excluded_class = (kill.killer is None or kill.killer == kill.victim
                          or (kill.victim is not None and team.get(kill.killer) == team.get(kill.victim)))
        (excluded if excluded_class else matched)[kill.round_number].append(kill)
    return matched, excluded


def match_kills(replay_kills: list[list], db_kills: list[DbKill], mapping: dict[int, int]) -> list[tuple[list, DbKill]] | None:
    """Pairs replay kills [t, killer, victim] with DB kills, or None if the sequences differ.

    Identity and order first, the clock second (P-g): each replay kill, in time order, pairs
    with the earliest unused DB kill of the same (killer, victim); the counts must agree; and
    two pairs may be out of time order only if they are within MAX_RESIDUAL_S of each other on
    both clocks. Pass 5 grouped 0.1 s ties separately on each side, which refused real jitter.
    A replay self-kill (Clove's ult running out, in the first real export) pairs with nothing,
    as the DB's are excluded.
    """
    ours = sorted((k for k in replay_kills if k[1] != k[2]), key=lambda k: k[0])
    pool = sorted(db_kills, key=lambda k: (k.t, k.id))
    if len(ours) != len(pool):
        return None
    pairs = []
    for kill in ours:
        key = (mapping[kill[1]], mapping[kill[2]])
        hit = next((d for d in pool if (d.killer, d.victim) == key), None)
        if hit is None:
            return None
        pool.remove(hit)
        pairs.append((kill, hit))
    for i, (a, db_a) in enumerate(pairs):
        for b, db_b in pairs[i + 1:]:
            if db_a.t > db_b.t and (b[0] - a[0] > MAX_RESIDUAL_S or db_a.t - db_b.t > MAX_RESIDUAL_S):
                return None
    return pairs


# ---------------------------------------------------------------- the whole link


def link(replay: ReplayLinkView, candidates: list[DbMatch], subject_owner: dict[str, int]) -> LinkResult:
    """`candidates`: the `matches` rows whose external_id equals the replay's match UUID (any case).
    `subject_owner`: every `players.riot_subject` already set in the DB -> that player's id."""
    rows = [m for m in candidates if m.external_id.lower() == replay.match_uuid.lower()]
    if not rows:
        return LinkResult("unlinked", {"reason": "no match with this external_id"})
    if len(rows) > 1:
        return LinkResult("refused", {"check": "match", "reason": "more than one match row", "count": len(rows)})
    match = rows[0]
    try:
        return _link(replay, match, subject_owner)
    except Refused as refused:
        return LinkResult("refused", {"check": refused.check, "reason": refused.detail, **refused.facts,
                                      "match_id": match.match_id})


def _link(replay: ReplayLinkView, match: DbMatch, subject_owner: dict[str, int]) -> LinkResult:
    check_eligibility(replay)
    played, surrender = check_rounds(replay, match)
    check_players(replay)
    db_matched, db_excluded = split_db_kills(match)
    replay_kills = {int(n): kills for n, kills in (replay.link_inputs.get("kills") or {}).items()}

    survivors = []
    for mapping in assignment_candidates(replay, match):
        pairs = {n: match_kills(replay_kills.get(n, []), db_matched.get(n, []), mapping) for n in played}
        if all(p is not None for p in pairs.values()):
            survivors.append((mapping, pairs))
    pins = pinning(replay, [mapping for mapping, _ in survivors])
    if len(survivors) != 1:
        raise Refused("assignment", "not exactly one assignment of slots to match players", candidates=len(survivors),
                      **pins)
    mapping, pairs = survivors[0]
    orientation = check_proximity(replay, match, mapping)
    unmatched_db = sum(len(db_matched.get(n, [])) for n in played) - sum(len(p) for p in pairs.values())
    if unmatched_db:
        raise Refused("kills", "DB kills left unmatched", count=unmatched_db)

    # Step 5: one match-wide clock offset, t_db = t_replay + offset.
    anchors = [(n, db.t - kill[0]) for n in played for kill, db in pairs[n]]
    if len(anchors) < MIN_ANCHORS:
        raise Refused("clock", "too few anchors", anchors=len(anchors), minimum=MIN_ANCHORS)
    offset = median(d for _, d in anchors)
    residuals = [(n, abs(d - offset)) for n, d in anchors]
    if abs(offset) > MAX_ABS_OFFSET_S:
        raise Refused("clock", "clock offset over the limit", offset=round(offset, 3))
    worst = max(r for _, r in residuals)
    if worst > MAX_RESIDUAL_S:
        raise Refused("clock", "a residual is over the limit", max_residual=round(worst, 3))
    typical = median(r for _, r in residuals)
    if typical > MAX_MEDIAN_RESIDUAL_S:
        raise Refused("clock", "the median residual is over the limit", median_residual=round(typical, 3))
    anchored = {n for n, r in residuals if r <= MAX_RESIDUAL_S}
    missing = [n for n in played if n not in anchored]
    if missing:
        raise Refused("clock", "a round has no anchor", rounds=missing)

    # Step 6: the riot_subject backfill, refused on any conflict.
    subject_of = {p["slot"]: p["subject"] for p in replay.players}
    db_player = {p.match_player_id: p for p in match.players}
    backfills = []
    for slot, mp in sorted(mapping.items()):
        player, subject = db_player[mp], subject_of[slot]
        if subject is None:
            continue
        if player.riot_subject is not None and player.riot_subject != subject:
            raise Refused("backfill", "a linked player already has a different Subject", match_player_id=mp)
        owner = subject_owner.get(subject)
        if owner is not None and owner != player.player_id:
            raise Refused("backfill", "the Subject already belongs to another player", match_player_id=mp)
        if player.riot_subject is None:
            backfills.append((player.player_id, subject))

    slot_of_mp = {mp: slot for slot, mp in mapping.items()}
    kill_map = {}
    for n in played:
        ids: list[int | None] = [None] * len(replay_kills.get(n, []))
        order = {id(k): i for i, k in enumerate(replay_kills.get(n, []))}
        for kill, db in pairs[n]:
            ids[order[id(kill)]] = db.id
        kill_map[str(n)] = ids
    db_deaths = {str(n): [{"slot": slot_of_mp.get(k.victim), "t_db": k.t} for k in db_excluded[n]]
                 for n in played if db_excluded.get(n)}
    report = {
        "match_id": match.match_id, "rounds": len(played), "anchors": len(anchors),
        "clock_offset": round(offset, 3), "max_residual": round(worst, 3), "median_residual": round(typical, 3),
        "excluded_db_kills": sum(len(v) for v in db_excluded.values()),
        "agents": {str(slot): db_player[mp].agent for slot, mp in sorted(mapping.items())},
        "side_to_team": orientation, "backfills": len(backfills), "pinned": pins["pinned"],
        "winners_checked": bool(replay.link_inputs.get("round_results")), "surrender": surrender,
    }
    return LinkResult("linked", report, match.match_id, round(offset, 3), kill_map, db_deaths,
                      dict(sorted(mapping.items())), orientation, backfills)

import math
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, object_session, selectinload

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import Team

# Round-win diamond: compact, with larger nodes since there's no edge label to make room for.
ROUND_WIN_X_STEP = 56
ROUND_WIN_Y_STEP = 64
ROUND_WIN_NODE_RADIUS = 28

# Kill-order diamond: edges need to be long enough to fit a label bubble at their midpoint.
KILL_ORDER_X_STEP = 118
KILL_ORDER_Y_STEP = 134
KILL_ORDER_NODE_RADIUS = 22
EDGE_BUBBLE_RADIUS = 13

PADDING = 36

_CRITICAL = (0xE0, 0x55, 0x4F)
_GOOD = (0x0C, 0xA3, 0x0C)
_NO_DATA_FILL = "#2c2c2a"


@dataclass
class GraphNode:
    id: str
    x: float
    y: float
    label: str
    sublabel: str | None
    short_label: str | None
    fill: str
    text_fill: str
    radius: float


@dataclass
class GraphEdge:
    source: str
    target: str
    weight: int
    x1: float
    y1: float
    x2: float
    y2: float
    stroke: str
    stroke_width: float
    label: str | None = None
    label_x: float | None = None
    label_y: float | None = None


@dataclass
class StateDiagram:
    nodes: list[GraphNode]
    edges: list[GraphEdge] = field(default_factory=list)
    view_box: str = "0 0 10 10"


def _pos(a: int, b: int, x_step: float, y_step: float) -> tuple[float, float]:
    """Diamond layout: x by man-advantage skew, y by total players remaining."""
    x = (a - b) * x_step / 2
    y = (10 - (a + b)) * y_step
    return x, y


def _layout(
    raw_nodes: dict[str, tuple[float, float]], node_radius: float
) -> tuple[dict[str, tuple[float, float]], str]:
    xs = [p[0] for p in raw_nodes.values()]
    ys = [p[1] for p in raw_nodes.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    shift_x = -min_x + node_radius + PADDING
    shift_y = -min_y + node_radius + PADDING
    width = (max_x - min_x) + 2 * (node_radius + PADDING)
    height = (max_y - min_y) + 2 * (node_radius + PADDING)

    positions = {state: (x + shift_x, y + shift_y) for state, (x, y) in raw_nodes.items()}
    return positions, f"0 0 {width:.0f} {height:.0f}"


def _shrink_line(x1: float, y1: float, x2: float, y2: float, gap: float) -> tuple[float, float, float, float]:
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy)
    if dist == 0:
        return x1, y1, x2, y2
    ux, uy = dx / dist, dy / dist
    return x1 + ux * gap, y1 + uy * gap, x2 - ux * gap, y2 - uy * gap


def _edge_midpoint(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Center of the edge, where the label bubble sits."""
    return (x1 + x2) / 2, (y1 + y2) / 2


def _win_color(win_pct: float) -> str:
    r = round(_CRITICAL[0] + (_GOOD[0] - _CRITICAL[0]) * win_pct)
    g = round(_CRITICAL[1] + (_GOOD[1] - _CRITICAL[1]) * win_pct)
    b = round(_CRITICAL[2] + (_GOOD[2] - _CRITICAL[2]) * win_pct)
    return f"#{r:02x}{g:02x}{b:02x}"


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _team_sizes(match_players: list[MatchPlayer]) -> dict[Team, int]:
    sizes = {Team.TEAM_1: 0, Team.TEAM_2: 0}
    for mp in match_players:
        sizes[mp.team] += 1
    return sizes


def build_state_diagrams(
    db: Session, player: Player, match_limit: int | None = None
) -> tuple[StateDiagram, StateDiagram]:
    """Rebuilds playerTrends.py's round-win and kill-order diagrams from the DB.

    The scraper's `playersOnTeam` field (alive count on each side at the moment
    of a kill) isn't stored -- kill_events only has who/what/when. So each
    round's kills are replayed in time order here, tracking alive counts per
    team, to recover the man-advantage state ("<own>v<opponent>") the player
    experienced at each kill. Aggregated across every match the player appears in.
    """
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(
            selectinload(MatchPlayer.match).selectinload(Match.match_players),
            selectinload(MatchPlayer.match).selectinload(Match.rounds).selectinload(Round.kill_events),
        )
    )
    if match_limit is not None:
        query = query.order_by(Match.played_at.desc().nullsfirst(), Match.id.desc()).limit(match_limit)
    match_players = query.all()

    win_stats: dict[str, dict[str, int]] = {}
    kill_order_weights: dict[tuple[str, str], int] = {}

    for match_player in match_players:
        match = match_player.match
        own_team = match_player.team
        opp_team = Team.TEAM_2 if own_team == Team.TEAM_1 else Team.TEAM_1
        sizes = _team_sizes(match.match_players)
        team_of = {mp.id: mp.team for mp in match.match_players}

        for round_row in match.rounds:
            alive = {Team.TEAM_1: sizes[Team.TEAM_1], Team.TEAM_2: sizes[Team.TEAM_2]}
            winner = _winner_team(round_row.outcome)
            player_alive = True
            events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))

            for event in events:
                own_alive, opp_alive = alive[own_team], alive[opp_team]
                state = f"{own_alive}v{opp_alive}"

                if player_alive and own_alive >= 1 and opp_alive >= 1 and winner is not None:
                    bucket = win_stats.setdefault(state, {"win": 0, "total": 0})
                    bucket["total"] += 1
                    if winner == own_team:
                        bucket["win"] += 1

                # Gated on player_alive: a dead player can't rack up further kills or
                # deaths -- the raw kill feed sometimes logs a duplicate entry
                # for an already-dead player, which would otherwise show up here as a
                # bogus zero-length self-loop edge.
                is_kill = player_alive and event.killer_match_player_id == match_player.id
                is_death = player_alive and event.death_match_player_id == match_player.id

                if event.death_match_player_id is not None:
                    dead_team = team_of.get(event.death_match_player_id)
                    if dead_team is not None and alive[dead_team] > 0:
                        alive[dead_team] -= 1

                if is_kill or is_death:
                    after_state = f"{alive[own_team]}v{alive[opp_team]}"
                    key = (state, after_state)
                    delta = (1 if is_kill else 0) - (1 if is_death else 0)
                    kill_order_weights[key] = kill_order_weights.get(key, 0) + delta

                if is_death:
                    player_alive = False

                if alive[Team.TEAM_1] <= 0 or alive[Team.TEAM_2] <= 0:
                    # Round is definitionally over -- stop replaying. Guards against
                    # the raw feed occasionally double-logging a death (the
                    # same artifact the import pipeline's resurrection check filters),
                    # which would otherwise clamp a team's alive count to 0 early and
                    # produce a bogus zero-length self-loop edge on later events.
                    break

    return _round_win_diagram(win_stats), _kill_order_diagram(kill_order_weights)


def build_match_round_win_diagrams(match: Match) -> tuple[StateDiagram, StateDiagram]:
    """Team-level version of build_state_diagrams's round-win diagram, scoped to one match.

    Replays each round's kills once, recording a state/outcome sample for both teams'
    perspectives simultaneously (unlike the player version, there's no player_alive gate --
    a team's own_alive count already tells you whether it's still in the round).
    """
    db = object_session(match)
    if db is not None:
        # Populates match.match_players / match.rounds / round.kill_events in bulk
        # (a few queries total) instead of one lazy-load per relationship per round.
        db.query(Match).filter_by(id=match.id).options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.kill_events),
        ).one()

    sizes = _team_sizes(match.match_players)
    team_of = {mp.id: mp.team for mp in match.match_players}

    win_stats: dict[Team, dict[str, dict[str, int]]] = {Team.TEAM_1: {}, Team.TEAM_2: {}}

    for round_row in match.rounds:
        alive = {Team.TEAM_1: sizes[Team.TEAM_1], Team.TEAM_2: sizes[Team.TEAM_2]}
        winner = _winner_team(round_row.outcome)
        events = sorted(round_row.kill_events, key=lambda e: e.event_time_seconds)

        for event in events:
            for own_team, opp_team in ((Team.TEAM_1, Team.TEAM_2), (Team.TEAM_2, Team.TEAM_1)):
                own_alive, opp_alive = alive[own_team], alive[opp_team]
                if own_alive >= 1 and opp_alive >= 1 and winner is not None:
                    state = f"{own_alive}v{opp_alive}"
                    bucket = win_stats[own_team].setdefault(state, {"win": 0, "total": 0})
                    bucket["total"] += 1
                    if winner == own_team:
                        bucket["win"] += 1

            if event.death_match_player_id is not None:
                dead_team = team_of.get(event.death_match_player_id)
                if dead_team is not None and alive[dead_team] > 0:
                    alive[dead_team] -= 1

            if alive[Team.TEAM_1] <= 0 or alive[Team.TEAM_2] <= 0:
                break

    return _round_win_diagram(win_stats[Team.TEAM_1]), _round_win_diagram(win_stats[Team.TEAM_2])


def _own_team_win_stats_for_match(match: Match, own_team: Team) -> dict[str, dict[str, int]]:
    """Replays one match's rounds from a single team's perspective, returning
    the raw win/total counts per man-advantage state that _round_win_diagram
    turns into a diagram. Shared by the session-combined and per-match
    session diagram builders below.
    """
    win_stats: dict[str, dict[str, int]] = {}
    opp_team = Team.TEAM_2 if own_team == Team.TEAM_1 else Team.TEAM_1
    sizes = _team_sizes(match.match_players)
    team_of = {mp.id: mp.team for mp in match.match_players}

    for round_row in match.rounds:
        alive = {Team.TEAM_1: sizes[Team.TEAM_1], Team.TEAM_2: sizes[Team.TEAM_2]}
        winner = _winner_team(round_row.outcome)
        events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))

        for event in events:
            own_alive, opp_alive = alive[own_team], alive[opp_team]
            if own_alive >= 1 and opp_alive >= 1 and winner is not None:
                state = f"{own_alive}v{opp_alive}"
                bucket = win_stats.setdefault(state, {"win": 0, "total": 0})
                bucket["total"] += 1
                if winner == own_team:
                    bucket["win"] += 1

            if event.death_match_player_id is not None:
                dead_team = team_of.get(event.death_match_player_id)
                if dead_team is not None and alive[dead_team] > 0:
                    alive[dead_team] -= 1

            if alive[Team.TEAM_1] <= 0 or alive[Team.TEAM_2] <= 0:
                break

    return win_stats


def _preload_session_matches(matches: list[Match]) -> None:
    if not matches:
        return
    db = object_session(matches[0])
    if db is not None:
        # Populates every match's match_players / rounds / kill_events in bulk
        # up front instead of one lazy-load per relationship per round per match.
        db.query(Match).filter(Match.id.in_([m.id for m in matches])).options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.kill_events),
        ).all()


def build_session_round_win_diagram(
    matches: list[Match], team_by_match: dict[int, str]
) -> StateDiagram:
    """Combined round-win-by-game-state diagram for a session's tracked team.

    Same replay as build_match_round_win_diagrams's per-team pass, but merges
    every match in the session into one win_stats dict from that match's
    "our team" side (team_by_match), so a session covering several matches
    against different opponents reads as one combined diagram. Matches with
    no resolvable side (team_by_match missing an entry) are skipped.
    """
    _preload_session_matches(matches)

    win_stats: dict[str, dict[str, int]] = {}
    for match in matches:
        own_team_str = team_by_match.get(match.id)
        if own_team_str is None:
            continue
        for state, bucket in _own_team_win_stats_for_match(match, Team(own_team_str)).items():
            merged = win_stats.setdefault(state, {"win": 0, "total": 0})
            merged["win"] += bucket["win"]
            merged["total"] += bucket["total"]

    return _round_win_diagram(win_stats)


def build_session_round_win_diagrams_by_match(
    matches: list[Match], team_by_match: dict[int, str]
) -> dict[int, StateDiagram]:
    """Same replay as build_session_round_win_diagram, but keeping each
    match's diagram separate (match_id -> StateDiagram) instead of merging
    them, so a session's round-win-by-game-state chart can be viewed one map
    at a time. Matches with no resolvable side (team_by_match missing an
    entry) are omitted from the result.
    """
    _preload_session_matches(matches)

    diagrams: dict[int, StateDiagram] = {}
    for match in matches:
        own_team_str = team_by_match.get(match.id)
        if own_team_str is None:
            continue
        diagrams[match.id] = _round_win_diagram(_own_team_win_stats_for_match(match, Team(own_team_str)))

    return diagrams


def _round_win_diagram(win_stats: dict[str, dict[str, int]]) -> StateDiagram:
    raw_nodes = {
        f"{a}v{b}": _pos(a, b, ROUND_WIN_X_STEP, ROUND_WIN_Y_STEP) for a in range(1, 6) for b in range(1, 6)
    }
    positions, view_box = _layout(raw_nodes, ROUND_WIN_NODE_RADIUS)

    nodes = []
    for state, (x, y) in positions.items():
        bucket = win_stats.get(state)
        total = bucket["total"] if bucket else 0
        if total:
            win_pct = bucket["win"] / total
            nodes.append(
                GraphNode(
                    id=state, x=x, y=y, label=state,
                    sublabel=f"{bucket['win']}/{total} rounds won ({win_pct:.0%})",
                    short_label=f"{win_pct:.0%}",
                    fill=_win_color(win_pct), text_fill="#ffffff",
                    radius=ROUND_WIN_NODE_RADIUS,
                )
            )
        else:
            nodes.append(
                GraphNode(
                    id=state, x=x, y=y, label=state, sublabel="No data", short_label=None,
                    fill=_NO_DATA_FILL, text_fill="#898781",
                    radius=ROUND_WIN_NODE_RADIUS,
                )
            )

    # Faint lattice connectors purely for the diamond shape -- no data of their own.
    edges = []
    for a in range(1, 6):
        for b in range(1, 6):
            state = f"{a}v{b}"
            for na, nb in ((a - 1, b), (a, b - 1)):
                if na >= 1 and nb >= 1:
                    nstate = f"{na}v{nb}"
                    x1, y1 = positions[state]
                    x2, y2 = positions[nstate]
                    edges.append(
                        GraphEdge(
                            source=state, target=nstate, weight=0,
                            x1=x1, y1=y1, x2=x2, y2=y2,
                            stroke="#2c2c2a", stroke_width=1.5,
                        )
                    )

    return StateDiagram(nodes=nodes, edges=edges, view_box=view_box)


def top_kill_order_state_deltas(
    diagram: StateDiagram, top_n: int = 5
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Every game state in the diamond has exactly two outgoing edges: a kill
    (opponent count drops -- e.g. 5v5 -> 5v4) and a death (own count drops --
    e.g. 5v5 -> 4v5). Each edge's weight is already signed by construction
    (kill-edge weights only ever accumulate +1 per kill at that state, so
    they're always >= 0; death-edge weights only ever accumulate -1 per death,
    so they're always <= 0) -- so delta = kill_edge.weight + death_edge.weight
    is the true net swing AT that specific state (kills minus deaths), not a
    single edge's own magnitude. (Subtracting would be wrong here: since
    death_edge.weight is already negative, kill - death can never go negative,
    which would make the "biggest swing deaths" list structurally always
    empty.) Returns (top_n biggest positive deltas, top_n biggest negative
    deltas) as (state_label, delta) pairs, skipping states where delta == 0
    (no signal).
    """
    by_source: dict[str, dict[str, GraphEdge]] = {}
    for edge in diagram.edges:
        by_source.setdefault(edge.source, {})[edge.target] = edge

    deltas: list[tuple[str, int]] = []
    for state, targets in by_source.items():
        a, b = (int(part) for part in state.split("v"))
        kill_edge = targets.get(f"{a}v{b - 1}")
        death_edge = targets.get(f"{a - 1}v{b}")
        if kill_edge is None or death_edge is None:
            continue
        delta = kill_edge.weight + death_edge.weight
        if delta != 0:
            deltas.append((state, delta))

    positives = sorted((d for d in deltas if d[1] > 0), key=lambda d: d[1], reverse=True)[:top_n]
    negatives = sorted((d for d in deltas if d[1] < 0), key=lambda d: d[1])[:top_n]
    return positives, negatives


def _fixed_kill_order_edges() -> list[tuple[str, str]]:
    """Reproduces playerTrends.py's createKillOrderGraph(): the full 25-state diamond
    (own/opponent alive counts 1..5) plus the 10 terminal 0-states, with every state's
    two possible transitions pre-registered -- a kill (opponent count drops) or a death
    (own count drops) -- so the diagram always renders the same fixed lattice shape
    used by the round-win diagram, not just the transitions this player happened to hit.
    """
    edges = []
    for a in range(1, 6):
        for b in range(1, 6):
            src = f"{a}v{b}"
            edges.append((src, f"{a}v{b - 1}"))
            edges.append((src, f"{a - 1}v{b}"))
    return edges


_KILL_ORDER_EDGES = _fixed_kill_order_edges()
_NEUTRAL_EDGE = (0xFF, 0xFF, 0xFF)


def _kill_order_diagram(weights: dict[tuple[str, str], int]) -> StateDiagram:
    node_ids: set[str] = set()
    for src, dst in _KILL_ORDER_EDGES:
        node_ids.add(src)
        node_ids.add(dst)

    raw_nodes = {
        state: _pos(*(int(part) for part in state.split("v")), KILL_ORDER_X_STEP, KILL_ORDER_Y_STEP)
        for state in node_ids
    }
    positions, view_box = _layout(raw_nodes, KILL_ORDER_NODE_RADIUS)

    nodes = [
        GraphNode(
            id=state, x=x, y=y, label=state, sublabel=None, short_label=None,
            fill="#3a3b42", text_fill="#ffffff", radius=KILL_ORDER_NODE_RADIUS,
        )
        for state, (x, y) in positions.items()
    ]

    edges = []
    for src, dst in _KILL_ORDER_EDGES:
        weight = weights.get((src, dst), 0)
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        x1, y1, x2, y2 = _shrink_line(x1, y1, x2, y2, KILL_ORDER_NODE_RADIUS + 6)
        if weight > 0:
            stroke = _GOOD
        elif weight < 0:
            stroke = _CRITICAL
        else:
            stroke = _NEUTRAL_EDGE
        label_x, label_y = _edge_midpoint(x1, y1, x2, y2)
        edges.append(
            GraphEdge(
                source=src, target=dst, weight=weight,
                x1=x1, y1=y1, x2=x2, y2=y2,
                stroke=f"#{stroke[0]:02x}{stroke[1]:02x}{stroke[2]:02x}",
                stroke_width=1.5,
                label=str(abs(weight)),
                label_x=label_x, label_y=label_y,
            )
        )

    return StateDiagram(nodes=nodes, edges=edges, view_box=view_box)

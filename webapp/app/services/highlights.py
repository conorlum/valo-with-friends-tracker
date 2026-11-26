"""Highlight selection over replay-clock inputs, with read-only DB input loaders.

Round helpers accept the round blob plus an optional ``round`` number (``find`` supplies it).
``win_chance`` returns every friend kill's gain, including nonpositive gains for best-play
fallbacks. Only the largest positive gain enters the match's highlight ladder.
"""

from collections import Counter, defaultdict

PRE_S = 3.0
POST_S = 2.0
TRIPLE_S = 10.0
ENTRY_S = 5.0
SEG_PRE_S = 2.0
SEG_POST_S = 1.0
SEG_JOIN_S = 1.0
UTIL_TAIL_S = 4.0
TOP_FIGHTS = 3
TOP_UTIL = 3
MORE_PER_PLAYER = 2
RUNGS = {("clutch", 5): 1, ("clutch", 4): 2, "ace": 3, ("clutch", 3): 4,
         "4k": 5, ("clutch", 2): 6, "3k": 7, "entry2k": 8, "fight": 9,
         "util": 10, "winchance": 11, "kill": 12}

_KILL_KINDS = {"clutch", "ace", "4k", "3k", "entry2k"}
_SUMMARIES = {"ace": "Ace", "4k": "4k", "3k": "3k in 10 s", "entry2k": "Entry 2k",
              "fight": "Team-fight swing", "util": "Util play", "winchance": "Win-chance swing",
              "kill": "Kill"}


def _valid(kill, players):
    killer, victim = kill["killer"], kill["victim"]
    return (killer in players and victim in players and killer != victim
            and players[killer]["team"] != players[victim]["team"])


def _kills(round_, players):
    return [k for k in sorted(round_["kills"], key=lambda k: k["t"])
            if k["t"] <= round_["t_decided"] and _valid(k, players)]


def _score(round_, player):
    score = round_.get("score", {})
    other = "team-2" if player["team"] == "team-1" else "team-1"
    return [score.get(player["team"], 0), score.get(other, 0)]


def segments(item):
    """Cut windows for kills; clutch events are [start, *clutch kills, decision].

    Non-kill highlights retain one continuous segment. Clutch starts are context,
    not kills; the decision always contributes a final segment.
    """
    kind, events = item["kind"], item["events"]
    if kind == "clutch":
        times = events[1:-1] + events[-1:]
    elif kind in _KILL_KINDS or kind in {"kill", "winchance"}:
        times = events
    else:
        return [[item["t_start"], item["t_end"]]]
    spans = []
    for t in sorted(times):
        a = round(max(item["t_start"], t - SEG_PRE_S), 2)
        b = round(min(item["t_end"], t + SEG_POST_S), 2)
        if a > b:
            continue
        if spans and a <= spans[-1][1] + SEG_JOIN_S:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return spans


def _item(round_, players, slot, kind, events, *, metric=None, n=None, index=0):
    player = players[slot]
    first, last = events[0], events[-1]
    number = round_.get("round", 0)
    item = {"id": f"r{number}-{kind}-{slot}-{index}", "round": number, "slot": slot,
            "kind": kind, "rung": RUNGS[(kind, n)] if kind == "clutch" else RUNGS[kind],
            "t_start": round(max(0.0, first - PRE_S), 2),
            "t_end": round(min(round_["t_end"], last + POST_S), 2),
            "events": [round(t, 2) for t in events], "segments": [], "metric": metric,
            "speed_s": round(last - first, 2),
            "summary": f"1v{n} clutch" if kind == "clutch" else _SUMMARIES[kind],
            "tags": [], "player": player["name"].split("#", 1)[0], "agent": player["agent"],
            "score": _score(round_, player)}
    item["segments"] = segments(item)
    return item


def clutch_of(round_, players):
    items = []
    winner = round_["winner"]
    if winner not in {"team-1", "team-2"}:
        return items
    side = 1 if winner == "team-1" else 2
    for t, team1, team2 in sorted(round_["alive_steps"]):
        if t > round_["t_decided"]:
            break
        counts = (None, team1, team2)
        n = counts[3 - side]
        if counts[side] != 1 or n < 2:
            continue
        survivors = [slot for slot, p in players.items() if p["team"] == winner
                     and any(a <= t and (b is None or t < b)
                             for a, b in round_["alive"].get(slot, []))]
        # The first clutch step fixes the clutcher, even when they aren't a friend.
        if len(survivors) == 1 and players[survivors[0]]["friend"]:
            slot = survivors[0]
            kills = [k["t"] for k in _kills(round_, players) if k["killer"] == slot and k["t"] >= t]
            items.append(_item(round_, players, slot, "clutch", [t, *kills, round_["t_decided"]], n=n))
        break
    return items


def multikills(round_, players):
    by_slot = defaultdict(list)
    for k in _kills(round_, players):
        if players[k["killer"]]["friend"]:
            by_slot[k["killer"]].append(k["t"])
    items = []
    for slot, times in sorted(by_slot.items()):
        if len(times) >= 5:
            kind, events = "ace", times[:5]
        elif len(times) == 4:
            kind, events = "4k", times
        elif len(times) == 3 and times[-1] - times[0] <= TRIPLE_S:
            kind, events = "3k", times
        else:
            continue
        items.append(_item(round_, players, slot, kind, events))
    return items


def entry(round_, players):
    kills = _kills(round_, players)
    if len(kills) < 2:
        return []
    first, second = kills[:2]
    slot = first["killer"]
    if (slot == second["killer"] and players[slot]["friend"]
            and second["t"] - first["t"] <= ENTRY_S):
        return [_item(round_, players, slot, "entry2k", [first["t"], second["t"]])]
    return []


def fight_swings(team_fights, rounds, players):
    items = []
    for number, fights in sorted((team_fights or {}).items()):
        if number not in rounds:
            continue
        round_ = dict(rounds[number], round=number)
        kills = _kills(round_, players)
        for index, fight in enumerate(fights):
            matched = [k for k in kills if players[k["killer"]]["friend"]
                       and any(k["victim"] == victim and abs(k["t"] - t) <= .25
                               for victim, t, _ in fight["deaths"])]
            for group in ("A", "B"):
                friends = [k for k in matched if players[k["killer"]]["group"] == group]
                other = "B" if group == "A" else "A"
                gain = fight["lost_m2"].get(other, 0) - fight["lost_m2"].get(group, 0)
                if not friends or gain <= 0:
                    continue
                counts = Counter(k["killer"] for k in friends)
                slot = min(counts, key=lambda s: (-counts[s], s))
                events = [fight["t0"], min(fight["t1"], round_["t_decided"])]
                items.append(_item(round_, players, slot, "fight", events, metric=gain, index=index))
    return order(items)[:TOP_FIGHTS]


def _util_candidates(util, players):
    items = []
    for index, cast in enumerate(util or []):
        slot = cast["slot"]
        if (slot not in players or not players[slot]["friend"] or not cast.get("scored", True)
                or cast.get("value") is None or cast["value"] <= 0):
            continue
        # The flat cast contract has no round end; find clamps against the round blob.
        round_ = {"round": cast["round"], "t_end": float("inf")}
        items.append(_item(round_, players, slot, "util", [cast["t"], cast["t"] + UTIL_TAIL_S],
                           metric=cast["value"], index=index))
    return order(items)


def util_plays(util, players):
    return _util_candidates(util, players)[:TOP_UTIL]


def win_chance(round_, players, model, clock_offset):
    from app.services.util_model import p_side

    if model is None or round_["attack"] not in {"team-1", "team-2"}:
        return []
    attack = round_["attack"]
    counts = {team: sum(p["team"] == team for p in players.values()) for team in ("team-1", "team-2")}
    steps = iter(sorted(round_["alive_steps"]))
    step = next(steps, None)
    items = []
    for index, kill in enumerate(sorted(round_["kills"], key=lambda k: k["t"])):
        t = kill["t"]
        if t > round_["t_decided"]:
            break
        # Steps at the kill timestamp already include that death. Earlier steps also
        # account for disconnects and extra lives; simultaneous kills replay in order.
        while step is not None and step[0] < t:
            counts = {"team-1": step[1], "team-2": step[2]}
            step = next(steps, None)
        a = counts[attack]
        defense = "team-2" if attack == "team-1" else "team-1"
        d = counts[defense]
        victim = players.get(kill["victim"])
        if victim is None:
            continue
        after_a = max(0, a - int(victim["team"] == attack))
        after_d = max(0, d - int(victim["team"] == defense))
        slot = kill["killer"]
        if _valid(kill, players) and players[slot]["friend"]:
            side = "attack" if players[slot]["team"] == attack else "defense"
            plant = round_["plant"]
            planted = plant is not None and plant <= t
            t_plant = None if plant is None else plant + clock_offset
            before = p_side(model, side, a, d, planted, t + clock_offset, t_plant)
            after = p_side(model, side, after_a, after_d, planted, t + clock_offset, t_plant)
            items.append(_item(round_, players, slot, "winchance", [t], metric=after - before, index=index))
        counts[attack], counts[defense] = after_a, after_d
    return items


def order(items):
    return sorted(items, key=lambda i: (i["rung"],
                                       -(i["metric"] or 0) if i["rung"] in (9, 10, 11, 12) else 0,
                                       i["speed_s"], i["round"], i["t_start"]))


def dedupe(items):
    kept, by_slot_round = [], {}
    for item in order(items):
        item = dict(item, tags=list(item["tags"]))
        key = (item["slot"], item["round"])
        if item["kind"] not in _KILL_KINDS:
            kept.append(item)
        elif key not in by_slot_round:
            by_slot_round[key] = item
            kept.append(item)
        else:
            best = by_slot_round[key]
            if item["kind"] != best["kind"] and item["kind"] not in best["tags"]:
                best["tags"].append(item["kind"])
                best["summary"] += " · " + item["summary"]
    return kept


def best_plays(items, kill_gains, util, players):
    result = {}
    ranked = order(items)
    util_items = util or []
    if util_items and "kind" not in util_items[0]:
        util_items = _util_candidates(util_items, players)
    for slot, player in sorted(players.items()):
        if not player["friend"]:
            continue
        own = [i for i in ranked if i["slot"] == slot]
        best = own[0] if own else None
        fallback = None
        if best is None:
            for kind, candidates in (("kill", kill_gains or []), ("util", util_items)):
                candidates = order([i for i in candidates if i["slot"] == slot])
                if candidates:
                    best = dict(candidates[0], kind=kind, rung=12, tags=[], summary=_SUMMARIES[kind])
                    best["id"] = best["id"].replace("-winchance-", "-kill-") if kind == "kill" else best["id"]
                    fallback = kind
                    break
        result[str(slot)] = {"name": player["name"], "agent": player["agent"], "best": best,
                             "fallback": fallback, "more": own[1:1 + MORE_PER_PLAYER]}
    return result


def find(inputs):
    players, rounds = inputs["players"], inputs["rounds"]
    items, gains = [], []
    missing = [kind for key, kind in (("team_fights", "fight"), ("util", "util"), ("model", "winchance"))
               if inputs.get(key) is None]
    for number, blob in sorted(rounds.items()):
        round_ = dict(blob, round=number)
        items.extend(clutch_of(round_, players))
        items.extend(multikills(round_, players))
        items.extend(entry(round_, players))
        if inputs.get("model") is not None:
            gains.extend(win_chance(round_, players, inputs["model"], inputs.get("clock_offset", 0.0)))
    items.extend(fight_swings(inputs.get("team_fights"), rounds, players))
    # Keep all eligible casts for per-friend fallbacks, even below the global top three.
    casts = [cast for cast in inputs.get("util") or [] if cast["round"] in rounds
             and cast["t"] <= rounds[cast["round"]]["t_decided"]]
    util = _util_candidates(casts, players)
    for item in util:
        round_ = rounds[item["round"]]
        item["t_end"] = round(min(item["t_end"], round_["t_end"]), 2)
        item["score"] = _score(round_, players[item["slot"]])
        item["segments"] = segments(item)
    items.extend(util[:TOP_UTIL])
    items.extend(order([i for i in gains if i["metric"] > 0])[:1])
    items = order(dedupe(items))
    return {"potg": items[0] if items else None,
            "players": best_plays(items, gains, util, players), "highlights": items, "missing": missing}


def friend_ids(db, page_players):
    """Players in either end of any friendship, as in the moments tab."""
    from sqlalchemy import or_
    from app.models.friendship import Friendship

    ids = [p['player_id'] for p in page_players.values() if p.get('player_id') is not None]
    return {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id)
            .filter(or_(Friendship.owner_player_id.in_(ids), Friendship.friend_player_id.in_(ids)))
            for pid in pair} if ids else set()


def clip_labels(page_players, home_team):
    """Clip-safe names: home names without tags, opponent agents, agents alone on a tie."""
    from app.services import enemy_names

    return {str(slot): (p['agent'] if home_team is None else
                       p['name'].split('#', 1)[0] if p['team'] == home_team else enemy_names.label(p['agent']))
            for slot, p in page_players.items()}


def round_scores(rounds, home_team):
    """Scores before each round, oriented to the friends (team-1 when tied)."""
    home = home_team or 'team-1'
    away = 'team-2' if home == 'team-1' else 'team-1'
    score, out = {'team-1': 0, 'team-2': 0}, {}
    for n, row in sorted(rounds.items(), key=lambda pair: int(pair[0])):
        out[str(n)] = [score[home], score[away]]
        winner = row['db']['winner']
        if winner in score:
            score[winner] += 1
    return out


def load_inputs(db, replay, page=None):
    """Read every round once here, using only current control, util and estimator inputs."""
    from app.models.replay import ReplayRound
    from app.replays import format as fmt
    from app.services import replay_control_views, replay_moments, replay_util, replay_view, util_model
    from app.services import replays as replay_service
    from app.services.state_replay import attacking_team_for_round

    if page is None:
        page = replay_service.page_context(db, replay)
    friends = friend_ids(db, page['players'])
    home = replay_moments.home_team(page['players'], friends)
    clip_names = clip_labels(page['players'], home)
    players = {int(slot): {'team': p['team'], 'group': p['side'], 'agent': p['agent'],
                          'friend': home is not None and p['team'] == home and p['player_id'] in friends,
                          'name': clip_names[str(slot)]}
               for slot, p in page['players'].items()}
    offset = replay.clock_offset or 0.0
    scores = round_scores(page['rounds'], None)
    rounds = {}
    for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
        n = row.round_number
        info = page['rounds'].get(str(n))
        if info is None:
            continue
        blob = fmt.decode_blob(row.data)
        attack = attacking_team_for_round(n)
        rounds[n] = {'t_decided': blob['t_decided'], 't_end': blob['t_end'],
                     'winner': info['db']['winner'], 'attack': attack.value if attack is not None else None,
                     'kills': blob['kills'], 'alive_steps': info['alive_steps'],
                     'alive': replay_view.corrected_lives(blob, info.get('db_deaths', []), offset),
                     'plant': info['db']['plant'],
                     'score': dict(zip(('team-1', 'team-2'), scores[str(n)]))}
    loaded = replay_control_views.load_round_summaries(db, replay)
    stale = set(loaded.stale)
    fights = {int(n): s.get('fights') or [] for n, s in loaded.summaries.items() if n not in stale}
    util_payload = replay_util.match_payload(db, replay, read_only=True, page=page)
    util = None
    if not util_payload.get('updating'):
        util = []
        for n, row in util_payload.get('rounds', {}).items():
            for cast in row.get('casts', []):
                if cast.get('slot') is None:
                    continue
                try:
                    slot = int(cast['slot'])
                except (TypeError, ValueError, OverflowError):
                    continue
                util.append(dict(cast, slot=slot, round=int(n)))
    model = util_model.load(db)
    if not util_model.covers(model, {replay.match_id}):
        model = None
    return {'players': players, 'rounds': rounds, 'clock_offset': offset, 'team_fights': fights or None,
            'util': util, 'model': model, 'home_team': home,
            'clip_names': clip_names, 'uuid': str(replay.match_uuid).lower()}


def payload(db, replay, page=None):
    """The public payload; every clip score uses the home team's orientation."""
    inputs = load_inputs(db, replay, page=page)
    result = find(inputs)
    home = inputs['home_team'] or 'team-1'
    away = 'team-2' if home == 'team-1' else 'team-1'
    items = [*result['highlights'], result['potg']]
    for player in result['players'].values():
        items.extend([player['best'], *player['more']])
    for item in items:
        if item is not None:
            score = inputs['rounds'][item['round']]['score']
            item['score'] = [score[home], score[away]]
    return {**result, **{k: inputs[k] for k in ('clip_names', 'home_team', 'uuid')}}

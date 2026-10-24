"""Critical Util on the site: the replay viewer's tab (`match_payload`) and the cross-match page (`corpus`,
`page_for`). Design: docs/superpowers/specs/2026-10-21-critical-util-design.md.

The worker stores each round's casts and evidence (replay_round_util); this module values them
(app/services/util_values.py) with the stored estimator and weights (util_model "win" and "values") and shapes the
results. Only current rows are read (`current_rows`: the gap run's fingerprint, like Critical Moments). The corpus
products are rebuilt when what they are made from changes (`corpus_key`, `ensure_values`; the estimator refits
when a match it trained on gets a replay, util_model.ensure). Two numbers per cast: its `value`, the share of the
round's swings and jobs credited to it, and its `lift`, its outcome against the no-util baseline (the comparison
the page's scores and calls use; util_values "the no-util baseline").
Friends only: the page shows the viewer's circle (app/services/circle.py), never tracked_players.json. Opponents
are "the enemy <agent>" (app/services/enemy_names.py).
"""

from __future__ import annotations

import functools
import hashlib
import re
from collections import defaultdict
from pathlib import Path

from app.critutil import abilities, constants
from app.services import util_model, util_values

PAGE_VERSION = 2
EXAMPLES_KEPT = 5
COMBO_S = 3.0               # measure (spec, "Measurements" 4)
COMBO_UV = 700              # minimap units (0..10000): about 8 m on a mid-size map; measure
SHRINK_K = 5.0
MIN_CASTS = 5


# --- names


@functools.lru_cache(maxsize=1)
def _ability_styles() -> list[tuple[re.Pattern, str]]:
    """The replay viewer's own code -> ability name table (app/static/js/replay.js ABILITY_STYLES), so the tab and
    the viewer name an ability the same way."""
    path = Path(__file__).resolve().parents[1] / "static" / "js" / "replay.js"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for pattern, label in re.findall(r'\[/(\^[^/]+)/, \{ ability: "([^"]+)"', text):
        try:
            out.append((re.compile(pattern), label))
        except re.error:
            continue
    return out


def ability_label(cast: dict) -> str:
    table_id = cast.get("ability_id")
    if isinstance(table_id, str) and "/" in table_id:
        return table_id.split("/", 1)[1]
    agent = cast.get("agent") or ""
    codes = [c for c in cast.get("codes") or () if c]
    names = [n for n in cast.get("names") or () if n]
    for name in names:
        for code in codes or [agent]:
            key = f"{code}_{name}"
            for pattern, label in _ability_styles():
                if pattern.match(key):
                    return label
    for name in names:
        for pattern, label in _ability_styles():
            if pattern.search("_" + name) or pattern.pattern.endswith(name + "$"):
                return label
    for name in names:          # a flash row's own name: "phoenix_curveball_left" -> "Curveball"
        if name.islower() and "_" in name:
            words = [w for w in name.split("_") if w not in ("left", "right")]
            if agent and words and words[0] == agent.lower().replace("/", ""):
                words = words[1:]
            if words:
                return " ".join(w.capitalize() for w in words)
    return names[0] if names else "utility"


def kind_label(kinds) -> str:
    return util_values.main_kind(kinds)


LABELS = (("blind", "Blinds"), ("smoke", "Smokes"), ("damaging", "Damaging"), ("status", "Status"),
          ("info", "Info"), ("teleport", "Teleports"), ("heal", "Heals"))


def label_of(kinds) -> str | None:
    """The group a row sits in on the page: the first of LABELS whose tag the cast has."""
    have = set(kinds or ())
    for tag, label in LABELS:
        if tag in have:
            return label
    return None


# --- areas and economy


def area_of(map_name: str, u, v, side: str | None) -> str:
    from app.coach import areas

    if u is None or v is None:
        return "unknown"
    found = areas.area_at(map_name, u, v)
    sup = (found or {}).get("super")
    if sup in ("Attacker Side", "Defender Side"):
        own = (sup == "Attacker Side") == (side == "attack")
        return "own half" if own else "enemy half"
    return sup or "unknown"


# The site's existing economy conventions, mirrored so this web-side module never imports the scorer
# (tests/replays/test_replay_isolation.py); tests/critutil/test_page.py pins both to their sources.
FULL_BUY_THRESHOLD = 4200       # app.scoring.impact.FULL_BUY_THRESHOLD (econ_tier_name's FULL_BUY)
PISTOL_ROUNDS = frozenset({1, 13})   # app.services.economy_graphs.PISTOL_ROUNDS


def buy_type(round_number: int, avg_loadout: float | None) -> str:
    if round_number in PISTOL_ROUNDS:
        return "pistol"
    if avg_loadout is not None and avg_loadout >= FULL_BUY_THRESHOLD:
        return "full buy"
    return "other"


def winner_side(outcome: str | None, round_number: int) -> str | None:
    from app.scoring.plant_window import attacking_team
    from app.services.state_replay import _winner_team

    winner, attack = _winner_team(outcome), attacking_team(round_number)
    if winner is None or attack is None:
        return None
    return "attack" if winner == attack else "defense"


# --- loading


def _values_row(db) -> tuple[str, dict] | None:
    """(key, data) of the stored corpus build, or None."""
    from app.models.replay import UtilModel

    row = db.get(UtilModel, "values")
    if row is None or row.version != util_values.VERSION or not row.data:
        return None
    return row.key, row.data


class Freshness:
    """Which stored util rounds are current, by Critical Moments' rule (replay_moments.load_moments_payload): the
    gap run's revision, and its fingerprint against the round's now (the link and its deaths, so a relink or a
    clock correction; the geometry and heights; the chokes; the hearing table). Built once per request: each map's
    geometry and each replay's side groups are read once."""

    def __init__(self, db):
        from app.services import replay_gaps

        self.db, self.hearing = db, replay_gaps.hearing_hash()
        self.maps: dict[str, tuple] = {}
        self.groups: dict[int, dict] = {}

    def expected(self, replay, n: int) -> str | None:
        from app.replays import choke_assets
        from app.services import replay_control, replay_gaps

        if replay.map_name not in self.maps:
            self.maps[replay.map_name] = (replay_control.resolve_current_geometry(self.db, replay.map_name),
                                          choke_assets.asset_hash(replay.map_name))
        context, chokes = self.maps[replay.map_name]
        if replay.id not in self.groups:
            self.groups[replay.id] = replay_control.side_groups(self.db, replay)
        control = replay_control.round_fingerprint(replay, self.groups[replay.id], n, context=context)
        if control is None:
            return None
        return replay_gaps.gap_fingerprint(control, replay.map_name, chokes=chokes, hearing=self.hearing)

    def current(self, replay, n: int, revision: int, fingerprint: str) -> bool:
        from app.services import replay_gaps

        return revision == replay_gaps.GAPS_REVISION and fingerprint == self.expected(replay, n)


def current_rows(db, replay_ids=None, *, with_data: bool = True) -> list[tuple]:
    """(replay id, round, fingerprint, computed_at[, data]) of every ok util row that is current (`Freshness`).
    What every reader values: the page's corpus, the tab and the coach (through the tab's payload)."""
    from app.models.replay import Replay, ReplayRoundUtil
    from app.services import replay_gaps

    cols = [ReplayRoundUtil.replay_id, ReplayRoundUtil.round_number, ReplayRoundUtil.fingerprint,
            ReplayRoundUtil.computed_at] + ([ReplayRoundUtil.data] if with_data else [])
    q = db.query(*cols).filter(ReplayRoundUtil.status == "ok", ReplayRoundUtil.gaps_revision == replay_gaps.GAPS_REVISION)
    if replay_ids is not None:
        q = q.filter(ReplayRoundUtil.replay_id.in_(list(replay_ids)))
    rows = q.all()
    ids = {r[0] for r in rows}
    replays = {r.id: r for r in db.query(Replay).filter(Replay.id.in_(list(ids)))} if ids else {}
    fresh = Freshness(db)
    return [tuple(r) for r in rows if r[0] in replays
            and fresh.current(replays[r[0]], r[1], replay_gaps.GAPS_REVISION, r[2])]


def _stored_rounds(db, replay_ids=None, rows=None):
    """{replay_id: {round: data}} of every current ok util row (`current_rows`)."""
    out: dict[int, dict] = defaultdict(dict)
    for r in rows if rows is not None else current_rows(db, replay_ids):
        if r[4]:
            out[r[0]][r[1]] = r[4]
    return out


ON_SPANS_FROM = 22          # CONDENSE revision from which Viper's Poison Cloud `on` spans exist


def on_known(recipe: str | None) -> bool:
    """Whether a replay's blob has the `on` spans (condense revision ON_SPANS_FROM or later)."""
    from app.services import replay_control

    return (replay_control.condense_revision(recipe or "") or 0) >= ON_SPANS_FROM


def _round_data(data: dict, ctx: dict) -> dict:
    """A stored round with the replay's clock offset and whether its blob has `on` spans."""
    return {**data, "clock_offset": ctx.get("offset", 0.0), "on_known": bool(ctx.get("on_known", False))}


def _replay_context(db, replay_ids):
    """Per replay: map, uuid, match id, slot -> (player_id, agent, team), round -> (winner side, number), and each
    slot's team's average start loadout per round."""
    from app.models.match import MatchPlayer
    from app.models.replay import Replay, ReplayPlayer
    from app.models.round import Round, RoundPlayerStat

    out = {}
    replays = db.query(Replay.id, Replay.map_name, Replay.match_uuid, Replay.match_id, Replay.clock_offset,
                       Replay.recipe).filter(
        Replay.id.in_(list(replay_ids)), Replay.match_id.is_not(None)).all()
    match_of = {r.id: r.match_id for r in replays}
    players: dict[int, dict] = defaultdict(dict)
    mp_slot: dict[int, tuple[int, int]] = {}
    for rp, mp in (db.query(ReplayPlayer, MatchPlayer).join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
                   .filter(ReplayPlayer.replay_id.in_(list(match_of)))):
        team = mp.team.value if hasattr(mp.team, "value") else str(mp.team)
        players[rp.replay_id][rp.slot] = {"player_id": mp.player_id, "agent": mp.agent, "team": team}
        mp_slot[mp.id] = (rp.replay_id, rp.slot)
    rounds: dict[int, dict] = defaultdict(dict)
    round_ids = {}
    by_match = defaultdict(list)
    for rid, mid in match_of.items():
        by_match[mid].append(rid)
    for r in db.query(Round.id, Round.match_id, Round.round_number, Round.outcome).filter(
            Round.match_id.in_(list(by_match))):
        for rid in by_match[r.match_id]:
            rounds[rid][r.round_number] = {"winner": winner_side(r.outcome, r.round_number), "id": r.id}
            round_ids[r.id] = (rid, r.round_number)
    loadouts: dict[tuple, list] = defaultdict(list)
    for s in db.query(RoundPlayerStat.round_id, RoundPlayerStat.match_player_id, RoundPlayerStat.loadout).filter(
            RoundPlayerStat.round_id.in_(list(round_ids))):
        rid, n = round_ids[s.round_id]
        slot = mp_slot.get(s.match_player_id)
        if slot is None:
            continue
        team = players[slot[0]].get(slot[1], {}).get("team")
        loadouts[(rid, n, team)].append(s.loadout or 0)
    for r in replays:
        out[r.id] = {"map": r.map_name, "uuid": str(r.match_uuid), "match_id": r.match_id,
                     "offset": r.clock_offset or 0.0, "on_known": on_known(r.recipe),
                     "players": players.get(r.id, {}), "rounds": rounds.get(r.id, {}),
                     "loadout": {(n, team): sum(v) / len(v) for (rid, n, team), v in loadouts.items()
                                 if rid == r.id and v}}
    return out


# --- per cast rows


NO_LIFT = {"swing": None, "base": None, "base_n": 0, "base_level": None, "lift": None}


def _cast_rows(replay_id: int, ctx: dict, rounds: dict, model, weights, *, include_unscored: bool = False
               ) -> list[dict]:
    """Every valued cast of one replay, flat, with who threw it and where, and its combo partners. The lift fields
    are empty here: `build_from_rounds` fills them (the v2 baseline), the tab reads them from the stored build. A
    cast after the round was decided is never scored (spec, "The cast"): it is left out, or with `include_unscored`
    (the tab) kept with `scored` false and no value."""
    rows = []
    for n, data in sorted(rounds.items()):
        data = _round_data(data, ctx)
        valued = util_values.value_round(data, model, weights)
        sides = data.get("sides") or {}
        casts = data.get("casts") or []
        for c in casts:
            v = valued["casts"].get(c["i"]) or {}
            scored = bool(v.get("scored"))
            if not scored and not include_unscored:
                continue
            p = ctx["players"].get(c["by"]) or {}
            team = p.get("team")
            rows.append({
                "replay": replay_id, "uuid": ctx["uuid"], "map": ctx["map"], "match": ctx["match_id"], "round": n,
                "i": c["i"], "slot": c["by"], "player_id": p.get("player_id"), "agent": c.get("agent") or p.get("agent"),
                "ability": ability_label(c), "kind": kind_label(c.get("kinds")), "kinds": c.get("kinds"),
                "label": label_of(c.get("kinds")),
                "side": c.get("side"), "area": area_of(ctx["map"], c.get("u"), c.get("v"), c.get("side")),
                "phase": c.get("phase"), "t": c["t"],
                "buy": buy_type(n, ctx["loadout"].get((n, team))),
                "won": (ctx["rounds"].get(n) or {}).get("winner") == c.get("side") if c.get("side") else None,
                "scored": scored,
                "value": v.get("total", 0.0), "ledger": v.get("ledger", 0.0), "jobs": v.get("jobs", 0.0),
                "wasted": v.get("wasted", False), "found_nobody": v.get("found_nobody", False),
                "harm": len(c.get("harm") or ()), "harm_died": sum(1 for h in c.get("harm") or () if h.get("died")),
                "followed": c.get("followed"), "ult_points": c.get("ult_points"),
                "enemies": len(c.get("enemies") or ()), "features": v.get("features", {}), **NO_LIFT,
            })
        _mark_combos([r for r in rows if r["round"] == n and r["scored"]], casts)
        for r in rows:
            r.setdefault("combo", [])
        for h in valued["heals"]:
            p = ctx["players"].get(h.get("by")) or {}
            rows.append({"replay": replay_id, "uuid": ctx["uuid"], "map": ctx["map"], "match": ctx["match_id"],
                         "round": n, "i": None, "slot": h.get("by"), "player_id": p.get("player_id"),
                         "agent": p.get("agent"), "ability": ability_label({"agent": p.get("agent"),
                                                                            "codes": [h.get("code")],
                                                                            "names": [h.get("name")]}),
                         "kind": "heal", "kinds": ["heal"], "label": "Heals", "side": sides.get(str(h.get("by"))), "area": "unknown",
                         "phase": None, "t": h["t"], "buy": buy_type(n, None), "won": None, "scored": True,
                         "value": h["value"], "ledger": 0.0, "jobs": h["value"], "wasted": False, "harm": 0,
                         "harm_died": 0, "followed": None, "ult_points": None, "enemies": 0, "combo": [],
                         "features": {"saved": bool(h.get("saved")), "amount": h.get("amount")}, **NO_LIFT,
                         "heal": {"target": h.get("target"), "saved": h.get("saved"),
                                  "saved_share": h.get("saved_share"), "fought": h.get("fought")}})
    return rows


def _mark_combos(rows: list[dict], casts: list[dict]) -> None:
    """Casts by teammates within COMBO_S of each other, on the same enemy or within COMBO_M: each row gets the
    (round-local) indices of its partners."""
    by_i = {c["i"]: c for c in casts}
    for r in rows:
        r["combo"] = []
    for a in rows:
        ca = by_i.get(a["i"])
        for b in rows:
            if b is a or b["slot"] == a["slot"] or b["side"] != a["side"]:
                continue
            cb = by_i.get(b["i"])
            if ca is None or cb is None or abs(ca["t"] - cb["t"]) > COMBO_S:
                continue
            same_enemy = bool(set(ca.get("enemies") or ()) & set(cb.get("enemies") or ()))
            close = (ca.get("u") is not None and cb.get("u") is not None
                     and ((ca["u"] - cb["u"]) ** 2 + (ca["v"] - cb["v"]) ** 2) ** 0.5 <= COMBO_UV)
            if same_enemy or close:
                a["combo"].append(b["i"])


# --- the corpus


REBUILD_TRIES = 3


def corpus_key(db, rows: list[tuple], model: dict | None) -> str:
    """What a corpus build is made from, as one key: the versions, every current util row it reads (round,
    fingerprint, when computed), each of their replays' match and clock offset, and the estimator's key. Computed
    from the very rows a build reads, so the key stored with a build always describes that build's inputs."""
    from app.models.replay import Replay

    ids = sorted({r[0] for r in rows})
    replays = sorted(db.query(Replay.id, Replay.match_id, Replay.clock_offset).filter(Replay.id.in_(ids))) if ids else []
    friends = hashlib.sha256(",".join(str(p) for p in sorted(friend_players(db))).encode()).hexdigest()
    h = hashlib.sha256(f"{util_values.VERSION}:{PAGE_VERSION}:{(model or {}).get('key')}:{abilities.table_hash()}:"
                       f"{constants.VERSION}:{friends}".encode())
    for rid, n, fp, at, *_ in sorted(rows, key=lambda r: (r[0], r[1])):
        h.update(f"|{rid}:{n}:{fp}:{at}".encode())
    for rid, match_id, offset in replays:
        h.update(f"|r{rid}:{match_id}:{offset}".encode())
    return h.hexdigest()[:32]


def lineup_score(lifts: list[float], prior: float) -> float | None:
    """A lineup's score: its casts' mean lift over the no-util baseline, shrunk toward the ability's mean lift (the
    ability prior) while casts are few. None under MIN_CASTS compared casts."""
    if len(lifts) < MIN_CASTS:
        return None
    return util_values.shrunk(lifts, prior, SHRINK_K)


def friend_players(db) -> set[int]:
    """Every player id in any friendship: the players whose casts get a Yours baseline."""
    from app.models.friendship import Friendship

    return {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id) for pid in pair}


def _gate_list(by_l: dict) -> list[dict]:
    return [dict(by_l[L]) for L in range(1, util_values.MAX_L + 1)]


def _item_of(data: dict, row: dict) -> dict | None:
    """The raw cast (by `i`) or heal (by t and who) a row was made from."""
    if row["i"] is not None:
        return next((c for c in data.get("casts") or () if c["i"] == row["i"]), None)
    return next((h for h in data.get("heals") or () if h.get("t") == row["t"] and h.get("by") == row["slot"]), None)


def build_from_rounds(stored: dict, ctx: dict, model, friend_players=frozenset()) -> dict:
    """The corpus build, pure: `stored` is {replay id: {round: data}}, `ctx` the `_replay_context`, `friend_players`
    the players in at least one friendship (they get a Yours baseline). Each match is valued with weights fitted
    without it (util_values.weights_for) and each cast compared with the v2 baseline (util_values: per type, at its
    own length L): the other matches' moments (`lift_all`, R1), the caster's own moments with every match of the
    cast's lineup left out (`lift_yours`, R3), and for a placeable the same map and side without that device
    (`lift_device`, R10). A mode's lift counts toward scores, priors and spread only where the gate passes at the
    cast's L (R2); `lift` is the all-players one for the page and the tab."""
    friend_players = frozenset(friend_players)
    stored = {rid: rounds for rid, rounds in stored.items() if rid in ctx}
    prepared: dict[int, dict] = {}
    items, training = [], []
    for rid, rounds in stored.items():
        c = ctx[rid]
        player_of = {slot: p.get("player_id") for slot, p in c["players"].items()}
        prepared[rid] = {}
        for n, data in rounds.items():
            d = prepared[rid][n] = _round_data(data, c)
            items.append({"match": c["match_id"], "map": c["map"], "data": d, "player_of": player_of})
            training.append({"match": c["match_id"], "data": d, "winner": (c["rounds"].get(n) or {}).get("winner")})
    tables = util_values.match_tables(items, model, friend_players)
    gates = util_values.gate_all_modes(tables)
    gate_pass = {mode: {L: bool(g["pass"]) for L, g in gates[mode].items()} for mode in ("all", "yours", "device")}
    fit = util_values.fit_weights(util_values.team_rounds(training, model))
    out_rows = []
    for rid, rounds in stored.items():
        m = ctx[rid]["match_id"]
        out_rows.extend(_cast_rows(rid, ctx[rid], prepared[rid], model, util_values.weights_for(fit, m)))

    lineup_of = lambda r: (r["player_id"], r["ability"], r["map"], r["side"], r["area"])  # noqa: E731
    lineup_matches: dict[tuple, set] = defaultdict(set)
    for r in out_rows:
        lineup_matches[lineup_of(r)].add(r["match"])
    tot_all = util_values.totals(tables["all"])
    tot_yours: dict = {}
    dev_wrapped: dict = {}
    tot_dev: dict = {}
    for r in out_rows:
        r.update(lift_all=None, gate_all=None, yours_level=None, yours_n=0, lift_yours=None, gate_yours=None,
                 device_level=None, device_n=0, lift_device=None, gate_device=None, L=0, type=None)
        data = prepared[r["replay"]].get(r["round"]) or {}
        item = _item_of(data, r)
        if item is None:
            continue
        typ = util_values.type_of(item)
        r["type"] = typ
        side = r["side"]
        segs = util_values.item_segments(data, item)
        if not segs or side not in ("attack", "defense"):
            continue
        swing, L = util_values.cast_swing_v2(data, model, item, segs, side)
        if swing is None:
            continue
        keys = util_values.levels(data, side, max(float(segs[0][0]), 0.0))
        r.update(swing=round(swing, 4), L=L)
        got = util_values.lift_v2(tot_all, tables["all"], typ, keys, L, swing, leave_out={r["match"]})
        if got is not None:
            r.update(base=round(got["mean"], 4), base_n=got["n"], base_level=got["level"],
                     lift_all=round(got["lift"], 4), gate_all=gate_pass["all"][L])
        pid = r["player_id"]
        if pid in tables["yours"]:
            if pid not in tot_yours:
                tot_yours[pid] = util_values.totals(tables["yours"][pid])
            got = util_values.lift_v2(tot_yours[pid], tables["yours"][pid], typ, keys, L, swing,
                                      leave_out=lineup_matches[lineup_of(r)])
            if got is not None:
                r.update(yours_level=got["level"], yours_n=got["n"], lift_yours=round(got["lift"], 4),
                         gate_yours=gate_pass["yours"][L])
        if typ == "placeable" and item.get("ability_id") is not None:
            dkey = f"{item['ability_id']}|{r['map']}|{side}"
            per = tables["device"].get(dkey)
            if per is not None:
                if dkey not in tot_dev:
                    dev_wrapped[dkey] = {mm: {"device": cells} for mm, cells in per.items()}
                    tot_dev[dkey] = util_values.totals(dev_wrapped[dkey])
                got = util_values.lift_v2(tot_dev[dkey], dev_wrapped[dkey], "device", keys, L, swing,
                                          leave_out={r["match"]})
                if got is not None:
                    r.update(device_level=got["level"], device_n=got["n"], lift_device=round(got["lift"], 4),
                             gate_device=gate_pass["device"][L])
        r["lift"] = r["lift_all"] if r["gate_all"] else None

    modes = {"all": ("lift_all", "gate_all"), "yours": ("lift_yours", "gate_yours")}
    prior_sums: dict[str, dict] = {"all": {}, "yours": {}}
    for mode, (lk, gk) in modes.items():
        for r in out_rows:
            if r["kind"] != "heal" and r.get(lk) is not None and r.get(gk):
                cell = prior_sums[mode].setdefault(r["ability"], {}).setdefault(str(r["match"]), [0, 0.0])
                cell[0] += 1
                cell[1] += r[lk]
    by_ability: dict[str, list] = defaultdict(list)
    for r in out_rows:
        if r["kind"] != "heal" and r.get("lift_all") is not None and r.get("gate_all"):
            by_ability[r["ability"]].append(r["lift_all"])
    ability_prior = {k: sum(v) / len(v) for k, v in by_ability.items()}
    spread_by_mode = {}
    for mode, (lk, gk) in modes.items():
        lineup: dict[tuple, list] = defaultdict(list)
        for r in out_rows:
            if r["kind"] != "heal" and r.get(lk) is not None and r.get(gk):
                lineup[lineup_of(r)].append(r[lk])
        scores = []
        for k, v in lineup.items():
            cells = prior_sums[mode].get(k[1]) or {}
            left = lineup_matches[k]
            n = sum(c[0] for mm, c in cells.items() if int(mm) not in left)
            s = sum(c[1] for mm, c in cells.items() if int(mm) not in left)
            score = lineup_score(v, s / n if n else 0.0)
            if score is not None:
                scores.append(score)
        spread_by_mode[mode] = (sum(s * s for s in scores) / len(scores)) ** 0.5 if scores else 0.0
    overlap: dict[str, dict] = {}
    for r in out_rows:
        if r["kind"] == "heal":
            continue
        o = overlap.setdefault(r["ability"], {"casts": 0, "full": 0, "fallback": 0, "none": 0})
        o["casts"] += 1
        level = r.get("base_level")
        o["none" if level is None else "full" if level == util_values.BASE_LEVELS[0] else "fallback"] += 1
    return {"rows": out_rows, "fit": fit, "ability_prior": ability_prior, "spread": spread_by_mode["all"],
            "spread_by_mode": spread_by_mode, "prior_sums": prior_sums, "overlap": overlap,
            "matches": sorted({ctx[rid]["match_id"] for rid in stored}),
            "gate": {"all": _gate_list(gates["all"]), "yours": _gate_list(gates["yours"]),
                     "device": _gate_list(gates["device"]),
                     "by_type": {mode: {typ: _gate_list(g) for typ, g in gates["by_type"][mode].items()}
                                 for mode in ("all", "yours")}},
            "same_type_overlap": {typ: [float(x) for x in arr] for typ, arr in tables["overlap"].items()},
            "estimator": {k: (model or {}).get(k) for k in ("n", "matches", "check", "calibration")}}


def build_corpus(db, *, rows: list[tuple] | None = None, model: dict | None = None) -> dict:
    """The stored build's data (`build_from_rounds`) from `rows` (`current_rows`; read here when not given), the
    estimator `model` (the stored one when not given) and the friendships, with each row's fingerprint added.
    Reads only: `ensure_values` stores it."""
    rows = current_rows(db) if rows is None else rows
    model = util_model.load(db) if model is None else model
    stored = _stored_rounds(db, rows=rows)
    ctx = _replay_context(db, set(stored))
    out = build_from_rounds(stored, ctx, model, friend_players(db))
    out["fingerprints"] = {}
    for r in rows:
        out["fingerprints"].setdefault(str(r[0]), {})[str(r[1])] = r[2]
    return out


def _store_values(db, key: str, data: dict) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.exc import IntegrityError

    from app.models.replay import UtilModel

    try:
        db.merge(UtilModel(name="values", version=util_values.VERSION, key=key, data=data,
                           computed_at=datetime.now(timezone.utc)))
        db.commit()
    except IntegrityError:
        db.rollback()       # another process stored the first row meanwhile (no lock on this database): keep theirs


def ensure_values(db, *, force: bool = False) -> dict:
    """The stored corpus build, rebuilt when what it is made from changed (`corpus_key`) or with `force`.

    One rebuild at a time (util_model.rebuild_lock): a request that waited on another's rebuild re-reads and uses
    it. The key stored is computed from the rows the build read, and when the rows changed while it was building it
    builds again (up to REBUILD_TRIES), so an older build is never stored under a newer key, and a request never
    goes on with a build it knows is stale."""
    data = None
    for _ in range(REBUILD_TRIES):
        model = util_model.ensure(db)
        key = corpus_key(db, current_rows(db, with_data=False), model)
        stored = _values_row(db)
        if not force and stored is not None and stored[0] == key:
            return stored[1]
        with util_model.rebuild_lock(db, "values"):
            db.expire_all()
            stored = _values_row(db)
            if not force and stored is not None and stored[0] == key:
                db.commit()                 # ends the transaction, and with it the lock
                return stored[1]
            rows = current_rows(db)
            used = corpus_key(db, rows, model)
            data = build_corpus(db, rows=rows, model=model)
            _store_values(db, used, data)
        force = False
        if corpus_key(db, current_rows(db, with_data=False), util_model.load(db)) == used:
            return data
    return data


# --- the cross-match page


def _summ(values):
    n = len(values)
    mean = sum(values) / n if n else 0.0
    return n, mean


def page_for(corpus: dict, circle: set[int]) -> dict:
    """The page's tables for one viewer's circle: friends' casts only."""
    rows = [r for r in corpus.get("rows") or () if r.get("player_id") in circle and r.get("scored", True)]
    prior = corpus.get("ability_prior") or {}
    personal = corpus.get("personal") or {}
    spread = corpus.get("spread") or 0.0
    out: dict = {"n": len(rows), "fit": corpus.get("fit"), "estimator": corpus.get("estimator"),
                 "overlap": corpus.get("overlap") or {}, "base_window": util_values.BASE_WINDOW_S}

    def mean_lift(v):
        lifts = [r["lift"] for r in v if r.get("lift") is not None]
        return (round(100 * sum(lifts) / len(lifts), 2) if lifts else None), len(lifts)

    wins: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            wins[(r["ability"], r["agent"], r["map"], r["side"])].append(r)
    out["wins_rounds"] = sorted(
        ({"ability": k[0], "agent": k[1], "map": k[2], "side": k[3], "n": len(v),
          "matches": len({r["match"] for r in v}), "value": round(100 * sum(r["value"] for r in v) / len(v), 2),
          "lift": mean_lift(v)[0], "compared": mean_lift(v)[1],
          "round_win": round(100 * sum(1 for r in v if r["won"]) / len(v), 1)}
         for k, v in wins.items() if len(v) >= MIN_CASTS), key=lambda x: -x["value"])

    lineups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            lineups[(r["player_id"], r["agent"], r["ability"], r["map"], r["side"], r["area"])].append(r)
    calls = []
    for k, v in lineups.items():
        if len(v) < MIN_CASTS:
            continue
        lifts = [r["lift"] for r in v if r.get("lift") is not None]
        ability_prior = prior.get(k[2], 0.0)
        score = lineup_score(lifts, ability_prior)
        mine = personal.get(f"{k[0]}|{util_values.main_kind(v[0].get('kinds'))}|{k[4]}") or (0.0, 0)
        swings = [r["swing"] for r in v if r.get("swing") is not None]
        personal_lift = None
        if mine[1] >= util_values.BASE_MIN_N and swings:
            personal_lift = round(100 * (sum(swings) / len(swings) - mine[0] / mine[1]), 2)
        calls.append({"player_id": k[0], "agent": k[1], "ability": k[2], "map": k[3], "side": k[4], "area": k[5],
                      "n": len(v), "compared": len(lifts), "matches": len({r["match"] for r in v}),
                      "value": round(100 * sum(r["value"] for r in v) / len(v), 2),
                      "score": None if score is None else round(100 * score, 2),
                      "prior": round(100 * ability_prior, 2), "personal": personal_lift,
                      "call": "no comparison yet" if score is None else util_values.call(score, spread)})
    out["lineups"] = sorted(calls, key=lambda c: -(c["score"] if c["score"] is not None else -1e9))

    by_player: dict[int, list] = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(r)
    out["players"] = {pid: _player_block(v) for pid, v in by_player.items()}
    out["combos"] = _combo_table(rows)
    out["ults"] = _ult_table(rows)
    return out


def _example(r) -> dict:
    return {k: r.get(k) for k in ("uuid", "round", "t", "agent", "ability", "map", "side", "area", "value", "kind",
                                  "features", "harm_died", "wasted")} | {"value": round(100 * r["value"], 2)}


def _player_block(rows: list[dict]) -> dict:
    casts = [r for r in rows if r["kind"] != "heal"]
    heals = [r for r in rows if r["kind"] == "heal"]
    ranked = sorted(casts, key=lambda r: r["value"])

    def share(pred, among):
        return round(100 * sum(1 for r in among if pred(r)) / len(among), 1) if among else None

    def mean(among):
        return round(100 * sum(r["value"] for r in among) / len(among), 2) if among else None

    affecting = [r for r in casts if r["followed"] is not None]
    patterns = {
        "phase": {ph: {"n": len(g), "value": mean(g)} for ph in ("pre_contact", "contact", "post_plant")
                  for g in [[r for r in casts if r["phase"] == ph]]},
        "buy": {b: {"n": len(g), "value": mean(g)} for b in ("pistol", "full buy", "other")
                for g in [[r for r in casts if r["buy"] == b]]},
        "support_rate": share(lambda r: r["followed"], affecting),
        "waste_rate": share(lambda r: r["wasted"],
                            [r for r in casts if set(r.get("kinds") or ()) & util_values.JUDGED_KINDS]),
        "harm_rate": share(lambda r: r["harm"] > 0, casts),
        "harm_deaths": sum(r["harm_died"] for r in casts),
        "timing": _timing(casts),
    }
    return {"casts": len(casts), "value": mean(casts), "best": [_example(r) for r in ranked[::-1][:EXAMPLES_KEPT]],
            "worst": [_example(r) for r in ranked[:EXAMPLES_KEPT]], "patterns": patterns,
            "heals": {"n": len(heals), "saved": sum(1 for r in heals if (r.get("heal") or {}).get("saved")),
                      "value": mean(heals)},
            "areas": _area_repeats(casts)}


def _timing(casts):
    buckets = {"0-20s": (0, 20), "20-45s": (20, 45), "45-70s": (45, 70), "70s+": (70, 999)}
    return {k: {"n": len(g), "value": round(100 * sum(r["value"] for r in g) / len(g), 2) if g else None}
            for k, (a, b) in buckets.items() for g in [[r for r in casts if a <= (r["t"] or 0) < b]]}


def _area_repeats(casts):
    """How often the same ability went into the same area, and its value by round number within the match (first
    half of uses vs second): area patterns, not proof of a lineup or of enemies adapting."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in casts:
        groups[(r["ability"], r["map"], r["side"], r["area"])].append(r)
    out = []
    for k, v in groups.items():
        if len(v) < 3:
            continue
        by_match: dict[int, list] = defaultdict(list)
        for r in v:
            by_match[r["match"]].append(r)
        early, late = [], []
        for ms in by_match.values():
            ms.sort(key=lambda r: r["round"])
            half = len(ms) // 2
            early += ms[:half] if half else []
            late += ms[half:] if half else []
        out.append({"ability": k[0], "map": k[1], "side": k[2], "area": k[3], "n": len(v),
                    "per_match": round(len(v) / len(by_match), 1),
                    "early": round(100 * sum(r["value"] for r in early) / len(early), 2) if early else None,
                    "late": round(100 * sum(r["value"] for r in late) / len(late), 2) if late else None})
    return sorted(out, key=lambda x: -x["n"])[:10]


def _combo_table(rows):
    by_key = {(r["replay"], r["round"], r["i"]): r for r in rows if r["i"] is not None}
    pairs: dict[tuple, list] = defaultdict(list)
    for r in by_key.values():
        for j in r.get("combo") or ():
            other = by_key.get((r["replay"], r["round"], j))
            if other is None or (r["i"], r["agent"]) > (other["i"], other["agent"]):
                continue
            a, b = sorted([f"{r['agent']} {r['ability']}", f"{other['agent']} {other['ability']}"])
            pairs[(a, b)].append(r["value"] + other["value"])
    return sorted(({"pair": list(k), "n": len(v), "value": round(100 * sum(v) / len(v), 2)}
                   for k, v in pairs.items() if len(v) >= 3), key=lambda x: -x["value"])[:20]


def _ult_table(rows):
    ults: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if "ult" in (r.get("kinds") or ()):
            ults[(r["agent"], r["ability"])].append(r)
    out = []
    for (agent, ability), v in ults.items():
        pts = [r["ult_points"] for r in v if r.get("ult_points")]
        value = sum(r["value"] for r in v) / len(v)
        out.append({"agent": agent, "ability": ability, "n": len(v), "value": round(100 * value, 2),
                    "per_point": round(100 * value / (sum(pts) / len(pts)), 3) if pts else None,
                    "phase": max(("pre_contact", "contact", "post_plant"),
                                 key=lambda ph: sum(1 for r in v if r["phase"] == ph))})
    return sorted(out, key=lambda x: -(x["per_point"] or -1e9))


# --- the replay viewer's tab


def match_payload(db, replay) -> dict:
    """The tab's body for one linked replay: each player's casts with values, best and worst, unused util, harm.
    Opponents named by agent."""
    from sqlalchemy import or_

    from app.models.friendship import Friendship
    from app.models.replay import ReplayRoundUtil
    from app.services import replays as replay_service
    from app.services.replay_moments import tab_players

    rows = db.query(ReplayRoundUtil).filter(ReplayRoundUtil.replay_id == replay.id).all()
    stored = {r.round_number: r for r in rows}
    page = replay_service.page_context(db, replay)["players"]
    ids = [p["player_id"] for p in page.values() if p.get("player_id") is not None]
    friends = {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id)
               .filter(or_(Friendship.owner_player_id.in_(ids), Friendship.friend_player_id.in_(ids)))
               for pid in pair} if ids else set()
    players = tab_players(page, friends)
    ctx = _replay_context(db, {replay.id}).get(replay.id)
    model = util_model.load(db)
    if ctx is not None and not util_model.covers(model, {ctx["match_id"]}):
        model = util_model.ensure(db)       # this match was in the stored estimator's training: refit it first
    corpus = (_values_row(db) or (None, {}))[1]
    weights = util_values.weights_for(corpus.get("fit"), (ctx or {}).get("match_id"))
    rounds_out, stale, failed = {}, 0, []
    current = {}
    fresh = Freshness(db)
    for n in range(1, replay.round_count + 1):
        row = stored.get(n)
        if row is None or not fresh.current(replay, n, row.gaps_revision, row.fingerprint):
            stale += 1
            continue
        if row.status != "ok" or not row.data:
            failed.append(n)
            continue
        current[n] = row.data
    built = {(r["replay"], r["round"], r["i"], r["slot"] if r["i"] is None else None, r["t"] if r["i"] is None else None): r
             for r in corpus.get("rows") or () if r.get("replay") == replay.id}
    flat = _cast_rows(replay.id, ctx, current, model, weights, include_unscored=True) if ctx else []
    for r in flat:          # the lift comes from the stored build (the same cast there), when it has one
        b = built.get((r["replay"], r["round"], r["i"], r["slot"] if r["i"] is None else None,
                       r["t"] if r["i"] is None else None))
        if b is not None:
            r.update(lift=b.get("lift"), base_n=b.get("base_n", 0), base_level=b.get("base_level"))
    by_round: dict[int, list] = defaultdict(list)
    for r in flat:
        by_round[r["round"]].append(r)
    ults = unused_ults(current)
    for n, data in current.items():
        casts = {c["i"]: c for c in data.get("casts") or ()}
        out = []
        for r in by_round.get(n, []):
            c = casts.get(r["i"]) or {}
            out.append({**{k: r[k] for k in ("i", "slot", "agent", "ability", "kind", "kinds", "side", "area", "phase",
                                             "t", "wasted", "found_nobody", "followed", "enemies", "harm_died",
                                             "combo", "scored", "base_n", "base_level")
                           if k in r},
                        "value": round(100 * r["value"], 2) if r["scored"] else None,
                        "ledger": round(100 * r["ledger"], 2), "jobs": round(100 * r["jobs"], 2),
                        "lift": None if r.get("lift") is None else round(100 * r["lift"], 2),
                        "features": r["features"],
                        "kills": c.get("kills") or [], "harm": c.get("harm") or [], "effects": c.get("effects") or [],
                        "cover": c.get("cover"), "presence": c.get("presence"), "trigger": c.get("trigger"),
                        "hold": c.get("hold"),
                        "heal": r.get("heal")})
        rounds_out[str(n)] = {"casts": out, "unused": (data.get("unused") or []) + ults.get(n, []),
                              "avail": data.get("avail") or {}}
    summary = {}
    for slot in players:
        mine = [r for r in flat if str(r["slot"]) == str(slot) and r["kind"] != "heal" and r["scored"]]
        ranked = sorted(mine, key=lambda r: r["value"])
        pick = lambda r: {"round": r["round"], "t": r["t"], "i": r["i"], "ability": r["ability"],  # noqa: E731
                          "value": round(100 * r["value"], 2)}
        summary[str(slot)] = {"casts": len(mine), "best": [pick(r) for r in ranked[::-1][:3] if r["value"] > 0],
                              "worst": [pick(r) for r in ranked[:3] if r["value"] < 0],
                              "value": round(100 * sum(r["value"] for r in mine), 2)}
    return {"players": players, "rounds": rounds_out, "summary": summary, "stale": stale,
            "failed": sorted(failed), "estimator": bool(model and model.get("n")),
            "weights_fitted": weights != util_values.DEFAULT_WEIGHTS,
            "baseline": bool(corpus.get("rows"))}


def unused_ults(rounds: dict[int, dict]) -> dict[int, list]:
    """Full ults never used, once per player-round, from one match's rounds (spec, "Unused util"). A slot's ult
    cost is proven by the match: the most points it held just before any use in any round (extract `_ults`). A
    round where they ended (died, or the round was decided) holding that many and never used it is one line. No
    claim for a slot that never used its ult in the match: its cost is unproven."""
    caps: dict[str, int] = {}
    for data in rounds.values():
        for slot, u in (data.get("ults") or {}).items():
            if u.get("cap_seen"):
                caps[slot] = max(caps.get(slot, 0), int(u["cap_seen"]))
    out: dict[int, list] = defaultdict(list)
    for n, data in rounds.items():
        for slot, u in (data.get("ults") or {}).items():
            cap = caps.get(slot)
            if cap and u.get("end") is not None and u["end"] >= cap and not u.get("used"):
                out[n].append({"slot": int(slot), "t": u.get("t"), "kind": "ult_unused", "points": u["end"]})
    return out

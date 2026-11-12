"""Critical Util's portable corpus build: plain round inputs, no database imports.

The site loads the inputs in app.services.replay_util and re-exports these helpers.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import re
import tarfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import util_model, util_values


PAGE_VERSION = 2
EXAMPLES_KEPT = 5
COMBO_S = 3.0               # measure (spec, "Measurements" 4)
COMBO_UV = 700              # minimap units (0..10000): about 8 m on a mid-size map; measure
SHRINK_K = 5.0
MIN_CASTS = 5


@functools.lru_cache(maxsize=1)
def _ability_styles() -> list[tuple[re.Pattern, str]]:
    """The replay viewer's own code -> ability name table (app/static/js/replay.js ABILITY_STYLES), so the tab and
    the viewer name an ability the same way."""
    path = Path(__file__).resolve().parents[1] / "static" / "js" / "replay.js"
    text = path.read_text(encoding="utf-8")
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
          ("info", "Info"), ("teleport", "Teleports"), ("dash", "Dashes"), ("stim", "Stims"), ("heal", "Heals"))


def label_of(kinds) -> str | None:
    """The group a row sits in on the page: the first of LABELS whose tag the cast has."""
    have = set(kinds or ())
    for tag, label in LABELS:
        if tag in have:
            return label
    return None


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


# The site's economy conventions, mirrored without importing the scorer.
# tests/critutil/test_page.py pins both constants to their sources.
FULL_BUY_THRESHOLD = 4200       # app.scoring.impact.FULL_BUY_THRESHOLD (econ_tier_name's FULL_BUY)
PISTOL_ROUNDS = frozenset({1, 13})   # app.services.economy_graphs.PISTOL_ROUNDS


def buy_type(round_number: int, avg_loadout: float | None) -> str:
    if round_number in PISTOL_ROUNDS:
        return "pistol"
    if avg_loadout is not None and avg_loadout >= FULL_BUY_THRESHOLD:
        return "full buy"
    return "other"


def _round_data(data: dict, ctx: dict) -> dict:
    """A stored round with the replay's clock offset and whether its blob has `on` spans."""
    return {**data, "clock_offset": ctx.get("offset", 0.0), "on_known": bool(ctx.get("on_known", False))}


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
                "wasted": v.get("wasted", False), "wasted_why": v.get("wasted_why"), "found_nobody": v.get("found_nobody", False),
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
                                  "saved_share": h.get("saved_share"), "fought": h.get("fought"),
                                  "kept_alive": h.get("kept_alive"), "hits_after": h.get("hits_after")}})
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


def lineup_score(lifts: list[float], prior: float) -> float | None:
    """A lineup's score: its casts' mean lift over the no-util baseline, shrunk toward the ability's mean lift (the
    ability prior) while casts are few. None under MIN_CASTS compared casts."""
    if len(lifts) < MIN_CASTS:
        return None
    return util_values.shrunk(lifts, prior, SHRINK_K)


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
    # each table's matches expanded once, kept across its casts (util_values.lift_v2 `expanded`)
    exp_all: dict = {}
    exp_yours: dict = defaultdict(dict)
    exp_dev: dict = defaultdict(dict)
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
        keys = util_values.levels(data, side, max(util_values.start_time(data, float(segs[0][0])), 0.0))
        r.update(swing=round(swing, 4), L=L)
        got = util_values.lift_v2(tot_all, tables["all"], typ, keys, L, swing, leave_out={r["match"]},
                                  expanded=exp_all)
        if got is not None:
            r.update(base=round(got["mean"], 4), base_n=got["n"], base_level=got["level"],
                     lift_all=round(got["lift"], 4), gate_all=gate_pass["all"][L])
        pid = r["player_id"]
        if pid in tables["yours"]:
            if pid not in tot_yours:
                tot_yours[pid] = util_values.totals(tables["yours"][pid])
            got = util_values.lift_v2(tot_yours[pid], tables["yours"][pid], typ, keys, L, swing,
                                      leave_out=lineup_matches[lineup_of(r)], expanded=exp_yours[pid])
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
                                          leave_out={r["match"]}, expanded=exp_dev[dkey])
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


def corpus_from_inputs(inputs: tuple[dict, dict, set], rows: list[tuple], model) -> dict:
    """build_corpus's computing, with no database: `inputs` from corpus_inputs on the same `rows`."""
    stored, ctx, friends = inputs
    out = build_from_rounds(stored, ctx, model, friends)
    out["fingerprints"] = {}
    for r in rows:
        out["fingerprints"].setdefault(str(r[0]), {})[str(r[1])] = r[2]
    return out


# --- the shared site / task wire format

KEY_RE = r"^[0-9A-Za-z._-]{1,120}$"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _at_us(at: datetime) -> int:
    # SQLite returns naive UTC; never interpret it in the machine's local timezone.
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    delta = at - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def row_key(replay, n, fp, computed_at) -> str:
    """The site's file-safe name for one frozen util row, precise to the microsecond."""
    key = f"{replay}-{n}-{fp}-{_at_us(computed_at)}"
    _check_key(key)
    return key


def _check_key(key: str) -> None:
    if not isinstance(key, str) or re.fullmatch(KEY_RE, key) is None:
        raise ValueError("invalid input key")


def encode_output(data) -> bytes:
    """The exact bytes hashed and stored by the site; non-finite values are errors."""
    return json.dumps(data, separators=(",", ":"), allow_nan=False).encode()


def encode_round(data) -> bytes:
    return encode_output(data)


def encode_obs(entries: list) -> bytes:
    """Encode a shard as a list of [match_id, observations] pairs.

    A shard may hold one or several matches. Match ids live in the payload, never in the opaque filename;
    observations have util_model.fit's six fields. For one match use [[match_id, observations]].
    """
    return encode_output(entries)


def dispatch_key(values_key_now, refit, excluded, obs_keys, digest) -> str:
    """Build/tries identity, distinct from the output's estimator-dependent values key."""
    return hashlib.sha256(encode_output(
        [values_key_now, bool(refit), sorted(excluded), sorted(obs_keys), digest])).hexdigest()


def values_key(key_parts, model_key) -> str:
    """The original corpus_key hash, from frozen metadata with no database access."""
    p = key_parts
    h = hashlib.sha256(f"{p['values_version']}:{p['page_version']}:{model_key}:{p['abilities_hash']}:"
                       f"{p['constants_version']}:{p['friends_hash']}".encode())
    for rid, n, fp, at in sorted(p["rows"], key=lambda r: (r[0], r[1])):
        h.update(f"|{rid}:{n}:{fp}:{at}".encode())
    for rid, match_id, offset in sorted(p["replays"]):
        h.update(f"|r{rid}:{match_id}:{offset}".encode())
    return h.hexdigest()[:32]


def encode_context(ctx, friends, model_or_None, rows, excluded, refit, key_parts) -> bytes:
    """Freeze replay context and ordered row metadata without loading their blobs.

    Pair lists keep integer keys, tuple loadout keys, and insertion order without JSON object-key coercion.
    The context's rows are always [replay id, round, fingerprint, UTC epoch microseconds], in load order.
    """
    replays = []
    for rid, c in ctx.items():
        replays.append([rid, {**c, "players": list(c["players"].items()),
                              "rounds": list(c["rounds"].items()),
                              "loadout": [[n, team, value] for (n, team), value in c["loadout"].items()]}])
    return encode_output({"ctx": replays, "friends": sorted(friends), "model": model_or_None,
                          "rows": [[rid, n, fp, _at_us(at)] for rid, n, fp, at, *_ in rows],
                          "excluded": sorted(excluded), "refit": bool(refit), "key_parts": key_parts})


def decode_context(data: bytes) -> dict:
    context = json.loads(data)
    context["ctx"] = {
        rid: {**c, "players": dict(c["players"]), "rounds": dict(c["rounds"]),
              "loadout": {(n, team): value for n, team, value in c["loadout"]}}
        for rid, c in context["ctx"]}
    context["friends"] = set(context["friends"])
    context["excluded"] = set(context["excluded"])
    context["rows"] = [(rid, n, fp, _EPOCH + timedelta(microseconds=at))
                       for rid, n, fp, at in context["rows"]]
    return context


@dataclass(frozen=True)
class Inputs:
    """Decoded build inputs. Round/observation names are opaque transport keys."""

    context: dict
    rounds: dict[str, dict]
    obs: dict[str, list]


def pack_inputs(context_bytes, rounds: dict[str, bytes], obs: dict[str, bytes], tar_path) -> None:
    """Write the three shared member namespaces. Nothing is extracted to disk on read."""
    with tarfile.open(tar_path, "w") as archive:
        def add(name, data):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        add("context.json", context_bytes)
        for folder, blobs in (("rounds", rounds), ("obs", obs)):
            for key, data in blobs.items():
                _check_key(key)
                add(f"{folder}/{key}.json", data)


def unpack_inputs(tar_path) -> Inputs:
    context, rounds, obs = None, {}, {}
    seen = set()
    with tarfile.open(tar_path, "r:*") as archive:
        for member in archive:
            if not member.isfile() or member.name in seen:
                raise ValueError("invalid or duplicate input member")
            seen.add(member.name)
            name = member.name
            if name == "context.json":
                context = decode_context(archive.extractfile(member).read())
                continue
            folder, sep, filename = name.partition("/")
            if not sep or folder not in ("rounds", "obs") or not filename.endswith(".json"):
                raise ValueError("unknown input member")
            key = filename[:-5]
            _check_key(key)
            data = json.loads(archive.extractfile(member).read())
            (rounds if folder == "rounds" else obs)[key] = data
    if context is None:
        raise ValueError("missing context.json")
    return Inputs(context, rounds, obs)


def compute(inputs: Inputs) -> dict:
    """The entire portable build, including an optional estimator refit, without a database."""
    c = inputs.context
    win = c["model"]
    if c["refit"]:
        by_match = {}
        for entries in inputs.obs.values():
            for match_id, observations in entries:
                if match_id in by_match:
                    raise ValueError("duplicate observation match")
                by_match[match_id] = [tuple(o) for o in observations]
        win = util_model.refit_from(by_match, c["excluded"])
    stored = defaultdict(dict)
    for row in c["rows"]:
        data = inputs.rounds[row_key(*row)]
        if data:
            stored[row[0]][row[1]] = data
    values = corpus_from_inputs((stored, c["ctx"], c["friends"]), c["rows"], win)
    return {"win": win, "values": values, "values_key": values_key(c["key_parts"], (win or {}).get("key"))}

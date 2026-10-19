"""Everything one coach run needs about a match, read from the database once and kept as one JSON file on
disk. The brief, the detectors and the query commands read the file; the model never reaches the database."""

from __future__ import annotations

import json
from pathlib import Path

from app.coach import control_areas
from app.coach.roundview import RoundView
from app.replays import format as fmt
from app.services import enemy_names, replay_control, replays

VERSION = 1
FILE = "snapshot.json"


class SnapshotError(Exception):
    pass


def assemble(context: dict, blobs: dict[int, dict], controls: dict[int, tuple], links: dict | None = None) -> dict:
    rounds = {}
    for n, blob in sorted(blobs.items()):
        status, stale, shares = controls.get(n, ("not_ready", False, None))
        link = (links or {}).get(n) or {}
        link = {"sides": dict(link.get("sides") or {}), "db_deaths": [list(d) for d in link.get("db_deaths") or []]}
        gaps = []
        if status != "ok":
            gaps.append(f"control_{status}")
        elif stale:
            gaps.append("control_stale")
        if not (shares and shares.get("knew")):
            gaps.append("team_knowledge")
        if "player_state" not in blob:
            gaps.append("player_state")
        if link["db_deaths"]:
            gaps.append("db_only_deaths")
        rounds[str(n)] = {"blob": blob, "control": shares, "link": link, "gaps": gaps}
    return {"v": VERSION, "context": context, "rounds": rounds}


def _groups(context: dict) -> dict[int, str]:
    return {int(slot): p["side"] for slot, p in context["players"].items() if p.get("side") is not None}


def read(db, match_uuid: str) -> dict:
    replay = replays.find_replay(db, match_uuid)
    if replay is None:
        raise SnapshotError(f"no replay stored for {match_uuid}")
    context = replays.page_context(db, replay)
    if not context["match"].get("linked"):
        raise SnapshotError(f"replay {match_uuid} is {context['match'].get('reason') or 'not linked'}: nothing to coach")
    groups = _groups(context)
    blobs, controls, links = {}, {}, {}
    for n in context["match"]["rounds"]:
        blobs[n] = fmt.decode_blob(replays.round_blob(db, replay, n))
        links[n] = replay_control.round_link(replay, groups, n)
        roles = links[n].get("sides") or {}
        for p in blobs[n]["players"]:
            slot = int(p["slot"])
            if p.get("side") is None and slot not in groups and str(slot) not in roles and slot not in roles:
                raise SnapshotError(f"round {n}: slot {p['slot']} has no side in the replay or the link: "
                                    "nothing can be said about teammates or enemies")
        answer = replay_control.round_control(db, replay, n)
        shares = control_areas.area_shares(answer.row.data, replay.map_name) if answer.status == "ok" else None
        controls[n] = (answer.status, bool(answer.stale), shares)
    return assemble(context, blobs, controls, links)


def hide_enemy_names(data: dict, player_id: int) -> dict:
    """The snapshot as one player's run sees it: every opponent named by their agent ("the enemy Neon"), so no
    opponent's name reaches the model or its report."""
    players = data["context"]["players"]
    team = next((p.get("team") for p in players.values() if p.get("player_id") == player_id), None)
    if team is None:
        raise SnapshotError(f"player {player_id} is not in this replay")
    return {**data, "context": {**data["context"], "players": enemy_names.hide_in(players, team)}}


def save(data: dict, directory: Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / FILE).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


class Snapshot:
    def __init__(self, data: dict):
        if data.get("v") != VERSION:
            raise SnapshotError(f"snapshot version {data.get('v')!r} is not {VERSION}")
        self.data, self.context = data, data["context"]
        self.map, self.uuid = self.context["match"]["map"], self.context["match"]["uuid"]
        self.names = {int(slot): p["name"] for slot, p in self.context["players"].items()}
        self._views: dict[int, RoundView] = {}

    round_numbers = property(lambda self: sorted(int(n) for n in self.data["rounds"]))

    def _round(self, n: int) -> dict:
        try:
            return self.data["rounds"][str(n)]
        except KeyError:
            raise SnapshotError(f"round {n} is not in this match (rounds {self.round_numbers[0]}"
                                f"-{self.round_numbers[-1]})") from None

    def view(self, n: int) -> RoundView:
        if n not in self._views:
            entry = self._round(n)
            link = entry.get("link") or {}
            roles = {int(slot): role for slot, role in (link.get("sides") or {}).items()}
            self._views[n] = RoundView(entry["blob"], self.names, self.context["match"]["uv_per_unit"], self.map,
                                       groups=_groups(self.context), db_deaths=link.get("db_deaths") or (),
                                       roles=roles)
        return self._views[n]

    def role(self, n: int, slot: int) -> str | None:
        """`attack` or `defense` for this slot in round n, from the link; None when the link names none."""
        return ((self._round(n).get("link") or {}).get("sides") or {}).get(str(slot))

    def control(self, n: int) -> dict | None:
        return self._round(n)["control"]

    def gaps(self, n: int) -> list[str]:
        return list(self._round(n)["gaps"])

    def site(self, n: int) -> dict:
        return self.context["rounds"].get(str(n), {})

    def slot_of(self, token: str) -> int:
        token = str(token).strip()
        if token.isdigit() and int(token) in self.names:
            return int(token)
        wanted = token.lower()
        exact = [s for s, name in self.names.items() if name.lower() == wanted]
        short = [s for s, name in self.names.items() if name.lower().split("#")[0] == wanted]
        found = exact or short
        if len(found) != 1:
            raise SnapshotError(f"{token!r} is {'ambiguous' if found else 'not in this match'}; players: "
                                + ", ".join(self.names[s] for s in sorted(self.names)))
        return found[0]


def load(directory: Path) -> Snapshot:
    return Snapshot(json.loads((Path(directory) / FILE).read_text(encoding="utf-8")))

"""A coach snapshot on disk, and the reader every coach command uses. No database imports: `scripts/coach_query.py`
starts a fresh Python for each of the model's questions, so whatever this module imports is paid on every one.

On disk a snapshot is `snapshot.json` (the version, the page context and the list of rounds) plus one file per round
under `rounds/`, so a question about one round reads that round alone. A snapshot written as one file (every
round inside `snapshot.json`, before the split) still loads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from app.coach.roundview import RoundView

VERSION = 1
FILE = "snapshot.json"
ROUNDS_DIR = "rounds"


class SnapshotError(Exception):
    pass


def _groups(context: dict) -> dict[int, str]:
    return {int(slot): p["side"] for slot, p in context["players"].items() if p.get("side") is not None}


class _RoundFiles(Mapping):
    """The `rounds` of a split snapshot: each round's file is read the first time it is asked for, then kept."""

    def __init__(self, directory: Path, numbers: list):
        self._directory, self._numbers, self._loaded = directory, [str(n) for n in numbers], {}

    def __getitem__(self, key):
        key = str(key)
        if key not in self._numbers:
            raise KeyError(key)
        if key not in self._loaded:
            path = self._directory / ROUNDS_DIR / f"{key}.json"
            self._loaded[key] = json.loads(path.read_text(encoding="utf-8"))
        return self._loaded[key]

    def __iter__(self):
        return iter(self._numbers)

    def __len__(self):
        return len(self._numbers)


def save(data: dict, directory: Path) -> None:
    """The round files first and `snapshot.json` last, so a snapshot.json on disk always has its rounds."""
    directory = Path(directory)
    rounds_dir = directory / ROUNDS_DIR
    rounds_dir.mkdir(parents=True, exist_ok=True)
    for n, entry in data["rounds"].items():
        (rounds_dir / f"{n}.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    head = {"v": data["v"], "context": data["context"], "round_files": list(data["rounds"])}
    (directory / FILE).write_text(json.dumps(head, separators=(",", ":")), encoding="utf-8")


def load(directory: Path) -> Snapshot:
    directory = Path(directory)
    data = json.loads((directory / FILE).read_text(encoding="utf-8"))
    if "round_files" in data:
        data = {"v": data.get("v"), "context": data["context"], "rounds": _RoundFiles(directory, data["round_files"])}
    return Snapshot(data)


class Snapshot:
    def __init__(self, data: dict):
        if data.get("v") != VERSION:
            raise SnapshotError(f"snapshot version {data.get('v')!r} is not {VERSION}")
        self.data, self.context = data, data["context"]
        self.map, self.uuid = self.context["match"]["map"], self.context["match"]["uuid"]
        # Snapshots written before layout selection used the base map's callouts.
        self.asset_key = self.context["match"].get("asset_key", self.map)
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
            self._views[n] = RoundView(entry["blob"], self.names, self.context["match"]["uv_per_unit"], self.asset_key,
                                       groups=_groups(self.context), db_deaths=link.get("db_deaths") or (),
                                       roles=roles)
        return self._views[n]

    def role(self, n: int, slot: int) -> str | None:
        """`attack` or `defense` for this slot in round n, from the link; None when the link names none."""
        return ((self._round(n).get("link") or {}).get("sides") or {}).get(str(slot))

    def control(self, n: int) -> dict | None:
        return self._round(n)["control"]

    def util(self, n: int) -> dict | None:
        """The round's Critical Util ({"casts", "unused", "avail"}), or None when the snapshot has none."""
        return self._round(n).get("util")

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

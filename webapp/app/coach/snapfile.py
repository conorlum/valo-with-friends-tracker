"""A coach snapshot on disk, and the reader every coach command uses. No database imports: `scripts/coach_query.py`
starts a fresh Python for each of the model's questions, so whatever this module imports is paid on every one.

On disk `snapshot.json` holds the version, page context and list of rounds. It points to a directory with one
file per round, so a question about one round reads that round alone. Older snapshots with rounds in
`snapshot.json` or in a fixed `rounds/` directory still load."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from app.coach.roundview import RoundView

VERSION = 1
FILE = "snapshot.json"
ROUNDS_DIR = "rounds"
SEARCH_FILE = "search.json"


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
            path = self._directory / f"{key}.json"
            try:
                self._loaded[key] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:      # missing, unreadable or not JSON: a snapshot problem
                raise SnapshotError(f"round {key}'s file {path} could not be read "
                                    f"({type(error).__name__}: {error})") from None
        return self._loaded[key]

    def __iter__(self):
        return iter(self._numbers)

    def __len__(self):
        return len(self._numbers)


REPLACE_TRIES = 40         # x REPLACE_WAIT_S: how long a save waits for readers to let go of the manifest
REPLACE_WAIT_S = 0.05


def _replace(source: Path, target: Path) -> None:
    """os.replace, retried while Windows refuses it: CPython opens files without FILE_SHARE_DELETE, so a reader
    holding the manifest for its few milliseconds makes the swap fail with a PermissionError."""
    for attempt in range(REPLACE_TRIES):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise PermissionError(f"{target} stayed locked by another process for "
                                      f"{REPLACE_TRIES * REPLACE_WAIT_S:.0f} s; the snapshot was not replaced") from None
            time.sleep(REPLACE_WAIT_S)


def _generation(directory: Path) -> str | None:
    """The rounds directory the manifest in `directory` names now, if any."""
    try:
        return json.loads((directory / FILE).read_text(encoding="utf-8")).get("rounds_dir")
    except (OSError, ValueError, AttributeError):
        return None


def save(data: dict, directory: Path) -> None:
    """Publish the manifest after all of its round files have been written. A save into a directory that already
    holds a snapshot keeps the generation the old manifest named (a reader that loaded it may still open its round
    files) and removes every older one, so repeated saves hold at most two generations."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    previous = _generation(directory)
    rounds_dir = Path(tempfile.mkdtemp(prefix=f"{ROUNDS_DIR}-", dir=directory))
    try:
        for n, entry in data["rounds"].items():
            (rounds_dir / f"{n}.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
        head = {"v": data["v"], "context": data["context"], "round_files": list(data["rounds"]),
                "rounds_dir": rounds_dir.name}
        if "search" in data:        # one file for the whole match's index rows, read only by `./q search`
            (rounds_dir / SEARCH_FILE).write_text(json.dumps(data["search"], separators=(",", ":")), encoding="utf-8")
            head["search_file"] = SEARCH_FILE
        manifest = rounds_dir / FILE
        manifest.write_text(json.dumps(head, separators=(",", ":")), encoding="utf-8")
        _replace(manifest, directory / FILE)
    except BaseException:
        shutil.rmtree(rounds_dir, ignore_errors=True)
        raise
    for old in directory.glob(f"{ROUNDS_DIR}-*"):
        if old.is_dir() and old.name not in (rounds_dir.name, previous):
            shutil.rmtree(old, ignore_errors=True)       # best effort: a locked file waits for the next save


def load(directory: Path) -> Snapshot:
    directory = Path(directory)
    data = json.loads((directory / FILE).read_text(encoding="utf-8"))
    if "round_files" in data:
        rounds_dir = directory / data.get("rounds_dir", ROUNDS_DIR)
        found = {"v": data.get("v"), "context": data["context"], "rounds": _RoundFiles(rounds_dir, data["round_files"])}
        if data.get("search_file"):
            found["_search_path"] = rounds_dir / data["search_file"]
        data = found
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

    def search_rows(self) -> list[dict]:
        """The match's replay-search index rows. [] for a snapshot saved without any; only the search file is read."""
        if "search" in self.data:
            return self.data["search"]
        path = self.data.get("_search_path")
        if path is None:
            return []
        if "_search_rows" not in self.__dict__:
            try:
                self._search_rows = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise SnapshotError(f"the search file {path} could not be read "
                                    f"({type(error).__name__}: {error})") from None
        return self._search_rows

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

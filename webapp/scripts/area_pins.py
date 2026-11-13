"""Export coach area corrections as pins for the local map tagger.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/area_pins.py

By default the script writes %TEMP%/valo-areas/pins.json. Use --out to choose another local file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.replays import format as fmt  # noqa: E402

SAMPLE_WINDOW_S = 0.5
_AREA_COLUMN = r"(?:coach_feedback\.)?area_(?:name|player_id)"
_MISSING_COLUMN = re.compile(rf"\b(?:column {_AREA_COLUMN} does not exist|no such column: {_AREA_COLUMN})\b",
                             re.IGNORECASE)


def pins_from(rows, blob_for) -> list[dict]:
    """Resolve each eligible row against its chosen slot's closest sample in the stored round."""
    pins = []
    for row in rows:
        area, match, map_name = row.get("area_name"), row.get("match"), row.get("map")
        round_number, t, slot = row.get("round"), row.get("t"), row.get("slot")
        if not area or not match or not map_name or type(round_number) is not int or type(slot) is not int:
            continue
        if type(t) not in (int, float) or not math.isfinite(t):
            continue
        blob = blob_for(match, round_number)
        if blob is None:
            continue
        tracks, hz = blob.get("tracks"), blob.get("hz")
        if not isinstance(tracks, dict) or type(hz) is not int or hz <= 0:
            continue
        try:
            samples = (s for segment in tracks.get(str(slot), ()) for s in fmt.decode_segment_z(segment, hz))
            nearest = min(samples, key=lambda s: (abs(s[0] - t), s[0]), default=None)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if nearest is not None and abs(nearest[0] - t) <= SAMPLE_WINDOW_S:
            _, u, v, _, z = nearest
            pins.append({"map": map_name, "u": u, "v": v, "z": z, "area": area, "match": match})
    return pins


def _rows(db) -> list[dict]:
    from sqlalchemy import and_, func, select

    from app.models import MatchPlayer
    from app.models.coach import CoachFeedback, CoachPoint, CoachReport
    from app.models.replay import Replay, ReplayPlayer

    # An unmatched or ambiguous slot still contributes one skipped feedback row.
    slot = (select(func.min(ReplayPlayer.slot))
            .where(ReplayPlayer.replay_id == Replay.id, ReplayPlayer.match_player_id == MatchPlayer.id)
            .having(func.count() == 1).correlate(Replay, MatchPlayer).scalar_subquery())
    rows = (db.query(CoachFeedback.area_name, CoachFeedback.instance_index, CoachPoint.instances,
                     Replay.match_uuid, Replay.map_name, slot)
            .select_from(CoachFeedback)
            .join(CoachPoint, CoachPoint.id == CoachFeedback.point_id)
            .join(CoachReport, CoachReport.id == CoachPoint.report_id)
            .outerjoin(Replay, and_(Replay.match_id == CoachReport.match_id, Replay.link_status == "linked"))
            .outerjoin(MatchPlayer, and_(MatchPlayer.match_id == CoachReport.match_id,
                                         MatchPlayer.player_id == CoachFeedback.area_player_id))
            .filter(CoachFeedback.area_name.is_not(None)).order_by(CoachFeedback.id).all())
    out = []
    for area, index, instances, match, map_name, player_slot in rows:
        moment = instances[index] if isinstance(instances, list) and index is not None and 0 <= index < len(instances) else {}
        if not isinstance(moment, dict):
            moment = {}
        out.append({"area_name": area, "match": str(match).lower() if match else None, "map": map_name,
                    "round": moment.get("round"), "t": moment.get("t"), "slot": player_slot})
    return out


def _read_blob(db, match: str, round_number: int) -> dict | None:
    from app.models.replay import Replay, ReplayRound

    data = (db.query(ReplayRound.data).join(Replay, Replay.id == ReplayRound.replay_id)
            .filter(Replay.match_uuid == match, ReplayRound.round_number == round_number).scalar())
    if data is None:
        return None
    try:
        return fmt.decode_blob(data)
    except (OSError, EOFError, ValueError, TypeError, AttributeError):
        return None


def _write_pins(out: Path, pins: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(pins, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        os.replace(temporary, out)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-areas" / "pins.json"
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            rows = _rows(db)
        except Exception as error:
            diagnostic = str(getattr(error, "orig", error)).partition("\n")[0].replace('"', "")
            if not _MISSING_COLUMN.search(diagnostic):
                raise
            print("Area pins are unavailable because migration 0033 isn't live yet.")
            return 0

        @lru_cache(maxsize=64)
        def blob_for(match: str, round_number: int) -> dict | None:
            return _read_blob(db, match, round_number)

        pins = pins_from(rows, blob_for)
    _write_pins(out, pins)
    print(f"{len(pins)} pins, {len(rows) - len(pins)} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Exports one stored replay for the local preview page (scripts/render_replay_standalone.py). Read-only.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\export_replay_preview.py <match uuid> [--out <dir>]

Writes, into `--out` (default `%TEMP%\\valo-replay\\<uuid>-preview`, never the repo):

- `N.json.gz`: each round's stored blob, as served;
- `N.control.bin`: each round's stored map control (`replay_round_control.data`, gzip), when it has an ok row;
- `context.json`: the replay page's site data (app/services/replays.py `page_context`), with player names:
  keep the folder local;
- `control_players.json` (and `control_heatmap_<view>.json` once Stage 5 exists): the control endpoints' answers.

Then `render_replay_standalone.py --blobs <dir>` builds the page with names, the layer and the tables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models.replay import Replay, ReplayRound, ReplayRoundControl  # noqa: E402
from app.services import replay_control_views as views  # noqa: E402
from app.services import replays as replay_service  # noqa: E402


def export(db, uuid: str, out: Path) -> dict:
    replay = db.query(Replay).filter(Replay.match_uuid == uuid.lower()).one_or_none()
    if replay is None:
        raise SystemExit(f"no stored replay {uuid}")
    out.mkdir(parents=True, exist_ok=True)
    rounds = 0
    for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
        (out / f"{row.round_number}.json.gz").write_bytes(row.data)
        rounds += 1
    control = 0
    for row in db.query(ReplayRoundControl).filter(ReplayRoundControl.replay_id == replay.id,
                                                    ReplayRoundControl.status == "ok"):
        (out / f"{row.round_number}.control.bin").write_bytes(row.data)
        control += 1
    context = replay_service.page_context(db, replay)
    (out / "context.json").write_text(json.dumps(context, default=str), encoding="utf-8")
    loaded = views.load_round_summaries(db, replay)
    (out / "control_players.json").write_text(json.dumps(views.player_tables(replay, loaded)), encoding="utf-8")
    heatmaps = 0
    if hasattr(views, "match_heatmap_for"):
        for view in views.HEATMAP_VIEWS:
            body = views.match_heatmap_for(db, replay, view, loaded)
            (out / f"control_heatmap_{view}.json").write_text(json.dumps(body), encoding="utf-8")
            heatmaps += 1
    return {"map": replay.map_name, "rounds": rounds, "control_rows": control, "heatmaps": heatmaps}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("uuid", help="the replay's match UUID")
    parser.add_argument("--out", type=Path, help="the folder to write (default under %%TEMP%%\\valo-replay\\)")
    args = parser.parse_args(argv)
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay" / f"{args.uuid.lower()}-preview"
    db = SessionLocal()
    try:
        result = export(db, args.uuid, out)
    finally:
        db.rollback()
        db.close()
    print(f"{result['map']}: {result['rounds']} rounds, {result['control_rows']} control rows, "
          f"{result['heatmaps']} heatmaps -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

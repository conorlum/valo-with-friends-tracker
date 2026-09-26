"""The local replay preview: dev-only routes over bundles from scripts/build_replay_bundle.py.

Off unless `settings.replay_preview_dir` names a folder, and always off in demo mode. They read
`<replay_preview_dir>/<match uuid>/bundle.json` from local disk and nothing else: no DB reads
or writes, no migration. Stage 2's stored replays replace them.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.services import replay_preview as preview
from app.services.replay_preview import UUID_SHAPE
from app.templates import templates

router = APIRouter(prefix="/dev/replays", tags=["replay-preview"])


def preview_root() -> Path:
    root = preview.preview_root()
    if root is None:
        raise HTTPException(status_code=404)
    return root


def bundle_path(match_uuid: str) -> Path:
    if not UUID_SHAPE.match(match_uuid):
        raise HTTPException(status_code=404)
    path = preview_root() / match_uuid / "bundle.json"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return path


def _summary(path: Path) -> dict | None:
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    match = bundle.get("match") or {}
    return {"uuid": path.parent.name, "map": match.get("map"), "match_id": match.get("match_id"),
            "score": [match.get("team1_rounds_won"), match.get("team2_rounds_won")],
            "rounds": len(bundle.get("rounds") or {}), "built_at": bundle.get("built_at")}


@router.get("")
def replay_list(request: Request):
    root = preview_root()
    bundles = [s for s in (_summary(p) for p in sorted(root.glob("*/bundle.json")) if UUID_SHAPE.match(p.parent.name))
               if s is not None]
    return templates.TemplateResponse(request, "replays/preview_list.html", {"bundles": bundles})


@router.get("/{match_uuid}/bundle.json")
def replay_bundle(match_uuid: str):
    return FileResponse(bundle_path(match_uuid), media_type="application/json")


@router.get("/{match_uuid}")
def replay_page(request: Request, match_uuid: str):
    summary = _summary(bundle_path(match_uuid))
    if summary is None:
        raise HTTPException(status_code=404)
    replay = {"map_name": summary["map"], "round_numbers": list(range(1, summary["rounds"] + 1)),
              "title_suffix": "(local preview)", "bundle_url": f"/dev/replays/{match_uuid}/bundle.json",
              "match_url": f"/matches/{match_uuid}"}
    return templates.TemplateResponse(request, "replays/preview.html", {"replay": replay})
